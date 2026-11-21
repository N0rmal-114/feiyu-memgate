'use strict'

// The one place that asks for the host's UI primitives (@deepseek-ai/dsh-client-ui-primitives).
// Older hosts do not export the package or some of its components, so the result is an object that
// may be empty: callers test `ui.Name` and fall back to plain elements. It must never throw.

let ui = {}
try {
  ui = require('@deepseek-ai/dsh-client-ui-primitives') || {}
} catch (error) {
  ui = {}
}

module.exports = ui
