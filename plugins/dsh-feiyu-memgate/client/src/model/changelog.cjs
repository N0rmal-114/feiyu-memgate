'use strict'
// The changelog as plain data: the answer of GET feiyu-memgate/changelog turned into the list of
// releases the page shows. Pure; drawing the Markdown of a release is view/markdown.cjs.

/** The date a changelog entry gives on its date line ("日期：2026-11-18"), or ''. */
function entryDate(body) {
  const match = /日期[：:]\s*(\d{4}-\d{2}-\d{2})/.exec(String(body || ''))
  return match ? match[1] : ''
}

/**
 * The releases of a changelog answer, newest first as the host half sent them.
 * @param {{ version?: string, entries?: object[] } | null} data
 * @returns {{ id: string, name: string, date: string, body: string, installed: boolean }[]}
 *   `installed` marks the release whose version is the one running now. Entries without a text body
 *   are left out.
 */
function releases(data) {
  const entries = data && Array.isArray(data.entries) ? data.entries : []
  const current = data && data.version ? String(data.version) : ''
  return entries
    .filter((entry) => entry && typeof entry.body === 'string')
    .map((entry, index) => {
      const name = String(entry.version || entry.title || '')
      return { id: name || String(index), name, date: entryDate(entry.body), body: entry.body, installed: Boolean(current) && String(entry.version || '') === current }
    })
}

module.exports = { entryDate, releases }
