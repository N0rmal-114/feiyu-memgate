// The browser half (client/client.js, assembled from client/src), offline: it is a plain script in the
// client module system's lazy-CJS form that asks the host for React only, its generated parts are current,
// it registers its dictionaries, its stylesheet and the row's settings page, and the page (the summary at
// the top, the tabs, one row per setting, the save bar, the changelog and the model list) draws what its
// pure parts say. React is a small stand-in (tests/helpers/client-harness.js) that expands function
// components with inert hooks; what only a running Desktop can show (the look, the real slot renderer)
// is not claimed here.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { sync } from '../scripts/sync-client.mjs'
import { FIELDS } from '../src/config.js'
import { CHANGELOG_PATH, MODELS_PATH, STATUS_PATH } from '../src/plugin.js'
import { ADDON_CONFIRM } from '../src/setup.js'
import { expand, findAll, load, plain, textOf, translator, walk, withClass, withRole } from './helpers/client-harness.js'

const SOURCE = fs.readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
const pkg = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'))

const shared = load()
const I = shared.internals
const DICT = plain(I.dictionaries())
const tr = (lang) => translator(DICT[lang])
const LANGS = ['en', 'zh']
const MOODS = ['ready', 'busy', 'asleep', 'trouble']
const ALL_FIELDS = FIELDS.flatMap((group) => group.fields.map((field) => ({ ...field, group: group.group, k: `${group.group}.${field.key}` })))
const SETTINGS_TABS = ['features', 'retrieval', 'advanced']
// Every tab of the page, in the order of the tab bar.
const PAGE_TABS = ['overview', 'features', 'retrieval', 'advanced', 'changelog', 'feedback']
const ALL_OPEN = Object.fromEntries(I.TABS.flatMap((tab) => tab.sections.map((section) => [section.id, true])))

const formState = (overrides = {}) => ({
  status: 'ready',
  value: { modules: { autoRecall: true }, advanced: { recallLimit: 5 }, external: { url: '' } },
  base: {},
  user: { advanced: { recallLimit: 5 } },
  revision: 7,
  writable: true,
  ...overrides,
})

const status = (overrides = {}) => ({
  version: '0.3.2',
  gateway: { mode: 'managed', state: 'running' },
  python: { found: true, version: '3.12.4', source: 'path', executable: 'python-for-tests' },
  retrieval: { setting: 'auto', active: 'keyword', hybrid: { setting: false, active: false } },
  store: { type: 'sqlite', file: 'memories.db', count: 1284 },
  embed: null,
  migration: null,
  secrets: { 'external.token': false, 'retrieval.apiKey': true },
  ...overrides,
})

// A rendering of the whole page in a chosen first state. `states` of the stand-in React maps the
// initial value of a hook to the one the test wants: the tab is the only hook that starts as 'overview',
// the phase of a save the only one that starts as 'idle', and every empty object (the staged edits, the
// open groups, the opened releases) becomes `draft` plus, with `openAll`, every group of the Advanced tab.
function renderBody({ tab = 'overview', lang = 'en', draft = {}, openAll = false, phase, form = { state: formState(), mutate: async () => true }, statusData, changelog, fetch } = {}) {
  const loaded = load({
    states: (init) => {
      if (init === 'overview') return tab
      if (init === 'idle' && phase) return phase
      if (init && typeof init === 'object' && !Array.isArray(init)) {
        if (Object.keys(init).length === 0) return Object.assign({}, draft, openAll ? ALL_OPEN : {})
        if (statusData !== undefined && 'error' in init && 'loading' in init) return { loading: false, data: statusData, error: null }
        if (changelog !== undefined && 'data' in init && 'loading' in init && !('error' in init)) return { loading: false, data: changelog }
      }
      return init
    },
  })
  const t = tr(lang)
  const props = { view: 'page', t, form, fetch: fetch || (async () => ({ ok: false, status: 503 })) }
  return { tree: expand(loaded.internals.SettingsPage(props)), t, internals: loaded.internals }
}

const byId = (tree, id) => findAll(tree, (n) => n.props.id === id)
const buttonsNamed = (tree, text) => findAll(tree, (n) => n.type === 'button' && textOf(n) === text)
const tick = () => new Promise((resolve) => setImmediate(resolve))

const rowFor = (k, { state = formState(), draft = {}, secrets = null } = {}) => {
  for (const tabView of I.pageView(state, draft, secrets)) for (const section of tabView.sections) for (const row of section.rows) if (row.view.k === k) return row
  throw new Error(`no row ${k}`)
}

function renderRow(k, { lang = 'en', disabled = false, edit = () => {}, reset = () => {}, ...rest } = {}) {
  return expand(I.FieldRow({ t: tr(lang), lang, row: rowFor(k, rest), disabled, edit, reset }))
}

// One settings tab, drawn directly, every group of the Advanced tab open.
function renderTab(id, { lang = 'en', draft = {}, secrets = null, state = formState(), disabled = false, readOnly = false, open = ALL_OPEN } = {}) {
  const tabView = I.pageView(state, draft, secrets).find((view) => view.tab.id === id)
  const t = tr(lang)
  return expand(I.SettingsTab({
    t, lang, tabView, disabled, readOnly, edit() {}, reset() {}, open, onToggle() {},
    panels: { models: { type: I.ModelsPanel, props: { t, lang, fetch: async () => ({ ok: false, status: 503 }), children: [] } } },
  }))
}

const allTabs = (options) => Object.fromEntries(SETTINGS_TABS.map((id) => [id, renderTab(id, options)]))

// ------------------------------------------------------------------ the script and what it asks of the host

test('a plain script in the lazy-CJS form: registers the package name and asks the host for react and nothing else', () => {
  assert.match(SOURCE, /window\.__ModuleLoader__\.load\(\{ id: 'dsh-feiyu-memgate', factory: \(hostRequire\) => \{/)
  assert.ok(!/^\s*(import|export)\s/m.test(SOURCE), 'no module syntax: the page runs it as a script')
  const { registered, exports, required } = load()
  assert.equal(registered.id, pkg.name)
  assert.deepEqual(required, ['react'])
  assert.equal(exports.name, 'dsh-feiyu-memgate')
  assert.deepEqual(plain(exports.inject), ['slots', 'locale'])
  assert.equal(typeof exports.apply, 'function')
  // Relative requires are the bundle's own modules; every other name goes to the host.
  const hostIds = [...SOURCE.matchAll(/require\('([^']+)'\)/g)].map((m) => m[1]).filter((id) => !id.startsWith('.'))
  assert.deepEqual(hostIds, ['react'])
  assert.ok(!SOURCE.includes('dsh-client-ui-primitives'), 'the host primitives are not used any more')
})

test('client/src/fields.cjs is the field table of src/config.js and client/client.js is assembled from client/src (npm run sync-client)', () => {
  const { fields, client } = sync({ check: true })
  assert.ok(fields.current, 'client/src/fields.cjs is stale: run npm run sync-client')
  assert.ok(client.current, 'client/client.js is stale: run npm run sync-client')
  assert.deepEqual(plain(load().internals.FIELDS), plain(FIELDS))
})

// ------------------------------------------------------------------ the settings table as the page carries it

test('the page script carries advanced.linkedRecall right after skipRepeats: a switch that is on, with both languages', () => {
  const advanced = plain(load().internals.FIELDS).find((g) => g.group === 'advanced').fields
  const keys = advanced.map((f) => f.key)
  assert.equal(keys[keys.indexOf('skipRepeats') + 1], 'linkedRecall')
  const row = advanced.find((f) => f.key === 'linkedRecall')
  assert.deepEqual([row.kind, row.default, row.label.en, row.label.zh], ['boolean', true, 'Recall linked memories', '召回相关联的记忆'])
  const { en, zh } = plain(load().internals.dictionaries())
  assert.equal(en['f.advanced.linkedRecall'], row.label.en)
  assert.equal(zh['f.advanced.linkedRecall'], row.label.zh)
  assert.equal(en['h.advanced.linkedRecall'], row.help.en)
  assert.equal(zh['h.advanced.linkedRecall'], row.help.zh)
})

test('the page script carries the four chat settings with their kinds, defaults and range', () => {
  const table = plain(load().internals.FIELDS)
  const advanced = table.find((g) => g.group === 'advanced').fields
  const row = (key) => advanced.find((f) => f.key === key)
  for (const key of ['skipRepeats', 'showAge', 'failureNote']) assert.deepEqual([row(key).kind, row(key).default], ['boolean', true], key)
  assert.deepEqual([row('captureMinChars').kind, row('captureMinChars').default, row('captureMinChars').min, row('captureMinChars').max], ['integer', 4, 0, 50])
  const { en, zh } = plain(load().internals.dictionaries())
  for (const key of ['skipRepeats', 'showAge', 'failureNote', 'captureMinChars']) {
    assert.equal(en[`f.advanced.${key}`], row(key).label.en)
    assert.equal(zh[`f.advanced.${key}`], row(key).label.zh)
    assert.equal(zh[`h.advanced.${key}`], row(key).help.zh)
  }
})

test('the page script carries the four prototype settings, off by default, with both languages', () => {
  const advanced = plain(load().internals.FIELDS).find((g) => g.group === 'advanced').fields
  const row = (key) => advanced.find((f) => f.key === key)
  for (const key of ['afterthought', 'conversationPool']) assert.deepEqual([row(key).kind, row(key).default], ['boolean', false], key)
  for (const key of ['afterthoughtMax', 'poolSize']) assert.deepEqual([row(key).kind, row(key).min, row(key).max], ['integer', 1, 5], key)
  assert.equal(row('afterthoughtMax').default, 2)
  assert.equal(row('poolSize').default, 3)
  const { en, zh } = plain(load().internals.dictionaries())
  for (const key of ['afterthought', 'afterthoughtMax', 'conversationPool', 'poolSize']) {
    assert.equal(en[`f.advanced.${key}`], row(key).label.en)
    assert.equal(zh[`f.advanced.${key}`], row(key).label.zh)
    assert.equal(en[`h.advanced.${key}`], row(key).help.en)
    assert.equal(zh[`h.advanced.${key}`], row(key).help.zh)
  }
})

test('the page script carries the three recent pool settings with both languages', () => {
  const advanced = plain(load().internals.FIELDS).find((g) => g.group === 'advanced').fields
  const row = (key) => advanced.find((f) => f.key === key)
  assert.deepEqual([row('recentPool').kind, row('recentPool').default], ['boolean', false])
  assert.deepEqual([row('recentSize').default, row('recentSize').min, row('recentSize').max], [3, 1, 5])
  assert.deepEqual([row('recentDays').default, row('recentDays').min, row('recentDays').max], [7, 1, 30])
  const { en, zh } = plain(load().internals.dictionaries())
  for (const key of ['recentPool', 'recentSize', 'recentDays']) {
    assert.equal(en[`f.advanced.${key}`], row(key).label.en)
    assert.equal(zh[`f.advanced.${key}`], row(key).label.zh)
    assert.equal(zh[`h.advanced.${key}`], row(key).help.zh)
  }
})

test('the page script carries the block wording as a choice and the cap as a count, with both languages', () => {
  const advanced = plain(load().internals.FIELDS).find((g) => g.group === 'advanced').fields
  const row = (key) => advanced.find((f) => f.key === key)
  assert.deepEqual([row('blockTone').kind, row('blockTone').default, row('blockTone').options.map((o) => o.value)], ['choice', 'normal', ['normal', 'strict', 'bare']])
  assert.deepEqual([row('maxNewPerTurn').kind, row('maxNewPerTurn').default, row('maxNewPerTurn').min, row('maxNewPerTurn').max], ['integer', 0, 0, 5])
  const { en, zh } = plain(load().internals.dictionaries())
  for (const key of ['blockTone', 'maxNewPerTurn']) {
    assert.equal(en[`f.advanced.${key}`], row(key).label.en)
    assert.equal(zh[`f.advanced.${key}`], row(key).label.zh)
    assert.equal(zh[`h.advanced.${key}`], row(key).help.zh)
  }
  assert.equal(zh['o.advanced.blockTone.strict'], row('blockTone').options[1].label.zh)
  assert.equal(zh['o.advanced.blockTone.bare'], row('blockTone').options[2].label.zh)
  assert.equal(en['o.advanced.blockTone.bare'], row('blockTone').options[2].label.en)
  assert.equal(zh['h.advanced.blockTone'], row('blockTone').help.zh)
  assert.match(zh['h.advanced.blockTone'], /bare 不附说明语句/)
})

test('the page script carries the four relevance check settings with both languages', () => {
  const advanced = plain(load().internals.FIELDS).find((g) => g.group === 'advanced').fields
  const row = (key) => advanced.find((f) => f.key === key)
  assert.deepEqual([row('relevanceCheck').kind, row('relevanceCheck').default], ['boolean', false])
  assert.deepEqual([row('relevanceProvider').kind, row('relevanceProvider').default, row('relevanceModel').kind, row('relevanceModel').default], ['string', '', 'string', ''])
  assert.deepEqual([row('relevanceTimeoutMs').kind, row('relevanceTimeoutMs').default, row('relevanceTimeoutMs').min, row('relevanceTimeoutMs').max], ['integer', 3000, 500, 10000])
  const { en, zh } = plain(load().internals.dictionaries())
  for (const key of ['relevanceCheck', 'relevanceProvider', 'relevanceModel', 'relevanceTimeoutMs']) {
    assert.equal(en[`f.advanced.${key}`], row(key).label.en)
    assert.equal(zh[`f.advanced.${key}`], row(key).label.zh)
    assert.equal(zh[`h.advanced.${key}`], row(key).help.zh)
  }
})

test('every key has copy in both languages, and every setting has a label, help and option names', () => {
  const { en, zh } = DICT
  assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort())
  for (const [key, value] of Object.entries(en)) assert.ok(value && zh[key], key)
  for (const group of FIELDS) {
    assert.ok(en[`g.${group.group}`] && en[`gh.${group.group}`])
    for (const field of group.fields) {
      const k = `${group.group}.${field.key}`
      assert.equal(en[`f.${k}`], field.label.en)
      assert.equal(zh[`h.${k}`], field.help.zh)
      for (const option of field.options ?? []) assert.equal(zh[`o.${k}.${option.value}`], option.label.zh)
    }
  }
})

// ------------------------------------------------------------------ what the host half answers

test('apply registers both dictionaries, the stylesheet and the page under <package name>#<row id>', () => {
  const { exports, internals } = load()
  const calls = { effects: [], locale: [], slots: [] }
  const ctx = {
    effect: (execute, label) => {
      calls.effects.push(label)
      return execute()
    },
    locale: {
      register: (ns, dicts) => {
        calls.locale.push({ ns, dicts })
        return () => {}
      },
      bind: (ns) => translator(calls.locale.find((c) => c.ns === ns).dicts.en),
    },
    slots: {
      inject: (name, callback) => {
        calls.slots.push({ inject: name })
        callback()
      },
      register: (options, component) => {
        calls.slots.push({ options, component })
        return () => {}
      },
    },
  }
  assert.doesNotThrow(() => exports.apply(ctx), 'no document here: the stylesheet effect must not throw')
  assert.deepEqual(calls.effects, ['dsh-feiyu-memgate: dictionaries', 'dsh-feiyu-memgate: stylesheet'])
  assert.equal(calls.locale.length, 1)
  assert.equal(calls.locale[0].ns, internals.NS)
  assert.deepEqual(Object.keys(calls.locale[0].dicts), ['en', 'zh'])
  const rowId = /^\s+- id: (\S+)$/m.exec(fs.readFileSync(new URL('../cordis.patch.yml', import.meta.url), 'utf8'))[1]
  const registration = calls.slots.find((c) => c.options)
  assert.deepEqual(calls.slots[0], { inject: 'plugins.row.config' })
  assert.equal(registration.options.name, 'plugins.row.config')
  assert.equal(registration.options.key, `${pkg.name}#${rowId}`)
  assert.equal(registration.options.locale, internals.NS)
  assert.equal(typeof registration.options.inject().translate, 'function')
  assert.equal(typeof registration.component, 'function')
  assert.equal(internals.STATUS_ROUTE, STATUS_PATH.slice(1), 'the page reads the host route, document-relative')
  assert.equal(internals.MODELS_ROUTE, MODELS_PATH.slice(1))
  assert.equal(internals.CHANGELOG_ROUTE, CHANGELOG_PATH.slice(1))
})

// A document with just what installStyles uses: head.appendChild, getElementById, createElement.
function fakeDocument() {
  const children = []
  const head = {
    children,
    appendChild(element) {
      element.parentNode = head
      children.push(element)
      return element
    },
    removeChild(element) {
      children.splice(children.indexOf(element), 1)
      element.parentNode = null
    },
  }
  return {
    head,
    getElementById: (id) => children.find((element) => element.id === id) || null,
    createElement: (tag) => ({ tagName: tag.toUpperCase(), id: '', textContent: '', parentNode: null }),
  }
}

test('the stylesheet effect adds one <style> with the page id, a second install reuses it, and the returned function removes it', () => {
  const doc = fakeDocument()
  const remove = I.installStyles(doc)
  assert.equal(doc.head.children.length, 1)
  const [style] = doc.head.children
  assert.equal(style.tagName, 'STYLE')
  assert.equal(style.id, I.STYLE_ID)
  assert.ok(style.textContent.includes('.fmg-root'), 'the element holds the page stylesheet')
  const removeAgain = I.installStyles(doc)
  assert.equal(doc.head.children.length, 1, 'loading the plugin twice adds no second copy')
  assert.equal(doc.head.children[0], style)
  remove()
  assert.equal(doc.head.children.length, 0)
  assert.doesNotThrow(removeAgain, 'removing what is already gone is harmless')
  assert.doesNotThrow(() => I.installStyles(undefined)(), 'outside a browser it does nothing')
  assert.doesNotThrow(() => I.installStyles({})(), 'a document without a head is left alone')
})

test('apply puts the stylesheet into the global document when there is one, and the effect removes it again', () => {
  const doc = fakeDocument()
  const { exports } = load({ globals: { document: doc } })
  let disposer
  const ctx = {
    effect: (execute, label) => { if (label.endsWith('stylesheet')) disposer = execute(); else execute() },
    locale: { register: () => () => {}, bind: () => (key) => key },
    slots: { inject: (name, callback) => callback(), register: () => () => {} },
  }
  exports.apply(ctx)
  assert.equal(doc.head.children.length, 1)
  assert.equal(doc.head.children[0].id, 'dsh-feiyu-memgate-styles')
  disposer()
  assert.equal(doc.head.children.length, 0)
})

test('fetchChangelog asks the route with the same credentials as the status and fails with the HTTP code', async () => {
  const calls = []
  const data = await I.fetchChangelog(async (url, options) => { calls.push([url, options]); return { ok: true, json: async () => ({ version: '1', entries: [] }) } })
  assert.deepEqual(plain(data), { version: '1', entries: [] })
  assert.deepEqual(plain(calls), [['feiyu-memgate/changelog', { headers: { accept: 'application/json' }, credentials: 'same-origin' }]])
  await assert.rejects(I.fetchChangelog(async () => ({ ok: false, status: 403 })), (error) => error.code === 'HTTP 403')
})

test('staged edits become one ordered set of operations; secrets are write-only', () => {
  const field = (k) => FIELDS.find((g) => g.group === k.split('.')[0]).fields.find((f) => f.key === k.split('.')[1])
  const state = formState()
  const view = (k, draft = {}, secrets) => plain(I.fieldView(field(k), k.split('.')[0], state, draft, secrets))
  assert.deepEqual([view('advanced.recallLimit').text, view('advanced.recallLimit').overridden], ['5', true], 'a field in the user layer is changed')
  assert.equal(view('modules.autoCapture').overridden, false)
  assert.equal(view('modules.autoCapture').value, false, 'missing values show their default')
  assert.equal(view('advanced.recallLimit', { 'advanced.recallLimit': { value: '11' } }).invalid, true)
  assert.equal(view('external.url', { 'external.url': { value: 'ftp://x' } }).invalid, true)
  assert.equal(view('external.url', { 'external.url': { value: '' } }).invalid, false, 'an empty address is allowed')
  assert.deepEqual(view('advanced.recallLimit', { 'advanced.recallLimit': { unset: true } }), {
    k: 'advanced.recallLimit', field: plain(field('advanced.recallLimit')), secret: false, value: 5, text: '5', overridden: false, invalid: false,
  })
  const token = view('external.token', {}, { 'external.token': true })
  assert.deepEqual([token.secret, token.text, token.configured, token.clearing], [true, '', true, false])
  assert.equal(view('external.token').configured, null, 'unknown until the status says')

  const ops = plain(I.buildOps({
    'external.token': { value: '' },
    'advanced.recallLimit': { value: ' 7 ' },
    'modules.autoCapture': { value: true },
    'retrieval.apiKey': { unset: true },
    'gateway.dataDir': { value: '  D:/memories  ' },
  }))
  assert.deepEqual(ops, [
    { op: 'set', path: ['modules', 'autoCapture'], value: true },
    { op: 'unset', path: ['retrieval', 'apiKey'] },
    { op: 'set', path: ['gateway', 'dataDir'], value: 'D:/memories' },
    { op: 'set', path: ['advanced', 'recallLimit'], value: 7 },
  ])
  assert.deepEqual(plain(I.buildOps({ 'external.token': { value: 'fake-new-token' } })), [{ op: 'set', path: ['external', 'token'], value: 'fake-new-token' }])
})

test('save sends every staged edit in one mutation fenced by the revision read', async () => {
  const calls = []
  const form = { state: formState(), mutate: async (ops, revision) => { calls.push({ ops: plain(ops), revision }); return true } }
  assert.equal(await I.saveDraft(form, { 'modules.autoRecall': { value: false }, 'advanced.profile': { value: 'broad' } }), true)
  assert.deepEqual(calls, [{ ops: [{ op: 'set', path: ['modules', 'autoRecall'], value: false }, { op: 'set', path: ['advanced', 'profile'], value: 'broad' }], revision: 7 }])
  assert.equal(await I.saveDraft(form, { 'external.token': { value: '' } }), true)
  assert.equal(calls.length, 1, 'nothing to write, nothing sent')
  const refused = { state: formState(), mutate: async () => false }
  assert.equal(await I.saveDraft(refused, { 'modules.autoRecall': { value: false } }), false)
})

test('status rows: the relevance check appears only while it is on, with its counts and the skipped kinds', () => {
  const base = { gateway: { mode: 'external', state: 'configured', url: 'http://127.0.0.1:8080' }, embed: null }
  const rows = (data, lang) => plain(I.statusRows({ ...base, ...data }, tr(lang)))
  assert.ok(!rows({}, 'en').some(([label]) => label === 'Relevance check'))
  assert.ok(!rows({ relevance: { enabled: false, calls: 3, kept: 1, rejected: 2, skipped: {} } }, 'en').some(([label]) => label === 'Relevance check'))
  assert.deepEqual(rows({ relevance: { enabled: true, calls: 5, kept: 7, rejected: 9, skipped: {} } }, 'en').at(-1), ['Relevance check', '5 calls · 7 kept · 9 left out'])
  assert.deepEqual(rows({ relevance: { enabled: true, calls: 5, kept: 7, rejected: 9, skipped: { timeout: 2, unusable: 1 } } }, 'en').at(-1), ['Relevance check', '5 calls · 7 kept · 9 left out · 3 skipped (timeout 2, unusable 1)'])
  assert.deepEqual(rows({ relevance: { enabled: true, calls: 1, kept: 0, rejected: 2, skipped: {} } }, 'zh').at(-1), ['相关性检查', '调用 1 次 · 保留 0 条 · 排除 2 条'])
})

test('status rows: managed and external, in the page language', async () => {
  const managed = {
    gateway: { mode: 'managed', state: 'running' },
    python: { found: true, version: '3.12.4', source: 'path', executable: 'python-for-tests' },
    retrieval: { setting: 'auto', active: 'local' },
    store: { type: 'embedding', file: 'memories-local-x.db', count: 3 },
    embed: { state: 'ready', model: 'bge-small-zh-v1.5', dimensions: 512, parity: 'approximate' },
    migration: { state: 'running', copied: 1, total: 3 },
  }
  assert.deepEqual(plain(I.statusRows(managed, tr('en'))), [
    ['Gateway', 'managed · running'],
    ['Python', '3.12.4 (path) · python-for-tests'],
    ['Retrieval', 'auto → local'],
    ['Store', 'embedding · memories-local-x.db · 3 memories'],
    ['Embedding add-on', 'ready · bge-small-zh-v1.5 · 512d · scores not calibrated'],
    ['Copy from the keyword store', 'copying · 1 of 3 copied'],
  ])
  const hybrid = {
    gateway: { mode: 'managed', state: 'running' },
    python: { found: true, version: '3.12.4', source: 'path', executable: 'python-for-tests' },
    retrieval: { setting: 'auto', active: 'local', hybrid: { setting: true, active: true } },
    store: { type: 'hybrid', file: 'memories-local-x.db', keywordFile: 'memories-local-x-keywords.db', hybrid: true, count: 3 },
    embed: null,
  }
  assert.deepEqual(plain(I.statusRows(hybrid, tr('en'))).slice(2, 4), [
    ['Retrieval', 'auto → local + keyword (hybrid)'],
    ['Store', 'hybrid · memories-local-x.db + memories-local-x-keywords.db · 3 memories'],
  ])
  assert.deepEqual(plain(I.statusRows(hybrid, tr('zh'))).slice(2, 4), [
    ['检索', 'auto → local + 关键词（混合）'],
    ['存储', 'hybrid · memories-local-x.db + memories-local-x-keywords.db · 3 条记忆'],
  ])
  // The switch is on but the resolved store is not hybrid (keyword): the rows stay as they were.
  const idle = { ...hybrid, retrieval: { setting: 'keyword', active: 'keyword', hybrid: { setting: true, active: false } }, store: { type: 'sqlite', file: 'memories.db', count: 1 } }
  assert.deepEqual(plain(I.statusRows(idle, tr('en'))).slice(2, 4), [['Retrieval', 'keyword → keyword'], ['Store', 'sqlite · memories.db · 1 memories']])
  const external = plain(I.statusRows({ gateway: { mode: 'external', state: 'configured', url: 'http://127.0.0.1:8080' }, embed: null }, tr('zh')))
  assert.deepEqual(external, [['网关', '外部 · 已配置 · http://127.0.0.1:8080'], ['检索', '由外部网关决定'], ['向量插件', '未安装']])
  assert.deepEqual(plain(I.statusRows({ gateway: { mode: 'managed', state: 'brand-new' }, python: { found: false }, retrieval: { setting: 'keyword', active: 'keyword' } }, tr('en')))[0], ['Gateway', 'managed · brand-new'])

  const fetched = []
  const ok = await I.fetchStatus(async (url, init) => {
    fetched.push({ url, init: plain(init) })
    return { ok: true, json: async () => ({ gateway: { mode: 'managed' } }) }
  })
  assert.deepEqual(plain(ok), { gateway: { mode: 'managed' } })
  assert.deepEqual(fetched, [{ url: 'feiyu-memgate/status', init: { headers: { accept: 'application/json' }, credentials: 'same-origin' } }])
  await assert.rejects(I.fetchStatus(async () => ({ ok: false, status: 403 })), (error) => error.code === 'HTTP 403')
})

// ------------------------------------------------------------------ the page: summary, tabs, footer

test('the summary view is the one-line text, in the page language', () => {
  for (const lang of LANGS) assert.equal(I.SettingsPage({ view: 'summary', translate: tr(lang) }), DICT[lang].summary)
  assert.equal(I.SettingsPage({ view: 'summary', t: tr('en') }), DICT.en.summary, 'the slot may pass t or translate')
})

test('the page view is the root div.fmg-root with the hero, six tabs in order, one tabpanel and the footer, and no save bar while nothing is staged', () => {
  const expectedIds = PAGE_TABS
  const labels = {
    en: ['Overview', 'Features', 'Retrieval', 'Advanced', 'Changelog', 'Feedback'],
    zh: ['概览', '功能', '检索', '高级', '更新日志', '反馈'],
  }
  for (const lang of LANGS) {
    const { tree } = renderBody({ lang })
    assert.equal(tree.type, 'div')
    assert.equal(tree.props.className, 'fmg-root')
    assert.deepEqual(tree.props.children.filter(Boolean).map((child) => child.type), ['header', 'div', 'div', 'footer'], 'hero, tabs, panel, footer (a save bar is not there)')
    assert.equal(withClass(tree, 'fmg-hero').length, 1)
    const [tablist] = withRole(tree, 'tablist')
    assert.ok(tablist.props['aria-label'])
    const tabs = withRole(tablist, 'tab')
    assert.deepEqual(tabs.map((tab) => tab.props.id), expectedIds.map((id) => `feiyu-memgate-tab-${id}`))
    assert.deepEqual(tabs.map((tab) => textOf(tab)), labels[lang])
    assert.deepEqual(tabs.map((tab) => tab.props['aria-selected']), ['true', 'false', 'false', 'false', 'false', 'false'])
    assert.deepEqual(tabs.map((tab) => tab.props.tabIndex), [0, -1, -1, -1, -1, -1], 'roving focus')
    const panels = withRole(tree, 'tabpanel')
    assert.equal(panels.length, 1)
    assert.equal(panels[0].props['aria-labelledby'], 'feiyu-memgate-tab-overview')
    assert.equal(tabs[0].props['aria-controls'], panels[0].props.id)
    assert.deepEqual(tabs.slice(1).map((tab) => tab.props['aria-controls']), [undefined, undefined, undefined, undefined, undefined], 'a tab whose panel is not in the document points at nothing')
    assert.equal(withClass(tree, 'fmg-savebar').length, 0)
  }
})

test('the tab bar moves with the arrow keys, Home and End, and selects at once', () => {
  const picked = []
  const loaded = load()
  const t = tr('en')
  const tabs = PAGE_TABS.map((id) => ({ id, label: id }))
  const bar = (value) => expand({ type: (p) => loaded.internals.SettingsPage && p, props: {} }) && null
  void bar
  // The key handler is the Tabs component's: take it from a rendered page, whose current tab is 'overview'.
  const { tree } = renderBody()
  const [tablist] = withRole(tree, 'tablist')
  const focused = []
  const press = (key) => {
    const event = { key, preventDefault() { event.prevented = true }, currentTarget: { querySelectorAll: () => tabs.map((x, i) => ({ focus: () => focused.push(i) })) } }
    tablist.props.onKeyDown(event)
    return event
  }
  assert.equal(press('ArrowRight').prevented, true)
  assert.equal(press('ArrowLeft').prevented, true)
  assert.equal(press('End').prevented, true)
  assert.equal(press('Home').prevented, true)
  assert.equal(press('x').prevented, undefined)
  assert.deepEqual(focused, [1, 5, 5, 0])
  assert.ok(t('tabsLabel'))
  assert.deepEqual(picked, [])
})

test('the footer links are https only, open in a new tab and carry noopener and noreferrer; feedback is a button that stays on the page', () => {
  const { tree } = renderBody({ statusData: status() })
  const [footer] = findAll(tree, (n) => n.type === 'footer')
  const links = findAll(footer, (n) => n.type === 'a')
  assert.deepEqual(links.map(textOf), ['Documentation', 'Source'])
  assert.deepEqual(findAll(footer, (n) => n.type === 'button').map(textOf), ['Feedback'])
  for (const link of links) {
    assert.equal(new URL(link.props.href).protocol, 'https:')
    assert.equal(link.props.target, '_blank')
    assert.ok(link.props.rel.split(' ').includes('noopener') && link.props.rel.split(' ').includes('noreferrer'), link.props.rel)
  }
  assert.ok(textOf(footer).includes('dsh-feiyu-memgate 0.3.2'), 'the installed version, once the status is known')
  const [before] = findAll(renderBody().tree, (n) => n.type === 'footer')
  assert.ok(!textOf(before).includes('dsh-feiyu-memgate '), 'no version before that')
})

test('every element id the page makes starts with feiyu-memgate- and is unique within the tree, on every tab with every group open', () => {
  for (const lang of LANGS) {
    for (const tab of PAGE_TABS) {
      const { tree } = renderBody({ tab, lang, openAll: true, statusData: status(), changelog: { version: '0.3.2', entries: [{ version: '0.3.2', body: 'a' }, { version: '0.3.1', body: 'b' }] } })
      const ids = findAll(tree, (n) => n.props.id !== undefined).map((n) => n.props.id)
      assert.ok(ids.length > 5, tab)
      for (const id of ids) assert.match(id, /^feiyu-memgate-/, `${tab}: ${id}`)
      assert.equal(new Set(ids).size, ids.length, `${tab}: duplicate ids ${ids.filter((id, i) => ids.indexOf(id) !== i)}`)
    }
  }
  const ids = findAll(renderTab('advanced', { secrets: { 'external.token': true } }), (n) => n.props.id !== undefined).map((n) => n.props.id)
  assert.equal(new Set(ids).size, ids.length)
})

test('htmlFor, aria-labelledby, aria-describedby and aria-controls point at ids that exist in the tree', () => {
  for (const tab of PAGE_TABS) {
    const { tree } = renderBody({ tab, openAll: true, statusData: status(), changelog: { version: '0.3.2', entries: [{ version: '0.3.2', body: 'a' }] } })
    const ids = new Set(findAll(tree, (n) => n.props.id !== undefined).map((n) => n.props.id))
    walk(tree, (n) => {
      for (const attr of ['htmlFor', 'aria-labelledby', 'aria-describedby']) if (n.props[attr] !== undefined) assert.ok(ids.has(n.props[attr]), `${tab}: ${attr}=${n.props[attr]}`)
      // The tab bar names the panel of every tab, and only the selected one is in the tree.
      if (n.props['aria-controls'] !== undefined && n.props.role !== 'tab') assert.ok(ids.has(n.props['aria-controls']), `${tab}: aria-controls=${n.props['aria-controls']}`)
    })
  }
})

// ------------------------------------------------------------------ the page when the form is not usable

test('without a usable settings form the page still draws the hero, tabs and footer, and says what is the matter on a settings tab', () => {
  for (const [name, form, notice] of [
    ['no form', null, 'unavailable'],
    ['unavailable', { state: { status: 'unavailable' } }, 'unavailable'],
    ['loading', { state: { status: 'loading' } }, 'loading'],
  ]) {
    for (const tab of PAGE_TABS) {
      const { tree, t } = renderBody({ tab, form })
      assert.equal(withClass(tree, 'fmg-hero').length, 1, `${name} ${tab}`)
      assert.equal(withRole(tree, 'tab').length, PAGE_TABS.length)
      assert.equal(findAll(tree, (n) => n.type === 'footer').length, 1)
      assert.equal(withClass(tree, 'fmg-savebar').length, 0)
      const text = textOf(withRole(tree, 'tabpanel'))
      if (SETTINGS_TABS.includes(tab)) {
        assert.ok(text.includes(t(notice)), `${name} ${tab}`)
        assert.equal(findAll(tree, (n) => n.type === 'input' || n.props.role === 'switch').length, 0, 'no rows nobody can use')
      }
      // The model list does not depend on the form.
      assert.equal(text.includes(t('modelsTitle')), tab === 'retrieval', `${name} ${tab}`)
    }
  }
  for (const lang of LANGS) {
    assert.notEqual(tr(lang)('unavailable'), 'unavailable', 'the key exists')
    assert.ok(tr(lang)('unavailable').includes('/memgate set'), 'it says where else the settings can be changed')
  }
})

test('a read-only form disables every control of every settings tab and says so', () => {
  const user = { advanced: { recallLimit: 5, afterthought: true } }
  const state = formState({ writable: false, user, value: user })
  for (const lang of LANGS) {
    for (const id of SETTINGS_TABS) {
      const tree = renderTab(id, { lang, state, disabled: true, readOnly: true, secrets: { 'external.token': true, 'retrieval.apiKey': true }, draft: { 'retrieval.apiKey': { value: 'x' } } })
      assert.ok(textOf(tree).includes(tr(lang)('readOnly')), `${lang} ${id}`)
      const controls = findAll(tree, (n) => (n.type === 'input' || n.type === 'button') && !(n.props.className || '').includes('fmg-fold__head'))
      assert.ok(controls.length > 3, id)
      for (const control of controls) assert.equal(control.props.disabled, true, `${id}: ${control.type} ${control.props.id || control.props.role || textOf(control) || control.props['aria-label']}`)
    }
  }
  // Through the page, from the form's own state.
  const { tree, t } = renderBody({ tab: 'features', form: { state: formState({ writable: false }), mutate: async () => true } })
  assert.ok(textOf(tree).includes(t('readOnly')))
  const switches = withRole(tree, 'switch')
  assert.ok(switches.length > 3)
  for (const control of switches) assert.equal(control.props.disabled, true)
  // And a writable form shows no such notice.
  assert.ok(!textOf(renderTab('features')).includes(tr('en')('readOnly')))
})

// ------------------------------------------------------------------ one row per setting

test('every setting is drawn exactly once across the settings tabs, with its label and help in both languages', () => {
  for (const lang of LANGS) {
    const tabs = allTabs({ lang, secrets: { 'external.token': false, 'retrieval.apiKey': false } })
    const labelIds = []
    const helpIds = []
    const texts = {}
    for (const tree of Object.values(tabs)) {
      walk(tree, (n) => {
        const id = n.props.id
        if (typeof id !== 'string') return
        if (id.endsWith('-label')) { labelIds.push(id); texts[id] = textOf(n) }
        if (id.endsWith('-help')) { helpIds.push(id); texts[id] = textOf(n) }
      })
    }
    for (const field of ALL_FIELDS) {
      const id = I.fieldId(field.k)
      assert.equal(labelIds.filter((x) => x === `${id}-label`).length, 1, `${field.k} label`)
      assert.equal(helpIds.filter((x) => x === `${id}-help`).length, 1, `${field.k} help`)
      assert.equal(texts[`${id}-label`], field.label[lang], field.k)
      if (field.secret) assert.ok(texts[`${id}-help`].startsWith(field.help[lang]), field.k)
      else assert.equal(texts[`${id}-help`], field.help[lang], field.k)
    }
    assert.equal(labelIds.length, ALL_FIELDS.length, 'no row that is not a setting')
  }
})

test('a setting\u2019s element id is feiyu-memgate- plus its key with the dot as a dash', () => {
  assert.equal(I.fieldId('advanced.recallLimit'), 'feiyu-memgate-advanced-recallLimit')
  assert.equal(I.fieldId('external.url'), 'feiyu-memgate-external-url')
})

test('booleans are switches: button[role=switch] with the right aria-checked, a name and the help as description', () => {
  const state = formState({ value: { modules: { autoRecall: false }, advanced: { skipRepeats: true } } })
  for (const field of ALL_FIELDS.filter((f) => f.kind === 'boolean')) {
    const tree = renderRow(field.k, { state })
    const [control] = byId(tree, I.fieldId(field.k))
    assert.equal(control.type, 'button', field.k)
    assert.equal(control.props.role, 'switch')
    const expected = field.k === 'modules.autoRecall' ? false : field.default
    assert.equal(control.props['aria-checked'], String(expected), field.k)
    assert.equal(control.props['aria-label'], field.label.en)
    assert.equal(control.props['aria-describedby'], `${I.fieldId(field.k)}-help`)
    assert.equal(control.props.type, 'button', 'a switch never submits a form')
  }
  const staged = renderRow('modules.autoCapture', { draft: { 'modules.autoCapture': { value: true } } })
  assert.equal(byId(staged, 'feiyu-memgate-modules-autoCapture')[0].props['aria-checked'], 'true', 'a staged edit shows')
  const edits = []
  const row = renderRow('modules.autoRecall', { edit: (k, v) => edits.push([k, v]) })
  byId(row, 'feiyu-memgate-modules-autoRecall')[0].props.onClick()
  assert.deepEqual(edits, [['modules.autoRecall', false]], 'a click stages the opposite value')
})

test('integers are steppers: two labelled buttons around a numeric input that shows the text as it is', () => {
  for (const lang of LANGS) {
    for (const field of ALL_FIELDS.filter((f) => f.kind === 'integer')) {
      const tree = renderRow(field.k, { lang })
      const [stepper] = withClass(tree, 'fmg-stepper')
      const buttons = findAll(stepper, (n) => n.type === 'button')
      assert.deepEqual(buttons.map((b) => b.props['aria-label']), [tr(lang)('stepDown'), tr(lang)('stepUp')], field.k)
      const [input] = byId(stepper, I.fieldId(field.k))
      assert.equal(input.type, 'input')
      assert.equal(input.props.inputMode, 'numeric')
      assert.equal(input.props.value, String(field.default), field.k)
      if (field.unit) assert.ok(textOf(tree).includes(field.unit[lang]), `${field.k} unit`)
    }
  }
  // The buttons lead one step, inside the range; at an end the button is off.
  const edits = []
  const tree = renderRow('advanced.recallLimit', { edit: (k, v) => edits.push([k, v]) })
  const [less, more] = findAll(withClass(tree, 'fmg-stepper')[0], (n) => n.type === 'button')
  less.props.onClick()
  more.props.onClick()
  byId(tree, 'feiyu-memgate-advanced-recallLimit')[0].props.onChange({ target: { value: '9x' } })
  assert.deepEqual(edits, [['advanced.recallLimit', '4'], ['advanced.recallLimit', '6'], ['advanced.recallLimit', '9x']])
  const stepperAt = (value) => findAll(withClass(renderRow('advanced.recallLimit', { draft: { 'advanced.recallLimit': { value } } }), 'fmg-stepper')[0], (n) => n.type === 'button')
  assert.deepEqual(stepperAt('1').map((b) => b.props.disabled), [true, undefined])
  assert.deepEqual(stepperAt('10').map((b) => b.props.disabled), [undefined, true])
})

test('stepText: one step inside the range, and a text that is no number steps from the nearer end', () => {
  assert.equal(I.stepText('5', 1, 1, 10), '6')
  assert.equal(I.stepText(' 5 ', -1, 1, 10), '4')
  assert.equal(I.stepText('10', 1, 1, 10), '10')
  assert.equal(I.stepText('1', -1, 1, 10), '1')
  assert.equal(I.stepText('', 1, 1, 10), '1', 'plus from nothing leads to the lowest value')
  assert.equal(I.stepText('x', -1, 1, 10), '10', 'minus from nothing leads to the highest')
  assert.equal(I.stepText('99', -1, 1, 10), '10', 'an out-of-range number is pulled back')
})

test('choices are a radiogroup with one radio per option, exactly one checked, and each option\u2019s name and note split from the dictionary label', () => {
  const choices = ALL_FIELDS.filter((f) => f.kind === 'choice')
  assert.ok(choices.length >= 4)
  for (const lang of LANGS) {
    for (const field of choices) {
      const tree = renderRow(field.k, { lang })
      const [group] = withRole(tree, 'radiogroup')
      assert.equal(group.props['aria-labelledby'], `${I.fieldId(field.k)}-label`, 'named by the label of the row')
      const radios = withRole(group, 'radio')
      assert.deepEqual(radios.map((r) => r.props.key), field.options.map((o) => o.value))
      assert.deepEqual(radios.map((r) => r.props['aria-checked']), field.options.map((o) => String(o.value === field.default)), field.k)
      assert.equal(radios.filter((r) => r.props['aria-checked'] === 'true').length, 1)
      assert.equal(radios.filter((r) => r.props.tabIndex === 0).length, 1, 'one tab stop')
      field.options.forEach((option, index) => {
        const expected = I.splitOption(option.label[lang])
        const [name] = withClass(radios[index], 'fmg-choice__name')
        assert.equal(name.props.children[0], expected.name, `${field.k}.${option.value}`)
        assert.equal(textOf(withClass(radios[index], 'fmg-choice__note')), expected.note)
        assert.ok(expected.name && expected.note, 'the dictionary label has both parts')
      })
      // The label of the row is not a <label> for one control: it names the group.
      assert.equal(findAll(tree, (n) => n.type === 'label').length, 0)
    }
  }
  const edits = []
  const radios = withRole(renderRow('advanced.profile', { edit: (k, v) => edits.push([k, v]) }), 'radio')
  radios[1].props.onClick()
  assert.deepEqual(edits, [['advanced.profile', 'precise']])
  const staged = withRole(renderRow('advanced.profile', { draft: { 'advanced.profile': { value: 'precise' } } }), 'radio')
  assert.deepEqual(staged.map((r) => r.props['aria-checked']), ['false', 'true'])
})

test('the arrow keys move a choice and select it, like a native radio group', () => {
  const edits = []
  const [group] = withRole(renderRow('retrieval.tier', { edit: (k, v) => edits.push([k, v]) }), 'radiogroup')
  const focused = []
  const press = (key) => {
    const event = { key, preventDefault() { event.prevented = true }, currentTarget: { querySelectorAll: () => [0, 1, 2, 3].map((i) => ({ focus: () => focused.push(i) })) } }
    group.props.onKeyDown(event)
    return event
  }
  assert.equal(press('ArrowDown').prevented, true)
  assert.equal(press('ArrowLeft').prevented, true, 'from the first option backwards wraps to the last')
  assert.equal(press('a').prevented, undefined, 'other keys are left alone')
  assert.deepEqual(edits, [['retrieval.tier', 'keyword'], ['retrieval.tier', 'local']])
  assert.deepEqual(focused, [1, 3])
  assert.equal(withRole(renderRow('retrieval.tier', { disabled: true }), 'radiogroup')[0].props.onKeyDown, undefined, 'a disabled group takes no keys')
})

test('strings are a text input and a url that is not an address says why; the staged text shows as typed', () => {
  for (const field of ALL_FIELDS.filter((f) => f.kind === 'string' && !f.secret)) {
    const tree = renderRow(field.k)
    const [input] = byId(tree, I.fieldId(field.k))
    assert.equal(input.type, 'input', field.k)
    assert.equal(input.props.type, 'text')
    assert.equal(input.props.value, field.default)
    assert.equal(input.props['aria-describedby'], `${I.fieldId(field.k)}-help`)
  }
  const draft = { 'retrieval.tier': { value: 'endpoint' }, 'retrieval.url': { value: 'ftp://x' } }
  const tree = renderRow('retrieval.url', { draft })
  assert.equal(byId(tree, 'feiyu-memgate-retrieval-url')[0].props.value, 'ftp://x')
  assert.equal(byId(tree, 'feiyu-memgate-retrieval-url')[0].props['aria-invalid'], 'true')
  assert.equal(textOf(withRole(tree, 'alert')), tr('en')('invalidUrl'))
  const edits = []
  byId(renderRow('gateway.python', { edit: (k, v) => edits.push([k, v]) }), 'feiyu-memgate-gateway-python')[0].props.onChange({ target: { value: 'py' } })
  assert.deepEqual(edits, [['gateway.python', 'py']])
})

test('secrets are password inputs with autoComplete=new-password and never a value; the tag follows the status and Clear shows only when something is stored or typed', () => {
  for (const field of ALL_FIELDS.filter((f) => f.secret)) {
    const id = I.fieldId(field.k)
    const view = (secrets, draft = {}) => renderRow(field.k, { secrets, draft })
    // stored
    let tree = view({ [field.k]: true })
    const [input] = byId(tree, id)
    assert.equal(input.props.type, 'password')
    assert.equal(input.props.autoComplete, 'new-password')
    assert.equal(input.props.value, '', 'a stored secret is never sent back to the page')
    assert.deepEqual(withClass(tree, 'fmg-tag').map(textOf), ['Set'])
    assert.deepEqual(buttonsNamed(tree, 'Clear').map((b) => b.props.disabled), [undefined])
    assert.ok(textOf(byId(tree, `${id}-help`)).endsWith('Leave empty to keep the stored value.'))
    // not stored
    tree = view({ [field.k]: false })
    assert.deepEqual(withClass(tree, 'fmg-tag').map(textOf), ['Not set'])
    assert.equal(buttonsNamed(tree, 'Clear').length, 0)
    // unknown (the status has not answered)
    tree = view(null)
    assert.deepEqual(withClass(tree, 'fmg-tag').map(textOf), [])
    assert.equal(buttonsNamed(tree, 'Clear').length, 0)
    // typed: shown as typed, and can be cleared again
    tree = view({ [field.k]: false }, { [field.k]: { value: 'fake-secret' } })
    assert.equal(byId(tree, id)[0].props.value, 'fake-secret')
    assert.equal(buttonsNamed(tree, 'Clear').length, 1)
    // cleared on save
    tree = view({ [field.k]: true }, { [field.k]: { unset: true } })
    assert.equal(byId(tree, id)[0].props.value, '')
    assert.equal(textOf(byId(tree, `${id}-help`)), 'Cleared when you save.')
    assert.equal(buttonsNamed(tree, 'Clear')[0].props.disabled, true)
  }
  const resets = []
  const tree = renderRow('external.token', { secrets: { 'external.token': true }, reset: (k) => resets.push(k) })
  buttonsNamed(tree, 'Clear')[0].props.onClick()
  assert.deepEqual(resets, ['external.token'])
})

test('a changed setting shows the Changed tag and a Reset that calls reset(key); an unchanged one shows neither', () => {
  for (const lang of LANGS) {
    const resets = []
    const tree = renderRow('advanced.recallLimit', { lang, reset: (k) => resets.push(k) })
    assert.deepEqual(withClass(tree, 'fmg-tag').map(textOf), [tr(lang)('overridden')])
    const [reset] = buttonsNamed(tree, tr(lang)('reset'))
    reset.props.onClick()
    assert.deepEqual(resets, ['advanced.recallLimit'])
  }
  const plainRow = renderRow('advanced.skipRepeats')
  assert.equal(withClass(plainRow, 'fmg-tag').length, 0)
  assert.equal(buttonsNamed(plainRow, 'Reset').length, 0)
  // A staged edit counts as changed, a staged return to the inherited value does not.
  assert.equal(withClass(renderRow('advanced.skipRepeats', { draft: { 'advanced.skipRepeats': { value: false } } }), 'fmg-tag').length, 1)
  assert.equal(withClass(renderRow('advanced.recallLimit', { draft: { 'advanced.recallLimit': { unset: true } } }), 'fmg-tag').length, 0)
})

test('an experimental setting shows the Experimental tag, in both languages; the others do not', () => {
  const experimental = ALL_FIELDS.filter((f) => f.experimental).map((f) => f.k)
  assert.deepEqual(experimental.sort(), ['advanced.afterthought', 'advanced.conversationPool', 'advanced.recentPool', 'advanced.relevanceCheck'])
  for (const lang of LANGS) {
    for (const field of ALL_FIELDS) {
      const tags = withClass(renderRow(field.k, { lang }), 'fmg-tag').map(textOf)
      assert.equal(tags.includes(tr(lang)('experimental')), Boolean(field.experimental), `${lang} ${field.k}`)
    }
  }
})

test('an invalid integer draft shows the range message with role=alert and marks the input aria-invalid', () => {
  for (const lang of LANGS) {
    const tree = renderRow('advanced.recallLimit', { lang, draft: { 'advanced.recallLimit': { value: '99' } } })
    const [alert] = withRole(tree, 'alert')
    assert.equal(textOf(alert), tr(lang)('invalidInteger', { min: 1, max: 10 }))
    const [input] = byId(tree, 'feiyu-memgate-advanced-recallLimit')
    assert.equal(input.props['aria-invalid'], 'true')
    assert.equal(withClass(tree, 'fmg-stepper')[0].props['data-invalid'], 'true')
    // The help text is not lost.
    assert.ok(textOf(tree).includes(FIELDS.find((g) => g.group === 'advanced').fields.find((f) => f.key === 'recallLimit').help[lang]))
  }
  assert.equal(withRole(renderRow('advanced.recallLimit'), 'alert').length, 0)
  assert.equal(byId(renderRow('advanced.recallLimit'), 'feiyu-memgate-advanced-recallLimit')[0].props['aria-invalid'], undefined)
  for (const bad of ['', 'abc', '0', '11', '1.5', '-1']) {
    assert.equal(withRole(renderRow('advanced.recallLimit', { draft: { 'advanced.recallLimit': { value: bad } } }), 'alert').length, 1, JSON.stringify(bad))
  }
})

// ------------------------------------------------------------------ settings that depend on another one

// The rows of a tab that sit in their own Reveal, by setting key.
function revealsByKey(tree) {
  const out = {}
  for (const reveal of withClass(tree, 'fmg-reveal')) {
    const inner = reveal.props.children[0]
    if (inner.props.id !== undefined) continue // a folding group, not a dependent row
    const [help] = findAll(reveal, (n) => typeof n.props.id === 'string' && n.props.id.endsWith('-help'))
    out[help.props.id.replace(/^feiyu-memgate-/, '').replace(/-help$/, '').replace('-', '.')] = reveal
  }
  return out
}

test('dependent settings sit in a Reveal that is closed (data-open=false, aria-hidden) while the setting they need has no fitting value', () => {
  const defaults = Object.fromEntries(ALL_FIELDS.map((f) => [f.k, f.default]))
  const withNeeds = ALL_FIELDS.filter((f) => f.needs)
  const found = {}
  for (const id of SETTINGS_TABS) Object.assign(found, revealsByKey(renderTab(id)))
  assert.deepEqual(Object.keys(found).sort(), withNeeds.map((f) => f.k).sort(), 'every dependent setting has a Reveal and no other row has one')
  for (const field of withNeeds) {
    const open = field.needs.in.includes(defaults[field.needs.key])
    const reveal = found[field.k]
    assert.equal(reveal.props['data-open'], String(open), field.k)
    assert.equal(reveal.props['aria-hidden'], open ? undefined : 'true', field.k)
  }
  const closed = withNeeds.filter((f) => !f.needs.in.includes(defaults[f.needs.key])).map((f) => f.k)
  for (const k of ['retrieval.url', 'retrieval.apiKey', 'external.url', 'external.token', 'advanced.captureMinChars', 'advanced.afterthoughtMax', 'advanced.poolSize', 'advanced.recentDays', 'advanced.relevanceModel']) {
    assert.ok(closed.includes(k), `${k} is closed with the defaults`)
  }
  assert.ok(!closed.includes('gateway.python') && !closed.includes('retrieval.hybrid'), 'these two are open with the defaults')
  // The dependent rows are drawn as sub-rows; the ones they depend on are not.
  assert.equal(withClass(found['advanced.afterthoughtMax'], 'fmg-row--sub').length, 1)
  assert.equal(withClass(renderRow('advanced.afterthought'), 'fmg-row--sub').length, 0)
})

test('staging the other setting to a fitting value opens the dependent rows before anything is saved', () => {
  const open = (draft, k, tab = 'advanced', state) => revealsByKey(renderTab(tab, { draft, state }))[k].props['data-open']
  assert.equal(open({}, 'advanced.afterthoughtMax'), 'false')
  assert.equal(open({ 'advanced.afterthought': { value: true } }, 'advanced.afterthoughtMax'), 'true')
  assert.equal(open({ 'advanced.afterthought': { value: true } }, 'advanced.poolSize'), 'false', 'a different switch')
  assert.equal(open({ 'advanced.conversationPool': { value: true } }, 'advanced.poolSize'), 'true')
  assert.equal(open({ 'advanced.recentPool': { value: true } }, 'advanced.recentDays'), 'true')
  assert.equal(open({ 'advanced.relevanceCheck': { value: true } }, 'advanced.relevanceTimeoutMs'), 'true')
  assert.equal(open({}, 'advanced.captureMinChars', 'features'), 'false')
  assert.equal(open({ 'modules.autoCapture': { value: true } }, 'advanced.captureMinChars', 'features'), 'true')
  assert.equal(open({}, 'retrieval.url', 'retrieval'), 'false')
  assert.equal(open({ 'retrieval.tier': { value: 'endpoint' } }, 'retrieval.url', 'retrieval'), 'true')
  assert.equal(open({ 'retrieval.tier': { value: 'endpoint' } }, 'retrieval.apiKey', 'retrieval'), 'true')
  assert.equal(open({ 'retrieval.tier': { value: 'keyword' } }, 'retrieval.hybrid', 'retrieval'), 'false', 'hybrid needs a vector tier')
  assert.equal(open({ 'gateway.mode': { value: 'external' } }, 'external.token'), 'true')
  assert.equal(open({ 'gateway.mode': { value: 'external' } }, 'gateway.python'), 'false')
  // A staged return to the inherited value closes it again.
  assert.equal(open({ 'advanced.afterthought': { unset: true } }, 'advanced.afterthoughtMax'), 'false')
  // Saved values count too.
  assert.equal(open({}, 'advanced.afterthoughtMax', 'advanced', formState({ value: { advanced: { afterthought: true } } })), 'true')
})

test('savable() drops a staged invalid edit of a closed row and keeps everything else', () => {
  const draft = {
    'advanced.afterthoughtMax': { value: '99' }, // closed row, invalid: dropped
    'advanced.poolSize': { value: '4' }, // closed row, valid: kept
    'advanced.recallLimit': { value: '99' }, // shown row, invalid: kept (it blocks the save)
    'modules.autoRecall': { value: false },
    'external.url': { value: 'ftp://x' }, // closed (managed gateway), invalid: dropped
    'external.token': { unset: true },
  }
  const views = I.pageView(formState(), draft, null)
  assert.deepEqual(plain(I.savable(draft, views)), {
    'advanced.poolSize': { value: '4' },
    'advanced.recallLimit': { value: '99' },
    'modules.autoRecall': { value: false },
    'external.token': { unset: true },
  })
  assert.deepEqual(draft['advanced.afterthoughtMax'], { value: '99' }, 'the draft itself is not changed')
  // Once the row is open the same edit is kept: it can be seen and corrected.
  const opened = { ...draft, 'advanced.afterthought': { value: true } }
  assert.ok('advanced.afterthoughtMax' in I.savable(opened, I.pageView(formState(), opened, null)))
  // The counts of the views ignore a closed invalid row, so it does not block the save.
  assert.equal(views.reduce((sum, view) => sum + view.invalid, 0), 1)
  assert.equal(I.pageView(formState(), opened, null).reduce((sum, view) => sum + view.invalid, 0), 2)
})

// ------------------------------------------------------------------ the settings tab: sections and folds

test('the Features and Retrieval tabs are plain sections; Advanced is a set of closed folding groups', () => {
  for (const lang of LANGS) {
    for (const id of ['features', 'retrieval']) {
      const tree = renderTab(id, { lang })
      const tab = I.TABS.find((entry) => entry.id === id)
      const sections = findAll(tree, (n) => n.type === 'section')
      assert.deepEqual(sections.map((s) => s.props['aria-label']), plain(tab.sections.map((s) => s.label[lang])))
      assert.equal(withClass(tree, 'fmg-fold').length, 0)
    }
    const closed = renderTab('advanced', { lang, open: {} })
    const advanced = I.TABS.find((entry) => entry.id === 'advanced')
    const heads = withClass(closed, 'fmg-fold__head')
    assert.deepEqual(withClass(closed, 'fmg-fold__title').map(textOf), plain(advanced.sections.map((s) => s.label[lang])))
    assert.deepEqual(heads.map((head) => head.props['aria-expanded']), ['false', 'false', 'false', 'false'], 'closed by default')
    for (const head of heads) assert.equal(byId(closed, head.props['aria-controls']).length, 1, 'the head controls an element that exists')
    assert.ok(textOf(closed).includes(advanced.note[lang]))
  }
  const opened = renderTab('advanced', { open: { tuning: true } })
  assert.deepEqual(withClass(opened, 'fmg-fold__head').map((head) => head.props['aria-expanded']), ['true', 'false', 'false', 'false'])
  assert.deepEqual(withClass(opened, 'fmg-fold').map((fold) => withClass(fold, 'fmg-reveal')[0].props['data-open']), ['true', 'false', 'false', 'false'])
  const toggled = []
  const tabView = I.pageView(formState(), {}, null).find((v) => v.tab.id === 'advanced')
  const tree = expand(I.SettingsTab({ t: tr('en'), lang: 'en', tabView, disabled: false, readOnly: false, edit() {}, reset() {}, open: {}, onToggle: (id) => toggled.push(id), panels: {} }))
  withClass(tree, 'fmg-fold__head')[2].props.onClick()
  assert.deepEqual(toggled, ['experiments'])
})

test('a folding group says how many of its settings are changed and marks unsaved or invalid edits', () => {
  const user = { advanced: { recallLimit: 5, skipRepeats: false } }
  const state = formState({ user, value: user })
  const heads = (draft) => withClass(renderTab('advanced', { state, draft, open: {} }), 'fmg-fold__head')
  const accessory = (head) => head.props.children[2]
  assert.deepEqual(withClass(accessory(heads({})[0]), 'fmg-tag').map(textOf), ['2 changed'])
  assert.equal(accessory(heads({})[1]), null, 'no changed setting, nothing said')
  const edited = heads({ 'advanced.showAge': { value: false } })
  const [mark] = withRole(edited[1], 'img')
  assert.equal(mark.props['aria-label'], tr('en')('tabEdited'))
  assert.ok(!mark.props.className.includes('fmg-mark--invalid'))
  const [bad] = withRole(heads({ 'advanced.recallLimit': { value: '99' } })[0], 'img')
  assert.equal(bad.props['aria-label'], tr('en')('fixFirst'))
  assert.ok(bad.props.className.includes('fmg-mark--invalid'))
})

test('the Retrieval tab places the model list after the rows of its section, and the recall trial last', () => {
  const tree = renderTab('retrieval')
  assert.deepEqual(withClass(tree, 'fmg-section__title').map(textOf), ['Retrieval method', 'Local embedding models', 'Try a recall'])
  assert.ok(textOf(tree).includes(tr('en')('modelsIntro')))
})

test('the tab bar marks a tab that holds unsaved edits or one that cannot be saved, and names the mark for a screen reader', () => {
  const t = tr('en')
  const marks = (draft) => withRole(renderBody({ draft }).tree, 'tab').map((tab) => withRole(tab, 'img').map((m) => [m.props['aria-label'], m.props.className.includes('invalid')]))
  assert.deepEqual(marks({}), [[], [], [], [], [], []])
  assert.deepEqual(marks({ 'modules.autoRecall': { value: false } }), [[], [[t('tabEdited'), false]], [], [], [], []], 'only the Features tab')
  assert.deepEqual(marks({ 'advanced.recallLimit': { value: '99' }, 'modules.autoRecall': { value: false } }), [[], [[t('tabEdited'), false]], [], [[t('fixFirst'), true]], [], []])
})

// ------------------------------------------------------------------ the save bar

test('SaveBar: nothing for no edits, the count with Discard and Save, Save off while invalid or saving, and the saved and failed lines', () => {
  const bar = (props, lang = 'en') => expand(I.SaveBar({ t: tr(lang), count: 0, invalid: false, phase: 'idle', onSave() {}, onDiscard() {}, ...props }))
  assert.equal(bar({}), null)
  assert.equal(bar({ phase: 'failed' }), null, 'with nothing staged there is nothing to show')
  const saved = bar({ phase: 'saved' })
  assert.equal(textOf(saved), 'Saved.')
  assert.equal(saved.props.role, 'status')
  assert.equal(findAll(saved, (n) => n.type === 'button').length, 0)

  const one = bar({ count: 1 })
  assert.equal(one.props.role, 'region')
  assert.equal(one.props['aria-label'], 'Save changes')
  assert.ok(textOf(one).includes('1 unsaved change') && !textOf(one).includes('1 unsaved changes'))
  assert.ok(textOf(bar({ count: 4 })).includes('4 unsaved changes'))
  assert.ok(textOf(bar({ count: 3 }, 'zh')).includes('3 项修改未保存'))
  const calls = []
  const live = bar({ count: 2, onSave: () => calls.push('save'), onDiscard: () => calls.push('discard') })
  assert.deepEqual(findAll(live, (n) => n.type === 'button').map((b) => [textOf(b), b.props.disabled]), [['Discard', undefined], ['Save changes', undefined]])
  buttonsNamed(live, 'Discard')[0].props.onClick()
  buttonsNamed(live, 'Save changes')[0].props.onClick()
  assert.deepEqual(calls, ['discard', 'save'])

  const invalid = bar({ count: 1, invalid: true })
  assert.equal(buttonsNamed(invalid, 'Save changes')[0].props.disabled, true)
  assert.equal(buttonsNamed(invalid, 'Discard')[0].props.disabled, undefined, 'an invalid edit can always be discarded')
  assert.ok(textOf(invalid).includes(tr('en')('fixFirst')))
  const saving = bar({ count: 1, phase: 'saving' })
  assert.deepEqual(findAll(saving, (n) => n.type === 'button').map((b) => [textOf(b), b.props.disabled]), [['Discard', true], ['Saving…', true]])
  const failed = bar({ count: 1, phase: 'failed' })
  assert.ok(textOf(failed).includes(tr('en')('saveFailed')))
  assert.equal(buttonsNamed(failed, 'Save changes')[0].props.disabled, undefined, 'a failed save can be tried again')
})

test('the page shows the save bar only while edits are staged, counts the edits a save would write, and Save sends them in one mutation', async () => {
  const calls = []
  const form = { state: formState(), mutate: async (ops, revision) => { calls.push({ ops: plain(ops), revision }); return true } }
  const draft = { 'modules.autoRecall': { value: false }, 'advanced.recallLimit': { value: '8' } }
  const { tree, t } = renderBody({ tab: 'features', draft, form })
  const [bar] = withClass(tree, 'fmg-savebar')
  assert.ok(textOf(bar).includes('2 unsaved changes'))
  buttonsNamed(bar, t('saveChanges'))[0].props.onClick()
  await tick()
  assert.deepEqual(calls, [{ ops: [{ op: 'set', path: ['modules', 'autoRecall'], value: false }, { op: 'set', path: ['advanced', 'recallLimit'], value: 8 }], revision: 7 }])
  // A staged invalid edit of a closed row is not counted and blocks nothing; an invalid one in view blocks Save.
  assert.equal(withClass(renderBody({ draft: { 'advanced.afterthoughtMax': { value: '99' } } }).tree, 'fmg-savebar').length, 0)
  const blocked = renderBody({ draft: { 'advanced.recallLimit': { value: '99' } } })
  const blockedSave = buttonsNamed(withClass(blocked.tree, 'fmg-savebar')[0], 'Save changes')[0]
  assert.equal(blockedSave.props.disabled, true)
  // Save does nothing for a read-only form or an invalid draft, whatever the button says.
  const readOnly = renderBody({ tab: 'features', draft, form: { state: formState({ writable: false }), mutate: async () => { calls.push('sent'); return true } } })
  buttonsNamed(withClass(readOnly.tree, 'fmg-savebar')[0], 'Save changes')[0].props.onClick()
  blockedSave.props.onClick()
  await tick()
  assert.equal(calls.length, 1)
  // The phase of a save: saving disables both buttons, saved shows the confirmation, failed the message.
  const buttonsOf = (phase) => findAll(withClass(renderBody({ draft, phase }).tree, 'fmg-savebar')[0], (n) => n.type === 'button').map((b) => b.props.disabled)
  assert.deepEqual(buttonsOf('saving'), [true, true])
  assert.ok(textOf(renderBody({ draft, phase: 'failed' }).tree).includes(t('saveFailed')))
  assert.equal(textOf(withClass(renderBody({ phase: 'saved' }).tree, 'fmg-savebar--done')[0]), 'Saved.')
})

// ------------------------------------------------------------------ the hero and the summary

test('Hero: each of the four moods shows its drawing at a document-relative address, with an alt text, and the headline, line and facts as text', () => {
  const summaries = {
    ready: I.summarise(status({ embed: { state: 'ready', model: 'bge-m3' } }), null, false),
    busy: I.summarise(null, null, true),
    asleep: I.summarise(status({ gateway: { mode: 'managed', state: 'unused' } }), null, false),
    trouble: I.summarise(status({ gateway: { mode: 'managed', state: 'failed' }, embed: { state: 'error' } }), null, false),
  }
  for (const lang of LANGS) {
    const t = tr(lang)
    for (const mood of MOODS) {
      const summary = summaries[mood]
      assert.equal(summary.mood, mood)
      const tree = expand(I.Hero({ t, summary }))
      const [img] = findAll(tree, (n) => n.type === 'img')
      assert.equal(img.props.src, `feiyu-memgate/art/mascot-${mood}.webp`)
      assert.ok(!img.props.src.startsWith('/') && !/^[a-z]+:/i.test(img.props.src))
      assert.equal(img.props.alt, t(`mascot.${mood}`))
      assert.ok(img.props.alt && img.props.alt !== `mascot.${mood}`)
      assert.equal(img.props['data-shown'], 'false', 'faded in once it has loaded')
      assert.equal(I.artUrl(mood), img.props.src)
      assert.equal(textOf(withClass(tree, 'fmg-hero__head')), t(summary.headline.key, summary.headline.params))
      assert.equal(textOf(withClass(tree, 'fmg-hero__line')), t(summary.line.key, summary.line.params))
      assert.deepEqual(withClass(tree, 'fmg-fact').map(textOf), plain(summary.facts.map((fact) => t(fact.key, fact.params))))
      assert.equal(withClass(tree, 'fmg-dot')[0].props['data-mood'], mood)
      assert.equal(withRole(tree, 'status').length, 1, 'a change of state is read out')
    }
  }
  assert.equal(withClass(expand(I.Hero({ t: tr('en'), summary: summaries.trouble })), 'fmg-fact--warn').length, 1, 'the add-on error is a warning fact')
  assert.equal(findAll(expand(I.Hero({ t: tr('en'), summary: summaries.busy })), (n) => n.type === 'ul').length, 0, 'no facts, no list')
  for (const mood of MOODS) assert.ok(fs.statSync(new URL(`../client/art/mascot-${mood}.webp`, import.meta.url)).size > 0, mood)
})

test('Hero: when a drawing cannot be loaded the card simply has none, and one that has arrived is shown', () => {
  const summary = I.summarise(status(), null, false)
  const failing = load({ states: (init) => (init && typeof init === 'object' && 'failed' in init ? { loaded: {}, failed: true } : init) })
  const tree = expand(failing.internals.Hero({ t: tr('en'), summary }))
  assert.equal(findAll(tree, (n) => n.type === 'img').length, 0)
  assert.ok(textOf(tree).includes('Memory is running'))
  const arrived = load({ states: (init) => (init && typeof init === 'object' && 'failed' in init ? { loaded: { ready: true }, failed: false } : init) })
  const shown = expand(arrived.internals.Hero({ t: tr('en'), summary }))
  assert.equal(findAll(shown, (n) => n.type === 'img')[0].props['data-shown'], 'true')
})

const SUMMARY_CASES = [
  ['no data while the first read is under way', [null, null, true], 'busy', 'overview.head.loading', 'overview.line.loading'],
  ['no data after a failed read', [null, 'HTTP 503', false], 'trouble', 'overview.head.unknown', 'overview.line.unknown'],
  ['python not found (managed)', [status({ python: { found: false } }), null, false], 'trouble', 'overview.head.trouble', 'overview.trouble.python'],
  ['python not found beats a gateway that is only idle', [status({ gateway: { mode: 'managed', state: 'idle' }, python: { found: false } }), null, false], 'trouble', 'overview.head.trouble', 'overview.trouble.python'],
  ['gateway failed', [status({ gateway: { mode: 'managed', state: 'failed' } }), null, false], 'trouble', 'overview.head.trouble', 'overview.trouble.failed'],
  ['external gateway incomplete', [status({ gateway: { mode: 'external', state: 'incomplete' } }), null, false], 'trouble', 'overview.head.trouble', 'overview.trouble.incomplete'],
  ['gateway off', [status({ gateway: { mode: 'managed', state: 'off' } }), null, false], 'asleep', 'overview.head.asleep', 'overview.asleep.off'],
  ['gateway stopped', [status({ gateway: { mode: 'managed', state: 'stopped' } }), null, false], 'asleep', 'overview.head.asleep', 'overview.asleep.stopped'],
  ['gateway unused (no feature on)', [status({ gateway: { mode: 'managed', state: 'unused' } }), null, false], 'asleep', 'overview.head.asleep', 'overview.asleep.unused'],
  ['gateway idle', [status({ gateway: { mode: 'managed', state: 'idle' } }), null, false], 'busy', 'overview.head.busy', 'overview.busy.gateway'],
  ['gateway starting', [status({ gateway: { mode: 'managed', state: 'starting' } }), null, false], 'busy', 'overview.head.busy', 'overview.busy.gateway'],
  ['gateway restarting', [status({ gateway: { mode: 'managed', state: 'restarting' } }), null, false], 'busy', 'overview.head.busy', 'overview.busy.gateway'],
  ['embedding model loading', [status({ embed: { state: 'loading', model: 'bge-m3' } }), null, false], 'busy', 'overview.head.busy', 'overview.busy.model'],
  ['migration running', [status({ migration: { state: 'running', copied: 420, total: 1284, refused: 0 } }), null, false], 'busy', 'overview.head.busy', 'overview.busy.copy'],
  ['migration partial', [status({ migration: { state: 'partial', copied: 1, total: 2, refused: 0 } }), null, false], 'busy', 'overview.head.busy', 'overview.busy.copy'],
  ['running, keyword', [status(), null, false], 'ready', 'overview.head.ready', 'overview.ready.keyword'],
  ['running, local with hybrid', [status({ retrieval: { setting: 'auto', active: 'local', hybrid: { setting: true, active: true } }, embed: { state: 'ready', model: 'bge-m3' } }), null, false], 'ready', 'overview.head.ready', 'overview.ready.local.hybrid'],
  ['running, local without hybrid', [status({ retrieval: { setting: 'local', active: 'local', hybrid: { setting: false, active: false } }, embed: { state: 'ready', model: 'bge-m3' } }), null, false], 'ready', 'overview.head.ready', 'overview.ready.local'],
  ['running, endpoint', [status({ retrieval: { setting: 'endpoint', active: 'endpoint' } }), null, false], 'ready', 'overview.head.ready', 'overview.ready.endpoint'],
  ['running, endpoint with hybrid', [status({ retrieval: { setting: 'endpoint', active: 'endpoint', hybrid: { setting: true, active: true } } }), null, false], 'ready', 'overview.head.ready', 'overview.ready.endpoint.hybrid'],
  ['running, hybrid switch on but keyword resolved', [status({ retrieval: { setting: 'keyword', active: 'keyword', hybrid: { setting: true, active: true } } }), null, false], 'ready', 'overview.head.ready', 'overview.ready.keyword'],
  ['external configured', [status({ gateway: { mode: 'external', state: 'configured', url: 'http://127.0.0.1:8080' }, python: null, store: null, retrieval: { setting: 'auto', active: null } }), null, false], 'ready', 'overview.head.ready', 'overview.ready.external'],
  ['external gateway needs no python', [status({ gateway: { mode: 'external', state: 'configured' }, python: { found: false } }), null, false], 'ready', 'overview.head.ready', 'overview.ready.external'],
]

test('summarise(): the mood, headline and line for each state a person can meet', () => {
  for (const [name, args, mood, head, line] of SUMMARY_CASES) {
    const summary = plain(I.summarise(...args))
    assert.equal(summary.mood, mood, name)
    assert.equal(summary.headline.key, head, name)
    assert.equal(summary.line.key, line, name)
    assert.ok(MOODS.includes(summary.mood))
  }
  assert.deepEqual(plain(I.summarise(null, 'HTTP 503', false).line.params), { code: 'HTTP 503' })
  assert.deepEqual(plain(I.summarise(null, null, false).line.params), { code: '' })
  assert.deepEqual(plain(I.summarise(status({ embed: { state: 'loading', model: 'bge-m3' } }), null, false).line.params), { model: 'bge-m3' })
  assert.deepEqual(plain(I.summarise(status({ migration: { state: 'running', copied: 420, total: 1284 } }), null, false).line.params), { copied: '420', total: '1,284' })
  assert.deepEqual(plain(I.summarise(status({ retrieval: { active: 'local', hybrid: { active: false } }, embed: { state: 'ready', model: 'bge-m3' } }), null, false).line.params), { model: 'bge-m3' })
  // What is wrong is said before what is on its way, which is said before what is resting.
  assert.equal(I.summarise(status({ gateway: { mode: 'managed', state: 'failed' }, embed: { state: 'loading' } }), null, false).mood, 'trouble')
  assert.equal(I.summarise(status({ gateway: { mode: 'managed', state: 'stopped' }, embed: { state: 'loading' } }), null, false).mood, 'asleep')
  assert.equal(I.summarise(status({ embed: { state: 'loading' }, migration: { state: 'running', copied: 1, total: 2 } }), null, false).line.key, 'overview.busy.model')
})

test('summarise(): the facts: the count with thousands separators, the tier, the gateway and the two warnings', () => {
  const facts = (overrides) => plain(I.summarise(status(overrides), null, false).facts)
  assert.deepEqual(facts({}), [
    { id: 'count', key: 'overview.fact.count', params: { count: '1,284' }, tone: 'plain' },
    { id: 'tier', key: 'overview.fact.tier.keyword', params: {}, tone: 'plain' },
    { id: 'gateway', key: 'overview.fact.managed', params: {}, tone: 'plain' },
  ])
  assert.equal(facts({ store: { count: 1234567 } })[0].params.count, '1,234,567')
  assert.equal(facts({ store: { count: 0 } })[0].params.count, '0', 'an empty store still says so')
  assert.equal(facts({ store: { count: null } })[0].id, 'tier', 'an unknown count is left out')
  assert.equal(facts({ store: null })[0].id, 'tier')
  const hybrid = facts({ retrieval: { active: 'local', hybrid: { active: true } } })
  assert.equal(hybrid.find((f) => f.id === 'tier').key, 'overview.fact.tier.local.hybrid')
  assert.equal(facts({ retrieval: { active: 'endpoint', hybrid: { active: false } } }).find((f) => f.id === 'tier').key, 'overview.fact.tier.endpoint')
  const external = facts({ gateway: { mode: 'external', state: 'configured' } })
  assert.deepEqual(external.map((f) => f.id), ['gateway'])
  assert.equal(external[0].key, 'overview.fact.external')
  const warn = facts({ embed: { state: 'error', model: 'x' }, migration: { state: 'done', copied: 10, total: 10, refused: 3 } }).filter((f) => f.tone === 'warn')
  assert.deepEqual(warn, [
    { id: 'embed', key: 'overview.fact.embedError', params: {}, tone: 'warn' },
    { id: 'refused', key: 'overview.fact.refused', params: { count: '3' }, tone: 'warn' },
  ])
  assert.equal(facts({ migration: { state: 'done', copied: 10, total: 10, refused: 0 } }).filter((f) => f.tone === 'warn').length, 0)
  assert.deepEqual(plain(I.summarise(null, null, true).facts), [])
})

test('formatCount: whole numbers with thousands separators, the same in both languages', () => {
  assert.deepEqual([0, 7, 999, 1000, 1284, 1234567, 12.9, -1284].map(I.formatCount), ['0', '7', '999', '1,000', '1,284', '1,234,567', '12', '-1,284'])
})

test('every key summarise() can return exists in both dictionaries', () => {
  const keys = new Set()
  const note = (summary) => {
    assert.ok(MOODS.includes(summary.mood))
    keys.add(summary.headline.key)
    keys.add(summary.line.key)
    for (const fact of summary.facts) keys.add(fact.key)
  }
  note(I.summarise(null, null, true))
  note(I.summarise(null, 'x', false))
  const gateways = []
  for (const mode of ['managed', 'external']) for (const state of ['idle', 'starting', 'restarting', 'running', 'off', 'stopped', 'unused', 'failed', 'configured', 'incomplete']) gateways.push({ mode, state })
  const retrievals = [{ active: 'keyword' }, { active: 'local' }, { active: 'endpoint' }, { active: null }, undefined]
  const hybrids = [undefined, { active: false }, { active: true }]
  const embeds = [null, { state: 'ready', model: 'm' }, { state: 'loading', model: 'm' }, { state: 'error' }, { state: 'no-model' }]
  const migrations = [null, { state: 'running', copied: 1, total: 2, refused: 1 }, { state: 'partial', copied: 1, total: 2 }, { state: 'done', copied: 2, total: 2, refused: 0 }]
  const pythons = [{ found: true }, { found: false }, null, undefined]
  let count = 0
  for (const gateway of gateways) for (const retrieval of retrievals) for (const hybrid of hybrids) for (const embed of embeds) for (const migration of migrations) for (const python of pythons) {
    note(I.summarise({ gateway, python, retrieval: retrieval && { ...retrieval, hybrid }, store: { count: 5 }, embed, migration }, null, false))
    count += 1
  }
  assert.ok(count > 1000)
  assert.ok(keys.size > 30)
  for (const lang of LANGS) for (const key of keys) assert.ok(DICT[lang][key], `${lang}: ${key}`)
})

// ------------------------------------------------------------------ the overview tab

test('OverviewTab: the rows of statusRows, the Refresh button, and the loading, failure and stale lines', () => {
  const refreshed = []
  const make = (statusProps, lang = 'en') => expand(I.OverviewTab({ t: tr(lang), status: { refresh: () => refreshed.push(1), ...statusProps } }))
  for (const lang of LANGS) {
    const data = status({ embed: { state: 'ready', model: 'bge-m3', dimensions: 1024 }, migration: { state: 'done', copied: 5, total: 5, refused: 0 } })
    const tree = make({ data, loading: false, error: null }, lang)
    const [facts] = findAll(tree, (n) => n.type === 'dl')
    const pairs = []
    for (let i = 0; i < facts.props.children.length; i += 2) pairs.push([textOf(facts.props.children[i]), textOf(facts.props.children[i + 1])])
    assert.deepEqual(pairs, plain(I.statusRows(data, tr(lang))))
    assert.ok(pairs.length >= 6)
    assert.equal(findAll(tree, (n) => n.props.role === 'status').length, 0, 'no line while the answer is fresh')
  }
  const loading = make({ data: null, loading: true, error: null })
  assert.equal(textOf(withRole(loading, 'status')), 'Reading the status…')
  assert.equal(findAll(loading, (n) => n.type === 'dl').length, 0)
  const failed = make({ data: null, loading: false, error: 'HTTP 503' })
  assert.equal(textOf(withRole(failed, 'status')), 'Status unavailable (HTTP 503).')
  assert.equal(textOf(withRole(make({ data: null, loading: false, error: 'network' }, 'zh'), 'status')), '状态不可用（network）。')
  const stale = make({ data: status(), loading: false, error: 'network' })
  assert.equal(textOf(withRole(stale, 'status')), 'Status unavailable (network).')
  assert.equal(findAll(stale, (n) => n.type === 'dl').length, 1, 'a stale answer keeps the rows')
  buttonsNamed(stale, 'Refresh')[0].props.onClick()
  buttonsNamed(loading, 'Refresh')[0].props.onClick()
  assert.equal(refreshed.length, 2)
  assert.ok(textOf(stale).includes(tr('en')('detailsHint')))
  assert.equal(buttonsNamed(make({ data: null, loading: true }, 'zh'), '刷新').length, 1)
})

// ------------------------------------------------------------------ the changelog tab

const entriesFor = (...entries) => ({ version: '0.3.2', entries })
const mdText = (body) => textOf(expand(I.renderMarkdown(body)))
const mdTree = (body) => expand(I.renderMarkdown(body))

test('the page reads the changelog route, document-relative, and shows it on the Changelog tab', () => {
  assert.equal(I.CHANGELOG_ROUTE, CHANGELOG_PATH.slice(1))
  assert.equal(CHANGELOG_PATH, '/feiyu-memgate/changelog')
  const { tree } = renderBody({ tab: 'changelog', form: { state: { status: 'unavailable' } } })
  const panel = withRole(tree, 'tabpanel')[0]
  assert.equal(panel.props.id, 'feiyu-memgate-panel-changelog')
  assert.equal(textOf(panel), 'Reading the changelog…')
  const withData = renderBody({ tab: 'changelog', changelog: entriesFor({ version: '0.3.2', body: 'a' }) }).tree
  assert.equal(withClass(withData, 'fmg-release').length, 1)
})

test('ChangelogTab: the newest release is open, the others closed, each with its version and date; the installed one is tagged', () => {
  const data = entriesFor(
    { version: '0.3.3', title: '0.3.3', body: '日期：2026-11-19。\n\n### 新增\n\n- one' },
    { version: '0.3.2', title: '0.3.2', body: '日期：2026-11-18。\n\ntwo' },
    { version: '0.3.0', title: '0.3.0', body: 'no date here' },
  )
  const view = (lang, state) => expand(I.ChangelogTab({ t: tr(lang), state }))
  const tree = view('en', { loading: false, data })
  const heads = withClass(tree, 'fmg-release__head')
  assert.deepEqual(heads.map((head) => head.props['aria-expanded']), ['true', 'false', 'false'])
  const reveals = withClass(tree, 'fmg-reveal')
  assert.deepEqual(reveals.map((r) => r.props['data-open']), ['true', 'false', 'false'])
  assert.deepEqual(reveals.map((r) => r.props['aria-hidden']), [undefined, 'true', 'true'])
  assert.deepEqual(heads.map((head) => head.props['aria-controls']), reveals.map((r) => r.props.children[0].props.id))
  assert.deepEqual(heads.map((head) => textOf(withClass(head, 'fmg-release__version'))), ['0.3.3', '0.3.2', '0.3.0'])
  assert.deepEqual(heads.map((head) => textOf(withClass(head, 'fmg-release__date'))), ['2026-11-19', '2026-11-18', ''])
  assert.deepEqual(heads.map((head) => withClass(head, 'fmg-tag').map(textOf)), [[], ['Installed'], []], 'the release whose version is the one running')
  assert.deepEqual(heads.map((head) => head.props.type), ['button', 'button', 'button'])
  assert.ok(textOf(withClass(tree, 'fmg-md')[0]).includes('新增'), 'the body is drawn')
  const text = textOf(tree)
  assert.ok(text.includes(tr('en')('changelogIntro')))
  assert.ok(text.includes('The entries are written in Chinese.'), 'English page: one line about the language')
  const zh = textOf(view('zh', { loading: false, data }))
  assert.ok(!zh.includes('用中文写的'), 'Chinese page: no such line')
  assert.ok(zh.includes(tr('zh')('changelogInstalled')))
  // Entries without a text body are left out; with no installed version nothing is tagged.
  const partial = view('en', { loading: false, data: { entries: [{ version: '1', body: 'x' }, { version: '0', body: 5 }, null] } })
  assert.equal(withClass(partial, 'fmg-release').length, 1)
  assert.equal(withClass(partial, 'fmg-tag').length, 0)
})

test('ChangelogTab: a release the person opened or closed keeps that state', () => {
  const data = entriesFor({ version: '0.3.2', body: 'a' }, { version: '0.3.1', body: 'b' })
  const toggled = load({ states: (init) => (init && typeof init === 'object' && Object.keys(init).length === 0 ? { '0.3.2': false, '0.3.1': true } : init) })
  const tree = expand(toggled.internals.ChangelogTab({ t: tr('en'), state: { loading: false, data } }))
  assert.deepEqual(withClass(tree, 'fmg-release__head').map((head) => head.props['aria-expanded']), ['false', 'true'])
  assert.deepEqual(withClass(tree, 'fmg-reveal').map((r) => r.props['data-open']), ['false', 'true'])
})

test('ChangelogTab: loading, a failure and no entries each show one quiet line, with no code or path', () => {
  const line = (lang, state) => expand(I.ChangelogTab({ t: tr(lang), state }))
  for (const [state, key] of [
    [{ loading: true, data: null }, 'changelogLoading'],
    [{ loading: false, data: null }, 'changelogFailed'],
    [{ loading: false, data: { version: '1', entries: [] } }, 'changelogFailed'],
    [{ loading: false, data: { version: '1', entries: 'nope' } }, 'changelogFailed'],
  ]) {
    for (const lang of LANGS) {
      const tree = line(lang, state)
      assert.equal(textOf(tree), tr(lang)(key))
      assert.equal(tree.props.role, 'status')
    }
  }
  assert.equal(textOf(line('en', { loading: true, data: null })), 'Reading the changelog…')
  assert.equal(textOf(line('zh', { loading: false, data: null })), '无法显示更新日志。')
})

// ------------------------------------------------------------------ the Markdown subset

test('the Markdown subset: headings, nested lists, paragraphs, inline code and bold', () => {
  const tree = mdTree('日期：2026-11-18。\n\n### 新增\n\n- 第一项 `advanced.x` 和 **粗体**\n  - 子项一\n  - 子项二\n- 第二项\n  接着写\n\n另一段\n两行')
  assert.deepEqual(plain(tree.map((n) => n.type)), ['p', 'h5', 'ul', 'p'])
  assert.equal(textOf(tree[0]), '日期：2026-11-18。')
  assert.equal(textOf(tree[1]), '新增')
  const items = findAll(tree[2], (n) => n.type === 'li')
  assert.equal(items.length, 4, 'two top items and two nested')
  assert.equal(findAll(tree[2], (n) => n.type === 'ul').length, 2, 'one nesting level')
  assert.deepEqual(findAll(tree[2], (n) => n.type === 'code').map(textOf), ['advanced.x'])
  assert.deepEqual(findAll(tree[2], (n) => n.type === 'strong').map(textOf), ['粗体'])
  assert.ok(textOf(items[3]).includes('第二项接着写'), 'a continuation line joins the item, with no space between Chinese characters')
  assert.equal(textOf(tree[3]), '另一段两行')
  const english = mdTree('first line\nsecond line')
  assert.equal(textOf(english[0]), 'first line second line')
})

test('the Markdown subset: classes instead of inline styles, and HTML from the file is text, never an element', () => {
  const body = '### <img src=x onerror=alert(1)>\n\n<script>alert(1)</script> and <b>bold</b>\n\n- <a href="https://evil.example">x</a> `<i>code</i>` **<u>b</u>** [ok](https://example.org/a)'
  const tree = mdTree(body)
  const types = new Set()
  walk(tree, (node) => types.add(node.type))
  for (const forbidden of ['script', 'img', 'b', 'u', 'i']) assert.ok(!types.has(forbidden), forbidden)
  const text = textOf(tree)
  for (const raw of ['<img src=x onerror=alert(1)>', '<script>alert(1)</script>', '<b>bold</b>', '<a href="https://evil.example">x</a>', '<i>code</i>', '<u>b</u>']) assert.ok(text.includes(raw), raw)
  walk(tree, (node) => {
    assert.equal(node.props.dangerouslySetInnerHTML, undefined)
    assert.equal(node.props.style, undefined, 'no style prop: the stylesheet draws it')
  })
  assert.deepEqual(findAll(tree, (n) => n.type === 'code').map((n) => n.props.className), ['fmg-code'])
  assert.deepEqual(findAll(tree, (n) => n.type === 'a').map((n) => n.props.className), ['fmg-link'], 'only the https link is a link')
  assert.deepEqual(plain(I.inlineMarkdown('a `b` c').map((x) => (typeof x === 'string' ? x : x.props.className))), ['a ', 'fmg-code', ' c'])
})

test('the Markdown subset: only https links are links, with noopener and noreferrer; any other address shows as its text', () => {
  const links = (body) => findAll(mdTree(body), (n) => n.type === 'a')
  const [good] = links('see [the design](https://github.com/N0rmal-114/feiyu-memgate/blob/main/docs/x.md) now')
  assert.equal(good.props.href, 'https://github.com/N0rmal-114/feiyu-memgate/blob/main/docs/x.md')
  assert.equal(good.props.target, '_blank')
  assert.equal(good.props.rel, 'noopener noreferrer')
  assert.equal(good.props.className, 'fmg-link')
  assert.equal(textOf(good), 'the design')
  for (const url of ['javascript:alert(1)', 'JavaScript:alert(1)', 'http://example.org', 'data:text/html,x', '//example.org', 'ftp://x.org', 'docs/design/x.md', '#anchor', 'mailto:someone']) {
    assert.equal(links(`a [click here](${url}) b`).length, 0, url)
    assert.ok(mdText(`a [click here](${url}) b`).split('\n').join('').startsWith('a click here'), url) // the text stays; a stray closing bracket after an address with its own brackets is only text
  }
  assert.equal(links('[HTTPS](HTTPS://example.org/a)').length, 1, 'the scheme is case-insensitive')
})

test('the Markdown subset: odd input never throws', () => {
  for (const odd of [undefined, null, '', '   ', '###', '- ', '[](', '`', '**', '[a](', '- - -\n  - x', '\r\n- a\r\n  - b\r\n', 5, {}]) {
    assert.doesNotThrow(() => mdTree(odd), String(odd))
  }
  assert.deepEqual(plain(mdTree('')), [])
  assert.equal(mdText('- a\r\n- b'), 'a\nb')
})

test('the real CHANGELOG.md of the package renders without any element outside the subset', () => {
  const real = fs.readFileSync(new URL('../CHANGELOG.md', import.meta.url), 'utf8')
  const tree = mdTree(real.split(/^## /m).slice(1).join('\n'))
  const types = new Set()
  walk(tree, (node) => types.add(node.type))
  for (const type of types) assert.ok(['p', 'h5', 'ul', 'li', 'code', 'strong', 'a'].includes(type), type)
  walk(tree, (node) => assert.equal(node.props.style, undefined))
})

// ------------------------------------------------------------------ the model list

const row = (overrides = {}) => ({
  id: 'bge-small-zh-v1.5', tier: 'lightest', tierLabel: { en: 'lightest tier', zh: '最轻档' }, sizeBytes: 47886240, memoryMB: { loaded: 188, peak: 219 },
  languages: ['zh'], license: { name: 'MIT', url: 'https://opensource.org/license/mit' }, requiresAcceptance: null,
  status: 'verified', state: 'absent', percent: null, note: { en: 'Small.', zh: '小。' }, ...overrides,
})
const modelView = (overrides = {}) => ({
  version: 1, addon: { package: 'dsh-feiyu-memgate-embed', state: 'absent' }, installer: { available: true },
  python: { found: true, problem: null }, link: 'https://example.invalid/tiers', busy: false,
  chain: { target: null, phase: 'idle', percent: null, installState: null, error: null }, models: [row()], ...overrides,
})
const renderModelRow = (model, { lang = 'en', view = modelView(), accepted = false, pending = false, onAccept = () => {}, onAction = () => {} } = {}) => (
  expand({ type: I.ModelRow, props: { t: tr(lang), lang, model, view, accepted, pending, onAccept, onAction, children: [] } }))

test('model rows: one button whose label follows the state, plus cancel or delete', () => {
  const t = tr('zh')
  const label = (acts) => t(acts.primary.label[0], acts.primary.label[1])
  const of = (state, extra = {}) => plain(I.modelActions(row({ state, ...extra }), modelView(), false))
  assert.equal(label(of('absent')), '下载并启用')
  assert.equal(of('absent').primary.action, 'install')
  assert.equal(label(of('downloading', { percent: 42 })), '下载中 42%')
  assert.equal(t(of('downloading', { percent: 42 }).secondary.label[0]), '取消')
  assert.equal(of('downloading').secondary.action, 'cancel')
  assert.equal(label(of('installed')), '启用')
  assert.equal(t(of('installed').secondary.label[0]), '删除')
  assert.equal(label(of('active')), '使用中')
  assert.equal(of('active').primary.enabled, false)
  // Another model being set up: nothing else starts meanwhile.
  const busy = modelView({ busy: true, chain: { target: 'bge-m3', phase: 'model', percent: 3, installState: null, error: null } })
  assert.equal(I.modelActions(row(), busy, false).primary.enabled, false)
  // A licence to accept: the button works only once the box is ticked.
  const gated = row({ id: 'licensed-model-test', requiresAcceptance: { name: 'Example Model Licence', url: 'https://example.invalid/licence' } })
  assert.equal(I.modelActions(gated, modelView(), false).primary.enabled, false)
  assert.equal(I.modelActions(gated, modelView(), true).primary.enabled, true)
})

test('every label key a model action or the setup line can name exists in both dictionaries', () => {
  for (const state of ['absent', 'downloading', 'installed', 'active', 'loading', 'paused']) {
    for (const percent of [null, 30]) {
      const acts = I.modelActions(row({ state, percent }), modelView(), true)
      for (const lang of LANGS) {
        for (const label of [acts.primary.label, acts.secondary && acts.secondary.label]) if (label) assert.notEqual(tr(lang)(label[0]), label[0], `${lang} ${label[0]}`)
      }
    }
  }
  for (const phase of ['addon', 'addon-starting', 'paused', 'model', 'activating', 'done']) {
    for (const installState of [null, { phase: 'installing' }, { phase: 'applying' }, { phase: 'cancelling' }]) {
      for (const lang of LANGS) {
        const line = I.chainLine(modelView({ chain: { target: 'bge-m3', phase, percent: 5, installState, error: null } }), tr(lang))
        assert.ok(line && !/^(phase|installState)\./.test(line) && !line.includes('{'), `${lang} ${phase}: ${line}`)
      }
    }
  }
})

test('the chain line says where the setup stands, in the page language', () => {
  const t = tr('en')
  const at = (phase, extra = {}) => I.chainLine(modelView({ chain: { target: 'bge-m3', phase, percent: null, installState: null, error: null, ...extra } }), t)
  assert.equal(at('idle'), null)
  assert.equal(at('addon', { installState: { phase: 'installing' } }), 'Installing the embedding add-on… · The host is installing it')
  assert.equal(at('model', { percent: 37 }), 'Downloading bge-m3: 37%')
  assert.equal(at('activating'), 'Enabling bge-m3…')
  assert.match(at('done'), /^bge-m3 is enabled/)
  assert.equal(I.pickText({ en: 'a', zh: '乙' }, 'zh'), '乙')
  assert.equal(I.formatSize(1157671200), '1.2 GB')
  // One failure reported both by the click's answer and by the chain's stored state is shown once.
  assert.equal(I.sameProblem({ code: 'builds-denied' }, { code: 'builds-denied' }), true)
  assert.equal(I.sameProblem({ code: 'builds-denied' }, { code: 'python-missing' }), false)
  assert.equal(I.sameProblem({ code: 'builds-denied' }, null), false)
  assert.equal(I.sameProblem({}, {}), false)
  assert.equal(I.formatSize(47886240), '47.9 MB')
})

test('a click is a same-origin JSON POST to the relative models route', async () => {
  const calls = []
  const fetchImpl = async (url, init) => {
    calls.push({ url, init })
    return { ok: true, status: 200, json: async () => ({ result: { ok: true, code: 'installing-addon' }, view: modelView() }) }
  }
  const answer = await I.postModels(fetchImpl, { action: 'install', id: 'bge-small-zh-v1.5' })
  assert.equal(answer.result.code, 'installing-addon')
  assert.equal(calls[0].url, 'feiyu-memgate/models')
  assert.equal(I.MODELS_ROUTE, MODELS_PATH.slice(1))
  assert.equal(calls[0].init.method, 'POST')
  assert.equal(calls[0].init.credentials, 'same-origin')
  assert.equal(calls[0].init.headers['content-type'], 'application/json')
  assert.deepEqual(JSON.parse(calls[0].init.body), { action: 'install', id: 'bge-small-zh-v1.5' })
  await assert.rejects(I.postModels(async () => ({ ok: false, status: 403 }), { action: 'cancel' }), (e) => e.code === 'HTTP 403')
})

test('a model row shows size, memory, languages and licence, and a licence box when one is needed', () => {
  const accepts = []
  const licence = { name: 'Example Model Licence', url: 'https://example.invalid/licence' }
  const gated = row({ id: 'licensed-model-test', tier: 'top', tierLabel: { en: 'top tier', zh: '最高档' }, sizeBytes: 612429792, memoryMB: { loaded: 761, peak: 900 }, languages: ['multilingual'], status: 'unverified', requiresAcceptance: licence, license: licence })
  const tree = renderModelRow(gated, { onAccept: (id, value) => accepts.push([id, value]) })
  const text = textOf(tree)
  for (const part of ['licensed-model-test', 'top tier', 'Not yet tested', 'Download 612.4 MB', 'Memory while running about 761 MB', 'Languages Many languages', 'Example Model Licence', 'I have read and accept the Example Model Licence']) {
    assert.ok(text.includes(part), part)
  }
  const [box] = findAll(tree, (n) => n.type === 'input' && n.props.type === 'checkbox')
  assert.equal(box.props.id, 'feiyu-memgate-accept-licensed-model-test')
  assert.equal(box.props.checked, false)
  const [label] = findAll(tree, (n) => n.type === 'label')
  assert.equal(label.props.htmlFor, box.props.id, 'the text of the box is its label')
  box.props.onChange({ target: { checked: true } })
  assert.deepEqual(accepts, [['licensed-model-test', true]])
  assert.equal(buttonsNamed(tree, 'Download and enable')[0].props.disabled, true, 'not before the licence box is ticked')
  assert.equal(buttonsNamed(renderModelRow(gated, { accepted: true }), 'Download and enable')[0].props.disabled, undefined)
  assert.equal(findAll(renderModelRow(row()), (n) => n.type === 'input').length, 0, 'no licence to accept, no box')
  assert.equal(findAll(renderModelRow(row({ state: 'installed', requiresAcceptance: licence })), (n) => n.type === 'input').length, 0, 'only before it is downloaded')
  // Every outside link opens in a new tab without handing the page over.
  const links = findAll(tree, (n) => n.type === 'a')
  assert.equal(links.length, 2, 'the licence name and the licence text of the box')
  for (const link of links) {
    assert.equal(link.props.target, '_blank')
    assert.ok(link.props.rel.includes('noopener') && link.props.rel.includes('noreferrer'), link.props.rel)
    assert.equal(new URL(link.props.href).protocol, 'https:')
  }
})

test('the zh row shows the measured running memory and a translated tier, never the file size or a raw tier word', () => {
  const render = (model) => textOf(renderModelRow(model, { lang: 'zh' }))
  const small = render(row())
  assert.ok(small.includes('运行内存 约 188 MB'), small)
  assert.ok(small.includes('最轻档'))
  assert.ok(!small.includes('lightest'), 'no untranslated tier word')
  assert.ok(!small.includes('约 47.9 MB'), 'memory is not the download size')
  const unmeasured = render(row({ memoryMB: null, tier: 'middle', tierLabel: null }))
  assert.ok(unmeasured.includes('运行内存 未测'))
  assert.ok(!unmeasured.includes('middle'), 'a tier without a label is left out')
  assert.equal(I.memoryText({ loaded: null, peak: 219 }, tr('zh')), '约 219 MB')
})

test('a model row\u2019s primary action is a button only while it can be pressed towards something; other states are said as text', () => {
  const primaryButtons = (tree) => findAll(tree, (n) => n.type === 'button' && n.props.className.includes('fmg-button--primary'))
  const calls = []
  const record = { onAction: (a, m) => calls.push([a, m.id]) }
  for (const [state, text] of [['absent', 'Download and enable'], ['installed', 'Enable'], ['paused', 'Resume download']]) {
    const buttons = primaryButtons(renderModelRow(row({ state })))
    assert.deepEqual(buttons.map(textOf), [text], state)
    assert.equal(buttons[0].props.disabled, undefined)
  }
  assert.equal(primaryButtons(renderModelRow(row({ state: 'installed' }), { pending: true }))[0].props.disabled, true, 'while a click is on its way')
  const busy = modelView({ busy: true, chain: { target: 'bge-m3', phase: 'model', percent: 3, installState: null, error: null } })
  assert.equal(primaryButtons(renderModelRow(row(), { view: busy }))[0].props.disabled, true, 'another model is being set up')
  // Presses reach the page with the action and the model.
  const installed = renderModelRow(row({ state: 'installed' }), record)
  primaryButtons(installed)[0].props.onClick()
  buttonsNamed(installed, 'Delete')[0].props.onClick()
  assert.deepEqual(calls, [['use', 'bge-small-zh-v1.5'], ['remove', 'bge-small-zh-v1.5']])

  // In use: a tag with the text and no button at all.
  const active = renderModelRow(row({ state: 'active' }))
  assert.equal(findAll(active, (n) => n.type === 'button').length, 0)
  assert.equal(withClass(active, 'fmg-tag').filter((tag) => textOf(tag) === 'In use').length, 1)
  assert.ok(withClass(renderModelRow(row({ state: 'active' }), { lang: 'zh' }), 'fmg-tag').some((tag) => textOf(tag) === '使用中'))
  // Loading: text only.
  const loading = renderModelRow(row({ state: 'loading' }))
  assert.equal(findAll(loading, (n) => n.type === 'button').length, 0)
  assert.equal(textOf(withRole(loading, 'status')), 'Loading…')
  // Downloading with a percent: a progress bar, the text, and only Cancel as a button.
  const downloading = renderModelRow(row({ state: 'downloading', percent: 42 }))
  assert.deepEqual(findAll(downloading, (n) => n.type === 'button').map(textOf), ['Cancel'])
  const [bar] = withRole(downloading, 'progressbar')
  assert.deepEqual([bar.props['aria-valuenow'], bar.props['aria-valuemin'], bar.props['aria-valuemax']], [42, 0, 100])
  assert.equal(bar.props['aria-label'], 'Downloading 42%')
  assert.equal(textOf(withRole(downloading, 'status')), 'Downloading 42%')
  const [fill] = findAll(bar, (n) => n.type === 'i')
  assert.deepEqual(plain(fill.props.style), { width: '42%' })
  // Without a percent there is nothing to measure.
  const unknown = renderModelRow(row({ state: 'downloading', percent: null }))
  assert.equal(withRole(unknown, 'progressbar').length, 0)
  assert.equal(textOf(withRole(unknown, 'status')), 'Downloading…')
  // The bar keeps to its range.
  assert.equal(withRole(renderModelRow(row({ state: 'downloading', percent: 250 })), 'progressbar')[0].props['aria-valuenow'], 100)
  assert.equal(withRole(renderModelRow(row({ state: 'downloading', percent: -5 })), 'progressbar')[0].props['aria-valuenow'], 0)
})

// The panel with the hooks' state chosen: the list the host half answered, or the failure of a read.
function renderPanel({ view = null, error = null, lang = 'en' } = {}) {
  const loaded = load({ states: (init) => (init && typeof init === 'object' && 'view' in init && 'error' in init ? { view, error } : init) })
  return expand({ type: loaded.internals.ModelsPanel, props: { t: tr(lang), lang, fetch: async () => ({ ok: false }), children: [] } })
}

test('ModelsPanel: the introduction, the list or why there is none, the setup line and the problems', () => {
  const t = tr('en')
  const loading = renderPanel()
  assert.equal(textOf(withRole(loading, 'status')), t('modelsLoading'))
  assert.ok(textOf(loading).includes(t('modelsIntro')))
  assert.equal(textOf(withRole(renderPanel({ error: 'HTTP 503' }), 'status')), 'The model list is unavailable (HTTP 503).')
  assert.equal(textOf(withRole(renderPanel({ error: 'network', lang: 'zh' }), 'status')), '模型列表不可用（network）。')

  const view = modelView({ models: [row(), row({ id: 'bge-m3', state: 'active', languages: ['multilingual'] })] })
  const tree = renderPanel({ view })
  assert.deepEqual(withClass(tree, 'fmg-model').map((m) => withClass(m, 'fmg-model__name')[0].props.children[0]), ['bge-small-zh-v1.5', 'bge-m3'])
  const [intro] = findAll(tree, (n) => n.type === 'a')
  assert.equal(intro.props.href, 'https://example.invalid/tiers', 'the link the host half gave')
  assert.equal(withRole(tree, 'status').length, 0, 'no setup line while nothing is going on')

  const going = renderPanel({ view: modelView({ busy: true, chain: { target: 'bge-m3', phase: 'model', percent: 37, installState: null, error: null } }) })
  assert.ok(withRole(going, 'status').map(textOf).includes('Downloading bge-m3: 37%'))
  const problem = { code: 'no-space', title: { en: 'There is not enough space.', zh: '空间不够。' }, todo: { en: 'Free some disk space.', zh: '腾出一些磁盘空间。' } }
  const withProblem = renderPanel({ view: modelView({ chain: { target: 'bge-m3', phase: 'idle', percent: null, installState: null, error: problem }, python: { found: false, problem } }) })
  const alerts = withRole(withProblem, 'alert').map(textOf)
  assert.equal(alerts.length, 2)
  assert.ok(alerts.every((a) => a.includes('There is not enough space.') && a.includes('Free some disk space.') && a.includes(t('todo'))))
  assert.ok(textOf(renderPanel({ view: modelView({ python: { found: false, problem } }), lang: 'zh' })).includes('腾出一些磁盘空间。'))
  const builds = renderPanel({ view: modelView({ builds: { note: { en: 'An install script wants to run.', zh: '有安装脚本要运行。' } } }) })
  const [group] = withRole(builds, 'group')
  assert.ok(textOf(group).includes('An install script wants to run.'))
  assert.deepEqual(findAll(group, (n) => n.type === 'button').map(textOf), ['Allow the script and continue', 'Do not allow'])
})

test('the page\u2019s fallback confirmation says the same as the host half\u2019s, CPU only and the Windows runtime included', () => {
  assert.equal(DICT.en.confirmAddon, ADDON_CONFIRM.en)
  assert.equal(DICT.zh.confirmAddon, ADDON_CONFIRM.zh)
})

// ------------------------------------------------------------------ accessibility basics across the trees

// Every tree the page can draw, in a state that shows as much as possible.
function sampleTrees() {
  const trees = []
  const secrets = { 'external.token': true, 'retrieval.apiKey': true }
  const user = { advanced: { recallLimit: 7, afterthought: true }, retrieval: { hybrid: true } }
  const state = formState({ user, value: user })
  const draft = { 'advanced.recallLimit': { value: '99' }, 'modules.autoCapture': { value: true }, 'retrieval.apiKey': { value: 'x' } }
  const modelStates = ['absent', 'downloading', 'installed', 'active', 'loading', 'paused']
  for (const lang of LANGS) {
    for (const tab of PAGE_TABS) {
      trees.push([`${lang} page ${tab}`, renderBody({
        tab, lang, draft, openAll: true, statusData: status({ embed: { state: 'error' }, migration: { state: 'done', copied: 1, total: 1, refused: 2 } }),
        form: { state, mutate: async () => true },
        changelog: entriesFor({ version: '0.3.2', body: '日期：2026-11-18。\n\n- `x` [y](https://example.org)' }, { version: '0.3.1', body: 'z' }),
      }).tree])
    }
    for (const id of SETTINGS_TABS) trees.push([`${lang} tab ${id}`, renderTab(id, { lang, state, draft, secrets })])
    trees.push([`${lang} retrieval tab read-only`, renderTab('retrieval', { lang, state, draft, secrets, disabled: true, readOnly: true })])
    const view = modelView({ models: modelStates.map((s) => row({ id: `m-${s}`, state: s, percent: s === 'downloading' ? 40 : null, requiresAcceptance: s === 'absent' ? { name: 'L', url: 'https://example.invalid/l' } : null })) })
    trees.push([`${lang} model panel`, renderPanel({ view, lang })])
    trees.push([`${lang} save bar`, expand(I.SaveBar({ t: tr(lang), count: 2, invalid: true, phase: 'failed', onSave() {}, onDiscard() {} }))])
  }
  return trees
}

test('accessibility: every button has text content or an aria-label, every image an alt, every role=img and progressbar a name', () => {
  let buttons = 0
  for (const [name, tree] of sampleTrees()) {
    for (const button of findAll(tree, (n) => n.type === 'button')) {
      buttons += 1
      assert.ok(textOf(button).trim() || button.props['aria-label'], `${name}: a button without a name (${button.props.id || button.props.className})`)
    }
    for (const image of findAll(tree, (n) => n.type === 'img')) assert.ok(image.props.alt, `${name}: img without alt`)
    for (const mark of withRole(tree, 'img')) assert.ok(mark.props['aria-label'], `${name}: role=img without a name`)
    for (const bar of withRole(tree, 'progressbar')) assert.ok(bar.props['aria-label'], `${name}: progressbar without a name`)
  }
  assert.ok(buttons > 200)
})

test('accessibility: every input has an id that a label[htmlFor] points at, or an aria-label; switches and radio groups are named', () => {
  let inputs = 0
  for (const [name, tree] of sampleTrees()) {
    const labelled = new Set(findAll(tree, (n) => n.type === 'label').map((n) => n.props.htmlFor))
    for (const input of findAll(tree, (n) => n.type === 'input' || n.type === 'textarea')) {
      inputs += 1
      assert.ok(input.props['aria-label'] || (input.props.id && labelled.has(input.props.id)), `${name}: input ${input.props.id}`)
    }
    // A switch is a button, so it carries its name itself.
    for (const control of withRole(tree, 'switch')) assert.ok(control.props['aria-label'], `${name}: switch ${control.props.id}`)
    // A radio group is named by an element of the row that exists.
    const ids = new Set(findAll(tree, (n) => n.props.id !== undefined).map((n) => n.props.id))
    for (const group of withRole(tree, 'radiogroup')) assert.ok(ids.has(group.props['aria-labelledby']), name)
  }
  assert.ok(inputs > 50)
})

test('no element has a style prop except the inner bar of a progress bar', () => {
  let bars = 0
  for (const [name, tree] of sampleTrees()) {
    const parents = new Map()
    walk(tree, (node) => { for (const child of [node.props.children].flat(Infinity)) if (child && typeof child === 'object') parents.set(child, node) })
    walk(tree, (node) => {
      if (node.props.style === undefined) return
      bars += 1
      assert.equal(node.type, 'i', `${name}: style on <${node.type}>`)
      assert.equal(parents.get(node).props.role, 'progressbar', name)
      assert.deepEqual(Object.keys(node.props.style), ['width'])
    })
  }
  assert.ok(bars > 0, 'the check saw at least one progress bar')
})
