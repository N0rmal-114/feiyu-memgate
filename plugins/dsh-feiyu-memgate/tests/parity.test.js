// The JavaScript port must give exactly the Python client's answer for every golden case in
// tests/fixtures/parity/ (made by tools/make-parity-fixtures.py from the real Python code).
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import { buildQuery, meaningful } from '../src/query.js'
import { createFormatter } from '../src/block.js'
import { createClient } from '../src/client.js'

const load = (name) => JSON.parse(fs.readFileSync(new URL(`./fixtures/parity/${name}`, import.meta.url), 'utf8'))
const query = load('query.json')
const block = load('block.json')
const outcomes = load('outcomes.json')
const TOKEN = 'tok-parity-fake'

// Python option names -> the JavaScript ones.
const QUERY_OPTIONS = { filler_chars: 'fillerChars', short_chars: 'shortChars', max_chars: 'maxChars', skip_prefixes: 'skipPrefixes', skip_words: 'skipWords' }
const BLOCK_OPTIONS = { lang: 'lang', tag: 'tag', labels: 'labels', default_label: 'defaultLabel', head: 'head', more_template: 'moreTemplate' }
const rename = (options, names) => Object.fromEntries(Object.entries(options).map(([k, v]) => [names[k], v]))

// Unicode versions: the fixture comes from Python's table (unicodedata), the port runs on this
// Node's table, and the two can be different versions (Python 3.14: Unicode 16.0, Node 24:
// Unicode 17.0). A code point that the older table leaves unassigned (category Cn, so dropped)
// and the newer one assigns as a letter or digit is kept by Node and dropped by Python. That is
// the ONLY difference allowed here, and it is listed in the test output; any other difference,
// in either direction, fails.
function inRanges(ranges) {
  const set = new Set()
  for (const [a, b] of ranges) for (let cp = a; cp <= b; cp++) set.add(cp)
  return set
}

test('meaningful(): every code point agrees with Python, apart from the Unicode version gap', () => {
  const python = inRanges(query.dropped_ranges)
  const unassigned = inRanges(query.unassigned_ranges)
  const differing = []
  for (let cp = 0; cp <= 0x10ffff; cp++) {
    if ((meaningful(String.fromCodePoint(cp)) === '') !== python.has(cp)) differing.push(cp)
  }
  const unexplained = differing.filter((cp) => !unassigned.has(cp) || !python.has(cp))
  assert.deepEqual(unexplained.map((cp) => cp.toString(16)), [], 'differences that are not a Unicode version gap')
  const known = [...differing].map((cp) => cp.toString(16).toUpperCase())
  console.log(`# unicode version gap: ${differing.length} code points are unassigned in Python ${query.python} (Unicode ${query.unicode}) ` +
    `and assigned in Node ${process.version} (Unicode ${process.versions.unicode}); first ones: U+${known.slice(0, 5).join(' U+')}`)
  if (query.unicode === process.versions.unicode) assert.equal(differing.length, 0)
})

test(`meaningful(): ${query.meaningful.length} golden cases`, () => {
  for (const c of query.meaningful) assert.equal(meaningful(c.text), c.expected, JSON.stringify(c.text))
})

test(`buildQuery(): ${query.build.length} golden cases`, () => {
  assert.ok(query.build.length >= 150)
  for (const c of query.build) {
    assert.equal(buildQuery(c.current, c.previous, rename(c.options, QUERY_OPTIONS)), c.expected, JSON.stringify([c.current, c.previous, c.options]).slice(0, 120))
  }
})

test(`block formatter: ${block.cases.length} golden cases`, () => {
  assert.ok(block.cases.length >= 80)
  for (const c of block.cases) {
    const label = JSON.stringify(c).slice(0, 160)
    let formatter
    try {
      formatter = createFormatter(rename(c.options, BLOCK_OPTIONS))
    } catch {
      assert.deepEqual(c.expected, { error: 'ValueError' }, label)
      continue
    }
    assert.ok(!c.expected.error, label)
    const got = c.kind === 'strip' ? formatter.strip(c.text) : formatter.format(c.results, c.more)
    assert.deepEqual(got, c.expected.value, label)
  }
})

// ---- the client, against a node:http server that plays each recorded reply

function expandBody(spec, path) {
  if ('body_hex' in spec) return Buffer.from(spec.body_hex, 'hex')
  let body = spec.body
  if (body === undefined || body === null) body = path === '/v1/recall' ? { results: [{ id: 'a', source: 'core', text: 'likes tea', score: 0.9 }] } : { status: 'ok' }
  if (body !== null && typeof body === 'object' && !Array.isArray(body) && 'repeat' in body) {
    const r = body.repeat
    return Buffer.from(r.head + r.unit.repeat(r.n) + r.tail)
  }
  return Buffer.from(typeof body === 'string' ? body : JSON.stringify(body))
}

async function startServer(spec) {
  const seen = []
  const server = http.createServer((req, res) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => {
      seen.push({ path: req.url, body: JSON.parse(Buffer.concat(chunks).toString('utf8')) })
      const mode = spec.mode ?? 'reply'
      if (mode === 'close_early') return req.socket.destroy()
      if (mode === 'redirect') {
        res.writeHead(302, { location: 'http://127.0.0.1:1/elsewhere', 'content-length': '0' })
        return res.end()
      }
      const raw = expandBody(spec, req.url)
      if (mode === 'truncate') {
        res.writeHead(spec.status ?? 200, { 'content-length': raw.length + 1000 })
        res.write(raw)
        return setTimeout(() => req.socket.destroy(), 20)
      }
      res.writeHead(spec.status ?? 200, { 'content-length': raw.length })
      res.end(raw)
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  return {
    url: `http://127.0.0.1:${server.address().port}`,
    seen,
    close: async () => {
      server.closeAllConnections()
      await new Promise((resolve) => server.close(resolve))
    },
  }
}

async function closedPortUrl() {
  const probe = net.createServer()
  await new Promise((r) => probe.listen(0, '127.0.0.1', r))
  const port = probe.address().port
  await new Promise((r) => probe.close(r))
  return `http://127.0.0.1:${port}`
}

test(`client outcomes: ${outcomes.replies.length} recorded replies (status, code, broken connections)`, async () => {
  assert.ok(outcomes.replies.length > 400)
  const silent = { warn() {} }
  for (const spec of outcomes.replies) {
    const server = spec.mode === 'refuse' ? null : await startServer(spec)
    try {
      const client = createClient({ url: server ? server.url : await closedPortUrl(), token: TOKEN, logger: silent })
      const got = {
        remember: await client.rememberStatus('hello'),
        recall: await client.recallDetail('hello'),
        used: await client.reportUsed([{ source: 's', id: '1' }]),
      }
      assert.deepEqual(got, spec.expected, spec.name)
    } finally {
      await server?.close()
    }
  }
})

test('client inputs: what is refused and what is sent, as Python', async () => {
  const server = await startServer({})
  const client = createClient({ url: server.url, token: TOKEN, logger: { warn() {} } })
  const seen = server.seen
  const sentSince = (before) => (seen.length > before ? seen[before].body : null)
  try {
    const { remember, used, recall, recall_limit: limits } = outcomes.inputs
    assert.ok(remember.length + used.length + recall.length >= 70)
    for (const c of remember) {
      const before = seen.length
      assert.equal(await client.rememberMessages(c.messages), c.result, c.name)
      assert.deepEqual(sentSince(before), c.sent, c.name)
    }
    for (const c of used) {
      const before = seen.length
      assert.equal(await client.reportUsed(c.items), c.result, c.name)
      assert.deepEqual(sentSince(before), c.sent, c.name)
    }
    for (const c of recall) {
      const before = seen.length
      assert.deepEqual(await client.recallDetail(c.query), c.result, c.name)
      assert.deepEqual(sentSince(before), c.sent, c.name)
    }
    for (const c of limits) {
      const before = seen.length
      await createClient({ url: server.url, token: TOKEN, recallLimit: c.limit }).recallDetail('hello')
      assert.deepEqual(sentSince(before), c.sent)
    }
    assert.deepEqual([...new Set(seen.map((s) => s.path))].sort(), outcomes.inputs.paths)
  } finally {
    await server.close()
  }
})
