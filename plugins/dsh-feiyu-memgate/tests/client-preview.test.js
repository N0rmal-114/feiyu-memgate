// The made-up answers of the page preview (scripts/preview/scenarios.mjs) against the page's own pure
// parts and components: every scenario can be summarised and drawn, the model list of each goes through
// the model rows, and the settings the preview starts with are real settings with valid values. The
// preview server is not started here.
import test from 'node:test'
import assert from 'node:assert/strict'
import { SCENARIOS, USER_SETTINGS, modelsFor } from '../scripts/preview/scenarios.mjs'
import { expand, findAll, load, plain, textOf, translator, withClass } from './helpers/client-harness.js'

const shared = load()
const I = shared.internals
const DICT = plain(I.dictionaries())
const LANGS = ['en', 'zh']
const tr = (lang) => translator(DICT[lang])

// A text that shows a hole in the data: an undefined, a null or an object turned into text.
const clean = (text, what) => {
  assert.ok(text.length > 0, `${what}: empty`)
  assert.ok(!/undefined|\[object Object\]|NaN|null/.test(text), `${what}: ${text.match(/.{0,30}(undefined|\[object Object\]|NaN|null).{0,30}/)}`)
}

const objectScenarios = Object.entries(SCENARIOS).filter(([, scenario]) => scenario.status && typeof scenario.status === 'object')

test('the preview has scenarios, each with a label, and its status is an object or an HTTP error number', () => {
  assert.ok(Object.keys(SCENARIOS).length >= 8)
  assert.ok('ready-local' in SCENARIOS, 'the default scenario of the preview page')
  for (const [name, scenario] of Object.entries(SCENARIOS)) {
    assert.ok(typeof scenario.label === 'string' && scenario.label.trim(), name)
    assert.ok(typeof scenario.status === 'number' ? scenario.status >= 400 : scenario.status && typeof scenario.status === 'object', name)
  }
  assert.ok(objectScenarios.length >= 7)
  assert.ok(Object.values(SCENARIOS).some((scenario) => typeof scenario.status === 'number'), 'a scenario for a route that does not answer')
})

test('every scenario’s status can be summarised: a mood of the page, and keys both dictionaries have', () => {
  const moods = new Set()
  for (const [name, scenario] of objectScenarios) {
    const summary = plain(I.summarise(scenario.status, null, false))
    moods.add(summary.mood)
    assert.ok(['ready', 'busy', 'asleep', 'trouble'].includes(summary.mood), name)
    for (const lang of LANGS) {
      for (const part of [summary.headline, summary.line, ...summary.facts]) {
        const text = tr(lang)(part.key, part.params)
        assert.notEqual(text, part.key, `${name} ${lang}: no text for ${part.key}`)
        clean(text, `${name} ${lang} ${part.key}`)
        assert.ok(!text.includes('{'), `${name} ${lang}: a parameter of ${part.key} is missing: ${text}`)
      }
    }
  }
  assert.equal(moods.size, 4, `the scenarios together show every mood, not just ${[...moods]}`)
  for (const [name, scenario] of Object.entries(SCENARIOS).filter(([, s]) => typeof s.status === 'number')) {
    const summary = plain(I.summarise(null, `HTTP ${scenario.status}`, false))
    assert.equal(summary.mood, 'trouble', name)
    assert.equal(tr('en')(summary.line.key, summary.line.params).includes(`HTTP ${scenario.status}`), true)
  }
})

test('every scenario’s status gives rows of text, a hero and an overview tab, in both languages, without a hole in the text', () => {
  for (const [name, scenario] of objectScenarios) {
    for (const lang of LANGS) {
      const t = tr(lang)
      const rows = plain(I.statusRows(scenario.status, t))
      assert.ok(rows.length >= 3, name)
      for (const [label, value] of rows) {
        clean(label, `${name} ${lang} label`)
        clean(String(value), `${name} ${lang} ${label}`)
      }
      const summary = I.summarise(scenario.status, null, false)
      const hero = expand(I.Hero({ t, summary }))
      clean(textOf(hero), `${name} ${lang} hero`)
      const [img] = findAll(hero, (n) => n.type === 'img')
      assert.equal(img.props.src, `feiyu-memgate/art/mascot-${summary.mood}.webp`)
      const overview = expand(I.OverviewTab({ t, status: { data: scenario.status, loading: false, error: null, refresh() {} } }))
      clean(textOf(overview), `${name} ${lang} overview`)
      assert.equal(findAll(overview, (n) => n.type === 'dl').length, 1, name)
    }
  }
  // The status route that does not answer: the hero and the overview say so.
  for (const lang of LANGS) {
    const t = tr(lang)
    const summary = I.summarise(null, 'HTTP 503', false)
    clean(textOf(expand(I.Hero({ t, summary }))), `offline ${lang}`)
    const overview = expand(I.OverviewTab({ t, status: { data: null, loading: false, error: 'HTTP 503', refresh() {} } }))
    clean(textOf(overview), `offline overview ${lang}`)
  }
})

test('the secrets the status of a scenario names are settings of the page that really are secrets', () => {
  for (const [name, scenario] of objectScenarios) {
    for (const key of Object.keys(scenario.status.secrets || {})) {
      assert.ok(I.INDEX[key] && I.INDEX[key].secret === true, `${name}: ${key}`)
      assert.equal(typeof scenario.status.secrets[key], 'boolean')
    }
  }
})

// The model panel with the hooks' state chosen: the list a scenario gives.
function renderPanel(view, lang) {
  const loaded = load({ states: (init) => (init && typeof init === 'object' && 'view' in init && 'error' in init ? { view, error: null } : init) })
  return expand({ type: loaded.internals.ModelsPanel, props: { t: tr(lang), lang, fetch: async () => ({ ok: false }), children: [] } })
}

test('modelsFor(name) renders through ModelsPanel and its ModelRow for every scenario, with a row per model', () => {
  for (const name of Object.keys(SCENARIOS)) {
    const view = modelsFor(name)
    assert.ok(view.models.length >= 3, name)
    assert.equal(new Set(view.models.map((model) => model.id)).size, view.models.length, `${name}: model ids are unique`)
    for (const lang of LANGS) {
      const tree = renderPanel(view, lang)
      clean(textOf(tree), `${name} ${lang} models`)
      assert.equal(withClass(tree, 'fmg-model').length, view.models.length, name)
      for (const model of view.models) {
        const row = expand({ type: I.ModelRow, props: { t: tr(lang), lang, model, view, accepted: false, pending: false, onAccept() {}, onAction() {}, children: [] } })
        clean(textOf(row), `${name} ${lang} ${model.id}`)
        assert.ok(textOf(row).includes(model.id))
      }
      // Every id the panel makes starts with the page prefix and none repeats.
      const ids = findAll(tree, (n) => n.props.id !== undefined).map((n) => n.props.id)
      for (const id of ids) assert.match(id, /^feiyu-memgate-/)
      assert.equal(new Set(ids).size, ids.length)
    }
  }
})

test('the model lists together show an active model, a download under way, a licence to accept and a problem', () => {
  const states = new Set()
  let licence = false
  let problem = false
  let progress = false
  for (const name of Object.keys(SCENARIOS)) {
    const view = modelsFor(name)
    for (const model of view.models) {
      states.add(model.state)
      if (model.requiresAcceptance && model.state === 'absent') licence = true
    }
    if (view.python && view.python.problem) problem = true
    if (findAll(renderPanel(view, 'en'), (n) => n.props.role === 'progressbar').length) progress = true
  }
  assert.ok(states.has('active') && states.has('installed') && states.has('absent') && states.has('downloading'), [...states].join())
  assert.ok(licence && problem && progress)
  // The panel shows the problem the host half would give, in the language asked for.
  const view = modelsFor('no-python')
  assert.ok(textOf(renderPanel(view, 'zh')).includes(view.python.problem.todo.zh))
  assert.ok(textOf(renderPanel(view, 'en')).includes(view.python.problem.todo.en))
})

test('USER_SETTINGS only names settings that exist, with values their fields accept', () => {
  let count = 0
  for (const [group, values] of Object.entries(USER_SETTINGS)) {
    for (const [key, value] of Object.entries(values)) {
      const field = I.INDEX[`${group}.${key}`]
      assert.ok(field, `${group}.${key} is not a setting`)
      assert.ok(!field.secret, 'the preview keeps no secret')
      if (field.kind === 'boolean') assert.equal(typeof value, 'boolean', field.key)
      else if (field.kind === 'integer') {
        assert.ok(Number.isInteger(value) && value >= field.min && value <= field.max, `${field.key}=${value}`)
        assert.ok(I.checkText(field, String(value)))
      } else if (field.kind === 'choice') assert.ok(field.options.some((option) => option.value === value), `${field.key}=${value}`)
      else {
        assert.equal(typeof value, 'string')
        assert.ok(I.checkText(field, value))
      }
      count += 1
    }
  }
  assert.ok(count >= 2)
})

test('the preview’s starting settings show as changed rows and open the sub-rows of their switch', () => {
  const state = { status: 'ready', value: plain(USER_SETTINGS), base: {}, user: plain(USER_SETTINGS), revision: 1, writable: true }
  const tabs = I.pageView(state, {}, null)
  const rows = tabs.flatMap((tab) => tab.sections.flatMap((section) => section.rows))
  const changed = plain(rows.filter((row) => row.view.overridden).map((row) => row.view.k).sort())
  assert.deepEqual(changed, Object.entries(USER_SETTINGS).flatMap(([group, values]) => Object.keys(values).map((key) => `${group}.${key}`)).sort())
  const shown = (k) => rows.find((row) => row.view.k === k).shown
  if (USER_SETTINGS.advanced.afterthought) assert.equal(shown('advanced.afterthoughtMax'), true, 'a sub-row visible in the preview')
  assert.equal(shown('advanced.poolSize'), false, 'and one that is not')
  assert.equal(tabs.reduce((sum, tab) => sum + tab.invalid, 0), 0)
})
