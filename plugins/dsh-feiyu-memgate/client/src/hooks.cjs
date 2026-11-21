'use strict'

// The two React hooks that read the host half: useStatus (polled) and useChangelog (read once).
// They own the timers and the "still mounted" flag; they know nothing about how the data is drawn.

const { useEffect, useRef, useState } = require('./react.cjs')
const { STATUS_POLL_MS } = require('./constants.cjs')
const { fetchStatus, fetchChangelog } = require('./net.cjs')

/** The changelog, read once when the page mounts: { loading, data }; data stays null when the read failed. */
function useChangelog(fetchImpl) {
  const [state, setState] = useState({ loading: true, data: null })
  useEffect(() => {
    let alive = true
    fetchChangelog(fetchImpl).then(
      (data) => { if (alive) setState({ loading: false, data }) },
      () => { if (alive) setState({ loading: false, data: null }) },
    )
    return () => { alive = false }
  }, [])
  return state
}

/**
 * The status, read on mount and every STATUS_POLL_MS: { loading, data, error, refresh, refreshSoon }.
 * A failed read keeps the last data and only sets `error` (a code such as 'HTTP 403', or 'network').
 */
function useStatus(fetchImpl) {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  const alive = useRef(true)
  const load = useRef(null)
  load.current = () => fetchStatus(fetchImpl).then(
    (data) => { if (alive.current) setState({ loading: false, data, error: null }) },
    (error) => { if (alive.current) setState((previous) => ({ loading: false, data: previous.data, error: (error && error.code) || 'network' })) },
  )
  useEffect(() => {
    alive.current = true
    load.current()
    const timer = setInterval(() => load.current(), STATUS_POLL_MS)
    return () => {
      alive.current = false
      clearInterval(timer)
    }
  }, [])
  return Object.assign({}, state, {
    refresh: () => load.current(),
    // The gateway restarts after most changes: read again once it has had a moment.
    refreshSoon: () => setTimeout(() => { if (alive.current) load.current() }, 1500),
  })
}

module.exports = { useChangelog, useStatus }
