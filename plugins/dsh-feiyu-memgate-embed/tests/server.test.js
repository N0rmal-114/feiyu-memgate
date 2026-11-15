// The worker's embeddings endpoint (src/server.js) with a fake model: what the gateway's client sends
// and expects, every refusal, and, with FEIYU_MEMGATE_SRC set, the gateway's own HTTP client.
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { after, before, describe, it } from 'node:test'
import { promisify } from 'node:util'
import { createEmbeddingServer } from '../src/server.js'

const KEY = 'k'.repeat(43)
const DIMS = 8
const delay = (ms) => new Promise((r) => setTimeout(r, ms))

// One token per character plus two special tokens; a cut keeps the head, as the worker does.
function fakeEmbedder({ slowMs = 0, seen = [] } = {}) {
  return {
    dimensions: DIMS,
    maxInputTokens: 20,
    prepare(text, limit) {
      const chars = [...text]
      const count = chars.length + 2
      const kept = count > limit ? chars.slice(0, limit - 2) : chars
      return { count, used: kept.length + 2, truncated: count > limit, input: kept.join('') }
    },
    async embed(text) {
      seen.push(text)
      if (slowMs) await delay(slowMs)
      const v = new Array(DIMS).fill(0)
      for (const ch of text) v[ch.codePointAt(0) % DIMS] += 1
      v[0] += 1
      return v
    },
  }
}

async function post(port, body, { key = KEY, path: p = '/v1/embeddings', method = 'POST', raw, headers = {} } = {}) {
  const response = await fetch(`http://127.0.0.1:${port}${p}`, {
    method,
    headers: { 'content-type': 'application/json', ...(key ? { authorization: `Bearer ${key}` } : {}), ...headers },
    body: method === 'GET' ? undefined : raw ?? JSON.stringify(body),
  })
  const text = await response.text()
  let json = null
  try {
    json = JSON.parse(text)
  } catch {
    json = null
  }
  return { status: response.status, json, text, truncated: response.headers.get('x-truncated-inputs') }
}

describe('embeddings endpoint', () => {
  let endpoint
  let port
  before(async () => {
    endpoint = createEmbeddingServer({ key: KEY, modelId: 'bge-small-zh-v1.5', embedder: fakeEmbedder(), limits: { maxBatch: 4, maxBodyBytes: 4096 } })
    port = await endpoint.listen(0)
  })
  after(() => endpoint.close())

  it('answers what the gateway client expects', async () => {
    const r = await post(port, { model: 'bge-small-zh-v1.5', input: ['周末去了动物园', 'hello'] })
    assert.equal(r.status, 200)
    assert.equal(r.json.object, 'list')
    assert.deepEqual(r.json.data.map((d) => d.index), [0, 1])
    for (const d of r.json.data) {
      assert.equal(d.embedding.length, DIMS)
      assert.ok(d.embedding.every((x) => typeof x === 'number' && Number.isFinite(x)))
      const norm = Math.sqrt(d.embedding.reduce((s, x) => s + x * x, 0))
      assert.ok(Math.abs(norm - 1) < 1e-9)
    }
    assert.equal(r.json.model, 'bge-small-zh-v1.5')
    assert.equal(r.json.usage.prompt_tokens, 9 + 7)
  })

  it('accepts a single string', async () => {
    const r = await post(port, { model: 'bge-small-zh-v1.5', input: 'one' })
    assert.equal(r.status, 200)
    assert.equal(r.json.data.length, 1)
  })

  it('adds no prefix: the vector depends on the text only', async () => {
    const a = await post(port, { model: 'bge-small-zh-v1.5', input: ['abc'] })
    const b = await post(port, { model: 'bge-small-zh-v1.5', input: ['abc'] })
    assert.deepEqual(a.json.data[0].embedding, b.json.data[0].embedding)
  })

  const refusals = [
    ['no key', { model: 'bge-small-zh-v1.5', input: ['x'] }, { key: null }, 401, 'invalid_api_key'],
    ['a wrong key', { model: 'bge-small-zh-v1.5', input: ['x'] }, { key: 'w'.repeat(43) }, 401, 'invalid_api_key'],
    ['another path', { model: 'bge-small-zh-v1.5', input: ['x'] }, { path: '/v1/chat/completions' }, 404, 'not_found'],
    ['GET', null, { method: 'GET' }, 405, 'method_not_allowed'],
    ['another model', { model: 'bge-m3', input: ['x'] }, {}, 404, 'model_not_found'],
    ['invalid JSON', null, { raw: '{"model":' }, 400, 'invalid_json'],
    ['a non-object body', [1, 2], {}, 400, 'invalid_body'],
    ['no input', { model: 'bge-small-zh-v1.5' }, {}, 400, 'invalid_input'],
    ['an empty list', { model: 'bge-small-zh-v1.5', input: [] }, {}, 400, 'invalid_input'],
    ['token arrays', { model: 'bge-small-zh-v1.5', input: [[1, 2, 3]] }, {}, 400, 'invalid_input'],
    ['an empty text', { model: 'bge-small-zh-v1.5', input: ['ok', '  '] }, {}, 400, 'invalid_input'],
    ['base64 encoding', { model: 'bge-small-zh-v1.5', input: ['x'], encoding_format: 'base64' }, {}, 400, 'invalid_encoding_format'],
    ['other dimensions', { model: 'bge-small-zh-v1.5', input: ['x'], dimensions: 4 }, {}, 400, 'invalid_dimensions'],
    ['too many inputs', { model: 'bge-small-zh-v1.5', input: ['a', 'b', 'c', 'd', 'e'] }, {}, 413, 'too_many_inputs'],
    ['a body over the cap', { model: 'bge-small-zh-v1.5', input: ['a'.repeat(5000)] }, {}, 413, 'request_too_large'],
  ]
  for (const [name, body, options, status, code] of refusals) {
    it(`refuses ${name} (${status} ${code})`, async () => {
      const r = await post(port, body, options)
      assert.equal(r.status, status)
      assert.equal(r.json.error.code, code)
      assert.equal(typeof r.json.error.message, 'string')
    })
  }

  it('by default truncates input longer than the context to its head, and says how many were cut', async () => {
    const long = '这是一段很长很长的记忆文本，一定超过二十个词元的上限'
    const r = await post(port, { model: 'bge-small-zh-v1.5', input: ['short', long] })
    assert.equal(r.status, 200)
    assert.equal(r.truncated, '1')
    // usage counts what was embedded: 5 + 2 and the 20-token cut
    assert.equal(r.json.usage.prompt_tokens, 7 + 20)
    const head = await post(port, { model: 'bge-small-zh-v1.5', input: [[...long].slice(0, 18).join('')] })
    assert.deepEqual(r.json.data[1].embedding, head.json.data[0].embedding, 'the vector of the first 18 characters')
    assert.equal(head.truncated, '0')
    assert.equal(r.text.includes(long), false)
  })

  it('refuses input longer than the context with 413 when asked to, and does not repeat it', async () => {
    const secret = '这是一段很长很长的记忆文本，一定超过二十个词元的上限'
    const r = await post(port, { model: 'bge-small-zh-v1.5', input: ['short', secret] }, { headers: { 'x-embed-overflow': 'reject' } })
    assert.equal(r.status, 413)
    assert.equal(r.json.error.code, 'context_length_exceeded')
    assert.match(r.json.error.message, /input 1 has \d+ tokens; this model accepts at most 20/)
    assert.equal(r.text.includes(secret), false)
    // an exact fit is not over the limit
    const fit = await post(port, { model: 'bge-small-zh-v1.5', input: ['x'.repeat(18)] }, { headers: { 'x-embed-overflow': 'reject' } })
    assert.equal(fit.status, 200)
    assert.equal(fit.json.usage.prompt_tokens, 20)
  })

  it('ignores an unknown overflow header value', async () => {
    const r = await post(port, { model: 'bge-small-zh-v1.5', input: ['y'.repeat(40)] }, { headers: { 'x-embed-overflow': 'explode' } })
    assert.equal(r.status, 200)
    assert.equal(r.truncated, '1')
  })
})

describe('embeddings endpoint in reject mode', () => {
  it('refuses over-long input before embedding anything', async () => {
    const seen = []
    const endpoint = createEmbeddingServer({ key: KEY, modelId: 'm', embedder: fakeEmbedder({ seen }), overflow: 'reject' })
    const port = await endpoint.listen(0)
    try {
      const r = await post(port, { model: 'm', input: ['ok', 'z'.repeat(30)] })
      assert.equal(r.status, 413)
      assert.deepEqual(seen, [])
      const t = await post(port, { model: 'm', input: ['z'.repeat(30)] }, { headers: { 'x-embed-overflow': 'truncate' } })
      assert.equal(t.status, 200)
      assert.equal(t.truncated, '1')
    } finally {
      await endpoint.close()
    }
  })
})

describe('embeddings endpoint queue', () => {
  it('answers 503 when too many requests wait', async () => {
    const endpoint = createEmbeddingServer({ key: KEY, modelId: 'm', embedder: fakeEmbedder({ slowMs: 200 }), limits: { maxQueue: 2 } })
    const port = await endpoint.listen(0)
    try {
      const results = await Promise.all([1, 2, 3, 4].map(() => post(port, { model: 'm', input: ['x'] })))
      const codes = results.map((r) => r.status).sort()
      assert.ok(codes.includes(200))
      assert.ok(codes.includes(503))
    } finally {
      await endpoint.close()
    }
  })
})

describe('the gateway client', { skip: !process.env.FEIYU_MEMGATE_SRC && 'FEIYU_MEMGATE_SRC is not set' }, () => {
  it('reads the vectors through backends/embedding.py _Http', async () => {
    const endpoint = createEmbeddingServer({ key: KEY, modelId: 'bge-small-zh-v1.5', embedder: fakeEmbedder() })
    const port = await endpoint.listen(0)
    try {
      const root = process.env.FEIYU_MEMGATE_SRC
      const src = fs.existsSync(path.join(root, 'src', 'feiyu_memgate')) ? path.join(root, 'src') : root
      const script = [
        'import json, sys',
        'sys.path.insert(0, sys.argv[1])',
        'from feiyu_memgate.backends.embedding import _Http',
        'from feiyu_memgate.errors import BackendError',
        'h = _Http(sys.argv[2], "bge-small-zh-v1.5", sys.argv[3], 10.0)',
        // the third text is over the fake model's limit: truncated, with the x-truncated-inputs header
        'vecs = h.embed(["\\u5468\\u672b", "b", "c" * 40], 10.0, None)',
        'out = {"n": len(vecs), "dims": [len(v) for v in vecs]}',
        'try:',
        '    _Http(sys.argv[2], "bge-m3", sys.argv[3], 10.0).embed(["x"], 10.0, None)',
        '    out["other_model"] = "accepted"',
        'except BackendError:',
        '    out["other_model"] = "refused"',
        'try:',
        '    _Http(sys.argv[2], "bge-small-zh-v1.5", "wrong" * 9, 10.0).embed(["x"], 10.0, None)',
        '    out["wrong_key"] = "accepted"',
        'except BackendError:',
        '    out["wrong_key"] = "refused"',
        'print(json.dumps(out))',
      ].join('\n')
      const python = process.platform === 'win32' ? 'python' : 'python3'
      const { stdout } = await promisify(execFile)(python, ['-I', '-c', script, src, `http://127.0.0.1:${port}/v1`, KEY])
      assert.deepEqual(JSON.parse(stdout.trim()), { n: 3, dims: [DIMS, DIMS, DIMS], other_model: 'refused', wrong_key: 'refused' })
    } finally {
      await endpoint.close()
    }
  })
})
