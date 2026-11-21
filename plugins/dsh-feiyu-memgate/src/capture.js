// Automatic capture (modules.autoCapture): what the user types during a turn is kept in a list per
// session and, when the turn ends, sent to the gateway in batches within its limits. Only the user's
// own messages (never injected context, goal rounds or recall blocks), and only those worth a lookup.
// Fire and forget: a failure never reaches the turn. Logs name kinds of failure only, never a message.
import { buildQuery, codePointLength, meaningful, pyStrip } from './query.js'
import { isObject, isSubagent, isUserOrigin, kindOf, MAX_REMEMBER_CHARS, REQUEST_CONTEXT, textOf } from './shared.js'
import { localTime } from './time.js'

const MAX_BATCH_MESSAGES = 20 // gateway limits of one /v1/remember
const MAX_BATCH_CHARS = 16000

/** Split messages into batches within the gateway's limits. A message over the per-message limit is
 * left out, never cut. */
export function captureBatches(messages) {
  const batches = []
  let batch = []
  let total = 0
  for (const m of messages) {
    const length = codePointLength(m.content)
    if (length > MAX_REMEMBER_CHARS) continue
    if (batch.length && (batch.length >= MAX_BATCH_MESSAGES || total + length > MAX_BATCH_CHARS)) {
      batches.push(batch)
      batch = []
      total = 0
    }
    batch.push(m)
    total += length
  }
  if (batch.length) batches.push(batch)
  return batches
}

/**
 * @param {object} deps
 * @param {Function} deps.client     client(limit): a gateway client, or null while memory is unavailable
 * @param {Function} deps.settings   the current settings
 * @param {Function} deps.formatter  formatter(lang): the block formatter (its strip() removes recall blocks)
 * @param {Function} deps.clock      the clock: a Date, milliseconds or null
 * @param {object}   deps.log        { warn, info }
 * @returns {{ onSessionEvent: Function, onSessionDisposed: Function, clear: Function }}
 */
export function createCapture({ client, settings, formatter, clock, log }) {
  const captured = new Map() // session id -> user messages of the running turn

  function sendCaptured(messages) {
    const c = client()
    if (!c) {
      log.warn('feiyu-memgate: capture skipped (memory unavailable)')
      return
    }
    for (const batch of captureBatches(messages)) {
      // Fire and forget: the turn is over and nothing waits for this.
      Promise.resolve()
        .then(() => c.rememberMessages(batch, { context: REQUEST_CONTEXT.capture }))
        .catch((error) => log.warn(`feiyu-memgate: capture failed (${kindOf(error)})`))
    }
  }

  // A message is kept out of automatic capture when it is not worth a lookup either (a command, only
  // filler: buildQuery says so), or when it has fewer meaningful characters than advanced.captureMinChars
  // (0 turns the length test off).
  function worthCapturing(content) {
    if (buildQuery(content, null) === null) return false
    const min = settings().advanced.captureMinChars
    return min === 0 || codePointLength(meaningful(content)) >= min
  }

  function onSessionEvent(session, event) {
    try {
      if (!isObject(event) || isSubagent(session?.header)) return
      const id = session?.id ?? session?.header?.id
      if (id === undefined) return
      if (event.type === 'user/message') {
        // Only what the user typed: injected context, goal rounds and recall blocks have other sources.
        if (!isUserOrigin(event.data)) return
        const content = pyStrip(formatter().strip(textOf(event.data)))
        if (!content || !worthCapturing(content)) return
        const list = captured.get(id) ?? []
        const time = localTime(clock())
        list.push(time ? { role: 'user', content, time } : { role: 'user', content })
        captured.set(id, list)
      } else if (event.type === 'turn/end') {
        const list = captured.get(id)
        captured.delete(id)
        if (list?.length) sendCaptured(list)
      }
    } catch (error) {
      log.warn(`feiyu-memgate: capture skipped (${kindOf(error)})`)
    }
  }

  function onSessionDisposed(session) {
    captured.delete(session?.id ?? session?.header?.id)
  }

  return { onSessionEvent, onSessionDisposed, clear: () => captured.clear() }
}
