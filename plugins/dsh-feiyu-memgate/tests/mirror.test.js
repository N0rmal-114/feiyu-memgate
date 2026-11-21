// The keyword index update (src/mirror.py and its runner in src/gateway.js), with the Python found on
// this machine and the bundled gateway's real `sqlite` backend. The vector store file is built with plain
// sqlite3 rows: the update reads only refs, texts, dates and creation times, never the vectors.
import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { MIRROR_SCRIPT, PACKAGE_DIR, findPython, runMirror, scrubbedEnv } from '../src/gateway.js'

// The helper process and its pipes are unref'd on purpose: they must never keep a host alive. In a test
// file nothing else holds the event loop open while a run is awaited, and Node 22 then ends the file with
// "Promise resolution is still pending" (Node 24 waits). This timer stands in for the host.
const keepAlive = setInterval(() => {}, 60000)
after(() => clearInterval(keepAlive))

const ISOLATED_ENV = { ...process.env, DSH_HOME: path.join(os.tmpdir(), 'dsh-memgate-no-home'), DSH_PRIMARY_RUNTIME: '' }
const python = await findPython({ cache: null, env: ISOLATED_ENV })
const skip = python ? false : 'no Python 3.11 or newer on PATH'

function tempDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-memgate-mirror-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }))
  return dir
}

// Runs a small script with the gateway's interpreter; its arguments arrive as one JSON value.
function py(code, argument) {
  const result = spawnSync(python.executable, ['-I', '-B', '-c', code, JSON.stringify(argument)], {
    env: { ...scrubbedEnv(), PYTHONIOENCODING: 'utf-8' }, encoding: 'utf8', windowsHide: true, timeout: 30000,
  })
  assert.equal(result.status, 0, result.stderr)
  return result.stdout.trim()
}

const MAKE_VECTOR = [
  'import json, sqlite3, sys',
  'arg = json.loads(sys.argv[1])',
  'db = sqlite3.connect(arg["path"])',
  'db.executescript("CREATE TABLE IF NOT EXISTS memories (ref TEXT PRIMARY KEY, text, created_at, date, vector); CREATE TABLE IF NOT EXISTS info (key TEXT PRIMARY KEY, value TEXT NOT NULL);")',
  'for ref, text, created_at, date in arg["rows"]:',
  '    db.execute("INSERT OR REPLACE INTO memories VALUES (?, ?, ?, ?, ?)", (ref, text, created_at, date, b"\\x00"))',
  'for ref in arg.get("drop", []):',
  '    db.execute("DELETE FROM memories WHERE ref = ?", (ref,))',
  'db.commit()',
  'db.close()',
].join('\n')

function vectorFile(file, rows, drop = []) {
  py(MAKE_VECTOR, { path: file, rows, drop })
}

// A store file that has no `memories` table.
const MAKE_BARE = [
  'import json, sqlite3, sys',
  'db = sqlite3.connect(json.loads(sys.argv[1]))',
  'db.execute("CREATE TABLE other (x)")',
  'db.commit()',
  'db.close()',
].join('\n')

const KEYWORD_REFS = [
  'import json, sqlite3, sys',
  'db = sqlite3.connect(json.loads(sys.argv[1]))',
  'print(json.dumps([r[0] for r in db.execute("SELECT ref FROM memories ORDER BY ref")]))',
].join('\n')

const keywordRefs = (file) => (fs.existsSync(file) ? JSON.parse(py(KEYWORD_REFS, file)) : [])

// The gateway's own backend finds a memory through the index: the rows are not just there, they are indexed.
const KEYWORD_FIND = [
  'import json, sys',
  'arg = json.loads(sys.argv[1])',
  'sys.path.insert(0, arg["package"])',
  'from feiyu_memgate import registry',
  'backend = registry.create("backend", {"type": "sqlite", "path": arg["path"]})',
  'try:',
  '    print(json.dumps([hit.ref for hit in backend.find(arg["query"], 5)]))',
  'finally:',
  '    backend.close()',
].join('\n')

const ROWS = [
  ['m-1', 'The user keeps the spare key under the blue flowerpot', 100, '2026-01-01'],
  ['m-2', 'The user drinks oolong tea every morning', 200, '2026-01-02'],
  ['m-3', '用户周末去西湖边跑步', 300, null],
]

function run(dir, options = {}) {
  return runMirror({
    python,
    mirror: { vector: path.join(dir, 'vector.db'), keyword: { type: 'sqlite', path: path.join(dir, 'keywords.db') } },
    dataDir: dir,
    env: ISOLATED_ENV,
    ...options,
  }).done
}

test('the update adds what the index lacks, oldest first, and the gateway backend finds it', { skip, timeout: 60000 }, async (t) => {
  const dir = tempDir(t)
  vectorFile(path.join(dir, 'vector.db'), ROWS)
  const result = await run(dir)
  assert.deepEqual(result, { state: 'done', added: 3, removed: 0, remaining: 0 })
  assert.deepEqual(keywordRefs(path.join(dir, 'keywords.db')), ['m-1', 'm-2', 'm-3'])
  const found = JSON.parse(py(KEYWORD_FIND, { package: PACKAGE_DIR, path: path.join(dir, 'keywords.db'), query: 'oolong tea' }))
  assert.equal(found[0], 'm-2')
})

test('the update removes what the vector store no longer has', { skip, timeout: 60000 }, async (t) => {
  const dir = tempDir(t)
  vectorFile(path.join(dir, 'vector.db'), ROWS)
  await run(dir)
  // A memory forgotten while hybrid was off, and one saved meanwhile.
  vectorFile(path.join(dir, 'vector.db'), [['m-4', 'The user waters the plants on Sundays', 400, '2026-01-04']], ['m-2'])
  const result = await run(dir)
  assert.deepEqual(result, { state: 'done', added: 1, removed: 1, remaining: 0 })
  assert.deepEqual(keywordRefs(path.join(dir, 'keywords.db')), ['m-1', 'm-3', 'm-4'])
})

test('a second run right after a finished one changes nothing', { skip, timeout: 60000 }, async (t) => {
  const dir = tempDir(t)
  vectorFile(path.join(dir, 'vector.db'), [...ROWS, ['m-5', '   ', 500, null]])
  const first = await run(dir)
  assert.deepEqual(first, { state: 'done', added: 3, removed: 0, remaining: 0 }, 'an empty text is skipped, not counted as work left')
  const second = await run(dir)
  assert.deepEqual(second, { state: 'done', added: 0, removed: 0, remaining: 0 })
  assert.deepEqual(keywordRefs(path.join(dir, 'keywords.db')), ['m-1', 'm-2', 'm-3'])
})

test('a missing vector file, or one without the table, holds nothing: the index is emptied', { skip, timeout: 60000 }, async (t) => {
  const dir = tempDir(t)
  assert.deepEqual(await run(dir), { state: 'done', added: 0, removed: 0, remaining: 0 }, 'neither file exists')
  vectorFile(path.join(dir, 'vector.db'), ROWS)
  await run(dir)
  fs.rmSync(path.join(dir, 'vector.db'))
  assert.deepEqual(await run(dir), { state: 'done', added: 0, removed: 3, remaining: 0 })
  assert.deepEqual(keywordRefs(path.join(dir, 'keywords.db')), [])
  vectorFile(path.join(dir, 'vector.db'), ROWS)
  await run(dir)
  const bare = path.join(dir, 'bare.db')
  py(MAKE_BARE, bare)
  const result = await runMirror({
    python, dataDir: dir, env: ISOLATED_ENV,
    mirror: { vector: bare, keyword: { type: 'sqlite', path: path.join(dir, 'keywords.db') } },
  }).done
  assert.deepEqual(result, { state: 'done', added: 0, removed: 3, remaining: 0 })
})

test('at the deadline the update reports partial and the next run continues', { skip, timeout: 60000 }, async (t) => {
  const dir = tempDir(t)
  vectorFile(path.join(dir, 'vector.db'), ROWS)
  const partial = await run(dir, { deadlineSeconds: 0 })
  assert.deepEqual(partial, { state: 'partial', added: 0, removed: 0, remaining: 3 })
  assert.deepEqual(keywordRefs(path.join(dir, 'keywords.db')), [])
  assert.deepEqual(await run(dir), { state: 'done', added: 3, removed: 0, remaining: 0 })
})

test('an index that cannot be opened is reported as a failure, by kind only', { skip, timeout: 60000 }, async (t) => {
  const dir = tempDir(t)
  vectorFile(path.join(dir, 'vector.db'), ROWS)
  // A folder where the index file should be: SQLite cannot open it.
  const keyword = { type: 'sqlite', path: dir }
  const result = await runMirror({ python, dataDir: dir, env: ISOLATED_ENV, mirror: { vector: path.join(dir, 'vector.db'), keyword } }).done
  assert.equal(result.state, 'failed')
  assert.match(result.kind, /^[A-Za-z]{1,40}$/)
})

test('what the script prints never names a text, a ref or a path', { skip, timeout: 60000 }, async (t) => {
  const dir = tempDir(t)
  vectorFile(path.join(dir, 'vector.db'), ROWS)
  const keyword = { type: 'sqlite', path: path.join(dir, 'keywords.db') }
  const job = { package_dir: PACKAGE_DIR, vector: path.join(dir, 'vector.db'), keyword, deadline_seconds: 30 }
  const child = spawn(python.executable, ['-I', '-B', MIRROR_SCRIPT], {
    env: { ...scrubbedEnv(), PYTHONIOENCODING: 'utf-8' }, stdio: ['pipe', 'pipe', 'ignore'], windowsHide: true,
  })
  t.after(() => child.kill())
  let out = ''
  child.stdout.setEncoding('utf8')
  child.stdout.on('data', (chunk) => { out += chunk })
  child.stdin.on('error', () => {})
  child.stdin.write(JSON.stringify(job) + '\n')
  // The script exits when stdin closes, so stdin stays open until the last event has arrived.
  // A loaded machine can take long to start Python: wait almost as long as the test itself may run.
  const deadline = Date.now() + 50000
  while (!/"event": "done"/.test(out) && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 50))
  child.stdin.end()
  assert.deepEqual(out.trim().split(/\r?\n/).map((line) => JSON.parse(line).event), ['start', 'progress', 'done'])
  for (const secret of ['m-1', 'm-2', 'flowerpot', 'oolong', '西湖', dir, 'keywords.db']) assert.ok(!out.includes(secret), secret)
  assert.equal(out.split(/\r?\n/)[0], '{"event": "start", "vector": 3, "keyword": 0}')
})

test('stop ends a running update and reports it as stopped', { skip, timeout: 60000 }, async (t) => {
  const dir = tempDir(t)
  vectorFile(path.join(dir, 'vector.db'), ROWS)
  const runner = runMirror({
    python, dataDir: dir, env: ISOLATED_ENV,
    mirror: { vector: path.join(dir, 'vector.db'), keyword: { type: 'sqlite', path: path.join(dir, 'keywords.db') } },
  })
  await runner.stop()
  assert.equal((await runner.done).state, 'stopped')
})
