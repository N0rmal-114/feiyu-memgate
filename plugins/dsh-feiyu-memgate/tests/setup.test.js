// Local embedding models in one click (src/setup.js) through the plugin and its owner-only route,
// with a fake host plugin manager and a fake embedding add-on that follows version 2 of the add-on's
// service contract (dsh-feiyu-memgate-embed docs/SERVICE.md): the whole chain from "add-on absent"
// to "local tier active", the install-script gate as the host's plugin manager source handles it,
// every failure state the page explains, resumption after a reload or a host restart, and that
// nothing the model can reach starts an installation.
import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { CATALOGUE } from '../src/catalogue.js'
import { MODELS_PATH, TOOL_REMEMBER, TOOL_SEARCH, applyPlugin } from '../src/plugin.js'
import { SETUP_FILE, addonBundle, addonFailureOf, enableFailureOf, failureOf, readJob, readList } from '../src/setup.js'
import { EMBED_SERVICE, EMBEDDINGS_KEY_ENV } from '../src/tiers.js'
import { OWNER, callRoute, createFakeHost, fakeClients, fakeConnection, fakeManagers, tick, volatile } from './helpers/fake-host.js'

// Helper processes are unref'd on purpose (they must never keep a host alive). In a test file nothing else
// holds the event loop open while one is awaited, and Node 22 then ends the file with "Promise resolution
// is still pending" (Node 24 waits). This timer stands in for the host.
const keepAlive = setInterval(() => {}, 60000)
after(() => clearInterval(keepAlive))

const PYTHON = Object.freeze({ executable: 'python-for-tests', argv: ['python-for-tests'], version: '3.12.4', source: 'path' })
const ADDON_KEY = 'fake-addon-key-0456'
const SMALL = 'bge-small-zh-v1.5'
const PACKAGE = 'dsh-feiyu-memgate-embed'
const SCRIPT = 'node-llama-cpp'
const UNDECIDED = 'set this to true or false' // build-approval.ts UNDECIDED_BUILD
// A model under licence terms that must be accepted first: the add-on keeps that mechanism, and no
// model of the current registry needs it, so the fake add-on lists one of its own.
const LICENCE = Object.freeze({ name: 'Example Model Licence', url: 'https://example.invalid/licence' })
const GATED = Object.freeze({
  id: 'licensed-model-test', title: 'Licensed test model', tier: 'top', sizeBytes: 1000, memoryMB: { loaded: 300, peak: 350 },
  languages: ['multilingual'], license: LICENCE, requiresAcceptance: LICENCE, status: 'verified',
})

// ------------------------------------------------------------------ fakes

/** The embedding add-on, service version 2: a frozen snapshot with control methods. */
function fakeAddon(host, { pinned = null } = {}) {
  const s = { active: null, installed: new Set(), job: null, error: null, calls: [] }
  const table = [...CATALOGUE, GATED]
  const running = (id) => s.job && s.job.id === id && !['done', 'failed', 'cancelled'].includes(s.job.state)
  const rows = () => table.map((m) => ({
    id: m.id, title: m.title, tier: m.tier, sizeBytes: m.sizeBytes, dimensions: 512,
    // The add-on's own figure wins over the shipped copy of its registry.
    memoryMB: m.id === SMALL ? { loaded: 190, peak: 221 } : m.memoryMB,
    maxInputTokens: 512, license: m.license.name, licenseUrl: m.license.url, requiresAcceptance: m.requiresAcceptance,
    parity: 'exact', status: m.status, installed: s.installed.has(m.id), active: s.active === m.id, job: running(m.id) ? s.job : null,
  }))
  const value = () => Object.freeze({
    version: 2,
    state: s.error ? 'error' : s.active ? 'ready' : 'no-model',
    error: s.error,
    url: s.active && !s.error ? 'http://127.0.0.1:43123/v1' : null,
    key: s.active && !s.error ? ADDON_KEY : null,
    model: s.active,
    dimensions: s.active ? 512 : null,
    maxInputTokens: 512,
    fingerprint: s.active ? `${s.active}|f16|0123456789ab` : null,
    calibration: null,
    loaded: false,
    downloading: s.job && running(s.job.id) ? s.job.id : null,
    overflow: 'truncate',
    progress: s.job,
    models: rows().map(({ id, title, installed, active, sizeBytes, license, licenseUrl, status }) => ({ id, title, installed, active, sizeBytes, license, licenseUrl, status })),
    list: async () => ({ ok: true, active: s.active, pinned, models: rows() }),
    job: async () => ({ ok: true, job: s.job }),
    install: async (id, options = {}) => {
      s.calls.push(['install', id, { ...options }])
      const m = table.find((x) => x.id === id)
      if (!m) return { ok: false, code: 'unknown-model', message: 'unknown' }
      if (m.requiresAcceptance && options.acceptLicence !== m.requiresAcceptance.name) return { ok: false, code: 'needs-acceptance', message: 'accept', licence: m.requiresAcceptance }
      if (pinned && pinned !== id) return { ok: false, code: 'pinned', message: 'pinned', pinned }
      if (s.installed.has(id)) {
        s.active = id
        publish()
        return { ok: true, id, job: null, active: id }
      }
      if (s.job && running(s.job.id) && s.job.id !== id) return { ok: false, code: 'busy', message: 'busy' }
      s.job = { id, state: 'downloading', bytes: 0, total: m.sizeBytes, speed: 0, source: 'https://hf-mirror.com', activateWhenDone: true, error: null, activated: false }
      publish()
      return { ok: true, id, job: s.job, acceptedAt: null }
    },
    use: async (id) => {
      s.calls.push(['use', id])
      if (pinned && pinned !== id) return { ok: false, code: 'pinned', message: 'pinned', pinned }
      if (!s.installed.has(id)) return { ok: false, code: 'not-installed', message: 'not installed' }
      s.active = id
      publish()
      return { ok: true, active: id }
    },
    cancel: async () => {
      s.calls.push(['cancel'])
      if (!s.job || !running(s.job.id)) return { ok: false, code: 'no-job', message: 'no job' }
      s.job = { ...s.job, state: 'cancelled' }
      publish()
      return { ok: true, id: s.job.id }
    },
    remove: async (id) => {
      s.calls.push(['remove', id])
      if (s.active === id) return { ok: false, code: 'active', message: 'active' }
      if (!s.installed.has(id)) return { ok: false, code: 'not-installed', message: 'no' }
      s.installed.delete(id)
      publish()
      return { ok: true, id, freedBytes: 1 }
    },
    add: async () => ({ ok: false, code: 'failed', message: 'not used here' }),
    setOrigin: async () => ({ ok: false, code: 'failed', message: 'not used here' }),
  })
  function publish() {
    const v = value()
    host.replace(EMBED_SERVICE, v) // ctx.set: silent
    host.emit('feiyu-memgate-embed/update', v)
  }
  return {
    s,
    calls: s.calls,
    start: () => host.provide(EMBED_SERVICE, value()),
    stop: () => host.unprovide(EMBED_SERVICE),
    progress(fraction) {
      s.job = { ...s.job, bytes: Math.floor(s.job.total * fraction) }
      publish()
    },
    finish({ activate = true } = {}) {
      s.installed.add(s.job.id)
      if (activate) s.active = s.job.id
      s.job = { ...s.job, state: 'done', bytes: s.job.total, activated: activate }
      publish()
    },
    fail(error) {
      s.job = { ...s.job, state: 'failed', error }
      publish()
    },
    crash(error) {
      s.error = error
      publish()
    },
    // The host restarted in the middle of a download: the add-on comes back with no job.
    restart() {
      host.unprovide(EMBED_SERVICE)
      s.job = null
      host.provide(EMBED_SERVICE, value())
    },
  }
}

/** A DSH profile as the plugin manager sees it: package.json, its bundle selection, node_modules, allowBuilds. */
function fakeProfile({ deps = [], selected = [], files = [], allowBuilds = {} } = {}) {
  return { deps: new Set(deps), selected: new Set(selected), files: new Set(files), allowBuilds: { ...allowBuilds } }
}

/**
 * The host's plugin manager, behaving as packages/boot/plugin-manager/src/index.ts and
 * build-approval.ts do for the cases this plugin meets. installBundle waits until the test settles it:
 * run() plays the host's logic on the fake profile, settle(result) answers with a given result (an
 * applied one changes the profile as the host would). onStart() is the host starting a selected
 * bundle (the add-on's service appears).
 */
function fakePluginManager({ profile = fakeProfile(), hmr = true, scripts = [SCRIPT], refuseEnable = null, errors = {}, listFails = 0 } = {}) {
  const select = (name, enabled) => {
    if (enabled === false) return
    profile.selected.add(name)
    pm.onStart()
  }
  const pm = {
    calls: [],
    profile,
    errors,
    pending: null,
    waitAnswer: null,
    onStart: () => {},
    installBundle(spec, options) {
      pm.calls.push({ spec, options: JSON.parse(JSON.stringify(options)) })
      return new Promise((resolve) => { pm.pending = { resolve, spec, options } })
    },
    // index.ts:976-1121, with pnpm 11 refusing to finish while an install script is undecided.
    run() {
      const { resolve, spec, options } = pm.pending
      pm.pending = null
      const failed = (extra) => resolve({ application: 'failed', stage: 'install', target: spec, changed: false, ...extra })
      if (hmr && profile.deps.has(spec)) return failed({ error: { code: 'already-installed' } })
      if (options.approvedBuilds !== undefined) {
        // build-approval.ts approveBuilds: only names still undecided; then true.
        if (options.approvedBuilds.some((n) => profile.allowBuilds[n] !== UNDECIDED)) return failed({ error: { code: 'stale-approval' } })
        for (const n of options.approvedBuilds) profile.allowBuilds[n] = true
      }
      profile.files.add(spec) // pnpm wrote node_modules; "downloaded files can stay"
      const blocked = scripts.filter((n) => profile.allowBuilds[n] === undefined || profile.allowBuilds[n] === UNDECIDED)
      if (blocked.length) {
        // recordPendingBuilds keeps the key in pnpm-workspace.yaml; package.json and pnpm-lock.yaml go back.
        for (const n of blocked) profile.allowBuilds[n] = UNDECIDED
        const pendingBuilds = Object.keys(profile.allowBuilds).filter((k) => profile.allowBuilds[k] === UNDECIDED)
        return failed({ changed: true, packageResult: { kind: 'build-blocked', exitCode: 1 }, pendingBuilds, error: { code: 'unknown' } })
      }
      profile.deps.add(spec)
      resolve({ application: hmr ? 'applied' : 'restart-required', stage: 'enable', target: spec, bundle: spec, changed: true, ...(options.approvedBuilds ? { approvedBuilds: options.approvedBuilds } : {}) })
      select(spec, options.enabled)
    },
    settle(result) {
      const { resolve, spec, options } = pm.pending
      pm.pending = null
      const applied = ['applied', 'restart-required', 'overridden'].includes(result.application) && !result.error
      if (applied) {
        profile.deps.add(spec)
        profile.files.add(spec)
      }
      for (const n of result.pendingBuilds ?? []) profile.allowBuilds[n] ??= UNDECIDED
      resolve(result)
      if (applied) select(spec, options.enabled)
    },
    // index.ts:955-960 -> selectBundle (:1278-1299): needs the package's files.
    async setBundleEnabled(name, enabled) {
      pm.calls.push({ enable: name, enabled })
      if (enabled && !profile.files.has(name)) return { application: 'failed', stage: 'enable', target: name, enabled, changed: false, error: { code: 'not-bundle' } }
      if (enabled && refuseEnable) return { application: 'failed', stage: 'enable', target: name, enabled, changed: false, error: { code: refuseEnable } }
      if (!enabled) profile.selected.delete(name)
      const result = { application: hmr ? 'applied' : 'restart-required', stage: 'enable', target: name, enabled, changed: true }
      if (enabled) queueMicrotask(() => select(name, true))
      return result
    },
    // index.ts:1156-1188
    async removeBundle(name) {
      pm.calls.push({ remove: name })
      profile.selected.delete(name)
      profile.deps.delete(name)
      profile.files.delete(name)
      return { application: 'applied', stage: 'remove', target: name, changed: true }
    },
    // index.ts:506-606: names are the selection and the dependencies.
    async listBundles() {
      if (listFails > 0) {
        listFails -= 1
        throw new Error('profile read failed')
      }
      const names = new Set([...profile.selected, ...profile.deps])
      return [...names].map((name) => ({
        name, official: false, installed: profile.deps.has(name), enabled: profile.selected.has(name),
        availability: profile.files.has(name) ? 'profile' : 'missing', optional: false, removable: true, rows: [], overrides: [],
        ...(pm.errors[name] ? { error: { code: pm.errors[name] } } : profile.files.has(name) ? {} : { error: { code: 'not-bundle' } }),
      }))
    },
    async cancelInstall(requestId) {
      pm.calls.push({ cancel: requestId })
      if (pm.pending?.options.requestId !== requestId) return { status: 'not-running' }
      pm.settle({ application: 'cancelled', changed: false })
      return { status: 'cancelled' }
    },
    async waitForInstall(requestId) {
      pm.calls.push({ wait: requestId })
      return pm.waitAnswer
    },
  }
  return pm
}

const changes = (pm) => pm.calls.filter((c) => c.spec || c.enable || c.remove)

function fakeMigrations() {
  const runs = []
  return {
    runs,
    runMigration(options) {
      runs.push(options)
      const result = { state: 'done', total: 3, copied: 3, refused: 0, remaining: 0 }
      return { done: Promise.resolve().then(() => { options.onProgress(result); return result }), stop: async () => {} }
    },
  }
}

const volatileConfig = (dataDir, values = {}) => Object.fromEntries(['modules', 'retrieval', 'gateway', 'external', 'advanced']
  .map((g) => [g, volatile({ ...(g === 'advanced' ? { language: 'en' } : {}), ...(g === 'gateway' ? { dataDir } : {}), ...values[g] })]))

function setup(t, { dataDir, pm = fakePluginManager(), python = PYTHON, values, addonOptions, profileDir } = {}) {
  const dir = dataDir ?? fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-memgate-setup-'))
  if (!dataDir) t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  const host = createFakeHost()
  host.useWebServer()
  host.useCommands()
  if (pm) host.provide('pluginManager', pm)
  if (profileDir) host.provide('profileContext', { name: 'web', dir: profileDir })
  const managers = fakeManagers({ python, dataDir: dir })
  const migrations = fakeMigrations()
  const plugin = applyPlugin(host.ctx, volatileConfig(dir, values), {
    createClient: fakeClients().createClient,
    createGatewayManager: managers.createGatewayManager,
    importHost: false,
    findPython: async () => python,
    countMemories: async () => 3,
    runMigration: migrations.runMigration,
    migration: { pauseMs: 5 },
    embedPollMs: 0,
    setup: { tickMs: 15, addonWaitMs: 150, newRequestId: (() => { let n = 0; return () => `feiyu-memgate-req-${++n}` })() },
  })
  t.after(() => host.unload())
  const addon = fakeAddon(host, addonOptions)
  return { host, plugin, managers, migrations, pm, addon, dir }
}

const route = (host) => host.routes.get(MODELS_PATH)
async function get(host, peer = OWNER) {
  const r = await callRoute(route(host), { peer })
  assert.equal(r.status, 200, r.body)
  return JSON.parse(r.body)
}
async function post(host, body, { peer = OWNER, type = 'application/json' } = {}) {
  const r = await callRoute(route(host), { method: 'POST', peer, headers: { 'content-type': type }, body: JSON.stringify(body) })
  return { status: r.status, ...(r.body ? JSON.parse(r.body) : {}) }
}
async function until(check, what, ms = 2000) {
  const end = Date.now() + ms
  for (;;) {
    const value = await check()
    if (value) return value
    if (Date.now() > end) assert.fail(`timed out waiting for ${what}`)
    await tick(10)
  }
}
const phase = async (host) => (await get(host)).chain.phase
const run = (host, rawInput) => host.commands.get('memgate').handler({ rawInput, attachments: [], signal: new AbortController().signal })
const warnings = (host) => host.logs.filter((l) => l.level === 'warn' || l.level === 'error')

// ------------------------------------------------------------------ the whole chain

test('one click: add-on absent -> plugin manager installs it -> model downloads -> local tier, keyword copy', async (t) => {
  const { host, plugin, managers, migrations, pm, addon } = setup(t)
  await plugin.settled()
  assert.equal(managers.created.at(-1).store().tier, 'keyword')

  const before = await get(host)
  assert.equal(before.addon.state, 'absent')
  assert.equal(before.installer.available, true)
  assert.deepEqual(before.models.map((m) => m.id), CATALOGUE.filter((m) => m.status === 'verified').map((m) => m.id), 'the shipped catalogue while the add-on is absent')
  assert.ok(before.models.every((m) => m.state === 'absent' && m.license && m.sizeBytes > 0 && m.languages.length && m.note && m.tierLabel?.zh))
  const shipped = before.models.find((m) => m.id === SMALL)
  assert.deepEqual(shipped.memoryMB, { loaded: 188, peak: 219 }, 'the measured memory from the shipped registry copy, not the file size')
  assert.equal(shipped.title, 'BGE small zh v1.5')
  assert.match(before.confirm.en, /dsh-feiyu-memgate-embed from npm/)
  assert.match(before.confirm.en, /about 90 MB/)
  assert.match(before.confirm.en, /CPU only/)
  assert.match(before.confirm.en, /Microsoft Visual C\+\+ 2015-2022 runtime \(x64\)/)
  assert.match(before.confirm.zh, /Microsoft Visual C\+\+ 2015-2022 运行库/)
  assert.equal(changes(pm).length, 0, 'reading the page installs nothing')

  const started = await post(host, { action: 'install', id: SMALL, confirmed: true })
  assert.deepEqual([started.status, started.result.ok, started.result.code], [200, true, 'installing-addon'])
  assert.deepEqual(pm.calls[0], { spec: PACKAGE, options: { requestId: 'feiyu-memgate-req-1', enabled: true } })
  assert.equal(started.view.addon.state, 'installing')

  // The host's progress events for our request show up; other requests are ignored.
  host.emit('plugin-manager/install-state', { requestId: 'someone-else', phase: 'applying' })
  host.emit('plugin-manager/install-state', { requestId: 'feiyu-memgate-req-1', phase: 'installing', attempt: { index: 0, of: 2, registry: 'x' } })
  assert.deepEqual((await get(host)).chain.installState, { phase: 'installing', attempt: { index: 0, of: 2 } })

  pm.settle({ application: 'applied', changed: true, stage: 'enable', bundle: PACKAGE, version: '0.1.0' })
  await until(async () => (await phase(host)) === 'addon-starting', 'the wait for the add-on')
  addon.start() // the host starts the new bundle: its service appears
  await until(() => addon.calls.some((c) => c[0] === 'install'), 'the model install call')
  assert.deepEqual(addon.calls.find((c) => c[0] === 'install'), ['install', SMALL, {}])

  addon.progress(0.4)
  const downloading = await get(host)
  assert.equal(downloading.chain.phase, 'model')
  assert.equal(downloading.chain.percent, 40)
  const row = downloading.models.find((m) => m.id === SMALL)
  assert.deepEqual([row.state, row.percent], ['downloading', 40])
  assert.deepEqual(row.memoryMB, { loaded: 190, peak: 221 }, 'the add-on’s own figure once it runs')

  addon.finish()
  await until(async () => (await phase(host)) === 'done', 'done')
  await plugin.settled()
  const gateway = managers.created.at(-1)
  assert.equal(gateway.store().tier, 'local', 'auto now runs the local tier')
  assert.equal(gateway.store().env[EMBEDDINGS_KEY_ENV], ADDON_KEY)
  await until(() => migrations.runs.length === 1, 'the keyword copy')
  assert.equal(migrations.runs[0].target.tier, 'local')
  const status = await plugin.status()
  assert.equal(status.retrieval.active, 'local')
  assert.equal(status.migration.state, 'done')
  assert.deepEqual(status.setup, { target: SMALL, phase: 'done', error: null })
  const after = await get(host)
  assert.equal(after.models.find((m) => m.id === SMALL).state, 'active')
  assert.equal(after.busy, false)
})

test('with the add-on present a click downloads at once; a downloaded model is enabled; a licence must be accepted', async (t) => {
  const { host, pm, addon } = setup(t)
  addon.start()
  addon.s.installed.add('bge-m3')
  const gated = await post(host, { action: 'install', id: GATED.id, confirmed: true })
  assert.deepEqual([gated.result.ok, gated.result.code], [false, 'needs-acceptance'])
  assert.equal(gated.result.licence.name, LICENCE.name)
  assert.equal(addon.calls.length, 0, 'nothing asked of the add-on before the licence is accepted')
  const accepted = await post(host, { action: 'install', id: GATED.id, confirmed: true, acceptLicence: LICENCE.name })
  assert.equal(accepted.result.ok, true)
  assert.deepEqual(addon.calls.at(-1), ['install', GATED.id, { acceptLicence: LICENCE.name }])
  assert.equal(changes(pm).length, 0, 'the add-on is there: the plugin manager is not involved')
  await post(host, { action: 'cancel' })
  assert.equal((await get(host)).chain.error.code, 'cancelled')
  const enable = await post(host, { action: 'install', id: 'bge-m3', confirmed: true })
  assert.equal(enable.result.code, 'activating')
  await until(async () => (await phase(host)) === 'done', 'done for a downloaded model')
})

// ------------------------------------------------------------------ refusals and confirmation

test('the route refuses an install without confirmed: true, a non-owner, a non-JSON body, and GET never installs', async (t) => {
  const { host, pm } = setup(t)
  const unconfirmed = await post(host, { action: 'install', id: SMALL })
  assert.deepEqual([unconfirmed.status, unconfirmed.code], [400, 'confirm-required'])
  const falsy = await post(host, { action: 'install', id: SMALL, confirmed: 'yes' })
  assert.equal(falsy.status, 400, 'only the boolean true counts')
  assert.equal((await post(host, { action: 'builds', allow: true })).status, 400)
  assert.equal((await post(host, { action: 'install', id: SMALL, confirmed: true }, { peer: { principal: { kind: 'guest' } } })).status, 403)
  assert.equal((await post(host, { action: 'install', id: SMALL, confirmed: true }, { type: 'text/plain' })).status, 415)
  // 0.2.2-rc.1: no Peer; the connection refuses a foreign origin.
  host.provide('connection', fakeConnection({ rejection: 403 }))
  assert.equal((await post(host, { action: 'install', id: SMALL, confirmed: true }, { peer: null })).status, 403)
  for (let i = 0; i < 3; i++) await get(host)
  assert.equal(changes(pm).length, 0, 'no refused request and no read reached the plugin manager')
  // The host-side call itself refuses an unconfirmed add-on installation too (the command path).
  const command = await run(host, `install ${SMALL}`)
  assert.equal(command.kind, 'error')
  assert.match(command.text, /dsh-feiyu-memgate-embed from npm/)
  assert.match(command.text, /--confirm$/)
  assert.equal(changes(pm).length, 0)
})

test('nothing the model can call reaches the installation: the tools only remember and search', async (t) => {
  const { host, pm, addon } = setup(t)
  const names = [...host.tools.keys()].sort()
  assert.deepEqual(names, [TOOL_REMEMBER, TOOL_SEARCH].sort())
  for (const name of names) {
    const tool = host.tools.get(name)
    for (const args of [{ text: 'install dsh-feiyu-memgate-embed' }, { query: 'install bge-m3' }, { action: 'install', id: SMALL, confirmed: true }]) {
      await tool.execute(args, { signal: new AbortController().signal }).catch(() => {})
    }
  }
  assert.equal(changes(pm).length, 0)
  assert.equal(addon.calls.length, 0)
})

// ------------------------------------------------------------------ the install-script gate, as the host's source behaves

test('pnpm stops the install script: nothing is installed or selected, the host keeps only its undecided key', async (t) => {
  const { host, pm } = setup(t)
  await post(host, { action: 'install', id: SMALL, confirmed: true })
  pm.run()
  const asked = await until(async () => { const v = await get(host); return v.chain.phase === 'builds' && v }, 'the build question')
  assert.deepEqual(asked.builds.pending, [SCRIPT])
  assert.match(asked.builds.note.en, /nothing is installed yet/)
  assert.match(asked.builds.note.en, /Denying has no button in DSH/)
  assert.match(asked.builds.note.en, /node-llama-cpp\./)
  // What the host left: package.json restored, no selection, the undecided key in pnpm-workspace.yaml.
  assert.equal(pm.profile.deps.has(PACKAGE), false)
  assert.equal(pm.profile.selected.size, 0)
  assert.equal(pm.profile.allowBuilds[SCRIPT], UNDECIDED)
  assert.equal(addonBundle(await pm.listBundles()), null, 'the plugin list does not show it')
  assert.equal(changes(pm).length, 1, 'nothing approved on the person’s behalf')
  assert.equal((await post(host, { action: 'install', id: SMALL, confirmed: true })).result.code, 'builds-pending')
  assert.equal((await post(host, { action: 'builds', allow: true })).status, 400, 'an answer needs confirmed: true')
})

test('allow: the retry approves exactly those keys and ends with the bundle selected, running and the model in use', async (t) => {
  const { host, pm, addon, managers, plugin } = setup(t)
  pm.onStart = () => addon.start()
  await post(host, { action: 'install', id: SMALL, confirmed: true })
  pm.run()
  await until(async () => (await phase(host)) === 'builds', 'the build question')
  const allowed = await post(host, { action: 'builds', allow: true, confirmed: true })
  assert.equal(allowed.result.ok, true)
  assert.deepEqual(pm.calls[1], { spec: PACKAGE, options: { requestId: 'feiyu-memgate-req-2', enabled: true, approvedBuilds: [SCRIPT] } })
  pm.run()
  assert.equal(pm.profile.allowBuilds[SCRIPT], true, 'the host recorded the permission the person gave')
  assert.ok(pm.profile.deps.has(PACKAGE) && pm.profile.selected.has(PACKAGE), 'installed and selected: the bundle is active')
  await until(() => addon.calls.some((c) => c[0] === 'install'), 'the model step after the approved install')
  addon.finish()
  await until(async () => (await phase(host)) === 'done', 'done')
  await plugin.settled()
  assert.equal(managers.created.at(-1).store().tier, 'local')
})

test('deny: the exact file and line, then the same button installs with the script skipped and switches it on', async (t) => {
  const profileDir = path.join(os.tmpdir(), 'dsh-home-for-tests', 'profiles', 'web')
  const { host, pm, addon } = setup(t, { profileDir })
  pm.onStart = () => addon.start()
  await post(host, { action: 'install', id: SMALL, confirmed: true })
  pm.run()
  await until(async () => (await phase(host)) === 'builds', 'the build question')
  const denied = await post(host, { action: 'builds', allow: false, confirmed: true })
  assert.equal(denied.result.code, 'builds-denied')
  const file = path.join(profileDir, 'pnpm-workspace.yaml')
  for (const problem of [denied.result.problem, denied.view.chain.error]) {
    assert.ok(problem.todo.en.includes(`Open ${file}.`), problem.todo.en)
    assert.match(problem.todo.en, /“node-llama-cpp: set this to true or false”; change it to “node-llama-cpp: false”/)
    assert.match(problem.todo.en, /press “Download and enable” again/)
    assert.ok(problem.todo.zh.includes(file))
  }
  assert.equal(changes(pm).length, 1, 'no second install, no approval')
  assert.equal(pm.profile.allowBuilds[SCRIPT], UNDECIDED, 'the plugin writes nothing into the profile')
  assert.match((await run(host, 'models')).text, /node-llama-cpp: false/, 'the command shows the same instruction')

  // Pressing again before the edit asks the question again (the key is still undecided).
  await post(host, { action: 'install', id: SMALL, confirmed: true })
  pm.run()
  await until(async () => (await phase(host)) === 'builds', 'the question again')
  assert.match((await run(host, 'builds deny')).text, /node-llama-cpp: false/)

  // The person edits the line as told, then presses the button again.
  pm.profile.allowBuilds[SCRIPT] = false
  const again = await post(host, { action: 'install', id: SMALL, confirmed: true })
  assert.equal(again.result.code, 'installing-addon')
  assert.equal(pm.calls.at(-1).options.approvedBuilds, undefined, 'no approval: the script stays denied')
  pm.run()
  assert.ok(pm.profile.deps.has(PACKAGE) && pm.profile.selected.has(PACKAGE), 'new to package.json, so the host selects it')
  await until(() => addon.calls.some((c) => c[0] === 'install'), 'the model step')
  addon.finish()
  await until(async () => (await phase(host)) === 'done', 'done')
})

test('installed but not selected (what the CLI leaves): the click switches it on instead of waiting for nothing', async (t) => {
  const pm = fakePluginManager({ profile: fakeProfile({ deps: [PACKAGE], files: [PACKAGE], allowBuilds: { [SCRIPT]: UNDECIDED } }) })
  const { host, addon } = setup(t, { pm })
  pm.onStart = () => addon.start()
  const r = await post(host, { action: 'install', id: SMALL, confirmed: true })
  assert.equal(r.result.code, 'enabling-addon')
  assert.deepEqual(changes(pm), [{ enable: PACKAGE, enabled: true }], 'setBundleEnabled, no second installation')
  await until(() => addon.calls.some((c) => c[0] === 'install'), 'the model step')
  assert.ok(pm.profile.selected.has(PACKAGE))
  addon.finish()
  await until(async () => (await phase(host)) === 'done', 'done')
  assert.match((await run(host, 'models')).text, /in use/)
})

test('the host refusing a second installation as already-installed leads to the same repair', async (t) => {
  // The listing fails once, so the click installs; with HMR the host refuses a package already in
  // package.json before pnpm runs.
  const pm = fakePluginManager({ profile: fakeProfile({ deps: [PACKAGE], files: [PACKAGE] }), listFails: 1 })
  const { host, addon } = setup(t, { pm })
  pm.onStart = () => addon.start()
  await post(host, { action: 'install', id: SMALL, confirmed: true })
  pm.run()
  await until(() => addon.calls.some((c) => c[0] === 'install'), 'the model step after the repair')
  assert.deepEqual(changes(pm).map((c) => (c.spec ? 'install' : c.enable ? 'enable' : 'remove')), ['install', 'enable'])
})

test('an add-on whose files are gone is removed and installed again', async (t) => {
  const pm = fakePluginManager({ profile: fakeProfile({ deps: [PACKAGE], allowBuilds: { [SCRIPT]: false } }) })
  const { host, addon } = setup(t, { pm })
  pm.onStart = () => addon.start()
  await post(host, { action: 'install', id: SMALL, confirmed: true })
  await until(() => pm.pending, 'the new installation')
  assert.deepEqual(changes(pm).map((c) => (c.spec ? 'install' : c.enable ? 'enable' : 'remove')), ['remove', 'install'])
  pm.run()
  await until(() => addon.calls.some((c) => c[0] === 'install'), 'the model step')
})

test('a bundle the host lists with a problem, or will not switch on, is reported instead of waited for', async (t) => {
  {
    const pm = fakePluginManager({ profile: fakeProfile({ deps: [PACKAGE], files: [PACKAGE], selected: [PACKAGE] }), errors: { [PACKAGE]: 'incompatible-version' } })
    const { host } = setup(t, { pm })
    const r = await post(host, { action: 'install', id: SMALL, confirmed: true })
    assert.equal(r.result.code, 'addon-incompatible')
  }
  {
    const pm = fakePluginManager({ profile: fakeProfile({ deps: [PACKAGE], files: [PACKAGE] }), refuseEnable: 'unsupported-platform' })
    const { host } = setup(t, { pm })
    await post(host, { action: 'install', id: SMALL, confirmed: true })
    const view = await until(async () => { const v = await get(host); return v.chain.phase === 'failed' && v }, 'refused switch-on')
    assert.equal(view.chain.error.code, 'runtime-unavailable')
  }
  {
    // Installed and selected, then the add-on is deselected while the page waits for it to start.
    const { host, pm } = setup(t)
    await post(host, { action: 'install', id: SMALL, confirmed: true })
    pm.settle({ application: 'applied', changed: true })
    await until(async () => (await phase(host)) === 'addon-starting', 'the wait')
    pm.profile.selected.delete(PACKAGE)
    await until(() => changes(pm).some((c) => c.enable), 'the repair while waiting')
  }
  assert.equal(enableFailureOf('management-required'), 'addon-enable-failed')
})

test('an unanswered question: restarts change nothing and warn nothing; the profile keeps only the host’s key', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-memgate-abandon-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  const profile = fakeProfile()
  const first = setup(t, { dataDir: dir, pm: fakePluginManager({ profile }) })
  await post(first.host, { action: 'install', id: SMALL, confirmed: true })
  first.pm.run()
  await until(async () => (await phase(first.host)) === 'builds', 'the build question')
  await first.host.unload()

  for (let restart = 0; restart < 2; restart++) {
    const pm = fakePluginManager({ profile })
    const next = setup(t, { dataDir: dir, pm })
    await tick(60)
    const view = await get(next.host)
    assert.equal(view.chain.phase, 'builds', 'the question is still offered after a restart')
    assert.deepEqual(view.builds.pending, [SCRIPT])
    assert.deepEqual(changes(pm), [], 'nothing installed, switched on or removed at start')
    assert.deepEqual(warnings(next.host), [], 'no warning at start')
    if (restart === 1) {
      const cancelled = await post(next.host, { action: 'cancel' })
      assert.equal(cancelled.result.code, 'cancelled')
      assert.equal(cancelled.view.chain.phase, 'idle')
      assert.deepEqual(changes(pm), [])
    }
    await next.host.unload()
  }
  // Nothing of the add-on is a dependency or selected, so the host starts nothing for it; the
  // undecided key is the host's own record, read only by a later installation.
  assert.deepEqual([profile.deps.size, profile.selected.size], [0, 0])
  assert.deepEqual(profile.allowBuilds, { [SCRIPT]: UNDECIDED })
})

// ------------------------------------------------------------------ failure states

test('every failure state is shown with what to do', async (t) => {
  const addonCase = async (result, code) => {
    const { host, pm } = setup(t)
    await post(host, { action: 'install', id: SMALL, confirmed: true })
    pm.settle(result)
    const view = await until(async () => { const v = await get(host); return v.chain.phase === 'failed' && v }, code)
    assert.equal(view.chain.error.code, code, JSON.stringify(result))
    assert.ok(view.chain.error.title.en && view.chain.error.todo.en && view.chain.error.title.zh && view.chain.error.todo.zh)
  }
  await addonCase({ application: 'cancelled' }, 'install-cancelled') // the person declined / cancelled
  await addonCase({ application: 'failed', error: { code: 'incompatible-version' } }, 'addon-incompatible')
  await addonCase({ application: 'failed', error: { code: 'unsupported-platform' } }, 'runtime-unavailable')
  await addonCase({ application: 'failed', packageResult: { kind: 'network' } }, 'network')
  await addonCase({ application: 'failed', packageResult: { kind: 'disk-full' } }, 'disk-full')
  await addonCase({ application: 'failed', packageResult: { kind: 'not-found' } }, 'addon-not-found')
  await addonCase({ application: 'failed', error: { code: 'stale-approval' } }, 'build-approval')

  const jobCase = async (error, code) => {
    const { host, addon } = setup(t)
    addon.start()
    await post(host, { action: 'install', id: SMALL, confirmed: true })
    addon.fail(error)
    const view = await until(async () => { const v = await get(host); return v.chain.phase === 'failed' && v }, code)
    assert.equal(view.chain.error.code, code)
  }
  await jobCase('network', 'network') // no network or mirror unreachable
  await jobCase('timeout', 'network')
  await jobCase('disk-full', 'disk-full')
  await jobCase('integrity', 'integrity') // download verification failed

  // The engine cannot load (on Windows: the Visual C++ runtime is missing): the model is active but
  // the add-on reports runtime-missing.
  {
    const { host, addon } = setup(t)
    addon.start()
    addon.s.installed.add(SMALL)
    addon.crash('runtime-missing')
    await post(host, { action: 'install', id: SMALL, confirmed: true })
    const view = await until(async () => { const v = await get(host); return v.chain.phase === 'failed' && v }, 'runtime')
    assert.equal(view.chain.error.code, 'runtime-unavailable')
    assert.match(view.chain.error.title.en, /runtime-missing/)
    assert.match(view.chain.error.todo.en, /Microsoft Visual C\+\+ 2015-2022 Redistributable \(x64\)/)
    assert.match(view.chain.error.todo.en, /vc_redist\.x64\.exe/)
    assert.match(view.chain.error.todo.en, /restart DSH/)
    assert.match(view.chain.error.todo.zh, /Microsoft Visual C\+\+ 2015-2022 运行库/)
  }
  // A model pinned in the add-on's own settings.
  {
    const { host, addon } = setup(t, { addonOptions: { pinned: 'bge-m3' } })
    addon.start()
    const r = await post(host, { action: 'install', id: SMALL, confirmed: true })
    assert.equal(r.result.code, 'pinned')
    assert.match(r.view.chain.error.todo.en, /activeModel back to none/)
    assert.equal(r.view.models.find((m) => m.id === 'bge-m3').pinned, true)
  }
  // Python missing: the managed gateway is off; the page says so next to the models.
  {
    const { host } = setup(t, { python: null })
    const view = await get(host)
    assert.equal(view.python.found, false)
    assert.equal(view.python.problem.code, 'python-missing')
  }
  // No plugin manager in this profile.
  {
    const { host } = setup(t, { pm: null })
    const r = await post(host, { action: 'install', id: SMALL, confirmed: true })
    assert.equal(r.result.code, 'no-plugin-manager')
    assert.equal(r.view.installer.available, false)
  }
  // Installed and started, but its service never comes: the wait is bounded.
  {
    const { host, pm } = setup(t)
    await post(host, { action: 'install', id: SMALL, confirmed: true })
    pm.settle({ application: 'applied', changed: true })
    const view = await until(async () => { const v = await get(host); return v.chain.phase === 'failed' && v }, 'not started')
    assert.equal(view.chain.error.code, 'addon-not-started')
  }
})

test('cancel stops the add-on installation through the host', async (t) => {
  const { host, pm } = setup(t)
  await post(host, { action: 'install', id: SMALL, confirmed: true })
  const r = await post(host, { action: 'cancel' })
  assert.equal(r.result.code, 'cancelled')
  assert.deepEqual(pm.calls[1], { cancel: 'feiyu-memgate-req-1' })
  await until(async () => (await get(host)).chain.error?.code === 'install-cancelled', 'the cancelled state')
})

// ------------------------------------------------------------------ resumption

test('a reload during the add-on installation picks it up from the host, or says it was interrupted', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-memgate-resume-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  const first = setup(t, { dataDir: dir })
  await post(first.host, { action: 'install', id: SMALL, confirmed: true })
  const saved = JSON.parse(fs.readFileSync(path.join(dir, SETUP_FILE), 'utf8'))
  assert.deepEqual([saved.phase, saved.target, saved.requestId], ['addon', SMALL, 'feiyu-memgate-req-1'])
  await first.host.unload()

  // The plugin reloads while the host still runs the install: waitForInstall answers it (and the host
  // has selected the bundle by then).
  const pm = fakePluginManager({ profile: fakeProfile({ deps: [PACKAGE], files: [PACKAGE], selected: [PACKAGE] }) })
  pm.waitAnswer = { application: 'applied', changed: true }
  const second = setup(t, { dataDir: dir, pm })
  await until(async () => (await phase(second.host)) === 'addon-starting', 'the resumed wait for the add-on')
  assert.deepEqual(pm.calls[0], { wait: 'feiyu-memgate-req-1' })
  second.addon.start()
  await until(() => second.addon.calls.length > 0, 'the model step after the reload')
  await second.host.unload()

  // A host restart lost the install call and the add-on is not there.
  fs.writeFileSync(path.join(dir, SETUP_FILE), JSON.stringify({ ...saved, updated: 1 }))
  const third = setup(t, { dataDir: dir })
  await until(async () => (await get(third.host)).chain.error?.code === 'interrupted', 'interrupted')
})

test('a host restart in the middle of the download shows "resume", which continues the same model', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-memgate-paused-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  const first = setup(t, { dataDir: dir })
  first.addon.start()
  await post(first.host, { action: 'install', id: SMALL, confirmed: true })
  first.addon.progress(0.3)
  await first.host.unload()

  // After the restart the add-on keeps the part but has no job and does not activate by itself.
  const second = setup(t, { dataDir: dir })
  second.addon.start()
  const view = await until(async () => { const v = await get(second.host); return v.chain.phase === 'paused' && v }, 'paused')
  assert.equal(view.models.find((m) => m.id === SMALL).state, 'paused')
  assert.match((await run(second.host, 'models')).text, /interrupted/)
  const resumed = await post(second.host, { action: 'install', id: SMALL, confirmed: true })
  assert.equal(resumed.result.ok, true)
  assert.deepEqual(second.addon.calls.at(-1), ['install', SMALL, {}])
  second.addon.finish()
  await until(async () => (await phase(second.host)) === 'done', 'done after resuming')
})

test('a finished download whose activation a restart cut off is enabled on the next look', async (t) => {
  const { host, addon } = setup(t)
  addon.start()
  await post(host, { action: 'install', id: SMALL, confirmed: true })
  addon.finish({ activate: false })
  await until(async () => (await phase(host)) === 'done', 'done')
  assert.ok(addon.calls.some((c) => c[0] === 'use' && c[1] === SMALL))
})

// ------------------------------------------------------------------ the command

test('/memgate models, install, use, remove and cancel call the same functions as the page', async (t) => {
  const { host, pm, addon } = setup(t, { values: { advanced: { language: 'zh' } } })
  const list = (await run(host, 'models')).text
  assert.match(list, /本地向量模型（向量插件：未安装）/)
  assert.match(list, /bge-small-zh-v1\.5（最轻档）：下载 47\.9 MB，运行内存约 188 MB，中文，MIT。状态：未下载。/)
  assert.match(list, /bge-m3（最高档）：下载 1\.2 GB，运行内存约 1577 MB，多语言，MIT。/)
  assert.doesNotMatch(list, /lightest|upper-middle|（top）/, 'no untranslated tier word')
  assert.match(list, /部署档位/)
  const confirm = await run(host, `install ${SMALL}`)
  assert.match(confirm.text, /--confirm$/)
  assert.match(confirm.text, /仅使用 CPU 运行/)
  const started = await run(host, `install ${SMALL} --confirm`)
  assert.equal(started.kind, 'success')
  assert.match(started.text, /插件管理器/)
  assert.equal(pm.calls[0].spec, PACKAGE)
  pm.settle({ application: 'applied', changed: true })
  addon.start()
  await until(() => addon.calls.length > 0, 'the model step')
  assert.match((await run(host, 'models')).text, /下载中/)
  assert.equal((await run(host, 'cancel')).text, '已取消。')
  addon.s.installed.add('bge-m3')
  assert.match((await run(host, 'use bge-m3')).text, /bge-m3/)
  assert.equal((await run(host, `remove bge-m3`)).kind, 'error', 'the active model is not removed')
  assert.match((await run(host, 'use keyword')).text, /设置服务/, 'use <tier> still goes to the tier setting (no Settings service here)')
  const gated = await run(host, `install ${GATED.id}`)
  assert.match(gated.text, /--accept-licence Example Model Licence/)
})

test('the English command names tiers and running memory in words', async (t) => {
  const { host } = setup(t)
  const list = (await run(host, 'models')).text
  assert.match(list, /- bge-small-zh-v1\.5 \(lightest tier\): 47\.9 MB download, about 188 MB of memory while running, Chinese, MIT\. State: not downloaded\./)
})

// ------------------------------------------------------------------ readers

test('the add-on and host answers are read defensively', () => {
  assert.deepEqual(readJob({ ok: true, job: { id: SMALL, state: 'downloading', bytes: 50, total: 200, error: null } }),
    { id: SMALL, state: 'downloading', bytes: 50, total: 200, percent: 25, reason: '', activated: false })
  assert.equal(readJob({ ok: true, job: null }), null)
  assert.equal(readJob({ ok: true, job: { id: '../x' } }), null)
  assert.equal(readJob({ id: SMALL, state: 'failed', error: 'disk-full' }).reason, 'disk-full')
  const list = readList({ ok: true, active: null, pinned: 'bge-m3', models: [{ id: SMALL, memoryMB: { loaded: 50, peak: null }, license: 'MIT', licenseUrl: 'javascript:alert(1)' }, { id: 7 }] })
  assert.equal(list.pinned, 'bge-m3')
  assert.equal(list.rows.length, 1)
  assert.deepEqual(list.rows[0].memoryMB, { loaded: 50, peak: null })
  assert.equal(readList({ models: [{ id: SMALL, memoryMB: { loaded: 'lots' } }] }).rows[0].memoryMB, null, 'no measurement stays unknown')
  assert.deepEqual(list.rows[0].license, { name: 'MIT', url: '' }, 'only https links reach the page')
  assert.equal(readList({ get models() { throw new Error('boom') } }).rows.length, 0)
  assert.equal(failureOf('dns'), 'network')
  assert.equal(failureOf('runtime-missing'), 'runtime-unavailable')
  assert.equal(failureOf('something-new'), 'unknown')
  assert.equal(addonFailureOf({ packageResult: { kind: 'build-blocked' } }), 'build-approval')
  assert.equal(addonFailureOf({ packageResult: { kind: 'pnpm-missing' } }), 'addon-install-failed')
  // listBundles rows: installed is the dependency, enabled the selection, availability the files.
  assert.deepEqual(addonBundle([{ name: PACKAGE, installed: true, enabled: false, availability: 'profile' }]), { installed: true, files: true, enabled: false, error: null })
  assert.deepEqual(addonBundle([{ name: PACKAGE, installed: true, enabled: true, availability: 'missing', error: { code: 'not-bundle' } }]), { installed: true, files: false, enabled: true, error: 'not-bundle' })
  assert.equal(addonBundle([{ name: 'another-plugin', installed: true }]), null)
  assert.equal(addonBundle('nope'), null)
})
