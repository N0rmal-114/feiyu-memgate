// The second lookup (advanced.afterthought): at the end of a turn the reply is the query of one more
// lookup, whose finds are kept for the next turn (src/pool.js offers them). Also where the end of a turn
// settles the conversation pool and the recent pool, and where a session's state is dropped when the
// session goes. Fire and forget; the lookups it has running are its own and are all stopped by
// stopLookups(). Logs name kinds of failure only, never a reply, a query or a memory.
import { buildQuery } from './query.js'
import * as pool from './pool.js'
import { FORM_NOTICE, isObject, isSubagent, isUserOrigin, kindOf, REQUEST_CONTEXT, sessionIdOf, SOURCE_KIND, speakerAccount, textOf, within } from './shared.js'

/**
 * @param {object} deps
 * @param {object}   deps.chat        the conversation pool (pool.createPool()), shared with the turn's recall
 * @param {Function} deps.client      client(limit): a gateway client, or null while memory is unavailable
 * @param {Function} deps.settings    the current settings
 * @param {Function} deps.formatter   formatter(lang): the block formatter (seen() recognises blocks)
 * @param {object}   deps.log         { warn, info }
 * @param {Function} deps.isDisposed  whether the plugin has been disposed
 * @param {Function} deps.saveRecent  saveRecent(id, state, settings): write the recent pool at the end of a turn
 * @returns {{ onTurnEnd: Function, onChatDisposed: Function, stopLookups: Function }}
 */
export function createFollowup({ chat, client, settings, formatter, log, isDisposed, saveRecent }) {
  const lookups = new Map() // session id -> AbortController of the second lookup that is running
  let replyWarned = false

  // The history of a session as the plugin reads it, or null when this host cannot give it.
  function historyOf(session) {
    if (typeof session?.deriveMessages !== 'function') return null
    try {
      const history = session.deriveMessages()
      return Array.isArray(history) ? history : null
    } catch {
      return null
    }
  }

  // The reply of the turn that just ended: the text of the last assistant message after the last message
  // the user typed, without this plugin's blocks. '' when there is none; null when the history cannot be read.
  function replyOf(history) {
    if (!history) return null
    let from = -1
    for (let i = history.length - 1; i >= 0; i--) {
      if (isUserOrigin(history[i])) {
        from = i
        break
      }
    }
    for (let i = history.length - 1; i > from; i--) {
      if (history[i]?.role === 'assistant') return formatter().strip(textOf(history[i]))
    }
    return ''
  }

  // The text of every block of this plugin (either form) in a history; a notice is not a block.
  function blocksIn(history) {
    return (history ?? []).filter((m) => m?.source?.kind === SOURCE_KIND && m.source.form !== FORM_NOTICE).map(textOf)
  }

  function stopLookups() {
    for (const controller of lookups.values()) controller.abort()
    lookups.clear()
  }

  // Fire and forget: one lookup with the reply as the query. What it finds that no block in the history
  // shows is kept for the next turn; an answer that comes after that turn began is dropped (pool.offer).
  function startSecondLookup(id, state, session, query, s) {
    const c = client(s.advanced.recallLimit)
    if (!c) {
      log.warn('feiyu-memgate: second lookup skipped (unavailable)')
      return
    }
    lookups.get(id)?.abort()
    const controller = new AbortController()
    lookups.set(id, controller)
    const { signal } = controller
    const forTurn = state.turn + 1
    const timeoutMs = s.advanced.recallTimeoutMs
    const max = s.advanced.afterthoughtMax
    Promise.resolve()
      .then(async () => {
        const detail = await within(c.recallDetail(query, { timeoutMs, signal, context: REQUEST_CONTEXT.followup, speaker: speakerAccount(s.advanced) }), timeoutMs + 250, signal)
        if (signal.aborted) return
        if (!detail || !Array.isArray(detail.results)) {
          log.warn('feiyu-memgate: second lookup skipped (timeout)')
          return
        }
        // Read the history now: it holds this turn's blocks, and the host may have compacted it meanwhile.
        const shown = formatter().seen(blocksIn(historyOf(session)))
        const found = detail.results.filter((r) => !shown(r?.text)).slice(0, max)
        if (!found.length || isDisposed() || signal.aborted || chat.peek(id) !== state) return
        pool.offer(state, found, forTurn)
      })
      .catch((error) => {
        if (!signal.aborted) log.warn(`feiyu-memgate: second lookup skipped (${kindOf(error)})`)
      })
      .finally(() => {
        if (lookups.get(id) === controller) lookups.delete(id)
      })
  }

  function onTurnEnd(session, event) {
    try {
      if (!isObject(event) || event.type !== 'turn/end' || isSubagent(session?.header)) return
      const id = sessionIdOf(session)
      if (id === undefined) return
      const state = chat.peek(id)
      if (!state || !state.open) return // no lookup was planned for this turn
      state.open = false
      const s = settings()
      const afterthought = s.modules.autoRecall && s.advanced.afterthought
      const tracking = s.modules.autoRecall && (s.advanced.conversationPool || s.advanced.recentPool)
      const recent = s.modules.autoRecall && s.advanced.recentPool
      if (!afterthought && !tracking) return
      const ended = event.data?.reason?.kind
      if (ended === 'aborted' || ended === 'error' || ended === 'interrupted') return // no whole reply
      const reply = replyOf(historyOf(session))
      if (reply === null) {
        if (!replyWarned) log.warn('feiyu-memgate: second lookup unavailable (reply-unreadable)')
        replyWarned = true
        return
      }
      // What the reply used gets stronger first: the pool is settled before a second lookup starts.
      if (tracking && reply) pool.reinforce(state, reply)
      if (recent) saveRecent(id, state, s)
      const query = afterthought ? buildQuery(reply, null) : null
      if (query) startSecondLookup(id, state, session, query, s)
    } catch (error) {
      log.warn(`feiyu-memgate: second lookup skipped (${kindOf(error)})`)
    }
  }

  function onChatDisposed(session) {
    const id = sessionIdOf(session)
    lookups.get(id)?.abort()
    lookups.delete(id)
    chat.drop(id)
  }

  return { onTurnEnd, onChatDisposed, stopLookups }
}
