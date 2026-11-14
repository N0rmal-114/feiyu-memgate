// Benchmark adapter for meow-memory (npm package, DSH memory plugin).
// Runs inside docker with --network none. Usage: node bench.mjs <data_dir> <out_file>
//
// Real code paths driven (nothing of the storage/retrieval logic is reimplemented):
//   write    : the plugin's `memory_remember` tool  -> execute(args, exec)
//   ranked   : the plugin's `memory_search` tool    -> execute({query, k: 10}, exec)
//   deployed : buildHitInjection(...), the per-message "keyword hit" injection the host runs
//              before every user turn (hitTopK default 2)
// Host glue that is stubbed: the tool registry (a collector function), the `exec` object
// (agent.session.header.cwd / id), and the clock (Date.now), see README.md.
import { readFileSync, writeFileSync } from "node:fs";
import { performance } from "node:perf_hooks";
import { join } from "node:path";

const [dataDir, outFile] = process.argv.slice(2);
const WS = process.env.MEOW_WS || "/tmp/ws";
const DIR = ".dsh-meow";

// --- clock: the plugin has no date field on writes, it stamps Date.now(). We replay history by
// setting the clock to each memory's date at write time, and to "the day after the last memory"
// for all questions (chosen before the first run). performance.now() is used for timings.
const realNow = Date.now.bind(Date);
let fakeNow = null;
Date.now = () => (fakeNow === null ? realNow() : fakeNow);

const lib = await import("meow-memory");
const memories = JSON.parse(readFileSync(join(dataDir, "memories.json"), "utf8"));
const queries = JSON.parse(readFileSync(join(dataDir, "queries.json"), "utf8"));

// The plugin's own tool registration function, with a collector as the host's `register`.
const tools = {};
lib.registerMemoryTools((t) => { tools[t.name] = t; }, DIR);
const execFor = (sid) => ({ agent: { session: { header: { cwd: WS, id: sid } } } });

const order = Object.keys(memories).sort((a, b) =>
  memories[a].date < memories[b].date ? -1 : memories[a].date > memories[b].date ? 1 : a < b ? -1 : 1);
const lastDate = memories[order[order.length - 1]].date;
const QUERY_NOW = Date.parse(lastDate + "T12:00:00Z") + 86400000;

// ---- ingest ----
const rowToMem = new Map();      // plugin row id -> benchmark memory id
const mergedNotes = [];
const GLOBAL = lib.globalProjectMarker(); // default scope marker ("全局" under the default zh prompt language)
const ingestStart = performance.now();
let writeFailures = 0;
for (let i = 0; i < order.length; i++) {
  const mid = order[i];
  const m = memories[mid];
  fakeNow = Date.parse(m.date + "T12:00:00Z") + i * 1000;
  // memory_remember REQUIRES keywords (normally written by the LLM agent). No model is available here,
  // so the adapter derives them with the plugin's own tokenizer: all unique tokens of the text.
  const keywords = [...new Set(lib.tokenize(m.text))];
  try {
    const r = await tools.memory_remember.execute(
      { content: m.text, project: GLOBAL, keywords, importance: 1, level: "fact" },
      execFor("ingest-session"));
    if (r.merged) mergedNotes.push(`${mid} merged into row of ${rowToMem.get(r.id)}`);
    rowToMem.set(r.id, mid); // after a merge the row holds the newest text, so it maps to the newest memory
  } catch (e) {
    writeFailures++;
  }
}
const ingestSeconds = (performance.now() - ingestStart) / 1000;
const db = lib.getDb(WS, DIR);
const rows = db.db.prepare("SELECT COUNT(*) AS n FROM fact").get().n;

// Reset usage statistics so every question sees the same state (a hit bumps last_accessed_at, which
// feeds the recency weight; without a reset earlier questions would change later ones).
const resetUsage = () => db.db.exec("UPDATE fact SET hit_count = 0, last_accessed_at = NULL");

// ---- warm-up (not from queries.json) ----
fakeNow = QUERY_NOW;
try {
  lib.buildHitInjection(db, WS, "warmup-session", "今天天气怎么样，晚饭吃什么", {}, DIR);
  await tools.memory_search.execute({ query: "今天天气怎么样，晚饭吃什么", k: 10, explain: true }, execFor("warmup-search"));
} catch (e) { /* warm-up only */ }
resetUsage();

const round4 = (x) => Math.round(x * 1e4) / 1e4;
const answers = {};
let failures = 0, unmapped = 0;
for (const qid of Object.keys(queries)) {
  const q = queries[qid].query;
  const sid = "session-" + qid; // fresh session per question: no "already injected/searched" carry-over
  fakeNow = QUERY_NOW;
  let deployed = [], ranked = [], ms = 0, failed = false;
  try {
    const t0 = performance.now();
    const hit = lib.buildHitInjection(db, WS, sid, q, {}, DIR);
    ms = performance.now() - t0;
    if (hit !== null) {
      // buildHitInjection returns the ids in rank order but no scores: use a descending rank score.
      deployed = hit.injectedIds.map((rid, i) => {
        const mid = rowToMem.get(rid) ?? null;
        if (mid === null) unmapped++;
        return { id: mid, score: hit.injectedIds.length - i };
      });
    }
    resetUsage();
    const res = await tools.memory_search.execute({ query: q, k: 10, explain: true }, execFor(sid));
    // the tool re-sorts its hits by updated_at; recover the retrieval order from bm25 * recencyWeight
    const scored = res.hits.map((h, i) => ({
      mid: rowToMem.get(h.id) ?? null, i,
      s: (h.explain?.bm25 ?? 0) * (h.explain?.recencyWeight ?? 1),
    }));
    scored.sort((a, b) => b.s - a.s || a.i - b.i);
    ranked = scored.map((x) => { if (x.mid === null) unmapped++; return { id: x.mid, score: round4(x.s) }; });
    resetUsage();
  } catch (e) {
    failed = true; failures++; deployed = []; ranked = [];
  }
  answers[qid] = { ranked, deployed, ms: round4(ms) };
}

const out = {
  system: "meow-memory",
  version: "0.32.6",
  mode: "verbatim ingest through memory_remember (level fact, global scope, importance 1), keywords derived by the plugin's tokenizer instead of an LLM, no LLM, no embeddings; default zh prompt language (CJK character bigrams)",
  how: "memory_remember.execute() per memory; ranked = memory_search.execute({query, k:10, explain:true}) (in-memory BM25 k1=1.5 b=0.75 over title+content+keywords with importance/recency weighting, ordered by bm25*recencyWeight); deployed = buildHitInjection(db, ws, sid, query, {}) = keywordHitScore over keywords of fact/lesson/rules/topic rows, hitTopK=2",
  ingest_seconds: round4(ingestSeconds),
  llm_tokens: 0,
  llm_cost_cny: 0,
  answers,
  notes: [
    "Package: meow-memory 0.32.6, the plugin's published lib/index.js imported unmodified; host peers stubbed: @deepseek-ai/dsh-llm (message constructors only, never used on this path); @deepseek-ai/schemastery installed from npm if available in the image build (see README).",
    "memory_remember requires keywords (the LLM agent normally writes them). No LLM was used; keywords = all unique tokens of the memory text from the plugin's own exported tokenize(). With LLM-chosen keywords the deployed (keyword-hit) path would behave differently, probably with fewer but more curated keywords.",
    "Tool has no date argument: Date.now() was set to each memory's date (12:00 UTC + index seconds) during ingest, and to the day after the last memory (" + new Date(QUERY_NOW).toISOString().slice(0, 10) + ") for all questions. Chosen before any run. Recency weighting (importance < 3 only, skipped for results scoring >= 85% of the best) therefore applies as it would to genuinely old memories.",
    "All memories: level fact, project = global marker (so hit injection, which needs global scope or the session's current project, can see them), importance 1 (the tool's schema default), no title.",
    "deployed = per-message keyword-hit injection (buildHitInjection, default hitTopK 2, no score threshold). The first-turn snapshot injects only soul/user levels and global rules with importance >= 2, which are not used here, so it returns no fact memories. deployed scores are rank-derived (2,1) because the function returns ids without scores. ms times buildHitInjection only (it reads rows from SQLite and tokenizes every candidate per call).",
    "ranked = memory_search tool with k=10 and a fresh session per question (blind top-5 plus 5 unseen). The tool returns hits re-sorted by updated_at; the adapter restores retrieval order from explain.bm25 * explain.recencyWeight.",
    "Usage stats (hit_count, last_accessed_at) are reset by direct SQL after each call so all questions see the same state; this only undoes the side effect of the lookup and does not influence ranking logic.",
    `Dedup: memory_remember merges a new memory into an existing fact row when token Jaccard >= 0.8; merges: ${mergedNotes.length}${mergedNotes.length ? " (" + mergedNotes.join("; ") + ")" : ""}. A merged row maps to the newest memory id. Rows stored: ${rows} of ${order.length} memories.`,
    `Write failures: ${writeFailures}. Question failures: ${failures}. Returned items that could not be mapped to a memory id: ${unmapped}.`,
  ],
};
writeFileSync(outFile, JSON.stringify(out, null, 1), "utf8");
console.log(`done: ${Object.keys(answers).length} answers, rows=${rows}, merged=${mergedNotes.length}, failures=${failures}, ingest=${ingestSeconds.toFixed(2)}s`);
