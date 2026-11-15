// End to end: the REAL gateway, started from a feiyu-memgate checkout, driven through the JS client.
// Runs only when FEIYU_MEMGATE_SRC points at the checkout's src directory (the folder that holds
// the feiyu_memgate package); otherwise it is skipped. Needs Python 3.11+ on PATH as `python`
// (or the interpreter named in FEIYU_MEMGATE_PYTHON).
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { createClient } from '../src/client.js'

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
  const child = spawn(PYTHON, ['-I', '-c', code, 'serve', '--config', configPath], {
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
