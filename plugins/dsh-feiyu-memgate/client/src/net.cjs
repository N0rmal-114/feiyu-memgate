'use strict'

// The page's calls, over a `fetch` passed in by the caller (so tests can fake it).
//
// Four go to the host half: the routes are document-relative and owner-only (src/plugin.js). A failed
// answer becomes an Error whose `.code` is 'HTTP <status>'; a network failure rejects as it comes.
//
// One goes outside the host: sendFeedback posts a report to the project's feedback receiver, and only
// when a person pressed Send. It carries no cookie, no credential and no referrer. No React, no DOM.

const { STATUS_ROUTE, CHANGELOG_ROUTE, MODELS_ROUTE, TRY_ROUTE, FEEDBACK_TIMEOUT_MS } = require('./constants.cjs')

/** GET a route and read its JSON; `name` labels the Error of a failed answer. */
async function getJson(fetchImpl, route, name) {
  const response = await fetchImpl(route, { headers: { accept: 'application/json' }, credentials: 'same-origin' })
  if (!response.ok) {
    const error = new Error(name)
    error.code = 'HTTP ' + response.status
    throw error
  }
  return response.json()
}

/** POST a JSON body to a route and read the JSON answer; errors as getJson. */
async function postJson(fetchImpl, route, name, body) {
  const response = await fetchImpl(route, {
    method: 'POST',
    headers: { accept: 'application/json', 'content-type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const error = new Error(name)
    error.code = 'HTTP ' + response.status
    throw error
  }
  return response.json()
}

function fetchStatus(fetchImpl) {
  return getJson(fetchImpl, STATUS_ROUTE, 'status')
}

function fetchChangelog(fetchImpl) {
  return getJson(fetchImpl, CHANGELOG_ROUTE, 'changelog')
}

function fetchModels(fetchImpl) {
  return getJson(fetchImpl, MODELS_ROUTE, 'models')
}

/** One click, sent to the host half: { result: { ok, code, problem }, view }. */
function postModels(fetchImpl, body) {
  return postJson(fetchImpl, MODELS_ROUTE, 'models', body)
}

/** A trial recall: the sentence goes to the host half, which answers { ok, ... } as src/plugin.js describes. */
function tryRecall(query, fetchImpl) {
  return postJson(fetchImpl, TRY_ROUTE, 'try', { query })
}

// Why a report was not sent, as an Error the page can tell from any other.
function notSent(code) {
  const error = new Error('feedback')
  error.code = code
  error.notSent = true
  return error
}

/**
 * Posts a feedback report (model/feedback.cjs payload()) to the receiver at `endpoint`.
 * Resolves true when the receiver took it. Otherwise rejects with an Error whose `.code` says why:
 * 'HTTP <status>' for a refusal (429 when there were too many), 'timeout' after FEEDBACK_TIMEOUT_MS,
 * 'network' when the receiver could not be reached at all.
 */
async function sendFeedback(fetchImpl, endpoint, report) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null
  const timer = controller ? setTimeout(() => controller.abort(), FEEDBACK_TIMEOUT_MS) : null
  try {
    const response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(report),
      mode: 'cors',
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
      cache: 'no-store',
      signal: controller ? controller.signal : undefined,
    })
    if (!response.ok) throw notSent('HTTP ' + response.status)
    return true
  } catch (error) {
    if (error && error.notSent === true) throw error
    // Whatever fetch threw is not passed on: an aborted call carries a numeric `code` of its own.
    throw notSent(controller && controller.signal.aborted ? 'timeout' : 'network')
  } finally {
    if (timer !== null) clearTimeout(timer)
  }
}

module.exports = { getJson, postJson, fetchStatus, fetchChangelog, fetchModels, postModels, tryRecall, sendFeedback }
