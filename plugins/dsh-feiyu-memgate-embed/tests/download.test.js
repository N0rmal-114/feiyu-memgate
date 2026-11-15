// The downloader against local node:http servers standing in for Hugging Face compatible origins.
import assert from 'node:assert/strict'
import { createHash, randomBytes } from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { after, before, describe, it } from 'node:test'
import {
  DownloadBusyError, DownloadError, classifyFailure, createDownloader, downloadFrom, freeBytes, modelFile, orderSources, partFile, verifyFile,
} from '../src/download.js'

const delay = (ms) => new Promise((r) => setTimeout(r, ms))
const BLOB = randomBytes(300 * 1024)
const MODEL = Object.freeze({
  id: 'test-model', repository: 'org/repo', revision: 'a'.repeat(40), file: 'model.gguf',
  sizeBytes: BLOB.length, sha256: createHash('sha256').update(BLOB).digest('hex'),
})

function blobServer(options = {}) {
  const o = { range: true, headDelay: 0, headStatus: 200, getStatus: 200, chunkDelay: 0, chunkSize: 64 * 1024, extra: null, noLength: false, corrupt: false, ...options }
  const requests = []
  let sent = 0
  const server = http.createServer(async (req, res) => {
    requests.push({ method: req.method, url: req.url, range: req.headers.range })
    if (!req.url.endsWith(`/org/repo/resolve/${MODEL.revision}/model.gguf`)) {
      res.writeHead(404)
      res.end()
      return
    }
    if (req.method === 'HEAD') {
      await delay(o.headDelay)
      res.writeHead(o.headStatus, { 'content-length': BLOB.length })
      res.end()
      return
    }
    if (o.getStatus !== 200) {
      res.writeHead(o.getStatus)
      res.end()
      return
    }
    let body = o.corrupt ? Buffer.from(BLOB.map((b, i) => (i === 1000 ? b ^ 0xff : b))) : BLOB
    if (o.extra) body = Buffer.concat([body, o.extra])
    let status = 200
    const headers = {}
    const m = o.range && /^bytes=(\d+)-$/.exec(req.headers.range ?? '')
    if (m) {
      const start = Number(m[1])
      status = 206
      headers['content-range'] = o.badRange ? `bytes 0-${BLOB.length - 1}/${BLOB.length}` : `bytes ${start}-${BLOB.length - 1}/${BLOB.length}`
      body = body.subarray(start)
    }
    if (!o.noLength) headers['content-length'] = o.wrongLength ? body.length + 7 : body.length
    res.writeHead(status, headers)
    for (let i = 0; i < body.length; i += o.chunkSize) {
      if (res.destroyed || req.destroyed) return
      const piece = body.subarray(i, i + o.chunkSize)
      sent += piece.length
      res.write(piece)
      if (o.chunkDelay) await delay(o.chunkDelay)
    }
    res.end()
  })
  return {
    requests,
    sent: () => sent,
    async start() {
      await new Promise((r) => server.listen(0, '127.0.0.1', r))
      this.origin = `http://127.0.0.1:${server.address().port}`
      return this
    },
    stop: () => new Promise((r) => {
      server.closeAllConnections?.()
      server.close(r)
    }),
  }
}

let root
before(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'fmg-embed-dl-'))
})
after(() => fs.rmSync(root, { recursive: true, force: true }))

const freshDir = () => fs.mkdtempSync(path.join(root, 'm-'))

describe('orderSources', () => {
  it('puts the first origin that answers HEAD first', async () => {
    const slow = await blobServer({ headDelay: 400 }).start()
    const fast = await blobServer().start()
    try {
      const urls = await orderSources(MODEL, [slow.origin, fast.origin], { timeoutMs: 3000 })
      assert.ok(urls[0].startsWith(fast.origin))
      assert.ok(urls[1].startsWith(slow.origin))
    } finally {
      await slow.stop()
      await fast.stop()
    }
  })

  it('keeps the configured order when every probe fails', async () => {
    const a = await blobServer({ headStatus: 405 }).start()
    const b = await blobServer({ headStatus: 404 }).start()
    try {
      const urls = await orderSources(MODEL, [a.origin, b.origin], { timeoutMs: 3000 })
      assert.ok(urls[0].startsWith(a.origin) && urls[1].startsWith(b.origin))
    } finally {
      await a.stop()
      await b.stop()
    }
  })

  it('does not probe a single (explicit) origin', async () => {
    const a = await blobServer().start()
    try {
      const urls = await orderSources(MODEL, [a.origin], { timeoutMs: 3000 })
      assert.equal(urls.length, 1)
      assert.equal(a.requests.length, 0)
    } finally {
      await a.stop()
    }
  })

  it('times out slow probes and still returns every origin', async () => {
    const a = await blobServer({ headDelay: 2000 }).start()
    const b = await blobServer({ headDelay: 2000 }).start()
    try {
      const t0 = Date.now()
      const urls = await orderSources(MODEL, [a.origin, b.origin], { timeoutMs: 200 })
      assert.ok(Date.now() - t0 < 1500)
      assert.equal(urls.length, 2)
    } finally {
      await a.stop()
      await b.stop()
    }
  })
})

describe('downloadFrom', () => {
  it('downloads, verifies and publishes; the .part file is gone', async () => {
    const s = await blobServer().start()
    const dir = freshDir()
    try {
      const url = `${s.origin}/org/repo/resolve/${MODEL.revision}/model.gguf`
      const result = await downloadFrom(url, MODEL, { modelsDir: dir })
      assert.equal(result.sha256, MODEL.sha256)
      assert.equal(result.resumedFrom, 0)
      assert.ok(await verifyFile(modelFile(dir, MODEL), MODEL))
      assert.equal(fs.existsSync(partFile(dir, MODEL)), false)
    } finally {
      await s.stop()
    }
  })

  it('resumes a partial file with a Range request', async () => {
    const s = await blobServer().start()
    const dir = freshDir()
    try {
      fs.mkdirSync(path.join(dir, MODEL.id), { recursive: true })
      fs.writeFileSync(partFile(dir, MODEL), BLOB.subarray(0, 100000))
      const url = `${s.origin}/org/repo/resolve/${MODEL.revision}/model.gguf`
      const result = await downloadFrom(url, MODEL, { modelsDir: dir })
      assert.equal(result.resumedFrom, 100000)
      assert.equal(s.requests.at(-1).range, 'bytes=100000-')
      assert.equal(s.sent(), BLOB.length - 100000)
      assert.ok(await verifyFile(modelFile(dir, MODEL), MODEL))
    } finally {
      await s.stop()
    }
  })

  it('starts over when the server ignores Range', async () => {
    const s = await blobServer({ range: false }).start()
    const dir = freshDir()
    try {
      fs.mkdirSync(path.join(dir, MODEL.id), { recursive: true })
      fs.writeFileSync(partFile(dir, MODEL), BLOB.subarray(0, 50000))
      const url = `${s.origin}/org/repo/resolve/${MODEL.revision}/model.gguf`
      await downloadFrom(url, MODEL, { modelsDir: dir })
      assert.ok(await verifyFile(modelFile(dir, MODEL), MODEL))
    } finally {
      await s.stop()
    }
  })

  it('refuses a 206 for the wrong range and drops the partial file', async () => {
    const s = await blobServer({ badRange: true }).start()
    const dir = freshDir()
    try {
      fs.mkdirSync(path.join(dir, MODEL.id), { recursive: true })
      fs.writeFileSync(partFile(dir, MODEL), BLOB.subarray(0, 50000))
      const url = `${s.origin}/org/repo/resolve/${MODEL.revision}/model.gguf`
      await assert.rejects(downloadFrom(url, MODEL, { modelsDir: dir }), (e) => e instanceof DownloadError && e.reason === 'integrity')
      assert.equal(fs.existsSync(partFile(dir, MODEL)), false)
    } finally {
      await s.stop()
    }
  })

  for (const [name, options] of [
    ['too many bytes (no length header)', { extra: Buffer.alloc(200000, 1), noLength: true }],
    ['a wrong Content-Length', { wrongLength: true }],
    ['a wrong hash', { corrupt: true }],
  ]) {
    it(`refuses ${name}: no model file, no partial file`, async () => {
      const s = await blobServer(options).start()
      const dir = freshDir()
      try {
        const url = `${s.origin}/org/repo/resolve/${MODEL.revision}/model.gguf`
        await assert.rejects(downloadFrom(url, MODEL, { modelsDir: dir }), (e) => e instanceof DownloadError && e.reason === 'integrity')
        assert.equal(fs.existsSync(modelFile(dir, MODEL)), false)
        assert.equal(fs.existsSync(partFile(dir, MODEL)), false)
      } finally {
        await s.stop()
      }
    })
  }

  it('stops reading at the size cap', async () => {
    const s = await blobServer({ extra: Buffer.alloc(5 * 1024 * 1024, 1), noLength: true, chunkSize: 16 * 1024, chunkDelay: 1 }).start()
    const dir = freshDir()
    try {
      const url = `${s.origin}/org/repo/resolve/${MODEL.revision}/model.gguf`
      await assert.rejects(downloadFrom(url, MODEL, { modelsDir: dir }), (e) => e.reason === 'integrity')
      await delay(100)
      assert.ok(s.sent() < BLOB.length + 2 * 1024 * 1024, `sent ${s.sent()}`)
    } finally {
      await s.stop()
    }
  })

  it('reports an HTTP failure with its status and origin', async () => {
    const s = await blobServer({ getStatus: 403 }).start()
    const dir = freshDir()
    try {
      const url = `${s.origin}/org/repo/resolve/${MODEL.revision}/model.gguf`
      await assert.rejects(downloadFrom(url, MODEL, { modelsDir: dir }), (e) => e.reason === 'http' && e.status === 403 && e.source === s.origin)
    } finally {
      await s.stop()
    }
  })
})

describe('createDownloader', () => {
  it('falls back to the next origin and reports the source used', async () => {
    const bad = await blobServer({ getStatus: 500 }).start()
    const good = await blobServer({ headDelay: 300 }).start()
    const dir = freshDir()
    let done = null
    try {
      const d = createDownloader()
      d.start(MODEL, { modelsDir: dir, origins: [bad.origin, good.origin], onDone: async (m, r) => { done = r } })
      const last = await d.settled()
      assert.equal(last.state, 'done')
      assert.equal(last.source, good.origin)
      assert.equal(done.sha256, MODEL.sha256)
    } finally {
      await bad.stop()
      await good.stop()
    }
  })

  it('reports the origin it chose when that origin redirects the file to another host', async () => {
    // huggingface.co answers a resolve URL with a redirect to its CDN; the job keeps reporting the
    // origin the user can choose, before and after the download.
    const cdn = await blobServer().start()
    const origin = http.createServer((req, res) => {
      res.writeHead(302, { location: `${cdn.origin}${req.url}` })
      res.end()
    })
    await new Promise((r) => origin.listen(0, '127.0.0.1', r))
    const originUrl = `http://127.0.0.1:${origin.address().port}`
    const dir = freshDir()
    try {
      const d = createDownloader()
      d.start(MODEL, { modelsDir: dir, origins: [originUrl] })
      const last = await d.settled()
      assert.equal(last.state, 'done')
      assert.equal(cdn.requests.filter((r) => r.method === 'GET').length, 1)
      assert.equal(last.source, originUrl)
      assert.equal(await verifyFile(modelFile(dir, MODEL), MODEL), true)
    } finally {
      origin.closeAllConnections?.()
      await new Promise((r) => origin.close(r))
      await cdn.stop()
    }
  })

  it('uses only the explicit origin', async () => {
    const a = await blobServer().start()
    const b = await blobServer().start()
    const dir = freshDir()
    try {
      const d = createDownloader()
      d.start(MODEL, { modelsDir: dir, origins: [b.origin] })
      assert.equal((await d.settled()).state, 'done')
      assert.equal(a.requests.length, 0)
      assert.deepEqual(b.requests.map((r) => r.method), ['GET'])
    } finally {
      await a.stop()
      await b.stop()
    }
  })

  it('runs one job at a time, reports progress, and cancels keeping the partial file', async () => {
    const s = await blobServer({ chunkSize: 8 * 1024, chunkDelay: 25 }).start()
    const dir = freshDir()
    try {
      const changes = []
      const d = createDownloader({ onChange: (v) => changes.push(v.state) })
      d.start(MODEL, { modelsDir: dir, origins: [s.origin] })
      assert.throws(() => d.start(MODEL, { modelsDir: dir, origins: [s.origin] }), DownloadBusyError)
      while ((d.progress()?.bytes ?? 0) < 40000) await delay(20)
      const p = d.progress()
      assert.equal(p.id, 'test-model')
      assert.equal(p.total, BLOB.length)
      assert.equal(p.state, 'downloading')
      assert.equal(p.source, s.origin)
      // The model file is not visible while bytes arrive.
      assert.equal(fs.existsSync(modelFile(dir, MODEL)), false)
      assert.equal(await verifyFile(modelFile(dir, MODEL), MODEL), false)
      assert.equal(d.cancel(), true)
      const last = await d.settled()
      assert.equal(last.state, 'cancelled')
      assert.equal(d.busy(), false)
      assert.ok(fs.existsSync(partFile(dir, MODEL)))
      assert.equal(fs.existsSync(modelFile(dir, MODEL)), false)
      assert.deepEqual(changes, ['starting', 'cancelled'])
      // The next job resumes from the partial file.
      const s2 = await blobServer().start()
      try {
        d.start(MODEL, { modelsDir: dir, origins: [s2.origin] })
        assert.equal((await d.settled()).state, 'done')
        assert.match(s2.requests.at(-1).range ?? '', /^bytes=\d+-$/)
      } finally {
        await s2.stop()
      }
    } finally {
      await s.stop()
    }
  })

  it('retries a resumed file that fails verification from the start', async () => {
    const s = await blobServer().start()
    const dir = freshDir()
    try {
      fs.mkdirSync(path.join(dir, MODEL.id), { recursive: true })
      fs.writeFileSync(partFile(dir, MODEL), randomBytes(60000)) // wrong bytes
      const d = createDownloader()
      d.start(MODEL, { modelsDir: dir, origins: [s.origin] })
      assert.equal((await d.settled()).state, 'done')
      assert.deepEqual(s.requests.map((r) => r.range ?? null), ['bytes=60000-', null])
      assert.ok(await verifyFile(modelFile(dir, MODEL), MODEL))
    } finally {
      await s.stop()
    }
  })

  it('checks free disk space before any request', async () => {
    const s = await blobServer().start()
    const dir = freshDir()
    try {
      const d = createDownloader({ statfs: async () => ({ bavail: 10, bsize: 4096 }) })
      d.start(MODEL, { modelsDir: dir, origins: [s.origin] })
      const last = await d.settled()
      assert.equal(last.state, 'failed')
      assert.equal(last.reason, 'disk-full')
      assert.equal(s.requests.length, 0)
    } finally {
      await s.stop()
    }
  })

  it('finishes at once when the verified file is already there', async () => {
    const s = await blobServer().start()
    const dir = freshDir()
    try {
      fs.mkdirSync(path.join(dir, MODEL.id), { recursive: true })
      fs.writeFileSync(modelFile(dir, MODEL), BLOB)
      const d = createDownloader()
      d.start(MODEL, { modelsDir: dir, origins: [s.origin] })
      assert.equal((await d.settled()).state, 'done')
      assert.equal(s.requests.length, 0)
    } finally {
      await s.stop()
    }
  })

  it('reports a failure when every origin fails', async () => {
    const a = await blobServer({ getStatus: 404 }).start()
    const dir = freshDir()
    try {
      const d = createDownloader()
      d.start(MODEL, { modelsDir: dir, origins: [a.origin] })
      const last = await d.settled()
      assert.equal(last.state, 'failed')
      assert.equal(last.reason, 'http')
      assert.equal(last.status, 404)
    } finally {
      await a.stop()
    }
  })
})

describe('helpers', () => {
  it('classifies nested fetch failures', () => {
    const refused = new TypeError('fetch failed', { cause: Object.assign(new Error('x'), { code: 'ECONNREFUSED' }) })
    assert.deepEqual(classifyFailure(refused), { reason: 'network', code: 'ECONNREFUSED' })
    assert.deepEqual(classifyFailure(Object.assign(new Error('x'), { code: 'ENOSPC' })), { reason: 'disk-full', code: 'ENOSPC' })
    assert.equal(classifyFailure(new DownloadError('integrity')).reason, 'integrity')
    assert.equal(classifyFailure(new Error('?')).reason, 'unknown')
  })

  it('reads free space for a folder that does not exist yet', async () => {
    const free = await freeBytes(path.join(root, 'no', 'such', 'dir'))
    assert.ok(free === null || free > 0)
  })
})
