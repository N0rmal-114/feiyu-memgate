// The token rule of the worker: the special tokens at both ends as Ollama has them (with <s> before
// bge-m3 style input), and truncation that keeps the head and both special tokens.
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { inputTokenizer } from '../src/tokens.js'

// Token ids: a character is its code point; bos 1, eos 2, sep 3.
const fakeModel = (vocabularyType, { bos = 1, eos = 2, sep = 3, prepend = false, append = false } = {}) => {
  const calls = []
  return {
    calls,
    vocabularyType,
    tokens: { bos, eos, sep, shouldPrependBosToken: prepend, shouldAppendEosToken: append },
    tokenize: (text, special) => {
      calls.push(special)
      return [...text].map((c) => c.codePointAt(0))
    },
  }
}
const run = (model, text, limit) => inputTokenizer(model)(text, limit)

describe('inputTokenizer: special tokens', () => {
  it('BERT word pieces (bge zh): [CLS] text [SEP]', () => {
    assert.deepEqual(run(fakeModel('wpm'), 'ab'), { tokens: [1, 97, 98, 3], count: 4, truncated: false })
  })

  it('unigram with add_bos (bge-m3): <s> text </s>; without add_bos: text </s> (T5)', () => {
    const m = fakeModel('ugm', { prepend: true })
    assert.deepEqual(run(m, 'ab').tokens, [1, 97, 98, 2])
    assert.deepEqual(m.calls, [false], 'special-token text in the input is not parsed')
    assert.deepEqual(run(fakeModel('ugm'), 'ab').tokens, [97, 98, 2])
  })

  it('BPE (qwen3): as the GGUF says, here text <|endoftext|>', () => {
    assert.deepEqual(run(fakeModel('bpe', { append: true }), 'ab').tokens, [97, 98, 2])
    assert.deepEqual(run(fakeModel('bpe', { prepend: true, append: true }), 'ab').tokens, [1, 97, 98, 2])
    assert.deepEqual(run(fakeModel('spm'), 'ab').tokens, [97, 98])
  })

  it('a model without such a token gets none', () => {
    assert.deepEqual(run(fakeModel('ugm', { prepend: true, bos: null }), 'ab').tokens, [97, 98, 2])
    assert.deepEqual(run(fakeModel('wpm', { sep: -1 }), 'ab').tokens, [1, 97, 98])
    assert.deepEqual(run(fakeModel('rwkv'), 'ab').tokens, [97, 98])
  })
})

describe('inputTokenizer: truncation', () => {
  it('keeps the head and both special tokens, exactly `limit` long', () => {
    const r = run(fakeModel('wpm'), 'abcdefgh', 6)
    assert.deepEqual(r, { tokens: [1, 97, 98, 99, 100, 3], count: 10, truncated: true })
    const q = run(fakeModel('bpe', { append: true }), 'abcdefgh', 6)
    assert.deepEqual(q.tokens, [97, 98, 99, 100, 101, 2])
  })

  it('leaves text that fits alone, an exact fit included', () => {
    assert.deepEqual(run(fakeModel('wpm'), 'abcd', 6), { tokens: [1, 97, 98, 99, 100, 3], count: 6, truncated: false })
  })

  it('returns a new array every time', () => {
    const fn = inputTokenizer(fakeModel('ugm', { prepend: true }))
    fn('a').tokens.push(9)
    assert.deepEqual(fn('a').tokens, [1, 97, 2])
  })
})
