// The receiver for feedback sent from the settings page of dsh-feiyu-memgate: a Cloudflare Worker.
//
// The settings page posts a report here when a person presses Submit on its Feedback tab
// (client/src/view/feedback.cjs). The Worker checks the report and files it as an issue in a
// repository only the maintainers can read. The token that may write there exists only in the Worker's
// secrets; the plugin package, which anybody can read, holds nothing but this Worker's address.
//
// Anybody can call that address, so the Worker trusts nothing it is sent:
//   - one small JSON object in a fixed shape, every field checked, nothing else accepted;
//   - the environment lines must be made of the few characters version numbers and state names use,
//     so they cannot carry markup or a second line into the issue;
//   - a few requests per minute per sender and in total, when the two rate limit bindings are set up
//     (wrangler.toml); the counts are Cloudflare's, approximate and per location;
//   - the answer never repeats what was sent and never says where it was filed.
//
// What is kept: the report as the person wrote it, the contact line if they gave one, the environment
// lines, and the time. The sender's address is used as the key of the rate limit and is not stored or
// logged; neither is anything else about the request.
//
// Settings (README.md in this folder):
//   GITHUB_TOKEN      secret; a fine-grained token with "Issues: read and write" on FEEDBACK_REPO only
//   FEEDBACK_REPO     secret or variable; "owner/name" of the repository that receives the issues
//   LIMIT_PER_SENDER  optional rate limit binding, keyed by the sender's address
//   LIMIT_ALL         optional rate limit binding, one key for everybody
//
// No dependency and no build step: the file runs as it is, and tests/feedback-worker.test.js calls
// handle() in Node with stand-ins for the settings and for GitHub.

/** The version of the report shape this Worker reads; the page sends it as `v`. */
export const VERSION = 1

/** The most the Worker accepts. The page keeps to the same numbers (client/src/model/feedback.cjs). */
export const LIMITS = { bodyBytes: 32768, text: 4000, title: 60, contact: 200, rows: 12 }

// What a report is about, with the start of the issue title that goes with it.
const TITLE_TAGS = { idea: '[idea]', problem: '[bug]', other: '[feedback]' }
// The name of an environment line: plugin, add-on, gateway, ...
const ROW_NAME = /^[a-z][a-z-]{0,23}$/
// The value of one: version numbers, state names, and the few signs the page joins them with.
const ROW_VALUE = /^[A-Za-z0-9 ._+,>?-]{1,160}$/
const REPO = /^[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/
// Control characters other than tab and line feed, and halves of a character pair without the other half.
const CONTROL = /[\u0000-\u0008\u000B-\u001F\u007F]/g
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g

// Every answer may be read by a page of any origin: the settings page is served by the person's own
// DSH, whose address the Worker cannot know. No cookie or credential is involved.
const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'POST, OPTIONS',
  'access-control-allow-headers': 'content-type',
  'access-control-max-age': '86400',
}

function answer(status, body, extra = {}) {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { ...CORS, 'cache-control': 'no-store', ...(body === null ? {} : { 'content-type': 'application/json; charset=utf-8' }), ...extra },
  })
}

const refuse = (status, code, extra) => answer(status, { ok: false, code }, extra)

/** A text with one kind of line break, no control characters and no stray half characters, trimmed. */
function tidy(value) {
  return value.replace(/\r\n?/g, '\n').replace(CONTROL, '').replace(LONE_SURROGATE, '�').trim()
}

/**
 * Checks what was posted.
 * @param {unknown} input the parsed JSON
 * @returns {{ ok: true, report: { kind: string, text: string, contact: string, environment: [string, string][] } }
 *   | { ok: false, field: string }} `field` names the first part that is not as it has to be
 */
export function validate(input) {
  const bad = (field) => ({ ok: false, field })
  if (input === null || typeof input !== 'object' || Array.isArray(input)) return bad('body')
  for (const key of Object.keys(input)) if (!['v', 'kind', 'text', 'contact', 'environment'].includes(key)) return bad(key.slice(0, 24))
  if (input.v !== VERSION) return bad('v')
  if (typeof input.kind !== 'string' || !Object.hasOwn(TITLE_TAGS, input.kind)) return bad('kind')
  if (typeof input.text !== 'string') return bad('text')
  const text = tidy(input.text)
  const length = Array.from(text).length
  if (length === 0 || length > LIMITS.text) return bad('text')
  let contact = ''
  if (input.contact !== undefined) {
    if (typeof input.contact !== 'string') return bad('contact')
    contact = tidy(input.contact).replace(/\s+/g, ' ')
    if (Array.from(contact).length > LIMITS.contact) return bad('contact')
  }
  const environment = []
  if (input.environment !== undefined) {
    if (!Array.isArray(input.environment) || input.environment.length > LIMITS.rows) return bad('environment')
    for (const row of input.environment) {
      if (!Array.isArray(row) || row.length !== 2 || typeof row[0] !== 'string' || typeof row[1] !== 'string') return bad('environment')
      if (!ROW_NAME.test(row[0]) || !ROW_VALUE.test(row[1])) return bad('environment')
      environment.push([row[0], row[1]])
    }
  }
  return { ok: true, report: { kind: input.kind, text, contact, environment } }
}

/**
 * The issue a report becomes.
 * @param {{ kind: string, text: string, contact: string, environment: [string, string][] }} report from validate()
 * @param {Date} now
 * @returns {{ title: string, body: string }}
 */
export function issueOf(report, now) {
  const first = report.text.split('\n').map((line) => line.trim()).find(Boolean) || ''
  const points = Array.from(first)
  const line = points.length > LIMITS.title ? points.slice(0, LIMITS.title).join('').trim() + '…' : first
  const parts = [report.text, '---', 'Contact: ' + (report.contact || '(none given)')]
  if (report.environment.length) parts.push('Environment\n' + report.environment.map((row) => '- ' + row[0] + ': ' + row[1]).join('\n'))
  parts.push('Received ' + now.toISOString() + ' by the feedback receiver, from the settings page.')
  return { title: TITLE_TAGS[report.kind] + ' ' + line, body: parts.join('\n\n') }
}

// Whether a rate limit binding lets this request through. A binding that is not set up, or that
// fails, lets it through: the limit is a courtesy of the platform, not what keeps the reports safe.
async function allowed(binding, key) {
  if (!binding || typeof binding.limit !== 'function') return true
  try {
    const { success } = await binding.limit({ key })
    return success !== false
  } catch {
    return true
  }
}

/**
 * One request.
 * @param {Request} request
 * @param {{ GITHUB_TOKEN?: string, FEEDBACK_REPO?: string, LIMIT_PER_SENDER?: object, LIMIT_ALL?: object }} env
 * @param {{ fetch?: typeof fetch, now?: () => Date }} [deps] replaced in tests
 * @returns {Promise<Response>} 200 { ok: true } when the report was filed; otherwise { ok: false, code }
 *   with 400 invalid, 405, 413 too-large, 415 not-json, 429 rate-limited, 502 upstream, 503 not-configured
 */
export async function handle(request, env, deps = {}) {
  const settings = env || {}
  const repo = String(settings.FEEDBACK_REPO || '')
  // "owner/name" and nothing that an address would read as a step up or down.
  const configured = typeof settings.GITHUB_TOKEN === 'string' && settings.GITHUB_TOKEN !== '' && REPO.test(repo) && !repo.split('/').some((part) => part === '.' || part === '..')
  if (request.method === 'OPTIONS') return answer(204, null)
  // A way to see that the Worker is up and has its settings, without sending a report.
  if (request.method === 'GET' || request.method === 'HEAD') return answer(200, request.method === 'HEAD' ? null : { ok: true, service: 'feiyu-memgate-feedback', v: VERSION, configured })
  if (request.method !== 'POST') return refuse(405, 'method-not-allowed', { allow: 'GET, HEAD, POST, OPTIONS' })
  if (!/^application\/json\b/i.test(request.headers.get('content-type') || '')) return refuse(415, 'not-json')
  if (Number(request.headers.get('content-length')) > LIMITS.bodyBytes) return refuse(413, 'too-large')

  let bytes
  try {
    bytes = await request.arrayBuffer()
  } catch {
    return refuse(400, 'invalid')
  }
  if (bytes.byteLength > LIMITS.bodyBytes) return refuse(413, 'too-large')
  let input
  try {
    input = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  } catch {
    return refuse(400, 'invalid')
  }
  const checked = validate(input)
  if (!checked.ok) return answer(400, { ok: false, code: 'invalid', field: checked.field })
  if (!configured) return refuse(503, 'not-configured')

  const sender = request.headers.get('cf-connecting-ip') || 'unknown'
  if (!(await allowed(settings.LIMIT_PER_SENDER, 'sender:' + sender)) || !(await allowed(settings.LIMIT_ALL, 'all'))) {
    return refuse(429, 'rate-limited', { 'retry-after': '60' })
  }

  const issue = issueOf(checked.report, (deps.now || (() => new Date()))())
  let filed
  try {
    filed = await (deps.fetch || fetch)('https://api.github.com/repos/' + repo + '/issues', {
      method: 'POST',
      headers: {
        authorization: 'Bearer ' + settings.GITHUB_TOKEN,
        accept: 'application/vnd.github+json',
        'content-type': 'application/json',
        'x-github-api-version': '2022-11-28',
        'user-agent': 'feiyu-memgate-feedback-receiver',
      },
      body: JSON.stringify(issue),
    })
  } catch {
    console.error('feedback: GitHub could not be reached')
    return refuse(502, 'upstream')
  }
  if (filed.status !== 201) {
    // The status is enough to tell a bad token (401, 403) from a wrong repository (404); nothing of the report is logged.
    console.error('feedback: GitHub answered ' + filed.status)
    return refuse(502, 'upstream')
  }
  return answer(200, { ok: true })
}

export default {
  fetch: (request, env) => handle(request, env),
}
