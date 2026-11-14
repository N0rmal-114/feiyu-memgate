# lingshu-dsh-memory adapter

System: "Lingshu" `@furongjun1999/dsh-memory` 0.10.1 (npm; GitHub FuRongJun-1999/dsh-memory), a memory plugin for the DSH desktop agent app.
It is a Node plugin that starts a Python 3 child process (`md_cg`, standard library only) and talks to it with JSON-RPC over stdio.
Memories are plain files; retrieval is lexical (BM25 inside `search_rrf`), no embeddings, no language model.

Run: `bash bench/adapters/lingshu-dsh-memory/run.sh` (about 35 minutes, see "Pacing"). It copies the package (set `LINGSHU_PKG` to the unpacked npm package folder) into
`$FMG_BENCH_HOME/work/lingshu/ctx`, builds image `fmg-bench-lingshu` (`node:24-slim` + `python3`, dependencies from the
package's own `package.json` with `npm install --omit=dev --ignore-scripts`), then runs the container with `--network none`,
`--cap-drop ALL`, `no-new-privileges`, 4 GB / 4 CPUs, data mounted read-only, only `bench/results/raw` writable.
Output: `bench/results/raw/lingshu-dsh-memory.json`.

## What is called (all the plugin's own code; only the host is faked)

- Faked host glue: a plugin context (`logger`, `on(event, handler)`), a session object (`{id: "bench-session"}`), the data directory (tmpfs `/work`).
- Brain: `resolveToken` (the plugin's own first-start token issue) and `new MdcgClient({...})` with the defaults of `lib/index.js`
  (python3, `-m md_cg.mcp_server`, actor `dsh-memory`, tenant `default`, clearance `private`).
- Ingest: `installMemoryHooks` (`lib/hooks.js`), the `session/event` handler fed one `user/message` event per memory, in date order. The hook
  redacts (`desensitize`) and calls `MdcgClient.remember` = MCP `mdcg_remember(gated=true, layer=contextual, dedupe=true)`; it also writes the
  short session window and issues its usual `read(k=3)`. Hook options are the schema defaults.
- `ranked`: `MdcgClient.read(query, {k: 10})` = MCP `cg(op=read, query, k=10)`. The default tool surface (`tools: "core"`) exposes only `cg` and `stg`,
  and `cg(op=read)` is the search primitive among them. No threshold is applied by the plugin.
- `deployed`: the `system-prompt/assemble` handler of `installMemoryHooks` with default settings. It pushes two blocks: `lingshu:auto-recall`
  (the 4 newest nodes of the session, 1400 characters) and `lingshu:session-window` (last 10 messages, 900 characters). Neither uses the question.
  Items of both blocks are parsed from the injected text and mapped to memory ids by exact text (decayed items by unambiguous prefix).
  A fresh hook instance per question so its "same item at most twice per session" cap starts clean; `ms` times that handler call.

## Why this is the fair entry point

The hooks are what the host runs on every message and every model request, so ingest and `deployed` are the system's shipped behaviour. The
plugin's only query-based retrieval is a tool the model chooses to call; `cg(op=read)` is that tool's search, so `ranked` is its own top-10.

## Pacing (a setting kept at default)

The write path allows 8 automatic writes per 60 s per layer/role and DEFERs the rest without storing them (`md_cg/writelimit.py`).
An unpaced run stored 8 of 240 memories. Instead of disabling the cap (`MDCG_WRITELIMIT=0`), the adapter sends one memory every 7.6 s
(`LINGSHU_PACE_MS`). `ingest_seconds` counts time inside the write calls only; the total wall clock is stated in the result's `notes`.

## Caveats

- Memory dates are not passed (the hook path has no date field); the plugin uses write time, and the writes are in date order.
- All memories sit in one session, which is the best case for its session-scoped injection.
- `source` and `kind` fields are not used. No LLM is involved (0 tokens).
- `ranked` holds fewer than 10 items when `cg(op=read)` returns fewer.
