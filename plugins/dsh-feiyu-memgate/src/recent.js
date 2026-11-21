// The file of the recent pool: recent-pool.json in the plugin's data folder (src/pool.js has the rules).
//
// Synchronous and small (a few kilobytes), written atomically: a temporary file in the same folder, then a
// rename, so a reader never sees half a file. Nothing is logged here; a failure comes back as { ok: false, kind }
// with an error code or name, never its message. The only fields in the file are those src/pool.js saves.
import fs from 'node:fs'
import path from 'node:path'
import { parseSaved } from './pool.js'

export const RECENT_FILE = 'recent-pool.json'
const MAX_FILE_BYTES = 256 * 1024

const kindOf = (error) => {
  const code = error?.code
  if (typeof code === 'string' && /^[A-Z0-9_]{1,40}$/.test(code)) return code
  const name = error?.name
  return typeof name === 'string' && /^[A-Za-z]{1,40}$/.test(name) ? name : 'error'
}

/** The saved object (validated), { saved: null } when there is no file, or { ok: false, kind }. */
export function readRecent(dir) {
  const file = path.join(dir, RECENT_FILE)
  let text
  try {
    const stat = fs.statSync(file)
    if (!stat.isFile() || stat.size > MAX_FILE_BYTES) return { ok: false, kind: 'unusable' }
    text = fs.readFileSync(file, 'utf8')
  } catch (error) {
    return error?.code === 'ENOENT' ? { ok: true, saved: null } : { ok: false, kind: kindOf(error) }
  }
  let value
  try {
    value = JSON.parse(text)
  } catch {
    return { ok: false, kind: 'malformed' }
  }
  const saved = parseSaved(value)
  return saved ? { ok: true, saved } : { ok: false, kind: 'malformed' }
}

/** Write `object` as the file, creating the folder when it is missing. { ok: true } or { ok: false, kind }. */
export function writeRecent(dir, object) {
  const file = path.join(dir, RECENT_FILE)
  const temp = path.join(dir, `${RECENT_FILE}.${process.pid}.tmp`)
  try {
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(temp, JSON.stringify(object), { encoding: 'utf8', mode: 0o600 })
    fs.renameSync(temp, file)
    return { ok: true }
  } catch (error) {
    try {
      fs.rmSync(temp, { force: true })
    } catch {
      // a leftover temporary file is harmless
    }
    return { ok: false, kind: kindOf(error) }
  }
}

/** Delete the file; a missing file is fine. { ok: true } or { ok: false, kind }. */
export function removeRecent(dir) {
  try {
    fs.rmSync(path.join(dir, RECENT_FILE), { force: true })
    return { ok: true }
  } catch (error) {
    return { ok: false, kind: kindOf(error) }
  }
}
