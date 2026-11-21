'use strict'
// The settings page itself: the summary at the top, the tabs, and the bar that saves.
//
// State kept here, and nowhere else:
//   tab     which tab is shown
//   draft   the staged edits (key → { value } | { unset: true }); nothing is written until Save
//   phase   idle | saving | saved | failed, the life of one save
//   open    which folding groups of the Advanced tab are open
//   report  the feedback a person is writing (view/feedback.cjs), kept while they look at other tabs
//   trial   the sentence being tried and the last answer (view/trial.cjs), kept the same way
//
// The page keeps its shape whatever the settings form is doing. While the form is loading,
// unavailable (the host serves no settings for this row) or read-only, the summary, the model list,
// the changelog and the feedback tab still work; the settings tabs then say what is the matter
// instead of showing rows nobody can use.

const { h, useEffect, useState } = require('../react.cjs')
const { ID_PREFIX, LINKS, SAVED_NOTE_MS } = require('../constants.cjs')
const { TABS } = require('../layout.cjs')
const { useChangelog, useStatus } = require('../hooks.cjs')
const { buildOps, saveDraft } = require('../model/form.cjs')
const { summarise } = require('../model/overview.cjs')
const { pageView, savable } = require('../model/page.cjs')
const { Button } = require('../kit/controls.cjs')
const { Icon } = require('../kit/icons.cjs')
const { Notice, Tabs, TabPanel, tabId } = require('../kit/layout.cjs')
const { emptyFeedback, FeedbackTab } = require('./feedback.cjs')
const { Hero } = require('./hero.cjs')
const { ModelsPanel } = require('./models.cjs')
const { SettingsTab } = require('./settings-tab.cjs')
const { TrialPanel } = require('./trial.cjs')
const { OverviewTab, ChangelogTab } = require('./tabs.cjs')

/**
 * The bar that saves. It is not there while nothing is staged; after a save it confirms for a moment.
 * @param {{ t: Function, count: number, invalid: boolean, phase: string, onSave: () => void, onDiscard: () => void }} props
 *   `count` is the number of staged edits a save would write.
 */
function SaveBar(props) {
  const { t, count, invalid, phase } = props
  if (count === 0) {
    if (phase !== 'saved') return null
    return h('div', { className: 'fmg-savebar fmg-savebar--done', role: 'status' }, h(Icon, { name: 'check' }), t('saved'))
  }
  const saving = phase === 'saving'
  return h('div', { className: 'fmg-savebar', role: 'region', 'aria-label': t('saveChanges') },
    h('div', { className: 'fmg-savebar__text', role: 'status' },
      count === 1 ? t('unsavedOne') : t('unsaved', { count }),
      phase === 'failed' ? h('span', { className: 'fmg-row__error' }, t('saveFailed')) : null,
      invalid ? h('span', { className: 'fmg-row__error' }, t('fixFirst')) : null),
    h('div', { className: 'fmg-actions' },
      h(Button, { disabled: saving, onClick: props.onDiscard }, t('discard')),
      h(Button, { variant: 'primary', disabled: saving || invalid, onClick: props.onSave }, saving ? t('saving') : t('saveChanges'))))
}

/**
 * The line under the page: the installed version, where to read more, and the way to the Feedback tab.
 * @param {{ t: Function, version?: string, onFeedback: () => void }} props
 */
function Footer(props) {
  const { t, version } = props
  const link = (href, text) => h('a', { className: 'fmg-link', href, target: '_blank', rel: 'noreferrer noopener' }, text)
  return h('footer', { className: 'fmg-foot' },
    version ? h('span', null, 'dsh-feiyu-memgate ' + version) : null,
    link(LINKS.docs, t('footDocs')),
    link(LINKS.source, t('footSource')),
    h(Button, { variant: 'text', onClick: props.onFeedback }, t('footFeedback')))
}

/**
 * The page.
 * @param {{ t: Function, form?: { state: object, mutate: Function }, fetch?: Function,
 *   feedback?: { endpoint?: string, send?: Function, copy?: Function } }} props
 *   `form` is the host's settings form for the plugin's row (absent when the host serves none);
 *   `fetch` replaces the browser's fetch for the host's routes in tests and in the preview;
 *   `feedback` does the same for the Feedback tab (view/feedback.cjs).
 */
function SettingsBody(props) {
  const { t, form } = props
  const lang = t('lang') === 'zh' ? 'zh' : 'en'
  const fetchImpl = props.fetch || ((...args) => fetch(...args))
  const [tab, setTab] = useState('overview')
  const [draft, setDraft] = useState({})
  const [phase, setPhase] = useState('idle')
  const [open, setOpen] = useState({})
  const [report, setReport] = useState(emptyFeedback)
  const [trial, setTrial] = useState(() => ({ text: '', result: null }))
  const status = useStatus(fetchImpl)
  const changelog = useChangelog(fetchImpl)

  // The "Saved" confirmation goes away by itself.
  useEffect(() => {
    if (phase !== 'saved') return undefined
    const timer = setTimeout(() => setPhase((current) => (current === 'saved' ? 'idle' : current)), SAVED_NOTE_MS)
    return () => clearTimeout(timer)
  }, [phase])

  const state = form && form.state
  const ready = Boolean(state && state.status === 'ready')
  const writable = ready && state.writable !== false
  const disabled = !writable || phase === 'saving'
  const secrets = status.data && status.data.secrets
  const tabViews = ready ? pageView(state, draft, secrets) : []
  // What a save writes: the staged edits, minus those nobody can see and the field would refuse.
  const toSave = ready ? savable(draft, tabViews) : {}
  const count = buildOps(toSave).length
  const invalid = tabViews.some((tabView) => tabView.invalid > 0)

  const edit = (k, value) => {
    setPhase('idle')
    setDraft((previous) => Object.assign({}, previous, { [k]: { value } }))
  }
  const reset = (k) => {
    setPhase('idle')
    setDraft((previous) => Object.assign({}, previous, { [k]: { unset: true } }))
  }
  const discard = () => {
    setPhase('idle')
    setDraft({})
  }
  const save = () => {
    if (count === 0 || invalid || disabled) return
    setPhase('saving')
    saveDraft(form, toSave).then((accepted) => {
      if (!accepted) return setPhase('failed')
      setDraft({})
      setPhase('saved')
      status.refreshSoon() // the gateway restarts after most changes
    }, () => setPhase('failed'))
  }

  const tabs = [{ id: 'overview', label: t('tab.overview') }]
  for (const entry of TABS) {
    const view = tabViews.find((tabView) => tabView.tab.id === entry.id)
    const mark = view && view.invalid > 0 ? 'invalid' : view && view.staged > 0 ? 'edited' : null
    tabs.push({ id: entry.id, label: entry.label[lang] || entry.label.en, mark, markLabel: mark ? t(mark === 'invalid' ? 'fixFirst' : 'tabEdited') : undefined })
  }
  tabs.push({ id: 'changelog', label: t('tab.changelog') })
  tabs.push({ id: 'feedback', label: t('tab.feedback') })

  // The footer's way to the Feedback tab: select it and move the keyboard there, which also brings
  // the tab bar into view from the bottom of a long tab.
  const openFeedback = () => {
    setTab('feedback')
    const button = typeof document === 'undefined' ? null : document.getElementById(tabId(ID_PREFIX, 'feedback'))
    if (button) button.focus()
  }

  // The model list and the recall trial do not depend on the settings form: they are built once and placed by the layout.
  const panels = { models: h(ModelsPanel, { t, lang, fetch: fetchImpl, onChange: status.refreshSoon }),
    trial: h(TrialPanel, {
      t, fetch: fetchImpl, text: trial.text, result: trial.result,
      onText: (text) => setTrial((previous) => Object.assign({}, previous, { text })),
      onResult: (result) => setTrial((previous) => Object.assign({}, previous, { result })),
    }) }
  let content
  if (tab === 'overview') content = h(OverviewTab, { t, status })
  else if (tab === 'changelog') content = h(ChangelogTab, { t, state: changelog })
  else if (tab === 'feedback') {
    // `feedback` replaces the receiver's address, its fetch and the clipboard in tests and in the preview.
    const io = props.feedback || {}
    content = h(FeedbackTab, {
      t, status, draft: report, endpoint: io.endpoint, send: io.send, copy: io.copy,
      onChange: (patch) => setReport((previous) => Object.assign({}, previous, patch)),
    })
  }
  else if (ready) {
    content = h(SettingsTab, {
      t, lang, disabled, readOnly: !writable, edit, reset, open, panels,
      tabView: tabViews.find((tabView) => tabView.tab.id === tab),
      onToggle: (id) => setOpen((previous) => Object.assign({}, previous, { [id]: !previous[id] })),
    })
  } else {
    // No form to show. The model list is still useful on its tab.
    const unavailable = !state || state.status === 'unavailable'
    content = [
      h(Notice, { key: 'form', tone: unavailable ? 'warn' : undefined, role: 'status' }, h('p', null, t(unavailable ? 'unavailable' : 'loading'))),
      tab === 'retrieval' ? h('section', { key: 'models', className: 'fmg-section', 'aria-label': t('modelsTitle') },
        h('div', { className: 'fmg-section__head' }, h('h4', { className: 'fmg-section__title' }, t('modelsTitle'))),
        panels.models) : null,
    ]
  }

  return h('div', { className: 'fmg-root' },
    h(Hero, { t, summary: summarise(status.data, status.error, status.loading) }),
    h(Tabs, { idPrefix: ID_PREFIX, label: t('tabsLabel'), tabs, value: tab, onChange: setTab }),
    h(TabPanel, { idPrefix: ID_PREFIX, id: tab }, content),
    h(SaveBar, { t, count, invalid, phase, onSave: save, onDiscard: discard }),
    h(Footer, { t, version: status.data && status.data.version, onFeedback: openFeedback }))
}

/** The slot entry: the one-liner for `summary`, the page for `page`. */
function SettingsPage(props) {
  const t = typeof props.t === 'function' ? props.t : typeof props.translate === 'function' ? props.translate : (key) => key
  if (props.view === 'summary') return t('summary')
  return h(SettingsBody, { t, form: props.form, fetch: props.fetch, feedback: props.feedback })
}

module.exports = { SaveBar, Footer, SettingsBody, SettingsPage }
