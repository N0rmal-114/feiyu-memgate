// The managed gateway with the real bundled Python package and the Python found on this machine
// (3.11 or newer). Discovery edge cases use a fake probe runner; everything else starts real
// gateway processes in temporary folders and checks that none is left behind.
import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createClient } from '../src/client.js'
import { readConfig } from '../src/config.js'
import {
  BOOTSTRAP, IDENTITY_KEY_ENV, IDENTITY_KEY_FILE, PACKAGE_DIR, TOKEN_ENV, createGatewayManager, findPython, freePort, gatewayConfig, loadIdentityKey, pythonCandidates, scrubbedEnv, tomlString,
} from '../src/gateway.js'
import { EMBEDDINGS_KEY_ENV, readEmbed } from '../src/tiers.js'

// The gateway process and its pipes are unref'd on purpose: they must never keep a host alive. In a test
// file nothing else holds the event loop open while a start or a stop is awaited, and Node 22 then ends
// the file with "Promise resolution is still pending" (Node 24 waits). This timer stands in for the host.
const keepAlive = setInterval(() => {}, 60000)
after(() => clearInterval(keepAlive))

const HERE = path.dirname(fileURLToPath(import.meta.url))
// Only the Python on PATH: the DSH home points at a folder that does not exist, so the discovery
// never reaches a desktop runtime installed on this machine.
const ISOLATED_ENV = { ...process.env, DSH_HOME: path.join(os.tmpdir(), 'dsh-memgate-no-home'), DSH_PRIMARY_RUNTIME: '' }
const python = await findPython({ cache: null, env: ISOLATED_ENV })
const skip = python ? false : 'no Python 3.11 or newer on PATH'
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// A temporary data folder per test. Its cleanup first stops every manager made for the test (an
// open store cannot be deleted on Windows), then removes the folder.
const managersOf = new WeakMap()
function tempDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-memgate-gw-'))
  managersOf.set(t, [])
  t.after(async () => {
    await Promise.allSettled(managersOf.get(t).map((m) => m.stop()))
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  })
  return dir
}

function isAlive(pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return error.code === 'EPERM'
  }
}

async function waitFor(check, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await check()) return
    await sleep(100)
  }
  assert.fail(`timed out waiting for ${label}`)
}

function manager(t, dataDir, overrides = {}, extra = {}) {
  const warnings = []
  const settings = readConfig({ ...overrides, gateway: { dataDir, python: python.executable, ...overrides.gateway } })
  const m = createGatewayManager({ settings, env: ISOLATED_ENV, logger: { warn: (w) => warnings.push(w) }, find: async () => python, ...extra })
  managersOf.get(t).push(m)
  return { m, warnings }
}

// The generated configuration names an identity key in the environment: a fixed one for the tests that run the gateway by hand.
const TEST_IDENTITY_KEY = 'ab'.repeat(32)

// `feiyu-memgate check` on a configuration file: 0 when the gateway accepts it.
function check(file, env = {}) {
  const code = `import os, sys; sys.path.insert(0, os.environ["FEIYU_MEMGATE_PACKAGE_DIR"]); from feiyu_memgate.cli import main; sys.exit(main())`
  // -B: no byte-code caches next to the bundled sources (the package copy must stay exact).
  return spawnSync(python.executable, ['-I', '-B', '-c', code, 'check', '--config', file], {
    env: { ...scrubbedEnv(), FEIYU_MEMGATE_PACKAGE_DIR: PACKAGE_DIR, PYTHONIOENCODING: 'utf-8', [IDENTITY_KEY_ENV]: TEST_IDENTITY_KEY, ...env },
    encoding: 'utf8',
    windowsHide: true,
    timeout: 30000,
  })
}

// ------------------------------------------------------------------ discovery

test('discovery tries the configured path first, then the desktop runtime, then PATH', () => {
  const list = pythonCandidates({ configured: '/opt/py/bin/python3', env: { DSH_PRIMARY_RUNTIME: '/app/runtime/primary-runtime', DSH_HOME: '/home/u/.dsh' }, platform: 'linux' })
  assert.deepEqual(list.map((c) => c.source), ['settings', 'desktop', 'desktop', 'path', 'path', 'path'])
  assert.equal(list[1].argv[0], path.join('/app/runtime/primary-runtime', 'dependencies', 'python', 'bin', 'python3'))
  assert.equal(list[2].argv[0], path.join(path.resolve('/home/u/.dsh'), 'dsh-runtimes', 'dsh-primary-runtime', 'dependencies', 'python', 'bin', 'python3'))
  assert.deepEqual(list.slice(3).map((c) => c.argv), [['python3'], ['python'], ['py', '-3']])
  const win = pythonCandidates({ env: { DSH_PRIMARY_RUNTIME: 'C:\\app\\primary-runtime' }, platform: 'win32' })
  assert.ok(win[0].argv[0].endsWith(path.join('dependencies', 'python', 'python.exe')))
})

test('discovery skips a too-old interpreter and missing desktop files, and caches its answer', async () => {
  const seen = []
  const runner = async (argv) => {
    seen.push(argv.join(' '))
    if (argv[0] === 'python3') return JSON.stringify({ version: [3, 10, 12], executable: '/usr/bin/python3.10', ok: true })
    if (argv[0] === 'python') return JSON.stringify({ version: [3, 12, 1], executable: '/usr/bin/python3.12', ok: true })
    return null
  }
  const cache = new Map()
  const exists = (p) => p === '/usr/bin/python3.12'
  const found = await findPython({ env: { DSH_HOME: '/nowhere' }, runner, exists, cache, platform: 'linux' })
  assert.deepEqual(found, { executable: '/usr/bin/python3.12', argv: ['/usr/bin/python3.12'], version: '3.12.1', source: 'path' })
  assert.deepEqual(seen, ['python3', 'python'], 'desktop paths that do not exist are not run')
  await findPython({ env: { DSH_HOME: '/nowhere' }, runner, exists, cache, platform: 'linux' })
  assert.equal(seen.length, 2, 'the second call is answered from the cache')
})

test('discovery refuses a build without sqlite3 and gives null when nothing fits', async () => {
  const noSqlite = async () => JSON.stringify({ version: [3, 13, 0], executable: '', ok: false })
  assert.equal(await findPython({ env: {}, runner: noSqlite, exists: () => false, cache: null }), null)
  assert.equal(await findPython({ env: {}, runner: async () => null, exists: () => false, cache: null }), null)
})

test('no suitable Python: one clear warning, memory off, no process', { skip }, async (t) => {
  const dataDir = tempDir(t)
  const warnings = []
  const m = createGatewayManager({ settings: readConfig({ gateway: { dataDir } }), logger: { warn: (w) => warnings.push(w) }, find: async () => null })
  managersOf.get(t).push(m)
  m.start()
  assert.equal(await m.ready(), 'off')
  assert.equal(warnings.length, 1)
  assert.match(warnings[0], /no Python 3\.11 or newer/)
  assert.equal(m.pid(), null)
  assert.equal(m.endpoint(), null)
})

test('the real discovery finds this machine\'s Python', { skip }, () => {
  assert.match(python.version, /^3\.(1[1-9]|[2-9]\d)\./)
  assert.ok(fs.existsSync(python.executable))
})

// ------------------------------------------------------------------ configuration

test('TOML strings escape quotes, backslashes and control characters', () => {
  assert.equal(tomlString('C:\\data\\"x"'), '"C:\\\\data\\\\\\"x\\""')
  assert.equal(tomlString('a\nb\u007f'), '"a\\u000Ab\\u007F"')
  assert.equal(tomlString('记忆'), '"记忆"')
})

test('the gateway accepts every generated configuration', { skip }, async (t) => {
  const dataDir = tempDir(t)
  const token = 'gateway-test-token-' + 'z'.repeat(32)
  // The embedding add-on's service as it would be offered (fake values; nothing listens there).
  const embed = (model, calibration = null) => readEmbed({
    version: 1, state: 'ready', url: 'http://127.0.0.1:9/v1', key: 'fake-addon-key', model, dimensions: 8,
    fingerprint: `${model}|f16|0123456789ab`, calibration, models: [],
  })
  const variants = [
    [{}],
    [{ advanced: { profile: 'precise' } }],
    [{ advanced: { redaction: false, newerFirst: false, dailyWrites: 0 } }],
    [{ advanced: { forgetting: true } }],
    // The linked stage: off, and on without newer_first before it.
    [{ advanced: { linkedRecall: false } }],
    [{ advanced: { newerFirst: false, linkedRecall: true } }],
    [{ retrieval: { tier: 'endpoint', hybrid: true, url: 'http://127.0.0.1:9/v1', model: 'bge-m3' }, advanced: { newerFirst: false, forgetting: true } }],
    [{ retrieval: { tier: 'endpoint', url: 'http://127.0.0.1:9/v1', model: 'bge-m3' } }],
    [{ retrieval: { tier: 'endpoint', url: 'http://127.0.0.1:9/v1', model: 'bge-m3', apiKey: 'fake-embeddings-key' }, advanced: { forgetting: true } }],
    // Hybrid: the keyword index and the vector table side by side; the key still comes from the environment.
    [{ retrieval: { tier: 'endpoint', hybrid: true, url: 'http://127.0.0.1:9/v1', model: 'bge-m3' } }],
    [{ retrieval: { tier: 'endpoint', hybrid: true, url: 'http://127.0.0.1:9/v1', model: 'bge-m3', apiKey: 'fake-embeddings-key' }, advanced: { forgetting: true } }],
    // auto with the add-on ready is the local tier; a model the gateway has a preset for gets no calibration.
    [{}, { embed: embed('bge-small-zh-v1.5', { preset: 'bge-small-zh', parity: 'exact', floor: 0.1, ceil: 0.2, query_prefix: 'q: ', document_prefix: '' }) }],
    // A model without a gateway preset takes the add-on's numbers.
    [{ retrieval: { tier: 'local' }, advanced: { forgetting: true } }, { embed: embed('tiny-test-embedder', { preset: '', parity: 'approximate', floor: 0.3, ceil: 0.7, query_prefix: 'query: ', document_prefix: 'passage: ' }) }],
  ]
  for (const [index, [overrides, facts = {}]] of variants.entries()) {
    const settings = readConfig(overrides)
    const { text, env, store } = gatewayConfig({ port: await freePort(), dataDir: path.join(dataDir, String(index)), settings, facts })
    fs.mkdirSync(path.join(dataDir, String(index)), { recursive: true })
    const file = path.join(dataDir, String(index), 'gateway.toml')
    fs.writeFileSync(file, text)
    for (const secret of [token, 'fake-embeddings-key', 'fake-addon-key']) assert.ok(!text.includes(secret), 'no secret in the file')
    const result = check(file, { [TOKEN_ENV]: token, ...env })
    assert.equal(result.status, 0, `variant ${index}:\n${result.stdout}\n${result.stderr}`)
    if (settings.advanced.forgetting) assert.match(text, /\[forgetting\]/)
    assert.equal(/"linked"/.test(text), settings.advanced.linkedRecall, 'the linked stage follows the setting')
    if (settings.retrieval.apiKey) assert.equal(env[EMBEDDINGS_KEY_ENV], 'fake-embeddings-key')
    if (settings.retrieval.hybrid) {
      assert.equal(store.hybrid, true)
      assert.match(text, /type = "hybrid"/)
      assert.match(text, /children = \[\{ type = "sqlite", path = /)
      assert.ok(!text.includes('weights'), 'the default weights are left to the gateway')
    }
    if (facts.embed) {
      assert.equal(store.tier, 'local')
      assert.equal(env[EMBEDDINGS_KEY_ENV], 'fake-addon-key')
      assert.match(text, /type = "embedding"/)
      assert.equal(/floor = /.test(text), facts.embed.model === 'tiny-test-embedder', 'calibration only for a model without a gateway preset')
    }
  }
})

test('the managed client may explain recalls (the trial bench)', { skip }, async () => {
  const { text } = gatewayConfig({ port: 4321, dataDir: path.join(os.tmpdir(), 'dsh-memgate-explain'), settings: readConfig({}) })
  const all = text.split('\n')
  const rest = all.slice(all.indexOf('[clients.dsh]') + 1)
  const lines = rest.slice(0, rest.indexOf(''))
  assert.ok(lines.includes('explain = true'), text)
})

test('a hybrid store is written as a children array of inline tables, backslash paths included', { skip }, async () => {
  const dataDir = String.raw`D:\data\feiyu data\store`
  const settings = readConfig({ retrieval: { tier: 'endpoint', hybrid: true, url: 'http://127.0.0.1:9/v1', model: 'bge-m3', apiKey: 'fake-embeddings-key' } })
  const { text, store, env } = gatewayConfig({ port: 4321, dataDir, settings })
  const [keyword, vector] = store.table.children
  assert.match(text, /^type = "hybrid"$/m)
  // The exact line: keyword child first, then the vector table, key_env by name only.
  const line = `children = [{ type = "sqlite", path = ${tomlString(keyword.path)} }, { type = "embedding", path = ${tomlString(vector.path)}, url = "http://127.0.0.1:9/v1", model = "bge-m3", key_env = "${EMBEDDINGS_KEY_ENV}" }]`
  assert.ok(text.split('\n').includes(line), text)
  assert.ok(text.includes(String.raw`path = "D:\\data\\feiyu data\\store`), 'backslashes are doubled')
  assert.ok(!text.includes('fake-embeddings-key'))
  assert.equal(env[EMBEDDINGS_KEY_ENV], 'fake-embeddings-key')
  // Parsed back by a real TOML reader, the paths are the original ones.
  const parse = [
    'import json, sys, tomllib',
    'print(json.dumps(tomllib.loads(sys.stdin.buffer.read().decode("utf-8"))["stores"]["memories"]))',
  ].join('\n')
  const parsed = spawnSync(python.executable, ['-I', '-B', '-c', parse], { input: text, encoding: 'utf8', windowsHide: true })
  assert.equal(parsed.status, 0, parsed.stderr)
  const memories = JSON.parse(parsed.stdout)
  assert.equal(memories.type, 'hybrid')
  assert.deepEqual(memories.children.map((c) => c.type), ['sqlite', 'embedding'])
  assert.equal(memories.children[0].path, keyword.path)
  assert.equal(memories.children[1].path, vector.path)
  assert.equal(memories.children[1].key_env, EMBEDDINGS_KEY_ENV)
})

test('the linked stage follows newer_first (relative_score when that is off) and comes before fill_text; off, the list is the old one', () => {
  const stagesOf = (advanced) => {
    const { text } = gatewayConfig({ port: 4321, dataDir: path.join(os.tmpdir(), 'dsh-memgate-stage-list'), settings: readConfig({ advanced }) })
    const line = text.split('\n').find((l) => l.startsWith('stages = '))
    return JSON.parse(line.slice('stages = '.length))
  }
  // record_audience follows linked (or whatever came last before it) and is always there, before fill_text.
  const tail = ['record_audience', 'fill_text', 'drop_oversize', 'redact', 'dedupe', 'budget']
  assert.deepEqual(stagesOf({}), ['min_score', 'relative_score', 'newer_first', 'linked', ...tail])
  assert.deepEqual(stagesOf({ newerFirst: false }), ['min_score', 'relative_score', 'linked', ...tail])
  assert.deepEqual(stagesOf({ forgetting: true }), ['min_score', 'freshness', 'relative_score', 'newer_first', 'linked', ...tail])
  assert.deepEqual(stagesOf({ linkedRecall: false }), ['min_score', 'relative_score', 'newer_first', ...tail])
  assert.deepEqual(stagesOf({ linkedRecall: false, newerFirst: false }), ['min_score', 'relative_score', ...tail])
  assert.deepEqual(stagesOf({ redaction: false }), ['min_score', 'relative_score', 'newer_first', 'linked', 'record_audience', 'fill_text', 'drop_oversize', 'dedupe', 'budget'])
})

// A gateway process that never answers its health check.
function fakeSpawn(spawned) {
  return (command, args, options) => {
    const child = new EventEmitter()
    child.exitCode = null
    child.signalCode = null
    child.pid = 0
    child.stdin = Object.assign(new EventEmitter(), { end() {}, write() {}, unref() {} })
    child.stdout = Object.assign(new EventEmitter(), { unref() {} })
    child.stderr = Object.assign(new EventEmitter(), { unref() {} })
    child.unref = () => {}
    child.kill = () => {
      if (child.exitCode !== null) return
      child.exitCode = 0
      setImmediate(() => child.emit('exit', 0))
    }
    spawned.push({ command, args, options, child })
    return child
  }
}

// A manager over a fake interpreter and a fake process; the keyword index update is the test's own.
function hybridManager(t, dataDir, { retrieval = {}, mirrorImpl, spawned }) {
  const warnings = []
  const calls = []
  const settings = readConfig({
    retrieval: { tier: 'endpoint', hybrid: true, url: 'http://127.0.0.1:9/v1', model: 'bge-m3', ...retrieval },
    gateway: { dataDir },
  })
  const fake = { argv: ['python-for-tests'], executable: 'python-for-tests', version: '3.12.0', source: 'test' }
  const m = createGatewayManager({
    settings,
    env: ISOLATED_ENV,
    logger: { warn: (w) => warnings.push(w) },
    find: async () => fake,
    spawnImpl: fakeSpawn(spawned),
    mirrorImpl: (job) => {
      calls.push({ at: spawned.length, job })
      return mirrorImpl(job)
    },
    healthTimeoutMs: 50,
    maxRestarts: 0,
  })
  managersOf.get(t).push(m)
  return { m, warnings, calls }
}

const finished = (result) => ({ done: Promise.resolve(result), stop: async () => {} })

test('the keyword index is updated before the gateway starts, and only for a hybrid store', async (t) => {
  const dataDir = tempDir(t)
  const spawned = []
  const { m, calls, warnings } = hybridManager(t, dataDir, { spawned, mirrorImpl: () => finished({ state: 'done', added: 3, removed: 1, remaining: 0 }) })
  m.start()
  await m.ready()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].at, 0, 'no gateway process existed when the update started')
  assert.equal(spawned.length, 1, 'the gateway started after it')
  assert.deepEqual(calls[0].job.mirror, m.store().mirror)
  assert.equal(calls[0].job.dataDir, dataDir)
  assert.deepEqual(warnings.filter((w) => /keyword index/.test(w)), [])
  const log = fs.readFileSync(path.join(dataDir, 'gateway.log'), 'utf8')
  assert.match(log, /keyword index done: 3 added, 1 removed, 0 remaining/)
  assert.ok(log.indexOf('keyword index done') < log.indexOf('gateway starting'), 'logged before the gateway starts')

  for (const retrieval of [{ hybrid: false }, { tier: 'keyword' }]) {
    const other = tempDir(t)
    const otherSpawned = []
    const plain = hybridManager(t, other, { spawned: otherSpawned, retrieval, mirrorImpl: () => finished({ state: 'done' }) })
    plain.m.start()
    await plain.m.ready()
    assert.equal(plain.calls.length, 0, JSON.stringify(retrieval))
    assert.equal(otherSpawned.length, 1, JSON.stringify(retrieval))
  }
})

test('a failed or partial keyword index update still starts the gateway; the failure is warned about once', async (t) => {
  const dataDir = tempDir(t)
  const spawned = []
  const { m, warnings } = hybridManager(t, dataDir, { spawned, mirrorImpl: () => finished({ state: 'failed', kind: 'OperationalError' }) })
  m.start()
  await m.ready()
  assert.ok(spawned.length >= 1, 'the gateway started anyway')
  assert.equal(warnings.filter((w) => /keyword index could not be updated \(OperationalError\)/.test(w)).length, 1)
  assert.ok(!warnings.join('\n').includes(dataDir), 'no path in a warning')

  const partialDir = tempDir(t)
  const partialSpawned = []
  const partial = hybridManager(t, partialDir, { spawned: partialSpawned, mirrorImpl: () => finished({ state: 'partial', added: 10, removed: 0, remaining: 5 }) })
  partial.m.start()
  await partial.m.ready()
  assert.equal(partialSpawned.length, 1)
  assert.equal(partial.warnings.filter((w) => /keyword index/.test(w)).length, 0, 'partial is not a failure')
  assert.match(fs.readFileSync(path.join(partialDir, 'gateway.log'), 'utf8'), /keyword index partial: 10 added, 0 removed, 5 remaining/)

  // A helper that throws instead of answering is a failure too.
  const throwDir = tempDir(t)
  const throwSpawned = []
  const thrown = hybridManager(t, throwDir, { spawned: throwSpawned, mirrorImpl: () => { throw new Error('boom') } })
  thrown.m.start()
  await thrown.m.ready()
  assert.ok(throwSpawned.length >= 1)
  assert.equal(thrown.warnings.filter((w) => /keyword index could not be updated/.test(w)).length, 1)
})

test('stopping during the keyword index update ends it and spawns nothing', async (t) => {
  const dataDir = tempDir(t)
  const spawned = []
  let release
  let stopCalls = 0
  const done = new Promise((resolve) => { release = resolve })
  const { m, calls } = hybridManager(t, dataDir, {
    spawned,
    mirrorImpl: () => ({ done, stop: async () => { stopCalls += 1; release({ state: 'stopped' }) } }),
  })
  m.start()
  await waitFor(() => calls.length === 1, 5000, 'the update to start')
  await m.stop()
  await sleep(50)
  assert.equal(stopCalls, 1)
  assert.equal(spawned.length, 0)
  assert.equal(m.state(), 'stopped')
  assert.equal(fs.existsSync(m.configFile), false, 'the configuration file is removed')
})

// ------------------------------------------------------------------ lifecycle

test('managed start: healthy, remember and recall, token only in memory', { skip, timeout: 60000 }, async (t) => {
  const dataDir = tempDir(t)
  const { m, warnings } = manager(t, dataDir)
  m.start()
  assert.equal(await m.ready(), 'running')
  const { url, token } = m.endpoint()
  assert.match(url, /^http:\/\/127\.0\.0\.1:\d+$/)
  assert.match(token, /^[A-Za-z0-9_-]{43,}$/)

  // The configuration file is there while the gateway runs, without the token.
  const config = fs.readFileSync(m.configFile, 'utf8')
  assert.ok(!config.includes(token))
  assert.match(config, new RegExp(`token_env = "${TOKEN_ENV}"`))

  const client = createClient({ url, token, logger: { warn: (w) => warnings.push(w) } })
  assert.equal((await client.health()).status, 'ok')
  // The client speaks for the owner only (only_for): every write and recall names the account.
  assert.equal(await client.rememberStatus('The user keeps the spare key under the blue flowerpot', { speaker: 'dsh:local', origin: 'said' }), 'saved')
  const detail = await client.recallDetail('where is the spare key kept', { speaker: 'dsh:local' })
  assert.ok(detail?.results.some((r) => r.text.includes('blue flowerpot')), JSON.stringify(detail))

  await m.stop()
  assert.equal(m.state(), 'stopped')
  assert.ok(!fs.existsSync(m.configFile), 'the configuration file is removed on stop')
  const log = fs.readFileSync(path.join(dataDir, 'gateway.log'), 'utf8')
  assert.ok(log.length > 0)
  assert.ok(!log.includes(token), 'no token in the log')
  assert.ok(!log.includes('blue flowerpot'), 'no memory text in the log')
  assert.ok(fs.existsSync(path.join(dataDir, 'memories.db')), 'the store lives in the data folder')
  assert.ok(!fs.readdirSync(path.join(PACKAGE_DIR, 'feiyu_memgate')).includes('__pycache__'), 'no byte-code next to the bundled sources')
  assert.deepEqual(warnings.filter((w) => w.includes(token)), [])
})

test('a killed gateway is restarted with a new port and token', { skip, timeout: 60000 }, async (t) => {
  const dataDir = tempDir(t)
  const { m } = manager(t, dataDir, {}, { backoffMs: 50 })
  m.start()
  assert.equal(await m.ready(), 'running')
  const firstPid = m.pid()
  const first = m.endpoint()
  process.kill(firstPid)
  await waitFor(() => m.state() === 'running' && m.pid() !== firstPid, 30000, 'the restart')
  assert.notEqual(m.endpoint().token, first.token)
  assert.ok(!isAlive(firstPid))
  const client = createClient({ url: m.endpoint().url, token: m.endpoint().token })
  assert.equal((await client.health()).status, 'ok')
})

test('restarts stop after the cap and memory goes off with one warning', { skip, timeout: 60000 }, async (t) => {
  const dataDir = tempDir(t)
  // A package folder without the gateway: every start fails at once.
  const empty = path.join(dataDir, 'no-package')
  fs.mkdirSync(empty)
  const { m, warnings } = manager(t, dataDir, {}, { backoffMs: 20, maxRestarts: 2, packageDir: empty })
  m.start()
  assert.equal(await m.ready(), 'failed')
  assert.equal(warnings.length, 1)
  assert.match(warnings[0], /keeps stopping/)
  assert.equal(m.pid(), null)
})

test('stop leaves no process behind', { skip, timeout: 60000 }, async (t) => {
  const dataDir = tempDir(t)
  const { m } = manager(t, dataDir)
  m.start()
  assert.equal(await m.ready(), 'running')
  const pid = m.pid()
  assert.ok(isAlive(pid))
  await m.stop()
  await waitFor(() => !isAlive(pid), 5000, 'the gateway process to end')
})

test('a host that dies without cleanup does not leave the gateway running', { skip, timeout: 60000 }, async (t) => {
  const dataDir = tempDir(t)
  const host = spawn(process.execPath, [path.join(HERE, 'helpers', 'orphan-host.mjs'), dataDir, python.executable], {
    env: ISOLATED_ENV,
    stdio: ['ignore', 'pipe', 'inherit'],
    windowsHide: true,
  })
  managersOf.get(t).push({ stop: async () => host.kill('SIGKILL') })
  let line = ''
  for await (const chunk of host.stdout) {
    line += chunk
    if (line.includes('\n')) break
  }
  const { state, pid } = JSON.parse(line)
  assert.equal(state, 'running')
  assert.ok(isAlive(pid))
  host.kill('SIGKILL') // no exit handler runs: only the gateway's own watchdog can end it
  await waitFor(() => !isAlive(pid), 15000, 'the orphaned gateway to exit')
})

test('two managers on one data folder at once share the store', { skip, timeout: 60000 }, async (t) => {
  const dataDir = tempDir(t)
  const a = manager(t, dataDir).m
  const b = manager(t, dataDir).m
  a.start()
  b.start()
  assert.deepEqual(await Promise.all([a.ready(), b.ready()]), ['running', 'running'])
  assert.notEqual(a.configFile, b.configFile)
  assert.notEqual(a.endpoint().url, b.endpoint().url)
  const writer = createClient(a.endpoint())
  const reader = createClient(b.endpoint())
  const speaker = { speaker: 'dsh:local' }
  assert.equal(await writer.rememberStatus('The user parks the bicycle in the courtyard shed', speaker), 'saved')
  const detail = await reader.recallDetail('where is the bicycle parked', speaker)
  assert.ok(detail?.results.some((r) => r.text.includes('courtyard shed')), JSON.stringify(detail))
  assert.equal(await reader.rememberStatus('The user waters the plants on Sundays', speaker), 'saved')
  await Promise.all([a.stop(), b.stop()])
})

test('the bootstrap exits when its stdin closes', { skip, timeout: 30000 }, async (t) => {
  const dataDir = tempDir(t)
  const settings = readConfig({})
  const file = path.join(dataDir, 'gateway.toml')
  fs.writeFileSync(file, gatewayConfig({ port: await freePort(), dataDir, settings }).text)
  const child = spawn(python.executable, ['-I', '-B', '-c', BOOTSTRAP, 'serve', '--config', file], {
    env: { ...scrubbedEnv(), [TOKEN_ENV]: 'bootstrap-test-token-' + 'q'.repeat(32), [IDENTITY_KEY_ENV]: TEST_IDENTITY_KEY, FEIYU_MEMGATE_PACKAGE_DIR: PACKAGE_DIR },
    stdio: ['pipe', 'ignore', 'ignore'],
    windowsHide: true,
  })
  managersOf.get(t).push({ stop: async () => child.kill() })
  await sleep(500)
  const exited = new Promise((resolve) => child.once('exit', resolve))
  child.stdin.end()
  const code = await Promise.race([exited, sleep(10000).then(() => 'timeout')])
  assert.equal(code, 0)
})

// ------------------------------------------------------------------ who speaks: the identity table and its key

// The lines of one table of the generated configuration (up to the blank line that ends it).
function sectionOf(text, name) {
  const all = text.split('\n')
  const start = all.indexOf(`[${name}]`)
  assert.ok(start >= 0, `[${name}] is in the configuration`)
  const rest = all.slice(start + 1)
  return rest.slice(0, rest.indexOf(''))
}

const configFor = (advanced = {}) => gatewayConfig({ port: 4321, dataDir: path.join(os.tmpdir(), 'dsh-memgate-identity'), settings: readConfig({ advanced }) }).text

test('the configuration has an identity table with one person, the owner, and the client reports the dsh platform for the owner only', () => {
  const text = configFor()
  assert.deepEqual(sectionOf(text, 'identity'), ['type = "table"', `hash_key_env = "${IDENTITY_KEY_ENV}"`])
  assert.deepEqual(sectionOf(text, 'identity.people.owner'), ['owner = true', 'accounts = ["dsh:local"]'])
  const client = sectionOf(text, 'clients.dsh')
  assert.ok(client.includes('platforms = ["dsh"]'), client.join('\n'))
  assert.ok(client.includes('only_for = ["owner"]'), client.join('\n'))
  assert.ok(client.includes('explain = true') && client.includes(`token_env = "${TOKEN_ENV}"`), 'what the client had before is still there')
  assert.equal(IDENTITY_KEY_ENV, 'FEIYU_MEMGATE_IDENTITY_KEY')
  // The key is named by its variable only: no key-like run of characters anywhere in the file.
  assert.ok(!/[0-9a-fA-F]{32}/.test(text), 'no key in the configuration')
  assert.ok(!text.includes('hash_key ='), 'the gateway refuses a key written in the file')
})

test('the account of the configuration is advanced.speakerAccount, and dsh:local when that is not a dsh:name', () => {
  assert.deepEqual(sectionOf(configFor({ speakerAccount: 'dsh:desk-2' }), 'identity.people.owner'), ['owner = true', 'accounts = ["dsh:desk-2"]'])
  for (const speakerAccount of ['', 'desk-2', 'aiocqhttp:10001', 'DSH:Desk', 'dsh:', 'dsh:a b', `dsh:${'a'.repeat(33)}`]) {
    assert.deepEqual(sectionOf(configFor({ speakerAccount }), 'identity.people.owner'), ['owner = true', 'accounts = ["dsh:local"]'], JSON.stringify(speakerAccount))
  }
})

test('record_audience is in the recall stages in every combination of the switches: right after linked (else newer_first, else relative_score), before fill_text', () => {
  const stagesOf = (advanced) => JSON.parse(configFor(advanced).split('\n').find((l) => l.startsWith('stages = ')).slice('stages = '.length))
  let combinations = 0
  for (const forgetting of [false, true]) {
    for (const newerFirst of [false, true]) {
      for (const linkedRecall of [false, true]) {
        for (const redaction of [false, true]) {
          const stages = stagesOf({ forgetting, newerFirst, linkedRecall, redaction })
          const at = stages.indexOf('record_audience')
          const label = JSON.stringify({ forgetting, newerFirst, linkedRecall, redaction })
          assert.equal(stages.filter((s) => s === 'record_audience').length, 1, label)
          assert.equal(stages[at - 1], linkedRecall ? 'linked' : newerFirst ? 'newer_first' : 'relative_score', label)
          assert.equal(stages[at + 1], 'fill_text', label)
          combinations += 1
        }
      }
    }
  }
  assert.equal(combinations, 16)
})

test('loadIdentityKey: created as 64 hexadecimal characters with the owner mode, then read again and again', (t) => {
  const dir = tempDir(t)
  const warned = []
  const key = loadIdentityKey(dir, (kind) => warned.push(kind))
  assert.match(key, /^[0-9a-f]{64}$/)
  const file = path.join(dir, IDENTITY_KEY_FILE)
  assert.equal(fs.readFileSync(file, 'utf8'), key)
  if (process.platform !== 'win32') assert.equal(fs.statSync(file).mode & 0o777, 0o600)
  assert.equal(loadIdentityKey(dir, (kind) => warned.push(kind)), key, 'the same key at the next start')
  assert.equal(loadIdentityKey(dir, (kind) => warned.push(kind)), key)
  assert.deepEqual(warned, [], 'nothing to warn about')
  // A line break after the key (an editor added it) does no harm.
  fs.writeFileSync(file, `${key}\n`)
  assert.equal(loadIdentityKey(dir, (kind) => warned.push(kind)), key)
  // Capital letters are hexadecimal too.
  fs.writeFileSync(file, 'AB'.repeat(32))
  assert.equal(loadIdentityKey(dir, (kind) => warned.push(kind)), 'AB'.repeat(32))
  assert.deepEqual(warned, [])
  // Another folder, another key.
  assert.notEqual(loadIdentityKey(tempDir(t)), key)
})

test('loadIdentityKey: a file that is not 64 hexadecimal characters is replaced, with one warning that names the kind only', (t) => {
  const planted = 'zz'.repeat(32) // looks like a key, is not hexadecimal
  for (const content of ['', 'short', 'ab'.repeat(31), 'AB'.repeat(31) + 'xy', planted, `${'ab'.repeat(32)}00`]) {
    const dir = tempDir(t)
    const file = path.join(dir, IDENTITY_KEY_FILE)
    fs.writeFileSync(file, content)
    const warned = []
    const key = loadIdentityKey(dir, (kind) => warned.push(kind))
    assert.match(key, /^[0-9a-f]{64}$/, JSON.stringify(content))
    assert.notEqual(key, content)
    assert.deepEqual(warned, ['malformed'], JSON.stringify(content))
    assert.equal(fs.readFileSync(file, 'utf8'), key, 'the new key is in the file')
    assert.equal(loadIdentityKey(dir, (kind) => warned.push(kind)), key, 'and kept from then on')
    assert.deepEqual(warned, ['malformed'])
  }
  // A file that cannot be read at all (a folder is there): another kind, the key lives on in memory.
  const dir = tempDir(t)
  fs.mkdirSync(path.join(dir, IDENTITY_KEY_FILE))
  const warned = []
  const key = loadIdentityKey(dir, (kind) => warned.push(kind))
  assert.match(key, /^[0-9a-f]{64}$/)
  assert.deepEqual(warned, ['unreadable'])
  // A folder that is not there: nothing can be written, one warning, a key for this start.
  const gone = []
  assert.match(loadIdentityKey(path.join(dir, 'no', 'such', 'folder'), (kind) => gone.push(kind)), /^[0-9a-f]{64}$/)
  assert.deepEqual(gone, ['unwritable'])
})

// A manager over a fake interpreter and a fake gateway process, which also keeps what the configuration file said at the start.
function keyManager(t, dataDir, { spawned, texts }) {
  const warnings = []
  const fake = { argv: ['python-for-tests'], executable: 'python-for-tests', version: '3.12.0', source: 'test' }
  const base = fakeSpawn(spawned)
  const m = createGatewayManager({
    settings: readConfig({ gateway: { dataDir } }),
    env: { ...ISOLATED_ENV, FEIYU_MEMGATE_IDENTITY_KEY: 'from-the-parent-environment', SOME_API_KEY: 'unrelated-secret' },
    logger: { warn: (w) => warnings.push(w) },
    find: async () => fake,
    spawnImpl: (command, args, options) => {
      texts.push(fs.readFileSync(args[args.indexOf('--config') + 1], 'utf8'))
      return base(command, args, options)
    },
    healthTimeoutMs: 50,
    maxRestarts: 0,
  })
  managersOf.get(t).push(m)
  return { m, warnings }
}

test('the manager hands the key of the file to the gateway in its environment, and nowhere else', async (t) => {
  const dataDir = tempDir(t)
  const planted = 'c0ffee'.repeat(10) + 'abcd' // 64 hexadecimal characters
  assert.match(planted, /^[0-9a-f]{64}$/)
  fs.writeFileSync(path.join(dataDir, IDENTITY_KEY_FILE), planted)
  const spawned = []
  const texts = []
  const { m, warnings } = keyManager(t, dataDir, { spawned, texts })
  m.start()
  await m.ready()
  assert.ok(spawned.length >= 1)
  const { env } = spawned[0].options
  assert.equal(env[IDENTITY_KEY_ENV], planted, 'the key of the file, not the one of the parent environment')
  assert.equal(env.SOME_API_KEY, undefined, 'the scrubbed environment still drops every other KEY variable')
  assert.equal(env[TOKEN_ENV].length >= 43, true)
  assert.equal(scrubbedEnv({ [IDENTITY_KEY_ENV]: planted, PATH: 'x' })[IDENTITY_KEY_ENV], undefined, 'scrubbedEnv itself removes it: the manager adds it on purpose')
  // Not in the configuration file, not in the log, not in a warning, not in what the manager shows.
  for (const text of texts) assert.ok(!text.includes(planted), 'not in the configuration')
  assert.ok(!fs.readFileSync(path.join(dataDir, 'gateway.log'), 'utf8').includes(planted), 'not in the log')
  assert.deepEqual(warnings.filter((w) => w.includes(planted)), [])
  assert.ok(!JSON.stringify({ endpoint: m.endpoint(), state: m.state(), pid: m.pid(), store: m.store(), python: m.python() }).includes(planted))
  assert.equal(fs.readFileSync(path.join(dataDir, IDENTITY_KEY_FILE), 'utf8'), planted, 'the file was only read')
})

test('the manager creates the key file when it is missing and replaces a broken one with one warning that does not show it', async (t) => {
  const created = tempDir(t)
  const first = { spawned: [], texts: [] }
  const a = keyManager(t, created, first)
  a.m.start()
  await a.m.ready()
  const key = fs.readFileSync(path.join(created, IDENTITY_KEY_FILE), 'utf8')
  assert.match(key, /^[0-9a-f]{64}$/)
  assert.equal(first.spawned[0].options.env[IDENTITY_KEY_ENV], key)
  assert.deepEqual(a.warnings.filter((w) => /identity key/.test(w)), [])

  const broken = tempDir(t)
  const bad = 'not a key but secret-looking text'
  fs.writeFileSync(path.join(broken, IDENTITY_KEY_FILE), bad)
  const second = { spawned: [], texts: [] }
  const b = keyManager(t, broken, second)
  b.m.start()
  await b.m.ready()
  const replaced = fs.readFileSync(path.join(broken, IDENTITY_KEY_FILE), 'utf8')
  assert.match(replaced, /^[0-9a-f]{64}$/)
  assert.equal(second.spawned[0].options.env[IDENTITY_KEY_ENV], replaced)
  const notes = b.warnings.filter((w) => /identity key/.test(w))
  assert.equal(notes.length, 1, 'one warning')
  assert.match(notes[0], /\(malformed\)/)
  assert.ok(!notes[0].includes(bad) && !notes[0].includes(replaced), 'the kind only')
  assert.ok(!b.warnings.join('\n').includes(replaced))
})

const STORED = [
  'import json, sqlite3, sys',
  'db = sqlite3.connect(sys.argv[1], timeout=10)',
  'rows = db.execute("SELECT text, speaker, about, channel, audience, origin, event_date, kind FROM memories ORDER BY text").fetchall()',
  'print(json.dumps(rows))',
  'db.close()',
].join('\n')

test('the real gateway takes the generated configuration: a write and a recall for the account work, the record keeps the owner and the origin', { skip, timeout: 60000 }, async (t) => {
  const dataDir = tempDir(t)
  const planted = 'fe'.repeat(32)
  fs.writeFileSync(path.join(dataDir, IDENTITY_KEY_FILE), planted)
  const { m, warnings } = manager(t, dataDir)
  m.start()
  assert.equal(await m.ready(), 'running')
  const { url, token } = m.endpoint()
  const client = createClient({ url, token, logger: { warn: (w) => warnings.push(w) } })
  const text = 'The user keeps the spare key under the blue flowerpot'
  // The client speaks for the owner only: a write or a recall without the account, or with another, is refused.
  assert.equal(await client.rememberStatus(text), 'failed', 'no speaker')
  assert.equal(await client.rememberStatus(text, { speaker: 'dsh:stranger' }), 'failed', 'an account nobody listed')
  assert.equal(await client.rememberStatus(text, { speaker: 'aiocqhttp:10001' }), 'failed', 'a platform the client was not given')
  assert.equal(await client.recallDetail('where is the spare key kept'), null, 'a recall without the account')
  assert.equal(await client.recallDetail('where is the spare key kept', { speaker: 'dsh:stranger' }), null)
  // The account the plugin reports.
  assert.equal(await client.rememberStatus(text, { speaker: 'dsh:local', origin: 'tool', kind: 'event', eventDate: '2026-10-31' }), 'saved')
  assert.equal(await client.rememberStatus('The user drinks oolong tea every morning', { speaker: 'dsh:local', origin: 'said' }), 'saved')
  const detail = await client.recallDetail('where is the spare key kept', { speaker: 'dsh:local' })
  assert.ok(detail?.results.some((r) => r.text.includes('blue flowerpot')), JSON.stringify(detail))
  // The explained recall shows what each record carries.
  const explained = await client.explainRecall('where is the spare key kept', { speaker: 'dsh:local' })
  assert.equal(explained.ok, true, JSON.stringify(explained))
  const row = explained.candidates.find((c) => c.text?.includes('blue flowerpot'))
  assert.ok(row, JSON.stringify(explained.candidates))
  assert.deepEqual([row.fields.speaker, row.fields.origin, row.fields.kind, row.fields.event_date], ['owner', 'tool', 'event', '2026-10-31'])
  assert.equal(row.fields.audience, 'owner')
  assert.ok(explained.stages.includes('record_audience'), explained.stages.join())
  // The stored records: the speaker is the owner's name, the account itself is not kept.
  const rows = JSON.parse(spawnSync(python.executable, ['-I', '-B', '-c', STORED, path.join(dataDir, 'memories.db')], { encoding: 'utf8', windowsHide: true }).stdout)
  assert.equal(rows.length, 2)
  assert.deepEqual(rows[1].slice(0, 2), ['The user keeps the spare key under the blue flowerpot', 'owner'])
  assert.deepEqual([rows[1][5], rows[1][6], rows[1][7]], ['tool', '2026-10-31', 'event'])
  assert.deepEqual([rows[0][1], rows[0][5]], ['owner', 'said'])
  assert.ok(!JSON.stringify(rows).includes('dsh:local'), 'the account is not stored')
  await m.stop()
  // The key stays out of the log and the warnings, and the file is the one that was planted.
  assert.ok(!fs.readFileSync(path.join(dataDir, 'gateway.log'), 'utf8').includes(planted))
  assert.deepEqual(warnings.filter((w) => w.includes(planted)), [])
  assert.equal(fs.readFileSync(path.join(dataDir, IDENTITY_KEY_FILE), 'utf8'), planted)
})
