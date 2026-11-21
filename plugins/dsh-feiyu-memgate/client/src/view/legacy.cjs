'use strict'

// The page as first written, moved here unchanged: styles, components and the settings page itself.
// Temporary: the redesigned view replaces this file. It may use React, the host primitives, the model
// modules and the hooks; nothing else may depend on it.

const { h, useEffect, useRef, useState } = require('../react.cjs')
const ui = require('../host-ui.cjs')
const { MODELS_POLL_MS, MODELS_BUSY_POLL_MS } = require('../constants.cjs')
const { FIELDS } = require('../fields.cjs')
const { fetchModels, postModels } = require('../net.cjs')
const { fieldView, buildOps, saveDraft } = require('../model/form.cjs')
const { statusRows } = require('../model/status.cjs')
const { pickText, formatSize, memoryText, modelActions, chainLine, sameProblem } = require('../model/models.cjs')
const markdown = require('../model/markdown.cjs')
const { useStatus, useChangelog } = require('../hooks.cjs')

// ------------------------------------------------------------ view

const S = {
  page: { display: 'flex', flexDirection: 'column', gap: '16px' },
  changelogList: { display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' },
  details: { padding: '8px 12px', border: '0.5px solid var(--dsw-alias-border-l2)', borderRadius: 'var(--dsw-radius-md)' },
  summary: { cursor: 'pointer', fontSize: '13px', fontWeight: 500, lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)' },
  mdBody: { marginTop: '8px', fontSize: '12px', lineHeight: 1.6, color: 'var(--dsw-alias-label-primary)', overflowWrap: 'anywhere' },
  mdP: markdown.STYLES.mdP,
  mdH: markdown.STYLES.mdH,
  mdUl: markdown.STYLES.mdUl,
  mdLi: markdown.STYLES.mdLi,
  code: markdown.STYLES.code,
  status: { padding: '12px', border: '0.5px solid var(--dsw-alias-border-l2)', borderRadius: 'var(--dsw-radius-md)', background: 'var(--dsw-alias-bg-layer-2)' },
  statusHead: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '8px' },
  title: { margin: 0, fontSize: '13px', fontWeight: 600, lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)' },
  rows: { display: 'grid', gridTemplateColumns: 'max-content 1fr', columnGap: '12px', rowGap: '4px', margin: 0, fontSize: '12px', lineHeight: 1.5 },
  dt: { color: 'var(--dsw-alias-label-secondary)' },
  dd: { margin: 0, minWidth: 0, color: 'var(--dsw-alias-label-primary)', overflowWrap: 'anywhere' },
  section: { display: 'flex', flexDirection: 'column' },
  sectionTitle: { margin: '0 0 4px', fontSize: '14px', fontWeight: 600, lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)' },
  hint: { margin: 0, fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-tertiary)' },
  note: { margin: 0, fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-secondary)' },
  field: { display: 'flex', flexDirection: 'column', gap: '6px', padding: '12px 0', borderTop: '0.5px solid var(--dsw-alias-border-l2)' },
  head: { display: 'flex', alignItems: 'center', gap: '8px' },
  label: { flex: 1, minWidth: 0, fontSize: '13px', fontWeight: 500, lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)' },
  badges: { display: 'inline-flex', alignItems: 'center', gap: '8px' },
  tag: { padding: '0 6px', borderRadius: 'var(--dsw-radius-sm)', fontSize: '11px', lineHeight: '18px', background: 'var(--dsw-alias-bg-layer-4)', color: 'var(--dsw-alias-label-secondary)' },
  reset: { border: 'none', background: 'none', padding: 0, font: 'inherit', fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-secondary)', cursor: 'pointer' },
  input: { height: '34px', padding: '0 12px', border: '0.5px solid var(--dsw-alias-border-l4)', borderRadius: 'var(--dsw-radius-md)', background: 'var(--dsw-alias-bg-layer-3)', font: 'inherit', fontSize: '13px', lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)' },
  invalid: { margin: 0, fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-state-error-primary)' },
  footer: { display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '12px', paddingTop: '12px' },
  button: { height: '32px', padding: '0 14px', border: '0.5px solid var(--dsw-alias-border-l4)', borderRadius: 'var(--dsw-radius-md)', background: 'var(--dsw-alias-bg-layer-3)', font: 'inherit', fontSize: '13px', color: 'var(--dsw-alias-label-primary)', cursor: 'pointer' },
  models: { display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' },
  model: { display: 'flex', flexDirection: 'column', gap: '6px', padding: '12px', border: '0.5px solid var(--dsw-alias-border-l2)', borderRadius: 'var(--dsw-radius-md)' },
  modelHead: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px' },
  modelName: { fontSize: '13px', fontWeight: 600, lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)', overflowWrap: 'anywhere' },
  facts: { display: 'flex', flexWrap: 'wrap', columnGap: '16px', rowGap: '2px', margin: 0, fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-secondary)' },
  actions: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px' },
  check: { display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)' },
  problem: { margin: 0, padding: '8px 12px', borderRadius: 'var(--dsw-radius-md)', fontSize: '12px', lineHeight: 1.5, background: 'var(--dsw-alias-bg-layer-4)', color: 'var(--dsw-alias-label-primary)' },
  confirm: { display: 'flex', flexDirection: 'column', gap: '8px', padding: '8px 12px', borderRadius: 'var(--dsw-radius-md)', background: 'var(--dsw-alias-bg-layer-4)' },
  link: markdown.STYLES.link,
}

function Tag(props) {
  return ui.Tag ? h(ui.Tag, { tone: props.tone || 'neutral' }, props.children) : h('span', { style: S.tag }, props.children)
}

function SmallButton(props) {
  if (ui.Button) return h(ui.Button, { variant: 'outline', size: 'sm', type: 'button', disabled: props.disabled, onClick: props.onClick }, props.children)
  return h('button', { type: 'button', style: S.button, disabled: props.disabled, onClick: props.onClick }, props.children)
}

/** Label, change tag and reset, control, then the hint or the reason the draft is refused. */
function FieldFrame(props) {
  const { t, view, disabled } = props
  return h('div', { style: S.field },
    h('div', { style: S.head },
      h('label', { style: S.label, htmlFor: props.id }, t('f.' + view.k)),
      view.overridden
        ? h('span', { style: S.badges }, h(Tag, null, t('overridden')),
          h('button', { type: 'button', style: S.reset, disabled, onClick: props.onReset }, t('reset')))
        : null,
      props.inline || null),
    props.control || null,
    view.invalid ? h('p', { style: S.invalid }, props.invalidText) : h('p', { style: S.hint }, props.hint !== undefined ? props.hint : t('h.' + view.k)))
}

function invalidText(t, field) {
  return field.kind === 'integer' ? t('invalidInteger', { min: field.min, max: field.max }) : t('invalidUrl')
}

function Field(props) {
  const { t, view, disabled, edit, reset } = props
  const field = view.field
  const id = 'feiyu-memgate-' + view.k.replace('.', '-')
  if (view.secret) {
    const stateLabel = view.configured === null ? '' : view.configured ? t('secretSet') : t('secretUnset')
    const clear = h('button', { type: 'button', style: S.reset, disabled: disabled || view.clearing, onClick: () => reset(view.k) }, t('secretClear'))
    const hint = view.clearing ? t('secretClearing') : t('h.' + view.k) + ' ' + t('secretHint')
    if (ui.SettingsSecretField) {
      return h('div', null,
        h(ui.SettingsSecretField, { id, label: t('f.' + view.k), hint, text: view.text, disabled, configured: view.configured === true, stateLabel, onEdit: (text) => edit(view.k, text) }),
        h('div', { style: S.badges }, clear))
    }
    return h(FieldFrame, {
      t, view, id, disabled, hint,
      inline: h('span', { style: S.badges }, stateLabel ? h(Tag, null, stateLabel) : null, clear),
      control: h('input', { id, type: 'password', autoComplete: 'new-password', style: S.input, value: view.text, disabled, onChange: (event) => edit(view.k, event.target.value) }),
    })
  }
  if (field.kind === 'boolean') {
    const label = t('f.' + view.k)
    const control = ui.Switch
      ? h(ui.Switch, { checked: view.value === true, label, disabled, onChange: (next) => edit(view.k, next) })
      : h('input', { type: 'checkbox', id, checked: view.value === true, 'aria-label': label, disabled, onChange: (event) => edit(view.k, event.target.checked) })
    return h(FieldFrame, { t, view, id, disabled, inline: control, onReset: () => reset(view.k) })
  }
  if (field.kind === 'choice') {
    const control = h('select', { id, style: S.input, value: view.value, disabled, onChange: (event) => edit(view.k, event.target.value) },
      field.options.map((option) => h('option', { key: option.value, value: option.value }, t('o.' + view.k + '.' + option.value))))
    return h(FieldFrame, { t, view, id, disabled, control, onReset: () => reset(view.k) })
  }
  if (ui.SettingsValueField) {
    return h(ui.SettingsValueField, {
      id, label: t('f.' + view.k), hint: t('h.' + view.k), text: view.text, overridden: view.overridden, invalid: view.invalid,
      overriddenLabel: t('overridden'), resetLabel: t('reset'), invalidLabel: invalidText(t, field), disabled,
      numeric: field.kind === 'integer', onEdit: (text) => edit(view.k, text), onReset: () => reset(view.k),
    })
  }
  const control = h('input', {
    id, type: 'text', style: S.input, value: view.text, disabled, 'aria-invalid': view.invalid || undefined,
    inputMode: field.kind === 'integer' ? 'numeric' : undefined, onChange: (event) => edit(view.k, event.target.value),
  })
  return h(FieldFrame, { t, view, id, disabled, control, invalidText: invalidText(t, field), onReset: () => reset(view.k) })
}

function StatusBlock(props) {
  const { t, status } = props
  let body
  if (status.data) {
    body = h('dl', { style: S.rows }, statusRows(status.data, t).flatMap((row, index) => [
      h('dt', { key: 'dt' + index, style: S.dt }, row[0]),
      h('dd', { key: 'dd' + index, style: S.dd }, row[1]),
    ]))
  } else {
    body = h('p', { style: S.note, role: 'status' }, status.loading ? t('statusLoading') : t('statusFailed', { code: status.error }))
  }
  return h('section', { style: S.status, 'aria-label': t('statusTitle') },
    h('div', { style: S.statusHead },
      h('h4', { style: S.title }, t('statusTitle')),
      h(SmallButton, { onClick: status.refresh }, t('refresh'))),
    status.data && status.error ? h('p', { style: S.note, role: 'status' }, t('statusFailed', { code: status.error })) : null,
    body)
}

function Problem(props) {
  const { problem, lang, t } = props
  if (!problem) return null
  return h('p', { style: S.problem, role: 'alert' },
    pickText(problem.title, lang), ' ', h('strong', null, t('todo')), ' ', pickText(problem.todo, lang))
}

function ModelRow(props) {
  const { t, lang, model, view, accepted, onAccept, onAction, pending } = props
  const acts = modelActions(model, view, accepted)
  const languages = (model.languages || []).map((l) => t('language.' + l) === 'language.' + l ? l : t('language.' + l)).join(lang === 'zh' ? '、' : ', ')
  // The memory the add-on measured for the running model process (loaded), never the file size.
  const memory = memoryText(model.memoryMB, t)
  const licence = model.license
    ? (model.license.url ? h('a', { href: model.license.url, target: '_blank', rel: 'noreferrer noopener', style: S.link }, model.license.name) : model.license.name)
    : '?'
  const id = 'feiyu-memgate-accept-' + model.id
  const press = (action) => () => { if (action) onAction(action, model) }
  return h('li', { style: S.model },
    h('div', { style: S.modelHead },
      h('span', { style: S.modelName }, model.id),
      model.tierLabel ? h(Tag, null, pickText(model.tierLabel, lang)) : null,
      model.status === 'unverified' ? h(Tag, { tone: 'warning' }, t('unverified')) : null,
      model.pinned ? h(Tag, null, t('pinned')) : null),
    model.note ? h('p', { style: S.note }, pickText(model.note, lang)) : null,
    h('p', { style: S.facts },
      h('span', null, t('colSize') + ' ' + formatSize(model.sizeBytes)),
      h('span', null, t('colMemory') + ' ' + memory),
      h('span', null, t('colLanguages') + ' ' + (languages || '?')),
      h('span', null, t('colLicence') + ' ', licence)),
    model.requiresAcceptance && model.state === 'absent'
      ? h('label', { style: S.check, htmlFor: id },
        h('input', { id, type: 'checkbox', checked: accepted, onChange: (event) => onAccept(model.id, event.target.checked) }),
        t('acceptLabel', { name: model.requiresAcceptance.name }), ' ',
        model.requiresAcceptance.url ? h('a', { href: model.requiresAcceptance.url, target: '_blank', rel: 'noreferrer noopener', style: S.link }, model.requiresAcceptance.url) : null)
      : null,
    h('div', { style: S.actions },
      h(SmallButton, { disabled: pending || !acts.primary.enabled, onClick: press(acts.primary.action) }, t(acts.primary.label[0], acts.primary.label[1])),
      acts.secondary ? h(SmallButton, { disabled: pending, onClick: press(acts.secondary.action) }, t(acts.secondary.label[0])) : null))
}

/** The local embedding models: what each costs, its state and its one button. */
function ModelsSection(props) {
  const { t, onChange } = props
  const fetchImpl = props.fetch
  const lang = t('lang') === 'zh' ? 'zh' : 'en'
  const [state, setState] = useState({ view: null, error: null })
  const [accepted, setAccepted] = useState({})
  const [confirm, setConfirm] = useState(null) // the model waiting for the add-on confirmation
  const [declined, setDeclined] = useState(false)
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
    // The tier and the copy follow a model that became active: read the status again.
    if (phase === 'done' && lastPhase.current && lastPhase.current !== 'done' && onChange) onChange()
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
  const installBody = (model) => {
    // `confirmed`: the person pressed the button that names the download (and, when the add-on is
    // missing, confirmed what it installs); the host route refuses an install without it.
    const body = { action: 'install', id: model.id, confirmed: true }
    if (model.requiresAcceptance) body.acceptLicence = model.requiresAcceptance.name
    return body
  }
  const onAction = (action, model) => {
    setDeclined(false)
    if (action === 'install' && state.view.addon.state === 'absent') {
      setConfirm(model.id) // the add-on comes first: say what will be installed, then wait for a yes
      return
    }
    if (action === 'install') return send(installBody(model))
    const body = { action }
    if (action !== 'cancel') body.id = model.id
    send(body)
  }

  const view = state.view
  let body
  if (!view) {
    body = h('p', { style: S.note, role: 'status' }, state.error ? t('modelsFailed', { code: state.error }) : t('modelsLoading'))
  } else {
    const waiting = confirm ? view.models.find((m) => m.id === confirm) : null
    const line = chainLine(view, t)
    body = h('div', null,
      state.error ? h('p', { style: S.note, role: 'status' }, t('modelsFailed', { code: state.error })) : null,
      view.python && view.python.problem ? h(Problem, { problem: view.python.problem, lang, t }) : null,
      line ? h('p', { style: S.note, role: 'status' }, line) : null,
      view.chain && view.chain.error ? h(Problem, { problem: view.chain.error, lang, t }) : null,
      refusal && !sameProblem(refusal, view.chain && view.chain.error) ? h(Problem, { problem: refusal, lang, t }) : null,
      declined ? h('p', { style: S.note, role: 'status' }, t('declined')) : null,
      view.builds
        ? h('div', { style: S.confirm, role: 'dialog', 'aria-label': t('phase.builds') },
          h('p', { style: S.note }, h('strong', null, t('phase.builds')), ' ', pickText(view.builds.note, lang)),
          h('div', { style: S.actions },
            h(SmallButton, { disabled: pending, onClick: () => send({ action: 'builds', allow: true, confirmed: true }) }, t('buildsAllow')),
            h(SmallButton, { disabled: pending, onClick: () => send({ action: 'builds', allow: false, confirmed: true }) }, t('buildsDeny'))))
        : null,
      waiting
        ? h('div', { style: S.confirm, role: 'dialog', 'aria-label': t('modelsTitle') },
          h('p', { style: S.note }, view.confirm
            ? pickText(view.confirm, lang).replace('{model}', waiting.id).replace('{size}', formatSize(waiting.sizeBytes))
            : t('confirmAddon', { model: waiting.id, size: formatSize(waiting.sizeBytes) })),
          h('div', { style: S.actions },
            h(SmallButton, { disabled: pending, onClick: () => { setConfirm(null); send(installBody(waiting)) } }, t('confirmYes')),
            h(SmallButton, { disabled: pending, onClick: () => { setConfirm(null); setDeclined(true) } }, t('confirmNo'))))
        : null,
      h('ul', { style: Object.assign({ listStyle: 'none', padding: 0, margin: 0 }, S.models) },
        view.models.map((model) => h(ModelRow, {
          key: model.id, t, lang, model, view, pending,
          accepted: accepted[model.id] === true,
          onAccept: (id, value) => setAccepted((previous) => Object.assign({}, previous, { [id]: value })),
          onAction,
        }))))
  }
  return h('section', { style: S.status, 'aria-label': t('modelsTitle') },
    h('div', { style: S.statusHead },
      h('h4', { style: S.title }, t('modelsTitle')),
      h(SmallButton, { onClick: () => load.current() }, t('refresh'))),
    h('p', { style: S.hint }, t('modelsIntro'), ' ',
      h('a', { href: view ? view.link : 'https://github.com/N0rmal-114/feiyu-memgate#部署档位', target: '_blank', rel: 'noreferrer noopener', style: S.link }, t('modelsLink'))),
    body)
}

/** The last section of the page: what each update changed, newest first, the newest open. Reference, not a banner. */
function ChangelogView(props) {
  const { t, state } = props
  const entries = state.data && Array.isArray(state.data.entries) ? state.data.entries.filter((entry) => entry && typeof entry.body === 'string') : []
  let body
  if (entries.length) {
    body = [
      h('p', { key: 'current', style: S.note }, t('changelogCurrent', { version: String(state.data.version || '') })),
      t('lang') === 'zh' ? null : h('p', { key: 'chinese', style: S.hint }, t('changelogChinese')),
      h('div', { key: 'entries', style: S.changelogList }, entries.map((entry, index) => {
        const date = markdown.entryDate(entry.body)
        return h('details', { key: String(entry.version || entry.title || index), open: index === 0, style: S.details },
          h('summary', { style: S.summary }, String(entry.version || entry.title || '') + (date ? ' · ' + date : '')),
          h('div', { style: S.mdBody }, markdown.renderMarkdown(entry.body)))
      })),
    ]
  } else {
    body = h('p', { style: S.note, role: 'status' }, state.loading ? t('changelogLoading') : t('changelogFailed'))
  }
  return h('section', { style: S.section, 'aria-label': t('changelogTitle') },
    h('h4', { style: S.sectionTitle }, t('changelogTitle')),
    body)
}

function SettingsBody(props) {
  const { t, form } = props
  const [draft, setDraft] = useState({})
  const [phase, setPhase] = useState('idle') // idle | saving | saved | failed
  const fetchImpl = props.fetch || ((...args) => fetch(...args))
  const status = useStatus(fetchImpl)
  const changelog = h(ChangelogView, { t, state: useChangelog(fetchImpl) })
  const state = form && form.state
  // The model list does not depend on the settings form: it shows even while the form is unavailable.
  const models = h(ModelsSection, { t, fetch: fetchImpl, onChange: status.refreshSoon })
  if (!state || state.status === 'unavailable') return h('div', { style: S.page }, h('p', { style: S.note, role: 'status' }, t('unavailable')), models, changelog)
  if (state.status !== 'ready') return h('div', { style: S.page }, h('p', { style: S.note, role: 'status' }, t('loading')), models, changelog)
  const secrets = status.data && status.data.secrets
  const groups = FIELDS.map((group) => ({ group, views: group.fields.map((field) => fieldView(field, group.group, state, draft, secrets)) }))
  const dirty = buildOps(draft).length > 0
  const invalid = groups.some((g) => g.views.some((v) => v.invalid))
  const writable = state.writable !== false
  const disabled = !writable || phase === 'saving'
  const edit = (k, value) => {
    setPhase('idle')
    setDraft((previous) => Object.assign({}, previous, { [k]: { value } }))
  }
  const reset = (k) => {
    setPhase('idle')
    setDraft((previous) => Object.assign({}, previous, { [k]: { unset: true } }))
  }
  const save = () => {
    if (!dirty || invalid || disabled) return
    setPhase('saving')
    saveDraft(form, draft).then((accepted) => {
      if (accepted) {
        setDraft({})
        setPhase('saved')
        status.refreshSoon()
      } else setPhase('failed')
    }, () => setPhase('failed'))
  }
  const sections = groups.map((g) => h('section', { key: g.group.group, style: S.section, 'aria-label': t('g.' + g.group.group) },
    h('h4', { style: S.sectionTitle }, t('g.' + g.group.group)),
    h('p', { style: S.hint }, t('gh.' + g.group.group)),
    g.views.map((view) => h(Field, { key: view.k, t, view, disabled, edit, reset }))))
  const saved = phase === 'saved' && !dirty ? h('p', { style: S.note, role: 'status' }, t('saved')) : null
  let form_
  if (ui.SettingsForm) {
    form_ = h(ui.SettingsForm, {
      labels: { unavailable: t('unavailable'), readOnly: t('readOnly'), saveFailed: t('saveFailed'), save: t('save'), saving: t('saving') },
      state: { available: true, writable, dirty, invalid, saving: phase === 'saving', failed: phase === 'failed' },
      onSave: save,
      onDiscard: () => setDraft({}),
    }, sections, saved)
  } else {
    form_ = h('div', null,
      writable ? null : h('p', { style: S.note, role: 'status' }, t('readOnly')),
      sections,
      h('div', { style: S.footer },
        phase === 'failed' ? h('p', { style: S.invalid, role: 'status' }, t('saveFailed')) : saved,
        h('button', { type: 'button', style: S.button, disabled: !dirty || invalid || disabled, onClick: save }, phase === 'saving' ? t('saving') : t('save'))))
  }
  return h('div', { style: S.page }, h(StatusBlock, { t, status }), models, form_, changelog)
}

/** The slot entry: the one-liner for `summary`, the page for `page`. */
function SettingsPage(props) {
  const t = typeof props.t === 'function' ? props.t : typeof props.translate === 'function' ? props.translate : (key) => key
  if (props.view === 'summary') return t('summary')
  return h(SettingsBody, { t, form: props.form })
}

module.exports = { S, Tag, SmallButton, FieldFrame, invalidText, Field, StatusBlock, Problem, ModelRow, ModelsSection, ChangelogView, SettingsBody, SettingsPage }
