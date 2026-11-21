'use strict'
// The status of the plugin boiled down for the top of the page: one mood, one headline, one line of
// explanation, and a few facts. Pure: it takes what GET feiyu-memgate/status answered and returns
// translation keys with their parameters; it never touches React or the DOM.
//
// The mood decides which drawing of the mascot is shown, so the order of the checks below is the
// order of what a person needs to hear first: something is broken, then something is still on its
// way, then nothing is running, and only then "all is well".

/** The four moods, each with the drawing that goes with it (view/hero.cjs). */
const MOODS = ['ready', 'busy', 'asleep', 'trouble']

// Gateway states (src/gateway.js for the bundled gateway, src/plugin.js for an external one).
const BUSY_STATES = ['idle', 'starting', 'restarting']
const ASLEEP_STATES = ['off', 'stopped', 'unused']

const text = (key, params) => ({ key, params: params || {} })

/** Whole numbers with thousands separators, the same in both languages (1,284). */
function formatCount(value) {
  return String(Math.trunc(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/**
 * The reason the plugin cannot work right now, or null.
 * @returns {{ key: string, params: object } | null} the explanation line
 */
function trouble(data) {
  const gateway = data.gateway || {}
  const managed = gateway.mode !== 'external'
  if (managed && data.python && data.python.found === false) return text('overview.trouble.python')
  if (gateway.state === 'failed') return text('overview.trouble.failed')
  if (!managed && gateway.state === 'incomplete') return text('overview.trouble.incomplete')
  return null
}

/**
 * What is still on its way, or null: the gateway starting, a model loading, memories being copied.
 * The copy comes last: the plugin already answers while it runs.
 */
function busy(data) {
  const gateway = data.gateway || {}
  if (BUSY_STATES.includes(gateway.state)) return text('overview.busy.gateway')
  if (data.embed && data.embed.state === 'loading') return text('overview.busy.model', { model: data.embed.model || '' })
  const copy = data.migration
  if (copy && (copy.state === 'running' || copy.state === 'partial')) {
    return text('overview.busy.copy', { copied: formatCount(copy.copied || 0), total: formatCount(copy.total || 0) })
  }
  return null
}

/** The explanation line of a working plugin: where the memories are and how they are found. */
function readyLine(data) {
  const gateway = data.gateway || {}
  if (gateway.mode === 'external') return text('overview.ready.external')
  const retrieval = data.retrieval || {}
  const tier = retrieval.active || 'keyword'
  const hybrid = Boolean(retrieval.hybrid && retrieval.hybrid.active)
  return text('overview.ready.' + tier + (hybrid && tier !== 'keyword' ? '.hybrid' : ''), { model: (data.embed && data.embed.model) || '' })
}

/**
 * The short facts under the headline, most useful first. Each is { id, key, params, tone }, where
 * tone is 'plain' or 'warn' (a fact the person should look at).
 */
function facts(data) {
  const out = []
  const gateway = data.gateway || {}
  const managed = gateway.mode !== 'external'
  const store = data.store
  if (managed && store && store.count !== null && store.count !== undefined) {
    out.push({ id: 'count', key: 'overview.fact.count', params: { count: formatCount(store.count) }, tone: 'plain' })
  }
  if (managed && data.retrieval && data.retrieval.active) {
    const hybrid = Boolean(data.retrieval.hybrid && data.retrieval.hybrid.active)
    // Keyword retrieval has no hybrid form: the switch only adds keyword search to a vector tier.
    const tier = data.retrieval.active
    out.push({ id: 'tier', key: 'overview.fact.tier.' + tier + (hybrid && tier !== 'keyword' ? '.hybrid' : ''), params: {}, tone: 'plain' })
  }
  out.push({ id: 'gateway', key: managed ? 'overview.fact.managed' : 'overview.fact.external', params: {}, tone: 'plain' })
  if (data.embed && data.embed.state === 'error') out.push({ id: 'embed', key: 'overview.fact.embedError', params: {}, tone: 'warn' })
  if (data.migration && data.migration.refused > 0) {
    out.push({ id: 'refused', key: 'overview.fact.refused', params: { count: formatCount(data.migration.refused) }, tone: 'warn' })
  }
  return out
}

/**
 * The top of the page for one status answer.
 * @param {object | null} data the answer of the status route, or null when there is none yet
 * @param {string | null} error the code of a failed read, or null
 * @param {boolean} loading whether the first read is still under way
 * @returns {{ mood: string, headline: object, line: object, facts: object[] }}
 */
function summarise(data, error, loading) {
  if (!data) {
    if (loading) return { mood: 'busy', headline: text('overview.head.loading'), line: text('overview.line.loading'), facts: [] }
    return { mood: 'trouble', headline: text('overview.head.unknown'), line: text('overview.line.unknown', { code: error || '' }), facts: [] }
  }
  const state = (data.gateway || {}).state
  const broken = trouble(data)
  if (broken) return { mood: 'trouble', headline: text('overview.head.trouble'), line: broken, facts: facts(data) }
  if (ASLEEP_STATES.includes(state)) {
    return { mood: 'asleep', headline: text('overview.head.asleep'), line: text('overview.asleep.' + state), facts: facts(data) }
  }
  const waiting = busy(data)
  if (waiting) return { mood: 'busy', headline: text('overview.head.busy'), line: waiting, facts: facts(data) }
  return { mood: 'ready', headline: text('overview.head.ready'), line: readyLine(data), facts: facts(data) }
}

module.exports = { MOODS, formatCount, summarise }
