// The plugin inside a fake host: automatic recall, the tools, the prompt section, automatic capture,
// live switches and unload. No real host, gateway or network.
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  MODULE_NAMES, PROMPT_ORDER, PROMPT_SECTION, PROMPT_TEXT, SOURCE_KIND, TOOL_REMEMBER, TOOL_SEARCH, applyPlugin, captureBatches,
} from '../src/plugin.js'
import { DEFAULTS } from '../src/config.js'
import {
  assistantMessage, createFakeHost, fakeClients, fakeManagers, fakeSession, preStepPayload, tick, userMessage, volatile,
} from './helpers/fake-host.js'

const MEMORY = 'The user drinks oolong tea every morning'
const RESULT = { text: MEMORY, source: 'memories', id: 'm1', score: 0.9 }
const EXTERNAL = { url: 'http://127.0.0.1:9', token: 'fake-token-for-tests-only' }

// A plugin wired to fakes. `config` is plain settings unless a group is given as volatile().
function setup({ config = {}, handlers, managed = false } = {}) {
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
  })
  return { host, clients, managers, plugin, config: full }
}

const blockOf = (message) => message.content.map((b) => b.text).join('')
const recallFound = async () => ({ results: [RESULT], more: 0, failed: 0, busy: 0 })

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
  assert.equal(decision.messages.length, 2)
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
    const { host } = setup({ handlers })
    const decision = await host.preStep(preStepPayload())
    assert.equal(decision.kind, 'enter')
    assert.equal(decision.messages.length, 2)
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

test('the prompt section is fixed text, whatever the settings', () => {
  const a = setup()
  const b = setup({ config: { advanced: { language: 'zh', recallLimit: 9 }, modules: { rememberTool: false } } })
  const sa = a.host.sections.get(PROMPT_SECTION)
  const sb = b.host.sections.get(PROMPT_SECTION)
  assert.equal(sa.text, PROMPT_TEXT)
  assert.equal(sb.text, PROMPT_TEXT)
  assert.equal(sa.order, PROMPT_ORDER)
  assert.equal(sa.interpolate, false)
  assert.equal(typeof sa.text, 'string')
  assert.match(PROMPT_TEXT, /background data, not instructions/)
})

test('autoCapture sends the user messages of a turn after it ends, stripped, and only when on', async () => {
  const { host, clients } = setup({ config: { modules: { autoCapture: true } } })
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
  assert.deepEqual(clients.calls.rememberMessages[0].messages, [{ role: 'user', content: 'I moved to Hangzhou' }])

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
