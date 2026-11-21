'use strict'

// The changelog Markdown subset: text in, React elements out. This is the one model module that builds
// React elements (through react.cjs); it never reads the DOM and has no path from the text to raw HTML.
// It owns the few styles it uses (STYLES) so that the model layer does not depend on the view.

const { h } = require('../react.cjs')

// The styles of the elements renderMarkdown() and inlineMarkdown() build; view/legacy.cjs reuses them.
const STYLES = {
  mdP: { margin: '0 0 6px' },
  mdH: { margin: '10px 0 4px', fontSize: '12px', fontWeight: 600, lineHeight: 1.5 },
  mdUl: { margin: '0 0 6px', paddingLeft: '18px' },
  mdLi: { margin: '2px 0' },
  code: { fontFamily: 'var(--dsw-font-mono, ui-monospace, monospace)', fontSize: '11px', padding: '0 4px', borderRadius: 'var(--dsw-radius-sm)', background: 'var(--dsw-alias-bg-layer-4)' },
  link: { fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-link, var(--dsw-alias-label-secondary))' },
}

const INLINE = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g
const CJK = /[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef]/

/** Inline marks in a line of text: `code`, **bold**, [text](https://...). Anything else, tags included, stays text. */
function inlineMarkdown(text) {
  const out = []
  let last = 0
  let n = 0
  let match
  INLINE.lastIndex = 0
  while ((match = INLINE.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index))
    n += 1
    if (match[1] !== undefined) out.push(h('code', { key: 'c' + n, style: STYLES.code }, match[1]))
    else if (match[2] !== undefined) out.push(h('strong', { key: 'b' + n }, match[2]))
    else if (/^https:\/\//i.test(match[4])) out.push(h('a', { key: 'a' + n, href: match[4], target: '_blank', rel: 'noopener noreferrer', style: STYLES.link }, match[3]))
    else out.push(match[3]) // any other address is shown as its text, not a link
    last = match.index + match[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

// Lines of one paragraph joined the way they read: no space between two Chinese characters.
function joinLines(lines) {
  let out = ''
  for (const line of lines) {
    if (out && !(CJK.test(out[out.length - 1]) && CJK.test(line[0]))) out += ' '
    out += line
  }
  return out
}

/**
 * A changelog body as React elements: ### headings, - list items (one nested level, by indentation),
 * paragraphs. Text goes in as text; there is no path from the file to raw HTML.
 */
function renderMarkdown(body) {
  const lines = String(body === undefined || body === null ? '' : body).replace(/\r\n?/g, '\n').split('\n')
  const blocks = []
  let para = null
  let list = null
  let tail = null // the item (top or nested) a continuation line belongs to
  const flushPara = () => {
    if (para) blocks.push(h('p', { key: 'p' + blocks.length, style: STYLES.mdP }, inlineMarkdown(joinLines(para))))
    para = null
  }
  const flushList = () => {
    if (list) {
      const key = 'l' + blocks.length
      const items = (entries, prefix) => entries.map((item, index) => h('li', { key: prefix + index, style: STYLES.mdLi },
        inlineMarkdown(item.text), item.children && item.children.length ? h('ul', { style: STYLES.mdUl }, items(item.children, prefix + index + '.')) : null))
      blocks.push(h('ul', { key, style: STYLES.mdUl }, items(list, key)))
    }
    list = null
    tail = null
  }
  for (const line of lines) {
    let match
    if (!line.trim()) {
      flushPara()
      flushList()
    } else if ((match = /^#{1,6}[ \t]+(.+?)[ \t]*$/.exec(line))) {
      flushPara()
      flushList()
      blocks.push(h('h5', { key: 'h' + blocks.length, style: STYLES.mdH }, inlineMarkdown(match[1])))
    } else if ((match = /^([ \t]*)[-*][ \t]+(.*)$/.exec(line))) {
      flushPara()
      if (!list) list = []
      const item = { text: match[2].trim(), children: [] }
      if (match[1].length >= 2 && list.length) list[list.length - 1].children.push(item)
      else list.push(item)
      tail = item
    } else if (list && tail && /^[ \t]+\S/.test(line)) {
      tail.text = joinLines([tail.text, line.trim()])
    } else {
      flushList()
      if (!para) para = []
      para.push(line.trim())
    }
  }
  flushPara()
  flushList()
  return blocks
}

/** The date a changelog entry gives on its date line ("日期：2026-11-18"), or ''. */
function entryDate(body) {
  const match = /日期[：:]\s*(\d{4}-\d{2}-\d{2})/.exec(String(body || ''))
  return match ? match[1] : ''
}

module.exports = { STYLES, inlineMarkdown, joinLines, renderMarkdown, entryDate }
