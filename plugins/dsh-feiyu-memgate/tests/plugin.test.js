// The plugin inside a fake host: automatic recall, the tools, the prompt section, automatic capture,
// live switches and unload. No real host, gateway or network.
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  MODULE_NAMES, PROMPT_ORDER, PROMPT_SECTION, PROMPT_TEXTS, SOURCE_KIND, TOOL_REMEMBER, TOOL_SEARCH, applyPlugin, captureBatches, localDate, localTime,
} from '../src/plugin.js'
import { createFormatter } from '../src/block.js'
import { DEFAULTS } from '../src/config.js'
import {
  assistantMessage, createFakeHost, fakeClients, fakeManagers, fakeSession, preStepPayload, tick, userMessage, volatile,
} from './helpers/fake-host.js'

const MEMORY = 'The user drinks oolong tea every morning'
const RESULT = { text: MEMORY, source: 'memories', id: 'm1', score: 0.9 }
const EXTERNAL = { url: 'http://127.0.0.1:9', token: 'fake-token-for-tests-only' }

// A plugin wired to fakes. `config` is plain settings unless a group is given as volatile().
function setup({ config = {}, handlers, managed = false, now, deps = {} } = {}) {
  const host = createFakeHost()
  const clients = fakeClients(handlers)
  const managers = fakeManagers()
  const full = { advanced: { language: 'en' }, ...config }
  if (!managed && !full.gateway) {
    full.gateway = { mode: 'external' }
    full.external ??= EXTERNAL
  }
  const plugin = applyPlugin(host.ctx, full, {
    createClient: clients.createClient,
    createGatewayManager: managers.createGatewayManager,
    importHost: false,
    ...(now ? { now } : {}),
    ...deps,
  })
  return { host, clients, managers, plugin, config: full }
}

const blockOf = (message) => message.content.map((b) => b.text).join('')
const recallFound = async () => ({ results: [RESULT], more: 0, failed: 0, busy: 0 })

// A fixed clock: 2026-11-17 20:15:03 on this machine's calendar and clock, whatever its time zone.
const NOW = new Date(2026, 10, 17, 20, 15, 3)
const offsetText = (date) => {
  const minutes = -date.getTimezoneOffset()
  const abs = Math.abs(minutes)
  return `${minutes < 0 ? '-' : '+'}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`
}
const NOW_TIME = `2026-11-17T20:15:03${offsetText(NOW)}`

test('registers every default module and nothing for autoCapture', () => {
  const { host, plugin } = setup()
  assert.deepEqual(plugin.activeModules().sort(), ['autoRecall', 'commands', 'promptSection', 'rememberTool', 'searchTool'])
  assert.equal(host.commands.size, 0, 'no chat command while the host has no command registry')
  assert.equal(host.count('agent/pre-step'), 1)
  assert.deepEqual([...host.tools.keys()].sort(), [TOOL_REMEMBER, TOOL_SEARCH])
  assert.equal(host.sections.size, 1)
  assert.equal(host.count('session/event'), 0)
  assert.equal(host.count('loader/volatile-update'), 1)
})

test('recall runs on step 1 of a user turn and places the block right after the user message', async () => {
  const { host, clients } = setup({ handlers: { recall: recallFound } })
  const payload = preStepPayload()
  const decision = await host.preStep(payload)
  assert.equal(clients.calls.recall.length, 1)
  assert.equal(clients.calls.recall[0].query, 'What tea do I like in the morning?')
  assert.equal(decision.kind, 'enter')
  assert.equal(decision.messages.length, 3)
  assert.equal(decision.messages[0], payload.messages[0])
  const block = decision.messages[1]
  assert.equal(block.role, 'user')
  assert.deepEqual(block.source, { kind: SOURCE_KIND, form: 'recall' })
  assert.equal(typeof block.id, 'string')
  assert.ok(Object.isFrozen(block) && Object.isFrozen(block.content))
  assert.match(blockOf(block), /^<memory_data>\n/)
  assert.ok(blockOf(block).includes(MEMORY))
  assert.equal(decision.messages[2].source.kind, 'context', 'the host context snapshot stays last')
})

test('recall skips later steps, subagents, turns without user text and null queries', async () => {
  const { host, clients } = setup({ handlers: { recall: recallFound } })
  for (const payload of [
    preStepPayload({ step: 2 }),
    preStepPayload({ session: fakeSession({ header: { origin: 'subagent', delegationDepth: 1 } }) }),
    preStepPayload({ session: fakeSession({ header: { delegationDepth: 2 } }) }),
    preStepPayload({ messages: [userMessage('goal round', { kind: 'goal' })] }),
    preStepPayload({ text: '/compact' }),
    preStepPayload({ text: '嗯' }),
  ]) {
    const decision = await host.preStep(payload)
    assert.equal(decision.messages.length, payload.messages.length + 1, 'only the host context was added')
  }
  assert.equal(clients.calls.recall.length, 0)
})

test('memory blocks are stripped from the current and previous message before the query is built', async () => {
  const { host, clients } = setup({ handlers: { recall: recallFound } })
  const old = `<memory_data>\nBelow are memories.\n- (memory) something private\n</memory_data>`
  const session = fakeSession({ history: [userMessage(`${old}\n\nwhere do I keep my bike`), assistantMessage('In the shed.')] })
  await host.preStep(preStepPayload({ text: `${old}\n\npump?`, session }))
  const { query } = clients.calls.recall[0]
  assert.ok(!query.includes('memory_data') && !query.includes('something private'), query)
  assert.equal(query, 'where do I keep my bike\npump?', 'a short message borrows the previous one')
})

test('an identical block already in the last plugin message is not added again', async () => {
  const first = setup({ handlers: { recall: recallFound } })
  const decision = await first.host.preStep(preStepPayload())
  const previous = decision.messages[1]
  const session = fakeSession({ history: [userMessage('What tea do I like?'), previous, assistantMessage('Oolong.')] })
  const again = await first.host.preStep(preStepPayload({ session }))
  assert.equal(again.messages.length, 2, 'no second copy of the same block')
  assert.equal(first.clients.calls.recall.length, 2)
})

test('an empty result adds nothing', async () => {
  const { host } = setup()
  const decision = await host.preStep(preStepPayload())
  assert.equal(decision.messages.length, 2)
})

test('a slow lookup is dropped after advanced.recallTimeoutMs and the turn goes on', async () => {
  let seenSignal = null
  const { host } = setup({
    config: { advanced: { language: 'en', recallTimeoutMs: 500 } },
    handlers: { recall: (q, opts) => { seenSignal = opts.signal; return new Promise(() => {}) } },
  })
  const started = Date.now()
  const decision = await host.preStep(preStepPayload())
  const took = Date.now() - started
  assert.equal(decision.messages.length, 3, 'the turn goes on, with the note that the lookup failed')
  assert.equal(decision.messages[1].source.form, 'notice')
  assert.ok(took >= 450 && took < 2500, `waited ${took} ms`)
  assert.equal(seenSignal.aborted, true, 'the lookup is told to stop')
})

test('aborting the turn aborts the lookup and returns at once', async () => {
  let seenSignal = null
  const { host } = setup({ handlers: { recall: (q, opts) => { seenSignal = opts.signal; return new Promise(() => {}) } } })
  const turn = new AbortController()
  const started = Date.now()
  const pending = host.preStep(preStepPayload({ signal: turn.signal }))
  await tick(50)
  turn.abort()
  const decision = await pending
  assert.ok(Date.now() - started < 1500)
  assert.equal(decision.messages.length, 2)
  assert.equal(seenSignal.aborted, true)
})

test('a rejection decided further down the chain is passed through unchanged', async () => {
  const { host, clients } = setup({ handlers: { recall: recallFound } })
  const decision = await host.preStep(preStepPayload(), () => Promise.resolve({ kind: 'reject' }))
  assert.deepEqual(decision, { kind: 'reject' })
  assert.equal(clients.calls.recall.length, 1)
})

test('an error from the rest of the chain is not swallowed', async () => {
  const { host } = setup({ handlers: { recall: recallFound } })
  await assert.rejects(host.preStep(preStepPayload(), () => Promise.reject(new Error('host failure'))), /host failure/)
})

test('recall fails open on every error of its own', async () => {
  const throwing = [
    { recall: () => { throw new Error('sync throw') } },
    { recall: () => Promise.reject(new Error('async reject')) },
    { recall: async () => ({ results: 'not a list' }) },
    { recall: async () => null },
  ]
  for (const handlers of throwing) {
    const { host } = setup({ handlers, config: { advanced: { language: 'en', failureNote: false } } })
    const decision = await host.preStep(preStepPayload())
    assert.equal(decision.kind, 'enter')
    assert.equal(decision.messages.length, 2, 'nothing added when the failure note is off')
    // With the note on (the default) the same failures add exactly the note.
    const noted = await setup({ handlers }).host.preStep(preStepPayload())
    assert.equal(noted.kind, 'enter')
    assert.deepEqual(noted.messages.map((m) => m.source.kind), ['user', SOURCE_KIND, 'context'])
    assert.equal(noted.messages[1].source.form, 'notice')
  }
  // A broken session history and a broken message factory.
  const broken = setup({ handlers: { recall: recallFound } })
  const session = { id: 's', header: {}, deriveMessages: () => { throw new Error('history broke') } }
  assert.equal((await broken.host.preStep(preStepPayload({ session }))).messages.length, 2)
  const host = createFakeHost()
  const clients = fakeClients({ recall: recallFound })
  applyPlugin(host.ctx, { gateway: { mode: 'external' }, external: EXTERNAL }, {
    createClient: clients.createClient, importHost: false, createUserMessage: () => { throw new Error('factory broke') },
  })
  const decision = await host.preStep(preStepPayload())
  assert.equal(decision.messages.length, 3, 'falls back to building the message itself')
  assert.equal(decision.messages[1].source.kind, SOURCE_KIND)
})

test('the host message factory is used when present', async () => {
  const host = createFakeHost()
  const clients = fakeClients({ recall: recallFound })
  const made = []
  applyPlugin(host.ctx, { gateway: { mode: 'external' }, external: EXTERNAL }, {
    createClient: clients.createClient,
    importHost: false,
    createUserMessage: (input) => {
      made.push(input)
      return Object.freeze({ ...input, role: 'user', id: 'from-host' })
    },
  })
  const decision = await host.preStep(preStepPayload())
  assert.equal(made.length, 1)
  assert.equal(decision.messages[1].id, 'from-host')
})

test('memgate_remember maps every outcome to a truthful sentence', async () => {
  const expected = {
    saved: /^Saved/,
    skipped: /^Not saved: the memory store looked at it/,
    limited: /^Not saved: today's limit/,
    unsure: /^Unknown: .*may or may not have been saved/,
    failed: /^Not saved: the memory store refused it/,
  }
  for (const [status, pattern] of Object.entries(expected)) {
    const { host, clients } = setup({ handlers: { remember: async () => status } })
    const tool = host.tools.get(TOOL_REMEMBER)
    const value = await tool.execute({ text: '  I prefer green tea.  ' }, { signal: new AbortController().signal })
    assert.equal(value.status, status)
    assert.match(value.text, pattern)
    assert.deepEqual(tool.output.render({}, value), [{ type: 'text', text: value.text }])
    assert.equal(clients.calls.remember[0].text, 'I prefer green tea.')
    if (status !== 'saved') assert.doesNotMatch(value.text, /^Saved/)
  }
})

test('memgate_remember speaks Chinese when the language is zh', async () => {
  const { host } = setup({ config: { advanced: { language: 'zh' } }, handlers: { remember: async () => 'unsure' } })
  const value = await host.tools.get(TOOL_REMEMBER).execute({ text: '我喜欢绿茶' }, {})
  assert.equal(value.status, 'unsure')
  assert.match(value.text, /^结果不确定/)
})

test('memgate_remember refuses bad input and reports an unavailable memory', async () => {
  const { host, clients } = setup()
  const tool = host.tools.get(TOOL_REMEMBER)
  assert.equal((await tool.execute({ text: '   ' }, {})).status, 'invalid')
  assert.equal((await tool.execute({}, {})).status, 'invalid')
  assert.equal((await tool.execute({ text: 'x'.repeat(4001) }, {})).status, 'invalid')
  assert.equal(clients.calls.remember.length, 0)
  const off = setup({ config: { gateway: { mode: 'external' }, external: { url: '', token: '' } } })
  const value = await off.host.tools.get(TOOL_REMEMBER).execute({ text: 'a fact' }, {})
  assert.equal(value.status, 'unavailable')
  // A client that throws: the write may have been sent, so never "saved".
  const broken = setup({ handlers: { remember: () => { throw new Error('boom') } } })
  assert.equal((await broken.host.tools.get(TOOL_REMEMBER).execute({ text: 'a fact' }, {})).status, 'unsure')
})

test('memgate_search returns the block, or tells "nothing found" from "unavailable"', async () => {
  const found = setup({ handlers: { recall: recallFound } })
  const tool = found.host.tools.get(TOOL_SEARCH)
  const hit = await tool.execute({ query: 'tea', limit: 3 }, {})
  assert.equal(hit.status, 'found')
  assert.ok(hit.text.startsWith('<memory_data>') && hit.text.includes(MEMORY))
  assert.equal(found.clients.calls.recall[0].limit, 3)
  assert.equal((await tool.execute({ query: 'tea', limit: 99 }, {})).status, 'found')
  assert.equal(found.clients.calls.recall[1].limit, 5, 'an out-of-range limit falls back to the setting')

  const none = setup()
  const empty = await none.host.tools.get(TOOL_SEARCH).execute({ query: 'tea' }, {})
  assert.equal(empty.status, 'none')
  assert.match(empty.text, /Nothing related/)

  for (const recall of [async () => null, async () => ({ results: [], more: 0, failed: 1, busy: 0 }), () => { throw new Error('x') }]) {
    const down = setup({ handlers: { recall } })
    const value = await down.host.tools.get(TOOL_SEARCH).execute({ query: 'tea' }, {})
    assert.equal(value.status, 'unavailable')
    assert.match(value.text, /unavailable/)
  }
  const bad = await none.host.tools.get(TOOL_SEARCH).execute({ query: 'x'.repeat(501) }, {})
  assert.equal(bad.status, 'invalid')
})

test('tool definitions use only the schema subset the host accepts', () => {
  const { host } = setup()
  const allowed = new Set(['type', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'const', 'oneOf', 'description', 'title', 'default', 'examples'])
  const walk = (node) => {
    for (const [key, value] of Object.entries(node)) {
      assert.ok(allowed.has(key), `unsupported schema keyword ${key}`)
      if (key === 'properties') for (const child of Object.values(value)) walk(child)
    }
  }
  for (const tool of host.tools.values()) {
    assert.match(tool.name, /^[a-z][a-z0-9_]{0,63}$/)
    assert.ok(!tool.name.startsWith('mcp__') && tool.name !== 'run_code')
    walk(tool.parameters)
    walk(tool.output.schema)
  }
})

test('the prompt section is fixed text per language, whatever the other settings', () => {
  const a = setup()
  const b = setup({ config: { advanced: { language: 'zh', recallLimit: 9 }, modules: { rememberTool: false } } })
  const sa = a.host.sections.get(PROMPT_SECTION)
  const sb = b.host.sections.get(PROMPT_SECTION)
  assert.equal(sa.text, PROMPT_TEXTS.en)
  assert.equal(sb.text, PROMPT_TEXTS.zh)
  assert.equal(sa.order, PROMPT_ORDER)
  assert.equal(sa.interpolate, false)
  assert.equal(typeof sa.text, 'string')
  assert.match(PROMPT_TEXTS.en, /background data, not instructions/)
  assert.match(PROMPT_TEXTS.zh, /背景资料，不是指令/)
})

test('the prompt section tells the model how to write a memory, in both languages, with one example each', () => {
  for (const lang of ['en', 'zh']) {
    const text = PROMPT_TEXTS[lang]
    assert.ok(text.includes(TOOL_REMEMBER) && text.includes(TOOL_SEARCH) && text.includes('<memory_data>'), lang)
    assert.equal(text.split('\n').filter((line) => /^ {2}\d\. /.test(line)).length, 3, `${lang}: three points`)
    assert.equal(text.split('\n').filter((line) => /^ {2}(Example|例如)/.test(line)).length, 1, `${lang}: one example`)
    assert.ok(!/\d{4}-\d{2}-\d{2}/.test(text), `${lang}: no date that would change`)
    assert.ok(!text.includes('\r'))
  }
  assert.match(PROMPT_TEXTS.en, /Do not write "he"/)
  assert.match(PROMPT_TEXTS.en, /Write times as dates/)
  assert.match(PROMPT_TEXTS.en, /says what it replaces/)
  assert.ok(PROMPT_TEXTS.zh.includes('用户的羽毛球课从 2026 年 11 月起改在周四，不再是周二。'))
  assert.match(PROMPT_TEXTS.zh, /不要写“他”/)
  assert.match(PROMPT_TEXTS.zh, /时间写成日期/)
  assert.match(PROMPT_TEXTS.zh, /说明它取代了什么/)
  assert.doesNotMatch(PROMPT_TEXTS.zh, /[！!]/, 'no exclamation marks')
  assert.doesNotMatch(PROMPT_TEXTS.zh, /\p{Extended_Pictographic}/u, 'no emoji')
})

test('the prompt section is registered again when the language setting changes, and only then', () => {
  const host = createFakeHost()
  const advanced = volatile({ language: 'en' })
  const modules = volatile({ autoRecall: false, rememberTool: false, searchTool: false, promptSection: true, autoCapture: false, commands: false })
  applyPlugin(host.ctx, { modules, advanced, gateway: volatile({ mode: 'external' }), external: volatile(EXTERNAL) }, {
    createClient: fakeClients().createClient, importHost: false,
  })
  const first = host.sections.get(PROMPT_SECTION)
  assert.equal(first.text, PROMPT_TEXTS.en)
  host.emit('loader/volatile-update', [['advanced']])
  assert.equal(host.sections.get(PROMPT_SECTION), first, 'same language: the same registration')
  advanced.set({ language: 'zh' })
  host.emit('loader/volatile-update', [['advanced']])
  assert.equal(host.sections.size, 1)
  assert.equal(host.sections.get(PROMPT_SECTION).text, PROMPT_TEXTS.zh)
  advanced.set({ language: 'en' })
  host.emit('loader/volatile-update', [['advanced']])
  assert.equal(host.sections.get(PROMPT_SECTION).text, PROMPT_TEXTS.en)
  // Switched off, the section is gone and a language change does not bring it back.
  modules.set({ autoRecall: false, rememberTool: false, searchTool: false, promptSection: false, autoCapture: false, commands: false })
  host.emit('loader/volatile-update', [['modules']])
  advanced.set({ language: 'zh' })
  host.emit('loader/volatile-update', [['advanced']])
  assert.equal(host.sections.size, 0)
})

test('auto language follows the host locale for the prompt section', () => {
  const zh = setup({ config: { advanced: { language: 'auto' } }, deps: { locale: 'zh-CN' } })
  assert.equal(zh.host.sections.get(PROMPT_SECTION).text, PROMPT_TEXTS.zh)
  const en = setup({ config: { advanced: { language: 'auto' } }, deps: { locale: 'en-US' } })
  assert.equal(en.host.sections.get(PROMPT_SECTION).text, PROMPT_TEXTS.en)
})

test('memgate_remember describes the three rules of a good memory, in English', () => {
  const { host } = setup()
  const tool = host.tools.get(TOOL_REMEMBER)
  const description = tool.description
  const field = tool.parameters.properties.text.description
  for (const text of [description, field]) {
    assert.match(text, /who or what/)
    assert.match(text, /as dates|dates, not/)
    assert.match(text, /replaces/)
    assert.ok(!/[一-鿿]/.test(text), 'tool descriptions stay English')
  }
  assert.match(description, /ranked first/)
})

test('autoCapture sends the user messages of a turn after it ends, stripped, and only when on', async () => {
  const { host, clients } = setup({ config: { modules: { autoCapture: true } }, now: () => NOW })
  assert.equal(host.count('session/event'), 1)
  const session = fakeSession()
  const child = fakeSession({ id: 'child', header: { origin: 'subagent', delegationDepth: 1 } })
  host.emit('session/event', session, { type: 'turn/start', data: { turn: 1 } })
  host.emit('session/event', session, { type: 'user/message', data: userMessage('<memory_data>\nold\n</memory_data>\nI moved to Hangzhou') })
  host.emit('session/event', session, { type: 'user/message', data: userMessage('file changed', { kind: 'file-watch' }) })
  host.emit('session/event', child, { type: 'user/message', data: userMessage('subagent task') })
  host.emit('session/event', session, { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })
  host.emit('session/event', child, { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })
  await tick(10)
  assert.equal(clients.calls.rememberMessages.length, 1)
  assert.deepEqual(clients.calls.rememberMessages[0].messages, [{ role: 'user', content: 'I moved to Hangzhou', time: NOW_TIME }])

  const off = setup()
  assert.equal(off.host.count('session/event'), 0)
})

test('autoCapture never lets a failing write escape', async () => {
  const rejections = []
  const onRejection = (reason) => rejections.push(reason)
  process.on('unhandledRejection', onRejection)
  try {
    const { host } = setup({ config: { modules: { autoCapture: true } }, handlers: { rememberMessages: () => Promise.reject(new Error('down')) } })
    const session = fakeSession()
    host.emit('session/event', session, { type: 'user/message', data: userMessage('remember me') })
    host.emit('session/event', session, { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })
    await tick(20)
  } finally {
    process.off('unhandledRejection', onRejection)
  }
  assert.deepEqual(rejections, [])
})

test('capture batches respect the gateway limits and never cut a message', () => {
  const many = Array.from({ length: 45 }, (_, i) => ({ role: 'user', content: `message ${i}` }))
  assert.deepEqual(captureBatches(many).map((b) => b.length), [20, 20, 5])
  const big = [{ role: 'user', content: 'a'.repeat(3000) }, { role: 'user', content: 'b'.repeat(4001) }, ...Array.from({ length: 6 }, () => ({ role: 'user', content: 'c'.repeat(3000) }))]
  const batches = captureBatches(big)
  assert.ok(batches.flat().every((m) => m.content.length <= 4000))
  assert.ok(batches.every((b) => b.reduce((n, m) => n + m.content.length, 0) <= 16000))
  assert.equal(batches.flat().length, 7)
})

test('switches take effect live through loader/volatile-update', async () => {
  const host = createFakeHost()
  const clients = fakeClients()
  const managers = fakeManagers()
  const modules = volatile({ autoRecall: true, rememberTool: true, searchTool: true, promptSection: true, autoCapture: false, commands: false })
  const config = { modules, advanced: volatile({ language: 'en' }), gateway: volatile({}) }
  const plugin = applyPlugin(host.ctx, config, { createClient: clients.createClient, createGatewayManager: managers.createGatewayManager, importHost: false })
  await plugin.settled()
  assert.equal(managers.created.length, 1)
  assert.equal(managers.created[0].started, 1)

  modules.set({ autoRecall: false, rememberTool: true, searchTool: false, promptSection: false, autoCapture: true, commands: false })
  host.emit('loader/volatile-update', [['modules']])
  assert.equal(host.count('agent/pre-step'), 0)
  assert.deepEqual([...host.tools.keys()], [TOOL_REMEMBER])
  assert.equal(host.sections.size, 0)
  assert.equal(host.count('session/event'), 1)
  assert.equal(managers.created.length, 1, 'memory settings did not change: the same gateway keeps running')

  // Every memory module off: the gateway goes too.
  modules.set({ autoRecall: false, rememberTool: false, searchTool: false, promptSection: true, autoCapture: false, commands: false })
  host.emit('loader/volatile-update', [['modules']])
  await plugin.settled()
  assert.equal(managers.created[0].stopped, 1)
  assert.deepEqual(plugin.activeModules(), ['promptSection'])
  assert.equal(host.tools.size, 0)
})

test('a gateway setting change restarts the managed gateway once', async () => {
  const host = createFakeHost()
  const managers = fakeManagers()
  const advanced = volatile({ dailyWrites: 50 })
  const plugin = applyPlugin(host.ctx, { gateway: volatile({}), advanced }, { createClient: fakeClients().createClient, createGatewayManager: managers.createGatewayManager, importHost: false })
  await plugin.settled()
  advanced.set({ dailyWrites: 10 })
  host.emit('loader/volatile-update', [['advanced']])
  await plugin.settled()
  assert.equal(managers.created.length, 2)
  assert.equal(managers.created[0].stopped, 1)
  assert.equal(managers.created[1].started, 1)
  assert.equal(managers.created[1].options.settings.advanced.dailyWrites, 10)
  host.emit('loader/volatile-update', [['advanced']])
  await plugin.settled()
  assert.equal(managers.created.length, 2, 'an unchanged setting does nothing')
  // Recall limits and the language are read per call: no restart for them.
  advanced.set({ dailyWrites: 10, recallLimit: 9, language: 'zh' })
  host.emit('loader/volatile-update', [['advanced']])
  await plugin.settled()
  assert.equal(managers.created.length, 2)
})

test('external mode starts no gateway and warns once when it has no address', () => {
  const ok = setup()
  assert.equal(ok.managers.created.length, 0)
  const missing = setup({ config: { gateway: { mode: 'external' } } })
  assert.equal(missing.managers.created.length, 0)
  missing.host.emit('loader/volatile-update', [['gateway']])
  assert.equal(missing.host.logs.filter((l) => l.text.includes('external mode needs')).length, 1)
})

test('unload removes everything and stops the gateway', async () => {
  const { host, managers } = setup({ managed: true, config: { modules: { autoCapture: true } } })
  await tick(0)
  assert.equal(managers.created[0].started, 1)
  await host.unload()
  assert.equal(host.count('agent/pre-step'), 0)
  assert.equal(host.count('session/event'), 0)
  assert.equal(host.count('session/disposed'), 0)
  assert.equal(host.tools.size, 0)
  assert.equal(host.sections.size, 0)
  assert.equal(managers.created[0].stopped, 1)
})

test('nothing logged contains a query, a memory or a token', async () => {
  const secretQuery = 'my bank pin is in the blue notebook'
  const secretMemory = 'the blue notebook is in the second drawer'
  const scenarios = [
    { recall: () => { throw new Error(secretQuery) }, remember: () => { throw new Error(secretMemory) }, rememberMessages: () => Promise.reject(new Error(secretMemory)) },
    { recall: async () => ({ results: [{ text: secretMemory }], more: 0, failed: 0, busy: 0 }) },
  ]
  const allLogs = []
  for (const handlers of scenarios) {
    const { host } = setup({ handlers, config: { modules: { autoCapture: true } } })
    await host.preStep(preStepPayload({ text: secretQuery }))
    await host.tools.get(TOOL_REMEMBER).execute({ text: secretMemory }, {})
    await host.tools.get(TOOL_SEARCH).execute({ query: secretQuery }, {})
    const session = fakeSession()
    host.emit('session/event', session, { type: 'user/message', data: userMessage(secretMemory) })
    host.emit('session/event', session, { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })
    await tick(10)
    allLogs.push(...host.logs.map((l) => l.text))
  }
  assert.ok(allLogs.length > 0, 'failures were logged')
  for (const line of allLogs) {
    for (const secret of [secretQuery, secretMemory, EXTERNAL.token, 'blue notebook']) assert.ok(!line.includes(secret), line)
  }
})

test('module names match the settings', () => {
  assert.deepEqual([...MODULE_NAMES].sort(), ['autoCapture', 'autoRecall', 'commands', 'promptSection', 'rememberTool', 'searchTool'])
  for (const name of MODULE_NAMES) assert.ok(Object.hasOwn(DEFAULTS.modules, name), name)
})

// ------------------------------------------------------------------ the chat experience (0.3.1)

const MINT = { text: 'The mint on the balcony has been trimmed twice', source: 'memories', id: 'a', date: '2026-10-27', score: 0.9 }
const TIRAMISU = { text: 'The user plans to learn tiramisu next month', source: 'memories', id: 'b', date: '2026-11-16', score: 0.8 }
const GREEN = { text: 'The user prefers green tea in the evening', source: 'memories', id: 'c', score: 0.7 }
const itemsOf = (message) => blockOf(message).split('\n').filter((line) => line.startsWith('- '))
const pluginMessages = (decision) => decision.messages.filter((m) => m.source.kind === SOURCE_KIND)
const NOTICE = {
  zh: '（这一轮没能查询长期记忆：查询超时或记忆服务暂时不可用。不要据此认为没有相关记忆。）',
  en: '(Long-term memory could not be searched this turn: the lookup timed out or the memory service is not available right now. Do not conclude from this that there is no related memory.)',
}

// A chat of several turns against a plugin with a fixed clock. Each answer is what the memory returns for
// that turn: a list of results, null (the lookup failed) or 'throw'. The history grows as the host's would:
// the user's message, what the plugin added, the model's reply. Returns what the plugin added per turn.
async function chat(answers, { config, now = () => NOW, deps, compactAfter } = {}) {
  const queue = [...answers]
  const { host } = setup({
    config,
    now,
    deps,
    handlers: {
      recall: async () => {
        const answer = queue.shift()
        if (answer === 'throw') throw new Error('lookup broke')
        return answer === null ? null : { results: answer, more: 0, failed: 0, busy: 0 }
      },
    },
  })
  const history = []
  const session = fakeSession({ history })
  const turns = []
  for (let i = 0; i < answers.length; i++) {
    const payload = preStepPayload({ text: `What do I know about topic number ${i}?`, session })
    const decision = await host.preStep(payload)
    const mine = pluginMessages(decision)
    turns.push(mine)
    history.push(payload.messages[0], ...mine, assistantMessage('Noted.'))
    if (i === compactAfter) history.length = 0 // the host compacted the conversation
  }
  return turns
}

test('skipRepeats: a memory that a block in the history still shows is not given again', async () => {
  const turns = await chat([[MINT, TIRAMISU], [TIRAMISU, GREEN], [MINT]])
  assert.equal(turns[0].length, 1)
  assert.equal(itemsOf(turns[0][0]).length, 2)
  assert.equal(turns[1].length, 1)
  const second = itemsOf(turns[1][0])
  assert.equal(second.length, 1, 'only the new memory')
  assert.ok(second[0].endsWith(GREEN.text))
  assert.ok(!blockOf(turns[1][0]).includes(TIRAMISU.text))
  assert.deepEqual(turns[2], [], 'everything was shown before: no block at all')
})

test('skipRepeats compares the text only: another label, date or age does not make a memory new', async () => {
  const turns = await chat([[MINT], [{ ...MINT, source: 'other', date: '2026-10-28' }]])
  assert.equal(turns[0].length, 1)
  assert.deepEqual(turns[1], [])
  // A day later the age after the date has changed; the memory is the same one.
  let reads = 0
  const days = await chat([[MINT], [MINT]], { now: () => new Date(2026, 10, reads++ === 0 ? 17 : 18, 9, 0, 0) })
  assert.ok(blockOf(days[0][0]).includes('Today is 2026-11-17.'))
  assert.deepEqual(days[1], [])
})

test('skipRepeats passes the more count through unchanged', async () => {
  const { host } = setup({ handlers: { recall: async () => ({ results: [GREEN], more: 3, failed: 0, busy: 0 }) }, now: () => NOW })
  const decision = await host.preStep(preStepPayload())
  assert.match(blockOf(decision.messages[1]), /\n3 more related memories did not fit\.\n/)
})

test('skipRepeats off: the old rule, only a block equal to the newest block is left out', async () => {
  const config = { advanced: { language: 'en', skipRepeats: false } }
  const turns = await chat([[MINT, TIRAMISU], [TIRAMISU, GREEN], [TIRAMISU, GREEN], [MINT]], { config })
  assert.equal(itemsOf(turns[0][0]).length, 2)
  assert.equal(itemsOf(turns[1][0]).length, 2, 'the whole block again, TIRAMISU included')
  assert.deepEqual(turns[2], [], 'identical to the newest block')
  assert.equal(itemsOf(turns[3][0]).length, 1, 'a block from earlier turns is not looked at')
})

test('a compacted history that no longer holds the block lets the memory in again', async () => {
  const turns = await chat([[MINT, TIRAMISU], [TIRAMISU, GREEN], [MINT]], { compactAfter: 1 })
  assert.equal(itemsOf(turns[1][0]).length, 1)
  assert.equal(turns[2].length, 1, 'the earlier blocks are gone, so MINT is given once more')
  assert.ok(itemsOf(turns[2][0])[0].endsWith(MINT.text))
  const again = await chat([[MINT], [MINT]], { compactAfter: 0 })
  assert.equal(again[1].length, 1)
})

test('only blocks of this plugin count as shown, not other sources or the user text', async () => {
  const { host } = setup({ handlers: { recall: recallFound }, now: () => NOW })
  const foreign = userMessage(`- (memory) ${MEMORY}`, { kind: 'plugin:someone-else', form: 'recall' })
  const quoted = userMessage(`you said: - (memory) ${MEMORY}`)
  const session = fakeSession({ history: [quoted, foreign, assistantMessage('ok')] })
  const decision = await host.preStep(preStepPayload({ session }))
  assert.equal(pluginMessages(decision).length, 1)
})

test('showAge: the block says today (the local date of the clock) and how long ago each memory is', async () => {
  const turns = await chat([[MINT, TIRAMISU, GREEN]])
  assert.ok(blockOf(turns[0][0]).includes('Today is 2026-11-17.'), blockOf(turns[0][0]))
  assert.deepEqual(itemsOf(turns[0][0]), [
    '- (memory, 2026-10-27, 3 weeks ago) ' + MINT.text,
    '- (memory, 2026-11-16, yesterday) ' + TIRAMISU.text,
    '- (memory) ' + GREEN.text,
  ])
  const zh = await chat([[MINT, TIRAMISU]], { config: { advanced: { language: 'zh' } } })
  assert.ok(blockOf(zh[0][0]).includes('今天是 2026-11-17。'))
  assert.ok(blockOf(zh[0][0]).includes('（记忆，2026-10-27，3 周前）'))
  assert.ok(blockOf(zh[0][0]).includes('（记忆，2026-11-16，昨天）'))
  // Late in the evening and just after midnight are different local days.
  const night = await chat([[TIRAMISU]], { now: () => new Date(2026, 10, 17, 23, 59, 59) })
  assert.ok(blockOf(night[0][0]).includes('Today is 2026-11-17.'))
  const after = await chat([[TIRAMISU]], { now: () => new Date(2026, 10, 18, 0, 0, 1) })
  assert.ok(blockOf(after[0][0]).includes('Today is 2026-11-18.'))
  assert.ok(blockOf(after[0][0]).includes('(memory, 2026-11-16, 2 days ago)'))
})

test('showAge off: the block is exactly what it was before, absolute dates only', async () => {
  const turns = await chat([[MINT, TIRAMISU, GREEN]], { config: { advanced: { language: 'en', showAge: false } } })
  assert.equal(blockOf(turns[0][0]), createFormatter({ lang: 'en' }).format([MINT, TIRAMISU, GREEN]))
  assert.doesNotMatch(blockOf(turns[0][0]), /Today is|ago|yesterday/)
})

test('an unusable clock gives a block without today, never an error', async () => {
  for (const now of [() => new Date(NaN), () => 'yesterday', () => { throw new Error('no clock') }]) {
    const turns = await chat([[MINT]], { now })
    assert.equal(turns[0].length, 1)
    assert.doesNotMatch(blockOf(turns[0][0]), /Today is/)
  }
})

test('memgate_search shows today and the ages too, and does not skip what the conversation showed', async () => {
  const { host } = setup({ handlers: { recall: async () => ({ results: [MINT], more: 0, failed: 0, busy: 0 }) }, now: () => NOW })
  const tool = host.tools.get(TOOL_SEARCH)
  const first = await tool.execute({ query: 'mint' }, {})
  const second = await tool.execute({ query: 'mint' }, {})
  assert.ok(first.text.includes('Today is 2026-11-17.') && first.text.includes('3 weeks ago'))
  assert.equal(second.text, first.text)
})

test('localDate and localTime: the local calendar date, and the date-time with its UTC offset', () => {
  assert.equal(localDate(NOW), '2026-11-17')
  assert.equal(localDate(NOW.getTime()), '2026-11-17')
  assert.equal(localTime(NOW), NOW_TIME)
  assert.match(localTime(NOW), /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/)
  assert.ok(localTime(NOW).length <= 40)
  for (const bad of [new Date(NaN), 'x', null, undefined, {}, new Date(-1e15)]) {
    assert.equal(localDate(bad), null)
    assert.equal(localTime(bad), null)
  }
  // The offset and its sign, with the zone fixed by a date that reports its own offset.
  const zone = (minutesBehindUtc) => {
    const date = new Date(2026, 0, 5, 7, 8, 9)
    date.getTimezoneOffset = () => minutesBehindUtc
    return date
  }
  assert.equal(localTime(zone(-540)), '2026-01-05T07:08:09+09:00')
  assert.equal(localTime(zone(0)), '2026-01-05T07:08:09+00:00')
  assert.equal(localTime(zone(300)), '2026-01-05T07:08:09-05:00')
  assert.equal(localTime(zone(-345)), '2026-01-05T07:08:09+05:45')
  assert.equal(localTime(zone(210)), '2026-01-05T07:08:09-03:30')
})

test('memgate_remember saves with the time of the message', async () => {
  const { host, clients } = setup({ now: () => NOW })
  const value = await host.tools.get(TOOL_REMEMBER).execute({ text: 'The user moved to Hangzhou on 2026-11-01' }, {})
  assert.equal(value.status, 'saved')
  assert.equal(clients.calls.remember[0].opts.time, NOW_TIME)
  // No usable clock: the text is saved without a time.
  const bare = setup({ now: () => new Date(NaN) })
  await bare.host.tools.get(TOOL_REMEMBER).execute({ text: 'a fact' }, {})
  assert.equal(bare.clients.calls.remember[0].opts.time, undefined)
})

test('automatic capture sends each message with the time it was written, even with the age setting off', async () => {
  const { host, clients } = setup({ config: { modules: { autoCapture: true }, advanced: { language: 'en', showAge: false } }, now: () => NOW })
  const session = fakeSession()
  host.emit('session/event', session, { type: 'user/message', data: userMessage('The dentist moved my appointment to Friday') })
  host.emit('session/event', session, { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })
  await tick(10)
  const [message] = clients.calls.rememberMessages[0].messages
  assert.equal(message.time, NOW_TIME)
  assert.ok(message.time.length <= 40)
})

test('automatic capture leaves out commands, filler and messages shorter than captureMinChars', async () => {
  const sent = async (texts, advanced = {}) => {
    const { host, clients } = setup({ config: { modules: { autoCapture: true }, advanced: { language: 'en', ...advanced } }, now: () => NOW })
    const session = fakeSession()
    for (const text of texts) host.emit('session/event', session, { type: 'user/message', data: userMessage(text) })
    host.emit('session/event', session, { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })
    await tick(10)
    return clients.calls.rememberMessages.flatMap((c) => c.messages.map((m) => m.content))
  }
  assert.deepEqual(await sent(['嗯', '好的', 'ok', '/memgate status', '／compact', '你好吗', '你好吗！！！', '今天下雨', '[图片]']), ['今天下雨'], 'default: at least 4 meaningful characters')
  assert.deepEqual(await sent(['今天下雨', 'I do']), ['今天下雨'], 'a 4-character message passes, a short one does not')
  assert.deepEqual(await sent(['你好吗']), [], 'a 3-character message is left out')
  assert.deepEqual(await sent(['你好吗', '今天下雨'], { captureMinChars: 0 }), ['你好吗', '今天下雨'], '0 switches the length test off')
  assert.deepEqual(await sent(['嗯', '好的', 'ok', '/memgate status', '[图片]', '！！！'], { captureMinChars: 0 }), ['好的'], 'the lookup test still applies; a 2-character message is not filler')
  assert.deepEqual(await sent(['今天下雨', '今天下雨了，我去买伞'], { captureMinChars: 6 }), ['今天下雨了，我去买伞'])
  assert.deepEqual(await sent(['😀😀😀😀😀', '。。。。。。'], { captureMinChars: 1 }), [], 'emoji and punctuation do not count')
  assert.deepEqual(await sent(['𠮷𠮷𠮷𠮷', '𠮷𠮷𠮷']), ['𠮷𠮷𠮷𠮷'], 'characters are counted by code point, not UTF-16 unit')
})

const stuckManager = (state) => () => ({
  dataDir: '/fake-data', start() {}, async stop() {}, endpoint: () => null, state: () => state, onChange: () => () => {}, python: () => null, store: () => null,
})

test('failure note: a lookup that times out adds one notice of the plugin, in the configured language', async () => {
  for (const lang of ['zh', 'en']) {
    const { host } = setup({ config: { advanced: { language: lang, recallTimeoutMs: 500 } }, handlers: { recall: () => new Promise(() => {}) } })
    const decision = await host.preStep(preStepPayload())
    assert.deepEqual(decision.messages.map((m) => m.source.kind), ['user', SOURCE_KIND, 'context'], 'right after the user message')
    const [notice] = pluginMessages(decision)
    assert.deepEqual(notice.source, { kind: SOURCE_KIND, form: 'notice' })
    assert.equal(notice.role, 'user')
    assert.equal(blockOf(notice), NOTICE[lang])
    assert.ok(Object.isFrozen(notice))
  }
})

test('failure note: a thrown error, a failed answer and an unusable answer add it too', async () => {
  for (const answers of [['throw'], [null]]) {
    const turns = await chat(answers)
    assert.equal(turns[0].length, 1)
    assert.equal(turns[0][0].source.form, 'notice')
    assert.equal(blockOf(turns[0][0]), NOTICE.en)
  }
  const unusable = setup({ handlers: { recall: async () => ({ results: 'not a list' }) } })
  assert.equal(pluginMessages(await unusable.host.preStep(preStepPayload()))[0]?.source.form, 'notice')
})

test('failure note: an empty answer from a failed or busy store counts as a failed lookup, a plain empty one does not', async () => {
  for (const [answer, expected] of [
    [{ results: [], more: 0, failed: 1, busy: 0 }, 1],
    [{ results: [], more: 0, failed: 0, busy: 1 }, 1],
    [{ results: [], more: 0, failed: 0, busy: 0 }, 0],
  ]) {
    const { host } = setup({ handlers: { recall: async () => answer } })
    const added = pluginMessages(await host.preStep(preStepPayload()))
    assert.equal(added.length, expected, JSON.stringify(answer))
    if (expected) assert.equal(added[0].source.form, 'notice')
  }
})

test('skipRepeats: a result that is not an object is left to the formatter, not an error', async () => {
  const { host } = setup({ handlers: { recall: async () => ({ results: [null, { text: 'GREEN tea', date: '2026-11-10' }], more: 0, failed: 0, busy: 0 }) } })
  const added = pluginMessages(await host.preStep(preStepPayload()))
  assert.equal(added.length, 1)
  assert.equal(added[0].source.form, 'recall')
})

test('failure note: while the managed gateway is starting or restarting', async () => {
  for (const state of ['idle', 'starting', 'restarting']) {
    const { host, clients } = setup({ managed: true, deps: { createGatewayManager: stuckManager(state) }, handlers: { recall: recallFound } })
    const decision = await host.preStep(preStepPayload())
    assert.equal(pluginMessages(decision).length, 1, state)
    assert.equal(blockOf(pluginMessages(decision)[0]), NOTICE.en, state)
    assert.equal(clients.calls.recall.length, 0, 'no lookup was possible')
  }
})

test('failure note: never when memory is off', async () => {
  const throwing = { recall: () => { throw new Error('must not be called') } }
  for (const state of ['off', 'failed', 'stopped']) {
    const { host, clients } = setup({ managed: true, deps: { createGatewayManager: stuckManager(state) }, handlers: throwing })
    assert.equal(pluginMessages(await host.preStep(preStepPayload())).length, 0, state)
    assert.equal(clients.calls.recall.length, 0)
  }
  // External mode without an address or a token.
  const external = setup({ config: { gateway: { mode: 'external' }, external: { url: '', token: '' } }, handlers: throwing })
  assert.equal(pluginMessages(await external.host.preStep(preStepPayload())).length, 0)
  // No module that needs memory: nothing is registered, so nothing runs.
  const none = setup({ managed: true, config: { modules: { autoRecall: false, rememberTool: false, searchTool: false, autoCapture: false } }, handlers: throwing })
  assert.equal(pluginMessages(await none.host.preStep(preStepPayload())).length, 0)
})

test('failure note: never for an empty result, a query not worth a lookup, an ended turn, or when the setting is off', async () => {
  assert.deepEqual((await chat([[]]))[0], [], 'an empty result is not a failure')
  const { host, clients } = setup({ handlers: { recall: () => { throw new Error('x') } } })
  for (const text of ['嗯', '/compact', 'ok']) assert.equal(pluginMessages(await host.preStep(preStepPayload({ text }))).length, 0, text)
  assert.equal(clients.calls.recall.length, 0)
  const off = await chat(['throw', null], { config: { advanced: { language: 'en', failureNote: false } } })
  assert.deepEqual(off, [[], []])
  const turn = new AbortController()
  const aborting = setup({ handlers: { recall: () => new Promise(() => {}) } })
  const pending = aborting.host.preStep(preStepPayload({ signal: turn.signal }))
  await tick(30)
  turn.abort()
  assert.equal(pluginMessages(await pending).length, 0, 'an aborted turn is not a failed lookup')
})

test('failure note: only on the first turn of a failure streak', async () => {
  const turns = await chat(['throw', null, 'throw', [GREEN], null, [], null])
  assert.equal(turns[0][0]?.source.form, 'notice', 'the first failure is announced')
  assert.deepEqual(turns[1], [], 'the second one in a row is not')
  assert.deepEqual(turns[2], [], 'nor the third')
  assert.equal(turns[3][0]?.source.form, 'recall', 'a block ends the streak')
  assert.equal(turns[4][0]?.source.form, 'notice', 'and the next failure is announced again')
  assert.deepEqual(turns[5], [], 'an empty result adds nothing')
  assert.deepEqual(turns[6], [], 'the notice is still the newest message of the plugin')
  // A compacted history forgets the notice, so the streak starts over.
  const compacted = await chat(['throw', 'throw'], { compactAfter: 0 })
  assert.equal(compacted[1].length, 1)
})

test('a notice is never taken for a memory block', async () => {
  for (const lang of ['zh', 'en']) {
    const f = createFormatter({ lang })
    const text = NOTICE[lang]
    assert.ok(!text.includes('<memory_data>'))
    assert.equal(f.strip(text), text, 'strip leaves the notice alone')
    assert.equal(f.strip(`What tea?\n\n${text}`), `What tea?\n\n${text}`)
    assert.equal(f.seen([text])('Long-term memory could not be searched this turn'), false)
  }
  // The block before a notice is still the one whose memories are skipped.
  const block = (await chat([[GREEN]]))[0][0]
  const notice = (await chat([null]))[0][0]
  const history = [userMessage('What tea?'), block, assistantMessage('Green.'), userMessage('And later?'), notice, assistantMessage('Not sure.')]
  const { host, clients } = setup({ handlers: { recall: async () => ({ results: [GREEN], more: 0, failed: 0, busy: 0 }) }, now: () => NOW })
  const decision = await host.preStep(preStepPayload({ session: fakeSession({ history }) }))
  assert.equal(pluginMessages(decision).length, 0, 'GREEN was shown by the block; the notice does not change that')
  assert.equal(clients.calls.recall.length, 1)
  // With skipRepeats off the newest block, not the notice, is what an identical block is compared with.
  const off = setup({ config: { advanced: { language: 'en', skipRepeats: false } }, handlers: { recall: async () => ({ results: [GREEN], more: 0, failed: 0, busy: 0 }) }, now: () => NOW })
  assert.equal(pluginMessages(await off.host.preStep(preStepPayload({ session: fakeSession({ history }) }))).length, 0)
})

test('the notice is not user text: the next query ignores it', async () => {
  const notice = (await chat([null]))[0][0]
  const { host, clients } = setup({ handlers: { recall: recallFound }, now: () => NOW })
  const history = [userMessage('where do I keep my bike'), notice, assistantMessage('In the shed.')]
  await host.preStep(preStepPayload({ text: 'pump?', session: fakeSession({ history }) }))
  assert.equal(clients.calls.recall[0].query, 'where do I keep my bike\npump?')
})
