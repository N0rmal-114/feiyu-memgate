# OpenViking and feiyu-memgate-in-front-of-OpenViking

Two benchmark systems against one throw-away OpenViking server.

- **OpenViking** v0.5.3 (`ghcr.io/volcengine/openviking`, image used by digest
  `sha256:57d3fd88...a104d`, never pulled by these scripts): a memory/context server with vector search over
  memory files and, on session commit, LLM extraction of memories.
- **openviking** (row): its own search API called directly, `POST /api/v1/search/find`.
- **feiyu-memgate-openviking** (row): feiyu-memgate's gateway with the `openviking` backend and the default
  recall stages, in front of the same kind of server (its own user).
- `openviking-unfiltered` (extra row, same calls as `openviking`): keeps the directory summaries OpenViking
  returns inside the memory tree (`.abstract.md`, `.overview.md`) as id-null items. Only 8 such items appeared in
  all ranked lists, so the two rows are nearly identical.

## Run

    bash bench/adapters/openviking/build.sh   # checks that the images exist
    bash bench/adapters/openviking/run.sh     # server up, setup, both adapters, backend capability check, server down

`run.sh` generates a random root key per run, renders `ov.conf.template` into a work directory outside the
repository (`../feiyu-memgate-bench/work/openviking/`), starts `fmg-bench-openviking` on the internal network
`fmg-bench` (nothing published; cap-drop ALL, no-new-privileges, 6 GB, 512 pids, data in the volume
`fmg-bench-openviking-data`), runs the adapters in `python:3.12-slim` containers on the same network (repository
read-only), copies the three raw result files into `bench/results/raw/`, and removes container and volume on exit.
Embeddings: bge-m3 (1024) from `fmg-bench-embedder` through OpenViking's `ollama` provider. The language-model
section of the config points at a model with a dummy key; it is never called (no session is committed).

## What is called

Admin routes found from `GET /openapi.json`: `POST /api/v1/admin/accounts` {account_id, admin_user_id};
`POST /api/v1/admin/accounts/{account}/users` {user_id, role} (the reply holds `result.user_key`). Two users:
`owner` (direct adapter) and `gate` (feiyu-memgate adapter), so each system ingests its own 240 files.

Ingest (both): every memory once, oldest first, text verbatim, as a memory file written with
`POST /api/v1/content/write` {uri, content, mode "create"} at
`viking://user/<user>/memories/events/YYYY/MM/DD/<memory id>.md` (the feiyu adapter spells it `viking://~/...`
through `OpenVikingBackend.put`). The server accepted this layout at once; the memory id is the file name. Then
a poll of `search/find` with 21 memories' own texts (including the last written) until each finds itself; the
wait is part of `ingest_seconds` because indexing is asynchronous.

- `openviking`: ranked = `find` with limit 10 and `target_uri viking://~/memories`; deployed = the same call
  with no limit and no threshold (API defaults: limit 10, no threshold). The default limit/threshold of
  OpenViking's DSH plugin is **unverified** (no package offline), so the API default stands in for it.
- `feiyu-memgate-openviking`: `config.build_gateway` with one `openviking` store (read-only, `writable` false)
  and an owner client; ranked = `backend.find(query, 10)`; deployed = `Gateway.recall` with the default stages and
  limit. Items carry `"id"` = the memory URI; the memory id is its file name.

## Results

Run `python bench/score.py` for the table. Timings in `bench/results/raw/*.json` come from a machine shared
with other workers and a shared CPU embedder: latency (a query embedding per call) and ingest time (embedding
240 texts, about 70 s of it waiting for the index) are noisy; the final sequential pass is the reference.
Both systems use no language model (`llm_tokens` 0), which is not how OpenViking is normally fed: extraction
quality, its cost and whatever it drops or merges are not measured here.

## Capability check of the `openviking` backend against the real server

`capabilities.py` writes `capabilities.json` into the work directory. Result after the backend fixes (v0.5.3):

- `refs()`: 240 of 240 files (also with page sizes 50 and 100). It pages `GET /api/v1/fs/tree?level_limit=10`
  with `node_limit`/`offset`; the first version paged `fs/ls?recursive=true`, which on this server lists
  directories only (0 files) and returned an empty store without any error.
- `put` + `read`: round trip equals the text; a second `put` on the same URI replaces it; the `viking://~/...`
  spelling is accepted.
- `delete`: first call `True` (file gone, search no longer finds it, a re-`put` restores it), second call
  `False`. The server answers 200 for a missing path and its `estimated_deleted_count` counts index records
  (0 for a file written a moment ago and not indexed yet), so the backend asks `GET /api/v1/fs/stat` first.
- `receipt_refs`: `None` for an uncommitted or unknown session. After a real commit
  (`receipt_check.sh`, one LLM extraction of about 9,300 tokens, a few thousandths of a CNY) it returns the
  memory files the session added, read from `history/archive_001/memory_diff.json`, and they match the new
  files in `refs()`.

Scores: see the `openviking` backend docstring (floor/ceil calibration, derived on the `examples/eval`
development set only).
