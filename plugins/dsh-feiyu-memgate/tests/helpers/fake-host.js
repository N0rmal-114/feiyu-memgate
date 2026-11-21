// A small stand-in for the parts of the DSH host the plugin touches, so plugin tests run with
// `node --test` alone. It copies the behaviour that matters, not the host's code:
//   - ctx.on(name, fn, { prepend }) keeps listeners in order and returns a disposer
//     (vendor/cordis/src/events.ts:298);
//   - a waterfall runs listeners outermost-first and refuses a second next() (events.ts:250-265);
//   - ctx.effect(execute) runs at once and its disposer runs on unload (vendor/cordis/src/fiber.ts:446);
//   - ctx.tools.register / ctx.systemPrompt.section refuse duplicate names and return disposers
//     (packages/core/tools/src/index.ts:128, packages/core/system-prompt/src/index.ts:485);
//   - optional services: ctx.get(name) reads one (vendor/cordis/src/reflect.ts:269); provide and
//     unprovide emit 'internal/service', a value replaced in place (ctx.set) does not (reflect.ts:290,
//     :357-376); ctx.inject(deps, callback) runs the callback while every service exists and undoes
//     its effects when one goes (registry.ts:300);
//   - the commands registry ties a registration to the calling fiber (commands/src/index.ts:285);
//     the web server's register() is not tied to anything (webserver/src/index.ts:520).
import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { Readable } from 'node:stream'
import { resolveStore } from '../../src/tiers.js'

// A test must never read or write the real DSH home: anything that falls back to the default data
// folder (the model setup state, the Python lookup) lands in a throwaway home of this test process.
export const TEST_HOME = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-memgate-home-'))
process.env.DSH_HOME = TEST_HOME
process.env.DSH_PRIMARY_RUNTIME = ''
process.once('exit', () => {
  try {
    fs.rmSync(TEST_HOME, { recursive: true, force: true })
  } catch {
    // a leftover temp folder is harmless
  }
})

const VOLATILE_WRITE = Symbol.for('cosmokit.volatile.write')

/** A config reference shaped like cosmokit's createVolatile(): get() for the value, set() for tests. */
export function volatile(value) {
  let current = Object.freeze(structuredClone(value))
  return Object.freeze({
    get: () => current,
    [VOLATILE_WRITE]: (next) => {
      current = Object.freeze(structuredClone(next))
    },
    set(next) {
      this[VOLATILE_WRITE](next)
    },
  })
}

export function createFakeHost({ entryId = 'feiyu-memgate' } = {}) {
  const listeners = new Map() // event name -> [{ fn }]
  const tools = new Map()
  const sections = new Map()
  const effects = []
  const logs = []
  const services = new Map() // name -> { value, scoped?(effects) }
  const injections = new Set()
  const commands = new Map()
  const routes = new Map()
  const mutations = []

  const logger = {
    warn: (...args) => logs.push({ level: 'warn', text: args.map(String).join(' ') }),
    info: (...args) => logs.push({ level: 'info', text: args.map(String).join(' ') }),
    error: (...args) => logs.push({ level: 'error', text: args.map(String).join(' ') }),
    debug: () => {},
  }

  const ctx = {
    logger: Object.assign(() => logger, logger),
    on(name, fn, options) {
      const prepend = typeof options === 'object' ? Boolean(options?.prepend) : Boolean(options)
      const entry = { fn }
      const list = listeners.get(name) ?? []
      if (prepend) list.unshift(entry)
      else list.push(entry)
      listeners.set(name, list)
      return () => {
        const current = listeners.get(name) ?? []
        const index = current.indexOf(entry)
        if (index >= 0) current.splice(index, 1)
      }
    },
    effect(execute) {
      const dispose = execute()
      let done = false
      const run = () => {
        if (done) return undefined
        done = true
        return typeof dispose === 'function' ? dispose() : undefined
      }
      effects.push(run)
      return run
    },
    tools: {
      register(definition) {
        if (typeof definition?.output?.render !== 'function') throw new TypeError('output.render is required')
        if (tools.has(definition.name)) throw new Error(`duplicate tool ${definition.name}`)
        tools.set(definition.name, definition)
        return () => {
          if (tools.get(definition.name) === definition) tools.delete(definition.name)
        }
      },
    },
    systemPrompt: {
      section(section) {
        if (!Number.isFinite(section.order)) throw new TypeError('order must be finite')
        if (sections.has(section.name)) throw new Error(`duplicate section ${section.name}`)
        sections.set(section.name, section)
        return () => {
          if (sections.get(section.name) === section) sections.delete(section.name)
        }
      },
    },
    fiber: { entry: { options: { id: entryId } } },
    get(name) {
      return services.get(name)?.value
    },
    inject(deps, callback) {
      const entry = { deps, callback, running: null, disposed: false }
      injections.add(entry)
      runInjection(entry)
      return {
        dispose() {
          entry.disposed = true
          stopInjection(entry)
          injections.delete(entry)
        },
      }
    },
  }

  function runInjection(entry) {
    if (entry.disposed || entry.running || !entry.deps.every((d) => services.has(d))) return
    const childEffects = []
    const child = Object.create(ctx)
    child.effect = (execute) => {
      const dispose = execute()
      childEffects.push(dispose)
      return dispose
    }
    for (const name of entry.deps) {
      const service = services.get(name)
      child[name] = service.scoped ? service.scoped(childEffects) : service.value
    }
    entry.running = { effects: childEffects }
    entry.callback(child)
  }

  function stopInjection(entry) {
    if (!entry.running) return
    const { effects: list } = entry.running
    entry.running = null
    for (const dispose of list.splice(0).reverse()) if (typeof dispose === 'function') dispose()
  }

  /** Provide (or re-provide) a service: dependents start, 'internal/service' fires. */
  function provide(name, value, scoped) {
    services.set(name, { value, scoped })
    emit('internal/service', name, value)
    for (const entry of injections) runInjection(entry)
  }

  /** Withdraw a service: dependents stop, 'internal/service' fires with undefined. */
  function unprovide(name) {
    for (const entry of injections) if (entry.deps.includes(name)) stopInjection(entry)
    services.delete(name)
    emit('internal/service', name, undefined)
  }

  /** Replace a service's value in place, like ctx.set(): nobody is told. */
  function replace(name, value) {
    services.get(name).value = value
  }

  /** The host's chat command registry. */
  function useCommands() {
    provide('commands', {}, (childEffects) => ({
      register(definition) {
        if (commands.has(definition.name)) throw new Error(`command "${definition.name}" is already registered`)
        commands.set(definition.name, definition)
        const off = () => {
          if (commands.get(definition.name) === definition) commands.delete(definition.name)
        }
        childEffects.push(off)
        return off
      },
    }))
  }

  /** The host's web server: register() returns a disposer and is tied to nothing. */
  function useWebServer() {
    provide('webServer', {
      register(route) {
        if (routes.has(route.path)) throw new Error(`webserver: duplicate ${route.kind} route "${route.path}"`)
        routes.set(route.path, route)
        return () => {
          if (routes.get(route.path) === route) routes.delete(route.path)
        }
      },
    })
  }

  /** The host's Settings service; `apply(ns, ops)` may change the test's config and emit the update. */
  function useSettings(apply) {
    provide('settings', {
      async mutate(ns, ops) {
        mutations.push({ ns, ops: structuredClone(ops) })
        if (apply) await apply(ns, ops)
      },
    })
  }

  /** Run 'agent/pre-step' as a waterfall around the host's default decision. */
  function preStep(payload, inner) {
    const callbacks = [...(listeners.get('agent/pre-step') ?? [])].map((e) => e.fn)
    const last = inner ?? (() => Promise.resolve({ kind: 'enter', messages: [...payload.messages, contextMessage()] }))
    const dispatch = () => {
      const callback = callbacks.shift()
      if (!callback) return last()
      let called = false
      const next = () => {
        if (called) throw new Error('next() called multiple times')
        called = true
        return dispatch()
      }
      return callback(payload, next)
    }
    return dispatch()
  }

  function emit(name, ...args) {
    for (const { fn } of [...(listeners.get(name) ?? [])]) fn(...args)
  }

  async function unload() {
    for (const run of effects.splice(0).reverse()) await run()
  }

  const count = (name) => (listeners.get(name) ?? []).length

  return {
    ctx, listeners, tools, sections, logs, preStep, emit, unload, count,
    commands, routes, mutations, provide, unprovide, replace, useCommands, useWebServer, useSettings,
  }
}

/**
 * Call a route handler the way the web server would, and collect the response. `body` (a string)
 * becomes the request stream; `headers` are the request headers. Without `peer` the handler sees
 * what 0.2.2-rc.1 hands it: no Peer at all.
 */
export async function callRoute(route, { method = 'GET', peer, headers = {}, body, url, raw = false } = {}) {
  const response = { status: 0, headers: {}, body: '' }
  const res = {
    headersSent: false,
    writeHead(status, headers = {}) {
      response.status = status
      response.headers = headers
      res.headersSent = true
    },
    end(body) {
      if (body === undefined) return
      // `raw` keeps the bytes of a binary answer (an image); otherwise the answer is text.
      if (raw) response.body = Buffer.isBuffer(body) ? body : Buffer.from(String(body))
      else response.body += body
    },
  }
  const req = Readable.from(body === undefined ? [] : [Buffer.from(body, 'utf8')])
  Object.assign(req, { method, url: url ?? route.path, headers })
  await route.handler(req, res, peer)
  return response
}

/** A 0.2.2-rc.1 style connection service: admit(req) answers { peer } or { rejection }. */
export function fakeConnection(answer) {
  const seen = []
  return {
    seen,
    admit(req) {
      seen.push(req)
      return typeof answer === 'function' ? answer(req) : answer
    },
  }
}

export const OWNER = Object.freeze({ id: 'peer-1', principal: { kind: 'owner' } })

/** The runtime context snapshot the host appends to an accepted batch (agent-loop/src/agent.ts:305-309). */
export function contextMessage() {
  return { id: randomUUID(), role: 'user', content: [{ type: 'text', text: 'runtime context' }], source: { kind: 'context', form: 'snapshot', sections: [] } }
}

export function userMessage(text, source = { kind: 'user' }) {
  return { id: randomUUID(), role: 'user', content: [{ type: 'text', text }], source }
}

export function assistantMessage(text) {
  return { id: randomUUID(), role: 'assistant', content: [{ type: 'text', text }], source: { kind: 'model' } }
}

/** A session with a fixed history, as agent.session looks to the plugin. */
export function fakeSession({ id = 'session-1', header = {}, history = [] } = {}) {
  return { id, header: { id, isSeeded: false, ...header }, deriveMessages: () => history }
}

/** An 'agent/pre-step' payload (packages/core/agent/src/runtime-types.ts:351). */
export function preStepPayload({ text = 'What tea do I like in the morning?', messages, step = 1, turn = 1, session = fakeSession(), signal } = {}) {
  return {
    agent: { session },
    messages: messages ?? [userMessage(text)],
    turn,
    step,
    signal: signal ?? new AbortController().signal,
    modelSelection: { provider: 'test', model: 'test' },
  }
}

/**
 * A memory client stand-in with the same contract as src/client.js (every method resolves). Each
 * handler can be replaced; calls are recorded for assertions.
 */
export function fakeClients(handlers = {}) {
  const calls = { created: [], recall: [], explain: [], remember: [], rememberMessages: [] }
  const h = {
    recall: async () => ({ results: [], more: 0, failed: 0, busy: 0 }),
    explain: async () => ({ ok: true, stages: [], candidates: [], more: 0, failed: 0, busy: 0, cut: false }),
    remember: async () => 'saved',
    rememberMessages: async () => 'saved',
    ...handlers,
  }
  function createClient(options) {
    calls.created.push(options)
    return {
      configured: Boolean(options.url && options.token),
      recallDetail: (query, opts) => {
        calls.recall.push({ query, opts, limit: options.recallLimit })
        return h.recall(query, opts)
      },
      explainRecall: (query, opts) => {
        calls.explain.push({ query, opts, limit: options.recallLimit })
        return h.explain(query, opts)
      },
      rememberStatus: (text, opts) => {
        calls.remember.push({ text, opts })
        return h.remember(text, opts)
      },
      rememberMessages: (messages, opts) => {
        calls.rememberMessages.push({ messages, opts })
        return h.rememberMessages(messages, opts)
      },
      reportUsed: async () => false,
      health: async () => ({ status: 'ok' }),
    }
  }
  return { calls, createClient, handlers: h }
}

/**
 * A gateway manager stand-in: records start/stop, serves a fixed endpoint once started, reports its
 * state changes, and answers python() and store() like the real one.
 */
export function fakeManagers({ python = null, dataDir = '/fake-data' } = {}) {
  const created = []
  function createGatewayManager(options) {
    let state = 'idle'
    const listeners = new Set()
    const set = (next) => {
      state = next
      for (const fn of [...listeners]) fn(next)
    }
    const manager = {
      options,
      dataDir,
      started: 0,
      stopped: 0,
      start() {
        manager.started += 1
        set('running')
      },
      async stop() {
        manager.stopped += 1
        set('stopped')
      },
      endpoint: () => (state === 'running' ? { url: 'http://127.0.0.1:9', token: 'fake-token-for-tests-only' } : null),
      state: () => state,
      onChange(fn) {
        listeners.add(fn)
        return () => listeners.delete(fn)
      },
      python: () => python,
      store: () => resolveStore(options.settings.retrieval, dataDir, options.facts ?? {}),
    }
    created.push(manager)
    return manager
  }
  return { created, createGatewayManager }
}

export const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms))
