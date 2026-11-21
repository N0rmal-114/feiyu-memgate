'use strict'
// The local embedding models: what each costs, where it stands, and its one button.
//
// Nothing here downloads or installs by itself. A click on "Download and enable" names what will be
// fetched; when the add-on plugin is missing, the page first says what installing it brings and
// waits for a second yes. The host half checks the same things again (src/setup.js): this file is
// the conversation, not the gate.

const { h, useState } = require('../react.cjs')
const { ID_PREFIX } = require('../constants.cjs')
const { useModels } = require('../hooks.cjs')
const { pickText, formatSize, memoryText, modelActions, chainLine, sameProblem } = require('../model/models.cjs')
const { Button, Tag } = require('../kit/controls.cjs')
const { Group, Notice, Progress } = require('../kit/layout.cjs')

// Where "measured retrieval quality" leads before the host half has answered with its own link.
const QUALITY_LINK = 'https://github.com/N0rmal-114/feiyu-memgate#部署档位'

/** A failure with what to do about it, as the host half worded it ({ title, todo }, each { en, zh }). */
function Problem(props) {
  const { problem, lang, t } = props
  if (!problem) return null
  return h(Notice, { tone: 'warn', role: 'alert' },
    h('p', null, pickText(problem.title, lang), ' ', h('strong', null, t('todo')), ' ', pickText(problem.todo, lang)))
}

/** An outside link that opens in a new tab without handing the page over to it. */
function OutLink(props) {
  return h('a', { className: 'fmg-link', href: props.href, target: '_blank', rel: 'noreferrer noopener' }, props.children)
}

/**
 * One model.
 * @param {{ t: Function, lang: string, model: object, view: object, accepted: boolean, pending: boolean,
 *   onAccept: (id: string, value: boolean) => void, onAction: (action: string, model: object) => void }} props
 */
function ModelRow(props) {
  const { t, lang, model, view, accepted, onAccept, onAction, pending } = props
  const acts = modelActions(model, view, accepted)
  const languages = (model.languages || []).map((l) => (t('language.' + l) === 'language.' + l ? l : t('language.' + l))).join(lang === 'zh' ? '、' : ', ')
  // The memory the add-on measured for the running model process (loaded), never the file size.
  const memory = memoryText(model.memoryMB, t)
  const licence = model.license
    ? (model.license.url ? h(OutLink, { href: model.license.url }, model.license.name) : model.license.name)
    : '?'
  const acceptId = ID_PREFIX + '-accept-' + model.id
  const press = (action) => () => { if (action) onAction(action, model) }
  const downloading = model.state === 'downloading' && model.percent !== null && model.percent !== undefined
  // The primary action is a button only while it can lead somewhere; a state that offers nothing to
  // press ("In use", "Loading", "Downloading 42%") is said as text, not as a greyed-out button.
  const primaryLabel = t(acts.primary.label[0], acts.primary.label[1])
  const primary = acts.primary.action
    ? h(Button, { variant: 'primary', disabled: pending || !acts.primary.enabled, onClick: press(acts.primary.action) }, primaryLabel)
    : model.state === 'active' ? h(Tag, { tone: 'ok' }, primaryLabel) : h('span', { className: 'fmg-note', role: 'status' }, primaryLabel)
  return h('li', { className: 'fmg-model' },
    h('div', { className: 'fmg-model__text' },
      h('div', { className: 'fmg-model__name' },
        model.id,
        model.tierLabel ? h(Tag, null, pickText(model.tierLabel, lang)) : null,
        model.status === 'unverified' ? h(Tag, { tone: 'warn' }, t('unverified')) : null,
        model.pinned ? h(Tag, null, t('pinned')) : null),
      model.note ? h('p', { className: 'fmg-note' }, pickText(model.note, lang)) : null,
      h('p', { className: 'fmg-model__facts' },
        h('span', null, t('colSize') + ' ' + formatSize(model.sizeBytes)),
        h('span', null, t('colMemory') + ' ' + memory),
        h('span', null, t('colLanguages') + ' ' + (languages || '?')),
        h('span', null, t('colLicence') + ' ', licence))),
    h('div', { className: 'fmg-actions' },
      primary,
      acts.secondary ? h(Button, { disabled: pending, onClick: press(acts.secondary.action) }, t(acts.secondary.label[0])) : null),
    downloading ? h('div', { className: 'fmg-model__wide' }, h(Progress, { percent: model.percent, label: t('actDownloading', { percent: model.percent }) })) : null,
    model.requiresAcceptance && model.state === 'absent'
      ? h('label', { className: 'fmg-check fmg-model__wide', htmlFor: acceptId },
        h('input', { id: acceptId, type: 'checkbox', checked: accepted, onChange: (event) => onAccept(model.id, event.target.checked) }),
        t('acceptLabel', { name: model.requiresAcceptance.name }),
        model.requiresAcceptance.url ? h(OutLink, { href: model.requiresAcceptance.url }, model.requiresAcceptance.url) : null)
      : null)
}

/**
 * The panel: the introduction, whatever needs saying about the setup, and the list.
 * @param {{ t: Function, lang: string, fetch: Function, onChange?: () => void }} props
 *   `onChange` is called when a model became active (the status has changed with it).
 */
function ModelsPanel(props) {
  const { t, lang } = props
  const models = useModels(props.fetch, props.onChange)
  const [accepted, setAccepted] = useState({}) // model id → the licence box is ticked
  const [confirm, setConfirm] = useState(null) // the model waiting for the add-on confirmation
  const [declined, setDeclined] = useState(false)
  const view = models.view

  // `confirmed`: the person pressed the button that names the download (and, when the add-on is
  // missing, confirmed what it installs); the host route refuses an install without it.
  const installBody = (model) => {
    const body = { action: 'install', id: model.id, confirmed: true }
    if (model.requiresAcceptance) body.acceptLicence = model.requiresAcceptance.name
    return body
  }
  const onAction = (action, model) => {
    setDeclined(false)
    if (action === 'install' && view.addon.state === 'absent') {
      setConfirm(model.id) // the add-on comes first: say what will be installed, then wait for a yes
      return
    }
    if (action === 'install') return models.send(installBody(model))
    const body = { action }
    if (action !== 'cancel') body.id = model.id
    models.send(body)
  }

  const intro = h('p', { className: 'fmg-note' }, t('modelsIntro'), ' ', h(OutLink, { href: view ? view.link : QUALITY_LINK }, t('modelsLink')))
  if (!view) {
    return h('div', { className: 'fmg-section' }, intro,
      h('p', { className: 'fmg-note', role: 'status' }, models.error ? t('modelsFailed', { code: models.error }) : t('modelsLoading')))
  }
  const waiting = confirm ? view.models.find((model) => model.id === confirm) : null
  const line = chainLine(view, t)
  const chainError = view.chain && view.chain.error
  return h('div', { className: 'fmg-section' },
    intro,
    models.error ? h('p', { className: 'fmg-note', role: 'status' }, t('modelsFailed', { code: models.error })) : null,
    view.python && view.python.problem ? h(Problem, { problem: view.python.problem, lang, t }) : null,
    line ? h(Notice, { role: 'status' }, h('p', null, line)) : null,
    chainError ? h(Problem, { problem: chainError, lang, t }) : null,
    // The answer to a click and the stored state of the setup often carry the same failure: show it once.
    models.refusal && !sameProblem(models.refusal, chainError) ? h(Problem, { problem: models.refusal, lang, t }) : null,
    declined ? h('p', { className: 'fmg-note', role: 'status' }, t('declined')) : null,
    view.builds
      ? h(Notice, { tone: 'warn', role: 'group', label: t('phase.builds') },
        h('p', null, h('strong', null, t('phase.builds')), ' ', pickText(view.builds.note, lang)),
        h('div', { className: 'fmg-actions' },
          h(Button, { variant: 'primary', disabled: models.pending, onClick: () => models.send({ action: 'builds', allow: true, confirmed: true }) }, t('buildsAllow')),
          h(Button, { disabled: models.pending, onClick: () => models.send({ action: 'builds', allow: false, confirmed: true }) }, t('buildsDeny'))))
      : null,
    waiting
      ? h(Notice, { role: 'group', label: t('modelsTitle') },
        h('p', null, view.confirm
          ? pickText(view.confirm, lang).replace('{model}', waiting.id).replace('{size}', formatSize(waiting.sizeBytes))
          : t('confirmAddon', { model: waiting.id, size: formatSize(waiting.sizeBytes) })),
        h('div', { className: 'fmg-actions' },
          h(Button, { variant: 'primary', disabled: models.pending, onClick: () => { setConfirm(null); models.send(installBody(waiting)) } }, t('confirmYes')),
          h(Button, { disabled: models.pending, onClick: () => { setConfirm(null); setDeclined(true) } }, t('confirmNo'))))
      : null,
    h(Group, null, h('ul', { className: 'fmg-rows' },
      view.models.map((model) => h(ModelRow, {
        key: model.id, t, lang, model, view, pending: models.pending,
        accepted: accepted[model.id] === true,
        onAccept: (id, value) => setAccepted((previous) => Object.assign({}, previous, { [id]: value })),
        onAction,
      })))))
}

module.exports = { Problem, ModelRow, ModelsPanel }
