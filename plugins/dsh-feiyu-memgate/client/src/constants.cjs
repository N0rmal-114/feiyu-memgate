'use strict'

// Names and numbers the page agrees on with the host half: the settings namespace, the slot key,
// the three routes and how often each one is polled. Plain values only; no logic, no host modules.

const NS = 'feiyuMemgate'
const ROW_KEY = 'dsh-feiyu-memgate#feiyu-memgate'
const STATUS_ROUTE = 'feiyu-memgate/status'
const STATUS_POLL_MS = 10000
const CHANGELOG_ROUTE = 'feiyu-memgate/changelog'
// The model list: GET reads it, POST is a click (install, cancel, use, remove); src/setup.js.
const MODELS_ROUTE = 'feiyu-memgate/models'
const MODELS_POLL_MS = 10000
const MODELS_BUSY_POLL_MS = 1500

module.exports = { NS, ROW_KEY, STATUS_ROUTE, STATUS_POLL_MS, CHANGELOG_ROUTE, MODELS_ROUTE, MODELS_POLL_MS, MODELS_BUSY_POLL_MS }
