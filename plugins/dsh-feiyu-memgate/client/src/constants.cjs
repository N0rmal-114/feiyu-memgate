'use strict'

// Names and numbers the page agrees on with the host half: the settings namespace, the slot key,
// the routes and how often each one is polled, and the public addresses the page links to. Plain values
// only; no logic, no host modules.

const NS = 'feiyuMemgate'
const ROW_KEY = 'dsh-feiyu-memgate#feiyu-memgate'
const STATUS_ROUTE = 'feiyu-memgate/status'
const STATUS_POLL_MS = 10000
const CHANGELOG_ROUTE = 'feiyu-memgate/changelog'
// The model list: GET reads it, POST is a click (install, cancel, use, remove); src/setup.js.
const MODELS_ROUTE = 'feiyu-memgate/models'
const MODELS_POLL_MS = 10000
const MODELS_BUSY_POLL_MS = 1500
// The mascot's drawings: GET <ART_ROUTE><mood>.webp (src/plugin.js serves the files of client/art).
const ART_ROUTE = 'feiyu-memgate/art/'
// How long the "Saved" confirmation stays before the bar goes away.
const SAVED_NOTE_MS = 2600
// The prefix of every element id the page makes, so that none can meet an id of the host's page.
const ID_PREFIX = 'feiyu-memgate'
const LINKS = {
  docs: 'https://github.com/N0rmal-114/feiyu-memgate/tree/main/plugins/dsh-feiyu-memgate#readme',
  source: 'https://github.com/N0rmal-114/feiyu-memgate',
  issues: 'https://github.com/N0rmal-114/feiyu-memgate/issues',
}

module.exports = {
  NS, ROW_KEY, STATUS_ROUTE, STATUS_POLL_MS, CHANGELOG_ROUTE, MODELS_ROUTE, MODELS_POLL_MS, MODELS_BUSY_POLL_MS,
  ART_ROUTE, SAVED_NOTE_MS, ID_PREFIX, LINKS,
}
