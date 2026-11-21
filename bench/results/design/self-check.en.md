# feiyu-memgate design self-check

> Date: 2026-11-15. Subject: feiyu-memgate 0.1.0 (the tree probed on 2026-11-15).

feiyu-memgate is a memory **gateway**. Its claim is not better search but what surrounds it: who may read what, what is cleaned, what is bounded, what is honest about failure. This self-check describes feiyu-memgate only and grades no other project. Retrieval quality is in the [benchmark summary](../summary.md); functional facts about the benchmarked projects (form, retrieval, whether writing calls a model, default delivery, forgetting, licence) are in [features.en.md](features.en.md).

## How it was obtained

- All ten properties were measured by running the project with [`bench/design_probe.py`](../../design_probe.py): each probe builds a gateway from a configuration in a temporary directory (SQLite stores, and a throw-away server on loopback where needed), with fake secrets and data only. Raw results: [feiyu-memgate.json](feiyu-memgate.json); machine-readable summary: [self-check.json](self-check.json).
- Symbols: ✓ holds, ◐ partly holds, ✗ does not hold. A property is ◐ when one of its checks fails, or when a known limit that is part of the property itself was observed.
- The probes were written and run by this project's own author. They show that these checks hold on this version, not that nothing else is wrong.

## Results

| # | Property | Result | In short |
|---|---|---|---|
| 1 | Redacted before storing | ✓ | regex |
| 2 | Redacted before returning | ✓ | regex |
| 3 | Delivered as data, cannot break out | ✓ | tagged, escaped |
| 4 | Isolation by credentials | ◐ | tenant binding opt-in |
| 5 | Failure told from empty | ✓ | 503 + failed count |
| 6 | Write caps | ✓ | daily + per-minute |
| 7 | Bounded output, whole items | ✓ | count, chars, whole |
| 8 | Forgetting, recoverable | ✓ | archived, restorable |
| 9 | Write needs no LLM | ✓ | no network |
| 10 | Minimal, authenticated network surface | ✓ | 5 routes, token |

Total: ✓ 9, ◐ 1, ✗ 0.

## Measured evidence per property

1. **Redacted before storing** (✓): Probe 1: a fake API key, a password in the 密码是… form and a bearer token were written; 0 of 3 remained in the raw stored row, 4 [REDACTED] marks appeared. Public clients also lose phone, e-mail, ID digits and address (probe 2). Regexes only: a secret with no key word near it is not recognised (observed unmasked).
2. **Redacted before returning** (✓): Probe 1: a fake key and password planted straight into the store, bypassing the gateway, left 0 traces in the recall reply (default redact stage; [REDACTED] marks appear). Public clients also lose personal data on recall (probe 2: phone, e-mail, ID digits and address were masked, the private client keeps them).
3. **Delivered as data, cannot break out** (✓): Probe 3: a memory holding a closing tag, an opening tag and a fake system instruction came out of BlockFormatter (zh and en) with exactly 1 opening and 1 closing tag, 4 left angle brackets escaped, one line per memory, and a head saying data, not instructions. Limits: the instruction prose itself is not rewritten and a model may still obey it; full-width and zero-width look-alikes are not ASCII tags and pass through; the block is built by the client library, so callers using the raw HTTP JSON must wrap it themselves.
4. **Isolation by credentials** (◐): Probe 4: the group client's token cannot read the owner's store (0 hits), /v1/used pointing at the owner store is 403; a client without tenants that sends tenant_id gets 400 bad_tenant; an unknown tenant 403; wrong or missing token 401; a path-like tenant id 400. Between tenants there are two modes. With tenant_binding = "token" (opt-in) each token is minted per tenant from a minting secret with an HMAC: measured, a token minted for tenant 10001 read 10001 without naming it and saw none of 10002's memories; 11 other tenant_id values in the body (another tenant, path-like, empty, leading or trailing whitespace, a number, null, true, a list, an object) were all 403 tenant_mismatch; writing and reporting use for 10002 were 403 and left 10002 unchanged; the minting secret used as a token was 401, a token with its tenant swapped to 10002 was 401, a token minted for an unregistered tenant 403. With tenant_binding = "request" (the default) the tenant id is asserted by the token holder, so one group token can read any registered tenant (measured: after 10002 returned nothing, the same token read tenant 10001's memory); [security.md](../../../docs/security.md) limits this mode to one trusted process and `feiyu-memgate check` prints a reminder for such a client. Partial because the binding is enforced only when tenant_binding = "token" is configured (measured to hold there), while in the default request mode one multi-tenant token still reads every registered tenant.
5. **Failure told from empty** (✓): Probe 5: the only store raising gives 503 unavailable; an empty store gives 200, empty results, failed 0; two stores with one broken give 200 with failed=1 and the other store's result; /healthz?deep=1 marks the broken store down and counts recall_failed_1h.
6. **Write caps** (✓): Probe 6: with daily_writes=5, 8 concurrent writers made 32 writes: exactly 5 succeeded, 27 got 429 daily_limit, 5 rows stored; with rate_limit=5, 8 requests gave 5 x 200 then 3 x 429; rotating 10 tenant ids was still stopped by rate_limit_total=6 (6 x 200 then 4 x 429); with daily_writes_total=4, 11 tenants stored 4 in all. Limit: counts live in memory and reset on restart.
7. **Bounded output, whole items** (✓): Probe 7: limit=3 gave 3, default 5, limit=50 gave 10 (ceiling), limit=0 gave 1; character budget 1500: of six 400-char memories plus one 1700-char memory, 3 items and 1200 chars came back with more=4, every returned text equals its stored text (never cut), the 1700-char memory was left out whole; a 75026-char memory was dropped whole (note oversize-dropped). Input: a 4001-char message gave 400 bad_messages with the row count unchanged, 4000 chars were stored in full, 5 x 3500 chars gave 400, a 501-char query gave 400, a body over 64 KiB gave 413.
8. **Forgetting, recoverable** (✓): Probe 8 (fake clock, half-life 10 days, archive kept 30 days): the first sweep of an old store (memories dated 2025-01-01) forgot 0; day 30 (C reported used via /v1/used) forgot 0; day 50: the unused A was archived then deleted, pinned B and used C stayed; the archive held A's text equal to the original; restore put it back and removed the archive row; a sweep right after the restore forgot nothing; day 100 archived it again; after the retention (day 135) the archive copy was purged. Limits: the archive file is plain text; forgetting needs a [forgetting] section and is off by default.
9. **Write needs no LLM** (✓): Probe 9: with sockets patched to raise, 3 writes on the SQLite path all returned 200 and recall 200, with 0 socket constructions, connects or lookups attempted; the write chain holds only the redact and daily_cap guards, no model piece. Limit: only SQLite was measured; the openviking, embedding and hybrid backends do call out (an embedding service, or a server with an extraction model).
10. **Minimal, authenticated network surface** (✓): Probe 10: the route table is exactly 4 (GET /healthz, POST /v1/recall, /v1/remember, /v1/used); 199 other method x path combinations (including /v1/delete, /v1/list, /v1/export, /admin, /metrics) all answered 404/405; the three /v1 routes answer 401 without a valid token; default bind 127.0.0.1; no delete, list or export route. Known deviation: /healthz?deep=1 needs no token and returns store names and counts.

## Where this project does not hold, and its limits

Reported by the probe itself; nothing was changed to improve the result:

- **Tenants are not isolated by credentials in the default mode** (probe 4, ◐): with tenant_binding = "request" (the default) a multi-tenant token is not bound to a tenant id, so one group token can read any registered tenant; this mode is for one trusted process. The opt-in tenant_binding = "token" mints one token per tenant, and probe 4 measured that it confines a token to the tenant it was minted for (another tenant id in the request: 403 tenant_mismatch; the minting secret used as a token: 401); it only applies when configured, see [docs/security.md](../../../docs/security.md).
- **`/healthz?deep=1` needs no token** and returns store names, failure counters and write statistics (documented design).
- **Redaction is regexes**: an isolated secret without a key word, landline numbers and full-width digits are missed (seen in probes 1 and 2, documented); instruction prose is not rewritten and full-width or zero-width tag look-alikes pass through (probe 3).
- **Only the SQLite path was measured** (probe 9): the openviking, embedding and hybrid backends call out.
- **Limit counters live in memory** and reset on restart; **the forgetting archive file is plain text**, and forgetting is off by default.

## Capabilities found elsewhere that this project does not have

Features seen in the benchmarked projects that feiyu-memgate does not have today. Only the capability is listed; each project's own documentation is the reference for its details.

1. **Automatic reinforcement when a reply actually used a memory** (SeekMemory). When a reply uses a memory it is reinforced automatically and its decay clock restarts; being retrieved or injected alone does not count. feiyu-memgate has the same idea: POST /v1/used records a real use (probe 8 measured recorded=1), but the chat program has to report it; without a report only the optional freshness stage credits the best result at recall time (credit defaults to top), and being returned is not being used.
2. **Graded disclosure: full text, paraphrase, hint or silence** (SeekMemory). How well a memory fits the current conversation decides whether it is given in full, paraphrased, hinted at or left out. feiyu-memgate's access is all or nothing per store and credential, with no grading.
3. **Superseded facts are marked invalid** (Graphiti, SeekMemory). Graphiti has a language model compare new and existing facts and sets an invalidation time on the superseded one; SeekMemory retires a contradicted older memory through its supersedes field. feiyu-memgate stores text as written, drops near-duplicates at recall (dedupe) and puts newer items first (newer_first), but has no notion of a later memory replacing an earlier one.
4. **Model-based extraction and consolidation of memories** (mem0, Graphiti, SeekMemory, dsh-mnemon, memsearch, meow-memory, OpenViking). mem0 extracts facts (names resolved, dates grounded); Graphiti extracts entities and time-stamped facts; OpenViking extracts on session commit; dsh-mnemon runs an idle review; SeekMemory extracts through the host model plus a nightly consolidation; the memsearch plugin summarises each turn; meow-memory has reflection and dream consolidation. The gateway stores the text as written and leaves any extraction to the backend (the OpenViking backend); that buys cheap, predictable, offline writes and gives up merging, rewriting and distilling long conversations.
5. **Write-time dedupe, merge and gate** (lingshu, meow-memory, mem0). lingshu's write gate returns ACCEPT/MERGE/DROP/DEFER for every write and logs the decision, and merges same-skeleton short messages within 24 h; meow-memory merges a new memory into an existing row of the same level when they are very similar (token Jaccard >= 0.8); mem0 deduplicates by hash. feiyu-memgate's default write guards are only redact and daily_cap, so duplicates are stored and only filtered at recall by the dedupe stage.
6. **Per-turn narrowing of read scope, and saying that empty does not mean nothing matches** (dsh-mnemon). A turn reads only the memory spaces named for that turn; when the budget is spent the strategy reports that it did not look and states that an empty list does not mean nothing matches. feiyu-memgate's read permission is fixed on the token and cannot be narrowed per request; it separates failed from empty (failed, busy, 503) but has no per-turn 'budget spent, did not look' statement beyond the busy count.
7. **Per-memory expiry date** (mem0). The optional expiration_date keeps a memory out of search results after that date. feiyu-memgate has no per-memory expiry; forgetting follows only how long a memory has gone unused.
8. **Markdown as the source of truth** (memsearch). Markdown files are the source of truth, the index can be rebuilt, and deleted content can be recovered from the files or git. feiyu-memgate keeps memories in SQLite and edits go through tools; it has encrypted backups (backup seal) but no format a person edits directly.
