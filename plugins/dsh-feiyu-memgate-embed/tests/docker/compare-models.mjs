// Runs the real worker (node-llama-cpp) for each model and compares its vectors with an Ollama server
// that serves the same model. Not part of `node --test`: it needs node-llama-cpp installed (an
// installed copy of this package), the GGUF files, and an Ollama endpoint. Run it in a container.
//
// usage:
//   node compare-models.mjs --addon <installed package dir> --data <feiyu-memgate bench/dev dir> --out <file.json>
//        <id>=<gguf path>@<ollama base url, e.g. http://host:11434/v1>#<ollama model name> ...
//
// For every model it reports: load time (spawn to self-test), worker memory after load and at its
// peak, query latency, the cosine between this worker's and Ollama's vector for the same text, and
// the agreement of the 20 x 40 query-memory score matrices (mean and max absolute difference, top-1
// and top-3 agreement), with the gateway's query prefix applied as the gateway applies it, and also
// without it. It also sends inputs near and over the model's limit (in reject mode), and one text
// well over it, truncated, compared with Ollama's truncation of the same text.
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const args = process.argv.slice(2)
const opt = (name) => {
  const i = args.indexOf(name)
  if (i < 0) return undefined
  const [value] = args.splice(i, 2).slice(1)
  return value
}
const addon = opt('--addon')
const dataDir = opt('--data')
const outFile = opt('--out')
const threads = Number(opt('--threads') ?? 0)
const specs = args.map((a) => {
  const m = /^([^=]+)=([^@]+)@([^#]+)#(.+)$/.exec(a)
  if (!m) throw new Error(`bad model argument: ${a}`)
  return { id: m[1], file: m[2], ollama: m[3], ollamaModel: m[4] }
})
const load = (rel) => import(pathToFileURL(path.join(addon, rel)).href)
const { createEngine, postJson } = await load('src/engine.js')
const { nodeSpawner } = await load('src/spawn.js')
const { getModel } = await load('src/models.js')

const mem = JSON.parse(fs.readFileSync(path.join(dataDir, 'memories.json'), 'utf8'))
const qry = JSON.parse(fs.readFileSync(path.join(dataDir, 'queries.json'), 'utf8'))
const memTexts = Object.keys(mem).slice(0, 40).map((k) => mem[k].text)
const qTexts = Object.keys(qry).slice(0, 20).map((k) => qry[k].query)

const unit = (v) => {
  const n = Math.sqrt(v.reduce((s, x) => s + x * x, 0)) || 1
  return v.map((x) => x / n)
}
const dot = (a, b) => a.reduce((s, x, i) => s + x * b[i], 0)
function proc(pid) {
  try {
    const text = fs.readFileSync(`/proc/${pid}/status`, 'utf8')
    const kb = (k) => Number(new RegExp(`${k}:\\s+(\\d+) kB`).exec(text)?.[1] ?? NaN)
    return { rssMB: Math.round(kb('VmRSS') / 1024), peakMB: Math.round(kb('VmHWM') / 1024) }
  } catch {
    return { rssMB: null, peakMB: null }
  }
}
const pct = (xs, p) => {
  const s = [...xs].sort((a, b) => a - b)
  return +s[Math.min(s.length - 1, Math.floor(p * s.length))].toFixed(1)
}

async function ollama(base, model, texts) {
  const out = []
  for (let i = 0; i < texts.length; i += 8) {
    const r = await fetch(`${base.replace(/\/$/, '')}/embeddings`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ model, input: texts.slice(i, i + 8) }),
    })
    if (!r.ok) throw new Error(`ollama HTTP ${r.status}`)
    const j = await r.json()
    out.push(...j.data.sort((a, b) => a.index - b.index).map((d) => unit(d.embedding)))
  }
  return out
}

function compareMatrices(qa, ma, qb, mb) {
  let mad = 0
  let max = 0
  let n = 0
  let top1 = 0
  let top3set = 0
  let top3order = 0
  const top = (row) => row.map((v, i) => [v, i]).sort((x, y) => y[0] - x[0]).slice(0, 3).map((x) => x[1])
  for (let i = 0; i < qa.length; i++) {
    const ra = ma.map((m) => dot(qa[i], m))
    const rb = mb.map((m) => dot(qb[i], m))
    for (let j = 0; j < ra.length; j++) {
      const d = Math.abs(ra[j] - rb[j])
      mad += d
      n += 1
      if (d > max) max = d
    }
    const ta = top(ra)
    const tb = top(rb)
    if (ta[0] === tb[0]) top1 += 1
    if ([...ta].sort().join() === [...tb].sort().join()) top3set += 1
    if (ta.join() === tb.join()) top3order += 1
  }
  return { meanAbsDiff: +(mad / n).toFixed(5), maxAbsDiff: +max.toFixed(4), top1: `${top1}/${qa.length}`, top3Set: `${top3set}/${qa.length}`, top3Order: `${top3order}/${qa.length}` }
}

function pairCos(a, b) {
  const c = a.map((v, i) => dot(v, b[i]))
  return { mean: +(c.reduce((s, x) => s + x, 0) / c.length).toFixed(6), min: +Math.min(...c).toFixed(6) }
}

const CJK = '的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取据处理府研质'
async function contextProbe(ep, model) {
  const results = []
  const max = ep.maxInputTokens
  for (const chars of [Math.floor(max * 0.5), Math.floor(max * 0.9), max - 4, max * 3]) {
    let text = ''
    for (let i = 0; i < chars; i++) text += CJK[(i * 7) % CJK.length]
    const r = await postJson({ port: ep.port, key: ep.key, timeoutMs: 120000, body: { model, input: [text] }, headers: { 'x-embed-overflow': 'reject' } })
    results.push({ chars, status: r.status, tokens: r.json?.usage?.prompt_tokens ?? null, code: r.json?.error?.code ?? null })
  }
  return results
}

// One text well over the model's limit (CJK with some English words and punctuation), embedded by
// this worker (truncated, the default) and by Ollama (which truncates by default), and refused when
// the request asks for "reject".
function longText(maxTokens) {
  const words = ['memory', 'gateway', 'garden', 'river', 'coffee', 'train', 'winter', 'music']
  let text = ''
  for (let i = 0; text.length < maxTokens * 2.5; i++) {
    text += CJK[(i * 11) % CJK.length]
    if (i % 37 === 36) text += ` ${words[i % words.length]} `
    if (i % 53 === 52) text += '。'
  }
  return text
}
async function longTextProbe(ep, model, spec) {
  const text = longText(ep.maxInputTokens)
  const ours = await postJson({ port: ep.port, key: ep.key, timeoutMs: 120000, body: { model, input: [text] } })
  const refused = await postJson({ port: ep.port, key: ep.key, timeoutMs: 120000, body: { model, input: [text] }, headers: { 'x-embed-overflow': 'reject' } })
  const r = await fetch(`${spec.ollama.replace(/\/$/, '')}/embeddings`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ model: spec.ollamaModel, input: [text] }),
  })
  const theirs = await r.json()
  return {
    chars: text.length,
    status: ours.status,
    truncatedHeader: ours.headers?.['x-truncated-inputs'] ?? null,
    ourTokens: ours.json?.usage?.prompt_tokens ?? null,
    ollamaStatus: r.status,
    ollamaTokens: theirs?.usage?.prompt_tokens ?? null,
    cosine: ours.status === 200 && r.ok ? +dot(unit(ours.json.data[0].embedding), unit(theirs.data[0].embedding)).toFixed(6) : null,
    rejectStatus: refused.status,
    rejectCode: refused.json?.error?.code ?? null,
  }
}

const report = []
for (const spec of specs) {
  const m = getModel(spec.id)
  const row = { id: spec.id, file: path.basename(spec.file) }
  let pid = null
  const base = nodeSpawner()
  const logs = []
  const engine = createEngine({
    spawnProcess: (s) => {
      const p = base(s)
      pid = p.pid
      return p
    },
    writeLog: (d) => logs.push(String(d)),
    readyTimeoutMs: 600000,
    selfTestTimeoutMs: 120000,
  })
  try {
    const t0 = performance.now()
    await engine.load({ modelId: m.id, modelPath: spec.file, dimensions: m.dimensions, contextTokens: m.contextTokens, threads })
    const ep0 = await engine.ensure(600000)
    if (!ep0) throw new Error(`engine ${engine.status().phase} ${engine.status().error}: ${logs.join('').slice(-1500)}`)
    const st = engine.status()
    const ep = { ...ep0, maxInputTokens: st.maxInputTokens }
    row.readyMs = Math.round(performance.now() - t0)
    row.workerLoadMs = st.loadMs
    row.maxInputTokens = st.maxInputTokens
    row.afterLoad = proc(pid)
    const embed = async (texts) => {
      const out = []
      for (let i = 0; i < texts.length; i += 32) {
        const r = await postJson({ port: ep.port, key: ep.key, timeoutMs: 120000, body: { model: m.id, input: texts.slice(i, i + 32) } })
        if (r.status !== 200) throw new Error(`worker HTTP ${r.status} ${JSON.stringify(r.json)}`)
        out.push(...r.json.data.map((d) => unit(d.embedding)))
      }
      return out
    }
    const prefix = m.calibration.query_prefix
    const lat = []
    const qOurs = []
    for (const q of qTexts) {
      const s = performance.now()
      qOurs.push((await embed([prefix + q]))[0])
      lat.push(performance.now() - s)
    }
    const qOursPlain = prefix ? await embed(qTexts) : qOurs
    const s1 = performance.now()
    const mOurs = await embed(memTexts.map((t) => m.calibration.document_prefix + t))
    row.memoryPerTextMs = +((performance.now() - s1) / memTexts.length).toFixed(1)
    row.queryLatencyMs = { p50: pct(lat, 0.5), p95: pct(lat, 0.95) }
    row.context = await contextProbe(ep, m.id)
    row.longText = await longTextProbe(ep, m.id, spec)
    row.peak = proc(pid)
    const qOl = await ollama(spec.ollama, spec.ollamaModel, qTexts.map((q) => prefix + q))
    const qOlPlain = prefix ? await ollama(spec.ollama, spec.ollamaModel, qTexts) : qOl
    const mOl = await ollama(spec.ollama, spec.ollamaModel, memTexts.map((t) => m.calibration.document_prefix + t))
    row.cosineWithOllama = { queries: pairCos(qOurs, qOl), memories: pairCos(mOurs, mOl) }
    row.matrixVsOllama = compareMatrices(qOurs, mOurs, qOl, mOl)
    if (prefix) {
      row.matrixVsOllamaNoPrefix = compareMatrices(qOursPlain, mOurs, qOlPlain, mOl)
      // The prefix must change the query vectors (it reaches the model) the same way on both sides.
      row.prefixEffect = { ours: pairCos(qOurs, qOursPlain).mean, ollama: pairCos(qOl, qOlPlain).mean }
    }
    const all = qOurs.flatMap((q) => mOurs.map((mm) => dot(q, mm)))
    row.meanCosine = +(all.reduce((s, x) => s + x, 0) / all.length).toFixed(4)
    row.ok = true
  } catch (error) {
    row.ok = false
    row.error = String(error?.message ?? error).slice(0, 2000)
  } finally {
    await engine.stop()
  }
  console.log(JSON.stringify(row))
  report.push(row)
}
if (outFile) fs.writeFileSync(outFile, JSON.stringify(report, null, 2))
