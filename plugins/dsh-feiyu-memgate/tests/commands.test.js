// The /memgate command on its own: parsing, every subcommand, refusals, both languages, and that no
// secret is ever repeated. Settings writes go to a recording stand-in for the host's Settings service.
import test from 'node:test'
import assert from 'node:assert/strict'
import { COMMAND_NAME, createMemgateCommand, formatStatus, resolveSetting } from '../src/commands.js'
import { FIELDS, readConfig } from '../src/config.js'

const TOKEN = 'fake-gateway-token-0123456789'

function fakeStatus(overrides = {}) {
  return {
    version: '0.1.0',
    language: 'en',
    modules: { autoRecall: true, rememberTool: true, searchTool: false, promptSection: true, autoCapture: false, commands: true, migrateOnTierChange: true },
    registered: ['autoRecall', 'rememberTool', 'promptSection', 'commands'],
    gateway: { mode: 'managed', state: 'running' },
    secrets: { 'external.token': false, 'retrieval.apiKey': false },
    python: { found: true, version: '3.12.4', source: 'path', executable: 'python-for-tests' },
    retrieval: {
      setting: 'auto',
      active: 'keyword',
      hybrid: { setting: false, active: false },
      tiers: [
        { name: 'keyword', available: true, missing: [], vector: false, active: true },
        { name: 'endpoint', available: false, missing: ['retrieval.url', 'retrieval.model'], vector: true, active: false },
        { name: 'local', available: false, missing: ['dsh-feiyu-memgate-embed (ready)'], vector: true, active: false },
      ],
    },
    store: { tier: 'keyword', id: 'keyword', type: 'sqlite', file: 'memories.db', count: 12 },
    embed: null,
    migration: null,
    ...overrides,
  }
}

function applyOps(config, ops) {
  for (const op of ops) {
    const [group, key] = op.path
    config[group] ??= {}
    if (op.op === 'set') config[group][key] = op.value
    else delete config[group][key]
  }
}

function harness({ lang = 'en', values = {}, status, write, changelog } = {}) {
  const config = structuredClone(values)
  const writes = []
  const command = createMemgateCommand({
    settings: () => readConfig(config),
    language: () => lang,
    status: status ?? (async () => fakeStatus()),
    write: write ?? (async (ops) => {
      writes.push(structuredClone(ops))
      applyOps(config, ops)
      return { ok: true }
    }),
    changelog,
  })
  const run = (rawInput) => command.handler({ rawInput, attachments: [], signal: new AbortController().signal })
  return { command, run, writes, config }
}

test('the definition: one lowercase name, a description, an input hint, and the input kept out of the log', () => {
  const { command } = harness()
  assert.equal(command.name, COMMAND_NAME)
  assert.match(command.name, /^[a-z][a-z0-9_-]*$/)
  assert.ok(command.description.trim())
  assert.ok(command.input.hint.trim())
  assert.equal(command.recordInput, false)
  assert.equal(typeof command.handler, 'function')
})

test('no subcommand or help shows the usage', async () => {
  const { run } = harness()
  for (const input of ['', '   ', ' help']) {
    const reply = await run(input)
    assert.equal(reply.kind, 'success')
    assert.match(reply.text, /^Usage: \/memgate/)
  }
  const zh = await harness({ lang: 'zh' }).run('')
  assert.match(zh.text, /^用法：\/memgate/)
})

test('status shows gateway, Python, retrieval, store, add-on, copy and features', async () => {
  const { run } = harness({
    status: async () => fakeStatus({
      retrieval: { ...fakeStatus().retrieval, active: 'local' },
      store: { tier: 'local', id: 'local-x', type: 'embedding', file: 'memories-local-x.db', count: 40 },
      embed: { version: 1, state: 'ready', model: 'bge-small-zh-v1.5', dimensions: 512, parity: 'approximate', models: [] },
      migration: { state: 'running', total: 12, copied: 5, remaining: 7, target: 'local-x' },
    }),
  })
  const reply = await run(' status')
  assert.equal(reply.kind, 'success')
  for (const part of ['Gateway: managed, running', 'Python: 3.12.4 (path) python-for-tests', 'Retrieval: auto → local',
    'Store: embedding, memories-local-x.db, 40 memories', 'Embedding add-on: ready, bge-small-zh-v1.5, 512d, scores not calibrated',
    'Copy from the keyword store: in progress, 5 of 12 copied', 'Features on: autoRecall, rememberTool, promptSection, commands, migrateOnTierChange']) {
    assert.ok(reply.text.includes(part), `${part}\n${reply.text}`)
  }
  assert.match(formatStatus(fakeStatus(), 'zh'), /网关：managed，运行中|网关: managed, 运行中/)
  const external = formatStatus(fakeStatus({ gateway: { mode: 'external', state: 'incomplete', url: '' }, python: null, store: null }), 'en')
  assert.match(external, /Gateway: external, needs external\.url and external\.token/)
  assert.ok(!external.includes('Python:'), 'no interpreter line for an external gateway')
})

test('config lists every setting with value and default, secrets only as set or not set', async () => {
  const { run } = harness({ values: { external: { token: TOKEN }, advanced: { recallLimit: 8 } } })
  const reply = await run('config')
  assert.equal(reply.kind, 'success')
  for (const group of FIELDS) for (const field of group.fields) assert.ok(reply.text.includes(`${group.group}.${field.key} = `), field.key)
  assert.ok(reply.text.includes('advanced.recallLimit = 8  (5)'))
  assert.ok(reply.text.includes('external.token = set  (not set) [secret]'))
  assert.ok(!reply.text.includes(TOKEN))
})

test('set writes one checked value through the Settings service', async () => {
  const { run, writes } = harness()
  const reply = await run(' set advanced.recallLimit 7')
  assert.deepEqual(writes, [[{ op: 'set', path: ['advanced', 'recallLimit'], value: 7 }]])
  assert.equal(reply.kind, 'success')
  assert.equal(reply.text, 'Saved: advanced.recallLimit = 7 (was 5).')
  assert.equal((await run('set recalllimit 3')).text, 'Saved: advanced.recallLimit = 3 (was 7).', 'a bare key in any case when it is unique')
  assert.equal((await run('set gateway.dataDir   D:/my memories ')).text, 'Saved: gateway.dataDir = D:/my memories (was (empty)).')
  assert.match((await run('set advanced.recallLimit 3')).text, /already 3; nothing changed/)
  assert.equal(writes.length, 3)
})

test('set refuses unknown keys and invalid values, and never repeats what does not look like a name', async () => {
  const { run, writes } = harness()
  const cases = [
    ['set', /^Usage: \/memgate set <key> <value>/],
    ['set advanced.recallLimit', /^Usage: \/memgate set <key> <value>/],
    ['set nothing.here 1', /^Unknown setting "nothing.here"/],
    ['set url http://x', /names more than one setting: retrieval.url, external.url/],
    ['set advanced.recallLimit 11', /takes a whole number from 1 to 10/],
    ['set advanced.recallLimit 2.5', /takes a whole number/],
    ['set modules.autoRecall perhaps', /takes on or off/],
    ['set retrieval.tier vector', /takes one of: auto, keyword, endpoint, local/],
    ['set external.url ftp://x', /http:\/\/ or https:\/\//],
  ]
  for (const [input, pattern] of cases) {
    const reply = await run(input)
    assert.equal(reply.kind, 'error', input)
    assert.match(reply.text, pattern, input)
  }
  const odd = await run('set sk-live:abc/def 1')
  assert.equal(odd.text, 'Unknown setting. /memgate config lists them.')
  const leaked = await run(`set advanced.recallLimit ${TOKEN}`)
  assert.ok(!leaked.text.includes(TOKEN))
  assert.equal(writes.length, 0)
})

test('a secret is written but never echoed; reset clears it', async () => {
  const { run, writes, config } = harness({ lang: 'zh' })
  const reply = await run(`set external.token ${TOKEN}`)
  assert.equal(reply.kind, 'success')
  assert.ok(!reply.text.includes(TOKEN))
  assert.match(reply.text, /已设置/)
  assert.deepEqual(writes[0], [{ op: 'set', path: ['external', 'token'], value: TOKEN }])
  assert.equal(config.external.token, TOKEN)
  const shown = await run('config')
  assert.ok(!shown.text.includes(TOKEN))
  const cleared = await run('reset token')
  assert.equal(cleared.kind, 'success')
  assert.deepEqual(writes[1], [{ op: 'unset', path: ['external', 'token'] }])
  assert.match(cleared.text, /已清空/)
})

test('reset returns a setting to its default', async () => {
  const { run, writes } = harness({ values: { advanced: { profile: 'precise' } } })
  const reply = await run('reset advanced.profile')
  assert.equal(reply.text, 'Reset: advanced.profile is back to its default (broad).')
  assert.deepEqual(writes, [[{ op: 'unset', path: ['advanced', 'profile'] }]])
  assert.equal((await run('reset')).kind, 'error')
  assert.equal((await run('reset a b')).kind, 'error')
})

test('on and off switch features', async () => {
  const { run, writes } = harness()
  assert.equal((await run('off autoRecall')).text, 'Saved: modules.autoRecall = off (was on).')
  assert.equal((await run('on modules.autoCapture')).text, 'Saved: modules.autoCapture = on (was off).')
  assert.match((await run('on promptsection')).text, /already on/)
  assert.deepEqual(writes, [
    [{ op: 'set', path: ['modules', 'autoRecall'], value: false }],
    [{ op: 'set', path: ['modules', 'autoCapture'], value: true }],
  ])
  const unknown = await run('on teleport')
  assert.equal(unknown.kind, 'error')
  assert.match(unknown.text, /Unknown feature "teleport"\. Features: autoRecall, .*migrateOnTierChange\./)
  assert.equal((await run('off')).kind, 'error')
})

test('tiers lists availability, the active tier and what each needs', async () => {
  const { run } = harness()
  const reply = await run('tiers')
  assert.equal(reply.kind, 'success')
  assert.match(reply.text, /^Retrieval tier: auto, active: keyword\./)
  assert.match(reply.text, /- keyword: available \(active\)\. Needs: nothing; always available\./)
  assert.match(reply.text, /- endpoint: not available\. Needs: retrieval\.url and retrieval\.model .*Missing: retrieval\.url, retrieval\.model\./)
  assert.match(reply.text, /- local: not available\. Needs: the dsh-feiyu-memgate-embed add-on/)
  const zh = await harness({ lang: 'zh' }).run('tiers')
  assert.match(zh.text, /- local：不可用。需要：已安装并启用 dsh-feiyu-memgate-embed 插件/)
})

test('the hybrid state shows in status and tiers, in both languages', async () => {
  const hybridStatus = () => fakeStatus({
    retrieval: { ...fakeStatus().retrieval, setting: 'endpoint', active: 'endpoint', hybrid: { setting: true, active: true } },
    store: { tier: 'endpoint', id: 'endpoint-m', type: 'hybrid', file: 'memories-endpoint-m.db', keywordFile: 'memories-endpoint-m-keywords.db', hybrid: true, count: 9 },
  })
  const en = await harness({ status: async () => hybridStatus() }).run('status')
  assert.ok(en.text.includes('Retrieval: endpoint → endpoint + keyword (hybrid)'), en.text)
  assert.ok(en.text.includes('Store: hybrid, memories-endpoint-m.db + memories-endpoint-m-keywords.db, 9 memories'), en.text)
  const zh = await harness({ lang: 'zh', status: async () => hybridStatus() }).run('status')
  assert.ok(zh.text.includes('检索: endpoint → endpoint + 关键词（混合）'), zh.text)
  // Off, or on but not applying (keyword): the line stays as it was.
  assert.ok(formatStatus(fakeStatus(), 'en').includes('Retrieval: auto → keyword\n'))
  const idle = fakeStatus({ retrieval: { ...fakeStatus().retrieval, hybrid: { setting: true, active: false } } })
  assert.ok(formatStatus(idle, 'en').includes('Retrieval: auto → keyword\n'))

  const tiers = await harness({ status: async () => hybridStatus() }).run('tiers')
  assert.match(tiers.text, /\nHybrid \(keyword and vector search together, retrieval\.hybrid\): on\.$/)
  const tiersZh = await harness({ lang: 'zh', status: async () => hybridStatus() }).run('tiers')
  assert.match(tiersZh.text, /\n混合检索（关键词与向量同时检索，retrieval\.hybrid）：开启。$/)
  assert.match((await harness().run('tiers')).text, /: off\.$/)
  assert.match((await harness({ status: async () => idle }).run('tiers')).text, /: on, but it does nothing under the current tier\.$/)
  const external = fakeStatus({ gateway: { mode: 'external', state: 'configured', url: '' }, python: null, store: null })
  assert.ok(!formatStatus(external, 'en').includes('hybrid'))
  assert.ok(!(await harness({ status: async () => external }).run('tiers')).text.includes('Hybrid'), 'tiers do not apply to an external gateway')
})

test('retrieval.hybrid is set like any switch, by full or bare name', async () => {
  const { run, writes } = harness()
  assert.equal((await run('set retrieval.hybrid on')).text, 'Saved: retrieval.hybrid = on (was off).')
  assert.equal((await run('set hybrid off')).text, 'Saved: retrieval.hybrid = off (was on).')
  assert.deepEqual(writes.map((w) => w[0].value), [true, false])
  assert.match((await run('set retrieval.hybrid maybe')).text, /retrieval\.hybrid takes on or off\./)
})

test('advanced.linkedRecall is listed, set by full or bare name and reset', async () => {
  const { run, writes } = harness()
  assert.ok((await run('config')).text.includes('advanced.linkedRecall = on  (on)'))
  assert.equal((await run('set advanced.linkedRecall off')).text, 'Saved: advanced.linkedRecall = off (was on).')
  assert.equal((await run('set linkedrecall on')).text, 'Saved: advanced.linkedRecall = on (was off).', 'a bare name is enough')
  assert.match((await run('set advanced.linkedRecall maybe')).text, /advanced\.linkedRecall takes on or off\./)
  assert.equal((await run('reset advanced.linkedRecall')).text, 'Reset: advanced.linkedRecall is back to its default (on).')
  assert.deepEqual(writes.map((ops) => [ops[0].op, ops[0].path.join('.'), ops[0].value]), [
    ['set', 'advanced.linkedRecall', false],
    ['set', 'advanced.linkedRecall', true],
    ['unset', 'advanced.linkedRecall', undefined],
  ])
})

test('the chat settings are listed, set and reset like every other setting', async () => {
  const { run, writes } = harness()
  const config = await run('config')
  assert.ok(config.text.includes('advanced.skipRepeats = on  (on)'), config.text)
  assert.ok(config.text.includes('advanced.showAge = on  (on)'))
  assert.ok(config.text.includes('advanced.failureNote = on  (on)'))
  assert.ok(config.text.includes('advanced.captureMinChars = 4  (4)'))
  assert.equal((await run('set advanced.captureMinChars 0')).text, 'Saved: advanced.captureMinChars = 0 (was 4).')
  assert.equal((await run('set captureminchars 12')).text, 'Saved: advanced.captureMinChars = 12 (was 0).', 'a bare name is enough')
  assert.match((await run('set advanced.captureMinChars 51')).text, /takes a whole number from 0 to 50/)
  assert.match((await run('set advanced.captureMinChars -1')).text, /takes a whole number from 0 to 50/)
  assert.equal((await run('set skipRepeats off')).text, 'Saved: advanced.skipRepeats = off (was on).')
  assert.equal((await run('set advanced.showAge off')).text, 'Saved: advanced.showAge = off (was on).')
  assert.equal((await run('set failurenote off')).text, 'Saved: advanced.failureNote = off (was on).')
  assert.match((await run('set advanced.showAge maybe')).text, /advanced\.showAge takes on or off\./)
  assert.equal((await run('reset advanced.captureMinChars')).text, 'Reset: advanced.captureMinChars is back to its default (4).')
  assert.deepEqual(writes.map((ops) => [ops[0].op, ops[0].path.join('.'), ops[0].value]), [
    ['set', 'advanced.captureMinChars', 0],
    ['set', 'advanced.captureMinChars', 12],
    ['set', 'advanced.skipRepeats', false],
    ['set', 'advanced.showAge', false],
    ['set', 'advanced.failureNote', false],
    ['unset', 'advanced.captureMinChars', undefined],
  ])
  const zh = await harness({ lang: 'zh' }).run('set advanced.failureNote off')
  assert.match(zh.text, /^已保存：advanced\.failureNote = /)
})

test('the prototype settings are listed, set by full or bare name and reset', async () => {
  const { run, writes } = harness()
  const config = await run('config')
  assert.ok(config.text.includes('advanced.afterthought = off  (off)'), config.text)
  assert.ok(config.text.includes('advanced.afterthoughtMax = 2  (2)'))
  assert.ok(config.text.includes('advanced.conversationPool = off  (off)'))
  assert.ok(config.text.includes('advanced.poolSize = 3  (3)'))
  assert.equal((await run('set advanced.afterthought on')).text, 'Saved: advanced.afterthought = on (was off).')
  assert.equal((await run('set afterthoughtmax 4')).text, 'Saved: advanced.afterthoughtMax = 4 (was 2).', 'a bare name is enough, and afterthought does not swallow afterthoughtMax')
  assert.equal((await run('set conversationpool on')).text, 'Saved: advanced.conversationPool = on (was off).')
  assert.equal((await run('set poolSize 5')).text, 'Saved: advanced.poolSize = 5 (was 3).')
  assert.match((await run('set advanced.poolSize 6')).text, /takes a whole number from 1 to 5/)
  assert.match((await run('set advanced.afterthoughtMax 0')).text, /takes a whole number from 1 to 5/)
  assert.match((await run('set advanced.conversationPool maybe')).text, /advanced\.conversationPool takes on or off\./)
  assert.equal((await run('reset advanced.poolSize')).text, 'Reset: advanced.poolSize is back to its default (3).')
  assert.deepEqual(writes.map((ops) => [ops[0].op, ops[0].path.join('.'), ops[0].value]), [
    ['set', 'advanced.afterthought', true],
    ['set', 'advanced.afterthoughtMax', 4],
    ['set', 'advanced.conversationPool', true],
    ['set', 'advanced.poolSize', 5],
    ['unset', 'advanced.poolSize', undefined],
  ])
})

test('the recent pool settings are listed, set by bare name and reset', async () => {
  const { run, writes } = harness()
  const config = await run('config')
  assert.ok(config.text.includes('advanced.recentPool = off  (off)'), config.text)
  assert.ok(config.text.includes('advanced.recentSize = 3  (3)'))
  assert.ok(config.text.includes('advanced.recentDays = 7  (7)'))
  assert.equal((await run('set recentpool on')).text, 'Saved: advanced.recentPool = on (was off).')
  assert.equal((await run('set advanced.recentDays 30')).text, 'Saved: advanced.recentDays = 30 (was 7).')
  assert.match((await run('set advanced.recentDays 31')).text, /takes a whole number from 1 to 30/)
  assert.match((await run('set recentsize 6')).text, /takes a whole number from 1 to 5/)
  assert.equal((await run('reset advanced.recentDays')).text, 'Reset: advanced.recentDays is back to its default (7).')
  assert.deepEqual(writes.map((ops) => [ops[0].op, ops[0].path.join('.'), ops[0].value]), [
    ['set', 'advanced.recentPool', true],
    ['set', 'advanced.recentDays', 30],
    ['unset', 'advanced.recentDays', undefined],
  ])
})

test('changelog prints the newest released entry as plain text, in both languages', async () => {
  const entries = [
    { version: '0.3.2', title: '0.3.2', body: '日期：2026-11-18。\n\n### 新增\n\n- 新设置 `advanced.linkedRecall`，**默认开**，见 [说明](https://example.org/a)。\n  - 子项' },
    { version: '0.3.1', title: '0.3.1', body: 'older' },
  ]
  const en = await harness({ changelog: () => ({ version: '0.3.2', entries }) }).run('changelog')
  assert.equal(en.kind, 'success')
  assert.match(en.text, /^Changelog 0\.3\.2 \(written in Chinese\)\n\n日期：2026-11-18。/)
  assert.ok(en.text.includes('新增\n'))
  assert.ok(en.text.includes('- 新设置 advanced.linkedRecall，默认开，见 说明 (https://example.org/a)。'), en.text)
  assert.ok(!en.text.includes('**') && !en.text.includes('`') && !en.text.includes('###'))
  assert.ok(!en.text.includes('older'), 'only the newest')
  const zh = await harness({ lang: 'zh', changelog: () => ({ version: '0.3.2', entries }) }).run('changelog')
  assert.match(zh.text, /^更新日志 0\.3\.2\n\n/)
  assert.match((await harness({ changelog: () => ({ version: '0.3.2', entries }) }).run('changelog now')).text, /takes no arguments/)
})

test('changelog says so when there is nothing to show, and is in the usage', async () => {
  assert.equal((await harness({ changelog: () => ({ version: '0.3.2', entries: [] }) }).run('changelog')).text, 'The changelog could not be shown.')
  assert.equal((await harness().run('changelog')).text, 'The changelog could not be shown.', 'no source at all')
  assert.equal((await harness({ lang: 'zh', changelog: () => ({ entries: [] }) }).run('changelog')).text, '无法显示更新日志。')
  assert.ok((await harness().run('')).text.includes('changelog'))
  assert.ok((await harness({ lang: 'zh' }).run('')).text.includes('changelog'))
  assert.ok(harness().command.input.hint.includes('changelog'))
})

test('the block wording and the cap are listed, set by bare name and reset', async () => {
  const { run, writes } = harness()
  const config = await run('config')
  assert.ok(config.text.includes('advanced.blockTone = normal  (normal)'), config.text)
  assert.ok(config.text.includes('advanced.maxNewPerTurn = 0  (0)'))
  assert.equal((await run('set blocktone strict')).text, 'Saved: advanced.blockTone = strict (was normal).')
  assert.equal((await run('set advanced.blockTone bare')).text, 'Saved: advanced.blockTone = bare (was strict).')
  assert.equal((await run('reset advanced.blockTone')).text, 'Reset: advanced.blockTone is back to its default (normal).')
  assert.match((await run('set advanced.blockTone loud')).text, /advanced\.blockTone/)
  assert.equal((await run('set maxnewperturn 3')).text, 'Saved: advanced.maxNewPerTurn = 3 (was 0).')
  assert.match((await run('set advanced.maxNewPerTurn 6')).text, /takes a whole number from 0 to 5/)
  assert.equal((await run('reset advanced.maxNewPerTurn')).text, 'Reset: advanced.maxNewPerTurn is back to its default (0).')
  assert.deepEqual(writes.map((ops) => [ops[0].op, ops[0].path.join('.'), ops[0].value]), [
    ['set', 'advanced.blockTone', 'strict'],
    ['set', 'advanced.blockTone', 'bare'],
    ['unset', 'advanced.blockTone', undefined],
    ['set', 'advanced.maxNewPerTurn', 3],
    ['unset', 'advanced.maxNewPerTurn', undefined],
  ])
})

test('the relevance check settings are listed, set by bare name and reset', async () => {
  const { run, writes } = harness()
  const config = await run('config')
  assert.ok(config.text.includes('advanced.relevanceCheck = off'), config.text)
  assert.ok(config.text.includes('advanced.relevanceTimeoutMs = 3000'))
  assert.equal((await run('set relevancecheck on')).text, 'Saved: advanced.relevanceCheck = on (was off).')
  assert.equal((await run('set relevancemodel small-model')).text, 'Saved: advanced.relevanceModel = small-model (was (empty)).')
  assert.match((await run('set advanced.relevanceTimeoutMs 100')).text, /takes a whole number from 500 to 10000/)
  assert.equal((await run('set relevancetimeoutms 5000')).text, 'Saved: advanced.relevanceTimeoutMs = 5000 (was 3000).')
  assert.deepEqual(writes.map((ops) => [ops[0].op, ops[0].path.join('.'), ops[0].value]), [
    ['set', 'advanced.relevanceCheck', true],
    ['set', 'advanced.relevanceModel', 'small-model'],
    ['set', 'advanced.relevanceTimeoutMs', 5000],
  ])
})

test('use chooses a tier and says when it cannot run yet', async () => {
  const { run, writes } = harness()
  const local = await run('use local')
  assert.equal(local.kind, 'success')
  assert.match(local.text, /^Saved: retrieval\.tier = local \(was auto\)\.\nNot available right now: it needs the dsh-feiyu-memgate-embed add-on/)
  const auto = await run('use AUTO')
  assert.match(auto.text, /auto uses keyword right now\./)
  assert.deepEqual(writes.map((ops) => ops[0].value), ['local', 'auto'])
  const bad = await run('use vector')
  assert.equal(bad.kind, 'error')
  assert.match(bad.text, /Unknown tier "vector"\. Tiers: auto, keyword, endpoint, local\./)
  assert.equal((await run('use')).kind, 'error')
})

test('unexpected input is refused with the usage', async () => {
  const { run } = harness()
  const unknown = await run('launch')
  assert.equal(unknown.kind, 'error')
  assert.match(unknown.text, /^Unknown subcommand "launch"\.\nUsage:/)
  assert.equal((await run('status now')).text, '/memgate status takes no arguments.')
  assert.equal((await run('config all')).kind, 'error')
  assert.equal((await run('tiers x')).kind, 'error')
  assert.equal((await run('set advanced.recallLimit 3\nset advanced.recallLimit 4')).text, 'The command takes one line.')
  assert.equal((await run('!!!')).text.split('\n')[0], 'Unknown subcommand.')
})

test('a host without Settings, a refused write and a broken status read are reported, never thrown', async () => {
  const none = harness({ write: async () => ({ ok: false, reason: 'unavailable' }) })
  assert.match((await none.run('off autoRecall')).text, /no Settings service/)
  const refused = harness({ write: async () => ({ ok: false, reason: 'refused', kind: 'SettingsConflictError' }) })
  const reply = await refused.run('set advanced.recallLimit 4')
  assert.equal(reply.kind, 'error')
  assert.match(reply.text, /refused the change \(SettingsConflictError\)/)
  const throwing = harness({ write: async () => { throw new Error(TOKEN) } })
  const thrown = await throwing.run(`set external.token ${TOKEN}`)
  assert.equal(thrown.kind, 'error')
  assert.ok(!thrown.text.includes(TOKEN))
  const broken = harness({ status: async () => { throw new Error('down') } })
  assert.equal((await broken.run('status')).kind, 'error')
})

test('setting names resolve exactly, by unique bare key, or not at all', () => {
  assert.equal(resolveSetting('ADVANCED.RECALLLIMIT').key, 'advanced.recallLimit')
  assert.equal(resolveSetting('python').key, 'gateway.python')
  assert.match(resolveSetting('token').key, /^external\.token$/)
  assert.ok(resolveSetting('url').error)
  assert.ok(resolveSetting('nope').error)
})
