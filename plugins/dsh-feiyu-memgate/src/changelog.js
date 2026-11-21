// The plugin's changelog (CHANGELOG.md, written in Chinese) as data: parsing only, no file or host access.
//
// A section starts at a line `## <heading>`; its body is everything up to the next `## ` line (`###`
// groups stay inside the body). A heading that is a version number ("0.3.2") gives `version`; any other
// heading ("未发布") gives version null. Nothing here throws, whatever the text looks like.

export const MAX_BODY_BYTES = 64 * 1024
export const CUT_MARKER = '\n\n[…]'
const VERSION_RE = /^v?(\d+(?:\.\d+){0,3})$/

/** The numeric parts of a dotted version ("0.3.2" is [0, 3, 2]); null when it is not one. */
export function versionParts(text) {
  if (typeof text !== 'string') return null
  const match = VERSION_RE.exec(text.trim())
  return match ? match[1].split('.').map(Number) : null
}

/** -1, 0 or 1 for a before, equal to or after b (dotted numeric parts; missing parts count as 0); null if either is not a version. */
export function compareVersions(a, b) {
  const x = versionParts(a)
  const y = versionParts(b)
  if (!x || !y) return null
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] ?? 0) - (y[i] ?? 0)
    if (d !== 0) return d < 0 ? -1 : 1
  }
  return 0
}

/** The sections of a changelog in file order: { version, title, body } (see the top of this file). */
export function parseChangelog(text) {
  if (typeof text !== 'string' || !text) return []
  const lines = text.replace(/^﻿/, '').split(/\r\n|\r|\n/)
  const sections = []
  let current = null
  for (const line of lines) {
    const heading = /^##[ \t]+(\S.*?)[ \t]*$/.exec(line)
    if (heading && !line.startsWith('###')) {
      current = { title: heading[1], lines: [] }
      sections.push(current)
    } else if (current) {
      current.lines.push(line)
    }
  }
  return sections.map(({ title, lines: body }) => ({
    version: versionParts(title) ? title.replace(/^v/, '') : null,
    title,
    body: body.join('\n').trim(),
  }))
}

function capBody(body) {
  if (Buffer.byteLength(body, 'utf8') <= MAX_BODY_BYTES) return body
  const cut = Buffer.from(body, 'utf8').subarray(0, MAX_BODY_BYTES).toString('utf8').replace(/�+$/, '')
  return cut + CUT_MARKER
}

/**
 * The released sections: those with a version number not newer than `currentVersion`, newest first, at
 * most `limit`, each body capped at MAX_BODY_BYTES (a marker says when it was cut). A current version that
 * is not a version number gives no entries.
 */
export function releasedEntries(text, currentVersion, limit = 20) {
  if (!versionParts(currentVersion)) return []
  const max = Number.isInteger(limit) && limit > 0 ? limit : 20
  return parseChangelog(text)
    .filter((section) => section.version !== null && compareVersions(section.version, currentVersion) <= 0)
    .map((section, index) => ({ section, index }))
    .sort((x, y) => compareVersions(y.section.version, x.section.version) || x.index - y.index)
    .slice(0, max)
    .map(({ section }) => ({ version: section.version, title: section.title, body: capBody(section.body) }))
}
