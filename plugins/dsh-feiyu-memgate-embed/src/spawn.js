// How the worker process is started: through the host's subprocess service when there is one, else
// with Node's child_process. Both give the engine the same small handle:
//   { stdin, stdout, stderr, exited: Promise<{ code, signal }>, kill(), waitForExit(ms) }
//
// The host service (packages/subprocess/subprocess/src/types.ts:77-107 for the spawn request,
// :169-196 for the handle) starts children from a scrubbed copy of the parent environment, without
// credential-shaped names and DSH_* names, and merges the request's explicit `env` after the scrub
// (src/index.ts:47-87). It owns the process range: terminate() and waitForExit() act on it, and its
// disposal ends every managed process (README.md:68-70, :114). The fallback applies the same scrub.
import { spawn as nodeSpawn } from 'node:child_process'

// Same rule as the host's SENSITIVE_ENV_PATTERN and DSH_ENV_PREFIX (subprocess/src/index.ts:54,
// types.ts:13), both case-insensitive as Windows environment names are.
const SENSITIVE_ENV = /KEY|PASSWORD|SECRET|TOKEN/i

export function scrubbedEnv(parent = process.env) {
  const env = {}
  for (const [key, value] of Object.entries(parent)) {
    if (value !== undefined && !SENSITIVE_ENV.test(key) && !key.toUpperCase().startsWith('DSH_')) env[key] = value
  }
  return env
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms).unref?.())

/**
 * A spawner on the host's subprocess service (`ctx.subprocess`).
 * @param {any} subprocess  the service
 * @param {{ graceMs?: number }} [options]  grace period of the host's termination procedure
 */
export function hostSpawner(subprocess, { graceMs = 3000 } = {}) {
  return function spawnProcess({ argv, env, cwd }) {
    const handle = subprocess.spawn({
      argv, cwd, env, graceMs,
      stdio: { stdin: 'pipe', stdout: 'pipe', stderr: 'pipe' },
    })
    const exited = Promise.resolve(handle.done).then(
      (outcome) => ({ code: outcome?.exitCode ?? null, signal: outcome?.signal ?? null }),
      () => ({ code: null, signal: null, spawnFailed: true }),
    )
    return {
      kind: 'host',
      stdin: handle.stdin,
      stdout: handle.stdout,
      stderr: handle.stderr,
      exited,
      kill: () => handle.terminate(),
      async waitForExit(ms) {
        const timer = AbortSignal.timeout(ms)
        try {
          return await handle.waitForExit(timer)
        } catch {
          return Promise.race([exited.then(() => true), wait(ms).then(() => false)])
        }
      },
    }
  }
}

// Children started here are killed when this process exits without stopping them (process.exit, an
// uncaught exception). A native crash or SIGKILL of this process skips this hook; the worker then
// sees its stdin close and exits by itself.
const live = new Set()
let hooked = false
function track(child) {
  live.add(child)
  child.once('exit', () => live.delete(child))
  if (!hooked) {
    hooked = true
    process.on('exit', () => {
      for (const c of live) {
        try {
          c.kill()
        } catch {
          // already gone
        }
      }
    })
  }
}

/** A spawner on Node's child_process, for hosts without the subprocess service (and for tests). */
export function nodeSpawner({ parentEnv = process.env } = {}) {
  return function spawnProcess({ argv, env, cwd }) {
    const child = nodeSpawn(argv[0], argv.slice(1), {
      cwd, env: { ...scrubbedEnv(parentEnv), ...env }, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
    })
    track(child)
    const exited = new Promise((resolve) => {
      let settled = false
      child.once('exit', (code, signal) => {
        if (!settled) {
          settled = true
          resolve({ code, signal })
        }
      })
      child.once('error', () => {
        if (!settled) {
          settled = true
          resolve({ code: null, signal: null, spawnFailed: true })
        }
      })
    })
    child.stdin?.on('error', () => {})
    return {
      kind: 'node',
      pid: child.pid,
      stdin: child.stdin,
      stdout: child.stdout,
      stderr: child.stderr,
      exited,
      kill: () => {
        try {
          child.kill()
        } catch {
          // already gone
        }
      },
      forceKill: () => {
        try {
          child.kill('SIGKILL')
        } catch {
          // already gone
        }
      },
      waitForExit: (ms) => Promise.race([exited.then(() => true), wait(ms).then(() => false)]),
    }
  }
}
