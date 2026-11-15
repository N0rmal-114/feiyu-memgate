// The add-on's state file: which models are installed (with the size, hash and modification time
// they were verified with), the model and origin chosen with the chat command, and the licence
// acceptances. It never holds a key, a token or anything the user wrote.
//
// Writes go to a temporary file in the same folder and are renamed over the old one, so a crash
// leaves either the old or the new state, never half of one.
import { randomBytes } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

export const STATE_FILE = 'embed-state.json'
const VERSION = 1

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)

export function emptyState() {
  return { version: VERSION, active: null, origin: null, installed: {}, acceptances: {} }
}

// Keep only well-formed parts of a state read from disk: a hand-edited or damaged file must not
// break the add-on, and unknown keys are dropped.
export function sanitizeState(raw) {
  const state = emptyState()
  if (!isObject(raw)) return state
  if (typeof raw.active === 'string' && raw.active) state.active = raw.active
  if (typeof raw.origin === 'string' && raw.origin) state.origin = raw.origin
  if (isObject(raw.installed)) {
    for (const [id, row] of Object.entries(raw.installed)) {
      if (isObject(row) && typeof row.sha256 === 'string' && Number.isSafeInteger(row.sizeBytes) && Number.isFinite(row.mtimeMs)) {
        state.installed[id] = { sha256: row.sha256, sizeBytes: row.sizeBytes, mtimeMs: row.mtimeMs, verifiedAt: typeof row.verifiedAt === 'string' ? row.verifiedAt : null }
      }
    }
  }
  if (isObject(raw.acceptances)) {
    for (const [id, row] of Object.entries(raw.acceptances)) {
      if (isObject(row) && typeof row.name === 'string' && typeof row.url === 'string' && typeof row.acceptedAt === 'string') {
        state.acceptances[id] = { name: row.name, url: row.url, acceptedAt: row.acceptedAt }
      }
    }
  }
  return state
}

/**
 * The state store of one data folder. read() never throws (a missing or unreadable file is an
 * empty state); update(fn) applies fn to a copy and writes the result, serialised.
 */
export function createStateStore(dataDir) {
  const file = path.join(dataDir, STATE_FILE)
  let chain = Promise.resolve()

  function read() {
    try {
      return sanitizeState(JSON.parse(fs.readFileSync(file, 'utf8')))
    } catch {
      return emptyState()
    }
  }

  function write(state) {
    fs.mkdirSync(dataDir, { recursive: true })
    const tmp = `${file}.${process.pid}.${randomBytes(4).toString('hex')}.tmp`
    try {
      fs.writeFileSync(tmp, JSON.stringify(state, null, 2) + '\n', { encoding: 'utf8', mode: 0o600 })
      fs.renameSync(tmp, file)
    } finally {
      fs.rmSync(tmp, { force: true })
    }
  }

  /** Apply fn(state) to the current state and save it; resolves with the saved state. */
  function update(fn) {
    const task = chain.then(() => {
      const draft = structuredClone(read())
      const next = sanitizeState(fn(draft) ?? draft)
      write(next)
      return next
    })
    chain = task.catch(() => {})
    return task
  }

  return { file, read, update }
}
