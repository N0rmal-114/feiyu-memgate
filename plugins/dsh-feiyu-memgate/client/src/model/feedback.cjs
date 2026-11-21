'use strict'
// Feedback: what a person wrote, turned into the address of GitHub's "new issue" page with the title
// and the text filled in. The plugin sends nothing anywhere. The person's own browser opens the
// address, shows what is filled in, and the report exists only once they press GitHub's own button.
//
// Pure, and the one module of the page that the host half loads as well (src/commands.js, for
// /memgate feedback), so that both build the same address. It requires the constants and nothing else.
//
// What may go into a report besides the person's own words is decided here and nowhere else:
// environment() takes version numbers and state names from the status, and every value has to look
// like one (SAFE) or is replaced by a question mark. So a path, an address, a file name, a count of
// memories or a secret cannot get in, whatever a later version of the status holds.

const { LINKS } = require('../constants.cjs')

/** What a report is about, in the order the page offers them. */
const KINDS = ['idea', 'problem', 'other']
// The start of the issue title, so that the list of issues can be read at a glance.
const TITLE_TAGS = { idea: '[idea]', problem: '[bug]', other: '[feedback]' }
/** The longest text the page accepts, in characters. */
const MAX_TEXT = 4000
// The title is the first line of the text, up to this many characters.
const MAX_TITLE = 60
// The longest address that is handed to a browser. GitHub answers 414 somewhere above 8,000
// characters; a Chinese character takes nine once it is percent-encoded.
const MAX_URL = 6000
// A version number, a state name, a model id: letters, digits, dots, dashes, at most 40 characters.
const SAFE = /^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,39}$/
// A half of a surrogate pair without its other half: encodeURIComponent throws on one.
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g
const ENV_HEADING = 'Environment (added by dsh-feiyu-memgate)'
const CUT_NOTE = '(Cut here: the text was too long for a link. Paste the whole text over this.)'

/** A value of the status as it may appear in a report: itself when it looks like a name or a number, else '?'. */
function safe(value) {
  const text = typeof value === 'number' ? String(value) : value
  return typeof text === 'string' && SAFE.test(text) ? text : '?'
}

/**
 * The environment lines of a report, from the answer of the status route.
 * @param {object | null | undefined} status
 * @returns {[string, string][]} name and value pairs; empty when there is no status to take them from
 */
function environment(status) {
  if (!status || typeof status !== 'object') return []
  const rows = [['plugin', 'dsh-feiyu-memgate ' + safe(status.version)]]
  const embed = status.embed
  rows.push(['add-on', embed
    ? ['dsh-feiyu-memgate-embed ' + safe(embed.version), safe(embed.state)].concat(embed.model ? [safe(embed.model)] : []).join(', ')
    : 'not installed'])
  const gateway = status.gateway || {}
  rows.push(['gateway', safe(gateway.mode) + ', ' + safe(gateway.state)])
  if (gateway.mode !== 'external') {
    const retrieval = status.retrieval || {}
    rows.push(['retrieval', safe(retrieval.setting)
      + (retrieval.active ? ' -> ' + safe(retrieval.active) : '')
      + (retrieval.hybrid && retrieval.hybrid.active ? ' + keyword' : '')])
    const python = status.python
    rows.push(['python', !python ? 'not checked' : python.found ? safe(python.version) : 'not found'])
  }
  const runtime = status.runtime
  if (runtime) rows.push(['system', safe(runtime.platform) + ' ' + safe(runtime.arch) + ', Node ' + safe(runtime.node)])
  rows.push(['language', safe(status.language)])
  return rows
}

/** The text as it is used: one kind of line break, no stray half characters, trimmed, at most MAX_TEXT characters. */
function cleanText(text) {
  const tidy = String(text === null || text === undefined ? '' : text).replace(/\r\n?/g, '\n').replace(LONE_SURROGATE, '�').trim()
  const points = Array.from(tidy)
  return points.length > MAX_TEXT ? points.slice(0, MAX_TEXT).join('').trim() : tidy
}

/** The issue title: the tag of the kind, then the first line of the text, shortened. */
function titleOf(kind, text) {
  const tag = TITLE_TAGS[kind] || TITLE_TAGS.other
  const first = cleanText(text).split('\n').map((line) => line.trim()).find(Boolean) || ''
  const points = Array.from(first)
  const line = points.length > MAX_TITLE ? points.slice(0, MAX_TITLE).join('').trim() + '…' : first
  return line ? tag + ' ' + line : tag
}

/** The issue text: the person's words, then the environment lines under a rule when there are any. */
function bodyOf(text, rows) {
  const parts = []
  if (text) parts.push(text)
  if (rows && rows.length) parts.push('---\n' + ENV_HEADING + '\n' + rows.map((row) => '- ' + row[0] + ': ' + row[1]).join('\n'))
  return parts.join('\n\n')
}

/** The address of GitHub's new-issue page with a title and a text filled in. */
function addressOf(title, body) {
  return LINKS.newIssue + '?title=' + encodeURIComponent(title) + '&body=' + encodeURIComponent(body)
}

/**
 * A report, ready to hand over.
 * @param {{ kind?: string, text?: string, environment?: [string, string][], cutNote?: string }} draft
 *   `environment` is environment() or an empty list when the person does not want it attached;
 *   `cutNote` is the line that marks a cut, in the person's language.
 * @returns {{ ready: boolean, title: string, body: string, url: string, clipped: boolean }}
 *   `ready`: there is something written. `title` and `body` are the whole report. `url` opens the
 *   new-issue page with them filled in; when they do not fit into an address (`clipped`), the text in
 *   the address is cut and ends with the note, and the whole report has to travel by the clipboard.
 */
function compose(draft) {
  const input = draft || {}
  const text = cleanText(input.text)
  const rows = Array.isArray(input.environment) ? input.environment : []
  const title = titleOf(input.kind, text)
  const body = bodyOf(text, rows)
  let url = addressOf(title, body)
  const clipped = url.length > MAX_URL
  if (clipped) {
    // The longest beginning of the text that still fits with the note and the environment lines.
    const note = '\n\n' + (input.cutNote || CUT_NOTE)
    const points = Array.from(text)
    const fits = (count) => addressOf(title, bodyOf(points.slice(0, count).join('') + note, rows)).length <= MAX_URL
    let low = 0
    let high = points.length
    while (low < high) {
      const middle = Math.ceil((low + high) / 2)
      if (fits(middle)) low = middle
      else high = middle - 1
    }
    url = addressOf(title, bodyOf(points.slice(0, low).join('').trim() + note, rows))
  }
  return { ready: text.length > 0, title, body, url, clipped }
}

/** The whole report as one text, for the clipboard. */
function reportText(composed) {
  return composed.title + '\n\n' + composed.body
}

module.exports = { KINDS, TITLE_TAGS, MAX_TEXT, MAX_TITLE, MAX_URL, safe, environment, cleanText, titleOf, bodyOf, compose, reportText }
