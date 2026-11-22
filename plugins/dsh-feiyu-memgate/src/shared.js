// Names and small pure helpers that more than one module of the plugin needs (src/plugin.js and the
// modules it wires up). Holds no state and reads no setting; a module imports from here, never from
// plugin.js. Logs name kinds of failure only: kindOf() gives an error name or code, never its message.

export const PLUGIN_NAME = 'dsh-feiyu-memgate'

// Session format v4 wants a producer-owned source kind; the old shared {kind: 'plugin'} wrapper is
// refused (packages/session/session-format-v3-to-v4/src/message-sources.ts:9-10; the third-party
// form is plugin:<name>, session-format-v3-to-v4/README.md:120-134). 'recall' is the host's form for
// material brought in from elsewhere (packages/llm/llm/src/message.ts:54-66, :100).
export const SOURCE_KIND = 'plugin:' + PLUGIN_NAME

// The two forms of a plugin message: the memory block and the note that a lookup failed.
export const FORM_RECALL = 'recall'
export const FORM_NOTICE = 'notice'
// The second block of a turn (advanced.afterthought, advanced.conversationPool): memories this conversation
// brought to mind. A block like the first, not a notice.
export const FORM_POOL = 'pool'

export const MAX_REMEMBER_CHARS = 4000 // gateway limit per message
export const MAX_QUERY_CHARS = 500 // gateway limit per query

// The label sent with every recall and remember: where the request comes from and what triggered
// it. Constants only, nothing from the conversation. The built-in gateway ignores it; an operator's
// own stages and guards can read it.
export const REQUEST_CONTEXT = Object.freeze({
  turn: Object.freeze({ channel: 'dsh', via: 'turn' }),
  search: Object.freeze({ channel: 'dsh', via: 'search' }),
  followup: Object.freeze({ channel: 'dsh', via: 'followup' }),
  tool: Object.freeze({ channel: 'dsh', via: 'tool' }),
  capture: Object.freeze({ channel: 'dsh', via: 'capture' }),
  trial: Object.freeze({ channel: 'dsh', via: 'trial' }),
})

// The account this DSH reports to the gateway (advanced.speakerAccount): dsh:name, or the default when the
// setting does not hold one. Mirrors the shape the gateway takes for an account of the platform dsh.
export const DEFAULT_ACCOUNT = 'dsh:local'
const ACCOUNT = /^dsh:[a-z0-9][a-z0-9_.-]{0,31}$/
export const speakerAccount = (advanced) => (typeof advanced?.speakerAccount === 'string' && ACCOUNT.test(advanced.speakerAccount) ? advanced.speakerAccount : DEFAULT_ACCOUNT)

export const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)
export const isUserOrigin = (m) => isObject(m) && m.role === 'user' && m.source?.kind === 'user'
export const isSubagent = (header) => header?.origin === 'subagent' || (Number.isInteger(header?.delegationDepth) && header.delegationDepth > 0)

export function textOf(message) {
  const content = message?.content
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content.filter((b) => isObject(b) && b.type === 'text' && typeof b.text === 'string').map((b) => b.text).join('\n')
}

// A kind of failure fit for a log line: an error name or code, never its message.
export function kindOf(error) {
  const code = error?.code
  if (typeof code === 'string' && /^[A-Z0-9_]{1,40}$/.test(code)) return code
  const name = error?.name
  return typeof name === 'string' && /^[A-Za-z]{1,40}$/.test(name) ? name : 'error'
}

// Resolves to the value, or to null when the time runs out or the signal aborts first.
export function within(promise, timeoutMs, signal) {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve(null)
    let timer = null
    const done = (value) => {
      if (timer) clearTimeout(timer)
      signal?.removeEventListener?.('abort', onAbort)
      resolve(value)
    }
    const onAbort = () => done(null)
    timer = setTimeout(() => done(null), timeoutMs)
    signal?.addEventListener?.('abort', onAbort, { once: true })
    Promise.resolve(promise).then(done, () => done(null))
  })
}

export const sessionIdOf = (session) => session?.id ?? session?.header?.id
