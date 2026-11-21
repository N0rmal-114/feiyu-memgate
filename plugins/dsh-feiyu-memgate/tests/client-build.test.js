// The assembly of the settings page script (scripts/build-client.mjs): the registry the modules run in,
// the collection of the source files, and the output being the same on every platform.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import vm from 'node:vm'
import { build, bundle, collect } from '../scripts/build-client.mjs'

// Run a bundle the way the page does and return what its factory returns (the exports of index.cjs).
function run(modules, hostRequire = () => { throw new Error('no host') }) {
  let registered = null
  const context = { window: { __ModuleLoader__: { load: (record) => { registered = record } } } }
  vm.runInNewContext(bundle(modules), context, { filename: 'bundle.js' })
  assert.equal(registered.id, 'dsh-feiyu-memgate')
  return registered.factory(hostRequire)
}

const mod = (id, text) => ({ id, text })

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'client-build-'))
}

test('a module reaches its neighbours with ./ and ../ relative to its own id', () => {
  const exports = run([
    mod('index.cjs', "const x = require('./a/x.cjs'); const top = require('./top.cjs'); module.exports = { x, top }"),
    mod('a/x.cjs', "module.exports = require('../top.cjs') + require('./y.cjs') + require('./deep/z.cjs')"),
    mod('a/y.cjs', "module.exports = 'y'"),
    mod('a/deep/z.cjs', "module.exports = require('../../top.cjs') + require('../y.cjs')"),
    mod('top.cjs', "module.exports = 't'"),
  ])
  assert.deepEqual({ ...exports }, { x: 'tyty', top: 't' })
})

test('a module runs once, and a cycle gets the exports filled so far', () => {
  const host = { runs: 0 }
  const exports = run([
    mod('index.cjs', "const a = require('./a.cjs'); const b = require('./b.cjs'); module.exports = { a, b, again: require('./a.cjs') === a, runs: require('count').runs }"),
    mod('a.cjs', "require('count').runs += 1; exports.early = 1; const b = require('./b.cjs'); exports.sawB = b.sawEarly; exports.late = 2"),
    mod('b.cjs', "const a = require('./a.cjs'); exports.sawEarly = a.early === 1 && a.late === undefined"),
  ], (id) => { if (id === 'count') return host; throw new Error(id) })
  assert.equal(exports.again, true)
  assert.equal(exports.runs, 1)
  assert.deepEqual({ ...exports.a }, { early: 1, sawB: true, late: 2 })
  assert.equal(exports.b.sawEarly, true)
})

test('a module that is not in the bundle throws an error that names it, and is not cached half-run', () => {
  assert.throws(() => run([mod('index.cjs', "require('./nope.cjs')")]), /^Error: dsh-feiyu-memgate: no client module nope\.cjs$/)
  assert.throws(() => run([mod('index.cjs', "require('../up.cjs')")]), /no client module up\.cjs/)
  assert.throws(() => run([]), /no client module index\.cjs/)
  // A module that throws while running is loaded again on the next require instead of answering half-filled.
  let attempts = 0
  const exports = run([
    mod('index.cjs', "let first = null; try { require('./bad.cjs') } catch (e) { first = e.message } let second = null; try { require('./bad.cjs') } catch (e) { second = e.message } module.exports = { first, second }"),
    mod('bad.cjs', "require('tick')(); throw new Error('boom')"),
  ], (id) => { if (id === 'tick') return () => { attempts += 1 }; throw new Error(id) })
  assert.deepEqual({ ...exports }, { first: 'boom', second: 'boom' })
  assert.equal(attempts, 2)
})

test('names that do not start with ./ or ../ go to the host require, with the name as written', () => {
  const asked = []
  const exports = run([
    mod('index.cjs', "module.exports = { react: require('react'), ui: require('@deepseek-ai/dsh-client-ui-primitives'), dot: require('.hidden') }"),
  ], (id) => { asked.push(id); return { id } })
  assert.deepEqual(asked, ['react', '@deepseek-ai/dsh-client-ui-primitives', '.hidden'])
  assert.deepEqual({ ...exports }.react, { id: 'react' })
})

test('a .css file is a module that exports its text as a string', () => {
  const css = '.page {\n  color: red;\n}\n/* "quotes", back\\slash,   */\n'
  const exports = run([
    mod('index.cjs', "module.exports = require('./style/page.css')"),
    mod('style/page.css', css),
  ])
  assert.equal(exports, css)
  assert.match(bundle([mod('style/page.css', css)]), /define\("style\/page\.css", function \(require, module\) \{/)
})

test('the order of the files does not change what the bundle does', () => {
  const modules = [
    mod('index.cjs', "module.exports = { sum: require('./m/a.cjs') + require('./m/b.cjs'), css: require('./s.css').length }"),
    mod('m/a.cjs', "module.exports = require('./b.cjs') * 2"),
    mod('m/b.cjs', 'module.exports = 21'),
    mod('s.css', 'a{}'),
  ]
  const expected = { sum: 63, css: 3 }
  for (const order of [[0, 1, 2, 3], [3, 2, 1, 0], [2, 0, 3, 1], [1, 3, 0, 2]]) {
    assert.deepEqual({ ...run(order.map((i) => modules[i])) }, expected, order.join())
  }
})

test('the output has LF line endings only, and is the same for CRLF sources', () => {
  const lf = [mod('index.cjs', "'use strict'\n\n// a\nmodule.exports = require('./a.cjs')\n"), mod('a.cjs', "module.exports = 'a'\n"), mod('s.css', 'a {\n}\n')]
  const crlf = lf.map(({ id, text }) => mod(id, text.replace(/\n/g, '\r\n')))
  assert.ok(!bundle(lf).includes('\r'))
  assert.equal(bundle(crlf), bundle(lf))
  assert.equal(run(crlf).length, 1)
  // collect() reads files the same way.
  const dir = tempDir()
  try {
    fs.mkdirSync(path.join(dir, 'sub'))
    fs.writeFileSync(path.join(dir, 'index.cjs'), "module.exports = 'x'\r\n")
    fs.writeFileSync(path.join(dir, 'sub', 'a.css'), 'a {\r\n}\r\n')
    assert.deepEqual(collect(dir), [{ id: 'index.cjs', text: "module.exports = 'x'\n" }, { id: 'sub/a.css', text: 'a {\n}\n' }])
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('collect() finds .cjs and .css files recursively, ignores the rest, and sorts by code unit', () => {
  const dir = tempDir()
  try {
    for (const rel of ['b.cjs', 'Z.cjs', 'a.cjs', 'z/k.css', 'z/a/deep.cjs', 'notes.md', 'z/data.json', 'm.mjs']) {
      fs.mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true })
      fs.writeFileSync(path.join(dir, rel), 'x')
    }
    assert.deepEqual(collect(dir).map((m) => m.id), ['Z.cjs', 'a.cjs', 'b.cjs', 'z/a/deep.cjs', 'z/k.css'])
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('build() writes the file, says when it is already current, and --check changes nothing', () => {
  const dir = tempDir()
  try {
    const srcDir = path.join(dir, 'src')
    const outFile = path.join(dir, 'client.js')
    fs.mkdirSync(srcDir)
    fs.writeFileSync(path.join(srcDir, 'index.cjs'), "module.exports = { name: 'x' }\n")
    assert.deepEqual(build({ check: true, srcDir, outFile }), { file: outFile, current: false, written: false })
    assert.ok(!fs.existsSync(outFile), 'a check does not write')
    assert.deepEqual(build({ srcDir, outFile }), { file: outFile, current: false, written: true })
    assert.deepEqual(build({ srcDir, outFile }), { file: outFile, current: true, written: false })
    assert.deepEqual(build({ check: true, srcDir, outFile }), { file: outFile, current: true, written: false })
    assert.equal(fs.readFileSync(outFile, 'utf8'), bundle(collect(srcDir)))
    // A file with CRLF line endings (a checkout that converts them) still counts as current.
    fs.writeFileSync(outFile, fs.readFileSync(outFile, 'utf8').replace(/\n/g, '\r\n'))
    assert.equal(build({ check: true, srcDir, outFile }).current, true)
    fs.writeFileSync(path.join(srcDir, 'index.cjs'), "module.exports = { name: 'y' }\n")
    assert.equal(build({ check: true, srcDir, outFile }).current, false)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('the bundle has a header that says it is generated, no absolute path, and one define per module in order', () => {
  const text = bundle([mod('index.cjs', 'module.exports = 1'), mod('x/y.cjs', 'module.exports = 2')])
  assert.match(text, /^\/\/ Generated by scripts\/build-client\.mjs/)
  assert.match(text, /npm run build-client/)
  assert.ok(!/[A-Za-z]:\\|\/Users\/|\/home\//.test(text))
  const ids = [...text.matchAll(/^ {2}define\("([^"]+)"/gm)].map((m) => m[1])
  assert.deepEqual(ids, ['index.cjs', 'x/y.cjs'])
  assert.ok(text.endsWith("  return load('index.cjs')\n} })\n"))
})

test('the real sources assemble to the same text twice', () => {
  assert.equal(bundle(collect()), bundle(collect()))
})
