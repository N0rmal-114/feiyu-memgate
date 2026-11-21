'use strict'

// The React hooks that talk to the host half: useStatus (polled), useChangelog (read once) and
// useModels (polled, plus the clicks of the model list); useCopy, which talks to the clipboard;
// useSend, which posts a feedback report to the project's receiver; and useTrial, which asks for one
// recall to be explained.
// They own the timers and the "still mounted" flag; they know nothing about how the data is drawn.

const { useEffect, useRef, useState } = require('./react.cjs')
const { STATUS_POLL_MS, MODELS_POLL_MS, MODELS_BUSY_POLL_MS, SAVED_NOTE_MS } = require('./constants.cjs')
const { copyText } = require('./clipboard.cjs')
const { fetchStatus, fetchChangelog, fetchModels, postModels, sendFeedback } = require('./net.cjs')

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

/**
 * The local model list and its clicks: { view, error, pending, refusal, reload, send }.
 *   view     what GET feiyu-memgate/models last answered (src/setup.js view()), or null
 *   error    the code of the last failed read or click, or null; a failure keeps the last view
 *   pending  a click is on its way
 *   refusal  the problem the host half gave for the last click it refused, or null
 *   send     post one click ({ action, id?, ... }); the answer carries the new view
 * The list is read more often while a download or an installation runs. `onDone` is called once when
 * a setup reaches its end, because the retrieval tier follows a model that became active.
 */
function useModels(fetchImpl, onDone) {
  const [state, setState] = useState({ view: null, error: null })
  const [pending, setPending] = useState(false)
  const [refusal, setRefusal] = useState(null)
  const alive = useRef(true)
  const load = useRef(null)
  const lastPhase = useRef(null)
  load.current = () => fetchModels(fetchImpl).then(
    (view) => { if (alive.current) setState({ view, error: null }) },
    (error) => { if (alive.current) setState((previous) => ({ view: previous.view, error: (error && error.code) || 'network' })) },
  )
  const busy = Boolean(state.view && state.view.busy)
  useEffect(() => {
    alive.current = true
    load.current()
    return () => { alive.current = false }
  }, [])
  useEffect(() => {
    const timer = setInterval(() => load.current(), busy ? MODELS_BUSY_POLL_MS : MODELS_POLL_MS)
    return () => clearInterval(timer)
  }, [busy])
  const phase = state.view && state.view.chain ? state.view.chain.phase : null
  useEffect(() => {
    if (phase === 'done' && lastPhase.current && lastPhase.current !== 'done' && onDone) onDone()
    lastPhase.current = phase
  }, [phase])
  const send = (body) => {
    setPending(true)
    setRefusal(null)
    postModels(fetchImpl, body).then((answer) => {
      if (!alive.current) return
      setPending(false)
      if (answer && answer.view) setState({ view: answer.view, error: null })
      if (answer && answer.result && !answer.result.ok && answer.result.problem) setRefusal(answer.result.problem)
    }, (error) => {
      if (!alive.current) return
      setPending(false)
      setState((previous) => ({ view: previous.view, error: (error && error.code) || 'network' }))
    })
  }
  return { view: state.view, error: state.error, pending, refusal, reload: () => load.current(), send }
}

/**
 * Copying to the clipboard, with what became of the last copy: { state, copy }.
 *   state  null | 'copied' | 'failed'; goes back to null by itself after SAVED_NOTE_MS
 *   copy   put a text on the clipboard; returns the promise of whether it arrived
 * @param {(text: string) => Promise<boolean>} [copyImpl] replaces the clipboard in tests
 */
function useCopy(copyImpl) {
  const [state, setState] = useState(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])
  useEffect(() => {
    if (state === null) return undefined
    const timer = setTimeout(() => setState(null), SAVED_NOTE_MS)
    return () => clearTimeout(timer)
  }, [state])
  const copy = (text) => Promise.resolve((copyImpl || copyText)(text)).then(
    (copied) => {
      if (alive.current) setState(copied ? 'copied' : 'failed')
      return Boolean(copied)
    },
    () => {
      if (alive.current) setState('failed')
      return false
    },
  )
  return { state, copy }
}

/**
 * Sending a feedback report, with what became of the last one: { phase, code, send }.
 *   phase  'ready' | 'sending' | 'sent' | 'failed'
 *   code   why the last one failed ('HTTP 429', 'timeout', 'network', ...), or null
 *   send   post a report (model/feedback.cjs payload()); returns the promise of whether it was taken.
 *          A second call while one is on its way does nothing and answers false.
 * @param {string} endpoint the receiver's address
 * @param {Function} [fetchImpl] replaces the browser's fetch in tests and in the preview
 */
function useSend(endpoint, fetchImpl) {
  const [state, setState] = useState({ phase: 'ready', code: null })
  const alive = useRef(true)
  const busy = useRef(false)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])
  const send = (report) => {
    if (busy.current || !endpoint) return Promise.resolve(false)
    busy.current = true
    setState({ phase: 'sending', code: null })
    return sendFeedback(fetchImpl || ((...args) => fetch(...args)), endpoint, report).then(
      () => {
        busy.current = false
        if (alive.current) setState({ phase: 'sent', code: null })
        return true
      },
      (error) => {
        busy.current = false
        if (alive.current) setState({ phase: 'failed', code: (error && error.code) || 'network' })
        return false
      },
    )
  }
  return { phase: state.phase, code: state.code, send }
}

/**
 * One trial recall at a time, with what became of the last: { state, answer, code, start }.
 *   state   'idle' | 'busy' | 'done' | 'failed'
 *   answer  the host half's answer while `state` is 'done'
 *   code    why it failed ('unavailable', 'unsupported', 'no-recall', 'failed', 'network'), or null
 *   start   try a sentence; a second call while one is on its way does nothing
 * What became of a trial is also handed to `remember`, so that the page can keep it while a person
 * looks at another tab; `saved` is what it kept.
 * @param {(query: string) => Promise<object>} run asks the host half (the panel passes net.cjs tryRecall)
 * @param {{ state: string, answer: object | null, code: string | null }} [saved]
 * @param {(result: { state: string, answer: object | null, code: string | null }) => void} [remember]
 */
function useTrial(run, saved, remember) {
  const [result, setResult] = useState(saved || { state: 'idle', answer: null, code: null })
  const busy = useRef(false)
  const start = (query) => {
    if (busy.current) return Promise.resolve(false)
    busy.current = true
    setResult({ state: 'busy', answer: null, code: null })
    const finish = (next) => {
      busy.current = false
      setResult(next)
      if (remember) remember(next)
      return next.state === 'done'
    }
    let asked
    try {
      asked = Promise.resolve(run(query))
    } catch (error) {
      asked = Promise.reject(error)
    }
    return asked.then(
      (answer) => {
        if (answer && answer.ok === true) return finish({ state: 'done', answer, code: null })
        return finish({ state: 'failed', answer: null, code: answer && typeof answer.code === 'string' ? answer.code : 'failed' })
      },
      // A refusal of the route reads as a failed lookup; a call that did not arrive as a network error.
      (error) => finish({ state: 'failed', answer: null, code: error && typeof error.code === 'string' && error.code.indexOf('HTTP') === 0 ? 'failed' : 'network' }),
    )
  }
  return { state: result.state, answer: result.answer, code: result.code, start }
}

module.exports = { useChangelog, useStatus, useModels, useCopy, useSend, useTrial }
