import test from 'node:test'
import assert from 'node:assert/strict'
import { meaningful, buildQuery, pyStrip, codePointLength } from '../src/query.js'

test('messages that are not worth a lookup', () => {
  for (const text of ['', '   ', '😀😀', '[图片]', '[表情][表情]', '嗯', '哈哈', 'OK', 'ok！', '/help', '／help', '嗯~', '好～', '哈哈~~', '1️⃣']) {
    assert.equal(buildQuery(text, '上一句'), null, text)
  }
  for (const value of [null, undefined, 5, ['a']]) assert.equal(buildQuery(value, 'x'), null)
})

test('a short message takes the previous one', () => {
  assert.equal(buildQuery('那你还记得吗', '我上周去了杭州'), '我上周去了杭州\n那你还记得吗')
  assert.equal(buildQuery('那你还记得吗', null), '那你还记得吗')
  assert.equal(buildQuery('那你还记得吗', '  '), '那你还记得吗')
  assert.equal(buildQuery('我明天要去医院复查一下牙齿', '上一句'), '我明天要去医院复查一下牙齿')
})

test('the cap keeps the current message whole and counts code points', () => {
  const q = buildQuery('那你还记得吗', '长'.repeat(600))
  assert.equal(codePointLength(q), 500)
  assert.ok(q.endsWith('那你还记得吗'))
  assert.equal(codePointLength(buildQuery('𝒳'.repeat(900), null)), 500)
  assert.equal(buildQuery('x'.repeat(900), null, { maxChars: 100 }).length, 100)
  assert.equal(buildQuery('abcdefghij', null, { maxChars: 5 }), 'abcde')
})

test('options', () => {
  assert.equal(buildQuery('ab', 'prev', { fillerChars: 'ab' }), null)
  assert.equal(buildQuery('ab', 'prev'), 'prev\nab')
  assert.equal(buildQuery('hello there', 'before', { shortChars: 3 }), 'hello there')
  assert.equal(buildQuery('hello there', 'before', { shortChars: 30 }), 'before\nhello there')
  assert.equal(buildQuery('!roll something long enough', null, { skipPrefixes: '!' }), null)
  assert.equal(buildQuery('/not a command here', null, { skipPrefixes: '!' }), '/not a command here')
  for (const word of ['OK', 'Thanks!', 'lol']) assert.equal(buildQuery(word, 'prev', { skipWords: ['ok', 'thanks', 'Lol'] }), null, word)
  assert.equal(buildQuery('ok', 'prev', { skipWords: [] }), 'prev\nok')
})

test('meaningful drops noise', () => {
  assert.equal(meaningful('[图片] 你好！😀 ~'), '你好')
  assert.equal(meaningful(null), '')
  assert.equal(meaningful('1️⃣a'), 'a')
})

test('Python whitespace, not JavaScript whitespace', () => {
  assert.equal(pyStrip('\x1c a \x85'), 'a')
  assert.equal(pyStrip('﻿a﻿'), '﻿a﻿')
  assert.equal(pyStrip('　a '), 'a')
})
