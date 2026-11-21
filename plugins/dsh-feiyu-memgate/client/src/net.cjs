'use strict'

// The page's three calls to the host half, over a `fetch` passed in by the caller (so tests can fake it).
// A failed answer becomes an Error whose `.code` is 'HTTP <status>'; a network failure rejects as it
// comes. The routes are document-relative and owner-only (src/plugin.js). No React, no DOM.

const { STATUS_ROUTE, CHANGELOG_ROUTE, MODELS_ROUTE } = require('./constants.cjs')

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

module.exports = { getJson, postJson, fetchStatus, fetchChangelog, fetchModels, postModels }
