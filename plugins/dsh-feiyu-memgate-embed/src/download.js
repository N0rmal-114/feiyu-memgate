// Model downloads: one registry file at a time, through the host's fetch, verified before use.
//
// The design follows the host's SenseVoice plugin (packages/experimental/speech-to-text-sensevoice:
// README.md:28-34, src/model-sources.ts, src/runtime.ts:96-148, src/download-error.ts):
//   - files are revision-pinned registry entries; their size and SHA-256 are known in advance;
//   - the Hugging Face compatible origins (huggingface.co and hf-mirror.com by default) are compared
//     with concurrent HEAD requests and the first 2xx answer is tried first; when every probe fails
//     the configured order is kept; an explicit origin is used alone, without probing;
//   - network, HTTP, certificate and integrity failures move on to the next origin; cancellation and
//     storage failures stop;
//   - errors carry a reason, an origin and an HTTP status or error code, never a URL with a query,
//     a local path or a raw message.
// Two differences: a partial file is resumed with an HTTP Range request when the server allows it
// (SenseVoice restarts), and the free disk space is checked first when the platform reports it.
//
// Network: globalThis.fetch only. The host installs its proxy policy as the global dispatcher, so a
// plain fetch goes through the user's proxy (packages/util/http-proxy/README.md:32); building an Agent
// would bypass it (same README, :45). Nothing here constructs a dispatcher.
//
// Files: <modelsDir>/<id>/<file>. Bytes arrive in <file>.part; the .part file is renamed to the final
// name only after the size and the SHA-256 match, so a model file that exists has been verified once.
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'
import { Readable, Transform } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { fileUrl } from './models.js'

export const PART_SUFFIX = '.part'
const DISK_MARGIN_BYTES = 64 * 1024 * 1024
const PROBE_TIMEOUT_MS = 3000 // the SenseVoice default (speech-to-text-sensevoice/src/config.ts:193)
const STALL_TIMEOUT_MS = 60000
const SPEED_WINDOW_MS = 5000

/** A failed download. `reason` is one of the REASONS; nothing in it names a local path. */
export class DownloadError extends Error {
  constructor(reason, { source, status, code, cause } = {}) {
    super(`download failed: ${reason}${status ? ` (HTTP ${status})` : ''}${code ? ` (${code})` : ''}`, cause ? { cause } : undefined)
    this.name = 'DownloadError'
    this.reason = reason
    if (source) this.source = source
    if (status) this.status = status
    if (code) this.code = code
  }
}

export class DownloadBusyError extends Error {
  constructor(current) {
    super('another download is running')
    this.name = 'DownloadBusyError'
    this.current = current
  }
}

export const REASONS = Object.freeze(['http', 'network', 'dns', 'timeout', 'certificate', 'integrity', 'storage', 'disk-full', 'cancelled', 'unknown'])

const CODES = [
  ['dns', /^(ENOTFOUND|EAI_AGAIN)$/],
  ['timeout', /^(ETIMEDOUT|ERR_SOCKET_CONNECTION_TIMEOUT|UND_ERR_(CONNECT|HEADERS|BODY)_TIMEOUT)$/],
  ['certificate', /^(CERT_[A-Z_]+|ERR_TLS_CERT_ALTNAME_INVALID|DEPTH_ZERO_SELF_SIGNED_CERT|SELF_SIGNED_CERT_IN_CHAIN|UNABLE_TO_VERIFY_LEAF_SIGNATURE|UNABLE_TO_GET_ISSUER_CERT_LOCALLY)$/],
  ['disk-full', /^(ENOSPC|EDQUOT)$/],
  ['storage', /^(EACCES|EPERM|EROFS|EISDIR|ENOTDIR|EMFILE)$/],
  ['network', /^(ECONNREFUSED|ECONNRESET|ENETUNREACH|EHOSTUNREACH|EPIPE|UND_ERR_SOCKET)$/],
]

/** A reason and, when recognised, an error code for any failure (fetch causes are nested). */
export function classifyFailure(failure) {
  const pending = [failure]
  const seen = new Set()
  let reason = 'unknown'
  while (pending.length) {
    const error = pending.shift()
    if (!(error instanceof Error) || seen.has(error)) continue
    seen.add(error)
    if (error instanceof DownloadError) return { reason: error.reason, code: error.code }
    if (error.name === 'TimeoutError') return { reason: 'timeout' }
    if (typeof error.code === 'string') {
      for (const [kind, pattern] of CODES) if (pattern.test(error.code)) return { reason: kind, code: error.code }
    }
    if (error instanceof TypeError && error.message === 'fetch failed') reason = 'network'
    pending.push(error.cause)
    if (error instanceof AggregateError) pending.push(...error.errors)
  }
  return { reason }
}

export const modelFile = (modelsDir, model) => path.join(modelsDir, model.id, model.file)
export const partFile = (modelsDir, model) => modelFile(modelsDir, model) + PART_SUFFIX

/** SHA-256 and size of a file, streamed. */
export async function hashFile(file, { signal } = {}) {
  const hash = createHash('sha256')
  let size = 0
  for await (const chunk of fs.createReadStream(file, { signal, highWaterMark: 1024 * 1024 })) {
    hash.update(chunk)
    size += chunk.length
  }
  return { sha256: hash.digest('hex'), size }
}

/** True when the file exists with the entry's size and SHA-256. */
export async function verifyFile(file, model, { signal } = {}) {
  try {
    const info = await fsp.stat(file)
    if (!info.isFile() || info.size !== model.sizeBytes) return false
    return (await hashFile(file, { signal })).sha256 === model.sha256
  } catch (error) {
    if (error?.code === 'ENOENT') return false
    throw error
  }
}

/** Free bytes for a folder (or its nearest existing parent), or null when the platform cannot say. */
export async function freeBytes(dir, statfs = fsp.statfs) {
  if (typeof statfs !== 'function') return null
  let current = path.resolve(dir)
  for (;;) {
    try {
      const info = await statfs(current)
      const free = Number(info.bavail) * Number(info.bsize)
      return Number.isFinite(free) ? free : null
    } catch (error) {
      const parent = path.dirname(current)
      if (error?.code !== 'ENOENT' || parent === current) return null
      current = parent
    }
  }
}

/**
 * The download URLs of an entry, best first: concurrent HEAD requests through `fetch`, the first 2xx
 * answer first (speech-to-text-sensevoice/src/model-sources.ts). One origin needs no probe; when every
 * probe fails the configured order is kept.
 */
export async function orderSources(model, origins, { fetch = globalThis.fetch, timeoutMs = PROBE_TIMEOUT_MS, signal } = {}) {
  signal?.throwIfAborted()
  const urls = [...new Set(origins.map((origin) => fileUrl(model, origin)))]
  if (urls.length <= 1) return urls
  const finished = new AbortController()
  const timeout = AbortSignal.timeout(timeoutMs)
  const probing = AbortSignal.any([timeout, finished.signal, ...(signal ? [signal] : [])])
  const requests = urls.map(async (url) => ({ url, response: await fetch(url, { method: 'HEAD', signal: probing, redirect: 'follow' }) }))
  let preferred
  try {
    preferred = await Promise.any(requests.map(async (request) => {
      const { url, response } = await request
      if (!response.ok) throw new Error('probe refused')
      return url
    }))
  } catch {
    // An origin may refuse HEAD or be slow to answer and still serve the download.
  } finally {
    finished.abort()
    const settled = await Promise.allSettled(requests)
    await Promise.allSettled(settled.map(async (r) => {
      if (r.status === 'fulfilled') await r.value.response.body?.cancel()
    }))
  }
  signal?.throwIfAborted()
  return preferred === undefined ? urls : [preferred, ...urls.filter((url) => url !== preferred)]
}

const CONTENT_RANGE = /^bytes (\d+)-(\d+)\/(\d+|\*)$/

/**
 * Download one entry from one URL into its .part file, resuming what is there, then verify and
 * publish it. Resolves with { sha256, sizeBytes, mtimeMs, resumedFrom } of the published file.
 */
export async function downloadFrom(url, model, { modelsDir, fetch = globalThis.fetch, signal, onProgress = () => {}, stallTimeoutMs = STALL_TIMEOUT_MS } = {}) {
  const final = modelFile(modelsDir, model)
  const part = partFile(modelsDir, model)
  let source = new URL(url).origin
  await fsp.mkdir(path.dirname(final), { recursive: true })

  let have = 0
  try {
    have = (await fsp.stat(part)).size
  } catch {
    have = 0
  }
  let hash = createHash('sha256')
  if (have > model.sizeBytes) {
    await fsp.rm(part, { force: true })
    have = 0
  } else if (have > 0) {
    // The bytes already on disk join the hash, so the whole file is verified at the end.
    try {
      for await (const chunk of fs.createReadStream(part, { signal, highWaterMark: 1024 * 1024 })) hash.update(chunk)
    } catch (error) {
      if (signal?.aborted) throw error
      await fsp.rm(part, { force: true })
      have = 0
      hash = createHash('sha256')
    }
  }
  const resumedFrom = have
  let bytes = have
  onProgress(bytes)

  if (have < model.sizeBytes) {
    const stall = new AbortController()
    let timer = null
    const arm = () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => stall.abort(), stallTimeoutMs)
      timer.unref?.()
    }
    const combined = signal ? AbortSignal.any([signal, stall.signal]) : stall.signal
    try {
      arm()
      let response
      try {
        response = await fetch(url, { headers: have > 0 ? { range: `bytes=${have}-` } : {}, signal: combined, redirect: 'follow' })
      } catch (error) {
        if (stall.signal.aborted && !signal?.aborted) throw new DownloadError('timeout', { source })
        throw error
      }
      try {
        source = new URL(response.url || url).origin
      } catch {
        // keep the origin we asked
      }
      let append = false
      if (response.status === 206 && have > 0) {
        const match = CONTENT_RANGE.exec(response.headers.get('content-range') ?? '')
        if (!match || Number(match[1]) !== have || (match[3] !== '*' && Number(match[3]) !== model.sizeBytes)) {
          await response.body?.cancel()
          await fsp.rm(part, { force: true })
          throw new DownloadError('integrity', { source })
        }
        append = true
      } else if (response.status === 200) {
        // The server sent the whole file (no Range support): start over.
        hash = createHash('sha256')
        bytes = 0
      } else {
        await response.body?.cancel()
        throw new DownloadError('http', { source, status: response.status })
      }
      const expected = model.sizeBytes - bytes
      const length = response.headers.get('content-length')
      if (length !== null && length !== '' && Number(length) !== expected) {
        await response.body?.cancel()
        throw new DownloadError('integrity', { source })
      }
      if (!response.body) throw new DownloadError('http', { source, status: response.status })
      const counter = new Transform({
        transform(chunk, _encoding, callback) {
          bytes += chunk.length
          if (bytes > model.sizeBytes) {
            callback(new DownloadError('integrity', { source }))
            return
          }
          arm()
          hash.update(chunk)
          onProgress(bytes)
          callback(null, chunk)
        },
      })
      try {
        await pipeline(Readable.fromWeb(response.body), counter, fs.createWriteStream(part, { flags: append ? 'a' : 'w', mode: 0o600 }), { signal: combined })
      } catch (error) {
        if (error instanceof DownloadError) {
          if (error.reason === 'integrity') await fsp.rm(part, { force: true })
          throw error
        }
        if (stall.signal.aborted && !signal?.aborted) throw new DownloadError('timeout', { source })
        throw error
      }
    } finally {
      if (timer) clearTimeout(timer)
    }
  }

  signal?.throwIfAborted()
  const sha256 = hash.digest('hex')
  if (bytes !== model.sizeBytes || sha256 !== model.sha256) {
    await fsp.rm(part, { force: true })
    throw new DownloadError('integrity', { source })
  }
  await fsp.rename(part, final)
  const info = await fsp.stat(final)
  return { sha256, sizeBytes: info.size, mtimeMs: info.mtimeMs, resumedFrom, source }
}

/**
 * One download job at a time.
 *   fetch, statfs, probeTimeoutMs, stallTimeoutMs: replaceable for tests
 *   onChange(job): called when a job starts, finishes, fails or is cancelled (not per chunk)
 * Returns { start, cancel, progress, last, busy, dispose }.
 */
export function createDownloader({
  fetch = globalThis.fetch, statfs = fsp.statfs, probeTimeoutMs = PROBE_TIMEOUT_MS, stallTimeoutMs = STALL_TIMEOUT_MS,
  onChange = () => {}, now = Date.now,
} = {}) {
  let job = null
  let last = null

  const notify = (value) => {
    try {
      onChange(value)
    } catch {
      // a listener failure must not stop a download
    }
  }

  function speedOf(current) {
    const samples = current.samples
    if (samples.length < 2) return 0
    const [t0, b0] = samples[0]
    const [t1, b1] = samples[samples.length - 1]
    return t1 > t0 ? Math.max(0, Math.round(((b1 - b0) * 1000) / (t1 - t0))) : 0
  }

  function view(current) {
    return {
      id: current.model.id, file: current.model.file, state: current.state, bytes: current.bytes, total: current.model.sizeBytes,
      speed: speedOf(current), source: current.source ?? null, startedAt: current.startedAt, resumedFrom: current.resumedFrom ?? 0,
    }
  }

  async function run(current, { modelsDir, origins, onDone }) {
    const { model, controller } = current
    const signal = controller.signal
    const progress = (bytes) => {
      current.bytes = bytes
      const t = now()
      current.samples.push([t, bytes])
      while (current.samples.length > 2 && t - current.samples[0][0] > SPEED_WINDOW_MS) current.samples.shift()
    }
    current.state = 'checking'
    const final = modelFile(modelsDir, model)
    if (await verifyFile(final, model, { signal })) {
      const info = await fsp.stat(final)
      return { sha256: model.sha256, sizeBytes: info.size, mtimeMs: info.mtimeMs, resumedFrom: 0, source: null }
    }
    let have = 0
    try {
      have = (await fsp.stat(partFile(modelsDir, model))).size
    } catch {
      have = 0
    }
    const free = await freeBytes(path.dirname(final), statfs)
    if (free !== null && free < model.sizeBytes - Math.min(have, model.sizeBytes) + DISK_MARGIN_BYTES) throw new DownloadError('disk-full')
    current.state = 'probing'
    const urls = await orderSources(model, origins, { fetch, timeoutMs: probeTimeoutMs, signal })
    let lastError = null
    for (const [index, url] of urls.entries()) {
      // A resumed file that fails verification is retried once from the start on the same origin.
      for (let attempt = 0; attempt < 2; attempt++) {
        current.state = 'downloading'
        current.source = new URL(url).origin
        let resumed = 0
        try {
          const result = await downloadFrom(url, model, {
            modelsDir, fetch, signal, stallTimeoutMs,
            onProgress: (bytes) => {
              if (current.samples.length === 0) resumed = bytes
              current.resumedFrom = resumed
              progress(bytes)
            },
          })
          return result
        } catch (error) {
          if (signal.aborted) throw new DownloadError('cancelled')
          const { reason, code } = classifyFailure(error)
          lastError = error instanceof DownloadError ? error : new DownloadError(reason, { source: current.source, code, cause: error })
          if (reason === 'storage' || reason === 'disk-full' || reason === 'unknown') throw lastError
          if (reason === 'integrity' && resumed > 0 && attempt === 0) {
            current.samples = []
            continue
          }
          break
        }
      }
      if (index === urls.length - 1) throw lastError
    }
    throw lastError ?? new DownloadError('unknown')
  }

  return {
    /**
     * Start downloading `model` in the background. Throws DownloadBusyError while another job runs.
     *   origins  ordered origins (one for an explicit origin)
     *   onDone(model, result) is awaited before the job counts as done
     */
    start(model, { modelsDir, origins, onDone = async () => {} }) {
      if (job) throw new DownloadBusyError(view(job))
      const current = { model, controller: new AbortController(), state: 'starting', bytes: 0, samples: [], startedAt: now(), source: null }
      job = current
      notify({ ...view(current) })
      current.promise = (async () => {
        try {
          const result = await run(current, { modelsDir, origins })
          current.state = 'verifying'
          await onDone(model, result)
          // `source` stays the origin the job chose (one of the configured origins), as it was while
          // running: an origin may redirect the file to its CDN (huggingface.co does), and that host
          // is not something the user can choose.
          last = { id: model.id, state: 'done', at: now(), source: current.source ?? result.source }
        } catch (error) {
          const cancelled = current.controller.signal.aborted
          const { reason, code } = cancelled ? { reason: 'cancelled' } : classifyFailure(error)
          last = { id: model.id, state: cancelled ? 'cancelled' : 'failed', reason, code, status: error?.status, source: current.source ?? error?.source ?? null, at: now() }
        } finally {
          job = null
          notify({ ...last })
        }
        return last
      })()
      return view(current)
    },
    /** Cancel the running job; the .part file stays for a later resume. */
    cancel() {
      if (!job) return false
      job.controller.abort()
      return true
    },
    /** The running job's progress (bytes, total, speed in bytes per second), or null. */
    progress: () => (job ? view(job) : null),
    /** The outcome of the last finished job, or null. */
    last: () => last,
    busy: () => job !== null,
    /** Resolves when the running job (if any) has settled. */
    settled: () => job?.promise ?? Promise.resolve(last),
    async dispose() {
      if (job) {
        job.controller.abort()
        await job.promise.catch(() => {})
      }
    },
  }
}
