// End to end: this add-on serving a real model, the feiyu-memgate plugin's own code turning the
// service snapshot into a gateway configuration, and the real memory gateway storing and recalling
// through it. Not part of `node --test`: it needs an installed copy of this package (node-llama-cpp),
// a GGUF file, a checkout of the dsh-feiyu-memgate plugin and a Python for the gateway.
//
// The gateway runs in another process that shares this one's loopback (in docker: a Python container
// started with --network container:<this container>). This script writes its configuration and
// environment into a shared folder, waits for it to answer, then:
//   - remembers three memories through POST /v1/remember;
//   - recalls each with a paraphrase that shares almost no words with it;
//   - checks that the right memory comes first with a score at or above the preset's min_score.
//
// usage:
//   node e2e-gateway.mjs --addon <installed package dir> --base <dsh-feiyu-memgate checkout>
//        --model <registry id> --file <gguf path> --shared <folder shared with the gateway process>
import { randomBytes } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const args = process.argv.slice(2)
const opt = (name) => {
  const i = args.indexOf(name)
  return i < 0 ? undefined : args[i + 1]
}
const addonDir = opt('--addon')
const baseDir = opt('--base')
const modelId = opt('--model')
const modelFile = opt('--file')
const shared = opt('--shared')
const here = path.dirname(fileURLToPath(import.meta.url))
const imp = (dir, rel) => import(pathToFileURL(path.join(dir, rel)).href)

const { applyAddon, SERVICE_NAME } = await imp(addonDir, 'src/plugin.js')
const { getModel } = await imp(addonDir, 'src/models.js')
const { createFakeCtx } = await imp(here, '../helpers/fake-ctx.js')
const { readEmbed, embedReady } = await imp(baseDir, 'src/tiers.js')
const { gatewayConfig, freePort, TOKEN_ENV } = await imp(baseDir, 'src/gateway.js')
const { readConfig } = await imp(baseDir, 'src/config.js')

const delay = (ms) => new Promise((r) => setTimeout(r, ms))
async function until(fn, ms, step = 200) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    const v = await fn()
    if (v) return v
    await delay(step)
  }
  return null
}

// A memory well over the bge-zh models' 512 tokens: with the default overflow "truncate" it is stored,
// searched by its beginning, and returned whole. It stays under the gateway's default recall budget of
// 1500 characters (core/stages.py, budget), which holds back longer memories whatever their score.
// Its last sentence lies beyond 512 tokens: a question about it shows what truncation costs.
const GARDEN = '薄荷长得很快，每周要剪一次；罗勒怕冷，晚上要搬进屋里。浇水要在早上，夏天每天一次，冬天三天一次。'
const TAIL = '另外，我的车停在地下二层的B区。'
const LONG = '我在阳台上种了三盆薄荷和一盆罗勒。' + GARDEN.repeat(Math.ceil(1400 / GARDEN.length)).slice(0, 1400) + TAIL
const MEMORIES = [
  { id: 'allergy', text: '我对花生过敏，吃一点就会起疹子。' },
  { id: 'cat', text: '家里养了一只橘猫，名字叫团子。' },
  { id: 'trip', text: '下个月要去成都出差，住在春熙路附近。' },
  { id: 'long', text: LONG },
]
// `paraphrase` queries are checked; `loose` ones share no content word with their memory and are
// reported only: under the gateway's default "precise" profile a small model may rightly return
// nothing for them.
const QUERIES = [
  { expect: 'allergy', kind: 'paraphrase', query: '我对哪种食物过敏？' },
  { expect: 'cat', kind: 'paraphrase', query: '我养的猫叫什么名字？' },
  { expect: 'trip', kind: 'paraphrase', query: '我下个月去哪里出差？' },
  { expect: 'long', kind: 'paraphrase', query: '薄荷多久剪一次，罗勒晚上要不要搬进屋，什么时候浇水？' },
  { expect: 'long', kind: 'loose', query: '我在阳台上种了哪些植物？' },
  { expect: 'long', kind: 'tail', query: '我的车停在哪里？' },
  { expect: 'allergy', kind: 'loose', query: '有什么东西我不能吃？' },
  { expect: 'cat', kind: 'loose', query: '我的宠物叫什么？' },
  { expect: 'trip', kind: 'loose', query: '我最近要去哪个城市？' },
]

const model = getModel(modelId)
const dataDir = '/tmp/fmg-embed-e2e'
fs.mkdirSync(path.join(dataDir, 'models', model.id), { recursive: true })
fs.copyFileSync(modelFile, path.join(dataDir, 'models', model.id, model.file))

// 1. The add-on in a host stand-in, nothing pinned; the model is made active through the service's
//    one-click install(), as the base plugin's page does. The file is already there (copied above,
//    no network here): the install job verifies it and activates it without downloading.
const fake = createFakeCtx({ commands: false })
const addon = applyAddon(fake.ctx, {
  modules: { engine: true, command: false },
  model: { activeModel: 'none', autoStart: true, idleUnloadSeconds: 0, threads: 0 },
  download: { origin: 'auto', dataDir },
}, {})
const t0 = Date.now()
await until(() => fake.services.get(SERVICE_NAME), 10000)
const installed = await fake.services.get(SERVICE_NAME).install(model.id, { acceptLicence: model.requiresAcceptance?.name })
console.log(JSON.stringify({ step: 'install', ok: installed.ok, code: installed.code ?? null, activateWhenDone: installed.job?.activateWhenDone ?? null }))
if (!installed.ok) throw new Error(`install refused: ${installed.code}`)
const snapshot = await until(() => {
  const s = fake.services.get(SERVICE_NAME)
  return s?.state === 'ready' || s?.state === 'error' ? s : null
}, 600000)
if (snapshot?.state !== 'ready') throw new Error(`add-on not ready: ${snapshot?.state} ${snapshot?.error}`)
console.log(JSON.stringify({ step: 'addon-ready', model: snapshot.model, ms: Date.now() - t0, fingerprint: snapshot.fingerprint }))

// 2. The base plugin's own code: read the snapshot and write the gateway configuration for its
//    `local` tier (retrieval.tier "auto" picks it while the add-on is ready).
const embed = readEmbed(snapshot)
if (!embedReady(embed)) throw new Error('the base plugin does not see the add-on as ready')
const settings = readConfig({})
const port = await freePort()
const gwData = path.join(shared, 'gw')
fs.mkdirSync(gwData, { recursive: true })
const { text, env, store } = gatewayConfig({ port, dataDir: gwData, settings, facts: { embed } })
if (store.tier !== 'local') throw new Error(`tier ${store.tier}`)
const token = randomBytes(32).toString('base64url')
fs.writeFileSync(path.join(shared, 'gateway.toml'), text + '\n')
const envLines = Object.entries({ ...env, [TOKEN_ENV]: token }).map(([k, v]) => `${k}=${v}`)
fs.writeFileSync(path.join(shared, 'gateway.env.tmp'), envLines.join('\n') + '\n', { mode: 0o600 })
fs.renameSync(path.join(shared, 'gateway.env.tmp'), path.join(shared, 'gateway.env'))
console.log(JSON.stringify({ step: 'config-written', tier: store.tier, storeId: store.id, url: store.table.url, keyEnv: store.table.key_env ?? null }))

// 3. Wait for the gateway, then remember and recall.
const base = `http://127.0.0.1:${port}`
const call = async (method, p, body) => {
  const r = await fetch(base + p, {
    method, headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
  })
  return { status: r.status, json: await r.json().catch(() => null) }
}
const healthy = await until(async () => {
  try {
    return (await fetch(base + '/healthz')).ok
  } catch {
    return false
  }
}, 180000, 500)
if (!healthy) throw new Error('the gateway did not answer')

for (const m of MEMORIES) {
  const r = await call('POST', '/v1/remember', { messages: [{ role: 'user', content: m.text }] })
  console.log(JSON.stringify({ step: 'remember', id: m.id, status: r.status, result: r.json?.status ?? r.json?.error?.code ?? null }))
  if (r.status !== 200 || r.json?.status !== 'ok') throw new Error(`remember failed: ${r.status}`)
}

// The raw cosines straight from the add-on's endpoint, with the prefixes the gateway adds, to explain
// what the gateway returns (numbers only).
const embedTexts = async (texts) => {
  const r = await fetch(`${snapshot.url}/embeddings`, {
    method: 'POST', headers: { authorization: `Bearer ${snapshot.key}`, 'content-type': 'application/json' },
    body: JSON.stringify({ model: snapshot.model, input: texts }),
  })
  return (await r.json()).data.map((d) => d.embedding)
}
const dot = (a, b) => a.reduce((s, x, i) => s + x * b[i], 0)
const memVectors = await embedTexts(MEMORIES.map((m) => snapshot.calibration.document_prefix + m.text))

const minScore = snapshot.calibration.min_score
const rows = []
for (const q of QUERIES) {
  const r = await call('POST', '/v1/recall', { query: q.query, limit: 3 })
  const results = Array.isArray(r.json?.results) ? r.json.results : []
  const top = results[0]
  const topId = top ? MEMORIES.find((m) => m.text === top.text)?.id ?? 'other' : null
  const [qv] = await embedTexts([snapshot.calibration.query_prefix + q.query])
  const row = {
    kind: q.kind, expect: q.expect, status: r.status, failed: r.json?.failed ?? null, more: r.json?.more ?? null,
    rawCosines: Object.fromEntries(MEMORIES.map((m, i) => [m.id, +dot(qv, memVectors[i]).toFixed(4)])),
    top: topId, score: top?.score ?? null, minScore,
    // the gateway reports floor/ceil-mapped scores; this is the cosine behind a score below 1
    cosine: typeof top?.score === 'number' ? +(snapshot.calibration.floor + top.score * (snapshot.calibration.ceil - snapshot.calibration.floor)).toFixed(4) : null,
    returned: results.map((x) => MEMORIES.find((m) => m.text === x.text)?.id ?? 'other'),
    // the length of the text that came back first (a match above means the whole memory came back)
    returnedChars: top ? top.text.length : null,
  }
  row.ok = r.status === 200 && topId === q.expect && typeof row.score === 'number' && row.score >= minScore
  rows.push(row)
  console.log(JSON.stringify({ step: 'recall', ...row }))
}

const status = addon.api.status()
await addon.dispose()
fs.writeFileSync(path.join(shared, 'done'), '')
const checked = rows.filter((r) => r.kind === 'paraphrase')
const passed = checked.filter((r) => r.ok).length
const loose = rows.filter((r) => r.kind === 'loose')
const tail = rows.find((r) => r.kind === 'tail')
// The long memory is over 512 tokens: the bge-zh models must have truncated it.
const mustTruncate = status.maxInputTokens <= 512
console.log(JSON.stringify({
  step: 'summary', model: model.id, passed, of: checked.length, looseFound: loose.filter((r) => r.ok).length, looseOf: loose.length,
  tailFound: tail.ok, longMemoryChars: LONG.length, maxInputTokens: status.maxInputTokens, truncatedInputs: status.truncatedInputs,
}))
process.exit(passed === checked.length && (!mustTruncate || status.truncatedInputs > 0) ? 0 : 1)
