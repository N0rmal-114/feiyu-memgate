// The changelog parser (src/changelog.js) and the package's own CHANGELOG.md.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { CUT_MARKER, MAX_BODY_BYTES, compareVersions, parseChangelog, releasedEntries, versionParts } from '../src/changelog.js'

const pkg = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
const real = fs.readFileSync(new URL('../CHANGELOG.md', import.meta.url), 'utf8')

const SAMPLE = [
  '# Changes',
  '',
  'intro text',
  '',
  '## 未发布',
  '',
  '### 新增',
  '',
  '- something new',
  '',
  '## 0.3.2',
  '',
  '日期：2026-11-18。',
  '',
  '### 新增',
  '',
  '- one',
  '  - nested',
  '',
  '## 0.10.0',
  '',
  'ten',
  '',
  '## 0.3.1',
  '',
  'older',
  '',
  '## 0.9.9',
  '',
  'nine',
].join('\n')

test('parseChangelog: sections in file order, version only for a version heading, ### groups stay in the body', () => {
  const sections = parseChangelog(SAMPLE)
  assert.deepEqual(sections.map((s) => [s.version, s.title]), [[null, '未发布'], ['0.3.2', '0.3.2'], ['0.10.0', '0.10.0'], ['0.3.1', '0.3.1'], ['0.9.9', '0.9.9']])
  assert.equal(sections[0].body, '### 新增\n\n- something new')
  assert.equal(sections[1].body, '日期：2026-11-18。\n\n### 新增\n\n- one\n  - nested')
  assert.ok(!sections.some((s) => s.body.includes('## ')) || sections.every((s) => !/^## /m.test(s.body)))
  assert.ok(!sections[0].body.includes('intro text'), 'text before the first section belongs to none')
})

test('parseChangelog: CRLF, a byte order mark and a heading with a v prefix', () => {
  const sections = parseChangelog('﻿## v1.2.3\r\n\r\n日期：2026-01-01。\r\n\r\n- a\r\n\r\n## 1.2.2\r\nb\r\n')
  assert.deepEqual(sections.map((s) => [s.version, s.title]), [['1.2.3', 'v1.2.3'], ['1.2.2', '1.2.2']])
  assert.equal(sections[0].body, '日期：2026-01-01。\n\n- a')
  assert.equal(sections[1].body, 'b')
})

test('parseChangelog: odd input never throws', () => {
  for (const odd of [undefined, null, 5, {}, [], '', '   ', 'no headings at all', '#', '##', '## ', '###  only a group\n- x', '## 1.0\n', '## \n- a']) {
    assert.ok(Array.isArray(parseChangelog(odd)), String(odd))
  }
  assert.deepEqual(parseChangelog('no headings'), [])
  assert.deepEqual(parseChangelog('## 1.0\n'), [{ version: '1.0', title: '1.0', body: '' }])
})

test('versionParts and compareVersions: dotted numbers compared by part, not as text', () => {
  assert.deepEqual(versionParts('0.3.2'), [0, 3, 2])
  assert.equal(versionParts('未发布'), null)
  assert.equal(versionParts('1.2.3-rc.1'), null)
  assert.equal(versionParts(undefined), null)
  assert.equal(compareVersions('0.10.0', '0.9.9'), 1)
  assert.equal(compareVersions('0.3.2', '0.3.10'), -1)
  assert.equal(compareVersions('1.0', '1.0.0'), 0)
  assert.equal(compareVersions('1', '0.99.99'), 1)
  assert.equal(compareVersions('x', '1'), null)
})

test('releasedEntries: only version sections not newer than the current one, newest first', () => {
  assert.deepEqual(releasedEntries(SAMPLE, '0.10.0').map((e) => e.version), ['0.10.0', '0.9.9', '0.3.2', '0.3.1'])
  assert.deepEqual(releasedEntries(SAMPLE, '0.3.2').map((e) => e.version), ['0.3.2', '0.3.1'], 'a newer version is left out')
  assert.deepEqual(releasedEntries(SAMPLE, '0.3.1').map((e) => e.version), ['0.3.1'])
  assert.deepEqual(releasedEntries(SAMPLE, '0.0.1'), [])
  assert.ok(!releasedEntries(SAMPLE, '99.0.0').some((e) => e.version === null), '未发布 is never an entry')
  assert.deepEqual(Object.keys(releasedEntries(SAMPLE, '0.3.2')[0]), ['version', 'title', 'body'])
})

test('releasedEntries: the limit, with 20 as the default', () => {
  assert.deepEqual(releasedEntries(SAMPLE, '1.0.0', 2).map((e) => e.version), ['0.10.0', '0.9.9'])
  const many = Array.from({ length: 30 }, (_, i) => `## 1.${i}.0\n\nbody ${i}`).join('\n\n')
  assert.equal(releasedEntries(many, '1.99.0').length, 20)
  assert.equal(releasedEntries(many, '1.99.0')[0].version, '1.29.0')
  for (const bad of [0, -1, 2.5, 'x', null]) assert.equal(releasedEntries(many, '1.99.0', bad).length, 20, String(bad))
})

test('releasedEntries: a long body is cut at 64 KB with a marker', () => {
  const big = 'é'.repeat(50000) // two bytes each
  const [entry] = releasedEntries(`## 1.0.0\n\n${big}\n\n## 0.9.0\n\nshort`, '1.0.0')
  assert.ok(Buffer.byteLength(entry.body, 'utf8') <= MAX_BODY_BYTES + Buffer.byteLength(CUT_MARKER))
  assert.ok(entry.body.endsWith(CUT_MARKER))
  assert.ok(!entry.body.includes('�'))
  assert.equal(releasedEntries(`## 1.0.0\n\n${big}\n\n## 0.9.0\n\nshort`, '1.0.0')[1].body, 'short')
  assert.equal(releasedEntries('## 1.0.0\n\nsmall', '1.0.0')[0].body, 'small', 'a short body is untouched')
})

test('releasedEntries: odd input and an odd current version give no entries and no error', () => {
  for (const text of [undefined, null, '', 'nothing', 5]) assert.deepEqual(releasedEntries(text, '1.0.0'), [])
  for (const version of [undefined, '', '未发布', 'next', null]) assert.deepEqual(releasedEntries(SAMPLE, version), [])
})

test('the package CHANGELOG.md parses: 未发布 on top is allowed, and its newest released entry is the package version or older', () => {
  const sections = parseChangelog(real)
  assert.ok(sections.length >= 3)
  assert.ok(sections.every((s) => s.body.length > 0))
  const released = releasedEntries(real, pkg.version)
  assert.ok(released.length >= 1)
  assert.ok(compareVersions(released[0].version, pkg.version) <= 0)
  assert.equal(released[0].version, pkg.version, 'the package version has its own section')
  assert.ok(released.every((e, i) => i === 0 || compareVersions(released[i - 1].version, e.version) > 0), 'newest first, no duplicate versions')
  assert.ok(/日期：\d{4}-\d{2}-\d{2}/.test(released[0].body), 'a released entry has a date line')
})
