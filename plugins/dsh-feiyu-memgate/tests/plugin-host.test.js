// The plugin with the optional host services: the /memgate command module, settings writes through
// the Settings service, the owner-only status route, the embedding add-on's service (appearing,
// changing in place, going away) and the copy into a new vector store. Fakes only: no gateway process.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { CHANGELOG_PATH, MODELS_PATH, ROW_ID, STATUS_PATH, applyPlugin } from '../src/plugin.js'
import { migrationStateFile } from '../src/migrate.js'
import { EMBED_SERVICE, EMBEDDINGS_KEY_ENV, keywordIndexFile } from '../src/tiers.js'
import { OWNER, callRoute, createFakeHost, fakeClients, fakeConnection, fakeManagers, tick, volatile } from './helpers/fake-host.js'

const PYTHON = Object.freeze({ executable: 'python-for-tests', argv: ['python-for-tests'], version: '3.12.4', source: 'path' })
const ADDON_KEY = 'fake-addon-key-0123'

const addon = (overrides = {}) => ({
  version: 1,
  state: 'ready',
  url: 'http://127.0.0.1:43123/v1',
  key: ADDON_KEY,
  model: 'bge-small-zh-v1.5',
  dimensions: 512,
  calibration: { preset: 'bge-small-zh', parity: 'exact', floor: 0.4, ceil: 0.8, query_prefix: '', document_prefix: '' },
  fingerprint: 'bge-small-zh-v1.5|f16|0123456789ab',
  models: [],
  ...overrides,
})

// Every group volatile, as the host hands them over; `values` overrides the starting values.
function volatileConfig(values = {}) {
  const groups = ['modules', 'retrieval', 'gateway', 'external', 'advanced']
  return Object.fromEntries(groups.map((g) => [g, volatile({ ...(g === 'advanced' ? { language: 'en' } : {}), ...values[g] })]))
}

// A Settings service that behaves like the host's: the change lands in the config, then the plugin
// hears loader/volatile-update.
function settingsApplier(host, config) {
  return (ns, ops) => {
    for (const op of ops) {
      const [group, key] = op.path
      const next = { ...config[group].get() }
      if (op.op === 'set') next[key] = op.value
      else delete next[key]
      config[group].set(next)
    }
    host.emit('loader/volatile-update', ops.map((op) => op.path))
  }
}

function setup({ values, services = [], deps = {}, entryId } = {}) {
  const host = createFakeHost(entryId ? { entryId } : {})
  const config = volatileConfig(values)
  if (services.includes('commands')) host.useCommands()
  if (services.includes('webServer')) host.useWebServer()
  if (services.includes('settings')) host.useSettings(settingsApplier(host, config))
  const managers = fakeManagers({ python: PYTHON })
  const plugin = applyPlugin(host.ctx, config, {
    createClient: fakeClients().createClient,
    createGatewayManager: managers.createGatewayManager,
    importHost: false,
    findPython: async () => PYTHON,
    countMemories: async () => 12,
    embedPollMs: 0,
    ...deps,
  })
  return { host, config, managers, plugin }
}

const run = (host, rawInput) => host.commands.get('memgate').handler({ rawInput, attachments: [], signal: new AbortController().signal })

// ------------------------------------------------------------------ commands

test('the command exists exactly while the host has a command registry and the switch is on', async () => {
  const { host, config } = setup({ services: ['commands'] })
  assert.ok(host.commands.has('memgate'))
  config.modules.set({ commands: false })
  host.emit('loader/volatile-update', [['modules']])
  assert.equal(host.commands.has('memgate'), false)
  config.modules.set({ commands: true })
  host.emit('loader/volatile-update', [['modules']])
  assert.ok(host.commands.has('memgate'))
  host.unprovide('commands')
  assert.equal(host.commands.has('memgate'), false, 'gone with the registry')
  host.useCommands()
  assert.ok(host.commands.has('memgate'), 'back with the registry')
  await host.unload()
  assert.equal(host.commands.size, 0)
})

test('/memgate set persists through the Settings service of this entry and applies live', async () => {
  const { host, config, managers, plugin } = setup({ services: ['commands', 'settings'], entryId: 'memory-row' })
  await plugin.settled()
  assert.equal(managers.created.length, 1)
  const reply = await run(host, 'set advanced.dailyWrites 10')
  assert.equal(reply.kind, 'success')
  assert.deepEqual(host.mutations, [{ ns: 'memory-row', ops: [{ op: 'set', path: ['advanced', 'dailyWrites'], value: 10 }] }])
  assert.equal(config.advanced.get().dailyWrites, 10)
  await plugin.settled()
  assert.equal(managers.created.length, 2, 'the gateway restarted with the new rule')
  assert.equal(managers.created[1].options.settings.advanced.dailyWrites, 10)
  assert.equal((await run(host, 'off searchTool')).kind, 'success')
  assert.equal(host.tools.has('memgate_search'), false)
  assert.equal((await run(host, 'reset searchTool')).kind, 'success')
  assert.equal(host.tools.has('memgate_search'), true)
})

test('switching advanced.linkedRecall restarts the managed gateway, and the same value does not', async () => {
  const { host, config, managers, plugin } = setup({ services: ['commands', 'settings'], entryId: 'memory-row' })
  await plugin.settled()
  assert.equal(managers.created.length, 1)
  assert.equal(managers.created[0].options.settings.advanced.linkedRecall, true)
  config.advanced.set({ ...config.advanced.get(), linkedRecall: false })
  host.emit('loader/volatile-update', [['advanced', 'linkedRecall']])
  await plugin.settled()
  assert.equal(managers.created.length, 2)
  assert.equal(managers.created[0].stopped, 1)
  assert.equal(managers.created[1].options.settings.advanced.linkedRecall, false)
  host.emit('loader/volatile-update', [['advanced', 'linkedRecall']])
  await plugin.settled()
  assert.equal(managers.created.length, 2, 'the same value does nothing')
  const reply = await run(host, 'set advanced.linkedRecall on')
  assert.equal(reply.kind, 'success')
  assert.deepEqual(host.mutations, [{ ns: 'memory-row', ops: [{ op: 'set', path: ['advanced', 'linkedRecall'], value: true }] }])
  await plugin.settled()
  assert.equal(managers.created.length, 3, 'set through the command restarts it too')
  assert.equal(managers.created[2].options.settings.advanced.linkedRecall, true)
})

test('the entry id falls back to the row id, and a host without Settings is told plainly', async () => {
  const host = createFakeHost()
  host.ctx.fiber = undefined
  host.useCommands()
  host.useSettings()
  applyPlugin(host.ctx, volatileConfig(), { createClient: fakeClients().createClient, createGatewayManager: fakeManagers().createGatewayManager, importHost: false, embedPollMs: 0 })
  await run(host, 'off autoCapture')
  assert.match((await run(host, 'on autoCapture')).text, /^Saved/)
  assert.equal(host.mutations[0].ns, ROW_ID)
  const bare = setup({ services: ['commands'] })
  const reply = await run(bare.host, 'use keyword')
  assert.equal(reply.kind, 'error')
  assert.match(reply.text, /no Settings service/)
})

// ------------------------------------------------------------------ status

test('the status route: owner only, GET and HEAD, no secret, removed on unload', async () => {
  const { host, plugin } = setup({ services: ['webServer'], values: { external: { token: 'fake-external-token' }, retrieval: { apiKey: 'fake-endpoint-key' } } })
  await plugin.settled()
  const route = host.routes.get(STATUS_PATH)
  assert.ok(route, 'registered while the host has a web server')
  assert.equal(route.kind, 'exact')
  assert.equal(route.auth, 'required')
  assert.equal((await callRoute(route, { peer: undefined })).status, 503, 'no Peer and no connection service: nobody to vouch')
  assert.equal((await callRoute(route, { peer: { principal: { kind: 'guest' } } })).status, 403)
  const post = await callRoute(route, { method: 'POST', peer: OWNER })
  assert.equal(post.status, 405)
  assert.equal(post.headers.allow, 'GET, HEAD')
  const head = await callRoute(route, { method: 'HEAD', peer: OWNER })
  assert.equal(head.status, 200)
  assert.equal(head.body, '')
  const ok = await callRoute(route, { peer: OWNER })
  assert.equal(ok.status, 200)
  assert.equal(ok.headers['cache-control'], 'no-store')
  const body = JSON.parse(ok.body)
  assert.equal(body.gateway.mode, 'managed')
  assert.equal(body.gateway.state, 'running')
  assert.deepEqual(body.python, { found: true, version: '3.12.4', source: 'path', executable: 'python-for-tests' })
  assert.deepEqual(body.store, { tier: 'keyword', id: 'keyword', type: 'sqlite', file: 'memories.db', count: 12, hybrid: false })
  assert.deepEqual(body.retrieval.hybrid, { setting: false, active: false })
  assert.deepEqual(body.secrets, { 'external.token': true, 'retrieval.apiKey': true })
  assert.equal(body.retrieval.setting, 'auto')
  assert.equal(body.retrieval.active, 'keyword')
  assert.deepEqual(body.retrieval.tiers.map((t) => [t.name, t.available]), [['keyword', true], ['endpoint', false], ['local', false]])
  for (const secret of ['fake-external-token', 'fake-endpoint-key']) assert.ok(!ok.body.includes(secret))
  await host.unload()
  assert.equal(host.routes.size, 0)
})

const PACKAGE_VERSION = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version

test('the changelog route: owner only, GET and HEAD, the released entries of the package file, removed on unload', async () => {
  const { host, plugin } = setup({ services: ['webServer'] })
  await plugin.settled()
  const route = host.routes.get(CHANGELOG_PATH)
  assert.ok(route, 'registered next to the status route')
  assert.equal(CHANGELOG_PATH, '/feiyu-memgate/changelog')
  assert.deepEqual([route.kind, route.auth], ['exact', 'required'])
  assert.equal((await callRoute(route, { peer: undefined })).status, 503)
  assert.equal((await callRoute(route, { peer: { principal: { kind: 'guest' } } })).status, 403)
  const post = await callRoute(route, { method: 'POST', peer: OWNER })
  assert.equal(post.status, 405)
  assert.equal(post.headers.allow, 'GET, HEAD')
  const head = await callRoute(route, { method: 'HEAD', peer: OWNER })
  assert.equal(head.status, 200)
  assert.equal(head.body, '')
  const ok = await callRoute(route, { peer: OWNER })
  assert.equal(ok.status, 200)
  assert.equal(ok.headers['cache-control'], 'no-store')
  const body = JSON.parse(ok.body)
  assert.deepEqual(Object.keys(body), ['version', 'entries'])
  assert.equal(body.version, PACKAGE_VERSION)
  assert.ok(body.entries.length >= 1)
  assert.equal(body.entries[0].version, PACKAGE_VERSION, 'the newest released entry is this version')
  assert.deepEqual(Object.keys(body.entries[0]), ['version', 'title', 'body'])
  assert.ok(!body.entries.some((e) => e.version === null || e.title === '未发布'))
  await host.unload()
  assert.equal(host.routes.size, 0)
})

test('the changelog route: 0.2.2-rc.1 asks connection.admit() like the status route', async () => {
  const { host, plugin } = setup({ services: ['webServer'] })
  await plugin.settled()
  const route = host.routes.get(CHANGELOG_PATH)
  host.provide('connection', fakeConnection({ peer: OWNER }))
  assert.equal((await callRoute(route)).status, 200)
  host.provide('connection', fakeConnection({ rejection: 401 }))
  assert.equal((await callRoute(route)).status, 401)
  host.provide('connection', fakeConnection({ peer: { principal: { kind: 'guest' } } }))
  assert.equal((await callRoute(route)).status, 403)
  await host.unload()
})

test('the changelog route: a missing or unreadable file answers no entries, never an error or a path; a changed file is read again', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'memgate-changelog-'))
  const file = path.join(dir, 'CHANGELOG.md')
  const { host, plugin } = setup({ services: ['webServer'], deps: { changelogFile: file } })
  await plugin.settled()
  const route = host.routes.get(CHANGELOG_PATH)
  const missing = await callRoute(route, { peer: OWNER })
  assert.equal(missing.status, 200)
  assert.deepEqual(JSON.parse(missing.body), { version: PACKAGE_VERSION, entries: [] })
  assert.ok(!missing.body.includes(dir))

  fs.writeFileSync(file, `## 99.0.0\n\nfuture\n\n## 未发布\n\nnot yet\n\n## ${PACKAGE_VERSION}\n\n日期：2026-11-18。\n\n- one\n\n## 0.0.1\n\nfirst\n`)
  const first = JSON.parse((await callRoute(route, { peer: OWNER })).body)
  assert.deepEqual(first.entries.map((e) => e.version), [PACKAGE_VERSION, '0.0.1'], 'a newer version and 未发布 are left out')
  fs.writeFileSync(file, `## ${PACKAGE_VERSION}\n\nchanged and longer\n`)
  const second = JSON.parse((await callRoute(route, { peer: OWNER })).body)
  assert.deepEqual(second.entries.map((e) => e.body), ['changed and longer'])

  fs.rmSync(file)
  fs.mkdirSync(file) // a folder where the file should be: reading fails
  assert.deepEqual(JSON.parse((await callRoute(route, { peer: OWNER })).body).entries, [])
  fs.rmSync(dir, { recursive: true, force: true })
})

test('0.2.2-rc.1: without a Peer the route asks connection.admit() and follows its answer', async () => {
  const { host, plugin } = setup({ services: ['webServer'] })
  await plugin.settled()
  const route = host.routes.get(STATUS_PATH)
  const answers = [[{ peer: OWNER }, 200], [{ rejection: 401 }, 401], [{ rejection: 403 }, 403], [{ peer: { principal: { kind: 'guest' } } }, 403]]
  for (const [answer, status] of answers) {
    const connection = fakeConnection(answer)
    host.provide('connection', connection)
    const response = await callRoute(route, { headers: { host: '127.0.0.1:1' } })
    assert.equal(response.status, status, JSON.stringify(answer))
    assert.equal(connection.seen.length, 1, 'the request itself is what the connection judges')
    assert.equal(connection.seen[0].headers.host, '127.0.0.1:1')
  }
  host.provide('connection', fakeConnection(() => { throw new Error('broken') }))
  assert.equal((await callRoute(route)).status, 503)
  // A Peer handed over by the web server (0.2.2-rc.3) wins; the connection is not asked again.
  const connection = fakeConnection({ rejection: 401 })
  host.provide('connection', connection)
  assert.equal((await callRoute(route, { peer: OWNER })).status, 200)
  assert.equal(connection.seen.length, 0)
  await host.unload()
})

test('the page routes come and go with the web server', async () => {
  const { host } = setup()
  assert.equal(host.routes.size, 0)
  host.useWebServer()
  assert.deepEqual([...host.routes.keys()].sort(), [CHANGELOG_PATH, MODELS_PATH, STATUS_PATH].sort())
  host.unprovide('webServer')
  host.useWebServer()
  assert.equal(host.routes.size, 3, 'registered again, not twice')
})

test('a broken probe or count degrades to unknown values', async () => {
  const { host } = setup({
    services: ['webServer'],
    deps: {
      createGatewayManager: fakeManagers().createGatewayManager, // a gateway that has not found its interpreter yet
      findPython: async () => { throw new Error('probe broke') },
    },
  })
  const ok = await callRoute(host.routes.get(STATUS_PATH), { peer: OWNER })
  assert.equal(ok.status, 200)
  const body = JSON.parse(ok.body)
  assert.deepEqual(body.python, { found: false })
  assert.equal(body.store.count, null)
  const counted = setup({ services: ['webServer'], deps: { countMemories: async () => { throw new Error('count broke') } } })
  assert.equal(JSON.parse((await callRoute(counted.host.routes.get(STATUS_PATH), { peer: OWNER })).body).store.count, null)
})

// ------------------------------------------------------------------ the embedding add-on

test('auto follows the add-on: it appears, changes model in place, goes away', async () => {
  const { host, managers, plugin } = setup({ deps: { embedPollMs: 20 } })
  await plugin.settled()
  assert.equal(managers.created.length, 1)
  assert.equal(managers.created[0].store().tier, 'keyword')

  host.provide(EMBED_SERVICE, addon({ state: 'loading' }))
  await plugin.settled()
  assert.equal(managers.created.length, 1, 'a loading add-on changes nothing')

  host.replace(EMBED_SERVICE, addon()) // the add-on replaces its value with ctx.set: no event
  await tick(80)
  await plugin.settled()
  assert.equal(managers.created.length, 2, 'the poll noticed the ready add-on')
  const local = managers.created[1]
  assert.equal(managers.created[0].stopped, 1)
  assert.equal(local.store().tier, 'local')
  assert.equal(local.store().env[EMBEDDINGS_KEY_ENV], ADDON_KEY)
  assert.equal(local.options.facts.embed.model, 'bge-small-zh-v1.5')

  host.replace(EMBED_SERVICE, addon({ models: [{ id: 'another', installed: true, active: false }] }))
  await tick(80)
  assert.equal(managers.created.length, 2, 'a display-only change restarts nothing')

  host.replace(EMBED_SERVICE, addon({ model: 'bge-m3', fingerprint: 'bge-m3|f16|aaaaaaaaaaaa' }))
  await tick(80)
  await plugin.settled()
  assert.equal(managers.created.length, 3, 'a new model restarts the gateway on its own index')
  assert.notEqual(managers.created[2].store().table.path, local.store().table.path)

  host.unprovide(EMBED_SERVICE)
  await plugin.settled()
  assert.equal(managers.created.length, 4)
  assert.equal(managers.created[3].store().tier, 'keyword', 'back to keyword, whose file was never touched')
  await host.unload()
})

test('the add-on update event drives restarts without any poll (SERVICE.md model switch)', async () => {
  const { host, managers, plugin } = setup() // embedPollMs: 0
  await plugin.settled()
  host.provide(EMBED_SERVICE, addon({ loaded: true }))
  await plugin.settled()
  assert.equal(managers.created.length, 2)
  assert.equal(managers.created[1].store().tier, 'local')

  // As the add-on does it: set the value (silent), then emit the event with the new snapshot.
  const announce = (snapshot) => {
    host.replace(EMBED_SERVICE, snapshot)
    host.emit('feiyu-memgate-embed/update', snapshot)
  }
  announce(addon({ loaded: false })) // idle unload: still ready
  announce(addon({ loaded: false, downloading: 'bge-m3' }))
  await plugin.settled()
  assert.equal(managers.created.length, 2, 'unload and download progress restart nothing')

  // A switch keeps url and key; model, fingerprint and state change.
  const m3 = { model: 'bge-m3', fingerprint: 'bge-m3|f16|bbbbbbbbbbbb', dimensions: 1024 }
  announce(addon({ ...m3, state: 'loading' }))
  await plugin.settled()
  assert.equal(managers.created.length, 3)
  assert.equal(managers.created[2].store().tier, 'keyword', 'auto uses keyword while the new model loads')
  announce(addon({ ...m3, loaded: true }))
  await plugin.settled()
  assert.equal(managers.created.length, 4)
  const local = managers.created[3].store()
  assert.equal(local.tier, 'local')
  assert.match(local.table.path, /memories-local-bge-m3-[0-9a-f]{12}\.db$/)
  assert.notEqual(local.table.path, managers.created[1].store().table.path)
  assert.equal(local.env[EMBEDDINGS_KEY_ENV], ADDON_KEY, 'the key reaches the gateway through its environment')
  assert.equal(JSON.stringify(local.table).includes(ADDON_KEY), false, 'and never its configuration')

  announce(addon({ ...m3, state: 'error', error: 'crashed', url: null, key: null }))
  await plugin.settled()
  assert.equal(managers.created.length, 5)
  assert.equal(managers.created[4].store().tier, 'keyword')
  assert.equal((await plugin.status()).embed.error, 'crashed')
  await host.unload()
})

test('an explicit local tier waits for the add-on; external mode ignores it', async () => {
  const waiting = setup({ values: { retrieval: { tier: 'local' } } })
  await waiting.plugin.settled()
  assert.equal(waiting.managers.created.length, 1)
  assert.deepEqual(waiting.managers.created[0].store().missing, ['dsh-feiyu-memgate-embed (ready)'])
  waiting.host.provide(EMBED_SERVICE, addon())
  await waiting.plugin.settled()
  assert.equal(waiting.managers.created.length, 2)
  assert.equal(waiting.managers.created[1].store().tier, 'local')

  const external = setup({ values: { gateway: { mode: 'external' }, external: { url: 'http://127.0.0.1:9', token: 'fake-token-for-tests-only' } } })
  external.host.provide(EMBED_SERVICE, addon())
  await external.plugin.settled()
  assert.equal(external.managers.created.length, 0)
  const status = await external.plugin.status()
  assert.equal(status.retrieval.active, null)
  assert.equal(status.embed.state, 'ready')
  assert.ok(!JSON.stringify(status).includes(ADDON_KEY) && !JSON.stringify(status).includes('43123'), 'no add-on key or address in the status')
})

// ------------------------------------------------------------------ copy into the vector store

function fakeMigrations(results) {
  const runs = []
  const runMigration = (options) => {
    const result = results[Math.min(runs.length, results.length - 1)]
    let stopped = false
    const entry = { options, stopped: () => stopped }
    runs.push(entry)
    options.onProgress({ state: 'running', total: result.total, copied: 0, remaining: result.total })
    return {
      done: Promise.resolve().then(() => {
        options.onProgress(result)
        return result
      }),
      stop: async () => { stopped = true },
    }
  }
  return { runs, runMigration }
}

test('a vector tier copies the keyword store in bounded steps while they make progress', async () => {
  const migrations = fakeMigrations([
    { state: 'partial', total: 5, copied: 2, remaining: 3 },
    { state: 'partial', total: 5, copied: 4, remaining: 1 },
    { state: 'done', total: 5, copied: 5, remaining: 0 },
  ])
  const { host, managers, plugin } = setup({ deps: { runMigration: migrations.runMigration, migration: { pauseMs: 5, maxItems: 2 } } })
  host.provide(EMBED_SERVICE, addon())
  await plugin.settled()
  await tick(60)
  assert.equal(migrations.runs.length, 3)
  const first = migrations.runs[0].options
  assert.equal(first.python, PYTHON)
  assert.equal(first.maxItems, 2)
  assert.equal(first.target.tier, 'local')
  assert.equal(first.target.id, managers.created.at(-1).store().id)
  assert.match(first.source, /memories\.db$/)
  assert.equal(first.dataDir, managers.created.at(-1).dataDir)
  assert.deepEqual(plugin.migration(), { state: 'done', total: 5, copied: 5, remaining: 0, target: first.target.id })
  const status = await plugin.status()
  assert.equal(status.migration.state, 'done')
  await host.unload()
})

test('no copy for the keyword tier, with the switch off, or twice for one gateway; switching off stops it', async () => {
  const keyword = fakeMigrations([{ state: 'done', total: 0, copied: 0, remaining: 0 }])
  const a = setup({ deps: { runMigration: keyword.runMigration } })
  await a.plugin.settled()
  assert.equal(keyword.runs.length, 0)

  const off = fakeMigrations([{ state: 'done', total: 0, copied: 0, remaining: 0 }])
  const b = setup({ values: { modules: { migrateOnTierChange: false } }, deps: { runMigration: off.runMigration } })
  b.host.provide(EMBED_SERVICE, addon())
  await b.plugin.settled()
  assert.equal(off.runs.length, 0)

  // A step that never ends: switching the copy off stops it.
  const hanging = []
  const c = setup({
    deps: {
      runMigration: (options) => {
        let stopped = false
        hanging.push({ options, stopped: () => stopped })
        return { done: new Promise(() => {}), stop: async () => { stopped = true } }
      },
    },
  })
  c.host.provide(EMBED_SERVICE, addon())
  await c.plugin.settled()
  c.host.emit('loader/volatile-update', [['modules']])
  assert.equal(hanging.length, 1, 'one copy per gateway, however often settings arrive')
  c.config.modules.set({ migrateOnTierChange: false })
  c.host.emit('loader/volatile-update', [['modules']])
  assert.equal(hanging[0].stopped(), true)
})

test('a failed copy is logged by kind and waits for the next gateway start', async () => {
  const migrations = fakeMigrations([{ state: 'failed', total: 3, copied: 1, remaining: 2, kind: 'BackendError' }])
  const { host, plugin } = setup({ deps: { runMigration: migrations.runMigration, migration: { pauseMs: 5 } } })
  host.provide(EMBED_SERVICE, addon())
  await plugin.settled()
  await tick(30)
  assert.equal(migrations.runs.length, 1)
  assert.ok(host.logs.some((l) => l.level === 'warn' && /stopped \(BackendError\)/.test(l.text)))
  assert.equal(plugin.migration().state, 'failed')
})

// ------------------------------------------------------------------ hybrid

const ENDPOINT = { tier: 'endpoint', url: 'http://127.0.0.1:9/v1', model: 'bge-m3' }
const toggle = (host, config, hybrid) => {
  config.retrieval.set({ ...config.retrieval.get(), hybrid })
  host.emit('loader/volatile-update', [['retrieval', 'hybrid']])
}

test('switching retrieval.hybrid restarts the gateway under a vector tier, and not under keyword', async () => {
  const vector = setup({ values: { retrieval: ENDPOINT } })
  await vector.plugin.settled()
  assert.equal(vector.managers.created.length, 1)
  toggle(vector.host, vector.config, true)
  await vector.plugin.settled()
  assert.equal(vector.managers.created.length, 2)
  assert.equal(vector.managers.created[0].stopped, 1)
  assert.equal(vector.managers.created[1].store().hybrid, true)
  toggle(vector.host, vector.config, true)
  await vector.plugin.settled()
  assert.equal(vector.managers.created.length, 2, 'the same value does nothing')
  toggle(vector.host, vector.config, false)
  await vector.plugin.settled()
  assert.equal(vector.managers.created.length, 3)
  assert.equal(vector.managers.created[2].store().hybrid, false)

  const keyword = setup({ values: { retrieval: { tier: 'keyword' } } })
  await keyword.plugin.settled()
  toggle(keyword.host, keyword.config, true)
  await keyword.plugin.settled()
  assert.equal(keyword.managers.created.length, 1, 'under keyword the switch changes nothing the gateway runs')

  // auto resolves to keyword until the add-on is ready: no restart for the switch; then hybrid follows.
  const auto = setup()
  await auto.plugin.settled()
  toggle(auto.host, auto.config, true)
  await auto.plugin.settled()
  assert.equal(auto.managers.created.length, 1)
  auto.host.provide(EMBED_SERVICE, addon())
  await auto.plugin.settled()
  assert.equal(auto.managers.created.length, 2)
  assert.equal(auto.managers.created[1].store().tier, 'local')
  assert.equal(auto.managers.created[1].store().hybrid, true)
})

test('status reports the hybrid switch and the store files; the count comes from the vector file', async () => {
  const counted = []
  const { host, plugin } = setup({
    services: ['webServer'],
    values: { retrieval: { ...ENDPOINT, hybrid: true } },
    deps: { countMemories: async ({ file }) => { counted.push(file); return 7 } },
  })
  await plugin.settled()
  const status = await plugin.status()
  assert.deepEqual(status.retrieval.hybrid, { setting: true, active: true })
  const { store } = status
  assert.equal(store.type, 'hybrid')
  assert.equal(store.hybrid, true)
  assert.equal(store.tier, 'endpoint')
  assert.match(store.file, /^memories-endpoint-bge-m3\.db$/)
  assert.equal(store.keywordFile, 'memories-endpoint-bge-m3-keywords.db')
  assert.equal(store.count, 7)
  assert.equal(counted.length, 1)
  assert.match(counted[0].replaceAll('\\', '/'), /\/memories-endpoint-bge-m3\.db$/, 'counted in the vector file')
  assert.ok(!JSON.stringify(status).includes('/fake-data'), 'folders stay out of the status')

  // The switch is on but does nothing under keyword.
  const keyword = setup({ values: { retrieval: { tier: 'keyword', hybrid: true } } })
  await keyword.plugin.settled()
  const plain = await keyword.plugin.status()
  assert.deepEqual(plain.retrieval.hybrid, { setting: true, active: false })
  assert.equal(plain.store.hybrid, false)
  assert.equal(plain.store.keywordFile, undefined)
  assert.equal(plain.store.type, 'sqlite')

  // External mode: the bundled gateway is not in use.
  const external = setup({ values: { retrieval: { ...ENDPOINT, hybrid: true }, gateway: { mode: 'external' } } })
  const outside = await external.plugin.status()
  assert.deepEqual(outside.retrieval.hybrid, { setting: true, active: false })
  assert.equal(outside.store, null)
})

test('/memgate set retrieval.hybrid on restarts a vector tier and the status line shows it', async () => {
  const { host, config, managers, plugin } = setup({ services: ['commands', 'settings'], values: { retrieval: ENDPOINT } })
  await plugin.settled()
  const reply = await run(host, 'set retrieval.hybrid on')
  assert.equal(reply.kind, 'success')
  assert.equal(config.retrieval.get().hybrid, true)
  await plugin.settled()
  assert.equal(managers.created.length, 2)
  const status = await run(host, 'status')
  assert.match(status.text, /Retrieval: endpoint → endpoint \+ keyword \(hybrid\)/)
  assert.match(status.text, /Store: hybrid, memories-endpoint-bge-m3\.db \+ memories-endpoint-bge-m3-keywords\.db, 12 memories/)
  assert.match((await run(host, 'tiers')).text, /Hybrid .*: on\./)
})

test('the copy target keeps its store id, so its state file does not change when hybrid is switched on', async () => {
  const migrations = []
  const runMigration = (options) => {
    migrations.push(options)
    return { done: Promise.resolve({ state: 'done', total: 0, copied: 0, remaining: 0 }), stop: async () => {} }
  }
  const { host, config, plugin } = setup({ values: { retrieval: ENDPOINT }, deps: { runMigration } })
  await plugin.settled()
  await tick(20)
  assert.equal(migrations.length, 1)
  toggle(host, config, true)
  await plugin.settled()
  await tick(20)
  assert.equal(migrations.length, 2)
  const [plain, hybrid] = migrations.map((m) => m.target)
  assert.equal(plain.table.type, 'embedding')
  assert.equal(hybrid.table.type, 'hybrid')
  assert.equal(hybrid.id, plain.id)
  assert.equal(migrationStateFile(migrations[1].dataDir, hybrid.id), migrationStateFile(migrations[0].dataDir, plain.id))
  assert.equal(hybrid.mirror.keyword.path, keywordIndexFile(migrations[1].dataDir, plain.id))
})
