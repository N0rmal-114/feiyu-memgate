'use strict'
// The recall trial (Retrieval tab): type one sentence and see which memories the gateway looked at for
// it, each with its score and, for the ones it left out, the step that removed it. Nothing is changed
// by a trial and it does not count as a use of a memory; the host half's try route (src/plugin.js)
// asks the gateway for an explained recall.
//
// The sentence and the last answer are kept by the page (view/page.cjs), so that looking at another
// tab does not lose them: `text` and `onText` are the draft, `result` and `onResult` the answer.

const { h } = require('../react.cjs')
const { ID_PREFIX, TRIAL_MAX_CHARS } = require('../constants.cjs')
const { useTrial } = require('../hooks.cjs')
const { tryRecall } = require('../net.cjs')
const { errorKey, rows, score: scoreText, summary, usable } = require('../model/trial.cjs')
const { Button, Tag, TextInput } = require('../kit/controls.cjs')
const { Group, Notice } = require('../kit/layout.cjs')

/** One memory the gateway looked at. @param {{ t: Function, row: object }} props a row of model/trial.cjs rows() */
function TrialRow(props) {
  const { t, row } = props
  const left = !row.delivered
  const found = scoreText(row.found)
  const scores = row.moved ? found + ' → ' + scoreText(row.score) : found
  return h('li', { className: 'fmg-trial__row', 'data-left': left ? 'true' : 'false' },
    h('p', { className: 'fmg-trial__text' }, row.text === null ? t('trial.noText') : row.text + (row.cut ? '…' : '')),
    h('div', { className: 'fmg-trial__facts' },
      row.date ? h('span', null, row.date) : null,
      h('span', null, scores),
      row.facts.map((fact) => h('span', { key: fact.key }, t(fact.key, fact.values))),
      left ? h(Tag, null, t('trial.leftOut')) : h(Tag, { tone: 'ok' }, t('trial.delivered')),
      row.reason ? h('span', { className: 'fmg-trial__reason' }, t(row.reason.key, row.reason.values)) : null,
      row.linked ? h(Tag, { tone: 'sea' }, t('trial.linked')) : null))
}

/**
 * @param {{ t: Function, fetch?: Function, text: string, onText: (text: string) => void,
 *   result?: { state: string, answer: object | null, code: string | null },
 *   onResult?: (result: object) => void, run?: (query: string) => Promise<object> }} props
 *   `fetch` replaces the browser's fetch for the try route in tests and in the preview; `run` replaces
 *   the whole call (a test that needs no route at all).
 */
function TrialPanel(props) {
  const { t, text, onText } = props
  const trial = useTrial(props.run || ((query) => tryRecall(query, props.fetch)), props.result, props.onResult)
  const busy = trial.state === 'busy'
  const ready = usable(text)
  const go = () => {
    if (ready && !busy) trial.start(text.trim())
  }

  let outcome = null
  if (trial.state === 'failed') {
    outcome = trial.code === 'no-recall'
      ? h(Notice, { role: 'status' }, h('p', null, t(errorKey(trial.code))))
      : h(Notice, { tone: 'warn', role: 'alert' }, h('p', null, t(errorKey(trial.code))))
  } else if (trial.state === 'done' && trial.answer) {
    const answer = trial.answer
    const count = summary(answer)
    const list = rows(answer)
    const partial = answer.failed > 0 || answer.busy > 0
      ? h(Notice, { key: 'partial', tone: 'warn', role: 'status' }, h('p', null, t('trial.partial')))
      : null
    outcome = count.found === 0
      ? [h('p', { key: 'none', className: 'fmg-note', role: 'status' }, t('trial.none')), partial]
      : [
        h('p', { key: 'summary', className: 'fmg-note', role: 'status' }, t('trial.summary', { found: count.found, given: count.given })),
        partial,
        h(Group, { key: 'rows' }, h('ul', { className: 'fmg-rows' }, list.map((row) => h(TrialRow, { key: row.key, t, row })))),
        h('p', { key: 'note', className: 'fmg-note' }, t('trial.note')),
      ]
  }

  return h('div', { className: 'fmg-trial' },
    h('p', { className: 'fmg-note' }, t('trial.intro')),
    h('form', { className: 'fmg-trial__form', onSubmit: (event) => { if (event && event.preventDefault) event.preventDefault(); go() } },
      h(TextInput, {
        id: ID_PREFIX + '-trial-text', value: text, maxLength: TRIAL_MAX_CHARS, label: t('trial.inputLabel'),
        placeholder: t('trial.placeholder'), onChange: onText,
      }),
      h(Button, { variant: 'primary', disabled: busy || !ready, onClick: go }, t(busy ? 'trial.busy' : 'trial.run'))),
    outcome)
}

module.exports = { TrialRow, TrialPanel }
