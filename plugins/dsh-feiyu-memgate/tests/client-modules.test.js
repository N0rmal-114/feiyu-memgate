// The modules of the settings page (client/src), loaded one by one in Node without React, without the
// bundle and without a host: the pure ones must work on their own. Also the import rules of
// client/README.md, checked by reading the sources.
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

test('constants: the routes and namespace the host half agrees on', () => {
  const c = load('constants.cjs')
  assert.deepEqual(plain(c), {
    NS: 'feiyuMemgate',
    ROW_KEY: 'dsh-feiyu-memgate#feiyu-memgate',
    STATUS_ROUTE: 'feiyu-memgate/status',
    STATUS_POLL_MS: 10000,
    CHANGELOG_ROUTE: 'feiyu-memgate/changelog',
    MODELS_ROUTE: 'feiyu-memgate/models',
    MODELS_POLL_MS: 10000,
    MODELS_BUSY_POLL_MS: 1500,
  })
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
  assert.equal(en.save, 'Save')
  assert.equal(zh.save, '保存')
  assert.equal(en['g.retrieval'], 'Retrieval')
  assert.equal(zh['f.gateway.mode'], '网关模式')
  assert.equal(en['o.retrieval.tier.keyword'], FIELDS[1].fields[0].options[1].label.en)
  assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort())
  assert.equal(UI.en.save, 'Save', 'the static dictionary is copied, not changed')
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

// ---- the import rules of client/README.md, by reading the sources

function sources(dir = SRC, prefix = '') {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => (
    entry.isDirectory() ? sources(path.join(dir, entry.name), prefix + entry.name + '/') : [prefix + entry.name]
  ))
}
const requires = (text) => [...text.matchAll(/\brequire\(\s*'([^']+)'\s*\)/g)].map((m) => m[1])
const resolveId = (from, spec) => path.posix.normalize(path.posix.join(path.posix.dirname(from), spec))

test('sources: every module starts with use strict and a header comment, and uses no module syntax', () => {
  const ids = sources()
  assert.ok(ids.includes('index.cjs') && ids.length === 13, ids.join())
  for (const id of ids) {
    const text = fs.readFileSync(path.join(SRC, id), 'utf8')
    assert.match(text, /^'use strict'\n\n\/\/ \S/, `${id}: 'use strict' then a header comment`)
    assert.ok(!/^\s*(import|export)\s/m.test(text), `${id}: module syntax`)
    assert.ok(text.endsWith('\n') && !text.includes('\r'), `${id}: ends with LF only`)
    assert.equal((text.match(/^module\.exports = /gm) || []).length, 1, `${id}: one module.exports`)
    if (id !== 'index.cjs') assert.ok(text.trimEnd().split('\n').at(-1).startsWith('module.exports = '), `${id}: exports on the last line`)
  }
})

test('sources: only react.cjs asks for react, only host-ui.cjs for the primitives, nothing else from the host', () => {
  const HOST_UI = '@deepseek-ai/dsh-client-ui-primitives'
  const found = { react: [], [HOST_UI]: [] }
  for (const id of sources()) {
    for (const spec of requires(fs.readFileSync(path.join(SRC, id), 'utf8'))) {
      if (spec.startsWith('./') || spec.startsWith('../')) continue
      assert.ok(spec in found, `${id}: requires ${spec}, which is not in the shared module table`)
      found[spec].push(id)
    }
  }
  assert.deepEqual(found.react, ['react.cjs'])
  assert.deepEqual(found[HOST_UI], ['host-ui.cjs'])
})

test('sources: every relative require ends in .cjs and resolves to a file under client/src', () => {
  const ids = new Set(sources())
  for (const id of ids) {
    for (const spec of requires(fs.readFileSync(path.join(SRC, id), 'utf8')).filter((s) => s.startsWith('.'))) {
      assert.match(spec, /^\.\.?\/.*\.(cjs|css)$/, `${id}: ${spec} must be relative and end in .cjs`)
      assert.ok(ids.has(resolveId(id, spec)), `${id}: ${spec} does not resolve to a file`)
    }
  }
})

test('sources: model/* needs neither React nor the host primitives, except model/markdown.cjs which builds elements', () => {
  for (const id of sources().filter((s) => s.startsWith('model/'))) {
    const targets = requires(fs.readFileSync(path.join(SRC, id), 'utf8')).filter((s) => s.startsWith('.')).map((s) => resolveId(id, s))
    if (id === 'model/markdown.cjs') {
      assert.ok(targets.includes('react.cjs'), 'markdown builds React elements through react.cjs')
      assert.ok(!targets.includes('host-ui.cjs'))
      assert.ok(!targets.some((t) => t.startsWith('view/')), 'the model layer does not depend on the view')
      continue
    }
    assert.ok(!targets.includes('react.cjs') && !targets.includes('host-ui.cjs'), `${id} reaches the host`)
    assert.ok(!targets.some((t) => t.startsWith('view/') || t === 'hooks.cjs'), `${id} depends on the view layer`)
  }
})
