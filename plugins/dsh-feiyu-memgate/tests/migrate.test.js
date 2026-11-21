// Copying the keyword store into a vector store, with the Python found on this machine, the bundled
// gateway's real `sqlite` and `embedding` backends, and a tiny embeddings server written here with
// node:http. The target store is the one the local tier builds from a fake add-on service.
import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { createClient } from '../src/client.js'
import { PACKAGE_DIR, findPython, runMirror, scrubbedEnv } from '../src/gateway.js'
import { migrationStateFile, readMigrationState, runMigration } from '../src/migrate.js'
import { applyPlugin } from '../src/plugin.js'
import { EMBED_SERVICE, TIERS, readEmbed, resolveStore } from '../src/tiers.js'
import { createFakeHost } from './helpers/fake-host.js'

// Helper processes are unref'd on purpose (they must never keep a host alive). In a test file nothing else
// holds the event loop open while one is awaited, and Node 22 then ends the file with "Promise resolution
// is still pending" (Node 24 waits). This timer stands in for the host.
const keepAlive = setInterval(() => {}, 60000)
after(() => clearInterval(keepAlive))

const ISOLATED_ENV = { ...process.env, DSH_HOME: path.join(os.tmpdir(), 'dsh-memgate-no-home'), DSH_PRIMARY_RUNTIME: '' }
const python = await findPython({ cache: null, env: ISOLATED_ENV })
const skip = python ? false : 'no Python 3.11 or newer on PATH'
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const ADDON_KEY = 'fake-addon-key-for-tests'
const MODEL = 'tiny-test-embedder'

const MEMORIES = [
  ['w-aaaa-0', 'The user keeps the spare key under the blue flowerpot'],
  ['w-aaaa-1', 'The user drinks oolong tea every morning'],
  ['w-bbbb-0', '用户周末去西湖边跑步'],
  ['w-cccc-0', 'The user parks the bicycle in the courtyard shed'],
  ['w-dddd-0', 'The user waters the plants on Sundays'],
]

// A deterministic, never-zero vector per text: the same text always gets the same direction.
function vector(text) {
  const v = Array.from({ length: 8 }, () => 0.05)
  for (const ch of text) v[ch.codePointAt(0) % 8] += 1
  return v
}

async function embeddingsServer() {
  const calls = { requests: 0, texts: 0, auth: new Set(), models: new Set() }
  // maxChars: refuse a request holding a longer text with 413, as the add-on refuses input above
  // its model's maxInputTokens.
  const control = { failAfter: Infinity, delayMs: 0, maxChars: Infinity }
  const server = http.createServer((req, res) => {
    let body = ''
    req.setEncoding('utf8')
    req.on('data', (chunk) => { body += chunk })
    req.on('end', () => {
      calls.requests += 1
      calls.auth.add(req.headers.authorization ?? '')
      if (req.method !== 'POST' || req.url !== '/v1/embeddings' || calls.requests > control.failAfter) {
        res.writeHead(503)
        res.end()
        return
      }
      const { model, input } = JSON.parse(body)
      if (input.some((text) => text.length > control.maxChars)) {
        res.writeHead(413, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ error: { message: 'input too long', type: 'invalid_request_error', code: 'context_length_exceeded' } }))
        return
      }
      calls.models.add(model)
      calls.texts += input.length
      const reply = () => {
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ object: 'list', model, data: input.map((text, index) => ({ object: 'embedding', index, embedding: vector(text) })) }))
      }
      // A delayed answer must not keep the test process alive once the server is closed.
      if (control.delayMs) setTimeout(reply, control.delayMs).unref()
      else reply()
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  return {
    url: `http://127.0.0.1:${server.address().port}/v1`,
    calls,
    control,
    close: () => new Promise((resolve) => {
      server.closeAllConnections?.()
      server.close(() => resolve())
    }),
  }
}

// The add-on's service value, version 1 of the contract.
const addonService = (url) => ({
  version: 1,
  state: 'ready',
  url,
  key: ADDON_KEY,
  model: MODEL,
  dimensions: 8,
  calibration: { preset: '', parity: 'approximate', floor: 0, ceil: 1, query_prefix: '', document_prefix: '' },
  fingerprint: `${MODEL}|f32|00112233aabb`,
  models: [{ id: MODEL, installed: true, active: true, sizeBytes: 1024, license: 'MIT' }],
})

function py(code, args, input) {
  const result = spawnSync(python.executable, ['-I', '-B', '-c', code, ...args], {
    env: { ...scrubbedEnv(ISOLATED_ENV), PYTHONIOENCODING: 'utf-8' }, input, encoding: 'utf8', windowsHide: true, timeout: 30000,
  })
  assert.equal(result.status, 0, result.stderr)
  return result.stdout
}

function seedKeyword(file, rows) {
  py([
    'import json, sys',
    'sys.path.insert(0, sys.argv[1])',
    'from feiyu_memgate.backends.sqlite import SQLiteBackend',
    'b = SQLiteBackend(sys.argv[2])',
    // -I ignores PYTHONIOENCODING: read bytes and decode them here.
    'for ref, text in json.loads(sys.stdin.buffer.read().decode("utf-8")):',
    '    b.put(text, ref, "2026-11-01")',
    'b.close()',
  ].join('\n'), [PACKAGE_DIR, file], JSON.stringify(rows))
}

function storeRows(file) {
  return JSON.parse(py('import json, sqlite3, sys\ndb = sqlite3.connect(sys.argv[1])\nprint(json.dumps(sorted(db.execute("SELECT ref, text FROM memories").fetchall())))\ndb.close()', [file]))
}

function deleteRow(file, ref) {
  py('import sqlite3, sys\ndb = sqlite3.connect(sys.argv[1])\ndb.execute("DELETE FROM memories WHERE ref = ?", (sys.argv[2],))\ndb.commit()\ndb.close()', [file, ref])
}

// A data folder with a seeded keyword store, an embeddings server, and the local tier's target store.
async function fixture(t, rows = MEMORIES) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-memgate-mig-'))
  const server = await embeddingsServer()
  const cleanups = []
  t.after(async () => {
    for (const fn of cleanups.reverse()) await fn()
    await server.close()
    fs.rmSync(dataDir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  })
  const source = TIERS.keyword.store(null, dataDir).table.path
  if (rows.length) seedKeyword(source, rows)
  const embed = readEmbed(addonService(server.url))
  const target = resolveStore({ tier: 'local', url: '', model: '', apiKey: '' }, dataDir, { embed })
  const copy = (options = {}) => runMigration({ python, source, target, dataDir, env: ISOLATED_ENV, ...options })
  return { dataDir, server, source, target, copy, cleanups }
}

test('copies every keyword memory into the local store once, with the add-on key and model', { skip, timeout: 60000 }, async (t) => {
  const { server, target, copy, dataDir } = await fixture(t)
  const progress = []
  const result = await copy({ onProgress: (s) => progress.push(s) }).done
  assert.deepEqual(result, { state: 'done', total: 5, copied: 5, refused: 0, remaining:0 })
  assert.deepEqual(storeRows(target.table.path), [...MEMORIES].sort())
  assert.deepEqual([...server.calls.auth], [`Bearer ${ADDON_KEY}`])
  assert.deepEqual([...server.calls.models], [MODEL])
  assert.ok(progress.some((p) => p.state === 'running'))
  assert.deepEqual(readMigrationState(migrationStateFile(dataDir, target.id)), { copied: 5, refused: 0, done: true, updated: readMigrationState(migrationStateFile(dataDir, target.id)).updated })
  const stateText = fs.readFileSync(migrationStateFile(dataDir, target.id), 'utf8')
  for (const [, text] of MEMORIES) assert.ok(!stateText.includes(text), 'the state file holds refs, never texts')

  const texts = server.calls.texts
  assert.deepEqual(await copy().done, { state: 'done', total: 5, copied: 5, refused: 0, remaining:0 })
  assert.equal(server.calls.texts, texts, 'a second run embeds nothing')
})

test('bounded steps resume where the last one stopped', { skip, timeout: 60000 }, async (t) => {
  const { target, copy } = await fixture(t)
  assert.deepEqual(await copy({ maxItems: 2, batch: 1 }).done, { state: 'partial', total: 5, copied: 2, refused: 0, remaining:3 })
  assert.equal(storeRows(target.table.path).length, 2)
  assert.deepEqual(await copy({ maxItems: 2, batch: 2 }).done, { state: 'partial', total: 5, copied: 4, refused: 0, remaining:1 })
  assert.deepEqual(await copy({ maxItems: 2 }).done, { state: 'done', total: 5, copied: 5, refused: 0, remaining:0 })
  assert.equal(storeRows(target.table.path).length, 5)
})

test('a memory removed from the vector store stays removed; a new keyword memory is copied', { skip, timeout: 60000 }, async (t) => {
  const { target, copy, source } = await fixture(t)
  await copy().done
  deleteRow(target.table.path, 'w-aaaa-1') // forgotten or deleted under the local tier
  seedKeyword(source, [['w-eeee-0', 'The user collects old maps of Hangzhou']])
  assert.deepEqual(await copy().done, { state: 'done', total: 6, copied: 6, refused: 0, remaining:0 })
  const refs = storeRows(target.table.path).map(([ref]) => ref)
  assert.ok(!refs.includes('w-aaaa-1'), 'not resurrected')
  assert.ok(refs.includes('w-eeee-0'))
  assert.equal(refs.length, 5)
})

test('a failing endpoint keeps what was copied, reports the kind, and the next run finishes', { skip, timeout: 60000 }, async (t) => {
  const { server, target, copy } = await fixture(t)
  server.control.failAfter = 1
  const failed = await copy({ batch: 2 }).done
  assert.equal(failed.state, 'failed')
  assert.equal(failed.kind, 'BackendError')
  assert.equal(failed.copied, 2)
  assert.equal(storeRows(target.table.path).length, 2)
  server.control.failAfter = Infinity
  assert.deepEqual(await copy({ batch: 2 }).done, { state: 'done', total: 5, copied: 5, refused: 0, remaining:0 })
})

test('a text the endpoint refuses (413) is recorded once and does not block the others', { skip, timeout: 60000 }, async (t) => {
  const long = 'The user wrote a very long note about the garden. '.repeat(20)
  const { server, target, copy, dataDir } = await fixture(t, [...MEMORIES.slice(0, 2), ['w-long-0', long], ...MEMORIES.slice(2)])
  server.control.maxChars = 200
  assert.deepEqual(await copy({ batch: 4 }).done, { state: 'done', total: 6, copied: 5, refused: 1, remaining: 0 })
  assert.deepEqual(storeRows(target.table.path), [...MEMORIES].sort())
  const state = readMigrationState(migrationStateFile(dataDir, target.id))
  assert.equal(state.refused, 1)
  assert.equal(state.done, true)
  assert.ok(!fs.readFileSync(migrationStateFile(dataDir, target.id), 'utf8').includes('garden'), 'refs only, never texts')

  const requests = server.calls.requests
  assert.deepEqual(await copy({ batch: 4 }).done, { state: 'done', total: 6, copied: 5, refused: 1, remaining: 0 })
  assert.equal(server.calls.requests, requests, 'the refused text is not offered to this store again')
})

test('nothing to copy: no keyword store yet', { skip, timeout: 60000 }, async (t) => {
  const { copy, server } = await fixture(t, [])
  assert.deepEqual(await copy().done, { state: 'done', total: 0, copied: 0, refused: 0, remaining:0 })
  assert.equal(server.calls.requests, 0)
})

test('stop ends a copy that is waiting on the endpoint', { skip, timeout: 60000 }, async (t) => {
  const { server, copy } = await fixture(t)
  server.control.delayMs = 20000
  let started = false
  const run = copy({ onProgress: (s) => { if (s.state === 'running') started = true } })
  const deadline = Date.now() + 15000
  while (!(started && server.calls.requests > 0) && Date.now() < deadline) await sleep(50)
  assert.ok(started && server.calls.requests > 0, 'the copy reached the endpoint')
  const begun = Date.now()
  await run.stop()
  const result = await run.done
  assert.equal(result.state, 'stopped')
  assert.ok(Date.now() - begun < 8000, 'stopped promptly')
})

test('the plugin with a fake add-on and the real gateway: auto runs local, copies, and recalls a copied memory', { skip, timeout: 90000 }, async (t) => {
  const { dataDir, server, cleanups } = await fixture(t)
  const host = createFakeHost()
  host.provide(EMBED_SERVICE, addonService(server.url))
  const plugin = applyPlugin(host.ctx, { gateway: { python: python.executable, dataDir }, advanced: { language: 'en' } }, {
    env: ISOLATED_ENV, importHost: false, embedPollMs: 0, migration: { pauseMs: 10 },
  })
  cleanups.push(() => host.unload())
  const deadline = Date.now() + 60000
  while (plugin.migration()?.state !== 'done' && Date.now() < deadline) {
    assert.notEqual(plugin.migration()?.state, 'failed', JSON.stringify(plugin.migration()))
    await sleep(100)
  }
  assert.equal(plugin.migration()?.state, 'done', JSON.stringify(plugin.migration()))
  const manager = plugin.gateway()
  assert.equal(manager.state(), 'running')
  assert.equal(manager.store().tier, 'local')
  const status = await plugin.status()
  assert.equal(status.retrieval.active, 'local')
  assert.equal(status.store.type, 'embedding')
  assert.equal(status.store.count, 5)
  assert.ok(!JSON.stringify(status).includes(ADDON_KEY))
  const client = createClient(manager.endpoint())
  const detail = await client.recallDetail('The user parks the bicycle in the courtyard shed')
  assert.ok(detail?.results.some((r) => r.text.includes('courtyard shed')), JSON.stringify(detail))
  await host.unload()
  assert.equal(manager.state(), 'stopped')
  const config = fs.readdirSync(dataDir).filter((f) => f.endsWith('.toml'))
  assert.deepEqual(config, [], 'no configuration file left behind')
  const log = fs.readFileSync(path.join(dataDir, 'gateway.log'), 'utf8')
  assert.ok(!log.includes(ADDON_KEY) && !log.includes('courtyard shed'), 'no key or memory in the log')
})

test('a hybrid target is filled through both children under one ref, with the same state file as the plain tier', { skip, timeout: 60000 }, async (t) => {
  const { server, source, target: plain, dataDir } = await fixture(t)
  const embed = readEmbed(addonService(server.url))
  const hybrid = resolveStore({ tier: 'local', hybrid: true, url: '', model: '', apiKey: '' }, dataDir, { embed })
  assert.equal(hybrid.hybrid, true)
  assert.equal(hybrid.id, plain.id)
  assert.equal(migrationStateFile(dataDir, hybrid.id), migrationStateFile(dataDir, plain.id), 'switching hybrid on does not start the copy over')
  const result = await runMigration({ python, source, target: hybrid, dataDir, env: ISOLATED_ENV }).done
  assert.deepEqual(result, { state: 'done', total: 5, copied: 5, refused: 0, remaining: 0 })
  assert.deepEqual(storeRows(hybrid.file), [...MEMORIES].sort(), 'the vector file holds every memory')
  assert.deepEqual(storeRows(hybrid.mirror.keyword.path), [...MEMORIES].sort(), 'and so does the keyword index')
  assert.deepEqual([...server.calls.auth], [`Bearer ${ADDON_KEY}`], 'the hybrid backend resolves the child key from the environment')
  assert.deepEqual(readMigrationState(migrationStateFile(dataDir, plain.id)).copied, 5)
  // The index is already in line with the vector file: the update before the next start has nothing to do.
  const mirrored = await runMirror({ python, mirror: hybrid.mirror, dataDir, env: ISOLATED_ENV }).done
  assert.deepEqual(mirrored, { state: 'done', added: 0, removed: 0, remaining: 0 })
})
