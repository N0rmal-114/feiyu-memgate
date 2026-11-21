// The modules of the settings page (client/src), loaded one by one in Node without React, without the
// bundle and without a host: the pure ones must work on their own. Also the rules of client/README.md,
// checked by reading the sources: header comments, imports and layers, the layout table, the stylesheet
// against the class names the modules use, and the dictionary against the keys they ask for.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { FIELDS } from '../src/config.js'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'client', 'src')
const req = createRequire(path.join(SRC, 'index.cjs'))
const load = (id) => req('./' + id)
const plain = (value) => JSON.parse(JSON.stringify(value))

const translate = (dict) => (key, params = {}) => {
  const template = dict[key]
  return template === undefined ? key : template.replace(/\{(\w+)\}/g, (match, name) => (name in params ? String(params[name]) : match))
}

const ALL = FIELDS.flatMap((group) => group.fields.map((field) => ({ ...field, group: group.group, k: `${group.group}.${field.key}` })))

test('constants: the routes, names and addresses the page and the host half agree on', () => {
  const c = plain(load('constants.cjs'))
  const { LINKS, ...rest } = c
  assert.deepEqual(rest, {
    NS: 'feiyuMemgate',
    ROW_KEY: 'dsh-feiyu-memgate#feiyu-memgate',
    STATUS_ROUTE: 'feiyu-memgate/status',
    STATUS_POLL_MS: 10000,
    CHANGELOG_ROUTE: 'feiyu-memgate/changelog',
    MODELS_ROUTE: 'feiyu-memgate/models',
    MODELS_POLL_MS: 10000,
    MODELS_BUSY_POLL_MS: 1500,
    ART_ROUTE: 'feiyu-memgate/art/',
    SAVED_NOTE_MS: 2600,
    FEEDBACK_ENDPOINT: c.FEEDBACK_ENDPOINT,
    FEEDBACK_TIMEOUT_MS: 15000,
    ID_PREFIX: 'feiyu-memgate',
  })
  assert.deepEqual(Object.keys(LINKS), ['docs', 'source', 'issues', 'newIssue'])
  assert.equal(LINKS.newIssue, LINKS.issues + '/new', 'a new issue of the same repository')
  // The one address outside the host the page may call: none, or an https address of the receiver.
  assert.ok(c.FEEDBACK_ENDPOINT === '' || /^https:\/\/[a-z0-9.-]+(\/[\w./-]*)?$/.test(c.FEEDBACK_ENDPOINT), c.FEEDBACK_ENDPOINT)
  for (const href of Object.values(LINKS)) assert.equal(new URL(href).protocol, 'https:')
  for (const route of [c.STATUS_ROUTE, c.CHANGELOG_ROUTE, c.MODELS_ROUTE, c.ART_ROUTE]) assert.ok(!route.startsWith('/'), `${route} is document-relative`)
})

test('fields: the generated table is the one in src/config.js and INDEX names every field once', () => {
  const { FIELDS: copy, INDEX } = load('fields.cjs')
  assert.deepEqual(plain(copy), plain(FIELDS))
  const names = FIELDS.flatMap((group) => group.fields.map((field) => `${group.group}.${field.key}`))
  assert.deepEqual(Object.keys(INDEX), names)
  assert.equal(INDEX['advanced.recallLimit'].group, 'advanced')
  assert.equal(INDEX['external.url'].key, 'url')
  assert.equal(INDEX['external.url'].group, 'external')
  assert.deepEqual(plain(INDEX['advanced.afterthoughtMax'].needs), { key: 'advanced.afterthought', in: [true] })
})

test('i18n: both languages carry the same static keys, and the dictionaries add every group, field and option', () => {
  const { UI, dictionaries } = load('i18n.cjs')
  assert.deepEqual(Object.keys(UI.en).sort(), Object.keys(UI.zh).sort())
  const { en, zh } = dictionaries()
  assert.equal(en.saveChanges, 'Save changes')
  assert.equal(zh.saveChanges, '保存修改')
  assert.equal(en['g.retrieval'], 'Retrieval')
  assert.equal(zh['f.gateway.mode'], '网关模式')
  assert.equal(en['o.retrieval.tier.keyword'], FIELDS[1].fields[0].options[1].label.en)
  assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort())
  assert.equal(UI.en.saveChanges, 'Save changes', 'the static dictionary is copied, not changed')
})

test('net: one shared pair of helpers behind the four calls; a refusal is an error with .code', async () => {
  const net = load('net.cjs')
  const calls = []
  const fetchOk = async (route, init) => {
    calls.push([route, init])
    return { ok: true, status: 200, json: async () => ({ route }) }
  }
  assert.deepEqual(await net.fetchStatus(fetchOk), { route: 'feiyu-memgate/status' })
  assert.deepEqual(await net.fetchChangelog(fetchOk), { route: 'feiyu-memgate/changelog' })
  assert.deepEqual(await net.fetchModels(fetchOk), { route: 'feiyu-memgate/models' })
  assert.deepEqual(plain(calls[0][1]), { headers: { accept: 'application/json' }, credentials: 'same-origin' })
  await net.postModels(fetchOk, { action: 'cancel' })
  const [route, init] = calls[3]
  assert.equal(route, 'feiyu-memgate/models')
  assert.deepEqual(plain(init), {
    method: 'POST',
    headers: { accept: 'application/json', 'content-type': 'application/json' },
    credentials: 'same-origin',
    body: '{"action":"cancel"}',
  })
  const refuse = (status) => async () => ({ ok: false, status, json: async () => ({}) })
  for (const [call, name] of [[net.fetchStatus, 'status'], [net.fetchChangelog, 'changelog'], [net.fetchModels, 'models'], [(f) => net.postModels(f, {}), 'models']]) {
    await assert.rejects(() => call(refuse(403)), (error) => error.code === 'HTTP 403' && error.message === name)
  }
  await assert.rejects(() => net.getJson(refuse(500), 'x/y', 'label'), (error) => error.code === 'HTTP 500' && error.message === 'label')
  await assert.rejects(() => net.postJson(refuse(404), 'x/y', 'label', {}), (error) => error.code === 'HTTP 404')
  const down = async () => { throw new TypeError('offline') }
  await assert.rejects(() => net.fetchStatus(down), /offline/)
})

test('model/form: field views, staged edits and the save operations', async () => {
  const form = load('model/form.cjs')
  const { INDEX } = load('fields.cjs')
  assert.equal(form.at({ a: { b: 1 } }, ['a', 'b']), 1)
  assert.equal(form.at({ a: {} }, ['a', 'b']), undefined)
  assert.equal(form.at({}, ['toString']), undefined, 'own properties only')
  assert.equal(form.checkText(INDEX['advanced.recallLimit'], '5'), true)
  assert.equal(form.checkText(INDEX['advanced.recallLimit'], '11'), false)
  assert.equal(form.checkText(INDEX['advanced.recallLimit'], 'x'), false)
  assert.equal(form.checkText(INDEX['external.url'], 'ftp://x'), false)
  assert.equal(form.checkText(INDEX['external.url'], ''), true)
  const state = { value: { advanced: { recallLimit: 7 } }, base: {}, user: { advanced: { recallLimit: 7 } } }
  const limit = form.fieldView(INDEX['advanced.recallLimit'], 'advanced', state, {}, null)
  assert.deepEqual([limit.text, limit.overridden, limit.invalid], ['7', true, false])
  const staged = form.fieldView(INDEX['advanced.recallLimit'], 'advanced', state, { 'advanced.recallLimit': { value: '99' } }, null)
  assert.deepEqual([staged.text, staged.invalid], ['99', true])
  const secret = form.fieldView(INDEX['external.token'], 'external', state, {}, { 'external.token': true })
  assert.deepEqual([secret.secret, secret.configured, secret.text], [true, true, ''])
  assert.deepEqual(plain(form.buildOps({
    'advanced.recallLimit': { value: ' 8 ' },
    'external.url': { unset: true },
    'external.token': { value: '' },
    'modules.autoCapture': { value: true },
  })), [
    { op: 'set', path: ['modules', 'autoCapture'], value: true },
    { op: 'unset', path: ['external', 'url'] },
    { op: 'set', path: ['advanced', 'recallLimit'], value: 8 },
  ])
  const sent = []
  const mutate = async (ops, revision) => { sent.push([ops.length, revision]); return true }
  assert.equal(await form.saveDraft({ mutate, state: { revision: 4 } }, { 'advanced.recallLimit': { value: '6' } }), true)
  assert.equal(await form.saveDraft({ mutate, state: { revision: 4 } }, {}), true)
  assert.deepEqual(sent, [[1, 4]], 'an empty draft sends nothing')
})

test('model/status: state words and the rows of the status block', () => {
  const { stateText, statusRows } = load('model/status.cjs')
  const { dictionaries } = load('i18n.cjs')
  const t = translate(dictionaries().en)
  assert.equal(stateText(t, 'running'), 'running')
  assert.equal(stateText(t, 'no-model'), 'no model installed')
  assert.equal(stateText(t, 'something-new'), 'something-new')
  assert.deepEqual(statusRows({ gateway: { mode: 'external', state: 'configured', url: 'http://127.0.0.1:8080' } }, t), [
    ['Gateway', 'external · configured · http://127.0.0.1:8080'],
    ['Retrieval', 'decided by the external gateway'],
    ['Embedding add-on', 'not installed'],
  ])
  const managed = statusRows({ gateway: { mode: 'managed', state: 'running' }, python: { found: true, version: '3.12', source: 'path', executable: 'py' }, retrieval: { setting: 'auto', active: 'keyword' }, store: { type: 'sqlite', file: 'a.db', count: 3 } }, t)
  assert.deepEqual(managed.slice(0, 4), [
    ['Gateway', 'managed · running'],
    ['Python', '3.12 (path) · py'],
    ['Retrieval', 'auto → keyword'],
    ['Store', 'sqlite · a.db · 3 memories'],
  ])
})

test('model/models: texts, sizes, the buttons of a row and the line on where the setup stands', () => {
  const m = load('model/models.cjs')
  const { dictionaries } = load('i18n.cjs')
  const t = translate(dictionaries().en)
  assert.equal(m.pickText({ en: 'a', zh: 'b' }, 'zh'), 'b')
  assert.equal(m.pickText({ en: 'a' }, 'zh'), 'a')
  assert.equal(m.pickText(null, 'en'), '')
  assert.deepEqual([m.formatSize(1.5e9), m.formatSize(2.5e6), m.formatSize(0), m.formatSize('x')], ['1.5 GB', '2.5 MB', '?', '?'])
  assert.equal(m.memoryText({ loaded: 640.4 }, t), 'about 640 MB')
  assert.equal(m.memoryText(null, t), 'not measured')
  const idle = { busy: false, chain: {} }
  assert.deepEqual(plain(m.modelActions({ id: 'm', state: 'installed' }, idle, false)), { primary: { label: ['actUse'], action: 'use', enabled: true }, secondary: { label: ['actRemove'], action: 'remove' } })
  assert.equal(m.modelActions({ id: 'm', state: 'absent', requiresAcceptance: { name: 'L' } }, idle, false).primary.enabled, false, 'a licence must be ticked first')
  assert.equal(m.modelActions({ id: 'm', state: 'absent', requiresAcceptance: { name: 'L' } }, idle, true).primary.enabled, true)
  assert.equal(m.modelActions({ id: 'm', state: 'absent' }, { busy: true, chain: { target: 'other' } }, false).primary.enabled, false, 'another model is being set up')
  assert.equal(m.chainLine({ chain: null }, t), null)
  assert.equal(m.chainLine({ chain: { target: 'bge', phase: 'model', percent: 40 } }, t), 'Downloading bge: 40%')
  assert.equal(m.sameProblem({ code: 'a' }, { code: 'a' }), true)
  assert.equal(m.sameProblem({ code: 'a' }, null), false)
})

// ---- the new pure modules, loaded on their own

test('layout: the tabs in page order, and placedKeys lists every placed setting in that order', () => {
  const { TABS, placedKeys } = load('layout.cjs')
  assert.deepEqual(TABS.map((tab) => tab.id), ['features', 'retrieval', 'advanced'])
  const keys = placedKeys()
  assert.equal(keys[0], 'modules.autoRecall')
  assert.equal(keys.at(-1), 'advanced.forgetting')
  assert.deepEqual(keys, TABS.flatMap((tab) => tab.sections.flatMap((section) => section.fields)))
  assert.equal(TABS.filter((tab) => tab.collapsible).map((tab) => tab.id).join(), 'advanced')
})

test('model/overview: formatCount and summarise work without React', () => {
  const o = load('model/overview.cjs')
  assert.deepEqual(o.MOODS, ['ready', 'busy', 'asleep', 'trouble'])
  assert.equal(o.formatCount(1284), '1,284')
  assert.equal(o.formatCount(12.9), '12')
  const running = { gateway: { mode: 'managed', state: 'running' }, python: { found: true }, retrieval: { active: 'keyword' }, store: { count: 2000 } }
  const summary = plain(o.summarise(running, null, false))
  assert.deepEqual([summary.mood, summary.headline.key, summary.line.key], ['ready', 'overview.head.ready', 'overview.ready.keyword'])
  assert.deepEqual(summary.facts.map((fact) => fact.id), ['count', 'tier', 'gateway'])
  assert.equal(plain(o.summarise(null, null, true)).mood, 'busy')
  assert.equal(plain(o.summarise(null, 'HTTP 500', false)).mood, 'trouble')
  assert.equal(plain(o.summarise({ gateway: { mode: 'managed', state: 'failed' } }, null, false)).mood, 'trouble')
  assert.equal(plain(o.summarise({ gateway: { mode: 'managed', state: 'unused' }, python: { found: true } }, null, false)).mood, 'asleep')
  assert.deepEqual(plain(o.summarise({}, null, false)).mood, 'ready', 'an answer with no parts is not an error')
})

test('model/page: splitOption, isMet, sectionView, pageView and savable', () => {
  const p = load('model/page.cjs')
  const { INDEX } = load('fields.cjs')
  const { TABS } = load('layout.cjs')
  assert.deepEqual(plain(p.splitOption('auto: local when the add-on is ready')), { name: 'auto', note: 'local when the add-on is ready' })
  assert.deepEqual(plain(p.splitOption('auto：本地向量插件就绪时用 local')), { name: 'auto', note: '本地向量插件就绪时用 local' })
  assert.deepEqual(plain(p.splitOption('plain')), { name: 'plain', note: '' }, 'no colon, all name')
  assert.deepEqual(plain(p.splitOption('')), { name: '', note: '' })
  assert.deepEqual(plain(p.splitOption(null)), { name: '', note: '' })
  assert.deepEqual(plain(p.splitOption(undefined)), { name: '', note: '' })
  assert.deepEqual(plain(p.splitOption('a: b: c')), { name: 'a', note: 'b: c' }, 'the first colon splits')
  const long = `${'x'.repeat(30)}: tail`
  assert.deepEqual(plain(p.splitOption(long)), { name: long, note: '' }, 'a name longer than 24 characters is not a name')
  assert.deepEqual(plain(p.splitOption('open:closed')), { name: 'open', note: 'closed' })

  assert.equal(p.isMet(INDEX['modules.autoRecall'], {}), true, 'no needs, always met')
  assert.equal(p.isMet(INDEX['advanced.afterthoughtMax'], { 'advanced.afterthought': true }), true)
  assert.equal(p.isMet(INDEX['advanced.afterthoughtMax'], { 'advanced.afterthought': false }), false)
  assert.equal(p.isMet(INDEX['advanced.afterthoughtMax'], {}), false)
  assert.equal(p.isMet(INDEX['retrieval.hybrid'], { 'retrieval.tier': 'local' }), true)
  assert.equal(p.isMet(INDEX['retrieval.hybrid'], { 'retrieval.tier': 'keyword' }), false)

  const state = { value: {}, base: {}, user: { advanced: { skipRepeats: false } }, revision: 1 }
  const experiments = TABS.find((tab) => tab.id === 'advanced').sections.find((section) => section.id === 'experiments')
  const view = p.sectionView(experiments, state, {}, null)
  assert.deepEqual(view.rows.map((row) => row.view.k), experiments.fields)
  assert.deepEqual(view.rows.map((row) => row.shown), experiments.fields.map((k) => !INDEX[k].needs))
  assert.deepEqual(view.rows.map((row) => row.parent), experiments.fields.map((k) => INDEX[k].needs ? INDEX[k].needs.key : null), 'the parent is named when it is in the same section')
  assert.deepEqual([view.staged, view.invalid, view.overridden], [0, 0, 0])
  const draft = { 'advanced.afterthoughtMax': { value: '99' }, 'advanced.afterthought': { value: true } }
  const staged = p.sectionView(experiments, state, draft, null)
  assert.deepEqual([staged.staged, staged.invalid], [2, 1], 'the opened row is invalid')
  assert.equal(staged.rows.find((row) => row.view.k === 'advanced.afterthoughtMax').shown, true)
  const hidden = p.sectionView(experiments, state, { 'advanced.afterthoughtMax': { value: '99' } }, null)
  assert.deepEqual([hidden.staged, hidden.invalid], [1, 0], 'a hidden row counts as staged but does not block')
  const tabs = p.pageView(state, draft, null)
  assert.deepEqual(tabs.map((entry) => entry.tab.id), TABS.map((tab) => tab.id))
  assert.deepEqual(tabs.map((entry) => [entry.staged, entry.invalid]), [[0, 0], [0, 0], [2, 1]])
  const advanced = tabs.find((entry) => entry.tab.id === 'advanced')
  assert.equal(advanced.sections.reduce((sum, section) => sum + section.overridden, 0), 3, 'the saved override and the two staged edits')
  assert.deepEqual(plain(p.savable({ 'advanced.afterthoughtMax': { value: '99' }, 'modules.autoRecall': { value: false } }, p.pageView(state, {}, null))), { 'advanced.afterthoughtMax': { value: '99' }, 'modules.autoRecall': { value: false } }, 'nothing is invalid in these views, so nothing is dropped')
  const views = p.pageView(state, { 'advanced.afterthoughtMax': { value: '99' } }, null)
  assert.deepEqual(plain(p.savable({ 'advanced.afterthoughtMax': { value: '99' }, 'modules.autoRecall': { value: false } }, views)), { 'modules.autoRecall': { value: false } })
})

test('model/page: splitOption gives a non-empty name for every option label of FIELDS, in both languages', () => {
  const { splitOption } = load('model/page.cjs')
  let seen = 0
  for (const field of ALL.filter((f) => f.options)) {
    for (const option of field.options) {
      for (const lang of ['en', 'zh']) {
        const { name, note } = splitOption(option.label[lang])
        assert.ok(name, `${field.k}.${option.value} ${lang}`)
        assert.equal(name, option.value, 'the name of an option is its value')
        assert.ok(note, `${field.k}.${option.value} ${lang} has a note`)
        seen += 1
      }
    }
  }
  assert.ok(seen >= 20)
})

test('model/changelog: entryDate and releases', () => {
  const c = load('model/changelog.cjs')
  assert.equal(c.entryDate('日期：2026-11-18。内置网关为提交'), '2026-11-18')
  assert.equal(c.entryDate('日期:2026-01-02'), '2026-01-02', 'the ASCII colon too')
  assert.equal(c.entryDate('日期： 2026-01-02'), '2026-01-02')
  assert.equal(c.entryDate('no date'), '')
  assert.equal(c.entryDate(undefined), '')
  assert.equal(c.entryDate('日期：2026-1-2'), '')
  const list = plain(c.releases({
    version: '0.3.2',
    entries: [
      { version: '0.3.3', title: '0.3.3', body: '日期：2026-11-19。' },
      { version: '0.3.2', title: '0.3.2', body: 'x' },
      { title: 'only a title', body: 'y' },
      { version: '0.3.1' },
      null,
    ],
  }))
  assert.deepEqual(list.map((r) => r.name), ['0.3.3', '0.3.2', 'only a title'])
  assert.deepEqual(list.map((r) => r.id), ['0.3.3', '0.3.2', 'only a title'])
  assert.deepEqual(list.map((r) => r.installed), [false, true, false])
  assert.deepEqual(list.map((r) => r.date), ['2026-11-19', '', ''])
  assert.deepEqual(plain(c.releases(null)), [])
  assert.deepEqual(plain(c.releases({ entries: 'x' })), [])
  assert.deepEqual(plain(c.releases({ entries: [{ body: 'x' }] })), [{ id: '0', name: '', date: '', body: 'x', installed: false }])
  assert.equal(c.releases({ entries: [{ version: '1', body: 'x' }] })[0].installed, false, 'with no installed version nothing is marked')
})

// ---- reading the sources

function sources(dir = SRC, prefix = '') {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => (
    entry.isDirectory() ? sources(path.join(dir, entry.name), prefix + entry.name + '/') : [prefix + entry.name]
  ))
}
const IDS = sources()
const CJS = IDS.filter((id) => id.endsWith('.cjs'))
const read = (id) => fs.readFileSync(path.join(SRC, id), 'utf8')
const resolveId = (from, spec) => path.posix.normalize(path.posix.join(path.posix.dirname(from), spec))

// The text of a module with its comments taken out (strings stay), and its single-quoted string literals.
// A backslash skips the next character everywhere, so a regular expression like /\/\// does not start a comment.
function scan(text) {
  let code = ''
  const literals = []
  let i = 0
  while (i < text.length) {
    const c = text[i]
    if (c === '\\') { code += text.slice(i, i + 2); i += 2; continue }
    if (c === '/' && text[i + 1] === '/') { while (i < text.length && text[i] !== '\n') i += 1; continue }
    if (c === '/' && text[i + 1] === '*') { i = text.indexOf('*/', i + 2) + 2; continue }
    if (c === "'") {
      let j = i + 1
      let value = ''
      while (j < text.length && text[j] !== "'") {
        if (text[j] === '\\') { value += text[j + 1]; j += 2 } else { value += text[j]; j += 1 }
      }
      literals.push(value)
      code += text.slice(i, j + 1)
      i = j + 1
      continue
    }
    code += c
    i += 1
  }
  return { code, literals }
}
const requires = (text) => [...scan(text).code.matchAll(/\brequire\(\s*'([^']+)'\s*\)/g)].map((m) => m[1])
const relativeRequires = (id) => requires(read(id)).filter((spec) => spec.startsWith('.')).map((spec) => resolveId(id, spec))

test('sources: every module starts with use strict and a header comment, and uses no module syntax', () => {
  for (const key of ['index.cjs', 'react.cjs', 'layout.cjs', 'model/page.cjs', 'kit/controls.cjs', 'view/page.cjs', 'theme/styles.cjs', 'theme/page.css']) assert.ok(IDS.includes(key), key)
  assert.ok(CJS.length >= 20)
  for (const id of CJS) {
    const text = read(id)
    const lines = text.split('\n')
    assert.equal(lines[0], "'use strict'", `${id}: starts with 'use strict'`)
    const firstAfter = lines.slice(1).find((line) => line.trim() !== '')
    assert.ok(firstAfter && firstAfter.startsWith('// '), `${id}: a // header comment follows`)
    assert.ok(!/^\s*(import|export)\s/m.test(scan(text).code), `${id}: module syntax`)
    assert.ok(text.endsWith('\n') && !text.includes('\r'), `${id}: ends with LF only`)
    assert.equal((text.match(/^module\.exports = /gm) || []).length, 1, `${id}: one module.exports`)
    // The export object is the last thing in the file (it may span lines).
    const afterExport = text.slice(text.lastIndexOf('module.exports = ')).split('\n').slice(1).join('\n')
    assert.ok(!/^(const|let|var|function|async|class|if|for)\b/m.test(afterExport), `${id}: nothing but the export object after module.exports`)
  }
  assert.ok(!read('theme/page.css').includes('\r'), 'the stylesheet has LF only')
})

// A regular expression literal after one of these characters, up to its flags. One that holds a
// backtick (the Markdown pattern) is taken out of the line before the backticks are counted.
const REGEX_LITERAL = /(^|[=(,:!&|?{};]\s*)\/(?![*/])(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n[])+\/[a-z]*/g

test('sources: no template literal spans lines (the build indents lines)', () => {
  // A template literal that is open at the end of a line has an odd number of backticks on it. Comments
  // are taken out first; the backticks of the Markdown pattern in view/markdown.cjs come in pairs.
  for (const id of CJS) {
    scan(read(id)).code.split('\n').forEach((raw, index) => {
      const line = raw.replace(REGEX_LITERAL, '$1')
      assert.equal((line.match(/`/g) || []).length % 2, 0, `${id}:${index + 1}: a template literal continues on the next line`)
    })
  }
})

test('sources: only react.cjs asks for react, nothing asks for any other host module', () => {
  const found = {}
  for (const id of CJS) {
    for (const spec of requires(read(id))) {
      if (spec.startsWith('./') || spec.startsWith('../')) continue
      ;(found[spec] ??= []).push(id)
    }
  }
  assert.deepEqual(found, { react: ['react.cjs'] })
  assert.ok(!IDS.includes('host-ui.cjs') && !IDS.includes('view/legacy.cjs'), 'the host primitives and the old view are gone')
})

test('sources: every relative require ends in .cjs (or .css) and resolves to a file under client/src', () => {
  const ids = new Set(IDS)
  for (const id of CJS) {
    for (const spec of requires(read(id)).filter((s) => s.startsWith('.'))) {
      assert.match(spec, /^\.\.?\/.*\.(cjs|css)$/, `${id}: ${spec} must be relative and end in .cjs`)
      assert.ok(ids.has(resolveId(id, spec)), `${id}: ${spec} does not resolve to a file`)
    }
  }
})

// What each layer may require, besides itself where it says so. Every rule is checked against the whole
// graph, and the graph must have no cycle.
const LAYER_RULES = [
  [(id) => ['constants.cjs', 'fields.cjs', 'layout.cjs', 'react.cjs', 'clipboard.cjs'].includes(id), () => false, 'tables, the React entry and the clipboard require nothing of the page'],
  [(id) => id === 'i18n.cjs', (to) => to === 'fields.cjs', 'i18n.cjs: the field table only'],
  [(id) => id === 'net.cjs', (to) => to === 'constants.cjs', 'net.cjs: the constants only'],
  [(id) => id === 'hooks.cjs', (to) => ['react.cjs', 'constants.cjs', 'net.cjs', 'clipboard.cjs'].includes(to), 'hooks.cjs: React, the constants, the calls and the clipboard; no view, kit or theme'],
  [(id) => id.startsWith('model/'), (to) => to.startsWith('model/') || ['fields.cjs', 'layout.cjs', 'constants.cjs'].includes(to), 'model/*: other model files and the tables; no React, kit, view, theme or hooks'],
  [(id) => id.startsWith('theme/'), (to) => to.startsWith('theme/'), 'theme/*: theme/* only'],
  [(id) => id.startsWith('kit/'), (to) => to.startsWith('kit/') || ['react.cjs', 'theme/styles.cjs'].includes(to), 'kit/*: kit/*, react.cjs and theme/styles.cjs only'],
  [(id) => id.startsWith('view/'), (to) => to !== 'index.cjs', 'view/*: anything but the entry point'],
]

test('sources: each layer requires only what its rule allows', () => {
  for (const id of CJS.filter((x) => x !== 'index.cjs')) {
    const rules = LAYER_RULES.filter(([applies]) => applies(id))
    assert.equal(rules.length, 1, `${id} belongs to exactly one layer`)
    const [, allowed, rule] = rules[0]
    for (const to of relativeRequires(id)) assert.ok(allowed(to), `${id} requires ${to}: ${rule}`)
  }
  assert.ok(relativeRequires('index.cjs').length >= 15, 'the entry point wires everything')
})

test('sources: only index.cjs requires view/page.cjs, nothing outside view/ requires a view, and nobody requires index.cjs', () => {
  for (const id of CJS) {
    const targets = relativeRequires(id)
    assert.ok(!targets.includes('index.cjs'), `${id} requires the entry point`)
    if (id !== 'index.cjs') assert.ok(!targets.includes('view/page.cjs'), `${id} requires view/page.cjs`)
    if (!id.startsWith('view/') && id !== 'index.cjs') assert.ok(!targets.some((to) => to.startsWith('view/')), `${id} requires a view`)
  }
  assert.ok(relativeRequires('index.cjs').includes('view/page.cjs'))
})

test('sources: the require graph has no cycle', () => {
  const state = {}
  const visit = (id, trail) => {
    assert.notEqual(state[id], 'open', `cycle: ${[...trail, id].join(' -> ')}`)
    if (state[id] === 'done') return
    state[id] = 'open'
    for (const to of IDS.includes(id) && id.endsWith('.cjs') ? relativeRequires(id) : []) visit(to, [...trail, id])
    state[id] = 'done'
  }
  for (const id of CJS) visit(id, [])
})

test('sources: the layers load on their own in Node, with no React and no browser', () => {
  for (const id of CJS.filter((x) => /^(constants|fields|i18n|layout|net|clipboard)\.cjs$|^model\//.test(x))) assert.doesNotThrow(() => load(id), id)
})

// ---- layout.cjs

test('layout: every setting is placed exactly once, in a tab and a section', () => {
  const { TABS, placedKeys } = load('layout.cjs')
  const placed = placedKeys()
  assert.equal(new Set(placed).size, placed.length, `placed twice: ${placed.filter((k, i) => placed.indexOf(k) !== i)}`)
  assert.deepEqual([...placed].sort(), ALL.map((f) => f.k).sort())
  assert.ok(TABS.every((tab) => tab.sections.length > 0))
})

test('layout: a setting with needs sits in the same section as the setting it needs, after it', () => {
  const { TABS } = load('layout.cjs')
  let checked = 0
  for (const tab of TABS) {
    for (const section of tab.sections) {
      for (const [index, key] of section.fields.entries()) {
        const field = ALL.find((f) => f.k === key)
        if (!field.needs) continue
        const at = section.fields.indexOf(field.needs.key)
        assert.ok(at >= 0, `${key} needs ${field.needs.key}, which is not in section ${section.id}`)
        assert.ok(at < index, `${key} comes before the setting it needs`)
        checked += 1
      }
    }
  }
  assert.equal(checked, ALL.filter((f) => f.needs).length)
})

test('layout: tab ids and section ids are unique and do not meet the ids of the tabs without settings', () => {
  const { TABS } = load('layout.cjs')
  const tabIds = TABS.map((tab) => tab.id)
  const sectionIds = TABS.flatMap((tab) => tab.sections.map((section) => section.id))
  assert.equal(new Set(tabIds).size, tabIds.length)
  assert.equal(new Set(sectionIds).size, sectionIds.length)
  for (const id of tabIds) assert.ok(!['overview', 'changelog', 'feedback'].includes(id), 'those three tabs are the page’s own')
  for (const id of [...tabIds, ...sectionIds]) assert.match(id, /^[a-z]+$/)
})

test('layout: every label, help and note has English and Chinese text', () => {
  const { TABS } = load('layout.cjs')
  const both = (text, what) => {
    assert.ok(text && typeof text.en === 'string' && text.en.trim(), `${what}: en`)
    assert.ok(typeof text.zh === 'string' && text.zh.trim(), `${what}: zh`)
  }
  for (const tab of TABS) {
    both(tab.label, `tab ${tab.id} label`)
    if (tab.note) both(tab.note, `tab ${tab.id} note`)
    for (const section of tab.sections) {
      both(section.label, `section ${section.id} label`)
      if (section.help) both(section.help, `section ${section.id} help`)
    }
  }
})

test('layout: a panel is a name the page provides, and a section with one may hold no settings', () => {
  const { TABS } = load('layout.cjs')
  const PROVIDED = ['models']
  const withPanel = TABS.flatMap((tab) => tab.sections).filter((section) => section.panel)
  assert.deepEqual(withPanel.map((section) => section.panel), ['models'])
  for (const section of withPanel) assert.ok(PROVIDED.includes(section.panel), section.id)
  assert.ok(read('view/page.cjs').includes('const panels = { models:'), 'view/page.cjs builds the panel the layout names')
  for (const section of TABS.flatMap((tab) => tab.sections).filter((s) => !s.panel)) assert.ok(section.fields.length > 0, `section ${section.id} is empty`)
})

// ---- the stylesheet

const CSS = read('theme/page.css').replace(/\r\n?/g, '\n')
const CSS_CODE = CSS.replace(/\/\*[\s\S]*?\*\//g, '')

// Every rule of a stylesheet, at-rules entered, keyframes noted and not entered: { selector, body } | { keyframes }.
function parseCss(css) {
  const rules = []
  const walk = (text) => {
    let i = 0
    while (i < text.length) {
      const open = text.indexOf('{', i)
      if (open < 0) break
      const prelude = text.slice(i, open).trim()
      let depth = 1
      let j = open + 1
      while (j < text.length && depth > 0) {
        if (text[j] === '{') depth += 1
        else if (text[j] === '}') depth -= 1
        j += 1
      }
      assert.equal(depth, 0, `unbalanced braces after "${prelude}"`)
      const body = text.slice(open + 1, j - 1)
      if (/^@keyframes\b/.test(prelude)) rules.push({ keyframes: prelude.replace(/^@keyframes\s+/, '') })
      else if (prelude.startsWith('@')) walk(body)
      else rules.push({ selector: prelude, body })
      i = j
    }
  }
  walk(css)
  return rules
}

// A selector list split at top-level commas (not those inside :is(), :where(), :not() or [..]).
function splitSelectors(list) {
  const out = []
  let depth = 0
  let from = 0
  for (let i = 0; i < list.length; i += 1) {
    const c = list[i]
    if (c === '(' || c === '[') depth += 1
    else if (c === ')' || c === ']') depth -= 1
    else if (c === ',' && depth === 0) { out.push(list.slice(from, i).trim()); from = i + 1 }
  }
  out.push(list.slice(from).trim())
  return out
}

// The part of a selector after its last top-level combinator.
function lastCompound(selector) {
  let depth = 0
  let from = 0
  for (let i = 0; i < selector.length; i += 1) {
    const c = selector[i]
    if (c === '(' || c === '[') depth += 1
    else if (c === ')' || c === ']') depth -= 1
    else if (depth === 0 && /[\s>+~]/.test(c)) from = i + 1
  }
  return selector.slice(from)
}

const RULES = parseCss(CSS_CODE).filter((rule) => rule.selector)

test('theme/page.css: braces balance, outside strings and comments', () => {
  assert.equal((CSS_CODE.match(/\{/g) || []).length, (CSS_CODE.match(/\}/g) || []).length)
  assert.equal((CSS_CODE.match(/\(/g) || []).length, (CSS_CODE.match(/\)/g) || []).length)
  let depth = 0
  for (const c of CSS_CODE) {
    if (c === '{') depth += 1
    if (c === '}') depth -= 1
    assert.ok(depth >= 0)
  }
  assert.equal(depth, 0)
  assert.ok(RULES.length > 100)
})

const codeLiterals = CJS.filter((id) => !['fields.cjs', 'i18n.cjs'].includes(id)).flatMap((id) => scan(read(id)).literals)
const usedClasses = new Set(codeLiterals.flatMap((literal) => [...literal.matchAll(/fmg-[a-z0-9_-]*/g)].map((m) => m[0])))
const definedClasses = new Set([...CSS_CODE.matchAll(/\.(fmg-[a-z0-9_-]+)/g)].map((m) => m[1]))
// A class built from a prefix ('fmg-button--' + variant) stands for every class that starts with it.
const isPrefix = (name) => name.endsWith('-')

test('theme/page.css: every fmg- class name the modules use is defined in the stylesheet', () => {
  assert.ok(usedClasses.size > 50)
  for (const name of usedClasses) {
    if (isPrefix(name)) assert.ok([...definedClasses].some((defined) => defined.startsWith(name)), `${name}* matches no class of the stylesheet`)
    else assert.ok(definedClasses.has(name), `.${name} is used but not defined`)
  }
})

test('theme/page.css: every class the stylesheet defines is used by some module', () => {
  const prefixes = [...usedClasses].filter(isPrefix)
  for (const name of definedClasses) {
    assert.ok(usedClasses.has(name) || prefixes.some((prefix) => name.startsWith(prefix)), `.${name} is defined but no module uses it`)
  }
})

test('theme/page.css: every selector is scoped to the page, and no selector outside @keyframes lacks the fmg- prefix', () => {
  const scoped = (selector) => /^(\.fmg-|:where\(\.fmg-root\))/.test(selector) || lastCompound(selector).includes('.fmg-')
  let count = 0
  for (const { selector } of RULES) {
    for (const one of splitSelectors(selector)) {
      count += 1
      assert.ok(scoped(one), `selector "${one}" could match outside the page`)
      assert.ok(!/(^|[\s>+~,(])(div|span|p|button|input|a|ul|li|h[1-6]|body|html)\b(?![-\w])/.test(one.replace(/\[[^\]]*\]/g, '')) || /^(\.fmg-|:where\(\.fmg-root\))/.test(one), `selector "${one}" starts with an element name`)
    }
  }
  assert.ok(count > 120)
  const keyframes = parseCss(CSS_CODE).filter((rule) => rule.keyframes).map((rule) => rule.keyframes)
  assert.ok(keyframes.length >= 3)
  for (const name of keyframes) assert.match(name, /^fmg-/)
})

test('theme/page.css: the .fmg-root block defines every --fmg- variable the rules use', () => {
  const roots = RULES.filter((rule) => rule.selector === '.fmg-root')
  assert.equal(roots.length, 1, 'one token block')
  const defined = new Set([...roots[0].body.matchAll(/(--fmg-[\w-]+)\s*:/g)].map((m) => m[1]))
  const used = new Set([...CSS_CODE.matchAll(/var\(\s*(--fmg-[\w-]+)/g)].map((m) => m[1]))
  assert.ok(used.size > 30)
  for (const name of used) assert.ok(defined.has(name), `${name} is used but not defined in .fmg-root`)
  // No rule outside the block defines a variable of its own: every token is in the one place.
  for (const rule of RULES.filter((r) => r !== roots[0])) assert.ok(!/(^|[;{\s])--[\w-]+\s*:/.test(rule.body), `${rule.selector} defines a custom property`)
})

// Tokens that are defined in the block and not used yet. They are listed so that a new unused one fails
// the test, and one that gets used has to be taken off the list.
const KNOWN_UNUSED_TOKENS = [] // none today; a token that is defined and never read fails the test below

test('theme/page.css: every --fmg- variable the block defines is used, except the ones listed in KNOWN_UNUSED_TOKENS', () => {
  const root = RULES.find((rule) => rule.selector === '.fmg-root')
  const defined = [...root.body.matchAll(/(--fmg-[\w-]+)\s*:/g)].map((m) => m[1])
  const unused = defined.filter((name) => !new RegExp(`var\\(\\s*${name}[,)\\s]`).test(CSS_CODE))
  assert.deepEqual(unused.sort(), KNOWN_UNUSED_TOKENS)
})

test('theme/page.css: the host’s variables (--dsw-, --dsh-, --ds-) are read only in the .fmg-root block, each with a fallback value', () => {
  const uses = [...CSS_CODE.matchAll(/var\(\s*(--(?:dsw|dsh|ds)-[\w-]+)\s*([,)])/g)]
  assert.ok(uses.length >= 20)
  for (const [, name, next] of uses) assert.equal(next, ',', `var(${name}) has no fallback value`)
  const root = RULES.find((rule) => rule.selector === '.fmg-root')
  const outside = CSS_CODE.replace(root.body, '')
  assert.ok(!/--(?:dsw|dsh|ds)-/.test(outside), 'a host variable is used outside the token block')
})

// ---- i18n.cjs against the keys the modules ask for

test('i18n: every t(’…’) key written out in client/src exists in the dictionary of both languages', () => {
  // Keys built by concatenation are out of scope here (a literal that ends in a dot is the start of one):
  // 'f.' 'h.' 'o.' (the field table), 'state.', 'installState.', 'language.', 'mascot.',
  // 'overview.ready.', 'overview.fact.tier.' and 'overview.asleep.'.
  // Keys inside the label arrays of model/models.cjs and the text() calls of model/overview.cjs are
  // covered by the behaviour tests of those two modules (every key they can return exists).
  const { UI } = load('i18n.cjs')
  let checked = 0
  for (const id of CJS.filter((x) => !['i18n.cjs', 'fields.cjs'].includes(x))) {
    const { code } = scan(read(id))
    const keys = [...code.matchAll(/\bt\(\s*'([^']*)'/g)].map((m) => m[1])
    for (const m of code.matchAll(/\bt\([^()]*?\?\s*'([^']+)'\s*:\s*'([^']+)'/g)) keys.push(m[1], m[2])
    for (const key of keys) {
      if (key.endsWith('.')) continue
      for (const lang of ['en', 'zh']) assert.ok(key in UI[lang], `${id}: t('${key}') is not in the ${lang} dictionary`)
      checked += 1
    }
  }
  assert.ok(checked > 60)
  // A string literal that looks like a namespaced key is a key.
  for (const literal of codeLiterals) {
    if (/^(overview|phase|tab|mascot|state|installState|language)\.[\w.-]*[\w]$/.test(literal)) assert.ok(literal in UI.en, `'${literal}' is not in the dictionary`)
  }
})

// Static keys of the dictionary that no module asks for yet. Listed so that a new unused key fails the
// test, and one that gets used has to be taken off the list.
const KNOWN_UNUSED = [] // none today; a text nobody asks for fails the test below

test('i18n: every static key of the dictionary is used somewhere in client/src, except the ones listed in KNOWN_UNUSED', () => {
  const { UI } = load('i18n.cjs')
  const DYNAMIC_PREFIXES = ['state.', 'installState.', 'language.', 'mascot.', 'overview.ready.', 'overview.fact.tier.', 'overview.asleep.', 'feedback.kind.']
  const code = CJS.filter((id) => !['i18n.cjs', 'fields.cjs'].includes(id)).map((id) => scan(read(id)).code).join('\n')
  for (const prefix of DYNAMIC_PREFIXES) {
    assert.ok(code.includes(`'${prefix}' +`), `no module builds a key from '${prefix}' any more: take it off the list`)
    assert.ok(Object.keys(UI.en).some((key) => key.startsWith(prefix)), `no key starts with '${prefix}'`)
  }
  const literals = new Set(codeLiterals)
  const unused = Object.keys(UI.en).filter((key) => !literals.has(key) && !DYNAMIC_PREFIXES.some((prefix) => key.startsWith(prefix)))
  assert.deepEqual(unused.sort(), KNOWN_UNUSED)
})
