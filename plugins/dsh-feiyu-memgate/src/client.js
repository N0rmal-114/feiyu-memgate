// Client for the feiyu-memgate gateway, over fetch. Every method resolves, none rejects.
//
// A port of the gateway's Python client (client/http.py). Out of scope in this version:
// tenants (tenant_id, the tenant token, tenant-bound clients) and from_env. A plugin talks to
// one gateway with one token. recall and remember take an optional "context" (a small flat
// object of labels) and send it as the request's "context" when it passes the gateway's own
// check; one that does not is dropped with a warning, and the request goes out without it.
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
const MAX_SPEAKER_CHARS = 64 // gateway limit of a message's optional "speaker" and "about"
const MAX_USED = 20 // gateway limit of items per /v1/used
const MAX_CONTEXT_KEYS = 16 // gateway limit of keys in a request's optional "context"
const MAX_CONTEXT_KEY_CHARS = 64 // gateway limit of a context key's length
const MAX_CONTEXT_VALUE_CHARS = 200 // gateway limit of a context string value's length
// The optional message keys that become record fields of the memory (the gateway's REQUEST_FIELDS).
const CHANNEL = /^[a-z0-9][a-z0-9_.:-]{0,63}$/
const ORIGINS = ['said', 'tool', 'curated', 'derived']
export const KINDS = ['identity', 'preference', 'event', 'plan', 'entity']
const FIELD_KEYS = ['speaker', 'about', 'channel', 'audience', 'origin', 'event_date', 'kind'] // the keys of a row's "fields"
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

// Mirrors the gateway's _valid_context: a plain object of at most MAX_CONTEXT_KEYS keys, each key
// a non-empty string of at most MAX_CONTEXT_KEY_CHARS characters, each value a boolean, a finite
// number or a string of at most MAX_CONTEXT_VALUE_CHARS characters.
export function validContext(value) {
  if (!isObject(value)) return false
  const proto = Object.getPrototypeOf(value)
  if (proto !== Object.prototype && proto !== null) return false
  const keys = Object.keys(value)
  if (keys.length > MAX_CONTEXT_KEYS) return false
  for (const key of keys) {
    if (!key || codePointLength(key) > MAX_CONTEXT_KEY_CHARS) return false
    const item = value[key]
    if (typeof item === 'boolean') continue
    if (typeof item === 'number') {
      if (!Number.isFinite(item)) return false
      continue
    }
    if (typeof item !== 'string' || codePointLength(item) > MAX_CONTEXT_VALUE_CHARS) return false
  }
  return true
}

// A real calendar date written YYYY-MM-DD and nothing else (the gateway's _valid_event_date).
export function validEventDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  if (year < 1 || month < 1 || month > 12 || day < 1) return false
  return day <= new Date(Date.UTC(2000 + (year % 400), month, 0)).getUTCDate()
}

// Whether `value` is acceptable as the optional message key `key` (the gateway's _valid_field).
function validField(key, value) {
  if (typeof value !== 'string') return false
  if (key === 'channel') return CHANNEL.test(value)
  if (key === 'event_date') return validEventDate(value)
  return (key === 'origin' ? ORIGINS : KINDS).includes(value)
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
    for (const key of ['channel', 'origin', 'event_date', 'kind']) {
      const value = m[key]
      if (value === null || value === undefined) continue
      if (!validField(key, value)) return null
      item[key] = value
    }
    const about = m.about
    if (about !== null && about !== undefined) {
      if (typeof about !== 'string' || codePointLength(about) > MAX_SPEAKER_CHARS) return null
      item.about = about
    }
    clean.push(item)
  }
  return clean
}

const OUTCOMES = new Set(['delivered', 'dropped', 'deferred', 'over_limit', 'not_reached'])

// One entry of an explained recall as the page may rely on it, or null when it is not well formed.
function cleanCandidate(c) {
  if (!isObject(c) || typeof c.source !== 'string' || typeof c.id !== 'string') return null
  if (!Number.isFinite(c.found) || !Number.isFinite(c.score) || !OUTCOMES.has(c.outcome)) return null
  const out = {
    source: c.source,
    id: c.id,
    found: c.found,
    score: c.score,
    outcome: c.outcome,
    stage: typeof c.stage === 'string' ? c.stage : null,
    addedBy: typeof c.added_by === 'string' ? c.added_by : null,
  }
  if (typeof c.text === 'string') out.text = c.text
  if (typeof c.date === 'string') out.date = c.date
  if (c.cut === true) out.cut = true
  if (isObject(c.fields)) {
    const fields = {}
    for (const key of FIELD_KEYS) if (typeof c.fields[key] === 'string') fields[key] = c.fields[key]
    if (Object.keys(fields).length) out.fields = fields
  }
  return out
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

export function createClient({ url, token, fetch: fetchImpl = globalThis.fetch, logger, recallLimit = 8 } = {}) {
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

  // The optional "speaker" of a recall: a string of at most MAX_SPEAKER_CHARS characters goes into the body,
  // anything else is left out (a label never makes a call fail, as with the context).
  function withSpeaker(body, speaker) {
    return typeof speaker === 'string' && speaker && codePointLength(speaker) <= MAX_SPEAKER_CHARS ? { ...body, speaker } : body
  }

  // { context } to add to a body: nothing when absent, nothing (and one warning that does not show
  // it) when invalid. A label never makes a call fail.
  function withContext(body, context, what) {
    if (context === undefined || context === null) return body
    if (!validContext(context)) {
      warn(`memory ${what}: context dropped, it breaks the gateway's limits`)
      return body
    }
    return { ...body, context: { ...context } }
  }

  // One request until an absolute deadline (timeoutMs from now). Returns [object|null, outcome, status]; status is the HTTP status of an error answer, else absent.
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
        return [null, failure(response.status, code), response.status]
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

  async function recallDetail(query, { timeoutMs = 8500, signal, context, speaker } = {}) {
    try {
      if (typeof query !== 'string' || !pyStrip(query)) return null
      if (codePointLength(query) > MAX_QUERY_CHARS) {
        warn(`memory recall not sent: query over ${MAX_QUERY_CHARS} characters`)
        return null
      }
      const [data] = await request('POST', '/v1/recall', withContext(withSpeaker({ query, limit: recallLimit }, speaker), context, 'recall'), timeoutMs, 'recall', signal, true)
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

  // POST /v1/explain: a recall with the reasons. { ok: true, stages, candidates, more, failed, busy,
  // cut } or { ok: false, code }: 'unsupported' when the gateway answers 403, 404 or 405 (an older
  // gateway, or explain is off for this client), 'failed' for anything else.
  async function explainRecall(query, { timeoutMs = 8500, signal, context, limit = recallLimit, speaker } = {}) {
    try {
      if (typeof query !== 'string' || !pyStrip(query)) return { ok: false, code: 'failed' }
      if (codePointLength(query) > MAX_QUERY_CHARS) {
        warn(`memory explain not sent: query over ${MAX_QUERY_CHARS} characters`)
        return { ok: false, code: 'failed' }
      }
      const [data, , status] = await request('POST', '/v1/explain', withContext(withSpeaker({ query, limit }, speaker), context, 'explain'), timeoutMs, 'explain', signal, true)
      if (status === 403 || status === 404 || status === 405) return { ok: false, code: 'unsupported' }
      if (!data || !Array.isArray(data.candidates)) return { ok: false, code: 'failed' }
      return {
        ok: true,
        stages: Array.isArray(data.stages) ? data.stages.filter((s) => typeof s === 'string') : [],
        candidates: data.candidates.map(cleanCandidate).filter((c) => c !== null),
        more: count(data.more),
        failed: count(data.failed),
        busy: count(data.busy),
        cut: data.candidates_cut === true,
      }
    } catch {
      return { ok: false, code: 'failed' }
    }
  }

  async function rememberMessages(messages, { timeoutMs = 12000, signal, context } = {}) {
    try {
      const clean = cleanMessages(messages)
      if (clean === null) {
        warn("memory remember not sent: messages break the gateway's limits")
        return 'failed'
      }
      const [data, outcome] = await request('POST', '/v1/remember', withContext({ messages: clean }, context, 'remember'), timeoutMs, 'remember', signal, true)
      if (outcome === OK) return data.status === 'ok' ? 'saved' : data.status === 'skipped' ? 'skipped' : 'unsure'
      return outcome === UNSURE ? 'unsure' : outcome === LIMITED ? 'limited' : 'failed'
    } catch {
      return 'failed'
    }
  }

  // opts.time: the message's own time ("2026-11-17T20:15:03+09:00", at most 40 characters), which the
  // gateway uses to date the memory. Not a Python option: the Python client's remember() takes no time.
  // opts.speaker, opts.origin, opts.kind, opts.eventDate: the message's own "speaker", "origin", "kind" and
  // "event_date" when given; the gateway checks them as cleanMessages does.
  async function rememberStatus(text, opts) {
    if (typeof text !== 'string' || !pyStrip(text)) return 'failed'
    const message = { role: 'user', content: text }
    if (typeof opts?.time === 'string') message.time = opts.time
    if (typeof opts?.speaker === 'string') message.speaker = opts.speaker
    if (typeof opts?.origin === 'string') message.origin = opts.origin
    if (typeof opts?.kind === 'string') message.kind = opts.kind
    if (typeof opts?.eventDate === 'string') message.event_date = opts.eventDate
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

  return { configured, recallDetail, explainRecall, rememberStatus, rememberMessages, reportUsed, health }
}
