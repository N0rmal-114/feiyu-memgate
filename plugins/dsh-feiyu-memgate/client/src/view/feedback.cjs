'use strict'
// The Feedback tab: a few words to the people who make the plugin.
//
// Where the page knows a receiver (constants.cjs FEEDBACK_ENDPOINT) the tab is one thing: write, then
// Send. Send posts the report to the project's feedback receiver (feedback-worker/), where only the
// maintainers can read it; it needs no account anywhere.
//
// Two other ways exist, and each is a click the person makes:
//   GitHub    a plain link to GitHub's new-issue page with the title and the text filled in
//             (model/feedback.cjs builds the address); the person reads it once more there and
//             submits it under their own account. Public.
//   Copy      the whole report on the clipboard, to send any other way.
// They are what the tab offers while there is no receiver, and what it falls back to when a Send
// failed, so that what was written is never stuck. Next to a working Send they are not shown.
//
// What is attached besides the person's own words is shown in full before anything leaves: the
// environment lines are listed under the switch that attaches them, and they are the same lines that
// go into the report. The contact line travels with Send only: it is never part of the public
// address or of the copied text.
//
// The draft (kind, text, contact, whether to attach the environment) is kept by the page
// (view/page.cjs), so that looking something up on another tab does not lose what was written.

const { h } = require('../react.cjs')
const { FEEDBACK_ENDPOINT, ID_PREFIX, LINKS } = require('../constants.cjs')
const { useCopy, useSend } = require('../hooks.cjs')
const { KINDS, MAX_CONTACT, MAX_TEXT, compose, environment, payload, reportText, usableEndpoint } = require('../model/feedback.cjs')
const { Button, Choices, Switch, TextArea, TextInput } = require('../kit/controls.cjs')
const { Icon } = require('../kit/icons.cjs')
const { Facts, Group, Notice, Reveal } = require('../kit/layout.cjs')

// From this share of the limit on, the tab says how much room is left.
const COUNT_FROM = 0.8

/** The draft a page starts with: an idea, nothing written, the environment attached. */
function emptyFeedback() {
  return { kind: KINDS[0], text: '', contact: '', environment: true }
}

/**
 * @param {{ t: Function, status: { data: object | null },
 *   draft: { kind: string, text: string, contact: string, environment: boolean },
 *   onChange: (patch: object) => void, endpoint?: string, send?: Function,
 *   copy?: (text: string) => Promise<boolean> }} props
 *   `onChange` merges a patch into the draft. `endpoint` replaces the receiver's address (tests and
 *   the preview; an empty text means there is none), `send` the browser's fetch for that one call,
 *   `copy` the clipboard.
 */
function FeedbackTab(props) {
  const { t, status, draft, onChange } = props
  const endpoint = usableEndpoint(props.endpoint === undefined ? FEEDBACK_ENDPOINT : props.endpoint)
  const copier = useCopy(props.copy)
  const sender = useSend(endpoint, props.send)
  const rows = environment(status.data)
  const attached = draft.environment && rows.length > 0
  const report = compose({ kind: draft.kind, text: draft.text, environment: attached ? rows : [], cutNote: t('feedback.cutNote') })
  const whole = reportText(report)
  const length = Array.from(draft.text).length
  const sending = sender.phase === 'sending'

  const kindId = ID_PREFIX + '-feedback-kind'
  const textId = ID_PREFIX + '-feedback-text'
  const contactId = ID_PREFIX + '-feedback-contact'
  const envId = ID_PREFIX + '-feedback-environment'
  const title = t('feedback.title')

  // Send: on success the text is cleared, so the same report cannot be sent twice by a second click.
  const send = () => {
    sender.send(payload(draft, attached ? rows : [])).then((sent) => { if (sent) onChange({ text: '' }) })
  }

  // The other ways are on the tab while there is no receiver, and after a Send that failed.
  const failed = sender.phase === 'failed'
  const otherWays = !endpoint || failed

  // The way to GitHub: a link when there is something to take there, a disabled button until then
  // (a link cannot be disabled). It is the main action only while there is no receiver.
  const githubText = [t('feedback.submit'), h(Icon, { key: 'icon', name: 'external', size: 13 })]
  const github = report.ready
    ? h('a', {
      className: endpoint ? 'fmg-button' : 'fmg-button fmg-button--primary',
      href: report.url,
      target: '_blank',
      rel: 'noreferrer noopener',
      // A report too long for an address travels by the clipboard: copy it on the way out.
      onClick: report.clipped ? () => { copier.copy(whole) } : undefined,
    }, githubText)
    : h(Button, { variant: endpoint ? undefined : 'primary', disabled: true }, githubText)

  const copyButton = h(Button, { key: 'copy', disabled: !report.ready, onClick: () => { copier.copy(whole) } }, t('feedback.copy'))
  const copySaid = copier.state ? h('span', { key: 'said', className: copier.state === 'copied' ? 'fmg-quiet' : 'fmg-row__error', role: 'status' },
    t(copier.state === 'copied' ? 'feedback.copied' : 'feedback.copyFailed')) : null

  // What became of the last Send. The thanks stay until the person starts writing again; a failure
  // says why and brings the other two ways with it.
  let outcome = null
  if (sender.phase === 'sent' && !report.ready) {
    outcome = h(Notice, { role: 'status' }, h('p', { className: 'fmg-actions' }, h(Icon, { name: 'check' }), t('feedback.sent')))
  } else if (failed) {
    outcome = h(Notice, { tone: 'warn', role: 'alert' },
      h('p', null, sender.code === 'HTTP 429' ? t('feedback.failedBusy') : t('feedback.failed', { code: sender.code || '' })),
      h('div', { className: 'fmg-actions' }, github, copyButton, copySaid))
  }

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
            disabled: sending,
            options: KINDS.map((kind) => ({ value: kind, name: t('feedback.kind.' + kind), note: t('feedback.kind.' + kind + '.note') })),
            onChange: (kind) => onChange({ kind }),
          }))),
      h('div', { className: 'fmg-row fmg-row--stack' },
        h('div', { className: 'fmg-row__text' },
          h('div', { className: 'fmg-row__label' }, h('label', { htmlFor: textId }, t('feedback.text'))),
          h('p', { className: 'fmg-row__help', id: textId + '-help' }, t('feedback.hint.' + draft.kind))),
        h('div', { className: 'fmg-row__control fmg-row__control--stack' },
          h(TextArea, { id: textId, value: draft.text, rows: 7, maxLength: MAX_TEXT, disabled: sending, describedBy: textId + '-help', onChange: (text) => onChange({ text }) }),
          length >= MAX_TEXT * COUNT_FROM
            ? h('span', { className: 'fmg-quiet', role: 'status' }, t('feedback.count', { count: length, max: MAX_TEXT }))
            : null)),
      // The contact line has a use only where an answer cannot reach the person otherwise.
      endpoint ? h('div', { className: 'fmg-row fmg-row--stack' },
        h('div', { className: 'fmg-row__text' },
          h('div', { className: 'fmg-row__label' }, h('label', { htmlFor: contactId }, t('feedback.contact'))),
          h('p', { className: 'fmg-row__help', id: contactId + '-help' }, t('feedback.contact.help'))),
        h('div', { className: 'fmg-row__control' },
          h('div', { className: 'fmg-input-line' },
            h(TextInput, { id: contactId, value: draft.contact || '', maxLength: MAX_CONTACT, disabled: sending, describedBy: contactId + '-help', onChange: (contact) => onChange({ contact }) })))) : null,
      h('div', { className: 'fmg-row' },
        h('div', { className: 'fmg-row__text' },
          h('div', { className: 'fmg-row__label' }, h('label', { htmlFor: envId }, t('feedback.env'))),
          h('p', { className: 'fmg-row__help', id: envId + '-help' }, rows.length ? t('feedback.env.help') : t('feedback.env.none'))),
        h('div', { className: 'fmg-row__control' },
          h(Switch, {
            id: envId, checked: attached, label: t('feedback.env'), describedBy: envId + '-help', disabled: rows.length === 0 || sending,
            onChange: (next) => onChange({ environment: next }),
          }))),
      h(Reveal, { open: attached, id: envId + '-lines' }, h(Facts, { rows }))),
    endpoint
      ? h(Notice, { role: 'note' }, h('p', null, t('feedback.howDirect')))
      : h(Notice, { role: 'note' }, h('p', null, t('feedback.how')), h('p', null, t('feedback.public'))),
    outcome,
    // The warning is about the address for GitHub, so it is shown where that link is.
    report.clipped && otherWays ? h(Notice, { tone: 'warn', role: 'status' }, h('p', null, t('feedback.clipped'))) : null,
    endpoint
      ? h('div', { className: 'fmg-actions' },
        h(Button, { variant: 'primary', disabled: !report.ready || sending, onClick: send }, t(sending ? 'feedback.sending' : 'feedback.send')))
      : h('div', { className: 'fmg-actions' },
        github, copyButton, copySaid,
        h('a', { className: 'fmg-link fmg-actions__end', href: LINKS.issues, target: '_blank', rel: 'noreferrer noopener' }, t('feedback.others'))))
}

module.exports = { emptyFeedback, FeedbackTab }
