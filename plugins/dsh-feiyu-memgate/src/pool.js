// What a conversation was given, and what a second lookup found for the next turn (prototype).
//
// Pure rules over plain objects: no host API, no clock, no I/O. src/plugin.js wires them to the host.
// The state lives in memory only, per session id, and is never written or logged.
//
// Per session:
//   turn     the number of user turns a lookup was planned for
//   open     a lookup was planned for the turn that is running (cleared when it ends)
//   pending  { forTurn, memories }: what a finished second lookup found, meant for turn `forTurn`
//   items    memory text -> { text, date, source, strength, turn }: what this conversation was given
import { meaningful } from './query.js'

export const DECAY = 0.8 // multiplied into every strength at the start of a turn
export const FORGET_BELOW = 0.2 // an item weaker than this is dropped
export const PICK_FROM = 1 // an item at least this strong may be brought back
export const MAX_ITEMS = 24 // per session; beyond it the weakest go
export const MAX_SESSIONS = 64 // the least recently used session goes first
export const MENTION_MIN = 3 // characters a message and a memory must share to count as a mention

// ASCII words too common to show that a message is about a memory.
const STOP_WORDS = new Set([
  'the', 'and', 'for', 'are', 'was', 'were', 'but', 'not', 'you', 'your', 'his', 'her', 'its', 'our', 'their',
  'with', 'that', 'this', 'these', 'those', 'have', 'has', 'had', 'from', 'they', 'them', 'what', 'when', 'who',
  'will', 'would', 'can', 'could', 'about', 'into', 'than', 'then', 'there', 'here', 'also', 'just', 'all', 'any',
  'user', 'users',
])

const WORD_RE = /[A-Za-z0-9]+/g
const NOT_ASCII_RE = /[^\u0000-\u007f]+/g

// The runs of meaningful characters of a text: whatever lies between two characters that `meaningful` drops
// (a space, punctuation, an emoji) ends a run, so two words never join into one.
function runsOf(text) {
  if (typeof text !== 'string') return []
  const runs = []
  let run = ''
  for (const ch of text) {
    if (meaningful(ch)) {
      run += ch
    } else if (run) {
      runs.push(run)
      run = ''
    }
  }
  if (run) runs.push(run)
  return runs
}

function wordsOf(runs) {
  const words = new Set()
  for (const run of runs) {
    for (const word of run.match(WORD_RE) ?? []) {
      const lower = word.toLowerCase()
      if (lower.length >= MENTION_MIN && !STOP_WORDS.has(lower)) words.add(lower)
    }
  }
  return words
}

function windowsOf(runs) {
  const windows = new Set()
  for (const run of runs) {
    for (const part of run.match(NOT_ASCII_RE) ?? []) {
      const points = Array.from(part)
      for (let i = 0; i + MENTION_MIN <= points.length; i++) windows.add(points.slice(i, i + MENTION_MIN).join(''))
    }
  }
  return windows
}

/**
 * Whether a user message mentions a memory: they share a run of at least MENTION_MIN meaningful
 * characters. Text without spaces (Chinese, Japanese) is compared by any shared run of that length; for
 * words written in ASCII letters and digits the shared run must be a whole word, because three letters
 * inside different words say nothing, and the commonest English words do not count.
 */
export function mentions(message, memoryText) {
  const a = runsOf(message)
  const b = runsOf(memoryText)
  if (!a.length || !b.length) return false
  const words = wordsOf(b)
  if (words.size) for (const word of wordsOf(a)) if (words.has(word)) return true
  const windows = windowsOf(b)
  if (windows.size) for (const window of windowsOf(a)) if (windows.has(window)) return true
  return false
}

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)

/** The part of a result the pool keeps: its text, date and source label; null for an unusable one. */
export function memoryOf(result) {
  if (!isObject(result) || typeof result.text !== 'string' || !result.text) return null
  const memory = { text: result.text }
  if (typeof result.date === 'string') memory.date = result.date
  if (typeof result.source === 'string') memory.source = result.source
  return memory
}

function freshState() {
  return { turn: 0, open: false, pending: null, items: new Map() }
}

/**
 * The state of every conversation: a map capped at MAX_SESSIONS, least recently used out first.
 * touch() creates a session and counts as a use; peek() reads without either.
 */
export function createPool() {
  const sessions = new Map()
  return {
    touch(id) {
      let state = sessions.get(id)
      if (state) {
        sessions.delete(id)
      } else {
        state = freshState()
        while (sessions.size >= MAX_SESSIONS) sessions.delete(sessions.keys().next().value)
      }
      sessions.set(id, state)
      return state
    },
    peek: (id) => sessions.get(id),
    drop: (id) => sessions.delete(id),
    clear: () => sessions.clear(),
    clearPending() {
      for (const state of sessions.values()) state.pending = null
    },
    get size() {
      return sessions.size
    },
    ids: () => [...sessions.keys()],
  }
}

/**
 * A new user turn begins: count it, and with `decay` weaken every item and drop the ones below
 * FORGET_BELOW. Then every item the message mentions gains 1.
 */
export function advance(state, { decay = false, message = null } = {}) {
  state.turn += 1
  if (!decay) return
  for (const [key, item] of state.items) {
    item.strength *= DECAY
    if (item.strength < FORGET_BELOW) state.items.delete(key)
  }
  if (typeof message === 'string' && message) {
    for (const item of state.items.values()) if (mentions(message, item.text)) item.strength += 1
  }
}

/** Memories were given this turn: a new one enters at strength 1, a known one gains 1; both take the turn number. */
export function enter(state, memories, turn = state.turn) {
  for (const raw of Array.isArray(memories) ? memories : []) {
    const memory = memoryOf(raw)
    if (!memory) continue
    const known = state.items.get(memory.text)
    if (known) {
      known.strength += 1
      known.turn = turn
      if (memory.date !== undefined) known.date = memory.date
      if (memory.source !== undefined) known.source = memory.source
    } else {
      state.items.set(memory.text, { ...memory, strength: 1, turn })
    }
  }
  evict(state)
}

// Over MAX_ITEMS the weakest go; of equal strength the one given longest ago, then the one entered first.
function evict(state) {
  if (state.items.size <= MAX_ITEMS) return
  const order = [...state.items.values()].map((item, index) => ({ item, index }))
  order.sort((x, y) => x.item.strength - y.item.strength || x.item.turn - y.item.turn || x.index - y.index)
  for (const { item } of order.slice(0, state.items.size - MAX_ITEMS)) state.items.delete(item.text)
}

/**
 * The items to bring back: at least PICK_FROM strong, not `skip`ped (a predicate over the text), strongest
 * first, then most recently given, then in the order they entered; at most `limit`.
 */
export function pick(state, { limit, skip = () => false } = {}) {
  const order = [...state.items.values()].map((item, index) => ({ item, index }))
    .filter(({ item }) => item.strength >= PICK_FROM && !skip(item.text))
  order.sort((x, y) => y.item.strength - x.item.strength || y.item.turn - x.item.turn || x.index - y.index)
  return order.slice(0, Math.max(0, limit)).map(({ item }) => ({ text: item.text, date: item.date, source: item.source }))
}

/**
 * Keep what a second lookup found for turn `forTurn`. It is for that turn only: once the turn has begun
 * (state.turn reached it) the answer is late and dropped. Returns whether it was kept.
 */
export function offer(state, memories, forTurn) {
  if (!Number.isInteger(forTurn) || state.turn >= forTurn) return false
  const kept = (Array.isArray(memories) ? memories : []).map(memoryOf).filter(Boolean)
  if (!kept.length) return false
  state.pending = { forTurn, memories: kept }
  return true
}

/** Take what is pending (and clear it): the memories when they are meant for the current turn, else none. */
export function takePending(state) {
  const pending = state.pending
  state.pending = null
  return pending && pending.forTurn === state.turn ? pending.memories : []
}
