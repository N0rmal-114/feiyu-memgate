// Benchmark adapter for lingshu dsh-memory (npm @furongjun1999/dsh-memory).
// Runs INSIDE the container (see Dockerfile / run.sh). The plugin's own code does all storage and
// retrieval: MdcgClient (lib/lib/mdcg_client.js) talks MCP over stdio to the Python md_cg server,
// installMemoryHooks (lib/hooks.js) is the real per-event write hook and per-turn injection hook.
// Only the host glue is faked: a plugin context (logger + event registry), a session object, a data dir.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { performance } from 'node:perf_hooks';

const PKG = process.env.LINGSHU_PKG || '/app/pkg';
const DATA = process.env.BENCH_DATA || '/bench/data';
const OUT = process.env.BENCH_OUT || '/out/lingshu-dsh-memory.json';
const SESSION_ID = 'bench-session';
const WARMUP_QUERY = '这是一个用来热身的问题';
const TOP = 10;

const { MdcgClient } = await import(`${PKG}/lib/lib/mdcg_client.js`);
const { resolveToken } = await import(`${PKG}/lib/lib/token_store.js`);
const { mdcgRoot } = await import(`${PKG}/lib/lib/datapath.js`);
const { installMemoryHooks, desensitize } = await import(`${PKG}/lib/hooks.js`);
const pkgJson = JSON.parse(readFileSync(`${PKG}/package.json`, 'utf8'));

const memories = JSON.parse(readFileSync(`${DATA}/memories.json`, 'utf8'));
const queries = JSON.parse(readFileSync(`${DATA}/queries.json`, 'utf8'));
const notes = [];

// ---- host glue: plugin context -------------------------------------------------------------
const logs = [];
function makeCtx() {
  const handlers = {};
  return {
    handlers,
    logger: {
      info: (m) => logs.push(['info', String(m)]), warn: (m) => logs.push(['warn', String(m)]),
      error: (m) => logs.push(['error', String(m)]), debug: () => {},
    },
    on: (name, fn) => { (handlers[name] ||= []).push(fn); },
  };
}

// ---- brain process: same construction as lib/index.js apply() with default config -------------
const python = process.env.LINGSHU_PYTHON || 'python3';
const resolution = resolveToken({ python, configured: undefined, actor: 'dsh-memory', clearance: 'private',
  autoIssue: true, tenant: 'default', env: {} });
if (!resolution.token) { console.error('no token: ' + (resolution.note || resolution.source)); process.exit(2); }
const mdcg = new MdcgClient({
  python, args: ['-m', 'md_cg.mcp_server'], root: mdcgRoot(''), actor: 'dsh-memory', tenant: 'default',
  clearance: 'private', identity: '灵枢', env: { MDCG_TOKEN: resolution.token }, timeoutMs: 60000, maxRetryDelayMs: 30000,
});
mdcg.start();
const ready = await mdcg.waitReady();
if (!ready || !mdcg.isReady()) { console.error('brain not ready'); process.exit(3); }

// Track the promises of calls the hooks fire and forget, so ingest can wait for each write.
const receipts = [];
let inflight = [];
const graph = new Proxy(mdcg, {
  get(target, prop) {
    const v = target[prop];
    if (typeof v !== 'function') return v;
    return (...args) => {
      const r = v.apply(target, args);
      if (r && typeof r.then === 'function' && (prop === 'remember' || prop === 'recentAdd' || prop === 'read')) {
        inflight.push(r.then((x) => { if (prop === 'remember') receipts.push({ ok: true, x }); },
                             (e) => { if (prop === 'remember') receipts.push({ ok: false, e: String(e) }); }));
      }
      return r;
    };
  },
});

// Defaults of the plugin's `memory` config schema (lib/index.js, z.object({...}).default({...})).
const memoryOpts = {
  userMessage: true, excludeSessions: [], sessionPrivate: false, dedupe: true, assistantMessage: false,
  toolResult: false, importance: 0.6, autoRecall: true, autoRecallLimit: 4, autoRecallIdentity: false,
  turnReminder: 10, desensitize: true, contextWindow: { enabled: true, turns: 10 },
  auditPath: '/tmp/lingshu-hook-audit.jsonl',
};
const session = { id: SESSION_ID, header: {} };

const ingestCtx = makeCtx();
const disposeIngestHooks = installMemoryHooks(ingestCtx, graph, memoryOpts);
const sessionEvent = ingestCtx.handlers['session/event'][0];

// ---- ingest: each memory is one user message through the real hook, in date order --------------
const order = Object.entries(memories).sort((a, b) => (a[1].date < b[1].date ? -1 : a[1].date > b[1].date ? 1 : a[0] < b[0] ? -1 : 1));
// The plugin's write path caps automatic writes at 8 per 60 s per (layer, role) (md_cg/writelimit.py RATE_MAX,
// RATE_WINDOW); a faster writer gets DEFER and nothing is stored. The setting stays at its default, so the
// adapter paces its writes (one per PACE_MS, default 7600 ms = 7.9 per minute) like a fast typist would.
const PACE_MS = Number(process.env.LINGSHU_PACE_MS ?? 7600);
const wallStart = performance.now();
let activeMs = 0;
const writeLog = []; // memory id -> receipt, in order
for (const [mid, m] of order) {
  const t = performance.now();
  const before = receipts.length;
  sessionEvent(session, { type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: m.text }] } });
  await Promise.allSettled(inflight); inflight = [];
  const rs = receipts.slice(before);
  writeLog.push({ mid, receipt: rs[0] ?? null });
  activeMs += performance.now() - t;
  if (PACE_MS > 0) await new Promise((r) => setTimeout(r, PACE_MS));
}
const wallSeconds = (performance.now() - wallStart) / 1000;
const ingestSeconds = activeMs / 1000;

// ---- what did the write path do to each memory ---------------------------------------------
const verdicts = {}; let noReceipt = 0; const idByMemory = {};
for (const { mid, receipt } of writeLog) {
  if (!receipt) { noReceipt++; continue; }
  const x = receipt.x;
  const v = receipt.ok && x && typeof x === 'object' ? String(x.verdict ?? (x.ok === false ? 'ok:false' : 'ok')) : 'error';
  verdicts[v] = (verdicts[v] || 0) + 1;
  const nid = receipt.ok && x && typeof x === 'object' ? (x.id ?? x.node_id ?? x.node?.id ?? x.receipt?.id ?? null) : null;
  if (nid) idByMemory[nid] = mid;
}
const redacted = Object.entries(memories).filter(([, m]) => desensitize(m.text) !== m.text).map(([id]) => id);
const rawReceiptSample = JSON.stringify(writeLog[0]?.receipt ?? null).slice(0, 600);

// ---- mapping from returned items back to memory ids -----------------------------------------
const norm = (s) => String(s ?? '').replace(/\s+/g, ' ').trim();
const byText = new Map();
for (const [id, m] of Object.entries(memories)) {
  byText.set(norm(m.text), id);
  const red = desensitize(m.text);
  if (red) byText.set(norm(red), id);
}
function mapText(t) {
  const n = norm(t).replace(/^\[[^\]]*\]\s*/, '');
  if (byText.has(n)) return byText.get(n);
  if (n.endsWith('…')) { // decayed rendering truncates; accept only an unambiguous prefix
    const p = n.slice(0, -1); const hits = [...byText.entries()].filter(([k]) => k.startsWith(p));
    if (hits.length === 1) return hits[0][1];
  }
  return null;
}
const item = (id, score) => ({ id, score: Math.round(Number(score) * 1e4) / 1e4 });
function dedupeBest(list) { // a memory may appear once per list: keep the first (best) occurrence
  const seen = new Set(); const out = [];
  for (const it of list) { if (it.id !== null) { if (seen.has(it.id)) continue; seen.add(it.id); } out.push(it); }
  return out;
}

// ---- ranked: cg(op=read, query, k=10), the search primitive the default tool surface exposes ---
let unmappedRanked = 0; let failures = 0;
async function ranked(q) {
  const res = await mdcg.read(q, { k: TOP });
  const rows = Array.isArray(res?.results) ? res.results : [];
  const out = rows.map((r) => {
    const n = r.node || {};
    const mid = idByMemory[n.id] ?? mapText(n.content);
    if (mid == null) unmappedRanked++;
    return item(mid ?? null, Number.isFinite(Number(r.score)) ? r.score : 0);
  });
  return dedupeBest(out);
}

// ---- deployed: the real system-prompt/assemble handler of a fresh hook instance -------------
// A fresh instance per question gives the per-session "same item at most twice" injection cap a clean
// slate (otherwise results would depend on how many questions came before). Same graph, same data.
let unmappedDeployed = 0; let injectedChars = []; let blockNames = {};
async function deployed(parseOnly = false) {
  const ctx = makeCtx();
  const dispose = installMemoryHooks(ctx, graph, memoryOpts);
  const assemble = ctx.handlers['system-prompt/assemble'][0];
  const assembly = { contexts: [] };
  const t = performance.now();
  await assemble(assembly, { agent: { session } }, async () => {});
  const ms = performance.now() - t;
  dispose();
  const items = [];
  for (const c of assembly.contexts) {
    blockNames[c.name] = (blockNames[c.name] || 0) + 1;
    if (c.name !== 'lingshu:auto-recall' && c.name !== 'lingshu:session-window') continue;
    injectedChars.push(c.text.length);
    for (const line of c.text.split('\n')) {
      const m = line.match(/^- \[[^\]]*\]\s*(.+)$/) || line.match(/^\[(?:user|assistant)[^\]]*\]\s*(.+)$/);
      if (!m) continue;
      const mid = mapText(m[1]);
      if (mid == null) unmappedDeployed++;
      items.push(item(mid, 1 - items.length * 0.01));
    }
  }
  return { items: dedupeBest(items), ms, assembly };
}

// warm-up (not from queries.json)
try { await ranked(WARMUP_QUERY); } catch (e) { notes.push('warm-up read failed: ' + e); }
const warm = await deployed();
const firstBlocks = warm.assembly.contexts.map((c) => ({ name: c.name, text: c.text }));

const answers = {};
for (const [qid, q] of Object.entries(queries)) {
  let r = []; let d = { items: [], ms: 0 };
  try { r = await ranked(q.query); } catch (e) { failures++; }
  try { d = await deployed(); } catch (e) { failures++; r = r; }
  answers[qid] = { ranked: r.slice(0, TOP), deployed: d.items, ms: Math.round(d.ms * 1000) / 1000 };
}

// ---- notes ---------------------------------------------------------------------------------
const deployedCounts = Object.values(answers).map((a) => a.deployed.length);
const distinctDeployed = new Set(Object.values(answers).map((a) => a.deployed.map((x) => x.id).join(',')));
notes.push(
  `Ingest pacing: the plugin's default write cap is 8 writes per 60 s per layer/role (excess is DEFERred, not stored; env MDCG_WRITELIMIT=0 would disable it). The cap was left at its default and writes were paced ${PACE_MS} ms apart; ingest_seconds is the time spent in the write calls only (${ingestSeconds.toFixed(1)} s); total wall clock including the pacing sleeps was ${wallSeconds.toFixed(0)} s.`,
  'Ingest: every memory was sent as one user/message event through the plugin\'s own session/event hook '
  + '(installMemoryHooks), which redacts with its regex list, then calls MdcgClient.remember -> MCP mdcg_remember(gated=true, layer=contextual, dedupe=true). '
  + 'It also fires a read (k=3) and a short-window write per message, as in production. Memory date is not passed: the hook path has no date field (write time is used).',
  'Write gate verdicts over the 240 memories (the hook lets the forgetting gate decide ACCEPT/MERGE/DROP/DEFER): ' + JSON.stringify(verdicts)
    + (noReceipt ? `; ${noReceipt} without receipt` : '') + '. First receipt shape: ' + rawReceiptSample,
  `Memories changed by the write-path redaction (desensitize, default on): ${redacted.length} ${JSON.stringify(redacted)}.`,
  'ranked = MdcgClient.read(query,{k:10}) = MCP cg(op=read, query, k=10) (BM25 path of search_rrf, no threshold). cg/stg is the only tool surface exposed by default (tools: "core").',
  'deployed = what the per-turn system-prompt/assemble hook injects with default settings: the "lingshu:auto-recall" block (timeline of the 4 newest non-identity nodes of the session, '
    + '1400-char cap, decayed per-item 160/144/130/117 chars) PLUS the "lingshu:session-window" block (last 10 messages of the session, 900-char cap). '
    + 'Neither block uses the question: the hook never passes the query to retrieval. Items from both blocks are mapped to memory ids by text and merged (timeline first).',
  `deployed item counts per question: min ${Math.min(...deployedCounts)} max ${Math.max(...deployedCounts)}; distinct deployed lists across ${Object.keys(answers).length} questions: ${distinctDeployed.size}. Block names seen in total: ${JSON.stringify(blockNames)}.`,
  'All 240 memories were placed in ONE session (the benchmark has no sessions); the injection is session-scoped, so this is the best case for it. A fresh hook instance was used per question so the per-session "max 2 injections per item" cap (issue #739) starts clean.',
  'Both injected blocks are wrapped in the plugin\'s own header and footer lines; those wrapper lines are skipped when mapping.',
  `Unmapped items (id null): ranked ${unmappedRanked}, deployed ${unmappedDeployed}. Failed calls: ${failures}.`,
  'Timings are for the assemble handler call only (it includes two round trips to the Python brain: timeline and session recall).',
);
const warnLogs = logs.filter(([l]) => l !== 'info').slice(0, 5).map(([l, m]) => `${l}: ${m.slice(0, 160)}`);
if (warnLogs.length) notes.push('First plugin warnings/errors logged: ' + JSON.stringify(warnLogs));

const result = {
  system: 'lingshu-dsh-memory', version: pkgJson.version,
  mode: 'verbatim user messages through the plugin hook (regex redaction on, forgetting gate on), md_cg lexical/BM25 retrieval, no embeddings, no LLM; deployed = default automatic injection',
  how: 'resolveToken + new MdcgClient(...) (MCP stdio to python -m md_cg.mcp_server); ingest: installMemoryHooks session/event handler (user/message) -> desensitize -> MdcgClient.remember -> mdcg_remember(gated=true); '
    + 'ranked: MdcgClient.read(query,{k:10}) = cg(op=read); deployed: installMemoryHooks system-prompt/assemble handler (lingshu:auto-recall timeline block + lingshu:session-window block)',
  ingest_seconds: Math.round(ingestSeconds * 1000) / 1000, llm_tokens: 0, llm_cost_cny: 0.0,
  answers, notes,
};
mkdirSync(OUT.replace(/[/\\][^/\\]*$/, ''), { recursive: true });
writeFileSync(OUT, JSON.stringify(result, null, 1) + '\n', 'utf8');
if (process.env.BENCH_DEBUG) writeFileSync(OUT.replace(/\.json$/, '.debug.json'), JSON.stringify({ firstBlocks, verdicts, redacted, logs: logs.slice(0, 40) }, null, 1), 'utf8');
disposeIngestHooks(); mdcg.dispose();
console.log('wrote ' + OUT);
process.exit(0);
