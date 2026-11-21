'use strict'
// The Feedback tab: a few words to the people who make the plugin.
//
// The tab writes a report and hands it to the person's browser; it sends nothing itself. The button
// is a plain link to GitHub's new-issue page with the title and the text filled in
// (model/feedback.cjs builds the address), so the person reads the report once more on GitHub and
// submits it there under their own account, or closes the page and nothing has happened.
//
// What is attached besides the person's own words is shown in full before anything leaves: the
// environment lines are listed under the switch that attaches them, and they are the same lines that
// go into the report.
//
// The draft (kind, text, whether to attach the environment) is kept by the page (view/page.cjs), so
// that looking something up on another tab does not lose what was written.

const { h } = require('../react.cjs')
const { ID_PREFIX, LINKS } = require('../constants.cjs')
const { useCopy } = require('../hooks.cjs')
const { KINDS, MAX_TEXT, compose, environment, reportText } = require('../model/feedback.cjs')
const { Button, Choices, Switch, TextArea } = require('../kit/controls.cjs')
const { Icon } = require('../kit/icons.cjs')
const { Facts, Group, Notice, Reveal } = require('../kit/layout.cjs')

// From this share of the limit on, the tab says how much room is left.
const COUNT_FROM = 0.8

/** The draft a page starts with: an idea, nothing written, the environment attached. */
function emptyFeedback() {
  return { kind: KINDS[0], text: '', environment: true }
}

/**
 * @param {{ t: Function, status: { data: object | null }, draft: { kind: string, text: string, environment: boolean },
 *   onChange: (patch: object) => void, copy?: (text: string) => Promise<boolean> }} props
 *   `onChange` merges a patch into the draft; `copy` replaces the clipboard in tests.
 */
function FeedbackTab(props) {
  const { t, status, draft, onChange } = props
  const copier = useCopy(props.copy)
  const rows = environment(status.data)
  const attached = draft.environment && rows.length > 0
  const report = compose({ kind: draft.kind, text: draft.text, environment: attached ? rows : [], cutNote: t('feedback.cutNote') })
  const whole = reportText(report)
  const length = Array.from(draft.text).length

  const kindId = ID_PREFIX + '-feedback-kind'
  const textId = ID_PREFIX + '-feedback-text'
  const envId = ID_PREFIX + '-feedback-environment'
  const title = t('feedback.title')

  // A link when there is something to send, a disabled button until then (a link cannot be disabled).
  const submitText = [t('feedback.submit'), h(Icon, { key: 'icon', name: 'external', size: 13 })]
  const submit = report.ready
    ? h('a', {
      className: 'fmg-button fmg-button--primary',
      href: report.url,
      target: '_blank',
      rel: 'noreferrer noopener',
      // A report too long for an address travels by the clipboard: copy it on the way out.
      onClick: report.clipped ? () => { copier.copy(whole) } : undefined,
    }, submitText)
    : h(Button, { variant: 'primary', disabled: true }, submitText)

  return h('section', { className: 'fmg-section', 'aria-label': title },
    h('div', { className: 'fmg-section__head' },
      h('h4', { className: 'fmg-section__title' }, title),
      h('p', { className: 'fmg-note' }, t('feedback.intro'))),
    h(Group, null,
      h('div', { className: 'fmg-row fmg-row--stack' },
        h('div', { className: 'fmg-row__text' },
          h('div', { className: 'fmg-row__label' }, h('span', { id: kindId + '-label' }, t('feedback.kind')))),
        h('div', { className: 'fmg-row__control' },
          h(Choices, {
            labelledBy: kindId + '-label',
            value: draft.kind,
            options: KINDS.map((kind) => ({ value: kind, name: t('feedback.kind.' + kind), note: t('feedback.kind.' + kind + '.note') })),
            onChange: (kind) => onChange({ kind }),
          }))),
      h('div', { className: 'fmg-row fmg-row--stack' },
        h('div', { className: 'fmg-row__text' },
          h('div', { className: 'fmg-row__label' }, h('label', { htmlFor: textId }, t('feedback.text'))),
          h('p', { className: 'fmg-row__help', id: textId + '-help' }, t('feedback.hint.' + draft.kind))),
        h('div', { className: 'fmg-row__control fmg-row__control--stack' },
          h(TextArea, { id: textId, value: draft.text, rows: 7, maxLength: MAX_TEXT, describedBy: textId + '-help', onChange: (text) => onChange({ text }) }),
          length >= MAX_TEXT * COUNT_FROM
            ? h('span', { className: 'fmg-quiet', role: 'status' }, t('feedback.count', { count: length, max: MAX_TEXT }))
            : null)),
      h('div', { className: 'fmg-row' },
        h('div', { className: 'fmg-row__text' },
          h('div', { className: 'fmg-row__label' }, h('label', { htmlFor: envId }, t('feedback.env'))),
          h('p', { className: 'fmg-row__help', id: envId + '-help' }, rows.length ? t('feedback.env.help') : t('feedback.env.none'))),
        h('div', { className: 'fmg-row__control' },
          h(Switch, {
            id: envId, checked: attached, label: t('feedback.env'), describedBy: envId + '-help', disabled: rows.length === 0,
            onChange: (next) => onChange({ environment: next }),
          }))),
      h(Reveal, { open: attached, id: envId + '-lines' }, h(Facts, { rows }))),
    h(Notice, { role: 'note' },
      h('p', null, t('feedback.how')),
      h('p', null, t('feedback.public'))),
    report.clipped ? h(Notice, { tone: 'warn', role: 'status' }, h('p', null, t('feedback.clipped'))) : null,
    h('div', { className: 'fmg-actions' },
      submit,
      h(Button, { disabled: !report.ready, onClick: () => { copier.copy(whole) } }, t('feedback.copy')),
      copier.state ? h('span', { className: copier.state === 'copied' ? 'fmg-quiet' : 'fmg-row__error', role: 'status' },
        t(copier.state === 'copied' ? 'feedback.copied' : 'feedback.copyFailed')) : null,
      h('a', { className: 'fmg-link fmg-actions__end', href: LINKS.issues, target: '_blank', rel: 'noreferrer noopener' }, t('feedback.others'))))
}

module.exports = { emptyFeedback, FeedbackTab }
