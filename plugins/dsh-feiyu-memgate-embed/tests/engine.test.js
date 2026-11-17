// The engine supervisor with a fake worker (tests/fixtures/fake-worker.mjs): readiness and the
// self-test, restarts with backoff, permanent failures, port retries, unload and wake-up, stopping,
// the worker ending with its parent, and keys that never reach a log.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { after, before, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createEngine, postJson } from '../src/engine.js'
import { KEY_ENV } from '../src/protocol.js'
import { nodeSpawner } from '../src/spawn.js'

const FAKE = fileURLToPath(new URL('./fixtures/fake-worker.mjs', import.meta.url))
const PARENT = fileURLToPath(new URL('./fixtures/parent-host.mjs', import.meta.url))
const delay = (ms) => new Promise((r) => setTimeout(r, ms))

// The worker process and its pipes are unref'd on purpose: they must never keep a host alive. In a test
// file nothing else holds the event loop open while the engine is awaited, and Node 22 then ends the
// file with "Promise resolution is still pending" (Node 24 waits). This timer stands in for the host.
const keepAlive = setInterval(() => {}, 60000)
after(() => clearInterval(keepAlive))

let root
before(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'fmg-embed-engine-'))
})
after(() => fs.rmSync(root, { recursive: true, force: true }))

function alive(pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return error.code === 'EPERM'
  }
}

async function until(fn, ms = 8000, step = 20) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (await fn()) return true
    await delay(step)
  }
  return false
}

function setup(mode = '', options = {}) {
  const dir = fs.mkdtempSync(path.join(root, 'e-'))
  const keys = []
  const log = []
  const phases = []
  const base = nodeSpawner()
  const spawnProcess = (spec) => {
    keys.push(spec.env[KEY_ENV])
    return base(spec)
  }
  const engine = createEngine({
    spawnProcess, workerPath: FAKE, backoffMs: 60, maxBackoffMs: 400, maxRestarts: 2, readyTimeoutMs: 8000, selfTestTimeoutMs: 5000,
    stopGraceMs: 2000, writeLog: (d) => log.push(String(d)), extraEnv: { FAKE_MODE: mode, FAKE_STATE_DIR: dir }, ...options,
  })
  engine.onChange((s) => phases.push([s.phase, Date.now(), s.error]))
  const spec = { modelId: 'bge-small-zh-v1.5', modelPath: path.join(dir, 'model.gguf'), dimensions: 8, contextTokens: 64 }
  const starts = () => Number(fs.readFileSync(path.join(dir, 'starts'), 'utf8'))
  const pid = () => Number(fs.readFileSync(path.join(dir, 'pid'), 'utf8'))
  return { engine, spec, keys, log, phases, starts, pid, dir }
}

describe('engine', () => {
  it('reports starting until the self-test passes, then serves', async () => {
    const t = setup()
    try {
      await t.engine.load(t.spec)
      assert.equal(t.engine.status().phase, 'starting')
      const ep = await t.engine.ensure(8000)
      assert.ok(ep && ep.port > 0 && ep.key.length >= 32)
      assert.deepEqual(t.phases.map((p) => p[0]), ['starting', 'ready'])
      const r = await postJson({ port: ep.port, key: ep.key, body: { model: 'bge-small-zh-v1.5', input: ['a', 'b'] } })
      assert.equal(r.status, 200)
      assert.equal(r.json.data.length, 2)
      assert.equal(t.engine.status().maxInputTokens, 64)
    } finally {
      await t.engine.stop()
    }
  })

  it('restarts a crashing worker with growing delays, then gives up', async () => {
    const t = setup('crash-after-ready')
    try {
      await t.engine.load(t.spec)
      assert.ok(await until(() => t.engine.status().phase === 'failed', 15000))
      assert.equal(t.engine.status().error, 'crashed')
      assert.equal(t.starts(), 3) // the first start and two restarts
      const restarting = t.phases.filter((p) => p[0] === 'restarting').map((p) => p[1])
      const starting = t.phases.filter((p) => p[0] === 'starting').map((p) => p[1])
      assert.equal(restarting.length, 2)
      const waits = restarting.map((at, i) => starting[i + 1] - at)
      assert.ok(waits[0] >= 50 && waits[1] >= 110 && waits[1] > waits[0] - 5, `waits ${waits}`)
      assert.equal(await t.engine.ensure(100), null)
    } finally {
      await t.engine.stop()
    }
  })

  it('recovers when only the first starts crash', async () => {
    const t = setup('crash-first-2')
    try {
      await t.engine.load(t.spec)
      assert.ok(await t.engine.ensure(10000))
      assert.equal(t.starts(), 3)
      assert.equal(t.engine.status().restarts, 2)
    } finally {
      await t.engine.stop()
    }
  })

  it('fails at once when the model does not load', async () => {
    const t = setup('fail-load')
    try {
      await t.engine.load(t.spec)
      assert.ok(await until(() => t.engine.status().phase === 'failed'))
      assert.equal(t.engine.status().error, 'load-failed')
      await delay(300)
      assert.equal(t.starts(), 1)
    } finally {
      await t.engine.stop()
    }
  })

  it('retries a taken port without counting a failure', async () => {
    const t = setup('port-taken-once')
    try {
      await t.engine.load(t.spec)
      assert.ok(await t.engine.ensure(8000))
      assert.equal(t.starts(), 2)
      assert.equal(t.engine.status().restarts, 0)
    } finally {
      await t.engine.stop()
    }
  })

  it('treats a failed self-test as a failed start', async () => {
    const t = setup('bad-vectors')
    try {
      await t.engine.load(t.spec)
      assert.ok(await until(() => t.engine.status().phase === 'failed', 15000))
      assert.equal(t.engine.status().error, 'crashed')
      assert.ok(t.log.join('').includes('self-test failed'))
    } finally {
      await t.engine.stop()
    }
  })

  it('unloads to idle and wakes up on ensure()', async () => {
    const t = setup()
    try {
      await t.engine.load(t.spec)
      const first = await t.engine.ensure(8000)
      const pid = t.pid()
      await t.engine.unload()
      assert.equal(t.engine.status().phase, 'idle')
      assert.equal(t.engine.endpoint(), null)
      assert.ok(await until(() => !alive(pid)))
      const second = await t.engine.ensure(8000)
      assert.ok(second)
      assert.notEqual(second.key, first.key) // a key per start
      assert.equal(t.starts(), 2)
    } finally {
      await t.engine.stop()
    }
  })

  it('does not start with start: false until asked', async () => {
    const t = setup()
    try {
      await t.engine.load(t.spec, { start: false })
      assert.equal(t.engine.status().phase, 'idle')
      await delay(200)
      assert.equal(fs.existsSync(path.join(t.dir, 'starts')), false)
      assert.ok(await t.engine.ensure(8000))
    } finally {
      await t.engine.stop()
    }
  })

  it('stops the worker process and forgets the model', async () => {
    const t = setup()
    await t.engine.load(t.spec)
    await t.engine.ensure(8000)
    const pid = t.pid()
    await t.engine.stop()
    assert.equal(t.engine.status().phase, 'stopped')
    assert.equal(t.engine.spec(), null)
    assert.ok(await until(() => !alive(pid)))
  })

  it('replaces the worker when the model changes', async () => {
    const t = setup()
    try {
      await t.engine.load(t.spec)
      await t.engine.ensure(8000)
      const pid = t.pid()
      await t.engine.load({ ...t.spec, modelId: 'bge-m3', dimensions: 8 })
      const ep = await t.engine.ensure(8000)
      assert.ok(await until(() => !alive(pid)))
      const old = await postJson({ port: ep.port, key: ep.key, body: { model: 'bge-small-zh-v1.5', input: ['x'] } })
      assert.equal(old.status, 404)
    } finally {
      await t.engine.stop()
    }
  })

  it('never writes a key to its log', async () => {
    const t = setup('crash-first-1')
    try {
      await t.engine.load(t.spec)
      await t.engine.ensure(8000)
      await t.engine.unload()
      await t.engine.ensure(8000)
      assert.ok(t.keys.length >= 3)
      const text = t.log.join('')
      assert.ok(text.includes('worker ready'))
      for (const key of t.keys) assert.equal(text.includes(key), false)
    } finally {
      await t.engine.stop()
    }
  })
})

describe('engine and its parent', () => {
  it('the worker exits when its parent is killed', async () => {
    const dir = fs.mkdtempSync(path.join(root, 'p-'))
    const parent = spawn(process.execPath, [PARENT, dir], { stdio: ['ignore', 'pipe', 'inherit'] })
    try {
      const line = await new Promise((resolve, reject) => {
        let text = ''
        parent.stdout.on('data', (d) => {
          text += d
          const m = /ready (\d+)/.exec(text)
          if (m) resolve(Number(m[1]))
        })
        parent.once('exit', () => reject(new Error('parent exited early')))
        setTimeout(() => reject(new Error('no ready line')), 10000)
      })
      assert.ok(alive(line))
      parent.kill('SIGKILL')
      assert.ok(await until(() => !alive(line), 10000), 'the worker outlived its parent')
    } finally {
      if (parent.exitCode === null) parent.kill('SIGKILL')
    }
  })
})
