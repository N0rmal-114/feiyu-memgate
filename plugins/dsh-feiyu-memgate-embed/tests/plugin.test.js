// The add-on in a fake host: the service snapshots, the command and every refusal, the licence
// acceptance gate, downloads through a local origin, the engine with the fake worker behind the
// front endpoint, idle unload, and keys that never reach a file or a log.
import assert from 'node:assert/strict'
import { createHash, randomBytes } from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { after, before, describe, it } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { postJson } from '../src/engine.js'
import { MODELS, fingerprint } from '../src/models.js'
import { SERVICE_NAME, UPDATE_EVENT, applyAddon } from '../src/plugin.js'
import { KEY_ENV } from '../src/protocol.js'
import { nodeSpawner } from '../src/spawn.js'
import { STATE_FILE } from '../src/state.js'
import { createFakeCtx } from './helpers/fake-ctx.js'

const FAKE = fileURLToPath(new URL('./fixtures/fake-worker.mjs', import.meta.url))
const delay = (ms) => new Promise((r) => setTimeout(r, ms))
const sha = (b) => createHash('sha256').update(b).digest('hex')

// The real registry with small files in place of the real ones (same ids), plus one fixture entry:
// no shipped model is under licence terms or unverified, and the fixture keeps the acceptance gate
// and the unverified path tested for a model that may be added later. The plugin hands its registry
// to the command, so the command accepts the fixture id too.
const LICENSED_TERMS = Object.freeze({ name: 'Example Terms', url: 'https://example.com/terms' })
const LICENSED = Object.freeze({
  ...MODELS[0], id: 'licensed-model', title: 'Licensed fixture', repository: 'example/licensed-model-GGUF', file: 'licensed-model.gguf',
  license: LICENSED_TERMS, requiresAcceptance: LICENSED_TERMS, parity: 'approximate', status: 'unverified', memoryMB: null,
})
const BLOBS = {}
const TEST_MODELS = [...MODELS, LICENSED].map((m, i) => {
  const blob = randomBytes(30000 + i * 1000)
  BLOBS[m.id] = blob
  return Object.freeze({ ...m, sizeBytes: blob.length, sha256: sha(blob) })
})
const tm = (id) => TEST_MODELS.find((m) => m.id === id)

let origin
let server
let requests = []
let slow = false
before(async () => {
  server = http.createServer(async (req, res) => {
    requests.push({ method: req.method, url: req.url })
    const model = TEST_MODELS.find((m) => req.url === `/${m.repository}/resolve/${m.revision}/${m.file}`)
    if (!model) {
      res.writeHead(404)
      res.end()
      return
    }
    const blob = BLOBS[model.id]
    if (req.method === 'HEAD') {
      res.writeHead(200, { 'content-length': blob.length })
      res.end()
      return
    }
    res.writeHead(200, { 'content-length': blob.length })
    for (let i = 0; i < blob.length; i += 4096) {
      if (res.destroyed) return
      res.write(blob.subarray(i, i + 4096))
      if (slow) await delay(40)
    }
    res.end()
  })
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  origin = `http://127.0.0.1:${server.address().port}`
})
after(() => new Promise((r) => {
  server.closeAllConnections?.()
  server.close(r)
}))

let root
before(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'fmg-embed-plugin-'))
})
after(() => fs.rmSync(root, { recursive: true, force: true }))

async function until(fn, ms = 10000, step = 20) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (await fn()) return true
    await delay(step)
  }
  return false
}

function setup({ config = {}, language, commands = true, models = TEST_MODELS, dataDir = fs.mkdtempSync(path.join(root, 'd-')) } = {}) {
  const fake = createFakeCtx({ commands, language })
  const cfg = {
    modules: { engine: true, command: true, ...config.modules },
    model: { activeModel: 'none', autoStart: true, idleUnloadSeconds: 0, threads: 0, ...config.model },
    download: { origin, dataDir, ...config.download },
  }
  const keys = []
  const base = nodeSpawner()
  const spawnProcess = (spec) => {
    keys.push(spec.env[KEY_ENV])
    return base(spec)
  }
  const addon = applyAddon(fake.ctx, cfg, {
    models, spawnProcess, workerPath: FAKE,
    engineOptions: { backoffMs: 50, readyTimeoutMs: 8000, selfTestTimeoutMs: 5000, stopGraceMs: 2000, extraEnv: { FAKE_STATE_DIR: dataDir } },
    frontOptions: { waitMs: 4000 },
    progressIntervalMs: 50,
  })
  const snap = () => fake.services.get(SERVICE_NAME)
  const update = (patch) => {
    for (const [group, values] of Object.entries(patch)) Object.assign(cfg[group], values)
    fake.ctx.emit('loader/volatile-update', [])
  }
  const place = (id, blob = BLOBS[id]) => {
    const m = tm(id)
    fs.mkdirSync(path.join(dataDir, 'models', id), { recursive: true })
    fs.writeFileSync(path.join(dataDir, 'models', id, m.file), blob)
  }
  const state = () => JSON.parse(fs.readFileSync(path.join(dataDir, STATE_FILE), 'utf8'))
  return { fake, addon, cfg, dataDir, keys, snap, update, place, state, run: fake.run, async close() { await fake.disposeAll() } }
}

describe('service snapshot', () => {
  it('with no model: state no-model, nothing listens, no process', async () => {
    const t = setup()
    try {
      await t.addon.settled()
      const s = t.snap()
      assert.equal(s.version, 2)
      assert.equal(s.state, 'no-model')
      assert.equal(s.url, null)
      assert.equal(s.key, null)
      assert.equal(s.model, null)
      assert.deepEqual(s.models.map((m) => m.id), TEST_MODELS.map((m) => m.id))
      assert.ok(s.models.every((m) => !m.installed && !m.active))
      assert.ok(Object.isFrozen(s) && Object.isFrozen(s.models[0]))
      assert.equal(t.keys.length, 0)
      assert.equal(t.addon.front.endpoint(), null)
      // provided once through ctx.provide, which is what fires internal/service
      assert.deepEqual(t.fake.serviceEvents.map(([n]) => n), [SERVICE_NAME])
    } finally {
      await t.close()
    }
  })

  it('provides nothing while the engine module is off, and appears when it is switched on', async () => {
    const t = setup({ config: { modules: { engine: false } } })
    try {
      await t.addon.settled()
      assert.equal(t.snap(), undefined)
      t.update({ modules: { engine: true } })
      await t.addon.settled()
      assert.equal(t.snap().state, 'no-model')
      t.update({ modules: { engine: false } })
      await t.addon.settled()
      assert.equal(t.snap(), undefined)
      assert.deepEqual(t.fake.serviceEvents.map(([, v]) => (v ? 'provide' : 'withdraw')), ['provide', 'withdraw'])
    } finally {
      await t.close()
    }
  })

  it('runs an installed model: loading, then ready behind a stable endpoint', async () => {
    const t = setup()
    try {
      t.place('bge-small-zh-v1.5')
      await t.addon.settled()
      const r = await t.run('/memgate-embed use bge-small-zh-v1.5')
      assert.equal(r.kind, 'success', r.text)
      assert.ok(await until(() => t.snap().state === 'ready'))
      const s = t.snap()
      const m = tm('bge-small-zh-v1.5')
      assert.equal(s.model, 'bge-small-zh-v1.5')
      assert.equal(s.dimensions, 512)
      assert.equal(s.fingerprint, fingerprint(m))
      assert.deepEqual(s.calibration, { preset: 'bge-small-zh', parity: 'exact', ...m.calibration })
      assert.equal(s.loaded, true)
      assert.match(s.url, /^http:\/\/127\.0\.0\.1:\d+\/v1$/)
      assert.ok(s.key.length >= 43)
      assert.equal(s.maxInputTokens, 512)
      assert.equal(s.overflow, 'truncate')
      // every change was announced, and the service value is the last announced snapshot
      const updates = t.fake.emitted.filter(([n]) => n === UPDATE_EVENT)
      assert.ok(updates.some(([, v]) => v.state === 'loading'))
      assert.equal(updates.at(-1)[1], t.snap())
      // the endpoint serves the gateway's request and refuses another model
      const port = Number(new URL(s.url).port)
      const ok = await postJson({ port, key: s.key, body: { model: 'bge-small-zh-v1.5', input: ['周末去了动物园'] } })
      assert.equal(ok.status, 200)
      assert.equal(ok.json.data[0].embedding.length, 512)
      const other = await postJson({ port, key: s.key, body: { model: 'bge-m3', input: ['x'] } })
      assert.equal(other.status, 404)
      const noKey = await postJson({ port, key: 'x'.repeat(43), body: { model: 'bge-small-zh-v1.5', input: ['x'] } })
      assert.equal(noKey.status, 401)
      assert.ok(s.models.every((x) => typeof x.license === 'string' && x.license && /^https:\/\//.test(x.licenseUrl)))
      // The base plugin's own reader, when its checkout is given (DSH_FEIYU_MEMGATE_SRC): the snapshot
      // passes its checks and counts as ready.
      const base = process.env.DSH_FEIYU_MEMGATE_SRC
      if (base) {
        const { readEmbed, embedReady } = await import(pathToFileURL(path.resolve(base, 'src', 'tiers.js')).href)
        const read = readEmbed(s)
        assert.equal(embedReady(read), true)
        assert.equal(read.key, s.key)
        assert.equal(read.fingerprint, s.fingerprint)
        assert.deepEqual(read.calibration, { preset: 'bge-small-zh', parity: 'exact', floor: 0.4, ceil: 0.8, query_prefix: '', document_prefix: '' })
        assert.deepEqual(read.models.map((x) => x.license), TEST_MODELS.map((x) => x.license.name))
      }
    } finally {
      await t.close()
    }
  })

  it('truncates over-long input by default, counts it, and refuses it after a switch to reject without a restart', async () => {
    const t = setup({ language: 'en', config: { model: { activeModel: 'bge-small-zh-v1.5' } } })
    try {
      t.place('bge-small-zh-v1.5')
      assert.ok(await until(() => t.snap()?.state === 'ready'))
      const s = t.snap()
      const port = Number(new URL(s.url).port)
      const long = '记'.repeat(600) // 602 fake tokens, over the 512 of the model
      const send = async (input) => {
        const response = await fetch(`${s.url}/embeddings`, {
          method: 'POST', headers: { authorization: `Bearer ${s.key}`, 'content-type': 'application/json' },
          body: JSON.stringify({ model: 'bge-small-zh-v1.5', input }),
        })
        return { status: response.status, truncated: response.headers.get('x-truncated-inputs'), json: await response.json() }
      }
      const cut = await send(['短', long, long])
      assert.equal(cut.status, 200)
      assert.equal(cut.truncated, '2')
      assert.equal(cut.json.usage.prompt_tokens, 3 + 512 + 512)
      const status = await t.run('/memgate-embed status')
      assert.match(status.text, /Text over 512 tokens: embedded by its beginning; 2 inputs truncated since start/)
      assert.equal(status.text.includes(long), false)
      t.update({ model: { overflow: 'reject' } })
      await t.addon.settled()
      assert.equal(t.snap().overflow, 'reject')
      const refused = await send([long])
      assert.equal(refused.status, 413)
      assert.equal(refused.json.error.code, 'context_length_exceeded')
      assert.equal(t.keys.length, 1, 'the model process was not restarted')
      assert.equal(Number(new URL(t.snap().url).port), port)
      assert.match((await t.run('/memgate-embed status')).text, /Text over 512 tokens: refused/)
      assert.ok(!t.fake.logs.some(([, m]) => m.includes('记')), 'no text in the logs')
    } finally {
      await t.close()
    }
  })

  it('verifies a file placed by hand, and reports a corrupt one', async () => {
    const t = setup({ config: { model: { activeModel: 'bge-base-zh-v1.5' } } })
    try {
      t.place('bge-base-zh-v1.5', randomBytes(tm('bge-base-zh-v1.5').sizeBytes))
      await t.addon.settled()
      assert.equal(t.snap().state, 'error')
      assert.equal(t.snap().error, 'file-corrupt')
      assert.equal(t.keys.length, 0)
      t.place('bge-base-zh-v1.5')
      t.update({})
      assert.ok(await until(() => t.snap().state === 'ready'))
      assert.equal(t.state().installed['bge-base-zh-v1.5'].sha256, tm('bge-base-zh-v1.5').sha256)
    } finally {
      await t.close()
    }
  })

  it('reports a pinned model that is not downloaded', async () => {
    const t = setup({ config: { model: { activeModel: 'bge-m3' } } })
    try {
      await t.addon.settled()
      assert.equal(t.snap().state, 'error')
      assert.equal(t.snap().error, 'not-installed')
      assert.equal(t.snap().url, null)
    } finally {
      await t.close()
    }
  })

  it('unloads an idle model and loads it again on the next request', async () => {
    const t = setup({ config: { model: { idleUnloadSeconds: 1 } } })
    try {
      t.place('bge-small-zh-v1.5')
      await t.addon.settled()
      await t.run('/memgate-embed use bge-small-zh-v1.5')
      assert.ok(await until(() => t.snap().state === 'ready' && t.snap().loaded))
      const { url, key } = t.snap()
      assert.ok(await until(() => t.snap().loaded === false, 6000))
      assert.equal(t.snap().state, 'ready')
      assert.equal(t.snap().url, url) // same endpoint while unloaded
      const r = await postJson({ port: Number(new URL(url).port), key, body: { model: 'bge-small-zh-v1.5', input: ['醒来'] } })
      assert.equal(r.status, 200)
      assert.ok(await until(() => t.snap().loaded === true))
      assert.equal(t.keys.length, 2)
    } finally {
      await t.close()
    }
  })

  it('stops the worker and withdraws everything on dispose', async () => {
    const t = setup()
    t.place('bge-small-zh-v1.5')
    await t.addon.settled()
    await t.run('/memgate-embed use bge-small-zh-v1.5')
    assert.ok(await until(() => t.snap().state === 'ready'))
    const pid = Number(fs.readFileSync(path.join(t.dataDir, 'pid'), 'utf8'))
    await t.close()
    assert.equal(t.snap(), undefined)
    assert.equal(t.fake.commands.size, 0)
    assert.ok(await until(() => {
      try {
        process.kill(pid, 0)
        return false
      } catch {
        return true
      }
    }))
  })
})

describe('command', () => {
  it('is registered only while the command module is on and the commands service exists', async () => {
    const t = setup()
    const u = setup({ commands: false })
    try {
      await t.addon.settled()
      await u.addon.settled()
      assert.ok(t.fake.commands.has('memgate-embed'))
      assert.equal(u.fake.commands.size, 0)
      assert.equal(u.snap().state, 'no-model') // the service does not need the command
      t.update({ modules: { command: false } })
      await t.addon.settled()
      assert.equal(t.fake.commands.size, 0)
    } finally {
      await t.close()
      await u.close()
    }
  })

  for (const line of [
    '/memgate-embed frobnicate',
    '/memgate-embed status now',
    '/memgate-embed list --everything',
    '/memgate-embed add',
    '/memgate-embed add nomic-embed-text',
    '/memgate-embed add bge-m3 bge-small-zh-v1.5',
    '/memgate-embed add bge-m3 --accept-licence',
    '/memgate-embed add embeddinggemma-300m',
    '/memgate-embed add https://example.com/model.gguf',
    '/memgate-embed use',
    '/memgate-embed remove ../../etc',
    '/memgate-embed cancel bge-m3',
    '/memgate-embed origin a b',
    `/memgate-embed ${'x'.repeat(500)}`,
  ]) {
    it(`refuses: ${line.slice(0, 60)}`, async () => {
      const t = setup()
      try {
        await t.addon.settled()
        const r = await t.run(line)
        assert.equal(r.kind, 'error')
        assert.ok(r.text.length > 0)
        assert.equal(t.addon.api.status().download, null)
      } finally {
        await t.close()
      }
    })
  }

  it('answers in Chinese by default and in English when the UI language is English', async () => {
    const zh = setup()
    const en = setup({ language: 'en-US' })
    try {
      await zh.addon.settled()
      await en.addon.settled()
      assert.match((await zh.run('/memgate-embed')).text, /状态：未选择模型/)
      assert.match((await en.run('/memgate-embed status')).text, /State: no model/)
      assert.match((await en.run('/memgate-embed list')).text, /bge-small-zh-v1\.5: [\d.]+ kB, 512 d, MIT/)
    } finally {
      await zh.close()
      await en.close()
    }
  })

  it('adds a model in the background, shows progress, then installs it', async () => {
    const t = setup({ language: 'en' })
    slow = true
    requests = []
    try {
      await t.addon.settled()
      const r = await t.run('/memgate-embed add bge-small-zh-v1.5')
      assert.equal(r.kind, 'success', r.text)
      assert.match(r.text, /Started downloading bge-small-zh-v1\.5/)
      assert.match(r.text, /File: CompendiumLabs\/bge-small-zh-v1\.5-gguf\/bge-small-zh-v1\.5-f16\.gguf/)
      assert.match(r.text, /Licence: MIT/)
      assert.ok(r.text.includes(`Source: ${origin}`))
      const busy = await t.run('/memgate-embed add bge-base-zh-v1.5')
      assert.equal(busy.kind, 'error')
      assert.match(busy.text, /already running/)
      assert.equal(t.addon.api.status().download.id, 'bge-small-zh-v1.5')
      // the partial file is not a model
      assert.equal(t.snap().models.find((m) => m.id === 'bge-small-zh-v1.5').installed, false)
      assert.equal(t.snap().downloading, 'bge-small-zh-v1.5')
      const status = await t.run('/memgate-embed status')
      assert.match(status.text, /Downloading bge-small-zh-v1\.5|Preparing to download/)
      assert.ok(await until(() => t.snap().models.find((m) => m.id === 'bge-small-zh-v1.5').installed, 15000))
      assert.equal(t.snap().downloading, null)
      assert.equal(t.snap().state, 'no-model') // downloaded, not chosen
      assert.match((await t.run('/memgate-embed status')).text, /finished and verified/)
      assert.match((await t.run('/memgate-embed add bge-small-zh-v1.5')).text, /already downloaded/)
      assert.ok(requests.every((q) => q.url.includes('bge-small-zh')))
    } finally {
      slow = false
      await t.close()
    }
  })

  it('cancels a download', async () => {
    const t = setup()
    slow = true
    try {
      await t.addon.settled()
      assert.equal((await t.run('/memgate-embed cancel')).kind, 'error')
      await t.run('/memgate-embed add bge-base-zh-v1.5')
      assert.ok(await until(() => (t.addon.api.status().download?.bytes ?? 0) > 0))
      const r = await t.run('/memgate-embed cancel')
      assert.equal(r.kind, 'success')
      assert.ok(await until(() => t.addon.api.status().download === null))
      assert.equal(t.addon.api.status().lastDownload.state, 'cancelled')
      assert.ok(fs.existsSync(path.join(t.dataDir, 'models', 'bge-base-zh-v1.5', `${tm('bge-base-zh-v1.5').file}.part`)))
      assert.equal(t.snap().models.find((m) => m.id === 'bge-base-zh-v1.5').installed, false)
    } finally {
      slow = false
      await t.close()
    }
  })

  it('downloads nothing of a model under licence terms before they are accepted', async () => {
    const t = setup({ language: 'en' })
    requests = []
    try {
      await t.addon.settled()
      const r = await t.run('/memgate-embed add licensed-model')
      assert.equal(r.kind, 'error')
      assert.match(r.text, /Example Terms: https:\/\/example\.com\/terms/)
      assert.match(r.text, /--accept-licence/)
      await delay(200)
      assert.equal(requests.length, 0)
      assert.equal(fs.existsSync(path.join(t.dataDir, STATE_FILE)), false)
      assert.equal(fs.existsSync(path.join(t.dataDir, 'models', 'licensed-model')), false)
      // the list marks it, offers --all for it, and the help names the flag
      const listed = await t.run('/memgate-embed list --all')
      assert.match(listed.text, /licensed-model: .*needs --accept-licence.*unverified/)
      assert.match((await t.run('/memgate-embed list')).text, /Add --all/)
      assert.match((await t.run('/memgate-embed help')).text, /add <id> \[--accept-licence\]/)
      const ok = await t.run('/memgate-embed add licensed-model --accept-licence')
      assert.equal(ok.kind, 'success', ok.text)
      // an unverified registry entry says so when it is added
      assert.match(ok.text, /has not been tested/)
      const acceptance = t.state().acceptances['licensed-model']
      assert.equal(acceptance.name, 'Example Terms')
      assert.equal(acceptance.url, 'https://example.com/terms')
      assert.ok(!Number.isNaN(Date.parse(acceptance.acceptedAt)))
      assert.ok(await until(() => t.snap().models.find((m) => m.id === 'licensed-model').installed))
    } finally {
      await t.close()
    }
  })

  it('offers neither --all nor the licence flag with the shipped registry', async () => {
    const t = setup({ language: 'en', models: TEST_MODELS.filter((m) => m.id !== 'licensed-model') })
    try {
      await t.addon.settled()
      const help = (await t.run('/memgate-embed help')).text
      assert.equal(help.includes('--all'), false)
      assert.equal(help.includes('--accept-licence'), false)
      assert.equal((await t.run('/memgate-embed list')).text.includes('--all'), false)
    } finally {
      await t.close()
    }
  })

  it('switches, refuses to remove the active model, removes others', async () => {
    const t = setup()
    try {
      t.place('bge-small-zh-v1.5')
      t.place('bge-base-zh-v1.5')
      await t.addon.settled()
      assert.equal((await t.run('/memgate-embed use bge-m3')).kind, 'error') // not downloaded
      assert.equal((await t.run('/memgate-embed use bge-small-zh-v1.5')).kind, 'success')
      assert.ok(await until(() => t.snap().state === 'ready'))
      assert.equal((await t.run('/memgate-embed remove bge-small-zh-v1.5')).kind, 'error')
      // bge-base was placed by hand: no record yet, but the file is there
      const removed = await t.run('/memgate-embed remove bge-base-zh-v1.5')
      assert.equal(removed.kind, 'success', removed.text)
      assert.equal(fs.existsSync(path.join(t.dataDir, 'models', 'bge-base-zh-v1.5')), false)
      assert.equal((await t.run('/memgate-embed remove bge-base-zh-v1.5')).kind, 'error')
      assert.equal((await t.run('/memgate-embed use none')).kind, 'success')
      assert.ok(await until(() => t.snap().state === 'no-model'))
      assert.equal(t.snap().url, null)
      assert.equal((await t.run('/memgate-embed remove bge-small-zh-v1.5')).kind, 'success')
    } finally {
      await t.close()
    }
  })

  it('lets the settings win over the command', async () => {
    const t = setup({ config: { model: { activeModel: 'bge-small-zh-v1.5' }, download: { origin: 'hf-mirror' } } })
    try {
      t.place('bge-small-zh-v1.5')
      t.place('bge-base-zh-v1.5')
      await t.addon.settled()
      const use = await t.run('/memgate-embed use bge-base-zh-v1.5')
      assert.equal(use.kind, 'error')
      assert.match(use.text, /activeModel/)
      const origin = await t.run('/memgate-embed origin huggingface')
      assert.equal(origin.kind, 'error')
      assert.match((await t.run('/memgate-embed origin')).text, /hf-mirror/)
    } finally {
      await t.close()
    }
  })

  it('stores the download source', async () => {
    const t = setup({ config: { download: { origin: 'auto' } }, language: 'en' })
    try {
      await t.addon.settled()
      assert.match((await t.run('/memgate-embed origin')).text, /auto/)
      assert.equal((await t.run('/memgate-embed origin hf-mirror')).kind, 'success')
      assert.equal(t.state().origin, 'hf-mirror')
      assert.equal((await t.run('/memgate-embed origin https://mirror.example:8443')).kind, 'success')
      assert.equal(t.state().origin, 'https://mirror.example:8443')
      for (const bad of ['ftp://mirror.example', 'https://user:pw@mirror.example', 'https://mirror.example/path', 'mirror.example']) {
        const r = await t.run(`/memgate-embed origin ${bad}`)
        assert.equal(r.kind, 'error', bad)
      }
      assert.equal((await t.run('/memgate-embed origin auto')).kind, 'success')
      assert.equal(t.state().origin, null)
    } finally {
      await t.close()
    }
  })
})

describe('control methods (service version 2)', () => {
  const CODES = ['unknown-model', 'needs-acceptance', 'busy', 'already-installed', 'not-installed', 'pinned', 'active', 'downloading', 'no-job', 'invalid-origin', 'origin-pinned', 'unavailable', 'failed']

  it('install: download, verify, activate, ready, with progress announced and the job reported', async () => {
    const t = setup()
    slow = true
    try {
      await t.addon.settled()
      const svc = t.snap()
      for (const name of ['list', 'add', 'use', 'install', 'cancel', 'remove', 'job', 'setOrigin']) assert.equal(typeof svc[name], 'function', name)
      const before = t.fake.emitted.filter(([n]) => n === UPDATE_EVENT).length
      const r = await svc.install('bge-small-zh-v1.5')
      assert.equal(r.ok, true, JSON.stringify(r))
      assert.equal(r.id, 'bge-small-zh-v1.5')
      assert.equal(r.job.activateWhenDone, true)
      // the page may go away: nothing more is called, and the add-on finishes on its own
      assert.ok(await until(() => t.snap().state === 'ready' && t.snap().model === 'bge-small-zh-v1.5', 15000))
      assert.equal(t.state().active, 'bge-small-zh-v1.5')
      const updates = t.fake.emitted.filter(([n]) => n === UPDATE_EVENT).slice(before).map(([, v]) => v)
      assert.ok(updates.some((u) => u.progress?.state === 'downloading' && u.progress.bytes > 0), 'progress went out through the update event')
      assert.equal(updates.at(-1).progress.state, 'done')
      const job = await t.snap().job()
      assert.deepEqual({ ...job, job: { ...job.job } }, {
        ok: true,
        job: { id: 'bge-small-zh-v1.5', state: 'done', bytes: tm('bge-small-zh-v1.5').sizeBytes, total: tm('bge-small-zh-v1.5').sizeBytes, speed: 0, source: origin, activateWhenDone: false, error: null, activated: true },
      })
      const list = await t.snap().list()
      assert.equal(list.ok, true)
      assert.equal(list.active, 'bge-small-zh-v1.5')
      assert.equal(list.pinned, null)
      const row = list.models.find((m) => m.id === 'bge-small-zh-v1.5')
      assert.equal(row.installed, true)
      assert.equal(row.active, true)
      assert.equal(row.title, 'BGE small zh v1.5')
      assert.deepEqual(row.memoryMB, { loaded: 188, peak: 219 })
      assert.equal(row.license, 'MIT')
      // installing it again only confirms it
      const again = await t.snap().install('bge-small-zh-v1.5')
      assert.deepEqual(again, { ok: true, id: 'bge-small-zh-v1.5', job: null, active: 'bge-small-zh-v1.5' })
      const add = await t.snap().add('bge-small-zh-v1.5')
      assert.equal(add.code, 'already-installed')
      const rm = await t.snap().remove('bge-small-zh-v1.5')
      assert.equal(rm.code, 'active')
    } finally {
      slow = false
      await t.close()
    }
  })

  it('the licence gate: nothing is fetched until acceptLicence names the licence, and the acceptance is recorded', async () => {
    const t = setup()
    requests = []
    try {
      await t.addon.settled()
      const svc = t.snap()
      const row = (await svc.list()).models.find((m) => m.id === 'licensed-model')
      assert.deepEqual(row.requiresAcceptance, { name: 'Example Terms', url: 'https://example.com/terms' })
      for (const options of [undefined, {}, { acceptLicence: true }, { acceptLicence: 'MIT' }]) {
        const r = await svc.install('licensed-model', options)
        assert.equal(r.code, 'needs-acceptance')
        assert.deepEqual(r.licence, row.requiresAcceptance)
        assert.equal((await svc.add('licensed-model', options)).code, 'needs-acceptance')
      }
      await delay(100)
      assert.equal(requests.length, 0)
      assert.equal(fs.existsSync(path.join(t.dataDir, STATE_FILE)), false)
      const ok = await svc.install('licensed-model', { acceptLicence: 'https://example.com/terms' })
      assert.equal(ok.ok, true, JSON.stringify(ok))
      assert.ok(!Number.isNaN(Date.parse(ok.acceptedAt)))
      assert.equal(t.state().acceptances['licensed-model'].name, 'Example Terms')
      assert.ok(await until(() => t.snap().state === 'ready' && t.snap().model === 'licensed-model', 15000))
    } finally {
      await t.close()
    }
  })

  it('cancel mid-download: no activation, the part is kept, a second cancel has no job', async () => {
    const t = setup()
    slow = true
    try {
      await t.addon.settled()
      const svc = t.snap()
      assert.equal((await svc.install('bge-base-zh-v1.5')).ok, true)
      assert.ok(await until(async () => ((await t.snap().job()).job?.bytes ?? 0) > 0))
      assert.equal((await t.snap().add('bge-m3')).code, 'busy')
      assert.equal((await t.snap().remove('bge-base-zh-v1.5')).code, 'downloading')
      assert.deepEqual(await t.snap().cancel(), { ok: true, id: 'bge-base-zh-v1.5' })
      assert.ok(await until(async () => (await t.snap().job()).job?.state === 'cancelled'))
      const { job } = await t.snap().job()
      assert.equal(job.activated, false)
      assert.equal(t.snap().model, null)
      assert.equal(t.snap().state, 'no-model')
      assert.ok(fs.existsSync(path.join(t.dataDir, 'models', 'bge-base-zh-v1.5', `${tm('bge-base-zh-v1.5').file}.part`)))
      const again = await t.snap().cancel()
      assert.equal(again.code, 'no-job')
      assert.equal(typeof again.message, 'string')
    } finally {
      slow = false
      await t.close()
    }
  })

  it('a restart resumes the active choice from the state file', async () => {
    const first = setup()
    const dataDir = first.dataDir
    try {
      await first.addon.settled()
      assert.equal((await first.snap().install('bge-small-zh-v1.5')).ok, true)
      assert.ok(await until(() => first.snap().state === 'ready', 15000))
    } finally {
      await first.close()
    }
    const second = setup({ dataDir })
    try {
      assert.ok(await until(() => second.snap()?.state === 'ready', 15000))
      assert.equal(second.snap().model, 'bge-small-zh-v1.5')
      assert.equal((await second.snap().list()).active, 'bge-small-zh-v1.5')
    } finally {
      await second.close()
    }
  })

  it('a model pinned in the settings wins: use and install say so', async () => {
    const t = setup({ config: { model: { activeModel: 'bge-base-zh-v1.5' } } })
    try {
      t.place('bge-small-zh-v1.5')
      t.place('bge-base-zh-v1.5')
      assert.ok(await until(() => t.snap()?.state === 'ready'))
      const svc = t.snap()
      for (const r of [await svc.install('bge-small-zh-v1.5'), await svc.use('bge-small-zh-v1.5'), await svc.use(null)]) {
        assert.equal(r.code, 'pinned')
        assert.equal(r.pinned, 'bge-base-zh-v1.5')
      }
      assert.equal((await svc.list()).pinned, 'bge-base-zh-v1.5')
      assert.equal(t.snap().model, 'bge-base-zh-v1.5')
      // the pinned model itself installs (and stays active)
      const same = await svc.install('bge-base-zh-v1.5')
      assert.equal(same.ok, true, JSON.stringify(same))
    } finally {
      await t.close()
    }
  })

  it('never throws: bad arguments, other refusals and a stopped add-on are results with fixed codes', async () => {
    const t = setup({ config: { download: { origin: 'hf-mirror' } } })
    try {
      await t.addon.settled()
      const svc = t.snap()
      const results = [
        [await svc.add(42), 'unknown-model'],
        [await svc.install('nomic-embed-text'), 'unknown-model'],
        [await svc.use({}), 'unknown-model'],
        [await svc.use('bge-m3'), 'not-installed'],
        [await svc.remove(undefined), 'unknown-model'],
        [await svc.remove('bge-m3'), 'not-installed'],
        [await svc.setOrigin(5), 'invalid-origin'],
        [await svc.setOrigin('https://mirror.example'), 'origin-pinned'],
        [await svc.cancel(), 'no-job'],
      ]
      for (const [r, code] of results) {
        assert.equal(r.ok, false)
        assert.equal(r.code, code)
        assert.ok(CODES.includes(r.code))
        assert.equal(typeof r.message, 'string')
      }
      assert.deepEqual(await svc.job(), { ok: true, job: null })
      t.update({ download: { origin: 'auto' } })
      await t.addon.settled()
      assert.equal((await svc.setOrigin('ftp://x')).code, 'invalid-origin')
      assert.deepEqual(await svc.setOrigin('hf-mirror'), { ok: true, origin: { value: 'hf-mirror', pinned: false } })
      assert.deepEqual(await svc.use(null), { ok: true, active: null })
      await t.close()
      assert.equal((await svc.list()).code, 'unavailable')
      assert.equal((await svc.install('bge-small-zh-v1.5')).code, 'unavailable')
    } finally {
      await t.close()
    }
  })
})

describe('secrets', () => {
  it('never writes the endpoint key or a worker key to a file or a log', async () => {
    const t = setup({ config: { model: { idleUnloadSeconds: 1 } } })
    try {
      t.place('bge-small-zh-v1.5')
      await t.addon.settled()
      await t.run('/memgate-embed use bge-small-zh-v1.5')
      assert.ok(await until(() => t.snap().state === 'ready'))
      const { url, key } = t.snap()
      await postJson({ port: Number(new URL(url).port), key, body: { model: 'bge-small-zh-v1.5', input: ['x'] } })
      assert.ok(await until(() => t.snap().loaded === false, 6000))
      await postJson({ port: Number(new URL(url).port), key, body: { model: 'bge-small-zh-v1.5', input: ['y'] } })
      const secrets = [key, ...t.keys]
      assert.ok(secrets.length >= 3)
      const files = []
      const walk = (dir) => {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
          const p = path.join(dir, entry.name)
          if (entry.isDirectory()) walk(p)
          else files.push(p)
        }
      }
      walk(t.dataDir)
      assert.ok(files.some((f) => f.endsWith('embed-worker.log')))
      for (const file of files) {
        const text = fs.readFileSync(file, 'latin1')
        for (const s of secrets) assert.equal(text.includes(s), false, `${path.basename(file)} holds a key`)
      }
      const logs = t.fake.logs.map(([, m]) => m).join('\n')
      for (const s of secrets) assert.equal(logs.includes(s), false)
    } finally {
      await t.close()
    }
  })
})
