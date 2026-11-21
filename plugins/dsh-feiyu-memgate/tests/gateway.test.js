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
  BOOTSTRAP, PACKAGE_DIR, TOKEN_ENV, createGatewayManager, findPython, freePort, gatewayConfig, pythonCandidates, scrubbedEnv, tomlString,
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

// `feiyu-memgate check` on a configuration file: 0 when the gateway accepts it.
function check(file, env = {}) {
  const code = `import os, sys; sys.path.insert(0, os.environ["FEIYU_MEMGATE_PACKAGE_DIR"]); from feiyu_memgate.cli import main; sys.exit(main())`
  // -B: no byte-code caches next to the bundled sources (the package copy must stay exact).
  return spawnSync(python.executable, ['-I', '-B', '-c', code, 'check', '--config', file], {
    env: { ...scrubbedEnv(), FEIYU_MEMGATE_PACKAGE_DIR: PACKAGE_DIR, PYTHONIOENCODING: 'utf-8', ...env },
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
  const tail = ['fill_text', 'drop_oversize', 'redact', 'dedupe', 'budget']
  assert.deepEqual(stagesOf({}), ['min_score', 'relative_score', 'newer_first', 'linked', ...tail])
  assert.deepEqual(stagesOf({ newerFirst: false }), ['min_score', 'relative_score', 'linked', ...tail])
  assert.deepEqual(stagesOf({ forgetting: true }), ['min_score', 'freshness', 'relative_score', 'newer_first', 'linked', ...tail])
  assert.deepEqual(stagesOf({ linkedRecall: false }), ['min_score', 'relative_score', 'newer_first', ...tail])
  assert.deepEqual(stagesOf({ linkedRecall: false, newerFirst: false }), ['min_score', 'relative_score', ...tail])
  assert.deepEqual(stagesOf({ redaction: false }), ['min_score', 'relative_score', 'newer_first', 'linked', 'fill_text', 'drop_oversize', 'dedupe', 'budget'])
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
  assert.equal(await client.rememberStatus('The user keeps the spare key under the blue flowerpot'), 'saved')
  const detail = await client.recallDetail('where is the spare key kept')
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
  assert.equal(await writer.rememberStatus('The user parks the bicycle in the courtyard shed'), 'saved')
  const detail = await reader.recallDetail('where is the bicycle parked')
  assert.ok(detail?.results.some((r) => r.text.includes('courtyard shed')), JSON.stringify(detail))
  assert.equal(await reader.rememberStatus('The user waters the plants on Sundays'), 'saved')
  await Promise.all([a.stop(), b.stop()])
})

test('the bootstrap exits when its stdin closes', { skip, timeout: 30000 }, async (t) => {
  const dataDir = tempDir(t)
  const settings = readConfig({})
  const file = path.join(dataDir, 'gateway.toml')
  fs.writeFileSync(file, gatewayConfig({ port: await freePort(), dataDir, settings }).text)
  const child = spawn(python.executable, ['-I', '-B', '-c', BOOTSTRAP, 'serve', '--config', file], {
    env: { ...scrubbedEnv(), [TOKEN_ENV]: 'bootstrap-test-token-' + 'q'.repeat(32), FEIYU_MEMGATE_PACKAGE_DIR: PACKAGE_DIR },
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
