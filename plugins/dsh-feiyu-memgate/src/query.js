// What to ask the memory gateway for one chat message.
//
// A port of the gateway's Python query shaper (client/query.py). The default shaper suits
// Chinese chat; other languages pass their own filler characters, a different "short"
// length or extra skip words.
//
// Python and JavaScript disagree on three small things, and each one is handled here on
// purpose so the port gives the same answer as the reference (tests/parity.test.js checks it):
//   - length and slicing count Unicode code points, not UTF-16 units;
//   - str.strip() / \s follow Python's whitespace set, which has U+001C..U+001F and U+0085
//     but not U+FEFF, unlike String.prototype.trim() / JavaScript's \s;
//   - unicodedata.category() is replaced by Unicode property escapes.

// Python's str.isspace() characters, as the inside of a regular expression class.
export const PY_SPACE_CLASS =
  '\\t\\n\\v\\f\\r\\u001c-\\u001f \\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000'

const STRIP_RE = new RegExp(`^[${PY_SPACE_CLASS}]+|[${PY_SPACE_CLASS}]+$`, 'gu')

/** str.strip() with Python's notion of whitespace. */
export function pyStrip(text) {
  return text.replace(STRIP_RE, '')
}

/** len(str): the number of code points. */
export function codePointLength(text) {
  let n = 0
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i)
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < text.length) {
      const d = text.charCodeAt(i + 1)
      if (d >= 0xdc00 && d <= 0xdfff) i++
    }
    n++
  }
  return n
}

export const FILLER_CHARS = '嗯哦噢啊哈呵嘿好行对是'
export const SHORT_CHARS = 8

const PLACEHOLDER_RE = /\[[^[\]]{1,8}\]/gu
// A keycap emoji ("1" + FE0F + 20E3) is one picture, not the digit.
const KEYCAP_RE = /[0-9#*]️?⃣/gu
// P*, Z*, C*, So, Sk, Me, the variation selectors FE00..FE0F and the tildes (~ and U+FF5E).
const NOISE_RE = /[\p{P}\p{Z}\p{C}\p{So}\p{Sk}\p{Me}︀-️~～]/u

/** The characters left after bracket placeholders ([图片]), emoji, punctuation and whitespace are removed. */
export function meaningful(text) {
  if (typeof text !== 'string') return ''
  const stripped = text.replace(PLACEHOLDER_RE, '').replace(KEYCAP_RE, '')
  let out = ''
  for (const ch of stripped) {
    if (!NOISE_RE.test(ch)) out += ch
  }
  return out
}

/**
 * The query for one message, or null when it is not worth a lookup.
 *
 * options (all optional, same meaning as the Python QueryShaper):
 *   fillerChars   a message of at most two meaningful characters, all of them in this
 *                 string/iterable, is not worth a lookup
 *   shortChars    a message with fewer meaningful characters borrows the previous user message
 *   maxChars      the query length cap (the gateway's limit is 500)
 *   skipPrefixes  a message starting with one of these characters is a command
 *   skipWords     whole messages (compared in lower case) that are not worth a lookup
 */
export function buildQuery(current, previous, options = {}) {
  const {
    fillerChars = FILLER_CHARS,
    shortChars = SHORT_CHARS,
    maxChars = 500,
    skipPrefixes = '/／',
    skipWords = ['ok'],
  } = options ?? {}
  if (typeof current !== 'string') return null
  const cur = pyStrip(current)
  if (!cur) return null
  const curPoints = Array.from(cur)
  if (new Set(skipPrefixes).has(curPoints[0])) return null
  const core = meaningful(cur)
  if (!core) return null
  if (new Set(Array.from(skipWords, (w) => String(w).toLowerCase())).has(core.toLowerCase())) return null
  const corePoints = Array.from(core)
  const fillers = new Set(fillerChars)
  if (corePoints.length <= 2 && corePoints.every((ch) => fillers.has(ch))) return null
  const prev = typeof previous === 'string' ? pyStrip(previous) : ''
  const cap = Math.max(0, maxChars)
  if (corePoints.length < shortChars && prev) {
    const room = cap - curPoints.length - 1
    if (room <= 0) return curPoints.slice(0, cap).join('')
    return Array.from(prev).slice(-room).join('') + '\n' + cur
  }
  return curPoints.slice(0, cap).join('')
}
