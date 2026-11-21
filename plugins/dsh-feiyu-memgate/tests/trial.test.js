// The recall trial of the Retrieval tab: the model that turns the host half's answer into rows, the call,
// the panel (drawn directly and inside the page) and the made-up answers of the preview. React is the
// stand-in of tests/helpers/client-harness.js, which has no state: a test that needs a hook to start from a
// value passes `states`.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import { TRIAL_MODES, trialFor } from '../scripts/preview/scenarios.mjs'
import { expand, findAll, load, plain, textOf, translatorFor, withClass, withRole } from './helpers/client-harness.js'

const require = createRequire(import.meta.url)
const trial = require('../client/src/model/trial.cjs')
const { tryRecall } = require('../client/src/net.cjs')
const { TRY_ROUTE, TRIAL_MAX_CHARS } = require('../client/src/constants.cjs')

const tick = () => new Promise((resolve) => setImmediate(resolve))
const LANGS = ['en', 'zh']

const cand = (id, outcome, found, score, extra = {}) => ({ source: 'memories', id, found, score, outcome, stage: null, addedBy: null, ...extra })

// Every outcome and every reason the page knows, and one stage it has no word for.
const ANSWER = {
  ok: true,
  stages: ['min_score'],
  candidates: [
    cand('a', 'delivered', 0.612, 0.58, { text: 'Drinks oolong', date: '2026-11-01' }),
    cand('b', 'delivered', 0.5, 0.5, { text: 'Linked one', addedBy: 'linked' }),
    cand('c', 'dropped', 0.2, 0.2, { stage: 'min_score', text: 'x', cut: true }),
    cand('d', 'dropped', 0.3, 0.3, { stage: 'relative_score' }),
    cand('e', 'dropped', 0.3, 0.3, { stage: 'fill_text' }),
    cand('f', 'dropped', 0.3, 0.3, { stage: 'drop_oversize' }),
    cand('g', 'dropped', 0.3, 0.3, { stage: 'redact' }),
    cand('h', 'dropped', 0.3, 0.3, { stage: 'dedupe' }),
    cand('i', 'deferred', 0.3, 0.3, { stage: 'budget' }),
    cand('j', 'dropped', 0.3, 0.3, { stage: 'drop_marker' }),
    cand('k', 'dropped', 0.3, 0.3, { stage: 'my_stage' }),
    cand('l', 'over_limit', 0.25, 0.25),
    cand('m', 'not_reached', 0.2, 0.2),
  ],
  more: 2, failed: 0, busy: 0, cut: false, limit: 5,
}

// ------------------------------------------------------------------ model/trial.cjs

test('rows(): every candidate in the order given, with its text, date, score, tags and reason', () => {
  const list = plain(trial.rows(ANSWER))
  assert.equal(list.length, ANSWER.candidates.length)
  assert.deepEqual(list.map((row) => row.key), ANSWER.candidates.map((c, index) => 'memories:' + c.id + ':' + index))
  assert.deepEqual(list[0], { key: 'memories:a:0', text: 'Drinks oolong', cut: false, date: '2026-11-01', found: 0.612, score: 0.58, moved: true, delivered: true, linked: false, reason: null })
  assert.equal(list[1].linked, true)
  assert.equal(list[1].moved, false)
  assert.deepEqual([list[2].text, list[2].cut, list[2].date], ['x', true, null])
  assert.equal(list[3].text, null, 'a missing text stays missing: the panel says so')
  assert.deepEqual(list.map((row) => row.delivered), [true, true, ...Array(11).fill(false)])
  assert.deepEqual(list.slice(2).map((row) => row.reason), [
    { key: 'trial.reason.min_score', values: {} },
    { key: 'trial.reason.relative_score', values: {} },
    { key: 'trial.reason.fill_text', values: {} },
    { key: 'trial.reason.drop_oversize', values: {} },
    { key: 'trial.reason.redact', values: {} },
    { key: 'trial.reason.dedupe', values: {} },
    { key: 'trial.reason.budget', values: {} },
    { key: 'trial.reason.drop_marker', values: {} },
    { key: 'trial.reason.other', values: { stage: 'my_stage' } },
    { key: 'trial.reason.over_limit', values: { limit: 5 } },
    { key: 'trial.reason.not_reached', values: {} },
  ])
})

test('rows(): a score counts as moved from 0.005 on, and the same key never repeats', () => {
  const moved = (found, score) => trial.rows({ candidates: [cand('a', 'delivered', found, score)] })[0].moved
  assert.equal(moved(0.5, 0.5), false)
  assert.equal(moved(0.5, 0.504), false)
  assert.equal(moved(0.5, 0.505), true)
  assert.equal(moved(0.505, 0.5), true)
  assert.equal(moved(0.61, 0.55), true)
  const twice = trial.rows({ candidates: [cand('a', 'delivered', 1, 1), cand('a', 'dropped', 1, 1)] })
  assert.equal(new Set(twice.map((row) => row.key)).size, 2)
  assert.deepEqual(trial.rows(null), [])
  assert.deepEqual(trial.rows({}), [])
})

test('summary(), score(), usable() and errorKey()', () => {
  assert.deepEqual(trial.summary(ANSWER), { found: 13, given: 2 })
  assert.deepEqual(trial.summary({ candidates: [] }), { found: 0, given: 0 })
  assert.deepEqual(trial.summary(null), { found: 0, given: 0 })
  assert.equal(trial.score(0.612), '0.61')
  assert.equal(trial.score(0.6), '0.60')
  assert.equal(trial.score(1), '1.00')
  assert.equal(trial.score(0), '0.00')
  assert.equal(trial.score(NaN), '?')
  assert.equal(trial.score('0.5'), '?')
  assert.equal(trial.usable('我早上喜欢喝什么茶'), true)
  assert.equal(trial.usable('  tea  '), true)
  for (const bad of ['', '   ', '\n\t', null, undefined, 5, 'a'.repeat(501), '茶'.repeat(501)]) assert.equal(trial.usable(bad), false, String(bad))
  assert.equal(trial.usable('a'.repeat(500)), true)
  assert.equal(trial.usable('茶'.repeat(500)), true)
  assert.equal(trial.usable('😀'.repeat(500)), true, 'code points, not UTF-16 units')
  assert.equal(trial.usable(' ' + 'a'.repeat(500) + ' '), true, 'counted after trimming')
  assert.equal(TRIAL_MAX_CHARS, 500)
  for (const code of ['unavailable', 'unsupported', 'no-recall', 'failed', 'network']) assert.equal(trial.errorKey(code), 'trial.error.' + code)
  for (const odd of ['HTTP 500', undefined, null, 'constructor', '__proto__']) assert.equal(trial.errorKey(odd), 'trial.error.failed', String(odd))
})

// ------------------------------------------------------------------ tryRecall()

test('tryRecall(): POSTs { query } to the try route with the conventions of the file, and gives the parsed body', async () => {
  const calls = []
  const fetchImpl = async (route, init) => {
    calls.push({ route, init })
    return { ok: true, status: 200, json: async () => ({ ok: true, candidates: [] }) }
  }
  assert.deepEqual(await tryRecall('我喜欢喝什么茶', fetchImpl), { ok: true, candidates: [] })
  assert.equal(calls[0].route, TRY_ROUTE)
  assert.equal(TRY_ROUTE, 'feiyu-memgate/try')
  assert.equal(calls[0].init.method, 'POST')
  assert.equal(calls[0].init.credentials, 'same-origin')
  assert.equal(calls[0].init.headers['content-type'], 'application/json')
  assert.deepEqual(JSON.parse(calls[0].init.body), { query: '我喜欢喝什么茶' })
  await assert.rejects(() => tryRecall('x', async () => ({ ok: false, status: 400 })), (error) => error.code === 'HTTP 400')
  await assert.rejects(() => tryRecall('x', async () => { throw new TypeError('Failed to fetch') }), TypeError)
})

// ------------------------------------------------------------------ the panel

const shared = load()

// The panel drawn directly. `saved` is what the hook starts from (the page keeps it between tabs).
function renderPanel({ lang = 'en', text = '', saved, run, onText = () => {}, onResult, fetch } = {}) {
  const loaded = saved ? load({ states: (init) => (init && typeof init === 'object' && 'state' in init && 'answer' in init ? saved : init) }) : shared
  const t = translatorFor(lang, loaded)
  const tree = expand(loaded.internals.TrialPanel({ t, text, onText, run, onResult, fetch }))
  return { tree, t }
}
const runButton = (tree) => withClass(tree, 'fmg-button--primary')[0]
const form = (tree) => findAll(tree, (n) => n.type === 'form')[0]
const byId = (tree) => findAll(tree, (n) => n.props.id === 'feiyu-memgate-trial-text')[0]
const input = (tree) => findAll(tree, (n) => n.type === 'input')[0]
const done = (answer) => ({ state: 'done', answer, code: null })
const failed = (code) => ({ state: 'failed', answer: null, code })

test('TrialPanel: the intro, one line to type in and a primary button on one row', () => {
  for (const lang of LANGS) {
    const { tree, t } = renderPanel({ lang, text: 'tea' })
    assert.equal(tree.props.className, 'fmg-trial')
    assert.ok(textOf(tree).includes(t('trial.intro')))
    const line = input(tree)
    assert.equal(line.props.id, 'feiyu-memgate-trial-text')
    assert.equal(line.props.value, 'tea')
    assert.equal(line.props.maxLength, 500)
    assert.equal(line.props['aria-label'], t('trial.inputLabel'))
    assert.equal(line.props.placeholder, t('trial.placeholder'))
    assert.equal(form(tree).props.className, 'fmg-trial__form')
    assert.deepEqual(findAll(form(tree), (n) => n.type === 'button').map(textOf), [t('trial.run')])
    assert.equal(withClass(tree, 'fmg-notice').length, 0, 'nothing before a trial')
    assert.equal(withClass(tree, 'fmg-trial__row').length, 0)
  }
  assert.equal(renderPanel({ lang: 'zh' }).t('trial.run'), '试一下')
})

test('TrialPanel: the button is off for a text that cannot be tried, and typing reports the text', () => {
  for (const text of ['', '   ', 'a'.repeat(501)]) assert.equal(runButton(renderPanel({ text }).tree).props.disabled, true, JSON.stringify(text.slice(0, 5)))
  assert.equal(runButton(renderPanel({ text: 'tea' }).tree).props.disabled, undefined)
  const typed = []
  input(renderPanel({ onText: (text) => typed.push(text) }).tree).props.onChange({ target: { value: '茶' } })
  assert.deepEqual(typed, ['茶'])
})

test('TrialPanel: Enter and the button start one trial with the trimmed text; a second start while one is on its way does nothing', async () => {
  const asked = []
  let release
  const run = (query) => { asked.push(query); return new Promise((resolve) => { release = resolve }) }
  const { tree } = renderPanel({ text: '  我早上喜欢喝什么茶 ', run })
  let prevented = 0
  form(tree).props.onSubmit({ preventDefault: () => { prevented += 1 } })
  runButton(tree).props.onClick()
  form(tree).props.onSubmit({ preventDefault: () => { prevented += 1 } })
  assert.equal(prevented, 2, 'the form does not reload the page')
  assert.deepEqual(asked, ['我早上喜欢喝什么茶'])
  release({ ok: true, candidates: [] })
  await tick()

  // Nothing is asked for a text that cannot be tried.
  const none = []
  const empty = renderPanel({ text: '   ', run: (query) => { none.push(query); return Promise.resolve({ ok: true }) } })
  form(empty.tree).props.onSubmit({ preventDefault() {} })
  runButton(empty.tree).props.onClick()
  assert.deepEqual(none, [])
})

test('TrialPanel: while a trial is on its way the button says so and is off', () => {
  for (const lang of LANGS) {
    const { tree, t } = renderPanel({ lang, text: 'tea', saved: { state: 'busy', answer: null, code: null } })
    assert.equal(textOf(runButton(tree)), t('trial.busy'))
    assert.equal(runButton(tree).props.disabled, true)
  }
})

test('TrialPanel: what became of a trial is handed to the page, and a failed call has a code', async () => {
  const seen = []
  const draw = (run) => renderPanel({ text: 'tea', run, onResult: (result) => seen.push(plain(result)) }).tree
  const answer = { ok: true, candidates: [cand('a', 'delivered', 0.5, 0.5, { text: 'x' })], limit: 5 }
  form(draw(async () => answer)).props.onSubmit({ preventDefault() {} })
  await tick()
  const refused = Object.assign(new Error('try'), { code: 'HTTP 500' })
  form(draw(async () => { throw refused })).props.onSubmit({ preventDefault() {} })
  await tick()
  form(draw(async () => { throw new TypeError('Failed to fetch') })).props.onSubmit({ preventDefault() {} })
  await tick()
  for (const code of ['unavailable', 'unsupported', 'no-recall', 'failed']) {
    form(draw(async () => ({ ok: false, code }))).props.onSubmit({ preventDefault() {} })
    await tick()
  }
  form(draw(async () => ({ ok: false }))).props.onSubmit({ preventDefault() {} })
  await tick()
  form(draw(async () => null)).props.onSubmit({ preventDefault() {} })
  await tick()
  assert.deepEqual(seen, [
    done(answer), failed('failed'), failed('network'),
    failed('unavailable'), failed('unsupported'), failed('no-recall'), failed('failed'), failed('failed'), failed('failed'),
  ])
})

test('TrialPanel: each failure code shows its message, in both languages', () => {
  for (const lang of LANGS) {
    for (const code of ['unavailable', 'unsupported', 'no-recall', 'failed', 'network', 'something-new']) {
      const { tree, t } = renderPanel({ lang, text: 'tea', saved: failed(code) })
      const notices = withClass(tree, 'fmg-notice')
      assert.equal(notices.length, 1, code)
      const key = code === 'something-new' ? 'trial.error.failed' : 'trial.error.' + code
      assert.equal(textOf(notices[0]), t(key), `${lang} ${code}`)
      assert.notEqual(t(key), key, 'the text exists')
      assert.equal(withClass(tree, 'fmg-trial__row').length, 0)
    }
    // An acknowledgement is news, not a fault; the rest are warnings.
    assert.equal(withRole(renderPanel({ lang, saved: failed('no-recall') }).tree, 'alert').length, 0)
    assert.equal(withClass(renderPanel({ lang, saved: failed('no-recall') }).tree, 'fmg-notice--warn').length, 0)
    assert.equal(withRole(renderPanel({ lang, saved: failed('network') }).tree, 'alert').length, 1)
    assert.equal(withClass(renderPanel({ lang, saved: failed('network') }).tree, 'fmg-notice--warn').length, 1)
  }
})

test('TrialPanel: a good answer shows the summary, every row with its tags and reason, and the note', () => {
  for (const lang of LANGS) {
    const { tree, t } = renderPanel({ lang, text: 'tea', saved: done(ANSWER) })
    const text = textOf(tree)
    assert.ok(text.includes(t('trial.summary', { found: 13, given: 2 })))
    assert.ok(text.includes(t('trial.note')))
    assert.equal(withClass(tree, 'fmg-notice').length, 0, 'no partial warning when nothing failed')
    const [group] = withClass(tree, 'fmg-group')
    assert.equal(findAll(group, (n) => n.type === 'ul')[0].props.className, 'fmg-rows')
    const rows = withClass(tree, 'fmg-trial__row')
    assert.equal(rows.length, 13)
    assert.deepEqual(rows.map((row) => row.props['data-left']), ['false', 'false', ...Array(11).fill('true')])
    const tags = (row) => withClass(row, 'fmg-tag')
    // Delivered: the ok tag; the score moved, so both are shown with an arrow.
    assert.deepEqual(tags(rows[0]).map((tag) => [tag.props.className, textOf(tag)]), [['fmg-tag fmg-tag--ok', t('trial.delivered')]])
    assert.ok(textOf(withClass(rows[0], 'fmg-trial__facts')[0]).includes('2026-11-01'))
    assert.ok(textOf(withClass(rows[0], 'fmg-trial__facts')[0]).includes('0.61 → 0.58'))
    assert.equal(withClass(rows[0], 'fmg-trial__reason').length, 0)
    // Linked and delivered: both tags.
    assert.deepEqual(tags(rows[1]).map((tag) => [tag.props.className, textOf(tag)]), [['fmg-tag fmg-tag--ok', t('trial.delivered')], ['fmg-tag fmg-tag--sea', t('trial.linked')]])
    assert.ok(textOf(withClass(rows[1], 'fmg-trial__facts')[0]).includes('0.50'))
    assert.ok(!textOf(withClass(rows[1], 'fmg-trial__facts')[0]).includes('→'), 'an unmoved score is shown once')
    // Left out: the plain tag and the reason as text; the cut text ends in an ellipsis.
    assert.deepEqual(tags(rows[2]).map((tag) => [tag.props.className, textOf(tag)]), [['fmg-tag', t('trial.leftOut')]])
    assert.equal(textOf(withClass(rows[2], 'fmg-trial__reason')[0]), t('trial.reason.min_score'))
    assert.equal(textOf(withClass(rows[2], 'fmg-trial__text')[0]), 'x…')
    assert.equal(textOf(withClass(rows[0], 'fmg-trial__text')[0]), 'Drinks oolong')
    // A row without a text shows the placeholder.
    assert.equal(textOf(withClass(rows[3], 'fmg-trial__text')[0]), t('trial.noText'))
    // Every reason has its own words, and the unknown step is named.
    const reasons = rows.slice(2).map((row) => textOf(withClass(row, 'fmg-trial__reason')[0]))
    assert.deepEqual(reasons, [
      t('trial.reason.min_score'), t('trial.reason.relative_score'), t('trial.reason.fill_text'), t('trial.reason.drop_oversize'),
      t('trial.reason.redact'), t('trial.reason.dedupe'), t('trial.reason.budget'), t('trial.reason.drop_marker'),
      t('trial.reason.other', { stage: 'my_stage' }), t('trial.reason.over_limit', { limit: 5 }), t('trial.reason.not_reached'),
    ])
    assert.ok(reasons[8].includes('my_stage'))
    assert.ok(reasons[9].includes('5'))
    for (const reason of reasons) assert.ok(!/\{\w+\}|undefined/.test(reason), reason)
  }
})

test('TrialPanel: no candidates says so instead of the summary and the rows; the partial notice shows when a store failed or was busy', () => {
  for (const lang of LANGS) {
    const none = renderPanel({ lang, saved: done({ ok: true, candidates: [], more: 0, failed: 0, busy: 0, limit: 5 }) })
    const text = textOf(none.tree)
    assert.ok(text.includes(none.t('trial.none')))
    assert.ok(!text.includes(none.t('trial.note')))
    assert.equal(withClass(none.tree, 'fmg-group').length, 0)
    assert.ok(!/\d+ candidates|找到 \d+/.test(text))
    assert.equal(withClass(none.tree, 'fmg-notice').length, 0)
    for (const patch of [{ failed: 1 }, { busy: 2 }]) {
      const partial = renderPanel({ lang, saved: done({ ...ANSWER, ...patch }) })
      const [notice] = withClass(partial.tree, 'fmg-notice')
      assert.equal(textOf(notice), partial.t('trial.partial'))
      assert.ok(notice.props.className.includes('fmg-notice--warn'))
      assert.equal(withClass(partial.tree, 'fmg-trial__row').length, 13)
    }
    assert.equal(withClass(renderPanel({ lang, saved: done({ ok: true, candidates: [], failed: 1 }) }).tree, 'fmg-notice').length, 1, 'a failed store matters when nothing was found too')
  }
})

test('the texts: both languages say the same thing, with the same placeholders, and match the specification', () => {
  const dict = plain(shared.internals.dictionaries())
  const keys = Object.keys(dict.en).filter((key) => key.startsWith('trial.'))
  assert.equal(keys.length, 29)
  for (const key of keys) {
    assert.ok(key in dict.zh, key)
    assert.deepEqual([...dict.zh[key].matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort(), [...dict.en[key].matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort(), `${key}: placeholders`)
  }
  assert.equal(Object.keys(dict.zh).filter((key) => key.startsWith('trial.')).length, 29)
  assert.equal(dict.zh['trial.run'], '试一下')
  assert.equal(dict.en['trial.run'], 'Try')
  assert.equal(dict.zh['trial.reason.over_limit'], '超过每次最多 {limit} 条')
  assert.equal(dict.en['trial.summary'], '{found} candidates found, {given} delivered.')
  // The keys the model and the view ask for are all there.
  const asked = new Set(['trial.intro', 'trial.placeholder', 'trial.inputLabel', 'trial.run', 'trial.busy', 'trial.summary', 'trial.none', 'trial.note', 'trial.partial', 'trial.delivered', 'trial.leftOut', 'trial.linked', 'trial.noText'])
  for (const row of trial.rows(ANSWER)) if (row.reason) asked.add(row.reason.key)
  for (const code of ['unavailable', 'unsupported', 'no-recall', 'failed', 'network']) asked.add(trial.errorKey(code))
  assert.deepEqual([...asked].sort(), keys.sort())
})

// ------------------------------------------------------------------ inside the page

const formState = () => ({ status: 'ready', value: { modules: { autoRecall: true }, advanced: { recallLimit: 5 }, external: { url: '' } }, base: {}, user: {}, revision: 1, writable: true })

function renderPage({ tab = 'retrieval', lang = 'en', saved } = {}) {
  const loaded = load({
    states: (init) => {
      if (init === 'overview') return tab
      if (init && typeof init === 'object' && !Array.isArray(init) && init.text === '' && 'result' in init) return saved || init
      return init
    },
  })
  const t = translatorFor(lang, loaded)
  const props = { view: 'page', t, form: { state: formState(), mutate: async () => true }, fetch: async () => ({ ok: false, status: 503 }) }
  return { tree: expand(loaded.internals.SettingsPage(props)), t }
}

test('the page: the trial is the last section of the Retrieval tab, after the model list, and only there', () => {
  for (const lang of LANGS) {
    const { tree, t } = renderPage({ lang })
    const titles = withClass(tree, 'fmg-section__title').map(textOf)
    assert.equal(titles.at(-1), lang === 'zh' ? '试一下召回' : 'Try a recall')
    assert.equal(titles.at(-2), lang === 'zh' ? '本地向量模型' : 'Local embedding models')
    assert.equal(withClass(tree, 'fmg-trial').length, 1)
    assert.ok(textOf(tree).includes(t('trial.intro')))
  }
  for (const tab of ['overview', 'features', 'advanced', 'changelog', 'feedback']) assert.equal(withClass(renderPage({ tab }).tree, 'fmg-trial').length, 0, tab)
})

test('the page keeps the draft and the last answer: leaving the tab and coming back shows them again', () => {
  const saved = { text: '我早上喜欢喝什么茶', result: done(ANSWER) }
  const { tree, t } = renderPage({ saved })
  assert.equal(byId(tree).props.value, '我早上喜欢喝什么茶')
  assert.ok(textOf(tree).includes(t('trial.summary', { found: 13, given: 2 })))
  assert.equal(withClass(tree, 'fmg-trial__row').length, 13)
  const fresh = renderPage()
  assert.equal(byId(fresh.tree).props.value, '')
  assert.equal(withClass(fresh.tree, 'fmg-trial__row').length, 0)
  // A failure is kept the same way.
  const kept = renderPage({ saved: { text: 'tea', result: failed('unsupported') } })
  assert.ok(textOf(kept.tree).includes(kept.t('trial.error.unsupported')))
})

test('the page wires the panel to its own state and to the page fetch, and the layout names it', () => {
  const page = fs.readFileSync(new URL('../client/src/view/page.cjs', import.meta.url), 'utf8')
  assert.ok(page.includes('trial: h(TrialPanel'))
  assert.ok(page.includes('fetch: fetchImpl, text: trial.text, result: trial.result'))
  const { TABS } = shared.internals
  const retrieval = TABS.find((tab) => tab.id === 'retrieval')
  assert.deepEqual(plain(retrieval.sections.map((section) => section.panel || null)), [null, 'models', 'trial'])
  assert.deepEqual(plain(retrieval.sections.at(-1).label), { en: 'Try a recall', zh: '试一下召回' })
  assert.deepEqual(plain(retrieval.sections.at(-1).fields), [])
})

// ------------------------------------------------------------------ the preview's made-up answers

test('the preview answers every trial mode, and the found answer shows every kind of row', () => {
  assert.deepEqual(TRIAL_MODES, ['found', 'empty', 'unsupported', 'norecall', 'fails'])
  assert.equal(trialFor('fails').status, 500)
  assert.deepEqual(trialFor('unsupported').body, { ok: false, code: 'unsupported' })
  assert.deepEqual(trialFor('norecall').body, { ok: false, code: 'no-recall' })
  assert.deepEqual(trialFor('empty').body.candidates, [])
  assert.equal(trialFor('anything else').body.ok, true, 'found is the default')
  const found = trialFor('found').body
  const list = trial.rows(found)
  assert.equal(list.filter((row) => row.delivered).length, 4)
  assert.equal(list.filter((row) => row.linked).length, 1)
  assert.equal(list.filter((row) => row.moved).length >= 1, true)
  assert.equal(list.filter((row) => row.cut).length, 1)
  assert.equal(list.filter((row) => row.text === null).length, 1)
  const kinds = new Set(list.map((row) => row.reason && row.reason.key))
  for (const key of ['trial.reason.min_score', 'trial.reason.relative_score', 'trial.reason.dedupe', 'trial.reason.budget', 'trial.reason.over_limit']) assert.ok(kinds.has(key), key)
  for (const row of list) if (row.text !== null) assert.ok(Array.from(row.text).length <= 300)
  for (const lang of LANGS) {
    const { tree } = renderPanel({ lang, saved: done(found) })
    assert.equal(withClass(tree, 'fmg-trial__row').length, found.candidates.length)
    assert.ok(!/undefined|\[object Object\]|NaN|null/.test(textOf(tree)))
  }
})
