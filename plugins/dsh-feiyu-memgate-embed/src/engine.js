// The engine supervisor: runs the worker process for the active model, checks it, restarts it.
//
// A start, every time (also a restart):
//   - a free port on 127.0.0.1, chosen here, and a fresh random key (32 bytes, base64url) kept in
//     memory and handed to the worker only through its environment (KEY_ENV); neither the key nor
//     anything derived from it is logged or written;
//   - the worker loads the model, binds the port and prints its ready line;
//   - one self-test embedding through the worker's endpoint; only then is the engine `ready`.
// A worker that exits unexpectedly is restarted with exponential backoff (1 s, 2 s, ... 30 s); after
// five failures in a row (a worker that ran for a minute resets the count) the engine is `failed`.
// A model that does not load, missing node-llama-cpp and bad options fail at once: retrying cannot
// help. A taken port is retried with another port and does not count.
//
// Phases: stopped (no model) | idle (model known, process not running) | starting | ready |
// restarting (waiting for the backoff) | failed.
import { randomBytes } from 'node:crypto'
import http from 'node:http'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { KEY_ENV, SELF_TEST_TEXT } from './protocol.js'

export const WORKER_PATH = fileURLToPath(new URL('./worker.js', import.meta.url))
const READY_LINE_LIMIT = 64 * 1024

/** A free TCP port on 127.0.0.1, chosen by the system. */
export function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.unref()
    probe.once('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

/** POST a JSON body to a loopback port; resolves { status, json, headers } (json null when unparsable). */
export function postJson({ port, path: urlPath = '/v1/embeddings', key, body, timeoutMs = 30000, headers = {} }) {
  return new Promise((resolve, reject) => {
    const data = Buffer.from(JSON.stringify(body))
    const request = http.request({
      host: '127.0.0.1', port, path: urlPath, method: 'POST', timeout: timeoutMs,
      headers: { ...headers, 'content-type': 'application/json', 'content-length': data.length, ...(key ? { authorization: `Bearer ${key}` } : {}) },
    }, (response) => {
      const chunks = []
      response.on('data', (chunk) => chunks.push(chunk))
      response.on('end', () => {
        let json = null
        try {
          json = JSON.parse(Buffer.concat(chunks).toString('utf8'))
        } catch {
          json = null
        }
        resolve({ status: response.statusCode, json, headers: response.headers })
      })
      response.on('error', reject)
    })
    request.on('timeout', () => request.destroy(Object.assign(new Error('request timed out'), { code: 'ETIMEDOUT' })))
    request.on('error', reject)
    request.end(data)
  })
}

// The first line of a stream, or { timeout } / { exited } / { tooLong }.
function readLine(stream, timeoutMs, exited) {
  return new Promise((resolve) => {
    if (!stream) {
      exited.then(() => resolve({ exited: true }))
      return
    }
    let text = ''
    let done = false
    const finish = (value) => {
      if (done) return
      done = true
      clearTimeout(timer)
      stream.off('data', onData)
      stream.resume() // keep draining: a full pipe must never block the worker
      resolve(value)
    }
    const onData = (chunk) => {
      text += chunk.toString('utf8')
      const end = text.indexOf('\n')
      if (end >= 0) finish({ value: text.slice(0, end) })
      else if (text.length > READY_LINE_LIMIT) finish({ tooLong: true })
    }
    const timer = setTimeout(() => finish({ timeout: true }), timeoutMs)
    timer.unref?.()
    stream.on('data', onData)
    exited.then(() => setTimeout(() => finish({ exited: true }), 20))
  })
}

const PERMANENT = { options: 'bad-options', runtime: 'runtime-missing', load: 'load-failed' }

const sameSpec = (a, b) => a && b && a.modelId === b.modelId && a.modelPath === b.modelPath && a.dimensions === b.dimensions
  && a.contextTokens === b.contextTokens && (a.threads ?? 0) === (b.threads ?? 0)

/**
 * One supervised worker.
 *   spawnProcess  from src/spawn.js
 *   writeLog(text) receives the worker's stderr and one line per lifecycle event (never the key)
 * The remaining options are timings and replacements for tests.
 */
export function createEngine({
  spawnProcess, workerPath = WORKER_PATH, execPath = process.execPath, execArgv = [], writeLog = () => {},
  getPort = freePort, backoffMs = 1000, maxBackoffMs = 30000, maxRestarts = 5, stableMs = 60000,
  readyTimeoutMs = 180000, selfTestTimeoutMs = 60000, stopGraceMs = 3000, maxPortAttempts = 5, extraEnv = {},
  now = Date.now,
}) {
  let spec = null
  let phase = 'stopped'
  let error = null
  let worker = null // { proc, port, key, gen, stopping, retryPort, permanent }
  let current = null // { port, key, maxInputTokens, loadMs, readyAt }
  let gen = 0
  let failures = 0
  let restarts = 0
  let healthyAt = 0
  let everReady = false
  let timer = null
  let launching = false
  const listeners = new Set()
  let waiters = []

  const note = (text) => {
    try {
      writeLog(`[${new Date(now()).toISOString()}] engine: ${text}\n`)
    } catch {
      // logging must never break the engine
    }
  }

  function setPhase(next, err = null) {
    phase = next
    error = err
    if (next !== 'starting' && next !== 'restarting') {
      const settled = waiters
      waiters = []
      for (const resolve of settled) resolve(next === 'ready' ? endpoint() : null)
    }
    for (const fn of listeners) {
      try {
        fn(status())
      } catch {
        // a listener failure must not stop the engine
      }
    }
  }

  function endpoint() {
    return phase === 'ready' && current ? { port: current.port, key: current.key } : null
  }

  function status() {
    return {
      phase, error, modelId: spec?.modelId ?? null, loaded: phase === 'ready', everReady,
      maxInputTokens: current?.maxInputTokens ?? null, loadMs: current?.loadMs ?? null, readyAt: current?.readyAt ?? null,
      restarts,
    }
  }

  function launchSoon() {
    if (launching || !spec) return
    if (timer) clearTimeout(timer)
    timer = null
    setPhase('starting')
    const myGen = ++gen
    launching = true
    launch(myGen).catch(() => {}).finally(() => {
      if (myGen === gen) launching = false
    })
  }

  async function selfTest(port, key, wanted) {
    try {
      const { status: code, json } = await postJson({ port, key, timeoutMs: selfTestTimeoutMs, body: { model: wanted.modelId, input: [SELF_TEST_TEXT] } })
      const vector = json?.data?.[0]?.embedding
      return code === 200 && Array.isArray(vector) && vector.length === wanted.dimensions && vector.every(Number.isFinite)
    } catch {
      return false
    }
  }

  async function launch(myGen) {
    for (let attempt = 0; attempt < maxPortAttempts; attempt++) {
      const wanted = spec
      let port
      try {
        port = await getPort()
      } catch {
        port = null
      }
      if (myGen !== gen || !wanted) return
      if (!port) break
      const key = randomBytes(32).toString('base64url')
      const options = {
        modelId: wanted.modelId, modelPath: wanted.modelPath, port, contextTokens: wanted.contextTokens,
        dimensions: wanted.dimensions, threads: wanted.threads ?? 0, limits: wanted.limits ?? {},
      }
      let proc
      try {
        proc = spawnProcess({
          argv: [execPath, ...execArgv, workerPath, JSON.stringify(options)],
          cwd: path.dirname(wanted.modelPath),
          env: { ...extraEnv, ELECTRON_RUN_AS_NODE: '1', NODE_LLAMA_CPP_GPU: 'false', NODE_LLAMA_CPP_SKIP_DOWNLOAD: 'true', [KEY_ENV]: key },
        })
      } catch {
        note('the worker could not be started')
        crashed({ gen: myGen })
        return
      }
      const w = { proc, port, key, gen: myGen, stopping: false, retryPort: false, permanent: null }
      worker = w
      note(`worker starting for ${wanted.modelId} on port ${port}`)
      proc.stderr?.on('data', (chunk) => {
        try {
          writeLog(chunk)
        } catch {
          // ignore
        }
      })
      proc.stdout?.on('error', () => {})
      proc.exited.then((outcome) => exited(w, outcome))
      const line = await readLine(proc.stdout, readyTimeoutMs, proc.exited)
      if (worker !== w) return
      if (line.timeout || line.tooLong) {
        note(line.timeout ? 'worker did not become ready in time' : 'worker printed an invalid ready line')
        proc.kill()
        return
      }
      if (line.exited) return
      let message = null
      try {
        message = JSON.parse(line.value)
      } catch {
        message = null
      }
      if (message?.event === 'error') {
        if (message.kind === 'port') {
          w.retryPort = true
          note('worker port was taken; trying another port')
          await proc.exited
          if (myGen !== gen) return
          continue
        }
        w.permanent = PERMANENT[message.kind] ?? null
        return
      }
      if (message?.event !== 'ready' || message.port !== port) {
        note('worker printed an unexpected ready line')
        proc.kill()
        return
      }
      if (!(await selfTest(port, key, wanted))) {
        if (worker !== w) return
        note('worker self-test failed')
        proc.kill()
        return
      }
      if (worker !== w) return
      current = {
        port, key, readyAt: now(),
        maxInputTokens: Number.isInteger(message.maxInputTokens) ? message.maxInputTokens : null,
        loadMs: Number.isFinite(message.loadMs) ? message.loadMs : null,
      }
      healthyAt = now()
      everReady = true
      note(`worker ready for ${wanted.modelId} (load ${current.loadMs ?? '?'} ms)`)
      setPhase('ready')
      return
    }
    if (myGen === gen) setPhase('failed', 'port-unavailable')
  }

  function crashed(w) {
    if (w.gen !== gen) return
    if (healthyAt && now() - healthyAt >= stableMs) failures = 0
    healthyAt = 0
    failures += 1
    restarts += 1
    if (failures > maxRestarts) {
      note('worker keeps failing; giving up until the model is chosen again or the add-on restarts')
      setPhase('failed', 'crashed')
      return
    }
    const delay = Math.min(maxBackoffMs, backoffMs * 2 ** (failures - 1))
    setPhase('restarting')
    timer = setTimeout(() => {
      timer = null
      if (phase === 'restarting' && spec && w.gen === gen) {
        launching = false
        launchSoon()
      }
    }, delay)
    timer.unref?.()
  }

  function exited(w, outcome) {
    if (worker !== w) return
    worker = null
    current = null
    note(`worker exited (code ${outcome?.code ?? 'none'}${outcome?.signal ? `, ${outcome.signal}` : ''})`)
    if (w.stopping || w.retryPort) return
    if (w.permanent) {
      setPhase('failed', w.permanent)
      return
    }
    crashed(w)
  }

  async function stopChild() {
    if (timer) clearTimeout(timer)
    timer = null
    gen += 1
    launching = false
    const w = worker
    worker = null
    current = null
    if (!w) return
    w.stopping = true
    try {
      w.proc.stdin?.end()
    } catch {
      // already closed
    }
    let gone = await w.proc.waitForExit(stopGraceMs)
    if (!gone) {
      w.proc.kill()
      gone = await w.proc.waitForExit(stopGraceMs)
    }
    if (!gone && w.proc.forceKill) {
      w.proc.forceKill()
      await w.proc.waitForExit(stopGraceMs)
    }
  }

  return {
    /**
     * Run `next` (a model spec: modelId, modelPath, dimensions, contextTokens, threads, limits). The same
     * spec again does nothing (or starts an idle worker when `start`); another spec replaces the worker.
     */
    async load(next, { start = true } = {}) {
      if (sameSpec(spec, next) && phase !== 'failed' && phase !== 'stopped') {
        if (start && phase === 'idle') launchSoon()
        return
      }
      await stopChild()
      spec = { ...next }
      failures = 0
      restarts = 0
      everReady = false
      if (start) launchSoon()
      else setPhase('idle')
    },
    /** Stop the worker but keep the model: the next ensure() starts it again (idle unload). */
    async unload() {
      await stopChild()
      if (spec) setPhase('idle')
    },
    /** Stop the worker and forget the model. */
    async stop() {
      await stopChild()
      spec = null
      everReady = false
      setPhase('stopped')
    },
    /**
     * The worker's endpoint once it is ready, starting an idle worker; null when it does not become
     * ready within `timeoutMs`, failed, or there is no model.
     */
    ensure(timeoutMs) {
      if (!spec || phase === 'failed' || phase === 'stopped') return Promise.resolve(null)
      if (phase === 'ready') return Promise.resolve(endpoint())
      if (phase === 'idle') launchSoon()
      return new Promise((resolve) => {
        let finished = false
        const done = (value) => {
          if (finished) return
          finished = true
          clearTimeout(t)
          resolve(value)
        }
        const t = setTimeout(() => {
          waiters = waiters.filter((fn) => fn !== done)
          done(null)
        }, timeoutMs)
        t.unref?.()
        waiters.push(done)
      })
    },
    endpoint,
    status,
    spec: () => (spec ? { ...spec } : null),
    onChange(fn) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
  }
}
