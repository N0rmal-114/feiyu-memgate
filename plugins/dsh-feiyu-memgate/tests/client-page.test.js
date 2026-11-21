// The browser half (client/client.js), offline: it is a plain script in the client module system's
// lazy-CJS form, its field table is current, it registers its dictionaries and the row's settings
// page, and the page's logic (staged edits, one mutation per save, secrets write-only, status rows)
// behaves. React is a small stand-in that expands function components with inert hooks; what only a
// running Desktop can show (styles, the real slot renderer) is not claimed here.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { render } from '../scripts/sync-client.mjs'
import { FIELDS } from '../src/config.js'
import { CHANGELOG_PATH, MODELS_PATH, STATUS_PATH } from '../src/plugin.js'
import { ADDON_CONFIRM } from '../src/setup.js'

const SOURCE = fs.readFileSync(new URL('../client/client.js', import.meta.url), 'utf8')
const pkg = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
const plain = (value) => JSON.parse(JSON.stringify(value)) // values from the script's own realm

function fakeReact() {
  const createElement = (type, props, ...children) => ({ type, props: { ...(props ?? {}), children: children.flat(Infinity) } })
  return {
    createElement,
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useRef: (value) => ({ current: value }),
    useEffect: () => {},
  }
}

function load({ primitives = {} } = {}) {
  let registered = null
  const context = { window: { __ModuleLoader__: { load: (record) => { registered = record } } }, console, setTimeout, clearTimeout, setInterval, clearInterval }
  vm.runInNewContext(SOURCE, context, { filename: 'client.js' })
  const required = []
  const React = fakeReact()
  const exports = registered.factory((id) => {
    required.push(id)
    if (id === 'react') return React
    if (id === '@deepseek-ai/dsh-client-ui-primitives') return primitives
    throw new Error(`not in the shared module table: ${id}`)
  })
  return { registered, exports, required, internals: exports.internals }
}

// Expand function components; keep host elements and text.
function expand(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return null
  if (Array.isArray(node)) return node.map(expand)
  if (typeof node !== 'object') return node
  if (typeof node.type === 'function') return expand(node.type(node.props))
  return { type: node.type, props: { ...node.props, children: node.props.children.map(expand) } }
}
const walk = (node, visit) => {
  if (Array.isArray(node)) return node.forEach((n) => walk(n, visit))
  if (node && typeof node === 'object') {
    visit(node)
    walk(node.props.children, visit)
  }
}
const textOf = (tree) => {
  const out = []
  const collect = (node) => {
    if (Array.isArray(node)) node.forEach(collect)
    else if (node && typeof node === 'object') collect(node.props.children)
    else if (node !== null && node !== undefined) out.push(String(node))
  }
  collect(tree)
  return out.join('\n')
}
const findAll = (tree, test) => {
  const out = []
  walk(tree, (node) => { if (test(node)) out.push(node) })
  return out
}

function translator(dict) {
  return (key, params = {}) => {
    const template = dict[key]
    if (template === undefined) return key
    return template.replace(/\{(\w+)\}/g, (match, name) => (name in params ? String(params[name]) : match))
  }
}

const formState = (overrides = {}) => ({
  status: 'ready',
  value: { modules: { autoRecall: true }, advanced: { recallLimit: 5 }, external: { url: '' } },
  base: {},
  user: { advanced: { recallLimit: 5 } },
  revision: 7,
  writable: true,
  ...overrides,
})

test('a plain script in the lazy-CJS form: registers the package name, asks only for shared modules', () => {
  assert.match(SOURCE, /window\.__ModuleLoader__\.load\(\{ id: 'dsh-feiyu-memgate', factory: \(require\) => \{/)
  assert.ok(!/^\s*(import|export)\s/m.test(SOURCE), 'no module syntax: the page runs it as a script')
  const { registered, exports, required } = load()
  assert.equal(registered.id, pkg.name)
  assert.deepEqual(required, ['react', '@deepseek-ai/dsh-client-ui-primitives'])
  assert.equal(exports.name, 'dsh-feiyu-memgate')
  assert.deepEqual(plain(exports.inject), ['slots', 'locale'])
  assert.equal(typeof exports.apply, 'function')
  assert.deepEqual([...SOURCE.matchAll(/require\('([^']+)'\)/g)].map((m) => m[1]), ['react', '@deepseek-ai/dsh-client-ui-primitives'])
})

test('the field table in the script is the one in src/config.js (npm run sync-client)', () => {
  assert.equal(render(SOURCE), SOURCE, 'client/client.js is stale: run npm run sync-client')
  assert.deepEqual(plain(load().internals.FIELDS), plain(FIELDS))
})

test('the page script carries advanced.linkedRecall right after skipRepeats: a switch that is on, with both languages', () => {
  const advanced = plain(load().internals.FIELDS).find((g) => g.group === 'advanced').fields
  const keys = advanced.map((f) => f.key)
  assert.equal(keys[keys.indexOf('skipRepeats') + 1], 'linkedRecall')
  const row = advanced.find((f) => f.key === 'linkedRecall')
  assert.deepEqual([row.kind, row.default, row.label.en, row.label.zh], ['boolean', true, 'Recall linked memories', '顺带想起相连的记忆'])
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

// ------------------------------------------------------------------ the changelog section

const changelogT = (lang) => translator(plain(load().internals.dictionaries())[lang])
const entriesFor = (...entries) => ({ version: '0.3.2', entries })
const mdText = (body) => textOf(expand(load().internals.renderMarkdown(body)))
const mdTree = (body) => expand(load().internals.renderMarkdown(body))

test('the changelog section reads the host route, document-relative, and sits below the rest of the page', () => {
  const { internals } = load()
  assert.equal(internals.CHANGELOG_ROUTE, CHANGELOG_PATH.slice(1))
  assert.equal(CHANGELOG_PATH, '/feiyu-memgate/changelog')
  const tree = expand(internals.SettingsBody({ t: changelogT('en'), form: { state: { status: 'unavailable' } }, fetch: async () => ({ ok: false, status: 503 }) }))
  const sections = findAll(tree, (n) => n.type === 'section')
  assert.equal(sections.at(-1).props['aria-label'], 'Changelog', 'the last section of the page')
  assert.equal(textOf(sections.at(-1)).split('\n')[0], 'Changelog')
})

test('fetchChangelog asks the route with the same credentials as the status and fails with the HTTP code', async () => {
  const { internals } = load()
  const calls = []
  const data = await internals.fetchChangelog(async (url, options) => { calls.push([url, options]); return { ok: true, json: async () => ({ version: '1', entries: [] }) } })
  assert.deepEqual(plain(data), { version: '1', entries: [] })
  assert.deepEqual(plain(calls), [['feiyu-memgate/changelog', { headers: { accept: 'application/json' }, credentials: 'same-origin' }]])
  await assert.rejects(internals.fetchChangelog(async () => ({ ok: false, status: 403 })), (error) => error.code === 'HTTP 403')
})

test('the changelog section: the newest entry is open, the others closed, each titled with its version and date', () => {
  const { internals } = load()
  const view = (lang, state) => expand(internals.ChangelogView({ t: changelogT(lang), state }))
  const data = entriesFor(
    { version: '0.3.2', title: '0.3.2', body: '日期：2026-11-18。\n\n### 新增\n\n- one' },
    { version: '0.3.1', title: '0.3.1', body: '日期：2026-11-17。\n\ntwo' },
    { version: '0.3.0', title: '0.3.0', body: 'no date here' },
  )
  const tree = view('en', { loading: false, data })
  const details = findAll(tree, (n) => n.type === 'details')
  assert.deepEqual(details.map((d) => d.props.open), [true, false, false])
  const summaries = findAll(tree, (n) => n.type === 'summary').map((n) => textOf(n))
  assert.deepEqual(summaries, ['0.3.2 · 2026-11-18', '0.3.1 · 2026-11-17', '0.3.0'])
  const text = textOf(tree)
  assert.ok(text.includes('Installed version 0.3.2.'))
  assert.ok(text.includes('The entries are written in Chinese.'), 'English page: one line about the language')
  assert.ok(!textOf(view('zh', { loading: false, data })).includes('用中文写的'), 'Chinese page: no such line')
  assert.ok(textOf(view('zh', { loading: false, data })).includes('当前版本 0.3.2。'))
  assert.equal(findAll(tree, (n) => n.type === 'section')[0].props['aria-label'], 'Changelog')
})

test('the changelog section: loading, a failure and no entries each show one quiet line, with no code or path', () => {
  const { internals } = load()
  const line = (lang, state) => textOf(expand(internals.ChangelogView({ t: changelogT(lang), state }))).split('\n').slice(1).join('\n')
  assert.equal(line('en', { loading: true, data: null }), 'Reading the changelog…')
  assert.equal(line('en', { loading: false, data: null }), 'The changelog could not be shown.')
  assert.equal(line('en', { loading: false, data: { version: '1', entries: [] } }), 'The changelog could not be shown.')
  assert.equal(line('en', { loading: false, data: { version: '1', entries: 'nope' } }), 'The changelog could not be shown.')
  assert.equal(line('zh', { loading: false, data: null }), '更新日志现在无法显示。')
  assert.equal(line('zh', { loading: true, data: null }), '正在读取更新日志…')
})

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

test('the Markdown subset: HTML from the file is text, never an element', () => {
  const body = '### <img src=x onerror=alert(1)>\n\n<script>alert(1)</script> and <b>bold</b>\n\n- <a href="https://evil.example">x</a> `<i>code</i>` **<u>b</u>**'
  const tree = mdTree(body)
  const types = new Set()
  walk(tree, (node) => types.add(node.type))
  for (const forbidden of ['script', 'img', 'b', 'u', 'i', 'a']) assert.ok(!types.has(forbidden), forbidden)
  const text = textOf(tree)
  for (const raw of ['<img src=x onerror=alert(1)>', '<script>alert(1)</script>', '<b>bold</b>', '<a href="https://evil.example">x</a>', '<i>code</i>', '<u>b</u>']) assert.ok(text.includes(raw), raw)
  walk(tree, (node) => { assert.equal(node.props.dangerouslySetInnerHTML, undefined) })
})

test('the Markdown subset: only https links are links, with noopener and noreferrer; any other address shows as its text', () => {
  const links = (body) => findAll(mdTree(body), (n) => n.type === 'a')
  const [good] = links('see [the design](https://github.com/N0rmal-114/feiyu-memgate/blob/main/docs/x.md) now')
  assert.equal(good.props.href, 'https://github.com/N0rmal-114/feiyu-memgate/blob/main/docs/x.md')
  assert.equal(good.props.target, '_blank')
  assert.equal(good.props.rel, 'noopener noreferrer')
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
})

test('apply registers both dictionaries and the page under <package name>#<row id>', () => {
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
  exports.apply(ctx)
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
})

test('every key has copy in both languages, and every setting has a label, help and option names', () => {
  const { internals } = load()
  const { en, zh } = plain(internals.dictionaries())
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

test('staged edits become one ordered set of operations; secrets are write-only', () => {
  const { internals } = load()
  const field = (k) => FIELDS.find((g) => g.group === k.split('.')[0]).fields.find((f) => f.key === k.split('.')[1])
  const state = formState()
  const view = (k, draft = {}, secrets) => plain(internals.fieldView(field(k), k.split('.')[0], state, draft, secrets))
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

  const ops = plain(internals.buildOps({
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
  assert.deepEqual(plain(internals.buildOps({ 'external.token': { value: 'fake-new-token' } })), [{ op: 'set', path: ['external', 'token'], value: 'fake-new-token' }])
})

test('save sends every staged edit in one mutation fenced by the revision read', async () => {
  const { internals } = load()
  const calls = []
  const form = { state: formState(), mutate: async (ops, revision) => { calls.push({ ops: plain(ops), revision }); return true } }
  assert.equal(await internals.saveDraft(form, { 'modules.autoRecall': { value: false }, 'advanced.profile': { value: 'broad' } }), true)
  assert.deepEqual(calls, [{ ops: [{ op: 'set', path: ['modules', 'autoRecall'], value: false }, { op: 'set', path: ['advanced', 'profile'], value: 'broad' }], revision: 7 }])
  assert.equal(await internals.saveDraft(form, { 'external.token': { value: '' } }), true)
  assert.equal(calls.length, 1, 'nothing to write, nothing sent')
  const refused = { state: formState(), mutate: async () => false }
  assert.equal(await internals.saveDraft(refused, { 'modules.autoRecall': { value: false } }), false)
})

test('status rows: managed and external, in the page language', async () => {
  const { internals } = load()
  const dicts = plain(internals.dictionaries())
  const managed = {
    gateway: { mode: 'managed', state: 'running' },
    python: { found: true, version: '3.12.4', source: 'path', executable: 'python-for-tests' },
    retrieval: { setting: 'auto', active: 'local' },
    store: { type: 'embedding', file: 'memories-local-x.db', count: 3 },
    embed: { state: 'ready', model: 'bge-small-zh-v1.5', dimensions: 512, parity: 'approximate' },
    migration: { state: 'running', copied: 1, total: 3 },
  }
  assert.deepEqual(plain(internals.statusRows(managed, translator(dicts.en))), [
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
  assert.deepEqual(plain(internals.statusRows(hybrid, translator(dicts.en))).slice(2, 4), [
    ['Retrieval', 'auto → local + keyword (hybrid)'],
    ['Store', 'hybrid · memories-local-x.db + memories-local-x-keywords.db · 3 memories'],
  ])
  assert.deepEqual(plain(internals.statusRows(hybrid, translator(dicts.zh))).slice(2, 4), [
    ['检索', 'auto → local + 关键词（混合）'],
    ['存储', 'hybrid · memories-local-x.db + memories-local-x-keywords.db · 3 条记忆'],
  ])
  // The switch is on but the resolved store is not hybrid (keyword): the rows stay as they were.
  const idle = { ...hybrid, retrieval: { setting: 'keyword', active: 'keyword', hybrid: { setting: true, active: false } }, store: { type: 'sqlite', file: 'memories.db', count: 1 } }
  assert.deepEqual(plain(internals.statusRows(idle, translator(dicts.en))).slice(2, 4), [['Retrieval', 'keyword → keyword'], ['Store', 'sqlite · memories.db · 1 memories']])
  const external = plain(internals.statusRows({ gateway: { mode: 'external', state: 'configured', url: 'http://127.0.0.1:8080' }, embed: null }, translator(dicts.zh)))
  assert.deepEqual(external, [['网关', '外部 · 已配置 · http://127.0.0.1:8080'], ['检索', '由外部网关决定'], ['向量插件', '未安装']])
  assert.deepEqual(plain(internals.statusRows({ gateway: { mode: 'managed', state: 'brand-new' }, python: { found: false }, retrieval: { setting: 'keyword', active: 'keyword' } }, translator(dicts.en)))[0], ['Gateway', 'managed · brand-new'])

  const fetched = []
  const ok = await internals.fetchStatus(async (url, init) => {
    fetched.push({ url, init: plain(init) })
    return { ok: true, json: async () => ({ gateway: { mode: 'managed' } }) }
  })
  assert.deepEqual(plain(ok), { gateway: { mode: 'managed' } })
  assert.deepEqual(fetched, [{ url: 'feiyu-memgate/status', init: { headers: { accept: 'application/json' }, credentials: 'same-origin' } }])
  await assert.rejects(internals.fetchStatus(async () => ({ ok: false, status: 403 })), (error) => error.code === 'HTTP 403')
})

test('the page renders every group and field with plain elements when the host has no primitives', () => {
  const { internals } = load()
  const t = translator(plain(internals.dictionaries()).en)
  assert.equal(internals.SettingsPage({ view: 'summary', translate: t }), plain(internals.dictionaries()).en.summary)
  const tree = expand(internals.SettingsPage({ view: 'page', t, form: { state: formState(), mutate: async () => true } }))
  const text = textOf(tree)
  for (const group of FIELDS) {
    assert.ok(text.includes(group.label.en), group.group)
    for (const field of group.fields) assert.ok(text.includes(field.label.en), field.key)
  }
  assert.ok(text.includes('Reading the status'), 'the status block is first, loading')
  const passwords = findAll(tree, (n) => n.type === 'input' && n.props.type === 'password')
  assert.equal(passwords.length, 2, 'both secrets are password inputs')
  for (const input of passwords) assert.equal(input.props.value, '', 'a secret never shows a value')
  const selects = findAll(tree, (n) => n.type === 'select')
  assert.deepEqual(selects.map((s) => s.props.id), ['feiyu-memgate-retrieval-tier', 'feiyu-memgate-gateway-mode', 'feiyu-memgate-advanced-language', 'feiyu-memgate-advanced-profile'])
  const save = findAll(tree, (n) => n.type === 'button' && textOf(n) === 'Save')
  assert.equal(save.length, 1)
  assert.equal(save[0].props.disabled, true, 'nothing staged, nothing to save')
  assert.ok(text.includes('Changed'), 'the overridden field shows its tag')

  assert.ok(text.includes('Local embedding models'), 'the model list is on the page')
  // Without a usable form the page says so, and still offers the model list.
  const unavailable = textOf(expand(internals.SettingsPage({ view: 'page', t, form: { state: formState({ status: 'unavailable' }) } })))
  assert.ok(unavailable.startsWith('The settings of this plugin are not available right now.'))
  assert.ok(unavailable.includes('Local embedding models'))
  assert.ok(textOf(expand(internals.SettingsPage({ view: 'page', t }))).startsWith('The settings of this plugin are not available right now.'))
  assert.ok(textOf(expand(internals.SettingsPage({ view: 'page', t, form: { state: formState({ status: 'loading' }) } }))).startsWith('Loading the settings…'))
  const readOnly = textOf(expand(internals.SettingsPage({ view: 'page', t, form: { state: formState({ writable: false }) } })))
  assert.ok(readOnly.includes('Settings are read-only in this deployment.'))
})

test('the page uses the host primitives when they exist', () => {
  const seen = {}
  const marker = (name) => (props) => {
    ;(seen[name] ??= []).push(props)
    return { type: `primitive:${name}`, props: { children: props.children === undefined ? [] : [props.children].flat(Infinity) } }
  }
  const primitives = Object.fromEntries(['SettingsForm', 'SettingsValueField', 'SettingsSecretField', 'Switch', 'Tag', 'Button'].map((n) => [n, marker(n)]))
  const { internals } = load({ primitives })
  const t = translator(plain(internals.dictionaries()).en)
  expand(internals.SettingsPage({ view: 'page', t, form: { state: formState(), mutate: async () => true } }))
  assert.equal(seen.SettingsForm.length, 1)
  assert.deepEqual(plain(seen.SettingsForm[0].state), { available: true, writable: true, dirty: false, invalid: false, saving: false, failed: false })
  assert.deepEqual(Object.keys(seen.SettingsForm[0].labels).sort(), ['readOnly', 'save', 'saveFailed', 'saving', 'unavailable'])
  const switches = FIELDS.flatMap((g) => g.fields).filter((f) => f.kind === 'boolean').length
  assert.equal(seen.Switch.length, switches)
  assert.equal(seen.SettingsSecretField.length, 2)
  for (const props of seen.SettingsSecretField) {
    assert.equal(props.text, '')
    assert.equal(props.configured, false, 'unknown counts as not configured for the host control')
  }
  const valueFields = FIELDS.flatMap((g) => g.fields).filter((f) => !f.secret && (f.kind === 'integer' || f.kind === 'string')).length
  assert.equal(seen.SettingsValueField.length, valueFields)
  const limit = seen.SettingsValueField.find((p) => p.id === 'feiyu-memgate-advanced-recallLimit')
  assert.deepEqual([limit.text, limit.overridden, limit.numeric, limit.invalidLabel], ['5', true, true, 'A whole number from 1 to 10.'])
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

test('model rows: one button whose label follows the state, plus cancel or delete', () => {
  const { internals } = load()
  const t = translator(plain(internals.dictionaries()).zh)
  const label = (acts) => t(acts.primary.label[0], acts.primary.label[1])
  const of = (state, extra = {}) => plain(internals.modelActions(row({ state, ...extra }), modelView(), false))
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
  assert.equal(internals.modelActions(row(), busy, false).primary.enabled, false)
  // A licence to accept: the button works only once the box is ticked.
  const gated = row({ id: 'licensed-model-test', requiresAcceptance: { name: 'Example Model Licence', url: 'https://example.invalid/licence' } })
  assert.equal(internals.modelActions(gated, modelView(), false).primary.enabled, false)
  assert.equal(internals.modelActions(gated, modelView(), true).primary.enabled, true)
})

test('the chain line says where the setup stands, in the page language', () => {
  const { internals } = load()
  const t = translator(plain(internals.dictionaries()).en)
  const at = (phase, extra = {}) => internals.chainLine(modelView({ chain: { target: 'bge-m3', phase, percent: null, installState: null, error: null, ...extra } }), t)
  assert.equal(at('idle'), null)
  assert.equal(at('addon', { installState: { phase: 'installing' } }), 'Installing the embedding add-on… · The host is installing it')
  assert.equal(at('model', { percent: 37 }), 'Downloading bge-m3: 37%')
  assert.equal(at('activating'), 'Enabling bge-m3…')
  assert.match(at('done'), /^bge-m3 is in use/)
  assert.equal(internals.pickText({ en: 'a', zh: '乙' }, 'zh'), '乙')
  assert.equal(internals.formatSize(1157671200), '1.2 GB')
  // One failure reported both by the click's answer and by the chain's stored state is shown once.
  assert.equal(internals.sameProblem({ code: 'builds-denied' }, { code: 'builds-denied' }), true)
  assert.equal(internals.sameProblem({ code: 'builds-denied' }, { code: 'python-missing' }), false)
  assert.equal(internals.sameProblem({ code: 'builds-denied' }, null), false)
  assert.equal(internals.sameProblem({}, {}), false)
  assert.equal(internals.formatSize(47886240), '47.9 MB')
})

test('a click is a same-origin JSON POST to the relative models route', async () => {
  const { internals } = load()
  const calls = []
  const fetchImpl = async (url, init) => {
    calls.push({ url, init })
    return { ok: true, status: 200, json: async () => ({ result: { ok: true, code: 'installing-addon' }, view: modelView() }) }
  }
  const answer = await internals.postModels(fetchImpl, { action: 'install', id: 'bge-small-zh-v1.5' })
  assert.equal(answer.result.code, 'installing-addon')
  assert.equal(calls[0].url, 'feiyu-memgate/models')
  assert.equal(internals.MODELS_ROUTE, MODELS_PATH.slice(1))
  assert.equal(calls[0].init.method, 'POST')
  assert.equal(calls[0].init.credentials, 'same-origin')
  assert.equal(calls[0].init.headers['content-type'], 'application/json')
  assert.deepEqual(JSON.parse(calls[0].init.body), { action: 'install', id: 'bge-small-zh-v1.5' })
  await assert.rejects(internals.postModels(async () => ({ ok: false, status: 403 }), { action: 'cancel' }), (e) => e.code === 'HTTP 403')
})

test('a model row shows size, memory, languages and licence, and a licence box when one is needed', () => {
  const { internals } = load()
  const t = translator(plain(internals.dictionaries()).en)
  const actions = []
  const licence = { name: 'Example Model Licence', url: 'https://example.invalid/licence' }
  const gated = row({ id: 'licensed-model-test', tier: 'top', tierLabel: { en: 'top tier', zh: '最高档' }, sizeBytes: 612429792, memoryMB: { loaded: 761, peak: 900 }, languages: ['multilingual'], status: 'unverified', requiresAcceptance: licence, license: licence })
  const tree = expand({ type: internals.ModelRowForTests, props: { t, lang: 'en', model: gated, view: modelView(), accepted: false, pending: false, onAccept() {}, onAction: (a) => actions.push(a), children: [] } })
  const text = textOf(tree)
  for (const part of ['licensed-model-test', 'top tier', 'Not yet tested', 'Download 612.4 MB', 'Memory while running about 761 MB', 'Languages Many languages', 'Example Model Licence', 'I have read and accept the Example Model Licence']) {
    assert.ok(text.includes(part), part)
  }
  const box = findAll(tree, (n) => n.type === 'input' && n.props.type === 'checkbox')
  assert.equal(box.length, 1)
  const button = findAll(tree, (n) => n.type === 'button' && textOf(n) === 'Download and enable')
  assert.equal(button[0].props.disabled, true, 'not before the licence box is ticked')
})

test('the zh row shows the measured running memory and a translated tier, never the file size or a raw tier word', () => {
  const { internals } = load()
  const t = translator(plain(internals.dictionaries()).zh)
  const render = (model) => textOf(expand({ type: internals.ModelRowForTests, props: { t, lang: 'zh', model, view: modelView(), accepted: false, pending: false, onAccept() {}, onAction() {}, children: [] } }))
  const small = render(row())
  assert.ok(small.includes('运行内存 约 188 MB'), small)
  assert.ok(small.includes('最轻档'))
  assert.ok(!small.includes('lightest'), 'no untranslated tier word')
  assert.ok(!small.includes('约 47.9 MB'), 'memory is not the download size')
  const unmeasured = render(row({ memoryMB: null, tier: 'middle', tierLabel: null }))
  assert.ok(unmeasured.includes('运行内存 未测'))
  assert.ok(!unmeasured.includes('middle'), 'a tier without a label is left out')
  assert.equal(internals.memoryText({ loaded: null, peak: 219 }, t), '约 219 MB')
})

test('the page’s fallback confirmation says the same as the host half’s, CPU only and the Windows runtime included', () => {
  const { internals } = load()
  const dict = plain(internals.dictionaries())
  assert.equal(dict.en.confirmAddon, ADDON_CONFIRM.en)
  assert.equal(dict.zh.confirmAddon, ADDON_CONFIRM.zh)
})
