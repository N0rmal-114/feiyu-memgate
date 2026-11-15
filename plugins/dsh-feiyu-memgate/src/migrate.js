// Copying the keyword store into a vector store when a vector tier becomes active.
//
// Memories live in the store file of the tier that saved them (src/tiers.js). Without a copy, the
// first switch from keyword to a vector tier would start with an empty memory. src/migrate.py does
// the copy with the gateway's own backends and the same interpreter; this module starts it, reads its
// progress and stops it. A run is bounded (items and seconds) and resumable: its state file records
// what was copied, so the plugin simply runs it again while it reports progress.
//
// Like the gateway, the copy never keeps the host alive and never outlives it: the child and its
// pipes are unref'd, it exits when its stdin closes, and the exit hook kills it if the host exits
// first. Logs and status name counts and kinds of failure only.
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { PACKAGE_DIR, scrubbedEnv, trackChild, untrackChild } from './gateway.js'

export const MIGRATE_SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'migrate.py')

/** The state file of the copy into one target store (named by the store id, like its other files). */
export function migrationStateFile(dataDir, targetId) {
  return path.join(dataDir, `migration-${targetId}.json`)
}

/**
 * What the state file says: { copied, refused, done, updated } (counts, never refs), or null when
 * there is none. Refused texts are those the endpoint would not embed (see src/migrate.py).
 */
export function readMigrationState(file) {
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf8'))
    if (!data || data.version !== 1 || !Array.isArray(data.copied)) return null
    return {
      copied: data.copied.length,
      refused: Array.isArray(data.refused) ? data.refused.length : 0,
      done: data.done === true,
      updated: Number.isInteger(data.updated) ? data.updated : null,
    }
  } catch {
    return null
  }
}

const COUNTS = ['total', 'copied', 'refused', 'remaining']

function counts(event) {
  const out = {}
  for (const key of COUNTS) if (Number.isSafeInteger(event?.[key]) && event[key] >= 0) out[key] = event[key]
  return out
}

/**
 * Run one bounded copy.
 *   python    the gateway's interpreter ({ argv })
 *   source    path of the keyword store file
 *   target    { id, table, env }: the vector store as resolveStore() describes it
 *   dataDir   where the state file goes
 *   onProgress(status)  called with every status update
 * Resolves with { state: 'done' | 'partial' | 'failed' | 'stopped', total?, copied?, refused?, remaining?, kind? }.
 * Returns { done: Promise, stop(): Promise }.
 */
export function runMigration({
  python, source, target, dataDir, env = process.env, packageDir = PACKAGE_DIR,
  maxItems = 500, batch = 32, timeoutSeconds = 30, deadlineSeconds = 120, stopGraceMs = 3000,
  onProgress = () => {}, spawnImpl = spawn,
}) {
  let child = null
  let last = { state: 'running' }
  let stopped = false
  const report = (status) => {
    last = status
    try {
      onProgress(status)
    } catch {
      // a broken listener must not stop the copy
    }
  }

  const done = new Promise((resolve) => {
    const job = {
      package_dir: packageDir,
      source,
      target: target.table,
      state_file: migrationStateFile(dataDir, target.id),
      max_items: maxItems,
      batch,
      timeout: timeoutSeconds,
      deadline_seconds: deadlineSeconds,
    }
    try {
      child = spawnImpl(python.argv[0], [...python.argv.slice(1), '-I', '-B', MIGRATE_SCRIPT], {
        cwd: dataDir,
        env: { ...scrubbedEnv(env), ...target.env, PYTHONIOENCODING: 'utf-8' },
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
    // A copy that hangs (an endpoint that never answers) ends after its own time limit and a margin.
    const guard = setTimeout(() => {
      try {
        child.kill('SIGKILL')
      } catch {
        // already gone
      }
    }, (deadlineSeconds + timeoutSeconds + 30) * 1000)
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
        if (event?.event === 'start' || event?.event === 'progress') report({ state: 'running', ...counts(event) })
        else if (event?.event === 'done' || event?.event === 'partial') final = { state: event.event, ...counts(event) }
        else if (event?.event === 'failed') {
          final = { state: 'failed', ...counts(event), kind: /^[A-Za-z]{1,40}$/.test(event.kind) ? event.kind : 'error' }
        }
      }
    })
    let ended = false
    const end = (code) => {
      if (ended) return
      ended = true
      clearTimeout(guard)
      untrackChild(child)
      const result = stopped
        ? { ...counts(last), state: 'stopped' }
        : final ?? { ...counts(last), state: 'failed', kind: code === null ? 'killed' : `exit${code}` }
      report(result)
      resolve(result)
    }
    child.once('exit', (code) => end(code))
    child.once('error', () => end(null))
  })

  return {
    done,
    status: () => last,
    /** End the copy now; what it copied so far stays copied and is recorded. */
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
