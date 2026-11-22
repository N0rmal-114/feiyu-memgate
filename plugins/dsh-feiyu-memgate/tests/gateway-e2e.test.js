// End to end: the REAL gateway, started from a feiyu-memgate checkout, driven through the JS client.
// Runs only when FEIYU_MEMGATE_SRC points at the checkout's src directory (the folder that holds
// the feiyu_memgate package); otherwise it is skipped. Needs Python 3.11+ on PATH as `python`
// (or the interpreter named in FEIYU_MEMGATE_PYTHON).
import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { createClient } from '../src/client.js'
import { readConfig } from '../src/config.js'
import { IDENTITY_KEY_ENV, TOKEN_ENV, gatewayConfig, runMirror } from '../src/gateway.js'

// Helper processes are unref'd on purpose (they must never keep a host alive). In a test file nothing else
// holds the event loop open while one is awaited, and Node 22 then ends the file with "Promise resolution
// is still pending" (Node 24 waits). This timer stands in for the host.
const keepAlive = setInterval(() => {}, 60000)
after(() => clearInterval(keepAlive))

const SRC = process.env.FEIYU_MEMGATE_SRC
const PYTHON = process.env.FEIYU_MEMGATE_PYTHON || 'python'
const TOKEN = 'e2e-fake-token-' + 'x'.repeat(32) // the gateway wants at least 32 characters

async function freePort() {
  const probe = net.createServer()
  await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve))
  const { port } = probe.address()
  await new Promise((resolve) => probe.close(resolve))
  return port
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

test('real gateway: remember, recall, used and health', { skip: SRC ? false : 'set FEIYU_MEMGATE_SRC to a feiyu-memgate checkout\'s src directory to run this test' }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-memgate-e2e-'))
  const port = await freePort()
  const toml = [
    '[server]', 'host = "127.0.0.1"', `port = ${port}`, '',
    '[stores.private]', 'type = "sqlite"', `path = ${JSON.stringify(path.join(dir, 'private.db').replaceAll('\\', '/'))}`, '',
    '[clients.owner]', 'token_env = "GATEWAY_TOKEN_OWNER"', 'recall = ["private"]', 'write = "private"', 'daily_writes = 50', '',
    '[recall]', 'stages = ["min_score", "redact", "dedupe", "budget"]', '',
    '[write]', 'guards = ["redact", { type = "daily_cap", utc_offset_hours = 8 }]', '',
  ].join('\n')
  const configPath = path.join(dir, 'gateway.toml')
  fs.writeFileSync(configPath, toml)
  const code = `import sys; sys.path.insert(0, ${JSON.stringify(path.resolve(SRC))}); from feiyu_memgate.cli import main; main()`
  const child = spawn(PYTHON, ['-I', '-B', '-c', code, 'serve', '--config', configPath], {
    env: { ...process.env, GATEWAY_TOKEN_OWNER: TOKEN, PYTHONIOENCODING: 'utf-8' },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  })
  let output = ''
  child.stdout.on('data', (d) => { output += d })
  child.stderr.on('data', (d) => { output += d })
  let exited = false
  child.on('exit', () => { exited = true })
  const warnings = []
  const client = createClient({ url: `http://127.0.0.1:${port}`, token: TOKEN, logger: { warn: (m) => warnings.push(m) } })
  try {
    let health = null
    for (let i = 0; i < 100 && !health && !exited; i++) {
      health = await client.health({ timeoutMs: 500 })
      if (!health) await sleep(150)
    }
    assert.ok(health, 'the gateway did not come up:\n' + output.slice(0, 2000))
    assert.equal(health.status, 'ok')

    assert.equal(await client.rememberStatus('The user drinks oolong tea every morning'), 'saved')
    let detail = null
    for (let i = 0; i < 40; i++) {
      detail = await client.recallDetail('what tea does the user drink in the morning')
      if (detail?.results.length) break
      await sleep(250)
    }
    assert.ok(detail, 'recall answered')
    assert.ok(detail.results.some((r) => r.text === 'The user drinks oolong tea every morning'), JSON.stringify(detail))
    assert.equal(detail.results[0].source, 'private')

    assert.equal(await client.reportUsed([{ source: detail.results[0].source, id: detail.results[0].id }]), true)

    const deep = await client.health({ deep: true })
    assert.equal(deep.stores.private, 'ok')

    assert.equal(await createClient({ url: `http://127.0.0.1:${port}`, token: 'wrong-' + 'y'.repeat(40) }).rememberStatus('hello there'), 'failed')
    assert.deepEqual(warnings.filter((w) => w.includes(TOKEN)), [])
  } finally {
    child.kill()
    await new Promise((resolve) => (exited ? resolve() : child.once('exit', resolve)))
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

// ------------------------------------------------------------------ hybrid store

// An OpenAI-compatible embeddings endpoint with deterministic vectors: hashed character bigrams,
// normalised. Texts that share wording get a high cosine, so a reworded question can still match.
const DIMENSIONS = 256
function bigramVector(text) {
  const v = new Array(DIMENSIONS).fill(0)
  const chars = [...String(text).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()]
  for (let i = 0; i + 1 < chars.length; i++) {
    let h = 17
    for (const ch of chars[i] + chars[i + 1]) h = (h * 31 + ch.codePointAt(0)) % 1000003
    v[h % DIMENSIONS] += 1
  }
  const norm = Math.sqrt(v.reduce((sum, x) => sum + x * x, 0)) || 1
  return v.map((x) => x / norm)
}

async function embeddingsStub(expectedKey) {
  const seen = { requests: 0, badAuth: 0 }
  const server = http.createServer((req, res) => {
    let body = ''
    req.setEncoding('utf8')
    req.on('data', (chunk) => { body += chunk })
    req.on('end', () => {
      seen.requests += 1
      if (req.headers.authorization !== `Bearer ${expectedKey}`) seen.badAuth += 1
      if (req.method !== 'POST' || req.url !== '/v1/embeddings') {
        res.writeHead(404)
        res.end()
        return
      }
      const { model, input } = JSON.parse(body)
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ object: 'list', model, data: input.map((text, index) => ({ object: 'embedding', index, embedding: bigramVector(text) })) }))
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  return {
    url: `http://127.0.0.1:${server.address().port}/v1`,
    seen,
    close: () => new Promise((resolve) => {
      server.closeAllConnections?.()
      server.close(resolve)
    }),
  }
}

// The refs of one store file, read with plain sqlite3 (the gateway is stopped when this is used).
function refsOf(file) {
  const code = 'import json, sqlite3, sys\ndb = sqlite3.connect(sys.argv[1])\nprint(json.dumps(sorted(r[0] for r in db.execute("SELECT ref FROM memories"))))\ndb.close()'
  const result = spawnSync(PYTHON, ['-I', '-B', '-c', code, file], { encoding: 'utf8', windowsHide: true, timeout: 30000 })
  assert.equal(result.status, 0, result.stderr)
  return JSON.parse(result.stdout)
}

function dropRef(file, ref) {
  const code = 'import sqlite3, sys\ndb = sqlite3.connect(sys.argv[1])\ndb.execute("DELETE FROM memories WHERE ref = ?", (sys.argv[2],))\ndb.commit()\ndb.close()'
  const result = spawnSync(PYTHON, ['-I', '-B', '-c', code, file, ref], { encoding: 'utf8', windowsHide: true, timeout: 30000 })
  assert.equal(result.status, 0, result.stderr)
}

const HYBRID_SKIP = SRC ? false : 'set FEIYU_MEMGATE_SRC to a feiyu-memgate checkout\'s src directory to run this test'

test('real gateway, hybrid endpoint store: both files hold the same refs, recall by wording and by paraphrase, and the index update drops a forgotten memory', { skip: HYBRID_SKIP, timeout: 120000 }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-memgate-e2e-hybrid-'))
  const embeddings = await embeddingsStub('fake-e2e-embeddings-key')
  const port = await freePort()
  const settings = readConfig({
    retrieval: { tier: 'endpoint', hybrid: true, url: embeddings.url, model: 'bge-m3', apiKey: 'fake-e2e-embeddings-key' },
    advanced: { redaction: false },
  })
  const { text, env, store } = gatewayConfig({ port, dataDir: dir, settings })
  assert.equal(store.hybrid, true)
  const configPath = path.join(dir, 'gateway.toml')
  fs.writeFileSync(configPath, text)
  const code = `import sys; sys.path.insert(0, ${JSON.stringify(path.resolve(SRC))}); from feiyu_memgate.cli import main; sys.exit(main())`
  const child = spawn(PYTHON, ['-I', '-B', '-c', code, 'serve', '--config', configPath], {
    env: { ...process.env, ...env, [TOKEN_ENV]: TOKEN, [IDENTITY_KEY_ENV]: 'cd'.repeat(32), PYTHONIOENCODING: 'utf-8' },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  })
  let output = ''
  child.stdout.on('data', (d) => { output += d })
  child.stderr.on('data', (d) => { output += d })
  let exited = false
  child.on('exit', () => { exited = true })
  const client = createClient({ url: `http://127.0.0.1:${port}`, token: TOKEN })
  const stop = async () => {
    child.kill()
    await new Promise((resolve) => (exited ? resolve() : child.once('exit', resolve)))
  }
  try {
    let health = null
    for (let i = 0; i < 100 && !health && !exited; i++) {
      health = await client.health({ timeoutMs: 500 })
      if (!health) await sleep(150)
    }
    assert.ok(health, 'the gateway did not come up with the generated hybrid configuration:\n' + output.slice(0, 2000))

    const memories = [
      'The user keeps the spare key under the blue flowerpot',
      'The user drinks oolong tea every morning',
      'The user parks the bicycle in the courtyard shed',
    ]
    // The generated configuration lets this client speak for the owner only: every call names the account.
    const speaker = { speaker: 'dsh:local' }
    for (const memory of memories) assert.equal(await client.rememberStatus(memory, speaker), 'saved', memory)
    const recall = async (query) => {
      for (let i = 0; i < 40; i++) {
        const detail = await client.recallDetail(query, speaker)
        if (detail?.results.length) return detail.results.map((r) => r.text)
        await sleep(250)
      }
      return []
    }
    assert.ok((await recall('The user drinks oolong tea every morning')).includes(memories[1]), 'exact wording')
    const reworded = await recall('where does the user keep the spare key, under the flowerpot?')
    assert.ok(reworded.includes(memories[0]), `a paraphrase the stub can match: ${JSON.stringify(reworded)}`)
    assert.equal(embeddings.seen.badAuth, 0, 'the key reached the embeddings endpoint through the hybrid backend')
    await stop()

    assert.ok(fs.existsSync(store.file), 'the vector file')
    assert.ok(fs.existsSync(store.mirror.keyword.path), 'the keyword index file')
    const vectorRefs = refsOf(store.file)
    assert.equal(vectorRefs.length, 3)
    assert.deepEqual(refsOf(store.mirror.keyword.path), vectorRefs, 'one ref in both files')

    // Forgotten while hybrid was off: the vector file loses a row, the index still has it.
    dropRef(store.file, vectorRefs[0])
    const mirrored = await runMirror({ python: { argv: [PYTHON] }, mirror: store.mirror, dataDir: dir, packageDir: path.resolve(SRC), env: process.env }).done
    assert.deepEqual(mirrored, { state: 'done', added: 0, removed: 1, remaining: 0 })
    assert.deepEqual(refsOf(store.mirror.keyword.path), vectorRefs.slice(1))
  } finally {
    if (!exited) await stop()
    await embeddings.close()
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  }
})
