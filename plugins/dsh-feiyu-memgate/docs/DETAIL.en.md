## What it is

The DeepSeek Harness (DSH) plugin for the [feiyu-memgate](https://github.com/N0rmal-114/feiyu-memgate) memory gateway. It gives DSH conversations a long-term memory: related memories are looked up at the start of each turn, and the model gets two tools, "remember" and "search". The gateway is bundled with the plugin and started by it on your machine. With the default settings it needs no model and downloads nothing; memories are found by keyword search.

## After install

1. The first time a memory feature is needed, the plugin starts the bundled gateway in the background. DSH does not wait for it.
2. When you send a message, the plugin looks up related memories and adds them after your message, in a block marked as data, not instructions. The block says today's date (the date on this computer) and each dated memory shows how long ago it was, for example `（记忆，2026-10-27，3 周前）` in Chinese or `(memory, 2026-10-27, 3 weeks ago)` in English. At most 5 by default; most turns get fewer, and many get none. The lookup waits at most 4 seconds and is skipped when slower.

   The block may also hold memories that are not close to your message but are close to the first ones found. With vector retrieval (the `endpoint` and `local` tiers, with or without `retrieval.hybrid`) and `advanced.linkedRecall` on (the default since 0.3.2), the gateway follows the first two memories it found to the memories most similar to them and adds at most two that are similar enough, never more than `advanced.recallLimit` in all. Measured through the bundled gateway with the bge-m3 embedding model on a held-out set of 48 casual chat remarks (one case is two points): of the memories connected to what a remark reaches, 26.4% were delivered before and 41.7% after with hybrid retrieval, and 29.2% and 42.7% with plain vector retrieval. Memories per remark went from 2.9 to 3.7 (hybrid) and from 3.7 to 4.3 (vector). Turns with nothing related stayed quiet as often as before (43.8% with hybrid, 31.2% with plain vector retrieval), and follow-up questions were not hurt. One model, a small set of invented cases, not verified in a live chat. Under `keyword` nothing changes. Each followed memory costs one extra pass over the stored vectors.
3. The block stays in the conversation history, so the context grows on every turn that has one. Nothing before your current message changes, so the prompt cache is not affected. A memory that a block still in the history already shows is not added again (`advanced.skipRepeats`); if the host compacts the conversation and the block is gone, the memory is given again.
4. If the lookup of a turn times out or fails, or the bundled gateway is still starting, one short note after your message tells the model that memory could not be searched, so it does not take that for "no related memory". It is added on the first turn of a failure streak only (`advanced.failureNote`).
5. The model has two tools: `memgate_remember` saves one sentence, `memgate_search` looks memories up. A short section in the system prompt (in Chinese or English, following `advanced.language`) tells it to save only when you ask it to remember something or state a lasting fact, never to save secrets, and how to write a memory: a sentence that can be understood on its own (who or what, not "he" or "this"), times as dates, and for a change the new state as a full sentence that says what it replaces.
6. With the default settings a memory is written only when the model calls `memgate_remember`. With `modules.autoCapture` on, your own messages are also stored after each turn, except commands, filler and messages shorter than `advanced.captureMinChars` (4 by default). Every saved message carries the local time of this computer, and the memory is dated by that day.
7. Memories are kept in SQLite files in `<DSH home>/data/feiyu-memgate/`. `<DSH home>` is the folder named by the `DSH_HOME` environment variable, or `.dsh` in your user folder when it is not set.
8. Experimental, off by default: a second block (all three settings need `modules.autoRecall`; with all off nothing above changes). With `advanced.afterthought`, after each reply the plugin looks memories up once more in the background with the reply as the query, keeps at most `advanced.afterthoughtMax` (2) memories that no block in the history shows, and gives them on the next turn in a second block, because the reply is known only when a turn is over. With `advanced.conversationPool`, the plugin remembers in memory which memories the conversation was given (strength 1, times 0.8 each turn, plus 1 when your message mentions it or the reply used it), and when the host has compacted the history and those blocks are gone it gives the few still in play (strength 0.3 or more) (at most `advanced.poolSize`, 3) once more in the second block. The second block may carry memories unrelated to the new message and makes the context longer; the second lookup costs one more lookup per turn in the background. With `advanced.recentPool`, the same strengths are tracked and, at the end of each turn, the memories of strength 0.5 or more (12 at most) are saved to `recent-pool.json` in the data folder; at the first turn of another conversation the `advanced.recentSize` strongest (3) are given once in the second block, when the file is at most `advanced.recentDays` (7) days old. Switching the setting off deletes the file. Nothing else is written to disk.

## Requirements

- DeepSeek Harness: tested with 0.2.2-rc.1 (installed Desktop) and checked against the 0.2.2-rc.3 source; engines.dsh accepts 0.2.2-alpha.1 through any 0.2.2 prerelease (<0.2.3-0). The package range also accepts 0.2.0-rc.1 up to 0.2.1-0 and 0.2.1-alpha.1 up to 0.2.2-0; those two ranges were not tested.
- Node 22.19 or later.
- Python 3.11 or later (with `sqlite3` and `tomllib`, which standard builds include); nothing to `pip install`. The plugin tries, in order: the `gateway.python` setting, the Desktop app's bundled Python, `python3`, `python`, `py -3`. If none is found, the gateway does not start and memory is off; DSH and your conversations are not affected.

## Install

On the Plugins page, install the package `dsh-feiyu-memgate`; there is nothing to configure. From the command line:

```sh
dsh plugin --profile <name> add dsh-feiyu-memgate
```

For the Desktop app the profile is `desktop`: start Desktop once, quit it completely, run `dsh plugin --profile desktop add dsh-feiyu-memgate`, then start it again.

## Settings

Every setting can be changed in three places: this plugin's settings page on the Plugins page, the chat command `/memgate`, or the profile's `cordis.patch.yml`.

| Setting | What it does | Default |
|---|---|---|
| `modules.autoRecall` | Automatic recall every turn | on |
| `modules.rememberTool`, `modules.searchTool` | The remember and search tools for the model | on |
| `modules.promptSection` | The fixed section in the system prompt | on |
| `modules.autoCapture` | Store your messages after each turn | off |
| `retrieval.tier` | How memories are found: `auto`, `keyword`, `endpoint`, `local` | `auto` |
| `retrieval.hybrid` | Also search by keyword in the vector tiers and fuse the results (no effect under `keyword`) | off |
| `advanced.recallLimit` | Most memories added per turn | 5 |
| `advanced.skipRepeats` | Do not add a memory that a block still in the history already shows | on |
| `advanced.linkedRecall` | With vector retrieval, follow the first two memories found to the memories closest to them and add at most two, never beyond `advanced.recallLimit`; nothing under `keyword`. Changing it restarts the managed gateway | on |
| `advanced.showAge` | The block says today's date and how long ago each memory is | on |
| `advanced.failureNote` | Tell the model when the lookup of a turn failed | on |
| `advanced.captureMinChars` | Automatic capture skips a message with fewer meaningful characters (0 to 50; 0 turns the length test off) | 4 |
| `advanced.afterthought` | Experimental. After each reply, look memories up once more with the reply as the query; hold the new ones for the next turn's second block | off |
| `advanced.afterthoughtMax` | With `advanced.afterthought`: most memories a second lookup keeps (1 to 5) | 2 |
| `advanced.conversationPool` | Experimental. Remember what the conversation was given; after a host compaction give the few still in play once more, in a second block | off |
| `advanced.poolSize` | With `advanced.conversationPool`: most memories brought back per turn (1 to 5) | 3 |
| `advanced.recentPool` | Experimental. Give the memories the previous conversation was still using once at the first turn of a new one; keeps `recent-pool.json` in the data folder | off |
| `advanced.recentSize` | With `advanced.recentPool`: most memories carried (1 to 5) | 3 |
| `advanced.recentDays` | With `advanced.recentPool`: days a saved conversation can still be carried (1 to 30) | 7 |
| `advanced.profile` | `broad`: more memories per turn, misses less, more noise, faster context growth; `precise`: about one close match per turn | `broad` |
| `advanced.dailyWrites` | Most memories written per day, 0 for no cap | 50 |
| `advanced.redaction` | Remove keys, tokens and passwords when saving and recalling | on |
| `gateway.mode` | `managed` uses the bundled gateway, `external` a gateway you run | `managed` |

Subcommands of `/memgate`: `status`, `config`, `set <key> <value>`, `reset <key>`, `on <feature>`, `off <feature>`, `tiers`, `use <tier>`, and for local models `models`, `install <model> [--confirm]`, `builds allow|deny`, `use <model>`, `remove <model>`, `cancel`. The full list of settings, their values and examples are in the package's README.md.

## Semantic search (optional)

Keyword search does not find reworded questions. The add-on [dsh-feiyu-memgate-embed](https://github.com/N0rmal-114/feiyu-memgate/tree/main/plugins/dsh-feiyu-memgate-embed) runs an embedding model on this machine, on the CPU only (on Windows it needs the Microsoft Visual C++ 2015-2022 runtime, x64, which most Windows installations have). Once a model is ready, `auto` switches to `local` and the existing memories are copied into the vector store in the background; `/memgate status` shows the progress. A memory longer than the model's input is searched by its beginning only, and the full text is still stored and returned (with the add-on's setting `model.overflow` at `reject`, it is refused instead). Each retrieval tier keeps its memories separately.

The section "Local embedding models" on the settings page lists each model's tier, download size, memory while running (what the add-on measured for the model process, for example about 190 MB for bge-small-zh-v1.5, not the file size), languages and licence. Press "Download and enable":

1. Without the add-on, the page first says what will be installed from npm (node-llama-cpp with a native binary, about 90 MB for the CPU build, up to about 740 MB on Windows and Linux x64), and only after you confirm does the host's plugin manager install it.
2. pnpm stops the install script of node-llama-cpp; nothing is installed at that point. Allow: it installs again and is switched on. Deny: DSH has no button for it; the page names the real path of the profile's `pnpm-workspace.yaml`, where you change `node-llama-cpp: set this to true or false` under `allowBuilds` to `node-llama-cpp: false`, save, and press the button again: the add-on is installed and switched on. The plugin never allows a script for you and does not edit the profile's files.
3. The model is downloaded, verified and enabled. Closing the page or restarting DSH does not lose the progress; an interrupted download can be resumed.

An add-on that is installed but not switched on (for example after a `dsh plugin add` that stopped at the install script) is switched on through the plugin manager by the same button. Every failure is shown with its reason and what to do; when the engine cannot load (the add-on's status shows `runtime-missing`), on Windows install the runtime named above and restart DSH.

## Hybrid search (optional)

With `retrieval.hybrid` on, the vector tiers (`endpoint`, `local`, and `auto` while it resolves to `local`) ask keyword search and vector search for every query and fuse the results. Under `keyword` it does nothing. It uses the same vector store file as the tier without it, plus a keyword index file next to it (`memories-<store id>-keywords.db`), so switching it on or off loses no memory: before the gateway starts, the plugin brings the index in line with the vector file, and the first start after switching it on builds the index from the stored texts (at most 60 seconds per start; the gateway starts either way and the next start continues). The forgetting files and the copy progress file keep their names.

Measured basis, in the gateway's own benchmark with bge-m3 (nothing was measured again for this plugin): Hit@5 on PerLTQA (Chinese, 8,588 questions) is 95.0% for hybrid, 87.3% for keyword and 83.3% for vector; on LoCoMo (English, 1,535 questions) 58.0% for hybrid, 51.6% for vector and 36.4% for keyword; on the project's own set (160 questions) 96.7% for hybrid and 98.3% for vector. The score lines were chosen with bge-m3; with other models the same lines are used and were not measured. Tables: [benchmark on public datasets](https://github.com/N0rmal-114/feiyu-memgate/blob/main/README.en.md#public-datasets-perltqa-and-locomo).

## Data and privacy

- In the default mode everything stays on your machine. The gateway listens only on a random port of `127.0.0.1`, and its token is made anew at each start and kept in memory only. Something is downloaded only when you install the add-on or a model from the settings page or with `/memgate install`: the host's plugin manager fetches the add-on from npm, and the add-on fetches the model.
- Memories are plain SQLite files; the plugin does not encrypt them. With `retrieval.hybrid` on there is one more, the keyword index `memories-<store id>-keywords.db`, which holds the same texts. To delete all data, quit DSH and delete `<DSH home>/data/feiyu-memgate/` (or the folder in `gateway.dataDir`, if you changed it).
- With the `endpoint` tier, the text of every saved memory and every recall query go to the embeddings API you configured.
- Recalled memories are part of the prompt and go to the model service of the conversation.

## Limits

- Keyword search does not find reworded questions: in the gateway's own benchmark it delivered a right memory for none of 55 reworded questions. See the [benchmark](https://github.com/N0rmal-114/feiyu-memgate#基准测试) (in Chinese).
- The memory block stays in the history, so the context grows. `advanced.skipRepeats` keeps the same memory from being added again while its block is there.
- Linked memories were measured once: one embedding model (bge-m3), 48 invented casual remarks held out (one case is two points), the gateway's own test tool, not a live chat. Other models keep the same threshold (0.60) unmeasured. Under `keyword` nothing changes. Each followed memory costs one extra pass over the stored vectors.
- The second lookup and the conversation pool are prototypes, measured offline only: on the project's held-out chat test set (remarks, memories and replies written by a language model, one embedding model), a second lookup with the reply as the query delivered the memory directly related to a casual remark for 72.2% of remarks instead of 61.8%, and the memories linked to it for 60.7% instead of 44.1%, with at most two memories added ([measurement](https://github.com/N0rmal-114/feiyu-memgate/blob/main/docs/design/2026-11-18-recall-with-a-model.md), in Chinese). Not measured: a live chat, the effect on the model's replies, compaction on a real host. The pool was measured separately, offline, on scripted conversations with a simulated compaction every third turn ([measurement](https://github.com/N0rmal-114/feiyu-memgate/blob/main/docs/design/2026-11-18-sessions.md), in Chinese): on the turns that return to an earlier topic the memory needed was in front of the model in 63.9% of cases instead of 44.4% (held-out conversations). A memory stays in play for about five turns after it was given, longer when your messages or the model's replies use it; "used" is judged by shared wording, so a memory worked into a reply where it did not belong is strengthened too. The memories of a second lookup arrive one turn late, and the second block may carry memories unrelated to the new message. Nothing applies while both settings are off.
- The recent pool writes `recent-pool.json` (text, date, source label and strength of up to 12 memories, the conversation id and the time) in the data folder: the one place outside the memory store where the plugin writes memory texts. A memory forgotten or replaced in the store can still be carried until the file is overwritten or `advanced.recentDays` have passed. "The previous conversation" is the last other conversation that ended a turn with this plugin; all conversations of this host count as one person's. Measured offline only, on scripted conversations and replies written by language models, one embedding model ([measurement](https://github.com/N0rmal-114/feiyu-memgate/blob/main/docs/design/2026-11-18-sessions.md), in Chinese): on turns that refer back without naming the memory, the memory meant was in front of the model in 47.2% of turns instead of 80.6% with three carried memories (held-out; development 36.1% and 72.2%). Not verified in a live chat.
- Not verified in a live chat. The 0.3.1 changes (no repeated memories, today's date and ages, the way memories are written, the filter of automatic capture, the note about a failed lookup) were exercised in unit tests and in a simulated host, not in a live conversation with a model. Whether a model follows the new instructions, and whether the date and ages improve its answers, was not measured. "Today" is the date on this computer.
- Only memories the model chose to save exist, unless automatic capture is on.
- The daily write cap is counted in memory, per DSH process, and starts again when the gateway restarts.
- The settings page depends on host UI interfaces that are still changing; without it, every setting can be changed with `/memgate` or in the profile file.
- Real-host testing. Tested in the installed DeepSeek Harness 0.2.2-rc.1 on Windows 11 with an isolated profile: the plugin loads, the bundled gateway starts with a Python from PATH, the settings page renders and saves, the status and model routes answer only to the owner session, the chat command works, and with the add-on and bge-small-zh-v1.5 the local tier is used and reworded questions find their memories. Installing the add-on from npm through the settings page was run too, with the dependency's install script refused: the add-on is installed and enabled, the model is downloaded and the local tier takes over (the path that allows the script was not run). The hybrid switch of 0.3.0 was also run in an isolated profile, with a stand-in embeddings service on the local machine, not a real model: with the `endpoint` tier and `retrieval.hybrid` on, the gateway starts with a hybrid store and the memories of the keyword store are copied into both the vector file and the keyword index; after one memory was removed from the vector file while the host was stopped, the next start removed it from the index too. In that profile, where the plugin is linked from its source folder, the settings form says the settings are not available (0.2.0 behaves the same there), so the new switch was not seen in the form and was set through the profile file. Not tested: macOS and Linux desktops, the Desktop application's own window (the web profile of the same host was used), and a conversation with a real model (automatic recall and the two tools were exercised in tests and against the gateway, not in a live chat).
- This is version 0.3.2; what each version changed is in the [changelog](https://github.com/N0rmal-114/feiyu-memgate/blob/main/plugins/dsh-feiyu-memgate/CHANGELOG.md) (Chinese). Version 0.3.2 changes one default: with vector retrieval the block may now hold up to two memories linked to the first two found; switch it off with `advanced.linkedRecall`. Version 0.3.1 changed defaults, and each change has a switch: repeated memories are skipped (`advanced.skipRepeats`), the memory block shows today's date and the age of each memory (`advanced.showAge`), a failed lookup is announced to the model (`advanced.failureNote`), and automatic capture skips trivial messages (`advanced.captureMinChars`). Saved memories carry the local time, and the bundled gateway is updated to the same version.

## Troubleshooting

Start with `/memgate status`. If no Python is found, install Python 3.11 or later, or set `gateway.python`; if the gateway does not start, read `gateway.log` in the data folder. To switch the whole plugin off, turn off its row on the Plugins page; to switch one feature off, `/memgate off <feature>`.

## Licence

MIT. The bundled gateway is the same author's project [feiyu-memgate](https://github.com/N0rmal-114/feiyu-memgate), under the same licence, version 0.1.0 at commit `7de07c60f2b370e6a79371dbc42068240f20942f`.
