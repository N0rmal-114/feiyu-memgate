// The managed gateway: find a Python, write the gateway configuration, start the bundled gateway,
// keep it running and stop it without leaving a process behind.
//
// Why Node's child_process and not the host's ctx.subprocess: the gateway lives as long as the
// plugin, outside any agent turn, and needs a stdin pipe it never writes to (the orphan watchdog
// below). The host documents spawning directly for such cases (packages/subprocess/subprocess/
// README.md:84, :150) and every child here starts from the same scrubbed environment the host uses
// (scrubbedParentEnv, packages/subprocess/subprocess/src/index.ts:54-76).
//
// What a start does, every time (also on a restart):
//   - a free port on 127.0.0.1, chosen here;
//   - a fresh random token (32 bytes, 43 URL-safe characters), kept in memory and handed to the
//     gateway only through an environment variable; it is never written to a file or a log;
//   - a fresh configuration file in the data directory, named per process, removed on stop;
//   - `python -I ... -c <bootstrap> serve --config <file>`: isolated mode, so no PYTHON* variable,
//     user site or current directory changes what runs.
// Nothing here blocks the host: start() returns at once and the work goes on in the background.
// Logs name kinds of failure only, never a token, a query or a memory.
import { execFile, spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { effectiveTier, resolveStore, TIERS } from './tiers.js'

/** The folder that holds the bundled feiyu_memgate package (python/ in this repository). */
export const PACKAGE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'python')

export const TOKEN_ENV = 'FEIYU_MEMGATE_TOKEN'
const PACKAGE_DIR_ENV = 'FEIYU_MEMGATE_PACKAGE_DIR'
export const MIN_PYTHON = Object.freeze([3, 11])
const STORE_NAME = 'memories'
const CLIENT_NAME = 'dsh'
const LOG_FILE = 'gateway.log'
const LOG_MAX_BYTES = 1024 * 1024
const CONFIG_RE = /^gateway-(\d+)-\d+\.toml$/

// Same rule as the host's scrubbedParentEnv: no credential-shaped names, no DSH_* names (both
// case-insensitive, as Windows environment names are). Explicit variables are added after this.
const SENSITIVE_ENV = /KEY|PASSWORD|SECRET|TOKEN/i

export function scrubbedEnv(parent = process.env) {
  const env = {}
  for (const [key, value] of Object.entries(parent)) {
    if (value !== undefined && !SENSITIVE_ENV.test(key) && !key.toUpperCase().startsWith('DSH_')) env[key] = value
  }
  return env
}

function expandHome(p) {
  return p === '~' || p.startsWith('~/') || p.startsWith('~\\') ? path.join(os.homedir(), p.slice(1)) : p
}

/** The DSH home folder: $DSH_HOME (blank counts as unset), else ~/.dsh (packages/util/home-paths/src/index.ts:101-137). */
export function resolveDshHome(env = process.env) {
  const fromEnv = env.DSH_HOME
  return path.resolve(expandHome(fromEnv !== undefined && fromEnv.trim() ? fromEnv : path.join(os.homedir(), '.dsh')))
}

/**
 * The default data folder. 0.2.2-rc.3 puts new durable records under `$DSH_HOME/data/<owner>`
 * (dshPath('data', ...), packages/util/home-paths/src/index.ts:176-214); 0.2.2-rc.1 has no such
 * helper, so the same layout is built by hand for both.
 */
export function defaultDataDir(env = process.env) {
  return path.join(resolveDshHome(env), 'data', 'feiyu-memgate')
}

export function resolveDataDir(setting, env = process.env) {
  return setting ? path.resolve(expandHome(setting)) : defaultDataDir(env)
}

// ------------------------------------------------------------------ Python discovery

// Prints the version and the real interpreter path; `ok` is false when a module the gateway needs
// is missing (some builds leave out sqlite3). Never fails on an old interpreter: it reports it.
const PROBE = [
  'import json, sys',
  'ok = True',
  'try:',
  '    import sqlite3, tomllib',
  'except Exception:',
  '    ok = False',
  'print(json.dumps({"version": list(sys.version_info[:3]), "executable": sys.executable, "ok": ok}))',
].join('\n')

/** The interpreters to try, in order. Each is an argv prefix; `mustExist` skips a missing file without running it. */
export function pythonCandidates({ configured = '', env = process.env, platform = process.platform } = {}) {
  const list = []
  if (configured) list.push({ argv: [path.resolve(expandHome(configured))], source: 'settings' })
  const tail = platform === 'win32' ? ['dependencies', 'python', 'python.exe'] : ['dependencies', 'python', 'bin', 'python3']
  // The Desktop app's bundled Python: 0.2.2-rc.3 names the payload in DSH_PRIMARY_RUNTIME
  // (apps/desktop-host/src/index.ts:44; layout in packages/skill/tool-workspace-dependencies/
  // README.md:45). 0.2.2-rc.1 does not set it; there, the copy installed on first use under
  // $DSH_HOME/dsh-runtimes/dsh-primary-runtime (apps/desktop/README.md:110) is the one place to look.
  const runtime = env.DSH_PRIMARY_RUNTIME
  if (runtime && runtime.trim()) list.push({ argv: [path.join(runtime, ...tail)], source: 'desktop', mustExist: true })
  list.push({ argv: [path.join(resolveDshHome(env), 'dsh-runtimes', 'dsh-primary-runtime', ...tail)], source: 'desktop', mustExist: true })
  list.push({ argv: ['python3'], source: 'path' }, { argv: ['python'], source: 'path' }, { argv: ['py', '-3'], source: 'path' })
  return list
}

/** Run the probe with one candidate: resolves to its stdout, or null when it cannot run. */
export function runProbe(argv, { timeoutMs = 10000, env = process.env } = {}) {
  return new Promise((resolve) => {
    try {
      execFile(argv[0], [...argv.slice(1), '-I', '-c', PROBE], {
        timeout: timeoutMs, windowsHide: true, env: scrubbedEnv(env), encoding: 'utf8', maxBuffer: 64 * 1024,
      }, (error, stdout) => resolve(error ? null : stdout))
    } catch {
      resolve(null)
    }
  })
}

function parseProbe(stdout) {
  try {
    const lines = String(stdout).trim().split(/\r?\n/)
    const data = JSON.parse(lines[lines.length - 1])
    const v = data.version
    if (!Array.isArray(v) || v.length < 2 || !v.every(Number.isInteger)) return null
    return { version: v, executable: typeof data.executable === 'string' ? data.executable : '', ok: data.ok === true }
  } catch {
    return null
  }
}

const atLeast = (version, min) => version[0] > min[0] || (version[0] === min[0] && version[1] >= min[1])

const discoveryCache = new Map()

/**
 * The first suitable interpreter: { executable, version, source } or null. The answer is cached per
 * candidate list for the life of the process: probing costs a process start per candidate.
 * `runner` and `exists` are for tests.
 */
export function findPython({
  configured = '', env = process.env, platform = process.platform, runner = runProbe, exists = fs.existsSync,
  minVersion = MIN_PYTHON, cache = discoveryCache,
} = {}) {
  const candidates = pythonCandidates({ configured, env, platform })
  const key = JSON.stringify([candidates.map((c) => c.argv), minVersion])
  if (cache && cache.has(key)) return cache.get(key)
  const task = (async () => {
    for (const candidate of candidates) {
      if (candidate.mustExist && !exists(candidate.argv[0])) continue
      const info = parseProbe(await runner(candidate.argv, { env }))
      if (!info || !info.ok || !atLeast(info.version, minVersion)) continue
      // Launch the interpreter itself (not a launcher such as py.exe): killing it then really ends
      // the gateway, and nothing sits between the plugin and the process it watches.
      const executable = info.executable && exists(info.executable) ? info.executable : candidate.argv[0]
      const argv = executable === candidate.argv[0] ? candidate.argv : [executable]
      return { executable: argv[0], argv, version: info.version.join('.'), source: candidate.source }
    }
    return null
  })()
  if (cache) cache.set(key, task)
  return task
}

// ------------------------------------------------------------------ configuration

/** A TOML basic string. Control characters (and DEL) are escaped, the rest is UTF-8 as it is. */
export function tomlString(value) {
  let out = '"'
  for (const ch of String(value)) {
    const code = ch.codePointAt(0)
    if (ch === '"') out += '\\"'
    else if (ch === '\\') out += '\\\\'
    else if (code < 0x20 || code === 0x7f) out += '\\u' + code.toString(16).toUpperCase().padStart(4, '0')
    else out += ch
  }
  return out + '"'
}

function tomlValue(value) {
  if (typeof value === 'string') return tomlString(value)
  if (typeof value === 'boolean') return String(value)
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new RangeError('not a finite number')
    return String(value)
  }
  if (Array.isArray(value)) return '[' + value.map(tomlValue).join(', ') + ']'
  return '{ ' + Object.entries(value).map(([k, v]) => `${k} = ${tomlValue(v)}`).join(', ') + ' }'
}

function table(name, values) {
  return [`[${name}]`, ...Object.entries(values).map(([k, v]) => `${k} = ${tomlValue(v)}`), '']
}

/** What the active tier still needs before the managed gateway can start (empty when nothing). */
export function missingSettings(settings, facts) {
  return TIERS[effectiveTier(settings.retrieval.tier, facts)].missing(settings.retrieval, facts)
}

/**
 * The gateway configuration for one start.
 *   settings  readConfig() output (retrieval and advanced are used)
 *   facts     { embed }: the embedding add-on's snapshot, for the local tier (src/tiers.js)
 * Returns { text, env, store }: env holds the secrets the store reads (never the client token, which
 * the manager adds itself); store is resolveStore()'s answer.
 */
export function gatewayConfig({ port, dataDir, settings, facts = {}, utcOffsetHours = -new Date().getTimezoneOffset() / 60 }) {
  const resolved = resolveStore(settings.retrieval, dataDir, facts)
  if (!resolved.table) throw new Error('the retrieval tier is not ready')
  const { id, table: store } = resolved
  const policy = settings.advanced
  // The gateway's default stages (core/stages.py:393) with the switches applied. freshness goes right
  // after min_score, so the score line sees similarity only (docs/forgetting.md:25-68); newer_first must
  // come after min_score and relative_score (core/stages.py:308).
  const stages = ['min_score']
  if (policy.forgetting) stages.push('freshness')
  stages.push('relative_score')
  if (policy.newerFirst) stages.push('newer_first')
  // linked comes after the score lines and newer_first, before fill_text (DEFAULT_STAGES in core/stages.py).
  if (policy.linkedRecall) stages.push('linked')
  stages.push('fill_text', 'drop_oversize')
  if (policy.redaction) stages.push('redact')
  stages.push('dedupe', 'budget')
  // A daily cap needs the daily_cap guard, or the gateway refuses the configuration (config.py:276-283).
  // The day starts at local midnight.
  const guards = []
  if (policy.redaction) guards.push('redact')
  if (policy.dailyWrites > 0) guards.push({ type: 'daily_cap', utc_offset_hours: utcOffsetHours })
  const client = { token_env: TOKEN_ENV, recall: [STORE_NAME], write: STORE_NAME }
  if (policy.dailyWrites > 0) client.daily_writes = policy.dailyWrites

  const lines = [
    '# Written by dsh-feiyu-memgate on every gateway start; changes here are overwritten.',
    '# The client token is not in this file: the gateway reads it from the environment.',
    '',
    ...table('server', { host: '127.0.0.1', port }),
    ...table(`stores.${STORE_NAME}`, store),
    ...table(`clients.${CLIENT_NAME}`, client),
    ...table('recall', { profile: policy.profile, stages }),
    ...table('write', { guards }),
  ]
  if (policy.forgetting) {
    // The gateway's documented defaults (config.py:328-341, docs/forgetting.md:45-55), written out so
    // the file says what runs. The ledger and archive follow the store they track.
    lines.push(...table('forgetting', {
      ledger: path.join(dataDir, `forgetting-ledger-${id}.db`),
      archive: path.join(dataDir, `forgetting-archive-${id}.db`),
      stores: [STORE_NAME],
      demote_below: 0.3,
      forget_below: 0.05,
      archive_days: 90,
      sweep_every_hours: 24,
      max_forget_fraction: 0.02,
      max_forget_min: 5,
    }))
  }
  return { text: lines.join('\n'), env: resolved.env, store: resolved }
}

// Counts the memories of one store file without opening it for writing. The path arrives as an
// argument, never inside the code, and a missing table counts as empty.
const COUNT = [
  'import pathlib, sqlite3, sys',
  'p = pathlib.Path(sys.argv[1]).resolve()',
  'db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=2)',
  'try:',
  '    print(db.execute("SELECT COUNT(*) FROM memories").fetchone()[0])',
  'except sqlite3.OperationalError:',
  '    print(0)',
  'finally:',
  '    db.close()',
].join('\n')

/**
 * The number of memories in a store file, or null when it cannot be read in time. Uses the gateway's
 * interpreter: the host process may have no SQLite of its own.
 */
export function countMemories({ python, file, timeoutMs = 3000, env = process.env }) {
  if (!python?.argv?.length || !file) return Promise.resolve(null)
  if (!fs.existsSync(file)) return Promise.resolve(0)
  return new Promise((resolve) => {
    try {
      execFile(python.argv[0], [...python.argv.slice(1), '-I', '-B', '-c', COUNT, file], {
        timeout: timeoutMs, windowsHide: true, env: scrubbedEnv(env), encoding: 'utf8', maxBuffer: 4096,
      }, (error, stdout) => {
        const value = Number(String(stdout ?? '').trim())
        resolve(!error && Number.isSafeInteger(value) && value >= 0 ? value : null)
      })
    } catch {
      resolve(null)
    }
  })
}

// ------------------------------------------------------------------ helpers

/** A free TCP port on 127.0.0.1, chosen by the system. */
export function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.once('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

function isAlive(pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return error?.code === 'EPERM'
  }
}

// An append-only log with one rotation: the file never grows past about twice the cap.
function openLog(file, maxBytes = LOG_MAX_BYTES) {
  let size = 0
  try {
    size = fs.statSync(file).size
  } catch {
    size = 0
  }
  return function write(data) {
    try {
      const bytes = Buffer.isBuffer(data) ? data : Buffer.from(String(data), 'utf8')
      if (size + bytes.length > maxBytes) {
        try {
          fs.renameSync(file, file + '.1')
        } catch {
          // another process may have rotated it just now
        }
        size = 0
      }
      fs.appendFileSync(file, bytes)
      size += bytes.length
    } catch {
      // a full disk must not break the gateway or the host
    }
  }
}

// Ends the gateway when this process goes away without stopping it (a crash, process.exit): the
// bootstrap exits when its stdin closes, and this hook kills whatever is still running at exit.
const liveChildren = new Set()
let exitHook = false
/** Kill this child process if the host process exits first (also used by src/migrate.js). */
export function untrackChild(child) {
  liveChildren.delete(child)
}
export function trackChild(child) {
  liveChildren.add(child)
  if (!exitHook) {
    exitHook = true
    process.on('exit', () => {
      for (const c of liveChildren) {
        try {
          c.kill()
        } catch {
          // already gone
        }
      }
    })
  }
}

// Runs inside the gateway process. The thread reads stdin until it closes: the plugin never writes
// to it, so it closes only when the plugin stops the gateway or the host process dies, however it
// died. Either way the gateway exits at once instead of living on as an orphan.
export const BOOTSTRAP = [
  'import os, sys, threading',
  'def _watch_parent():',
  '    try:',
  '        while sys.stdin.buffer.read(4096):',
  '            pass',
  '    except BaseException:',
  '        pass',
  '    os._exit(0)',
  'threading.Thread(target=_watch_parent, name="parent-watch", daemon=True).start()',
  `sys.path.insert(0, os.environ[${JSON.stringify(PACKAGE_DIR_ENV)}])`,
  'from feiyu_memgate.cli import main',
  'sys.exit(main())',
].join('\n')

// ------------------------------------------------------------------ keyword index mirror

export const MIRROR_SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'mirror.py')

const MIRROR_COUNTS = ['added', 'removed', 'remaining']

/**
 * Run src/mirror.py once: it brings the keyword index of a hybrid store in line with the vector file
 * (why: see that file). Same shape as the copy in src/migrate.js: a bounded helper, job as one JSON
 * line on stdin, JSON lines on stdout, never kept alive by or outliving the host.
 *   python   the gateway's interpreter ({ argv })
 *   mirror   resolveStore().mirror: { keyword, vector }
 * Resolves with { state: 'done' | 'partial' | 'failed' | 'stopped', added?, removed?, remaining?, kind? }.
 * Returns { done: Promise, stop(): Promise }.
 */
export function runMirror({
  python, mirror, dataDir, env = process.env, packageDir = PACKAGE_DIR,
  deadlineSeconds = 60, marginSeconds = 30, stopGraceMs = 3000, spawnImpl = spawn,
}) {
  let child = null
  let stopped = false
  const done = new Promise((resolve) => {
    const job = { package_dir: packageDir, vector: mirror.vector, keyword: mirror.keyword, deadline_seconds: deadlineSeconds }
    try {
      child = spawnImpl(python.argv[0], [...python.argv.slice(1), '-I', '-B', MIRROR_SCRIPT], {
        cwd: dataDir,
        env: { ...scrubbedEnv(env), PYTHONIOENCODING: 'utf-8' },
        stdio: ['pipe', 'pipe', 'ignore'],
        windowsHide: true,
      })
    } catch (error) {
      resolve({ state: 'failed', kind: typeof error?.code === 'string' ? error.code : 'spawn' })
      return
    }
    trackChild(child)
    child.unref?.()
    for (const stream of [child.stdin, child.stdout]) stream?.unref?.()
    child.stdin?.on('error', () => {})
    child.stdin?.write(JSON.stringify(job) + '\n')
    // A run that hangs ends after its own time limit and a margin.
    const guard = setTimeout(() => {
      try {
        child.kill('SIGKILL')
      } catch {
        // already gone
      }
    }, (deadlineSeconds + marginSeconds) * 1000)
    guard.unref?.()

    let buffer = ''
    let final = null
    child.stdout?.setEncoding?.('utf8')
    child.stdout?.on('data', (chunk) => {
      buffer += chunk
      let at
      while ((at = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, at)
        buffer = buffer.slice(at + 1)
        let event
        try {
          event = JSON.parse(line)
        } catch {
          continue
        }
        const counts = {}
        for (const key of MIRROR_COUNTS) if (Number.isSafeInteger(event?.[key]) && event[key] >= 0) counts[key] = event[key]
        if (event?.event === 'done' || event?.event === 'partial') final = { state: event.event, ...counts }
        else if (event?.event === 'failed') final = { state: 'failed', kind: /^[A-Za-z]{1,40}$/.test(event.kind) ? event.kind : 'error' }
      }
    })
    let ended = false
    const end = (code) => {
      if (ended) return
      ended = true
      clearTimeout(guard)
      untrackChild(child)
      resolve(stopped ? { state: 'stopped' } : final ?? { state: 'failed', kind: code === null ? 'killed' : `exit${code}` })
    }
    child.once('exit', (code) => end(code))
    child.once('error', () => end(null))
  })

  return {
    done,
    /** End the run now; what it wrote so far stays (the next run continues). */
    async stop() {
      stopped = true
      const proc = child
      if (!proc || proc.exitCode !== null || proc.signalCode !== null) return
      try {
        proc.stdin?.end()
      } catch {
        // already closed
      }
      const finished = await Promise.race([done.then(() => true), new Promise((r) => setTimeout(r, stopGraceMs, false))])
      if (!finished) {
        try {
          proc.kill('SIGKILL')
        } catch {
          // already gone
        }
        await Promise.race([done, new Promise((r) => setTimeout(r, stopGraceMs))])
      }
    },
  }
}

let instances = 0

// ------------------------------------------------------------------ manager

/**
 * One managed gateway.
 *   settings   readConfig() output
 *   facts      { embed }: what the local tier needs (src/tiers.js); fixed for the life of this manager
 *   logger     { warn(message) }: gets one line per problem, kinds only
 * The rest are for tests. Returns { start, stop, ready, endpoint, state, pid, dataDir, onChange,
 * python, store }.
 */
export function createGatewayManager({
  settings,
  facts = {},
  logger,
  env = process.env,
  dataDir = resolveDataDir(settings.gateway.dataDir, env),
  packageDir = PACKAGE_DIR,
  find = findPython,
  healthTimeoutMs = 20000,
  backoffMs = 1000,
  maxBackoffMs = 30000,
  maxRestarts = 5,
  stableMs = 60000,
  stopGraceMs = 3000,
  spawnImpl = spawn,
  mirrorImpl = runMirror,
  mirrorOptions = {},
} = {}) {
  const instance = ++instances
  const configFile = path.join(dataDir, `gateway-${process.pid}-${instance}.toml`)
  const listeners = new Set()
  let state = 'idle' // idle | starting | running | restarting | off | failed | stopped
  let child = null
  let current = null // { url, token } while healthy
  let failures = 0
  let healthyAt = 0
  let timer = null
  let stopping = false
  let python = null
  let log = () => {}
  let waiters = []
  let mirroring = null // the running keyword index update, see src/mirror.py
  let mirrorWarned = false

  const warn = (message) => {
    try {
      logger?.warn?.(message)
    } catch {
      // a broken logger must not stop the gateway
    }
  }
  const note = (text) => log(`[${new Date().toISOString()}] plugin: ${text}\n`)

  function setState(next) {
    state = next
    if (next !== 'starting' && next !== 'restarting') {
      const settled = waiters
      waiters = []
      for (const resolve of settled) resolve(next)
    }
    for (const fn of listeners) {
      try {
        fn(next)
      } catch {
        // a listener failure must not stop the gateway
      }
    }
  }

  function removeConfig() {
    try {
      fs.rmSync(configFile, { force: true })
    } catch {
      // nothing to do
    }
  }

  // Configuration files of processes that died without stopping their gateway.
  function removeStaleConfigs() {
    try {
      for (const name of fs.readdirSync(dataDir)) {
        const pid = Number(CONFIG_RE.exec(name)?.[1])
        if (pid && pid !== process.pid && !isAlive(pid)) fs.rmSync(path.join(dataDir, name), { force: true })
      }
    } catch {
      // best effort
    }
  }

  async function healthy(url, proc) {
    const deadline = Date.now() + healthTimeoutMs
    while (Date.now() < deadline && !stopping && child === proc && proc.exitCode === null) {
      try {
        const response = await fetch(url + '/healthz', { signal: AbortSignal.timeout(1000) })
        await response.body?.cancel().catch(() => {})
        if (response.status === 200) return true
      } catch {
        // not listening yet
      }
      await new Promise((resolve) => setTimeout(resolve, 150))
    }
    return false
  }

  // One run of the keyword index update; counts and kinds only go to the log.
  async function mirror(spec) {
    let result
    try {
      mirroring = mirrorImpl({ python, mirror: spec, dataDir, env, packageDir, ...mirrorOptions })
      result = await mirroring.done
    } catch {
      result = { state: 'failed', kind: 'error' }
    } finally {
      mirroring = null
    }
    if (result.state === 'done' || result.state === 'partial') {
      note(`keyword index ${result.state}: ${result.added ?? 0} added, ${result.removed ?? 0} removed, ${result.remaining ?? 0} remaining`)
    } else if (result.state === 'failed') {
      note(`keyword index update failed (${result.kind ?? 'error'})`)
    }
    return result
  }

  async function launch() {
    timer = null
    if (stopping) return
    let port
    let config
    try {
      port = await freePort()
      config = gatewayConfig({ port, dataDir, settings, facts })
      fs.writeFileSync(configFile, config.text, { encoding: 'utf8', mode: 0o600 })
    } catch {
      warn('feiyu-memgate: cannot write the gateway configuration; memory is off')
      setState('off')
      return
    }
    if (stopping) return removeConfig()
    // A hybrid store: bring the keyword index in line with the vector file first. The gateway is not
    // running now, so nothing else writes to either file. A failed or partial update never keeps the
    // gateway from starting: the next start continues where this one stopped.
    if (config.store.mirror) {
      const result = await mirror(config.store.mirror)
      if (stopping) return removeConfig()
      if (result.state === 'failed' && !mirrorWarned) {
        mirrorWarned = true
        warn(`feiyu-memgate: the keyword index could not be updated (${result.kind ?? 'error'}); hybrid search starts with what it has`)
      }
    }
    const token = randomBytes(32).toString('base64url')
    const proc = spawnImpl(python.argv[0], [
      ...python.argv.slice(1),
      '-I',
      // Byte-code caches go to the data folder, not next to the bundled sources in the install folder.
      '-X', `pycache_prefix=${path.join(dataDir, 'pycache')}`,
      '-c', BOOTSTRAP, 'serve', '--config', configFile,
    ], {
      cwd: dataDir,
      env: { ...scrubbedEnv(env), ...config.env, [TOKEN_ENV]: token, [PACKAGE_DIR_ENV]: packageDir, PYTHONIOENCODING: 'utf-8' },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })
    child = proc
    trackChild(proc)
    note(`gateway starting on port ${port}`)
    proc.stdout?.on('data', log)
    proc.stderr?.on('data', log)
    proc.stdin?.on('error', () => {})
    // The gateway must never keep the host process alive: a host that ends by running out of work
    // exits as usual, the exit hook kills the gateway and the watchdog covers the rest.
    proc.unref?.()
    for (const stream of [proc.stdin, proc.stdout, proc.stderr]) stream?.unref?.()
    let ended = false
    const onEnd = (code) => {
      if (ended) return
      ended = true
      liveChildren.delete(proc)
      exited(proc, code)
    }
    proc.once('exit', (code) => onEnd(code))
    proc.once('error', () => onEnd(null))
    const url = `http://127.0.0.1:${port}`
    if (await healthy(url, proc)) {
      if (stopping || child !== proc) return
      current = { url, token }
      healthyAt = Date.now()
      note('gateway healthy')
      setState('running')
    } else if (child === proc && !stopping && proc.exitCode === null) {
      note('gateway did not become healthy in time')
      try {
        proc.kill()
      } catch {
        // exit handling restarts it
      }
    }
  }

  function exited(proc, code) {
    if (child !== proc) return
    child = null
    current = null
    removeConfig()
    note(`gateway exited (code ${code ?? 'none'})`)
    if (stopping) return
    if (healthyAt && Date.now() - healthyAt >= stableMs) failures = 0
    healthyAt = 0
    failures += 1
    if (failures > maxRestarts) {
      warn('feiyu-memgate: the gateway keeps stopping; memory is off until the plugin restarts (see gateway.log in the data folder)')
      setState('failed')
      return
    }
    setState('restarting')
    timer = setTimeout(launch, Math.min(maxBackoffMs, backoffMs * 2 ** (failures - 1)))
    timer.unref?.()
  }

  async function run() {
    const missing = missingSettings(settings, facts)
    if (missing.length) {
      warn(`feiyu-memgate: the ${effectiveTier(settings.retrieval.tier, facts)} retrieval tier needs ${missing.join(', ')}; memory is off`)
      return setState('off')
    }
    try {
      fs.mkdirSync(dataDir, { recursive: true })
    } catch {
      warn('feiyu-memgate: cannot create the data folder; memory is off')
      return setState('off')
    }
    log = openLog(path.join(dataDir, LOG_FILE))
    removeStaleConfigs()
    try {
      python = await find({ configured: settings.gateway.python, env })
    } catch {
      python = null
    }
    if (stopping) return
    if (!python) {
      warn('feiyu-memgate: no Python 3.11 or newer was found, so memory is off. Set gateway.python in the plugin settings, or use an external gateway.')
      return setState('off')
    }
    note(`using Python ${python.version} (${python.source})`)
    await launch()
  }

  return {
    dataDir,
    configFile,
    /** Start in the background; calling it again does nothing. */
    start() {
      if (state !== 'idle') return
      setState('starting')
      run().catch(() => {
        warn('feiyu-memgate: the gateway could not start; memory is off')
        setState('off')
      })
    },
    /** Stop the gateway and wait until its process is gone. */
    async stop() {
      if (state === 'stopped') return
      stopping = true
      if (timer) clearTimeout(timer)
      timer = null
      const proc = child
      current = null
      // A keyword index update still running ends now; launch() then sees `stopping` and spawns nothing.
      if (mirroring) await mirroring.stop?.()
      if (proc && proc.exitCode === null && proc.signalCode === null) {
        const gone = new Promise((resolve) => {
          proc.once('exit', resolve)
          proc.once('error', resolve)
        })
        // Closing stdin makes the bootstrap exit by itself; the kill covers a gateway that hangs.
        try {
          proc.stdin?.end()
        } catch {
          // already closed
        }
        const graceful = await Promise.race([gone.then(() => true), new Promise((r) => setTimeout(r, stopGraceMs, false))])
        if (!graceful) {
          try {
            proc.kill('SIGKILL')
          } catch {
            // already gone
          }
          await Promise.race([gone, new Promise((r) => setTimeout(r, stopGraceMs))])
        }
      }
      liveChildren.delete(proc)
      child = null
      removeConfig()
      setState('stopped')
    },
    /** Resolves with the state once starting is over: running, off, failed or stopped. */
    ready() {
      if (state !== 'idle' && state !== 'starting' && state !== 'restarting') return Promise.resolve(state)
      return new Promise((resolve) => waiters.push(resolve))
    },
    /** { url, token } while the gateway is healthy, else null. */
    endpoint: () => current,
    state: () => state,
    pid: () => child?.pid ?? null,
    /** The interpreter in use: { executable, argv, version, source }, or null before discovery or when none was found. */
    python: () => python,
    /** The store this manager runs (resolveStore() for its settings and facts). */
    store: () => resolveStore(settings.retrieval, dataDir, facts),
    onChange(fn) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
  }
}
