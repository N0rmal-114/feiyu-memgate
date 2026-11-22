'use strict'
// The recall trial (Retrieval tab): the answer of the host half's try route turned into what the panel
// shows. Pure. The answer is { ok: true, candidates, more, failed, busy, limit, ... } (src/plugin.js);
// a candidate is { source, id, found, score, outcome, stage, addedBy, text?, date?, cut?, fields? } as
// src/client.js explainRecall cleans it; `fields` holds what the record carries (speaker, about, channel,
// audience, origin, event_date, kind), all strings.

const { TRIAL_MAX_CHARS } = require('../constants.cjs')

// The steps the page has a word for; any other step name is shown as it is, in the "other" text.
const REASONS = {
  min_score: 'trial.reason.min_score',
  relative_score: 'trial.reason.relative_score',
  fill_text: 'trial.reason.fill_text',
  drop_oversize: 'trial.reason.drop_oversize',
  redact: 'trial.reason.redact',
  dedupe: 'trial.reason.dedupe',
  budget: 'trial.reason.budget',
  drop_marker: 'trial.reason.drop_marker',
}

// The text for each code the host half or the hook can answer with; anything else reads as a failure.
const ERRORS = {
  unavailable: 'trial.error.unavailable',
  unsupported: 'trial.error.unsupported',
  'no-recall': 'trial.error.no-recall',
  failed: 'trial.error.failed',
  network: 'trial.error.network',
}

// The values of a record's origin and kind the page has a word for (the keys are 'trial.origin.' and 'trial.kind.' plus the value).
const ORIGINS = ['said', 'tool', 'curated', 'derived']
const KINDS = ['identity', 'preference', 'event', 'plan', 'entity']

// A score that moved by less than this on its way is shown once.
const MOVED = 0.005

/** The key of the message for an error code. */
function errorKey(code) {
  return Object.prototype.hasOwnProperty.call(ERRORS, code) ? ERRORS[code] : ERRORS.failed
}

/** A score with two decimals. */
function score(n) {
  return typeof n === 'number' && Number.isFinite(n) ? n.toFixed(2) : '?'
}

/** Whether a sentence may be tried: something besides white space, and at most TRIAL_MAX_CHARS characters. */
function usable(text) {
  if (typeof text !== 'string') return false
  const trimmed = text.trim()
  return trimmed.length > 0 && Array.from(trimmed).length <= TRIAL_MAX_CHARS
}

/** Why a candidate was not given: null, or { key, values } for the page's text. */
function reasonOf(candidate, limit) {
  const stage = typeof candidate.stage === 'string' ? candidate.stage : ''
  if (candidate.outcome === 'dropped' || candidate.outcome === 'deferred') {
    if (Object.prototype.hasOwnProperty.call(REASONS, stage)) return { key: REASONS[stage], values: {} }
    return { key: 'trial.reason.other', values: { stage } }
  }
  if (candidate.outcome === 'over_limit') return { key: 'trial.reason.over_limit', values: { limit } }
  if (candidate.outcome === 'not_reached') return { key: 'trial.reason.not_reached', values: {} }
  return null
}

const has = (value) => typeof value === 'string' && value !== ''

/**
 * What a record carries, as an ordered list of { key, values } for the page's text: who said it, who it is
 * about, the channel, how it came to be stored, its kind and the date of the event. The audience is not
 * shown, and a value the page has no word for (an origin or a kind) is left out.
 */
function factsOf(fields) {
  if (fields === null || typeof fields !== 'object') return []
  const facts = []
  if (has(fields.speaker)) facts.push({ key: 'trial.field.speaker', values: { who: fields.speaker } })
  if (has(fields.about)) facts.push({ key: 'trial.field.about', values: { who: fields.about } })
  if (has(fields.channel)) facts.push({ key: 'trial.field.channel', values: { channel: fields.channel } })
  if (ORIGINS.includes(fields.origin)) facts.push({ key: 'trial.origin.' + fields.origin, values: {} })
  if (KINDS.includes(fields.kind)) facts.push({ key: 'trial.kind.' + fields.kind, values: {} })
  if (has(fields.event_date)) facts.push({ key: 'trial.field.eventDate', values: { date: fields.event_date } })
  return facts
}

/** The candidates in the order given, each as the panel draws it. */
function rows(answer) {
  const list = answer && Array.isArray(answer.candidates) ? answer.candidates : []
  const limit = answer && Number.isFinite(answer.limit) ? answer.limit : 0
  return list.map((c, index) => ({
    key: String(c.source) + ':' + String(c.id) + ':' + index,
    text: typeof c.text === 'string' ? c.text : null,
    cut: c.cut === true,
    date: typeof c.date === 'string' ? c.date : null,
    found: c.found,
    score: c.score,
    moved: Math.abs(c.score - c.found) >= MOVED - 1e-9,
    delivered: c.outcome === 'delivered',
    linked: c.addedBy === 'linked',
    reason: reasonOf(c, limit),
    facts: factsOf(c.fields),
  }))
}

/** How many candidates the gateway looked at and how many it would give. */
function summary(answer) {
  const list = answer && Array.isArray(answer.candidates) ? answer.candidates : []
  return { found: list.length, given: list.filter((c) => c.outcome === 'delivered').length }
}

module.exports = { errorKey, score, usable, rows, summary }
