// Benchmark adapter for dsh-mnemon (default backend: Mnemon Native, the `mnemon` CLI).
// Runs inside docker. Usage: node --import tsx bench.mts   (env: BENCH_MODE=default|embedding, BENCH_OUT=<file>)
//
// Real code paths driven (no retrieval logic is reimplemented here):
//   write    : MemorySpacesService.remember()  -> MnemonNativeProvider.remember() -> `mnemon remember <text>`
//              (the same call the Agent's remember action and the UI "save" use; exact write, the CLI's own
//              duplicate/conflict detection stays on)
//   ranked   : MemorySpacesService.search({query, limit:10, inspect:true}) on a service whose quality policy is
//              'exhaustive-v1' (no score threshold, no per-tier caps) -> MnemonNativeProvider.search() ->
//              `mnemon --readonly recall <q> --limit 40 --verbose` + the service's lexical recovery
//   deployed : the Layered strategy's own retrieval module, createThreeTierTurn(view).query(), for a first
//              Recall call of a turn: limit 6 -> service.search with DEFAULT quality policy 'strict-v1'
//              -> boundedModelInsights (<= 4 results, <= 3600 chars, <= 1 medium + 1 unknown, no low tier)
// Host glue that is stubbed: the View/ReadGrant object, the Source route's evidence() wrapper (ported 1:1
// from source.ts), the process environment. See README.md.
import { readFileSync, writeFileSync } from 'node:fs'
import { performance } from 'node:perf_hooks'
import { join } from 'node:path'
import * as native from 'dsh-mnemon-provider-mnemon-native'
import { NORMALIZED_RELEVANCE_SCORE } from 'dsh-mnemon-source-memory-spaces/provider-sdk'
import { MemoryProviderCatalog } from 'dsh-mnemon-source-memory-spaces/internal/providers/catalog.ts'
import { MemoryProviderAdapterRegistry } from 'dsh-mnemon-source-memory-spaces/internal/providers/registry.ts'
import { MemorySpacesService } from 'dsh-mnemon-source-memory-spaces/internal/service.ts'
import { createRunner } from 'dsh-mnemon-source-memory-spaces/internal/runner.ts'
import { resolveMemorySpacesConfig } from 'dsh-mnemon-source-memory-spaces/internal/config.ts'
import { createThreeTierTurn } from 'dsh-mnemon-strategy-default-three-tier/retrieval'

const MODE = process.env.BENCH_MODE ?? 'default'
const OUT = process.env.BENCH_OUT ?? (MODE === 'embedding' ? '/out/dsh-mnemon-embedding.json' : '/out/dsh-mnemon.json')
const DATA = '/data'
const DATA_DIR = '/tmp/mnemon-data'
const EMBED_ENDPOINT = 'http://fmg-bench-embedder:11434'
const EMBED_MODEL = 'bge-m3'

const memories: Record<string, { text: string; date: string }> = JSON.parse(readFileSync(join(DATA, 'memories.json'), 'utf8'))
const queries: Record<string, { query: string }> = JSON.parse(readFileSync(join(DATA, 'queries.json'), 'utf8'))
const order = Object.keys(memories).sort((a, b) =>
  memories[a]!.date < memories[b]!.date ? -1 : memories[a]!.date > memories[b]!.date ? 1 : a < b ? -1 : 1)

// ---- assembly: the same objects the Source builds (source.ts createMemorySpacesSource), minus cordis ----
const definition = native.definition
const catalog = new MemoryProviderCatalog([native.descriptor])
const adapters = () => new MemoryProviderAdapterRegistry([{
  id: definition.manifest.typeId,
  scoreSemantics: NORMALIZED_RELEVANCE_SCORE,
  create: (context: any) => definition.create({ ...context, providerInstanceId: definition.manifest.typeId, manifest: definition.manifest }),
} as any])
const embedding = MODE === 'embedding' ? { enabled: true, endpoint: EMBED_ENDPOINT, model: EMBED_MODEL } : undefined
const baseConfig = { dataDir: DATA_DIR, ...(embedding === undefined ? {} : { embedding }) }
const config = resolveMemorySpacesConfig(baseConfig)                                   // default: strict-v1, limit 10
const service = new MemorySpacesService(createRunner(config), config, undefined, undefined, adapters(), catalog)
const configAll = resolveMemorySpacesConfig({ ...baseConfig, recallQuality: { policy: 'exhaustive-v1' } })
let serviceAll: MemorySpacesService   // created after the Memory Space exists on disk

const space = await service.createSpace({ name: 'bench', description: 'benchmark space', providerId: 'mnemon-native', active: true })
serviceAll = new MemorySpacesService(createRunner(configAll), configAll, undefined, undefined, adapters(), catalog)

// ---- ingest ----
const uuidToMem = new Map<string, string>()
const actions: Record<string, number> = {}
const notAdded: string[] = []
let writeFailures = 0
const ingestStart = performance.now()
for (const mid of order) {
  try {
    const r: any = await service.remember({ content: memories[mid]!.text, memoryBodyId: space.id })
    const action = String(r.action ?? 'unknown')
    actions[action] = (actions[action] ?? 0) + 1
    if (typeof r.id === 'string') {
      if (action !== 'added') notAdded.push(`${mid}:${action}->${uuidToMem.get(r.id) ?? 'new'}`)
      // After a merge/update the stored row holds the newest text, so it maps to the newest memory.
      uuidToMem.set(r.id, mid)
    } else writeFailures++
  } catch (e) {
    writeFailures++
  }
}
let embeddedNote = ''
if (MODE === 'embedding') {
  try {
    const fill: any = await service.embedMissing(space.id)
    embeddedNote = ` embed-missing fill result: ${JSON.stringify(fill).slice(0, 200)}.`
  } catch (e) {
    embeddedNote = ` embed-missing failed: ${(e as Error).message.slice(0, 150)}.`
  }
}
const ingestSeconds = (performance.now() - ingestStart) / 1000
const stored: any[] = (await (service as any).list({ memoryBodyIds: [space.id], limit: 1000 })).items
const rows = stored.length

// ---- the Layered strategy turn: View / grant stand-ins and the Source recall route's read() ----
const SOURCE_KEY = 'source:memory-spaces'
const view: any = {
  id: 'view-bench',
  readGrants: [{ id: 'grant-1', sourceInstanceKey: SOURCE_KEY, schema: 'dsh-mnemon.memory-spaces/v1', value: { memoryBodyIds: [space.id] } }],
}
const route: any = { id: 'route-recall', readGrantId: 'grant-1', sourceInstanceKey: SOURCE_KEY, sourceRouteId: 'recall', maxResults: 20, maxCharacters: 16_000 }
const clip = (s: string, n: number) => (s.length <= n ? s : s.slice(0, n - 1) + '…')
// Ported from source.ts: the 'recall' route calls service.search and wraps it with evidence() (budget + provenance).
async function sourceRead(input: any, signal: AbortSignal): Promise<any> {
  const limit = Math.min(route.maxResults ?? 20, input.limit === undefined ? 10 : Math.max(1, Math.min(20, input.limit)))
  const result = await service.search({ query: input.query, ...(input.mode === undefined ? {} : { mode: input.mode }), limit, memoryBodyIds: input.memoryBodyIds }, signal)
  let remaining = route.maxCharacters ?? 16_000
  let truncated = false
  const visible = result.results.slice(0, route.maxResults ?? 20).flatMap((item: any) => {
    if (remaining <= 0) { truncated = true; return [] }
    const content = clip(item.content, remaining)
    remaining -= content.length
    truncated ||= content !== item.content
    return [{ item, content }]
  })
  return {
    id: 'evidence:x', viewId: view.id, routeId: route.id, sourceInstanceKey: SOURCE_KEY, observedAt: new Date().toISOString(),
    items: visible.map(({ item, content }: any) => ({
      id: item.id, text: content,
      ...((item.normalizedScore ?? item.score) === undefined ? {} : { score: item.normalizedScore ?? item.score }),
      ...(item.createdAt === undefined ? {} : { revision: item.createdAt }),
      provenance: {
        memoryBodyId: item.memoryBodyId, memoryBodyName: item.memoryBodyName, memoryProviderId: item.memoryProviderId,
        ...(item.relevanceTier === undefined ? {} : { relevanceTier: item.relevanceTier }),
        ...(item.category === undefined ? {} : { category: item.category }),
        ...(item.createdAt === undefined ? {} : { createdAt: item.createdAt }),
      },
    })),
    truncated: truncated || visible.length < result.results.length,
  }
}
async function deployedFor(q: string): Promise<{ id: string; score: number }[]> {
  const turn = createThreeTierTurn(view)   // a fresh turn: its Recall budget (2 queries) is untouched
  const signal = new AbortController().signal
  const ev: any = await turn.query({ route, input: { query: q }, signal } as any, ((input: any) => sourceRead(input, signal)) as any)
  return ev.items.map((it: any) => ({ id: it.id, score: it.score ?? 0 }))
}
async function rankedFor(q: string): Promise<{ id: string; score: number }[]> {
  const r = await serviceAll.search({ query: q, limit: 10, memoryBodyIds: [space.id], inspect: true })
  return r.results.map((it: any) => ({ id: it.id, score: it.normalizedScore ?? it.score ?? 0 }))
}

const round4 = (x: number) => Math.round(x * 1e4) / 1e4
function mapList(list: { id: string; score: number }[], cap: number, unmappedRef: { n: number }) {
  const seen = new Set<string>()
  const out: { id: string | null; score: number }[] = []
  for (const it of list) {
    const mid = uuidToMem.get(it.id) ?? null
    if (mid === null) unmappedRef.n++
    else { if (seen.has(mid)) continue; seen.add(mid) }
    out.push({ id: mid, score: Number.isFinite(it.score) ? round4(it.score) : 0 })
    if (out.length >= cap) break
  }
  return out
}

// ---- warm-up (not from queries.json) ----
try { await rankedFor('今天天气怎么样，晚饭吃什么'); await deployedFor('今天天气怎么样，晚饭吃什么') } catch { /* warm-up only */ }

const answers: Record<string, any> = {}
const unmapped = { n: 0 }
let failures = 0
const firstErrors: string[] = []
for (const qid of Object.keys(queries)) {
  const q = queries[qid]!.query
  let ranked: any[] = [], deployed: any[] = [], ms = 0
  try {
    ranked = mapList(await rankedFor(q), 10, unmapped)          // readonly snapshot: no access counts
    const t0 = performance.now()
    const dep = await deployedFor(q)                            // live recall: records access, as in real use
    ms = performance.now() - t0
    deployed = mapList(dep, 10, unmapped)
  } catch (e) {
    failures++; ranked = []; deployed = []; ms = 0
    if (firstErrors.length < 3) firstErrors.push((e as Error).message.slice(0, 160))
  }
  answers[qid] = { ranked, deployed, ms: round4(ms) }
}

const emb = MODE === 'embedding'
const out = {
  system: emb ? 'dsh-mnemon-embedding' : 'dsh-mnemon',
  version: 'dsh-mnemon 0.5.45 (commit 1dec529); Memory Spaces 0.5.27, Mnemon Native provider 0.5.11, Layered strategy 0.5.13; mnemon CLI 0.2.13',
  mode: emb
    ? 'default backend Mnemon Native (mnemon CLI, SQLite graph) with its optional embedding mode switched on (config embedding.enabled, shared bge-m3 endpoint); verbatim exact writes through service.remember (no LLM review step); default quality policy strict-v1'
    : 'default backend Mnemon Native (mnemon CLI, SQLite graph, keyword + graph signals, no embeddings: dsh-mnemon default config); verbatim exact writes through service.remember (no LLM review step); default quality policy strict-v1',
  how: 'write: MemorySpacesService.remember({content}) -> MnemonNativeProvider.remember -> `mnemon remember <content> --cat general --imp 3 --source user`; '
    + 'ranked: MemorySpacesService.search({query, limit:10, inspect:true}) with recallQuality policy exhaustive-v1 -> MnemonNativeProvider.search -> `mnemon --readonly recall <q> --limit 40 --verbose` (+ service lexical recovery via `mnemon search` when no anchor evidence); '
    + 'deployed: createThreeTierTurn(view).query() recall route (Layered strategy retrieval.ts: limit 6, admission of <=4 results / 3600 chars / <=1 medium / <=1 unknown, low tier dropped) over MemorySpacesService.search with default policy strict-v1 (low 0.25, high 0.6, candidate pool 18, `mnemon recall --verbose` live, access recorded)',
  ingest_seconds: round4(ingestSeconds),
  llm_tokens: 0,
  llm_cost_cny: 0,
  answers,
  notes: [
    'Backend: the default Provider of dsh-mnemon is Mnemon Native, a thin driver over the separate `mnemon` CLI (npm @mnemon-dev/mnemon 0.2.13, Go binary, SQLite). dsh-mnemon itself holds no retrieval index for Memory Spaces; the CLI returns scored candidates and dsh-mnemon applies its quality policy and per-turn admission.',
    'Write path: the default product write path is an LLM-driven idle review / distillation that decides what to store. No LLM budget: memories were written verbatim through the explicit remember API (service.remember, defaults category=general, importance=3, source=user, no tags/entities). The review step was bypassed and is not measured. llm_tokens=0.',
    'Dates: `mnemon remember` has no date argument (created_at = now). Memories were written in date order within seconds; the memory dates are not represented in the store, so any recency/temporal signal of the CLI sees all memories as written at the same moment.',
    `Dedup/conflict detection of the CLI stayed on. Actions returned by remember: ${JSON.stringify(actions)}. Rows stored: ${rows} of ${order.length}.${notAdded.length ? ' Not plainly added: ' + notAdded.join('; ') + '. A row that an update/merge reused maps to the newest memory id.' : ''}`,
    'ranked uses a second service instance configured with the built-in policy exhaustive-v1 (no score threshold, no tier caps), limit 10, inspect=true (readonly snapshot). Items that carry score 0 are still listed in CLI order. The CLI itself fetches a pool of 40 for smart recall; with embeddings the driver re-ranks that pool by vector similarity.',
    'deployed = the model-facing first Recall call of one turn under the default Layered strategy. dsh-mnemon does NOT inject Memory Space recall automatically before a turn; the model has to call the recall tool (the resident runtime-memory source, which is separate hot memory and is not benchmarked, is the only thing always injected). A quiet question therefore only returns nothing if the model calls recall and the policy admits nothing; a model might also skip the call.',
    'deployed scores are 0 for every item: the strategy admission (boundedModelInsights) drops the score field before the model sees the result; order is the admitted order.',
    'deployed ms covers the whole strategy read (service.search incl. the `mnemon` process launches, any lexical-recovery call, admission), measured around that call only. Each launch is a new process (node launcher -> native binary).',
    'Ordering side effect: deployed runs a live (non-readonly) recall, which records access counts for returned candidates and may influence later questions, as in real use; ranked runs first, readonly, on the same state. Questions were run in id order.',
    'Host glue stubbed: View/ReadGrant objects, the Source recall-route evidence() wrapper (copied logic: limit, 16000-char budget, provenance incl. relevanceTier), `dsh-mnemon` core helper barrel (real input.ts / storage-lock.ts), cordis/Loader (not needed). Nothing of the CLI, the driver, the quality policy or the strategy admission is reimplemented.',
    `Write failures: ${writeFailures}. Question failures: ${failures}${firstErrors.length ? ' (first errors: ' + firstErrors.join(' | ') + ')' : ''}. Returned items that could not be mapped to a memory id: ${unmapped.n}.${embeddedNote}`,
  ],
}
writeFileSync(OUT, JSON.stringify(out, null, 1), 'utf8')
console.log(`done mode=${MODE}: ${Object.keys(answers).length} answers, rows=${rows}, actions=${JSON.stringify(actions)}, failures=${failures}, writeFailures=${writeFailures}, ingest=${ingestSeconds.toFixed(2)}s`)
