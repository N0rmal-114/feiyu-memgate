// The feedback receiver (feedback-worker/worker.js), run in Node: handle() with stand-ins for the
// Worker's settings and for GitHub. Checked: only one small JSON shape is taken, the report is filed
// with the token from the settings and nowhere else, the answer never repeats what was sent, the rate
// limits and every failure have their own answer, and what the settings page posts is what it takes.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import worker, { handle, issueOf, LIMITS, repositoryOf, validate, VERSION } from '../feedback-worker/worker.js'

const require = createRequire(import.meta.url)
const feedback = require('../client/src/model/feedback.cjs')
const pkg = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'))

const TOKEN = 'fake-token-for-tests-0123456789'
const ENV = { GITHUB_TOKEN: TOKEN, FEEDBACK_REPO: 'someone/feedback-inbox' }
const ADDRESS = 'https://receiver.example/'
const NOW = new Date('2026-11-21T08:00:00.000Z')
const ROWS = [['plugin', 'dsh-feiyu-memgate 0.3.2'], ['gateway', 'managed, running'], ['retrieval', 'auto -> local + keyword']]
const report = (overrides = {}) => ({ v: 1, kind: 'problem', text: 'Recall is slow\nsince 0.3.2', environment: ROWS, ...overrides })

// GitHub, recorded: every call with its address and options, answered with `status`.
function github(status = 201) {
  const calls = []
  const fetchImpl = async (url, init) => {
    calls.push({ url, init })
    if (status instanceof Error) throw status
    return new Response('{"number":1,"html_url":"https://github.invalid/private"}', { status })
  }
  return { calls, fetch: fetchImpl }
}

function post(body, { headers = {}, method = 'POST' } = {}) {
  return new Request(ADDRESS, {
    method,
    headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.7', ...headers },
    body: method === 'POST' ? (typeof body === 'string' ? body : JSON.stringify(body)) : undefined,
  })
}

const send = async (body, { env = ENV, hub = github(), request } = {}) => {
  const response = await handle(request || post(body), env, { fetch: hub.fetch, now: () => NOW })
  const text = await response.text()
  return { response, status: response.status, body: text ? JSON.parse(text) : null, text, hub }
}

// ------------------------------------------------------------------ a report that is taken

test('a report is filed as one issue in the configured repository, with the token from the settings', async () => {
  const { status, body, hub, response } = await send(report())
  assert.equal(status, 200)
  assert.deepEqual(body, { ok: true })
  assert.equal(response.headers.get('cache-control'), 'no-store')
  assert.equal(hub.calls.length, 1)
  const [{ url, init }] = hub.calls
  assert.equal(url, 'https://api.github.com/repos/someone/feedback-inbox/issues')
  assert.equal(init.method, 'POST')
  assert.equal(init.headers.authorization, 'Bearer ' + TOKEN)
  assert.equal(init.headers.accept, 'application/vnd.github+json')
  assert.ok(init.headers['user-agent'], 'GitHub refuses a call without one')
  assert.deepEqual(JSON.parse(init.body), {
    title: '[bug] Recall is slow',
    body: [
      'Recall is slow\nsince 0.3.2',
      '---',
      'Environment\n- plugin: dsh-feiyu-memgate 0.3.2\n- gateway: managed, running\n- retrieval: auto -> local + keyword',
      'Received 2026-11-21T08:00:00.000Z by the feedback receiver, from the settings page.',
    ].join('\n\n'),
  })
})

test('the answer never repeats the report, the token, the repository or where the issue is', async () => {
  for (const hub of [github(201), github(401), github(new Error('offline: ' + TOKEN))]) {
    const { text, response } = await send(report({ text: 'a secret sentence nobody else may see' }), { hub })
    for (const word of ['secret sentence', TOKEN, 'feedback-inbox', 'someone', 'github.invalid']) assert.ok(!text.includes(word), word)
    for (const [, value] of response.headers) assert.ok(!value.includes(TOKEN) && !value.includes('feedback-inbox'))
  }
})

test('issueOf(): the tag of the kind, the first line shortened to 60 characters, and what is missing said plainly', () => {
  const base = { kind: 'idea', text: '长'.repeat(61) + '\nmore', environment: [] }
  const issue = issueOf(base, NOW)
  assert.equal(issue.title, '[idea] ' + '长'.repeat(60) + '\u2026')
  assert.equal(issue.body, ['长'.repeat(61) + '\nmore', '---', 'Received 2026-11-21T08:00:00.000Z by the feedback receiver, from the settings page.'].join('\n\n'))
  assert.equal(issueOf({ ...base, kind: 'other', text: '\n  hello  \nworld' }, NOW).title, '[feedback] hello')
  // The same titles as the link to GitHub makes, so the two inboxes read alike.
  for (const [kind, text] of [['idea', 'Recall by date'], ['problem', '😀'.repeat(70)], ['other', 'x\ny']]) {
    assert.equal(issueOf({ kind, text, environment: [] }, NOW).title, feedback.titleOf(kind, text))
  }
})

// ------------------------------------------------------------------ what the settings page posts

test('what the settings page posts is what the receiver takes: payload() and environment() pass validate()', () => {
  const status = {
    version: '0.3.2-rc.1', language: 'zh', runtime: { platform: 'win32', arch: 'x64', node: '22.19.0' },
    gateway: { mode: 'managed', state: 'running' }, python: { found: true, version: '3.12.4' },
    retrieval: { setting: 'auto', active: 'local', hybrid: { active: true } },
    embed: { version: '0.1.0', state: 'ready', model: 'bge-small-zh-v1.5' },
  }
  for (const data of [status, { ...status, embed: null, python: { found: false } }, { ...status, version: 'C:\\x', gateway: { mode: 'external', state: 'configured' } }, null]) {
    for (const kind of feedback.KINDS) {
      const posted = feedback.payload({ kind, text: '  第一行\r\n第二行 ' }, feedback.environment(data))
      const checked = validate(JSON.parse(JSON.stringify(posted)))
      assert.equal(checked.ok, true, JSON.stringify(checked))
      assert.deepEqual(checked.report, { kind, text: '第一行\n第二行', environment: feedback.environment(data) })
    }
  }
  assert.equal(feedback.payload({ kind: 'nonsense', text: 'x' }, []).kind, 'other')
  assert.equal(feedback.payload({}, undefined).v, VERSION)
  assert.equal(LIMITS.text, feedback.MAX_TEXT, 'both sides cap the text at the same length')
  assert.equal(LIMITS.title, feedback.MAX_TITLE)
  const longest = feedback.payload({ kind: 'idea', text: '😀'.repeat(5000) }, feedback.environment(status))
  assert.equal(validate(longest).ok, true, 'the longest report the page can make')
  assert.ok(Buffer.byteLength(JSON.stringify(longest)) < LIMITS.bodyBytes, 'and it fits into one request')
})

// ------------------------------------------------------------------ what is refused

test('validate(): only the one shape, every field checked', () => {
  const field = (input) => { const checked = validate(input); return checked.ok ? null : checked.field }
  assert.equal(field(report()), null)
  assert.equal(field(report({ environment: undefined })), null, 'the environment is optional')
  for (const body of [null, 'text', 5, [], [report()]]) assert.equal(field(body), 'body')
  assert.equal(field({ ...report(), admin: true }), 'admin', 'an unknown field')
  assert.equal(field(report({ v: 2 })), 'v')
  assert.equal(field(report({ v: '1' })), 'v')
  for (const kind of ['bug', 'toString', '__proto__', 5, null]) assert.equal(field(report({ kind })), 'kind')
  for (const text of ['', ' \n\t ', '\u0000\u0007', 5, null, ['x'], 'x'.repeat(LIMITS.text + 1)]) assert.equal(field(report({ text })), 'text')
  assert.equal(field(report({ text: '😀'.repeat(LIMITS.text) })), null, 'counted in characters')
  for (const contact of ['x', '', null, 5]) assert.equal(field(report({ contact })), 'contact', 'the contact line is gone: it is an unknown field')
  for (const environment of [
    'plugin: x', {}, [['plugin']], [['plugin', 'a', 'b']], [['plugin', 5]], [[5, 'x']], [null],
    [['Plugin', 'x']], [['plugin name', 'x']], [['plugin', '']], [['plugin', 'a\nb']],
    [['plugin', '[link](https://x.invalid)']], [['plugin', '<img src=x>']], [['plugin', '@someone']], [['plugin', 'x'.repeat(161)]],
    Array.from({ length: LIMITS.rows + 1 }, () => ['plugin', 'x']),
  ]) assert.equal(field(report({ environment })), 'environment', JSON.stringify(environment).slice(0, 60))
})

test('validate(): the text is tidied (line breaks, control characters, half character pairs)', () => {
  const { report: tidy } = validate(report({ text: ' a\r\nb\rc\u0007\uD83D ', }))
  assert.equal(tidy.text, 'a\nb\nc\uFFFD')
})

test('a request that is not one small JSON report is refused before anything is filed', async () => {
  const refused = async (request, expected, code) => {
    const { status, body, hub } = await send(null, { request })
    assert.equal(status, expected)
    assert.equal(body.ok, false)
    assert.equal(body.code, code)
    assert.equal(hub.calls.length, 0, 'nothing reached GitHub')
  }
  await refused(post(report(), { headers: { 'content-type': 'text/plain' } }), 415, 'not-json')
  await refused(post(report(), { headers: { 'content-type': 'application/x-www-form-urlencoded' } }), 415, 'not-json')
  await refused(post('{"v":1,', {}), 400, 'invalid')
  await refused(post(new TextDecoder('latin1').decode(new Uint8Array([0x7b, 0xff, 0x7d])), {}), 400, 'invalid')
  await refused(post(report({ kind: 'spam' })), 400, 'invalid')
  await refused(post(JSON.stringify(report({ text: 'x'.repeat(LIMITS.bodyBytes) }))), 413, 'too-large')
  await refused(post(report(), { headers: { 'content-length': String(LIMITS.bodyBytes + 1) } }), 413, 'too-large')
  await refused(new Request(ADDRESS, { method: 'PUT', body: '{}' }), 405, 'method-not-allowed')
  await refused(new Request(ADDRESS, { method: 'DELETE' }), 405, 'method-not-allowed')
  const invalid = await send(report({ text: '' }))
  assert.deepEqual(invalid.body, { ok: false, code: 'invalid', field: 'text' }, 'the field is named, its content is not')
})

test('a Worker without its settings says so and files nothing; a broken repository name counts as none', async () => {
  for (const env of [{}, null, { GITHUB_TOKEN: TOKEN }, { FEEDBACK_REPO: 'someone/feedback-inbox' }, { GITHUB_TOKEN: '', FEEDBACK_REPO: 'someone/feedback-inbox' },
    { GITHUB_TOKEN: TOKEN, FEEDBACK_REPO: 'no-slash' }, { GITHUB_TOKEN: TOKEN, FEEDBACK_REPO: 'a/b/../../user' }, { GITHUB_TOKEN: TOKEN, FEEDBACK_REPO: 'a/b?x=1' }, { GITHUB_TOKEN: TOKEN, FEEDBACK_REPO: 'a/..' }, { GITHUB_TOKEN: TOKEN, FEEDBACK_REPO: './b' }]) {
    const { status, body, hub } = await send(report(), { env })
    assert.equal(status, 503)
    assert.deepEqual(body, { ok: false, code: 'not-configured' })
    assert.equal(hub.calls.length, 0)
  }
})

test('GitHub refusing or being out of reach is an upstream failure, and only its status is logged', async () => {
  const logged = []
  const original = console.error
  console.error = (...args) => logged.push(args.join(' '))
  try {
    for (const hub of [github(401), github(404), github(422), github(200), github(new Error('offline'))]) {
      const { status, body } = await send(report({ text: 'private words' }), { hub })
      assert.equal(status, 502)
      assert.deepEqual(body, { ok: false, code: 'upstream' })
    }
  } finally {
    console.error = original
  }
  assert.deepEqual(logged, ['feedback: GitHub answered 401', 'feedback: GitHub answered 404', 'feedback: GitHub answered 422', 'feedback: GitHub answered 200', 'feedback: GitHub could not be reached'])
})

test('the repository may be given as owner/name or as its address on github.com, and the token may carry stray spaces', async () => {
  for (const given of ['someone/feedback-inbox', '  someone/feedback-inbox\n', 'https://github.com/someone/feedback-inbox', 'https://github.com/someone/feedback-inbox/', 'https://GitHub.com/someone/feedback-inbox.git']) {
    assert.equal(repositoryOf(given), 'someone/feedback-inbox', given)
    const { status, hub } = await send(report(), { env: { GITHUB_TOKEN: ' ' + TOKEN + '\n', FEEDBACK_REPO: given } })
    assert.equal(status, 200, given)
    assert.equal(hub.calls[0].url, 'https://api.github.com/repos/someone/feedback-inbox/issues')
    assert.equal(hub.calls[0].init.headers.authorization, 'Bearer ' + TOKEN)
  }
  for (const given of ['', 'feedback-inbox', 'https://example.invalid/someone/feedback-inbox', 'http://github.com/someone/feedback-inbox', 'someone/feedback-inbox/issues', 'a/..', null, undefined, 5]) {
    assert.equal(repositoryOf(given), '', String(given))
  }
})

// ------------------------------------------------------------------ rate limits

test('the rate limit bindings: one key per sender, one for everybody, 429 when either says no, and none is fine', async () => {
  const limiter = (verdicts) => {
    const keys = []
    return { keys, limit: async ({ key }) => { keys.push(key); return { success: verdicts.shift() ?? true } } }
  }
  const perSender = limiter([true, false])
  const all = limiter([true])
  const env = { ...ENV, LIMIT_PER_SENDER: perSender, LIMIT_ALL: all }
  const first = await send(report(), { env })
  assert.equal(first.status, 200)
  const second = await send(report(), { env })
  assert.equal(second.status, 429)
  assert.deepEqual(second.body, { ok: false, code: 'rate-limited' })
  assert.equal(second.response.headers.get('retry-after'), '60')
  assert.equal(second.hub.calls.length, 0)
  assert.deepEqual(perSender.keys, ['sender:203.0.113.7', 'sender:203.0.113.7'])
  assert.deepEqual(all.keys, ['all'], 'the second request never got as far as the shared limit')

  const full = await send(report(), { env: { ...ENV, LIMIT_ALL: limiter([false]) } })
  assert.equal(full.status, 429)
  const broken = { limit: async () => { throw new Error('binding down') } }
  assert.equal((await send(report(), { env: { ...ENV, LIMIT_PER_SENDER: broken, LIMIT_ALL: {} } })).status, 200, 'a binding that fails does not stop a report')
  const unknown = limiter([true])
  await send(null, { env: { ...ENV, LIMIT_PER_SENDER: unknown }, request: new Request(ADDRESS, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(report()) }) })
  assert.deepEqual(unknown.keys, ['sender:unknown'])
  // An invalid report costs nothing of anybody's allowance.
  const untouched = limiter([])
  await send(report({ text: '' }), { env: { ...ENV, LIMIT_PER_SENDER: untouched } })
  assert.deepEqual(untouched.keys, [])
})

// ------------------------------------------------------------------ the browser's side of the call

test('every answer may be read from any origin, the preflight is answered, and GET says whether the Worker is set up', async () => {
  const preflight = await handle(new Request(ADDRESS, { method: 'OPTIONS', headers: { origin: 'http://localhost:47893', 'access-control-request-method': 'POST' } }), ENV)
  assert.equal(preflight.status, 204)
  assert.equal(await preflight.text(), '')
  assert.equal(preflight.headers.get('access-control-allow-origin'), '*')
  assert.equal(preflight.headers.get('access-control-allow-methods'), 'POST, OPTIONS')
  assert.equal(preflight.headers.get('access-control-allow-headers'), 'content-type')
  assert.equal(preflight.headers.get('access-control-allow-credentials'), null, 'no cookie is ever involved')
  for (const { response } of [await send(report()), await send(report({ text: '' })), await send(report(), { env: {} }), await send(report(), { hub: github(500) })]) {
    assert.equal(response.headers.get('access-control-allow-origin'), '*')
    assert.match(response.headers.get('content-type'), /^application\/json/)
  }
  const up = await handle(new Request(ADDRESS), ENV)
  assert.deepEqual(await up.json(), { ok: true, service: 'feiyu-memgate-feedback', v: 1, configured: true })
  assert.deepEqual(await (await handle(new Request(ADDRESS), {})).json(), { ok: true, service: 'feiyu-memgate-feedback', v: 1, configured: false, missing: ['GITHUB_TOKEN', 'FEEDBACK_REPO'] })
  // Which setting is the matter is said by name; a value is never shown.
  const half = await (await handle(new Request(ADDRESS), { GITHUB_TOKEN: TOKEN, FEEDBACK_REPO: 'not a repository' })).text()
  assert.deepEqual(JSON.parse(half).missing, ['FEEDBACK_REPO'])
  assert.ok(!half.includes(TOKEN) && !half.includes('not a repository'))
  assert.deepEqual((await (await handle(new Request(ADDRESS), { GITHUB_TOKEN: '  ', FEEDBACK_REPO: 'someone/feedback-inbox' })).json()).missing, ['GITHUB_TOKEN'])
  assert.equal(await (await handle(new Request(ADDRESS, { method: 'HEAD' }), ENV)).text(), '')
})

// ------------------------------------------------------------------ the file itself

test('the Worker is one file without imports, exports a fetch handler, and is not part of the published package', async () => {
  const source = fs.readFileSync(new URL('../feedback-worker/worker.js', import.meta.url), 'utf8')
  assert.ok(!/^\s*import\s/m.test(source), 'no import: the file can be pasted into the Cloudflare editor as it is')
  assert.ok(!source.includes('\r'))
  assert.equal(typeof worker.fetch, 'function')
  const viaDefault = await worker.fetch(new Request(ADDRESS), ENV)
  assert.equal(viaDefault.status, 200)
  assert.ok(!pkg.files.some((entry) => entry.startsWith('feedback-worker')), 'the receiver is deployed by the maintainers, not installed with the plugin')
  const config = fs.readFileSync(new URL('../feedback-worker/wrangler.toml', import.meta.url), 'utf8')
  assert.match(config, /^main = "worker\.js"$/m)
  assert.deepEqual([...config.matchAll(/^name = "(LIMIT_[A-Z_]+)"$/gm)].map((m) => m[1]), ['LIMIT_PER_SENDER', 'LIMIT_ALL'], 'the bindings the Worker reads')
  assert.deepEqual([...config.matchAll(/^\s*period = (\d+)$/gm)].map((m) => m[1]), ['60', '60'], 'Cloudflare takes 10 or 60 seconds')
  assert.ok(!/TOKEN\s*=|FEEDBACK_REPO\s*=/.test(config), 'the token and the repository name are secrets, not configuration')
})
