// Client for the feiyu-memgate gateway, over fetch. Every method resolves, none rejects.
//
// A port of the gateway's Python client (client/http.py). Out of scope in this version:
// tenants (tenant_id, the tenant token, tenant-bound clients) and from_env. A plugin talks to
// one gateway with one token.
//
// Logs go through logger.warn and name only the kind of failure ("http 500 busy", "timeout"):
// never the query, a memory text, an item id or the token. The error code of an answer is
// logged only when it is a short plain identifier, so a server cannot smuggle text into the log.
//
// Nothing is ever cut to fit: a query or a message over the gateway's limits is not sent at
// all (recallDetail gives null, remember gives 'failed'). Error answers are told apart by their
// stable "code", never by their text.
//
// Known, deliberate differences from the Python client:
//   - fetch does not expose "connected, not yet sent", so a deadline that fires before any answer
//     is 'unsure' for a write (Python says 'failed' if it fires while still connecting); a refused
//     connection or a failed name lookup is still 'failed'.
//   - JSON.parse reads 3.0 as the integer 3 and rejects NaN/Infinity, which Python accepts or
//     treats as a float; the gateway sends neither.
import { codePointLength, pyStrip } from './query.js'

const MAX_QUERY_CHARS = 500 // gateway limit for /v1/recall
const MAX_REMEMBER_CHARS = 4000 // gateway limit per message for /v1/remember
const MAX_TOTAL_CHARS = 16000 // gateway limit for all messages of one /v1/remember
const MAX_MESSAGES = 20 // gateway limit of messages per /v1/remember
const MAX_TIME_CHARS = 40 // gateway limit of a message's optional "time"
const MAX_SPEAKER_CHARS = 64 // gateway limit of a message's optional "speaker"
const MAX_USED = 20 // gateway limit of items per /v1/used
const MAX_RESPONSE_BYTES = 256 * 1024
const MAX_ERROR_BYTES = 4096

// Outcomes of one POST. NOT_SENT and REJECTED are definite failures; UNSURE means the request
// may have reached the gateway and done its work. LIMITED: the day's write cap is used up.
const OK = 'ok'
const NOT_SENT = 'not_sent'
const REJECTED = 'rejected'
const UNSURE = 'unsure'
const LIMITED = 'limited'

// Error codes that say for sure nothing was stored (besides every bad_* code).
const DEFINITE_CODES = new Set([
  'guard_error', 'forbidden', 'unknown_tenant', 'tenant_mismatch', 'too_large', 'unauthorized',
  'rate_limited', 'busy', 'length_required', 'not_found', 'method_not_allowed',
])

// Errors that happen before a request leaves this machine.
const PRE_SEND_CODES = new Set([
  'ECONNREFUSED', 'ENOTFOUND', 'EAI_AGAIN', 'EHOSTUNREACH', 'ENETUNREACH', 'EADDRNOTAVAIL',
  'UND_ERR_CONNECT_TIMEOUT', 'ERR_INVALID_URL',
])

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)

function failure(status, code) {
  if (code === 'daily_limit') return LIMITED
  if (code !== null && (DEFINITE_CODES.has(code) || code.startsWith('bad_'))) return REJECTED
  return status >= 400 && status < 500 ? REJECTED : UNSURE
}

const count = (value) => (Number.isInteger(value) && value > 0 ? value : 0)

// The messages as the gateway takes them, or null when one breaks a gateway limit.
function cleanMessages(messages) {
  if (!Array.isArray(messages) || messages.length === 0 || messages.length > MAX_MESSAGES) return null
  const clean = []
  let total = 0
  for (const m of messages) {
    if (!isObject(m) || (m.role !== 'user' && m.role !== 'assistant')) return null
    const content = m.content
    if (typeof content !== 'string' || !pyStrip(content)) return null
    const length = codePointLength(content)
    if (length > MAX_REMEMBER_CHARS) return null
    total += length
    if (total > MAX_TOTAL_CHARS) return null
    const item = { role: m.role, content }
    for (const [key, cap] of [['time', MAX_TIME_CHARS], ['speaker', MAX_SPEAKER_CHARS]]) {
      const value = m[key]
      if (value === null || value === undefined) continue
      if (typeof value !== 'string' || codePointLength(value) > cap) return null
      item[key] = value
    }
    clean.push(item)
  }
  return clean
}

function normaliseBase(url) {
  if (typeof url !== 'string') return null
  const trimmed = pyStrip(url).replace(/\/+$/, '')
  if (!trimmed) return null
  try {
    const parsed = new URL(trimmed)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null
    // Like the Python client: scheme, host, port and path; no credentials, query or fragment.
    return parsed.origin + parsed.pathname.replace(/\/+$/, '')
  } catch {
    return null
  }
}

function failureKind(error) {
  const code = error?.cause?.code ?? error?.code
  if (typeof code === 'string' && /^[A-Z0-9_]{1,40}$/.test(code)) return code
  const name = error?.name
  return typeof name === 'string' && /^[A-Za-z]{1,40}$/.test(name) ? name : 'error'
}

// The body of a reply, at most `cap` bytes. Throws on overflow, on a broken stream and (through
// the abort signal) when the deadline passes.
async function readBody(response, cap) {
  const length = Number(response.headers.get('content-length'))
  if (Number.isFinite(length) && length > cap) {
    await response.body?.cancel().catch(() => {})
    throw new RangeError('response too large')
  }
  if (!response.body) return Buffer.alloc(0)
  const reader = response.body.getReader()
  const chunks = []
  let size = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    size += value.byteLength
    if (size > cap) {
      await reader.cancel().catch(() => {})
      throw new RangeError('response too large')
    }
    chunks.push(value)
  }
  return Buffer.concat(chunks)
}

async function errorCode(response) {
  try {
    const data = JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await readBody(response, MAX_ERROR_BYTES)))
    return isObject(data) && typeof data.code === 'string' ? data.code : null
  } catch {
    return null
  }
}

export function createClient({ url, token, fetch: fetchImpl = globalThis.fetch, logger, recallLimit = 5 } = {}) {
  const base = normaliseBase(url)
  const secret = typeof token === 'string' ? pyStrip(token) : ''
  const configured = Boolean(base && secret)

  function warn(message) {
    try {
      logger?.warn?.(message)
    } catch {
      // a broken logger must not break a call
    }
  }

  // One request until an absolute deadline (timeoutMs from now). Returns [object|null, outcome].
  async function request(method, path, payload, timeoutMs, what, signal, withToken) {
    if (!base || (withToken && !secret)) return [null, NOT_SENT]
    if (!(Number.isFinite(timeoutMs) && timeoutMs > 0)) {
      warn(`memory ${what} not sent: deadline passed`)
      return [null, NOT_SENT]
    }
    if (signal?.aborted) {
      warn(`memory ${what} not sent: cancelled`)
      return [null, NOT_SENT]
    }
    const controller = new AbortController()
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, timeoutMs)
    const onAbort = () => controller.abort()
    signal?.addEventListener('abort', onAbort, { once: true })
    let sent = false
    try {
      let init
      try {
        // Header values and the body are checked before anything leaves: a token with characters
        // HTTP cannot carry fails here, as 'not sent'.
        const headers = new Headers()
        if (withToken) headers.set('authorization', 'Bearer ' + secret)
        let body
        if (payload !== null) {
          headers.set('content-type', 'application/json')
          // UTF-8 as it is: \uXXXX escapes would triple the size of Chinese text and could push a
          // message that is within the limits over the gateway's 64 KiB body cap.
          body = new TextEncoder().encode(JSON.stringify(payload))
        }
        // redirect: 'error': a redirect would carry the token to another host.
        init = { method, headers, body, redirect: 'error', signal: controller.signal }
      } catch {
        warn(`memory ${what} not sent: bad request`)
        return [null, NOT_SENT]
      }
      sent = true
      const response = await fetchImpl(base + path, init)
      if (response.status !== 200) {
        const code = await errorCode(response)
        const shown = code !== null && /^[a-z0-9_]{1,40}$/.test(code) ? code : '-'
        warn(`memory ${what} failed: http ${response.status} ${shown}`)
        await response.body?.cancel().catch(() => {})
        return [null, failure(response.status, code)]
      }
      // One deadline for the whole call: the abort signal also ends a body that drips in slowly.
      const data = JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await readBody(response, MAX_RESPONSE_BYTES)))
      if (!isObject(data)) throw new TypeError('not an object')
      return [data, OK]
    } catch (error) {
      const kind = timedOut ? 'timeout' : signal?.aborted ? 'cancelled' : failureKind(error)
      warn(`memory ${what} failed: ${kind}`)
      const code = error?.cause?.code ?? error?.code
      const beforeSend = !timedOut && !signal?.aborted && typeof code === 'string' && PRE_SEND_CODES.has(code)
      return [null, sent && !beforeSend ? UNSURE : NOT_SENT]
    } finally {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
    }
  }

  async function recallDetail(query, { timeoutMs = 8500, signal } = {}) {
    try {
      if (typeof query !== 'string' || !pyStrip(query)) return null
      if (codePointLength(query) > MAX_QUERY_CHARS) {
        warn(`memory recall not sent: query over ${MAX_QUERY_CHARS} characters`)
        return null
      }
      const [data] = await request('POST', '/v1/recall', { query, limit: recallLimit }, timeoutMs, 'recall', signal, true)
      const results = data?.results
      if (!Array.isArray(results)) return null
      return {
        results: results.filter((r) => isObject(r) && typeof r.text === 'string'),
        more: count(data.more),
        failed: count(data.failed),
        busy: count(data.busy),
      }
    } catch {
      return null
    }
  }

  async function rememberMessages(messages, { timeoutMs = 12000, signal } = {}) {
    try {
      const clean = cleanMessages(messages)
      if (clean === null) {
        warn("memory remember not sent: messages break the gateway's limits")
        return 'failed'
      }
      const [data, outcome] = await request('POST', '/v1/remember', { messages: clean }, timeoutMs, 'remember', signal, true)
      if (outcome === OK) return data.status === 'ok' ? 'saved' : data.status === 'skipped' ? 'skipped' : 'unsure'
      return outcome === UNSURE ? 'unsure' : outcome === LIMITED ? 'limited' : 'failed'
    } catch {
      return 'failed'
    }
  }

  // opts.time: the message's own time ("2026-11-17T20:15:03+09:00", at most 40 characters), which the
  // gateway uses to date the memory. Not a Python option: the Python client's remember() takes no time.
  async function rememberStatus(text, opts) {
    if (typeof text !== 'string' || !pyStrip(text)) return 'failed'
    const message = { role: 'user', content: text }
    if (typeof opts?.time === 'string') message.time = opts.time
    return rememberMessages([message], opts)
  }

  async function reportUsed(items, { timeoutMs = 5000, signal } = {}) {
    try {
      if (!Array.isArray(items) || items.length === 0 || items.length > MAX_USED) {
        warn(`memory used-report not sent: need 1 to ${MAX_USED} items`)
        return false
      }
      const clean = []
      for (const item of items) {
        const source = isObject(item) ? item.source : null
        const id = isObject(item) ? item.id : null
        if (typeof source !== 'string' || !source || typeof id !== 'string' || !id) {
          warn('memory used-report not sent: an item has no source or id')
          return false
        }
        clean.push({ source, id })
      }
      const [data, outcome] = await request('POST', '/v1/used', { used: clean }, timeoutMs, 'used', signal, true)
      return outcome === OK && data.status === 'ok'
    } catch {
      return false
    }
  }

  // GET /healthz needs no token and none is sent. null when the gateway does not answer 200.
  async function health({ deep = false, timeoutMs = 2000, signal } = {}) {
    try {
      const [data] = await request('GET', deep ? '/healthz?deep=1' : '/healthz', null, timeoutMs, 'health', signal, false)
      return data
    } catch {
      return null
    }
  }

  return { configured, recallDetail, rememberStatus, rememberMessages, reportUsed, health }
}
