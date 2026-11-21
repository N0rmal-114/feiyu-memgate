'use strict'

// The status block as text: turns the answer of GET feiyu-memgate/status into [label, text] rows.
// Pure; it takes the translate function t as an argument and never touches React or the DOM.

/** The words for a state name ('running', 'no-model'); a name the dictionary does not know is shown as it is. */
function stateText(t, state) {
  const key = 'state.' + state
  const text = t(key)
  return text && text !== key ? text : String(state)
}

/** The status block as [label, text] rows. */
function statusRows(data, t) {
  const rows = []
  const g = data.gateway || {}
  const managed = g.mode !== 'external'
  rows.push([t('gateway'), (managed ? t('managed') : t('external')) + ' · ' + stateText(t, g.state) + (!managed && g.url ? ' · ' + g.url : '')])
  if (managed) {
    const p = data.python
    rows.push([t('python'), !p ? t('pythonUnknown') : p.found ? p.version + ' (' + p.source + ') · ' + p.executable : t('pythonNone')])
    const r = data.retrieval || {}
    rows.push([t('retrieval'), r.setting + (r.active ? ' → ' + r.active + (r.hybrid && r.hybrid.active ? ' + ' + t('hybridWord') : '') : '')])
    if (data.store) {
      const count = data.store.count === null || data.store.count === undefined ? t('countUnknown') : t('memories', { count: data.store.count })
      rows.push([t('store'), data.store.type + ' · ' + data.store.file + (data.store.keywordFile ? ' + ' + data.store.keywordFile : '') + ' · ' + count])
    }
  } else {
    rows.push([t('retrieval'), t('retrievalExternal')])
  }
  const e = data.embed
  if (!e) rows.push([t('embed'), t('notInstalled')])
  else {
    const parts = [stateText(t, e.state)]
    if (e.error) parts.push(e.error)
    if (e.model) parts.push(e.model)
    if (e.dimensions) parts.push(e.dimensions + 'd')
    if (e.parity === 'approximate') parts.push(t('approximate'))
    rows.push([t('embed'), parts.join(' · ')])
  }
  const m = data.migration
  if (m) {
    const state = m.state === 'running' ? 'running-copy' : m.state
    const refused = m.refused > 0 ? ' · ' + t('refusedCount', { count: m.refused }) : ''
    rows.push([t('migration'), stateText(t, state) + ' · ' + t('progress', { copied: m.copied || 0, total: m.total || 0 }) + refused])
  }
  const r = data.relevance
  if (r && r.enabled) {
    const kinds = Object.keys(r.skipped || {})
    const skipped = kinds.reduce((sum, kind) => sum + (r.skipped[kind] || 0), 0)
    rows.push([t('relevance'), t('relevanceRow', { calls: r.calls || 0, kept: r.kept || 0, rejected: r.rejected || 0 })
      + (skipped > 0 ? ' · ' + t('relevanceSkipped', { count: skipped, kinds: kinds.map((kind) => kind + ' ' + r.skipped[kind]).join(', ') }) : '')])
  }
  return rows
}

module.exports = { stateText, statusRows }
