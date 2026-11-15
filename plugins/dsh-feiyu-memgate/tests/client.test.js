// The client against a real HTTP server made with node:http: no mocked fetch.
import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import net from 'node:net'
import { createClient } from '../src/client.js'

const TOKEN = 'tok-test-fake-0123456789'

// A server that records every request and answers with `respond(req, res, body)`.
async function startServer(respond) {
  const requests = []
  const server = http.createServer((req, res) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => {
      const raw = Buffer.concat(chunks)
      let json = null
      try {
        json = JSON.parse(raw.toString('utf8'))
      } catch {}
      const entry = { method: req.method, url: req.url, headers: req.headers, raw, json }
      requests.push(entry)
      respond(req, res, entry)
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const url = `http://127.0.0.1:${server.address().port}`
  const close = async () => {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
  }
  return { url, requests, close }
}

const json = (res, status, body) => {
  const raw = Buffer.from(typeof body === 'string' ? body : JSON.stringify(body))
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': raw.length })
  res.end(raw)
}

const usual = (req, res) => {
  if (req.url === '/v1/recall') return json(res, 200, { results: [{ id: 'a', source: 'core', text: 'likes tea', score: 0.9 }], more: 2 })
  if (req.url.startsWith('/healthz')) return json(res, 200, { status: 'ok' })
  return json(res, 200, { status: 'ok' })
}

function logs() {
  const lines = []
  return { lines, logger: { warn: (m) => lines.push(String(m)) } }
}

// Runs fn(client, server, log) against a fresh server and always cleans up.
async function withServer(respond, fn, options = {}) {
  const server = await startServer(respond)
  const log = logs()
  try {
    await fn(createClient({ url: server.url, token: TOKEN, logger: log.logger, ...options }), server, log)
  } finally {
    await server.close()
  }
}

test('recall: route, headers and body as the gateway expects', () =>
  withServer(usual, async (client, server) => {
    const detail = await client.recallDetail('我喜欢喝什么茶？')
    assert.deepEqual(detail, { results: [{ id: 'a', source: 'core', text: 'likes tea', score: 0.9 }], more: 2, failed: 0, busy: 0 })
    const [req] = server.requests
    assert.equal(req.method, 'POST')
    assert.equal(req.url, '/v1/recall')
    assert.equal(req.headers.authorization, `Bearer ${TOKEN}`)
    assert.equal(req.headers['content-type'], 'application/json')
    assert.equal(req.headers['content-length'], String(req.raw.length))
    assert.deepEqual(req.json, { query: '我喜欢喝什么茶？', limit: 5 })
    // UTF-8 as it is, not \uXXXX escapes.
    assert.ok(req.raw.includes(Buffer.from('我喜欢喝什么茶')))
    assert.ok(!req.raw.toString('latin1').includes('\\u'))
  }))

test('recall limit option', () =>
  withServer(usual, async (client, server) => {
    await client.recallDetail('hello')
    assert.equal(server.requests[0].json.limit, 3)
  }, { recallLimit: 3 }))

test('remember: saved, skipped, unsure; body shape', () =>
  withServer((req, res) => json(res, 200, { status: server_status }), async (client, server) => {
    for (const [status, expected] of [['ok', 'saved'], ['skipped', 'skipped'], ['who knows', 'unsure'], [5, 'unsure']]) {
      server_status = status
      assert.equal(await client.rememberStatus('my name is Fish'), expected)
    }
    assert.equal(server.requests[0].url, '/v1/remember')
    assert.deepEqual(server.requests[0].json, { messages: [{ role: 'user', content: 'my name is Fish' }] })
    server_status = 'ok'
    const messages = [{ role: 'user', content: 'I moved', time: '2026-11-14T12:00:00+08:00', speaker: 'guest', extra: 'dropped' }, { role: 'assistant', content: 'Nice' }]
    assert.equal(await client.rememberMessages(messages), 'saved')
    assert.deepEqual(server.requests.at(-1).json.messages, [
      { role: 'user', content: 'I moved', time: '2026-11-14T12:00:00+08:00', speaker: 'guest' },
      { role: 'assistant', content: 'Nice' },
    ])
  }))
let server_status = 'ok'

test('used: sends source and id only', () =>
  withServer(usual, async (client, server) => {
    assert.equal(await client.reportUsed([{ id: 'a', source: 'private', text: 'never sent', score: 1 }, { id: 'b', source: 'private' }]), true)
    assert.equal(server.requests[0].url, '/v1/used')
    assert.deepEqual(server.requests[0].json, { used: [{ source: 'private', id: 'a' }, { source: 'private', id: 'b' }] })
  }))

test('every error code maps to the Python outcome', () => {
  const table = [
    // [status, code, remember, used]
    [429, 'daily_limit', 'limited'],
    [500, 'guard_error', 'failed'], [403, 'forbidden', 'failed'], [403, 'unknown_tenant', 'failed'],
    [403, 'tenant_mismatch', 'failed'], [413, 'too_large', 'failed'], [401, 'unauthorized', 'failed'],
    [429, 'rate_limited', 'failed'], [503, 'busy', 'failed'], [411, 'length_required', 'failed'],
    [404, 'not_found', 'failed'], [405, 'method_not_allowed', 'failed'], [400, 'bad_messages', 'failed'],
    [500, 'bad_anything', 'failed'], [502, 'backend_unavailable', 'unsure'], [500, 'internal', 'unsure'],
    [500, 'something_new', 'unsure'], [500, null, 'unsure'], [409, 'something_new', 'failed'], [409, null, 'failed'],
    [429, 'DAILY_LIMIT', 'failed'], [429, ['daily_limit'], 'failed'], [302, null, 'unsure'], [204, null, 'unsure'],
  ]
  return withServer((req, res) => {
    const [status, code] = current
    json(res, status, code === null ? { error: 'x' } : { error: 'x', code })
  }, async (client) => {
    for (const [status, code, expected] of table) {
      current = [status, code]
      assert.equal(await client.rememberStatus('x'), expected, `${status} ${JSON.stringify(code)}`)
      assert.equal(await client.recallDetail('x'), null)
      assert.equal(await client.reportUsed([{ source: 's', id: '1' }]), false)
    }
  })
})
let current = []

test('the text of an error never decides, only its code', () =>
  withServer((req, res) => json(res, 429, { error: 'daily limit' }), async (client) => {
    assert.equal(await client.rememberStatus('x'), 'failed')
  }))

test('error bodies that are not JSON objects still map by status', () => {
  const bodies = ['not json', '', '[1,2]', '"daily_limit"', 'null']
  return withServer((req, res) => json(res, status429 ? 429 : 500, bodies[i]), async (client) => {
    for (i = 0; i < bodies.length; i++) {
      status429 = true
      assert.equal(await client.rememberStatus('x'), 'failed')
      status429 = false
      assert.equal(await client.rememberStatus('x'), 'unsure')
    }
  })
})
let i = 0
let status429 = true

test('an error body over 4096 bytes is not read: the status decides', () =>
  withServer((req, res) => json(res, 429, `{"code": "daily_limit", "pad": "${'x'.repeat(5000)}"}`), async (client) => {
    assert.equal(await client.rememberStatus('x'), 'failed')
  }))

test('a reply body over 256 KiB is refused; exactly 256 KiB is read', () => {
  let n = 0
  return withServer((req, res) => json(res, 200, `{"status": "ok", "pad": "${'x'.repeat(n)}"}`), async (client) => {
    const base = '{"status": "ok", "pad": ""}'.length
    n = 256 * 1024 - base
    assert.equal(await client.rememberStatus('x'), 'saved')
    n += 1
    assert.equal(await client.rememberStatus('x'), 'unsure')
    n = 300000
    assert.equal(await client.recallDetail('x'), null)
  })
})

test('recall: null means could not look, [] means found nothing', () => {
  let body
  return withServer((req, res) => json(res, 200, body), async (client) => {
    body = { results: [] }
    assert.deepEqual(await client.recallDetail('x'), { results: [], more: 0, failed: 0, busy: 0 })
    for (body of [{}, { results: null }, { results: 'oops' }, { results: { text: 'x' } }, [], 'null', 'not json']) {
      assert.equal(await client.recallDetail('x'), null, JSON.stringify(body))
    }
    body = { results: [{ source: 'core', text: 'ok', score: 1 }, 5, { text: 3 }, null, 'x', [], { text: 'b' }], more: 3, failed: 1, busy: 2 }
    assert.deepEqual(await client.recallDetail('x'), { results: [{ source: 'core', text: 'ok', score: 1 }, { text: 'b' }], more: 3, failed: 1, busy: 2 })
    body = { results: [], more: true, failed: -2, busy: '1' }
    assert.deepEqual(await client.recallDetail('x'), { results: [], more: 0, failed: 0, busy: 0 })
  })
})

test('a slow server and the deadline', () =>
  withServer((req, res) => setTimeout(() => json(res, 200, { results: [], status: 'ok' }), 1500), async (client, server, log) => {
    const t0 = Date.now()
    assert.equal(await client.recallDetail('hello', { timeoutMs: 200 }), null)
    assert.equal(await client.rememberStatus('hello', { timeoutMs: 200 }), 'unsure')
    assert.equal(await client.reportUsed([{ source: 's', id: '1' }], { timeoutMs: 200 }), false)
    assert.ok(Date.now() - t0 < 1200, 'three calls end near their deadlines')
    assert.ok(log.lines.some((l) => l.includes('timeout')))
  }))

test('one absolute deadline: a server that drips the body cannot stretch it', () =>
  withServer((req, res) => {
    res.writeHead(200, { 'content-length': '1000' })
    const timer = setInterval(() => res.write(' '), 50)
    res.on('close', () => clearInterval(timer))
  }, async (client) => {
    const t0 = Date.now()
    assert.equal(await client.rememberStatus('hello', { timeoutMs: 400 }), 'unsure')
    assert.ok(Date.now() - t0 < 1500)
  }))

test('a caller signal ends the call; an aborted signal sends nothing', () =>
  withServer((req, res) => setTimeout(() => json(res, 200, { status: 'ok' }), 1500), async (client, server) => {
    const early = new AbortController()
    early.abort()
    assert.equal(await client.rememberStatus('x', { signal: early.signal }), 'failed')
    assert.equal(server.requests.length, 0)
    const later = new AbortController()
    setTimeout(() => later.abort(), 100)
    const t0 = Date.now()
    assert.equal(await client.rememberStatus('x', { signal: later.signal }), 'unsure')
    assert.ok(Date.now() - t0 < 1000)
  }))

test('a deadline already past sends nothing', () =>
  withServer(usual, async (client, server) => {
    assert.equal(await client.rememberStatus('x', { timeoutMs: 0 }), 'failed')
    assert.equal(await client.recallDetail('x', { timeoutMs: -5 }), null)
    assert.equal(server.requests.length, 0)
  }))

test('a server that closes the socket mid-response', () => {
  let mode = 'mid'
  return withServer((req, res) => {
    if (mode === 'early') return req.socket.destroy()
    res.writeHead(200, { 'content-length': '1000' })
    res.write('{"status": ')
    setTimeout(() => req.socket.destroy(), 20)
  }, async (client, server, log) => {
    assert.equal(await client.rememberStatus('x'), 'unsure')
    assert.equal(await client.recallDetail('x'), null)
    assert.equal(await client.reportUsed([{ source: 's', id: '1' }]), false)
    mode = 'early'
    assert.equal(await client.rememberStatus('x'), 'unsure')
    assert.equal(await client.recallDetail('x'), null)
    assert.ok(log.lines.length >= 5)
  })
})

test('connection refused is a definite failure', async () => {
  const probe = net.createServer()
  await new Promise((r) => probe.listen(0, '127.0.0.1', r))
  const port = probe.address().port
  await new Promise((r) => probe.close(r))
  const log = logs()
  const client = createClient({ url: `http://127.0.0.1:${port}`, token: TOKEN, logger: log.logger })
  assert.equal(await client.rememberStatus('x'), 'failed')
  assert.equal(await client.recallDetail('x'), null)
  assert.equal(await client.reportUsed([{ source: 's', id: '1' }]), false)
  assert.equal(await client.health(), null)
  assert.ok(log.lines.length >= 4)
})

test('redirects are refused and never followed', async () => {
  const target = await startServer(usual)
  try {
    await withServer((req, res) => {
      res.writeHead(302, { location: `${target.url}/steal`, 'content-length': '0' })
      res.end()
    }, async (client, server, log) => {
      assert.equal(await client.recallDetail('x'), null)
      assert.equal(await client.rememberStatus('x'), 'unsure')
      assert.equal(await client.reportUsed([{ source: 's', id: '1' }]), false)
      assert.equal(await client.health(), null)
      assert.equal(server.requests.length, 4)
      assert.equal(target.requests.length, 0)
    })
  } finally {
    await target.close()
  }
})

test('over-limit input is never sent and never cut', () =>
  withServer(usual, async (client, server) => {
    const user = (content) => ({ role: 'user', content })
    assert.equal(await client.recallDetail('x'.repeat(501)), null)
    assert.equal(await client.recallDetail('𝒳'.repeat(501)), null)
    for (const bad of ['', '  ', null, 5]) assert.equal(await client.recallDetail(bad), null)
    assert.equal(await client.rememberStatus('y'.repeat(4001)), 'failed')
    assert.equal(await client.rememberMessages([user('z'.repeat(4000)), user('z'.repeat(4000)), user('z'.repeat(4000)), user('z'.repeat(4000)), user('z')]), 'failed')
    assert.equal(await client.rememberMessages(Array(21).fill(user('x'))), 'failed')
    assert.equal(await client.rememberMessages([]), 'failed')
    assert.equal(await client.rememberMessages([{ role: 'system', content: 'x' }]), 'failed')
    assert.equal(await client.rememberMessages([user(' ')]), 'failed')
    assert.equal(await client.rememberMessages([{ ...user('x'), time: 't'.repeat(41) }]), 'failed')
    assert.equal(await client.rememberMessages([{ ...user('x'), speaker: 's'.repeat(65) }]), 'failed')
    assert.equal(await client.rememberStatus('  '), 'failed')
    assert.equal(await client.reportUsed([]), false)
    assert.equal(await client.reportUsed(Array(21).fill({ source: 's', id: '1' })), false)
    assert.equal(await client.reportUsed([{ source: 's' }]), false)
    assert.equal(server.requests.length, 0, 'the server saw no request')
    // At the limits, the text goes out whole.
    assert.deepEqual((await client.recallDetail('x'.repeat(500))).results.length, 1)
    assert.equal(await client.rememberStatus('记'.repeat(4000)), 'saved')
    assert.equal(server.requests[1].json.messages[0].content, '记'.repeat(4000))
  }))

test('configured: off unless http(s) with a token', async () => {
  const server = await startServer(usual)
  try {
    const off = [
      createClient({ url: '', token: TOKEN }), createClient({ url: server.url, token: '' }), createClient({ url: server.url, token: '  ' }),
      createClient({}), createClient({ url: 'file:///etc/passwd', token: TOKEN }), createClient({ url: 'ftp://x', token: TOKEN }),
      createClient({ url: 'not a url', token: TOKEN }), createClient({ url: server.url, token: 5 }),
    ]
    for (const client of off) {
      assert.equal(client.configured, false)
      assert.equal(await client.recallDetail('hello'), null)
      assert.equal(await client.rememberStatus('hello'), 'failed')
      assert.equal(await client.reportUsed([{ source: 's', id: '1' }]), false)
    }
    assert.equal(server.requests.length, 0)
    assert.equal(createClient({ url: server.url, token: TOKEN }).configured, true)
    assert.equal(createClient({ url: ` ${server.url}/// `, token: ` ${TOKEN} ` }).configured, true)
  } finally {
    await server.close()
  }
})

test('base URL: trailing slashes are dropped and a path prefix is kept', async () => {
  const server = await startServer(usual)
  try {
    const client = createClient({ url: `${server.url}/gw//?x=1#y`, token: TOKEN })
    await client.recallDetail('x')
    assert.equal(server.requests[0].url, '/gw/v1/recall')
    await createClient({ url: `${server.url}/`, token: TOKEN }).recallDetail('x')
    assert.equal(server.requests[1].url, '/v1/recall')
  } finally {
    await server.close()
  }
})

test('health: no token, deep flag, null on failure', () => {
  let ok = true
  return withServer((req, res) => (ok ? json(res, 200, req.url.endsWith('deep=1') ? { status: 'ok', stores: { private: 'ok' } } : { status: 'ok' }) : json(res, 503, { error: 'x' })), async (client, server) => {
    assert.deepEqual(await client.health(), { status: 'ok' })
    assert.deepEqual(await client.health({ deep: true }), { status: 'ok', stores: { private: 'ok' } })
    assert.deepEqual(server.requests.map((r) => [r.method, r.url]), [['GET', '/healthz'], ['GET', '/healthz?deep=1']])
    assert.equal(server.requests[0].headers.authorization, undefined)
    ok = false
    assert.equal(await client.health(), null)
  })
})

test('a token HTTP cannot carry fails before anything is sent', () =>
  withServer(usual, async (client, server) => {
    const odd = createClient({ url: server.url, token: 'tok-中文' })
    assert.equal(await odd.rememberStatus('x'), 'failed')
    assert.equal(await odd.recallDetail('x'), null)
    assert.equal(server.requests.length, 0)
  }))

test('the logger sees only the kind of failure, never a query, a text, an id or the token', async () => {
  const secrets = ['my-secret-query-text', 'my-secret-memory-text', 'secret-item-id']
  const log = logs()
  const echo = `echo ${TOKEN} ${secrets[0]}`
  let mode = 'error'
  const server = await startServer((req, res) => {
    if (mode === 'error') return json(res, 500, { error: echo, code: echo })
    if (mode === 'code') return json(res, 429, { error: 'x', code: 'daily_limit' })
    if (mode === 'garbage') return json(res, 200, 'not json ' + echo)
    req.socket.destroy()
  })
  try {
    const client = createClient({ url: server.url, token: TOKEN, logger: log.logger })
    for (mode of ['error', 'code', 'garbage', 'close']) {
      await client.recallDetail(secrets[0])
      await client.rememberStatus(secrets[1])
      await client.reportUsed([{ source: 's', id: secrets[2] }])
    }
    await client.recallDetail('x'.repeat(501))
    await client.reportUsed([{ source: 's' }])
    await createClient({ url: 'http://127.0.0.1:1', token: TOKEN, logger: log.logger }).recallDetail(secrets[0])
    assert.ok(log.lines.length > 10)
    const all = log.lines.join('\n')
    for (const s of [TOKEN, ...secrets]) assert.ok(!all.includes(s), s)
    assert.ok(log.lines.includes('memory recall failed: http 500 -'), 'a code that is not a plain identifier is not logged')
    assert.ok(log.lines.includes('memory remember failed: http 429 daily_limit'))
  } finally {
    await server.close()
  }
})

test('a logger that throws, or none at all, changes nothing', async () => {
  const server = await startServer((req, res) => json(res, 500, { error: 'x' }))
  try {
    const noisy = createClient({ url: server.url, token: TOKEN, logger: { warn() { throw new Error('boom') } } })
    assert.equal(await noisy.rememberStatus('x'), 'unsure')
    const silent = createClient({ url: server.url, token: TOKEN })
    assert.equal(await silent.rememberStatus('x'), 'unsure')
  } finally {
    await server.close()
  }
})

test('never rejects, whatever fetch does', async () => {
  const throwing = createClient({ url: 'http://127.0.0.1:1', token: TOKEN, fetch: () => { throw new Error('sync boom') } })
  assert.equal(await throwing.rememberStatus('x'), 'unsure')
  assert.equal(await throwing.recallDetail('x'), null)
  const rejecting = createClient({ url: 'http://127.0.0.1:1', token: TOKEN, fetch: () => Promise.reject(null) })
  assert.equal(await rejecting.reportUsed([{ source: 's', id: '1' }]), false)
  assert.equal(await rejecting.health(), null)
})
