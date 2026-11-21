// Feedback: the Feedback tab of the settings page and /memgate feedback. Both hand a person the address
// of GitHub's new-issue page with a report filled in; neither sends anything. Checked here: what may be
// in a report besides the person's own words (version numbers and state names, never a path, an address,
// a file name, a count or a secret), how the address is built and cut when it gets too long, the
// clipboard, the command, and what the tab draws. React is the stand-in of tests/helpers/client-harness.js.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { createMemgateCommand } from '../src/commands.js'
import { readConfig } from '../src/config.js'
import { expand, findAll, load, plain, textOf, translatorFor, withClass, withRole } from './helpers/client-harness.js'

const require = createRequire(import.meta.url)
const feedback = require('../client/src/model/feedback.cjs')
const { copyText } = require('../client/src/clipboard.cjs')
const { LINKS } = require('../client/src/constants.cjs')

const tick = () => new Promise((resolve) => setImmediate(resolve))

// A status that holds everything a report must not: a private address, paths with a user name, a store
// file, a count of memories, an error text with a path.
const PRIVATE = ['someone', '10.1.2.3', 'memories-3f9a1c', '1284', 'gguf', 'true']
const status = (overrides = {}) => ({
  version: '0.3.2',
  language: 'zh',
  runtime: { platform: 'win32', arch: 'x64', node: '22.19.0' },
  modules: { autoRecall: true },
  gateway: { mode: 'managed', state: 'running', url: 'http://10.1.2.3:8080/private' },
  secrets: { 'external.token': true, 'retrieval.apiKey': true },
  python: { found: true, version: '3.12.4', source: 'path', executable: 'C:\\Users\\someone\\python.exe' },
  retrieval: { setting: 'auto', active: 'local', hybrid: { setting: true, active: true } },
  store: { tier: 'local', type: 'embedding', file: 'memories-3f9a1c.json', count: 1284 },
  embed: { version: '0.1.0', state: 'ready', model: 'bge-m3', error: 'ENOENT C:\\Users\\someone\\model.gguf' },
  ...overrides,
})

const ENVIRONMENT = [
  ['plugin', 'dsh-feiyu-memgate 0.3.2'],
  ['add-on', 'dsh-feiyu-memgate-embed 0.1.0, ready, bge-m3'],
  ['gateway', 'managed, running'],
  ['retrieval', 'auto -> local + keyword'],
  ['python', '3.12.4'],
  ['system', 'win32 x64, Node 22.19.0'],
  ['language', 'zh'],
]

/** The title and the text a new-issue address carries. */
const filled = (url) => {
  const parsed = new URL(url)
  return { page: parsed.origin + parsed.pathname, title: parsed.searchParams.get('title'), body: parsed.searchParams.get('body') }
}

// ------------------------------------------------------------------ what goes into a report

test('environment(): version numbers and state names, and nothing private the status holds', () => {
  const rows = feedback.environment(status())
  assert.deepEqual(rows, ENVIRONMENT)
  const text = JSON.stringify(rows)
  for (const word of PRIVATE) assert.ok(!text.includes(word), `the report would carry "${word}"`)
})

test('environment(): a value that does not look like a name or a number becomes a question mark', () => {
  const rows = Object.fromEntries(feedback.environment(status({
    version: 'C:\\Users\\someone',
    language: 'zh\nextra: line',
    runtime: { platform: 'win32', arch: '<script>', node: 'x'.repeat(41) },
    gateway: { mode: 'managed', state: 'https://10.1.2.3/' },
    python: { found: true, version: '/home/someone/bin/python3' },
    embed: { version: '0.1.0', state: 'ready', model: 'D:/models/custom.gguf' },
    retrieval: { setting: 'auto', active: { toString: () => 'someone' }, hybrid: { active: false } },
  })))
  assert.deepEqual(rows, {
    plugin: 'dsh-feiyu-memgate ?',
    'add-on': 'dsh-feiyu-memgate-embed 0.1.0, ready, ?',
    gateway: 'managed, ?',
    retrieval: 'auto -> ?',
    python: '?',
    system: 'win32 ?, Node ?',
    language: '?',
  })
  for (const value of ['0.3.2', '0.2.2-rc.1', 'no-model', 'bge-small-zh-v1.5', 'x64', 12]) assert.equal(feedback.safe(value), String(value))
  for (const value of ['', ' x', 'a/b', 'a\\b', 'a:b', 'a\nb', null, undefined, {}, ['x'], true]) assert.equal(feedback.safe(value), '?')
})

test('environment(): the states a person can be in, and no status at all', () => {
  const of = (overrides) => Object.fromEntries(feedback.environment(status(overrides)))
  assert.equal(of({ embed: null })['add-on'], 'not installed')
  assert.equal(of({ embed: { version: '0.1.0', state: 'no-model', model: null } })['add-on'], 'dsh-feiyu-memgate-embed 0.1.0, no-model')
  assert.equal(of({ python: { found: false } }).python, 'not found')
  assert.equal(of({ python: null }).python, 'not checked')
  assert.equal(of({ retrieval: { setting: 'keyword', active: 'keyword', hybrid: { active: false } } }).retrieval, 'keyword -> keyword')
  assert.equal(of({ retrieval: { setting: 'local', active: null } }).retrieval, 'local')
  const external = feedback.environment(status({ gateway: { mode: 'external', state: 'configured', url: 'http://10.1.2.3:8080' } }))
  assert.deepEqual(external.map((row) => row[0]), ['plugin', 'add-on', 'gateway', 'system', 'language'], 'an external gateway decides retrieval itself')
  assert.ok(!JSON.stringify(external).includes('10.1.2.3'))
  assert.deepEqual(feedback.environment(status({ runtime: undefined })).map((row) => row[0]).includes('system'), false, 'a host half without the runtime lines')
  for (const none of [null, undefined, 'x', 5]) assert.deepEqual(feedback.environment(none), [])
})

// ------------------------------------------------------------------ the title, the text, the address

test('titleOf(): the tag of the kind and the first line that holds something, shortened at 60 characters', () => {
  assert.deepEqual(feedback.KINDS, ['idea', 'problem', 'other'])
  assert.equal(feedback.titleOf('idea', 'Recall by date\nsecond line'), '[idea] Recall by date')
  assert.equal(feedback.titleOf('problem', '\n\n  召回太慢了  \n第二行'), '[bug] 召回太慢了')
  assert.equal(feedback.titleOf('other', 'hello'), '[feedback] hello')
  assert.equal(feedback.titleOf('nonsense', 'hello'), '[feedback] hello', 'an unknown kind is plain feedback')
  assert.equal(feedback.titleOf('idea', '   '), '[idea]')
  const long = feedback.titleOf('idea', '长'.repeat(61))
  assert.equal(long, '[idea] ' + '长'.repeat(60) + '\u2026')
  assert.equal(feedback.titleOf('idea', '长'.repeat(60)), '[idea] ' + '长'.repeat(60), 'exactly 60 is not shortened')
  assert.equal(feedback.titleOf('idea', '😀'.repeat(61)), '[idea] ' + '😀'.repeat(60) + '\u2026', 'counted in characters, a pair is never split')
})

test('compose(): the address is GitHub’s new-issue page of the project and carries exactly the title and the text', () => {
  const report = feedback.compose({ kind: 'problem', text: '  召回太慢了\r\n第二行 & more? #1  ', environment: ENVIRONMENT })
  assert.equal(report.ready, true)
  assert.equal(report.clipped, false)
  assert.equal(report.title, '[bug] 召回太慢了')
  assert.equal(report.body, [
    '召回太慢了\n第二行 & more? #1',
    '',
    '---',
    'Environment (added by dsh-feiyu-memgate)',
    ...ENVIRONMENT.map((row) => `- ${row[0]}: ${row[1]}`),
  ].join('\n'))
  assert.deepEqual(filled(report.url), { page: 'https://github.com/N0rmal-114/feiyu-memgate/issues/new', title: report.title, body: report.body })
  assert.ok(report.url.startsWith(LINKS.newIssue + '?title='))
  assert.deepEqual([...new URL(report.url).searchParams.keys()], ['title', 'body'], 'no label, assignee or template is asked for')
  assert.equal(feedback.reportText(report), report.title + '\n\n' + report.body)
})

test('compose(): without the environment the text stands alone, and nothing written is not ready', () => {
  const alone = feedback.compose({ kind: 'idea', text: 'one line', environment: [] })
  assert.equal(alone.body, 'one line')
  assert.equal(feedback.compose({ kind: 'idea', text: 'one line' }).body, 'one line', 'no list at all is no environment')
  const empty = feedback.compose({ kind: 'other', text: ' \n ', environment: ENVIRONMENT })
  assert.equal(empty.ready, false)
  assert.equal(empty.title, '[feedback]')
  assert.ok(empty.body.startsWith('---\nEnvironment'), 'the command hands over the environment alone')
  assert.deepEqual(filled(empty.url).body, empty.body)
  for (const nothing of [undefined, null, {}]) assert.equal(feedback.compose(nothing).ready, false)
})

test('compose(): half a character pair does not break the address, and the text is capped at 4000 characters', () => {
  const broken = feedback.compose({ kind: 'idea', text: 'a\uD83Db\uDE00c' })
  assert.equal(broken.body, 'a\uFFFDb\uFFFDc')
  assert.equal(filled(broken.url).body, broken.body)
  assert.equal(feedback.MAX_TEXT, 4000)
  assert.equal(Array.from(feedback.cleanText('😀'.repeat(4100))).length, 4000)
  assert.equal(feedback.cleanText('😀'.repeat(4100)), '😀'.repeat(4000))
})

test('compose(): a report too long for an address is cut in the address only, marked, and keeps its environment', () => {
  for (const [name, text] of [['Chinese', '长'.repeat(3000)], ['emoji', '😀'.repeat(1500)], ['mixed lines', ('line of text 第一行\n').repeat(200)]]) {
    const report = feedback.compose({ kind: 'idea', text, environment: ENVIRONMENT, cutNote: '（截断）' })
    assert.equal(report.clipped, true, name)
    assert.ok(report.url.length <= feedback.MAX_URL, `${name}: ${report.url.length}`)
    assert.ok(report.url.length > feedback.MAX_URL - 40, `${name}: as much of the text as fits (${report.url.length})`)
    assert.ok(report.body.startsWith(text.trim()), `${name}: the whole report still holds the whole text`)
    const { title, body } = filled(report.url)
    assert.equal(title, report.title)
    const [kept, rest] = body.split('\n\n（截断）\n\n')
    assert.ok(kept.length > 100 && text.startsWith(kept), `${name}: the address holds a beginning of the text`)
    assert.ok(!kept.includes('\uFFFD'), `${name}: no character pair was split`)
    assert.equal(rest, '---\nEnvironment (added by dsh-feiyu-memgate)\n' + ENVIRONMENT.map((row) => `- ${row[0]}: ${row[1]}`).join('\n'))
  }
  const plainNote = feedback.compose({ kind: 'idea', text: '长'.repeat(3000) })
  assert.ok(filled(plainNote.url).body.endsWith('(Cut here: the text was too long for a link. Paste the whole text over this.)'), 'a note in English when none is given')
  assert.equal(feedback.compose({ kind: 'idea', text: 'x'.repeat(4000), environment: ENVIRONMENT }).clipped, false, 'plain letters of the full length fit')
})

// ------------------------------------------------------------------ the clipboard

test('copyText(): the clipboard interface when there is one, the selection way when it refuses, and an honest false', async () => {
  const written = []
  assert.equal(await copyText('report', { navigator: { clipboard: { writeText: async (text) => { written.push(text) } } }, document: undefined }), true)
  assert.deepEqual(written, ['report'])

  const fakeDocument = (answer) => {
    const log = []
    const body = { appendChild: (node) => log.push(['append', node.value]), removeChild: (node) => log.push(['remove', node.value]) }
    const createElement = (tag) => ({ tag, style: {}, attributes: {}, setAttribute(name, value) { this.attributes[name] = value }, select() { log.push(['select']) } })
    const execCommand = (command) => {
      log.push(['exec', command])
      if (answer instanceof Error) throw answer
      return answer
    }
    return { log, body, createElement, execCommand }
  }
  const refusing = { clipboard: { writeText: async () => { throw new Error('not allowed') } } }
  const through = fakeDocument(true)
  assert.equal(await copyText('report', { navigator: refusing, document: through }), true)
  assert.deepEqual(through.log, [['append', 'report'], ['select'], ['exec', 'copy'], ['remove', 'report']])
  const throwing = { clipboard: { writeText: () => { throw new Error('sync') } } }
  assert.equal(await copyText('report', { navigator: throwing, document: fakeDocument(true) }), true, 'a clipboard that throws at once')

  const declined = fakeDocument(false)
  assert.equal(await copyText('report', { navigator: undefined, document: declined }), false)
  assert.deepEqual(declined.log.at(-1), ['remove', 'report'], 'the helper element is taken out again')
  const failing = fakeDocument(new Error('blocked'))
  assert.equal(await copyText('report', { navigator: {}, document: failing }), false)
  assert.deepEqual(failing.log.at(-1), ['remove', 'report'])
  assert.equal(await copyText('report', { navigator: undefined, document: undefined }), false)
  assert.equal(await copyText('report', { navigator: refusing, document: {} }), false)
})

// ------------------------------------------------------------------ /memgate feedback

function command({ lang = 'en', statusData = status() } = {}) {
  const writes = []
  const made = createMemgateCommand({
    settings: () => readConfig({}),
    language: () => lang,
    status: async () => statusData,
    write: async (ops) => { writes.push(ops); return { ok: true } },
  })
  return { made, writes, run: (rawInput) => made.handler({ rawInput, attachments: [], signal: new AbortController().signal }) }
}

test('/memgate feedback prints the new-issue address with the environment filled in, in both languages, and writes nothing', async () => {
  for (const lang of ['en', 'zh']) {
    const { run, writes } = command({ lang })
    const reply = await run('feedback')
    assert.equal(reply.kind, 'success')
    const url = reply.text.split('\n').find((line) => line.startsWith('https://'))
    assert.ok(url, 'the address stands on a line of its own')
    const { page, title, body } = filled(url)
    assert.equal(page, LINKS.newIssue)
    assert.equal(title, '[feedback]')
    assert.equal(body, '---\nEnvironment (added by dsh-feiyu-memgate)\n' + ENVIRONMENT.map((row) => `- ${row[0]}: ${row[1]}`).join('\n'))
    for (const word of PRIVATE) assert.ok(!decodeURIComponent(reply.text).includes(word), `${lang}: the reply carries "${word}"`)
    assert.match(reply.text, lang === 'zh' ? /公开/ : /public/)
    assert.match(reply.text, lang === 'zh' ? /不发送任何内容/ : /sends nothing/)
    assert.deepEqual(writes, [])
  }
  assert.equal(command({ statusData: null }).made.name, 'memgate')
  const bare = await command({ statusData: null }).run('feedback')
  assert.deepEqual(filled(bare.text.split('\n').find((line) => line.startsWith('https://'))), { page: LINKS.newIssue, title: '[feedback]', body: '' }, 'no status: the page without an environment')
})

test('/memgate feedback takes no text (it would come back inside the address) and is in the usage and the hint', async () => {
  const { run, made } = command()
  const refused = await run('feedback the recall is slow and my token is abc')
  assert.equal(refused.kind, 'error')
  assert.equal(refused.text, '/memgate feedback takes no arguments.')
  assert.ok(!refused.text.includes('abc'))
  assert.match((await run('')).text, /^ {2}feedback\s/m)
  assert.match((await command({ lang: 'zh' }).run('')).text, /^ {2}feedback\s/m)
  assert.ok(made.input.hint.endsWith('| feedback'))
  assert.equal(made.recordInput, false)
})

// ------------------------------------------------------------------ the Feedback tab

const shared = load()

// The tab drawn directly. `copyState` is what became of the last copy (null, 'copied', 'failed'),
// `sendState` what became of the last Send ({ phase, code }). Without `endpoint` there is no receiver.
function renderTab({ lang = 'en', draft = {}, statusData = status(), copyState = null, sendState, copy, endpoint = '', send, onChange = () => {} } = {}) {
  const loaded = copyState === null && !sendState ? shared : load({
    states: (init) => (init === null ? copyState : sendState && init && typeof init === 'object' && 'phase' in init ? sendState : init),
  })
  const I = loaded.internals
  const t = translatorFor(lang, loaded)
  const tree = expand(I.FeedbackTab({ t, status: { data: statusData }, draft: { ...plain(I.emptyFeedback()), ...draft }, onChange, copy, endpoint, send }))
  return { tree, t, I }
}
const primary = (tree) => withClass(tree, 'fmg-button--primary')[0]
const buttonNamed = (tree, text) => findAll(tree, (n) => n.type === 'button' && textOf(n) === text)[0]

test('a new draft is an idea with nothing written and the environment attached', () => {
  assert.deepEqual(plain(shared.internals.emptyFeedback()), { kind: 'idea', text: '', environment: true })
  assert.notEqual(shared.internals.emptyFeedback(), shared.internals.emptyFeedback(), 'a fresh object each time')
})

test('FeedbackTab: the kind, the text, the environment with every line that is attached, and how it is sent', () => {
  for (const lang of ['en', 'zh']) {
    const { tree, t } = renderTab({ lang })
    assert.equal(tree.type, 'section')
    assert.equal(tree.props['aria-label'], t('feedback.title'))
    const radios = withRole(tree, 'radio')
    assert.deepEqual(radios.map((radio) => radio.props['aria-checked']), ['true', 'false', 'false'])
    assert.deepEqual(radios.map((radio) => textOf(radio).split('\n')[0]), ['idea', 'problem', 'other'].map((kind) => t('feedback.kind.' + kind)))
    const [area] = findAll(tree, (n) => n.type === 'textarea')
    assert.equal(area.props.id, 'feiyu-memgate-feedback-text')
    assert.equal(area.props.value, '')
    assert.equal(area.props.maxLength, 4000)
    assert.equal(findAll(tree, (n) => n.type === 'label' && n.props.htmlFor === area.props.id).length, 0, 'no visible label')
    assert.equal(area.props['aria-label'], t('feedback.text'))
    assert.equal(area.props.placeholder, t('feedback.hint.idea'))
    assert.equal(area.props['aria-describedby'], undefined)
    const [toggle] = withRole(tree, 'switch')
    assert.equal(toggle.props['aria-checked'], 'true')
    assert.equal(toggle.props.disabled, undefined)
    const [reveal] = withClass(tree, 'fmg-reveal')
    assert.equal(reveal.props['data-open'], 'true')
    const terms = findAll(reveal, (n) => n.type === 'dt').map(textOf)
    const values = findAll(reveal, (n) => n.type === 'dd').map(textOf)
    assert.deepEqual(terms.map((term, index) => [term, values[index]]), ENVIRONMENT, 'the lines on screen are the lines of the report')
    const text = textOf(tree)
    for (const key of ['feedback.intro', 'feedback.env.help', 'feedback.how', 'feedback.public', 'feedback.others']) assert.ok(text.includes(t(key)), `${lang}: ${key}`)
    for (const word of PRIVATE) assert.ok(!text.includes(word), `${lang}: the tab shows "${word}"`)
    assert.equal(withClass(tree, 'fmg-notice--warn').length, 0)
  }
})

test('FeedbackTab: with nothing written there is no way out to GitHub and nothing to copy', () => {
  for (const text of ['', '  \n ']) {
    const { tree, t } = renderTab({ draft: { text } })
    const button = primary(tree)
    assert.equal(button.type, 'button')
    assert.equal(button.props.disabled, true)
    assert.equal(textOf(button), t('feedback.submit'))
    assert.equal(buttonNamed(tree, t('feedback.copy')).props.disabled, true)
    assert.deepEqual(findAll(tree, (n) => n.type === 'a').map((a) => a.props.href), [LINKS.issues], 'the only link is the list of issues')
  }
})

test('FeedbackTab: once something is written the primary control is a link to the composed address, in a new tab', () => {
  const { tree, t } = renderTab({ draft: { kind: 'problem', text: 'Recall is slow\nsince 0.3.2' } })
  const link = primary(tree)
  const report = feedback.compose({ kind: 'problem', text: 'Recall is slow\nsince 0.3.2', environment: ENVIRONMENT, cutNote: t('feedback.cutNote') })
  assert.equal(link.type, 'a')
  assert.equal(link.props.href, report.url)
  assert.equal(filled(link.props.href).title, '[bug] Recall is slow')
  assert.equal(link.props.target, '_blank')
  assert.deepEqual(link.props.rel.split(' ').sort(), ['noopener', 'noreferrer'])
  assert.equal(link.props.onClick, undefined, 'a report that fits needs no clipboard')
  assert.ok(link.props.className.split(' ').includes('fmg-button'))
  assert.equal(buttonNamed(tree, t('feedback.copy')).props.disabled, undefined)
  assert.equal(findAll(tree, (n) => n.type === 'textarea')[0].props.placeholder, t('feedback.hint.problem'), 'the hint follows the kind')
  for (const a of findAll(tree, (n) => n.type === 'a')) {
    assert.equal(new URL(a.props.href).origin, 'https://github.com')
    assert.equal(a.props.target, '_blank')
  }
})

test('FeedbackTab: the environment is left out when switched off, and cannot be attached while there is no status', () => {
  const off = renderTab({ draft: { text: 'hello', environment: false } })
  assert.equal(withRole(off.tree, 'switch')[0].props['aria-checked'], 'false')
  assert.equal(withClass(off.tree, 'fmg-reveal')[0].props['data-open'], 'false')
  assert.equal(withClass(off.tree, 'fmg-reveal')[0].props['aria-hidden'], 'true')
  assert.equal(filled(primary(off.tree).props.href).body, 'hello')

  const none = renderTab({ draft: { text: 'hello' }, statusData: null })
  const [toggle] = withRole(none.tree, 'switch')
  assert.equal(toggle.props['aria-checked'], 'false')
  assert.equal(toggle.props.disabled, true)
  assert.ok(textOf(none.tree).includes(none.t('feedback.env.none')))
  assert.ok(!textOf(none.tree).includes(none.t('feedback.env.help')))
  assert.equal(filled(primary(none.tree).props.href).body, 'hello')
  assert.equal(findAll(none.tree, (n) => n.type === 'dt').length, 0)
})

test('FeedbackTab: every control reports its change as a patch of the draft', () => {
  const patches = []
  const { tree } = renderTab({ onChange: (patch) => patches.push(plain(patch)) })
  withRole(tree, 'radio')[1].props.onClick()
  findAll(tree, (n) => n.type === 'textarea')[0].props.onChange({ target: { value: 'typed' } })
  withRole(tree, 'switch')[0].props.onClick()
  assert.deepEqual(patches, [{ kind: 'problem' }, { text: 'typed' }, { environment: false }])
})

test('FeedbackTab: Copy puts the whole report on the clipboard, and says whether it arrived', async () => {
  const copied = []
  const { tree, t } = renderTab({ lang: 'zh', draft: { text: '第一行\n第二行' }, copy: async (text) => { copied.push(text); return true } })
  buttonNamed(tree, t('feedback.copy')).props.onClick()
  await tick()
  const report = feedback.compose({ kind: 'idea', text: '第一行\n第二行', environment: ENVIRONMENT })
  assert.deepEqual(copied, [report.title + '\n\n' + report.body])
  assert.equal(withRole(tree, 'status').length, 0, 'nothing is said before a copy')

  const done = renderTab({ draft: { text: 'x' }, copyState: 'copied' })
  const [said] = withRole(done.tree, 'status')
  assert.equal(textOf(said), done.t('feedback.copied'))
  assert.equal(said.props.className, 'fmg-quiet')
  const failed = renderTab({ draft: { text: 'x' }, copyState: 'failed' })
  const [sorry] = withRole(failed.tree, 'status')
  assert.equal(textOf(sorry), failed.t('feedback.copyFailed'))
  assert.equal(sorry.props.className, 'fmg-row__error')
})

test('FeedbackTab: a report too long for an address says so, and the way out copies the whole report first', async () => {
  const copied = []
  const text = '长'.repeat(3000)
  const { tree, t } = renderTab({ lang: 'zh', draft: { text }, copy: async (value) => { copied.push(value); return true } })
  const [warning] = withClass(tree, 'fmg-notice--warn')
  assert.equal(textOf(warning), t('feedback.clipped'))
  assert.equal(warning.props.role, 'status')
  const link = primary(tree)
  assert.equal(link.type, 'a')
  assert.ok(link.props.href.length <= feedback.MAX_URL)
  assert.ok(filled(link.props.href).body.includes(t('feedback.cutNote')), 'the cut is marked in the page language')
  link.props.onClick()
  await tick()
  assert.equal(copied.length, 1)
  assert.ok(copied[0].includes(text), 'the clipboard holds the whole text')
  assert.ok(copied[0].endsWith('- language: zh'))
})

test('FeedbackTab: the count of characters appears only near the limit', () => {
  assert.ok(!textOf(renderTab({ draft: { text: 'x'.repeat(3199) } }).tree).includes('3199'))
  const near = renderTab({ draft: { text: '字'.repeat(3200) } })
  assert.ok(textOf(near.tree).includes(near.t('feedback.count', { count: 3200, max: 4000 })))
  assert.ok(textOf(renderTab({ lang: 'zh', draft: { text: '😀'.repeat(3300) } }).tree).includes('3300 / 4000 字'), 'counted in characters')
})

test('the page: Feedback is the last tab, works without a settings form, and the footer leads to it', () => {
  for (const form of [undefined, { state: { status: 'unavailable' } }, { state: { status: 'loading' } }]) {
    const loaded = load({ states: (init) => (init === 'overview' ? 'feedback' : init) })
    const t = translatorFor('en', loaded)
    const tree = expand(loaded.internals.SettingsPage({ view: 'page', t, form, fetch: async () => ({ ok: false, status: 503 }) }))
    const tabs = withRole(tree, 'tab')
    assert.equal(tabs.at(-1).props.id, 'feiyu-memgate-tab-feedback')
    assert.equal(tabs.at(-1).props['aria-selected'], 'true')
    const [panel] = withRole(tree, 'tabpanel')
    assert.equal(panel.props.id, 'feiyu-memgate-panel-feedback')
    assert.equal(findAll(panel, (n) => n.type === 'textarea').length, 1)
    assert.ok(!textOf(panel).includes(t('unavailable')) && !textOf(panel).includes(t('loading')))
    // No status has arrived in this first state: there is nothing to attach yet.
    assert.equal(withRole(panel, 'switch')[0].props.disabled, true)
  }
  const tree = expand(shared.internals.SettingsPage({ view: 'page', t: translatorFor('en'), fetch: async () => ({ ok: false, status: 503 }) }))
  const [footer] = findAll(tree, (n) => n.type === 'footer')
  const [toFeedback] = findAll(footer, (n) => n.type === 'button')
  assert.equal(textOf(toFeedback), 'Give feedback')
  assert.doesNotThrow(() => toFeedback.props.onClick(), 'outside a browser there is no tab to focus, and that is fine')
})

// ------------------------------------------------------------------ sending straight to the receiver

const ENDPOINT = 'https://receiver.example/'
// The receiver, recorded: every call with its address and options, answered as `reply` says.
function receiver(reply = { ok: true, status: 200 }) {
  const calls = []
  const send = async (url, init) => {
    calls.push({ url, init })
    if (reply instanceof Error) throw reply
    return reply
  }
  return { calls, send }
}

test('usableEndpoint(): an https address or one on this computer, and nothing else', () => {
  for (const good of ['https://receiver.example/', 'https://receiver.example', 'https://a.b.workers.dev/v1/feedback', 'http://localhost:47931/preview-feedback?mode=direct', 'http://127.0.0.1/x']) {
    assert.equal(feedback.usableEndpoint(good), good)
  }
  for (const bad of ['', 'http://receiver.example/', 'http://localhost.evil.example/', 'ftp://x/', 'javascript:alert(1)', '//receiver.example/', 'https://', 'https:// receiver.example/', '/relative', null, undefined, 5, {}]) {
    assert.equal(feedback.usableEndpoint(bad), '', String(bad))
  }
})

test('payload(): the report as it is posted, as it is posted', () => {
  assert.deepEqual(feedback.payload({ kind: 'problem', text: ' a\r\nb ', environment: true }, ENVIRONMENT), {
    v: 1, kind: 'problem', text: 'a\nb', environment: ENVIRONMENT,
  })
  assert.deepEqual(feedback.payload({ kind: 'idea', text: 'x' }, []), { v: 1, kind: 'idea', text: 'x', environment: [] })
  assert.equal(feedback.MAX_CONTACT, undefined)
  assert.equal(feedback.cleanContact, undefined)
  assert.equal('contact' in feedback.payload({ kind: 'idea', text: 'x', contact: 'me' }, []), false, 'a contact line is not part of the report')
})

test('sendFeedback(): one POST without cookie, credential or referrer; a refusal, an unreachable receiver and a timeout each have a code', async (t) => {
  const { sendFeedback } = require('../client/src/net.cjs')
  const { FEEDBACK_TIMEOUT_MS } = require('../client/src/constants.cjs')
  const taken = receiver()
  assert.equal(await sendFeedback(taken.send, ENDPOINT, { v: 1, kind: 'idea', text: 'x' }), true)
  const [{ url, init }] = taken.calls
  assert.equal(url, ENDPOINT)
  const { signal, ...rest } = init
  assert.deepEqual(rest, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: '{"v":1,"kind":"idea","text":"x"}',
    mode: 'cors',
    credentials: 'omit',
    referrerPolicy: 'no-referrer',
    cache: 'no-store',
  })
  assert.equal(signal.aborted, false)
  for (const status of [400, 429, 502, 503]) {
    await assert.rejects(() => sendFeedback(receiver({ ok: false, status }).send, ENDPOINT, {}), (error) => error.code === 'HTTP ' + status)
  }
  await assert.rejects(() => sendFeedback(receiver(new TypeError('Failed to fetch')).send, ENDPOINT, {}), (error) => error.code === 'network')
  await assert.rejects(() => sendFeedback(async () => { throw 'not an error' }, ENDPOINT, {}), (error) => error instanceof Error && error.code === 'network')

  // A receiver that never answers: the page gives up after FEEDBACK_TIMEOUT_MS and says so.
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const hanging = (url, init) => new Promise((resolve, reject) => init.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))
  const pending = assert.rejects(() => sendFeedback(hanging, ENDPOINT, {}), (error) => error.code === 'timeout')
  t.mock.timers.tick(FEEDBACK_TIMEOUT_MS)
  await pending
  t.mock.timers.reset()
})

test('FeedbackTab without a receiver: no Send, and the way to GitHub is the main action', () => {
  for (const endpoint of ['', 'http://receiver.example/', 'javascript:alert(1)']) {
    const { tree, t } = renderTab({ draft: { text: 'hello' }, endpoint })
    assert.equal(buttonNamed(tree, t('feedback.send')), undefined)
    assert.equal(findAll(tree, (n) => n.type === 'input').length, 0)
    assert.equal(primary(tree).type, 'a')
    const text = textOf(tree)
    assert.ok(text.includes(t('feedback.how')) && text.includes(t('feedback.public')))
    assert.ok(!text.includes(t('feedback.howDirect')))
  }
  // What ships: the tab of the page as the package has it follows the constant.
  const { FEEDBACK_ENDPOINT } = require('../client/src/constants.cjs')
  const shipped = expand(shared.internals.FeedbackTab({ t: translatorFor('en'), status: { data: status() }, draft: { kind: 'idea', text: 'hello', environment: true }, onChange() {} }))
  assert.equal(Boolean(buttonNamed(shipped, 'Send')), FEEDBACK_ENDPOINT !== '', 'Send is offered exactly when the package names a receiver')
})

test('FeedbackTab with a receiver: write, then Send; nothing else to choose from', () => {
  for (const lang of ['en', 'zh']) {
    const { tree, t } = renderTab({ lang, draft: { text: 'hello' }, endpoint: ENDPOINT })
    const main = primary(tree)
    assert.equal(main.type, 'button')
    assert.equal(textOf(main), t('feedback.send'))
    assert.equal(main.props.disabled, undefined)
    const [actions] = withClass(tree, 'fmg-actions')
    assert.deepEqual(findAll(actions, (n) => n.type === 'button' || n.type === 'a').map(textOf), [t('feedback.send')], 'Send is the only action')
    assert.equal(findAll(tree, (n) => n.type === 'a').length, 0, 'no link leads away from the page')
    assert.equal(buttonNamed(tree, t('feedback.copy')), undefined)
    assert.ok(!textOf(tree).includes(t('feedback.submit')) && !textOf(tree).includes(t('feedback.others')))
    assert.equal(findAll(tree, (n) => n.type === 'input').length, 0, 'the tab has no text line besides the text box')
    const text = textOf(tree)
    assert.ok(text.includes(t('feedback.howDirect')), lang)
    assert.ok(!text.includes(t('feedback.how')) && !text.includes(t('feedback.public')), 'the text about GitHub is not shown')
  }
  const empty = renderTab({ endpoint: ENDPOINT })
  assert.equal(primary(empty.tree).props.disabled, true, 'nothing written, nothing to send')
  // A long report needs no warning here: the warning is about the address for GitHub.
  const long = renderTab({ draft: { text: '长'.repeat(3000) }, endpoint: ENDPOINT })
  assert.equal(withClass(long.tree, 'fmg-notice--warn').length, 0)
  assert.equal(primary(long.tree).props.disabled, undefined)
})

test('FeedbackTab: Send posts the report to the receiver once, and clears the text when it was taken', async () => {
  const taken = receiver()
  const patches = []
  const draft = { kind: 'problem', text: ' Recall is slow\r\nsince 0.3.2 ' }
  const { tree } = renderTab({ draft, endpoint: ENDPOINT, send: taken.send, onChange: (patch) => patches.push(plain(patch)) })
  primary(tree).props.onClick()
  primary(tree).props.onClick() // an impatient second click while the first is on its way
  await tick()
  await tick()
  assert.equal(taken.calls.length, 1)
  assert.equal(taken.calls[0].url, ENDPOINT)
  assert.equal(taken.calls[0].init.credentials, 'omit')
  assert.deepEqual(JSON.parse(taken.calls[0].init.body), {
    v: 1, kind: 'problem', text: 'Recall is slow\nsince 0.3.2', environment: ENVIRONMENT,
  })
  assert.deepEqual(patches, [{ text: '' }], 'the kind and the switch stay as they are')

  const without = receiver()
  const off = renderTab({ draft: { text: 'x', environment: false }, endpoint: ENDPOINT, send: without.send })
  primary(off.tree).props.onClick()
  await tick()
  assert.deepEqual(JSON.parse(without.calls[0].init.body).environment, [], 'switched off, nothing is attached')
  for (const word of PRIVATE) assert.ok(!taken.calls[0].init.body.includes(word), `the report would carry "${word}"`)
})

test('FeedbackTab: a report the receiver did not take stays in the box', async () => {
  for (const reply of [{ ok: false, status: 502 }, { ok: false, status: 429 }, new TypeError('Failed to fetch')]) {
    const failing = receiver(reply)
    const patches = []
    const { tree } = renderTab({ draft: { text: 'keep me' }, endpoint: ENDPOINT, send: failing.send, onChange: (patch) => patches.push(patch) })
    primary(tree).props.onClick()
    await tick()
    await tick()
    assert.equal(failing.calls.length, 1)
    assert.deepEqual(patches, [])
  }
})

test('FeedbackTab: what became of a Send is said: on its way, taken, refused, too many', () => {
  const draw = (sendState, draft = { text: 'hello' }, lang = 'en') => renderTab({ lang, draft, endpoint: ENDPOINT, sendState })
  const sending = draw({ phase: 'sending', code: null })
  assert.equal(textOf(primary(sending.tree)), sending.t('feedback.sending'))
  assert.equal(primary(sending.tree).props.disabled, true)
  assert.equal(findAll(sending.tree, (n) => n.type === 'textarea')[0].props.disabled, true)
  assert.equal(findAll(sending.tree, (n) => n.type === 'input').length, 0, 'there is no line input on the tab')
  assert.ok(withRole(sending.tree, 'radio').every((radio) => radio.props.disabled === true))

  for (const lang of ['en', 'zh']) {
    const sent = draw({ phase: 'sent', code: null }, { text: '' }, lang)
    assert.ok(withRole(sent.tree, 'status').some((node) => textOf(node) === sent.t('feedback.sent')), lang)
    const again = draw({ phase: 'sent', code: null }, { text: 'a second thought' }, lang)
    assert.ok(!textOf(again.tree).includes(again.t('feedback.sent')), 'the thanks go when the person writes again')

    const failed = draw({ phase: 'failed', code: 'HTTP 502' }, { text: 'hello' }, lang)
    const [alert] = withRole(failed.tree, 'alert')
    assert.ok(textOf(alert).startsWith(failed.t('feedback.failed', { code: 'HTTP 502' })))
    assert.ok(alert.props.className.includes('fmg-notice--warn'))
    assert.equal(primary(failed.tree).props.disabled, undefined, 'it can be tried again')
    // The other two ways come with the failure, inside its notice, so what was written is not stuck.
    const [github] = findAll(alert, (n) => n.type === 'a')
    assert.equal(github.props.className, 'fmg-button')
    assert.equal(textOf(github), failed.t('feedback.submit'))
    assert.equal(filled(github.props.href).title, '[idea] hello')
    assert.equal(buttonNamed(alert, failed.t('feedback.copy')).props.disabled, undefined)
    assert.equal(findAll(failed.tree, (n) => n.type === 'a').length, 1, 'and only there')
    assert.ok(textOf(withRole(draw({ phase: 'failed', code: 'HTTP 429' }, { text: 'hello' }, lang).tree, 'alert')).startsWith(failed.t('feedback.failedBusy')))
    assert.ok(textOf(withRole(draw({ phase: 'failed', code: 'timeout' }, { text: 'hello' }, lang).tree, 'alert')).includes('timeout'))
  }
  assert.equal(withRole(draw({ phase: 'ready', code: null }).tree, 'alert').length, 0)
  // After a failure a long report gets the warning about the address, next to the link it is about.
  const long = draw({ phase: 'failed', code: 'network' }, { text: '长'.repeat(3000) })
  assert.equal(withClass(long.tree, 'fmg-notice--warn').length, 2)
})

test('FeedbackTab: after a failed Send, Copy takes the report', async () => {
  const copied = []
  const { tree, t } = renderTab({
    draft: { text: 'hello' }, endpoint: ENDPOINT, sendState: { phase: 'failed', code: 'network' },
    copy: async (text) => { copied.push(text); return true },
  })
  buttonNamed(withRole(tree, 'alert')[0], t('feedback.copy')).props.onClick()
  await tick()
  assert.equal(copied.length, 1)
  assert.ok(copied[0].startsWith('[idea] hello'))
})

test('the page hands its feedback options to the tab: a receiver, its fetch and the clipboard', async () => {
  const taken = receiver()
  const loaded = load({ states: (init) => (init === 'overview' ? 'feedback' : init && typeof init === 'object' && init.kind === 'idea' ? { ...init, text: 'from the page' } : init) })
  const t = translatorFor('en', loaded)
  const tree = expand(loaded.internals.SettingsPage({ view: 'page', t, fetch: async () => ({ ok: false, status: 503 }), feedback: { endpoint: ENDPOINT, send: taken.send } }))
  const [panel] = withRole(tree, 'tabpanel')
  primary(panel).props.onClick()
  await tick()
  assert.equal(taken.calls.length, 1)
  assert.equal(JSON.parse(taken.calls[0].init.body).text, 'from the page')
})

// ------------------------------------------------------------------ the drawing at the head of the tab

test('FeedbackTab: the head holds the drawing of the mascot and the cat, with its bubble once the picture has loaded', () => {
  // Nag keeps one object { loaded, failed }; the other hooks of the tab hold other values.
  const isArt = (init) => init && typeof init === 'object' && 'loaded' in init && 'failed' in init
  const draw = (lang, art) => {
    const loaded = load({ states: (init) => (isArt(init) && art ? { ...init, ...art } : init) })
    const t = translatorFor(lang, loaded)
    const tree = expand(loaded.internals.FeedbackTab({ t, status: { data: status() }, draft: { ...plain(loaded.internals.emptyFeedback()) }, onChange: () => {}, endpoint: '' }))
    return { tree, t }
  }
  for (const lang of ['en', 'zh']) {
    const before = draw(lang)
    const [head] = withClass(before.tree, 'fmg-feedback__head')
    assert.ok(head.props.className.split(' ').includes('fmg-section__head'))
    assert.equal(withClass(head, 'fmg-feedback__intro').length, 1, 'title and intro stay together')
    const [img] = findAll(before.tree, (n) => n.type === 'img')
    assert.equal(img.props.src, 'feiyu-memgate/art/mascot-nag.webp', 'document-relative, no leading slash')
    assert.equal(img.props.alt, before.t('mascot.nag'))
    assert.equal(img.props.width, 360)
    assert.equal(img.props.height, 378)
    assert.equal(img.props.draggable, false)
    assert.equal(img.props['data-shown'], 'false')
    assert.equal(withClass(before.tree, 'fmg-nag__bubble').length, 0, 'no bubble over a picture that is not there yet')

    const after = draw(lang, { loaded: true })
    assert.equal(findAll(after.tree, (n) => n.type === 'img')[0].props['data-shown'], 'true')
    const [bubble] = withClass(after.tree, 'fmg-nag__bubble')
    assert.equal(textOf(bubble), after.t('feedback.nag'))
    assert.equal(bubble.props['aria-hidden'], 'true', 'the alt text already says it')
  }
  assert.equal(textOf(withClass(draw('zh', { loaded: true }).tree, 'fmg-nag__bubble')[0]), '快更新！')

  // A picture that failed: no drawing, no bubble, and the rest of the tab is untouched.
  const failed = draw('en', { failed: true })
  assert.equal(withClass(failed.tree, 'fmg-nag').length, 0)
  assert.equal(findAll(failed.tree, (n) => n.type === 'img').length, 0)
  assert.equal(withClass(failed.tree, 'fmg-nag__bubble').length, 0)
  const text = textOf(failed.tree)
  assert.ok(text.includes(failed.t('feedback.title')))
  assert.equal(findAll(failed.tree, (n) => n.type === 'textarea').length, 1)
  assert.ok(findAll(failed.tree, (n) => n.type === 'button' || n.type === 'a').some((n) => textOf(n).includes(failed.t('feedback.submit'))), 'the way to send is still there')
})
