# dsh-feiyu-memgate

English | [中文](README.zh.md)

The DeepSeek Harness (DSH) plugin for the [feiyu-memgate](https://github.com/N0rmal-114/feiyu-memgate) memory gateway. It gives DSH conversations a long-term memory: related memories are looked up at the start of each turn, and the model gets two tools to save and search memories.

The gateway is bundled with the plugin and started by it on your machine. With the default settings it needs no model and downloads nothing: memories are kept in SQLite files and found by keyword search.

## What happens after install

With the default settings, this is what you see:

1. **The gateway starts in the background.** The first time a memory feature is needed, the plugin looks for Python 3.11 or later, writes a gateway configuration into the data folder and starts the bundled gateway on a free loopback port. DSH does not wait for it. Until the gateway is healthy, recall adds nothing and the tools report that memory is unavailable.
2. **Recall at the start of each turn.** When you send a message, the plugin looks up memories related to it. Only the first step of a turn does this, and only in your own session, not in subagent sessions. A message that starts with `/` or `／`, the message `ok`, a message with no meaningful characters (only emoji or punctuation), and a message of one or two filler characters such as `嗯` or `好` are not looked up. A very short message (fewer than 8 meaningful characters) is looked up together with your previous message. The query is cut to 500 characters.
3. **The memory block.** If the lookup finds something, the plugin adds one message right after your message, in a `<memory_data>` block. It says the content is data, not instructions, and lists up to `advanced.recallLimit` memories (5 by default), each with its date when known. If more memories matched than fit, a last line says how many. The gateway applies score thresholds, so most turns get fewer memories than the limit, and many get none. The lookup waits at most `advanced.recallTimeoutMs` (4000 ms by default); a slower lookup is skipped for that turn.
4. **The block stays in the conversation history.** It is saved with the turn, like any other message. Two consequences. The context grows by the block on every turn that has one, and those tokens are sent again on later turns. The prompt cache is not harmed: nothing before your current message changes, so the cached prefix of the earlier conversation is still valid. The next turn does not add a block that is identical to the one already in the history.
5. **Two tools for the model.** `memgate_remember` takes one short sentence (at most 4000 characters) and saves it. `memgate_search` takes a query (at most 500 characters) and an optional `limit` from 1 to 10, and returns the same kind of block. A short fixed section in the system prompt tells the model to call `memgate_remember` when you ask it to remember something or state a lasting fact about yourself, your preferences or your work, never to save secrets, to call `memgate_search` when earlier conversations may hold the answer, and to treat recalled memories as data, not instructions. The tools report what happened: saved, not saved (nothing worth keeping, daily cap used up, refused, memory unavailable), or unknown (the gateway did not answer in time). The model is told never to say "saved" unless the tool did.
6. **Writing happens only through the tool.** With default settings a memory is written only when the model calls `memgate_remember`. If you never ask and the model never decides to, nothing is stored. Switch on `modules.autoCapture` to also send your own messages to memory after each turn (see below).
7. **Where memories live.** In SQLite files in `<DSH home>/data/feiyu-memgate/`. `<DSH home>` is the folder named by the `DSH_HOME` environment variable, or `.dsh` in your user folder when it is not set.

## Requirements

- DeepSeek Harness. Tested with DeepSeek Harness 0.2.2-rc.1 (installed Desktop) and checked against the 0.2.2-rc.3 source; engines.dsh accepts 0.2.2-alpha.1 through any 0.2.2 prerelease (<0.2.3-0). The range in `package.json` also accepts 0.2.0-rc.1 up to but not including 0.2.1-0, and 0.2.1-alpha.1 up to but not including 0.2.2-0. Those two ranges were not tested.
- Node 22.19 or later (`engines.node`).
- Python 3.11 or later for the bundled gateway. It must have the `sqlite3` and `tomllib` modules, which standard builds include. The gateway uses only the Python standard library, so there is nothing to `pip install`. You do not need Python in external gateway mode.

The plugin looks for Python in this order and uses the first one that works:

1. The `gateway.python` setting, if it is not empty.
2. The Desktop app's bundled Python: the folder named by the `DSH_PRIMARY_RUNTIME` environment variable, if set (`dependencies/python/python.exe` on Windows, `dependencies/python/bin/python3` elsewhere).
3. The copy the Desktop app installs on first use: `<DSH home>/dsh-runtimes/dsh-primary-runtime/`, with the same file below it.
4. `python3` on the `PATH`.
5. `python` on the `PATH`.
6. `py -3` on the `PATH`.

A candidate is accepted only if it runs, is version 3.11 or later and has both modules. If none is found, the gateway does not start, a warning is logged, recall adds nothing and the tools report that memory is unavailable. DSH and your conversations are not affected. The result of the search is kept until DSH restarts; changing `gateway.python` searches again.

## Install

From the Plugins page: install the package `dsh-feiyu-memgate`. The package brings its own configuration row with default settings, so there is nothing to edit.

From the command line, with the profile you use:

```sh
dsh plugin --profile <name> add dsh-feiyu-memgate
```

For the Desktop app the profile is `desktop`. Start Desktop once so that it creates its profile, then quit the application completely, run the command below, and start Desktop again:

```sh
dsh plugin --profile desktop add dsh-feiyu-memgate
```

## Settings

Every setting can be changed in three ways. They write the same values.

- **The settings page.** On the Plugins page, open the row of this plugin. The page shows the settings and a status block (gateway, Python, store and memory count, retrieval tier, embedding add-on, copy progress). It depends on host interfaces that are still changing; if it does not work in your DSH version, use one of the other two ways.
- **The `/memgate` chat command.** See [The /memgate command](#the-memgate-command).
- **The profile's configuration file.** The row `feiyu-memgate` in the profile's `cordis.patch.yml`. The other two ways save their changes there. A home-level patch or a `--patch` overlay that sets the same row takes precedence, and a change they would override is refused.

Most settings take effect at once. A change of `gateway.mode`, `gateway.python`, `gateway.dataDir`, the retrieval tier or the endpoint settings in use, `advanced.profile`, `advanced.dailyWrites`, `advanced.redaction`, `advanced.newerFirst` or `advanced.forgetting` restarts the gateway. The count of the daily cap starts again then. Switching a module off removes what it registered in DSH. The gateway runs only while at least one of `modules.autoRecall`, `modules.rememberTool`, `modules.searchTool` and `modules.autoCapture` is on.

| Setting | What it does | Default | Notes |
|---|---|---|---|
| `modules.autoRecall` | Look up related memories at the start of each turn and add them after your message. | on | `/memgate on autoRecall` |
| `modules.rememberTool` | Give the model the `memgate_remember` tool. | on | |
| `modules.searchTool` | Give the model the `memgate_search` tool. | on | |
| `modules.promptSection` | Add the short fixed system prompt section about memory. | on | The text is the same whatever the other settings are, so it does not disturb the prompt cache. |
| `modules.autoCapture` | After each turn, send the messages you typed in that turn to memory, without asking the model. | off | Only your own messages, not recalled blocks, injected context or subagent turns. A message over 4000 characters is skipped, never cut. Each write counts against `advanced.dailyWrites`. |
| `modules.commands` | Register the `/memgate` chat command. | on | Needs a host that has chat commands. |
| `modules.migrateOnTierChange` | When a vector tier becomes active, copy the keyword memories into its store in the background. | on | See [Semantic search](#semantic-search-optional-add-on). |
| `retrieval.tier` | How the bundled gateway finds memories: `auto`, `keyword`, `endpoint` or `local`. | `auto` | `/memgate use <tier>`. Table below. |
| `retrieval.url` | `endpoint` tier: base URL of an OpenAI-compatible embeddings API, for example `http://127.0.0.1:11434/v1`. | empty | Must start with `http://` or `https://`. |
| `retrieval.model` | `endpoint` tier: the embeddings model name. | empty | A different model uses a new, empty store. |
| `retrieval.apiKey` | `endpoint` tier: the API key, if the endpoint needs one. | empty | Secret. Write-only: the settings page never shows it again. |
| `gateway.mode` | `managed` starts the bundled gateway. `external` uses a gateway you run. | `managed` | See [External gateway](#external-gateway). |
| `gateway.python` | `managed`: the Python to use (3.11 or later). Empty searches as described under Requirements. | empty | |
| `gateway.dataDir` | `managed`: folder for the memory stores, the generated gateway configuration and the log. | empty (`<DSH home>/data/feiyu-memgate`) | |
| `external.url` | `external`: the gateway address, for example `http://127.0.0.1:8080`. | empty | Must start with `http://` or `https://`. |
| `external.token` | `external`: the client token that gateway issued for this client. | empty | Secret. Write-only. |
| `advanced.recallLimit` | Most memories added per turn (1 to 10). Also the default number of `memgate_search` results. | 5 | Applies to a gateway you run yourself as well. |
| `advanced.recallTimeoutMs` | How long a turn waits for automatic recall (500 to 20000 ms). A slower lookup is skipped. | 4000 | |
| `advanced.language` | Language of the memory block, the tool replies and `/memgate`: `auto` (follow the system language), `zh` or `en`. | `auto` | Memories themselves are never translated. |
| `advanced.profile` | How strict the gateway is about what counts as related: `broad` or `precise`. | `broad` | `broad` returns more memories per turn, misses less and lets more noise through; because the memory block stays in the conversation, the context also grows faster. `precise` returns about one close match per turn and misses more. Up to 0.1.1 the default was `precise`; set it to `precise` to get that behaviour back. The measured numbers are in the [gateway's deployment tiers](https://github.com/N0rmal-114/feiyu-memgate#部署档位) (in Chinese); on the keyword store the gateway measured only a small gain from `broad`. Managed mode only. |
| `advanced.dailyWrites` | Most memories the gateway accepts per day (0 to 100000). 0 means no cap. | 50 | The day starts at local midnight. The count starts again whenever the gateway restarts. Managed mode only. |
| `advanced.redaction` | Remove secrets such as keys, tokens and passwords from memories when they are saved and when they are recalled. | on | Pattern based. It does not recognize a bare secret that has no keyword next to it. Managed mode only. |
| `advanced.newerFirst` | Among nearly equal matches, put newer memories first. | on | It looks only at dates. It does not detect that two memories contradict each other. Managed mode only. |
| `advanced.forgetting` | Experimental. Memories unused for a long time rank lower and are eventually archived and removed. | off | See below. Managed mode only. |

### Retrieval tiers

Each tier keeps its own store file in the data folder. `/memgate tiers` lists the tiers, whether each is available and what is missing. If the tier in use lacks something, the gateway does not start and memory stays off until it is provided.

| Tier | How it finds memories | Needs | Store file |
|---|---|---|---|
| `auto` | `local` while the `dsh-feiyu-memgate-embed` add-on has a model ready, otherwise `keyword`. | Nothing. | That of the tier it resolves to. |
| `keyword` | Keyword search over SQLite inside the gateway. No model, no network. | Nothing. | `memories.db` |
| `endpoint` | Semantic search through an OpenAI-compatible embeddings API. Memory texts and queries are sent to that API. | `retrieval.url` and `retrieval.model`, and `retrieval.apiKey` if the endpoint wants one. | `memories-endpoint-<model>.db` |
| `local` | Semantic search with a model the add-on runs on this machine. | The add-on installed, switched on, with a model ready. | `memories-local-<model>-<hash>.db` |

When the add-on goes away or its model changes, an `auto` or `local` gateway restarts with the new state.

### External gateway

With `gateway.mode` set to `external`, the plugin does not start anything. It talks to the gateway at `external.url` with the client token in `external.token`. If either is empty, memory is off and a warning is logged. `retrieval.*`, `gateway.python`, `gateway.dataDir`, the copy into a vector store and the gateway rules under `advanced` (`profile`, `dailyWrites`, `redaction`, `newerFirst`, `forgetting`) do nothing in this mode: the gateway follows its own configuration. `advanced.recallLimit`, `advanced.recallTimeoutMs` and `advanced.language` still apply.

Prefer it when several programs share one gateway, or when you want the store keys to stay out of the DSH process. The gateway speaks plain HTTP, so use it on the same machine or a network you trust, or put TLS in front of it. The plugin does not follow redirects, so the token is never sent to another address.

### Advanced: forgetting

`advanced.forgetting` is experimental and off by default. When on, the gateway ranks memories that have gone unused for a long time lower. With the gateway's defaults it archives and removes a memory after about 389 days without use, at most the larger of 5 and 2 percent of the store per sweep, with one sweep a day. Archived texts stay in the data folder, unencrypted, for 90 days and are then deleted. The first sweep runs 24 hours after the gateway starts. "Use" means being returned near the top of a recall; the plugin does not report which memories the model actually used. The feature has been tested only in the gateway's simulations. The plugin has no command to restore an archived memory.

### The /memgate command

A chat command for you, one line long. Keys are written as `group.key`; a bare key such as `recallLimit` works when it names one setting (`url` does not, it exists in `retrieval` and `external`). Keys are not case sensitive. Values for switches accept `on`, `off`, `true`, `false`, `yes`, `no`, `1` and `0`, and the Chinese words `开`, `关`, `是` and `否`. A secret you set is never repeated in the reply and the command line is not written to the session log.

| Subcommand | What it does | Example |
|---|---|---|
| `status` | Gateway state, Python found, store and memory count, retrieval tier, embedding add-on, copy progress, switched-on features. | `/memgate status` |
| `config` | Every setting with its current value and its default. Secrets show only as set or not set. | `/memgate config` |
| `set <key> <value>` | Change one setting. | `/memgate set advanced.recallLimit 3` |
| `reset <key>` | Return one setting to its default (this is also how to clear a text setting). | `/memgate reset retrieval.apiKey` |
| `on <feature>` | Switch a feature on. The features are the `modules.*` settings. | `/memgate on autoCapture` |
| `off <feature>` | Switch a feature off. | `/memgate off searchTool` |
| `tiers` | The retrieval tiers: available or not, which is active, what each needs. | `/memgate tiers` |
| `use <tier>` | Choose the retrieval tier: `auto`, `keyword`, `endpoint` or `local`. | `/memgate use keyword` |
| `models` | The local embedding models: download size, memory, languages, licence and state. | `/memgate models` |
| `install <model> [--accept-licence <licence name>] [--confirm]` | Download a model and enable it. Without the add-on, this installs the add-on first, which needs `--confirm` (the reply without it says what would be installed). | `/memgate install bge-small-zh-v1.5 --confirm` |
| `builds allow` / `builds deny` | Answer the install-script question of the add-on installation. | `/memgate builds deny` |
| `use <model>` | Switch to a downloaded model. | `/memgate use bge-m3` |
| `remove <model>` | Delete a downloaded model that is not in use. | `/memgate remove bge-m3` |
| `cancel` | Stop the running add-on installation or model download, or drop an unanswered install-script question. | `/memgate cancel` |

The model subcommands do the same as the buttons of the settings page; see [Installing from the settings page](#installing-from-the-settings-page).

`/memgate` with no argument, or `/memgate help`, prints the usage.

### The profile file

A row in the profile's `cordis.patch.yml` replaces the whole `config` of the row of the same `id`, so write every group you want to differ from the defaults, and leave the others out. For example, to use a larger recall limit and switch on automatic capture:

```yaml
- id: feiyu-memgate
  config:
    modules:
      autoCapture: true
    advanced:
      recallLimit: 8
```

Do not edit the package's own `cordis.patch.yml`. It only mounts the plugin with default settings.

## Semantic search (optional add-on)

Keyword search finds memories that share words with the question. To find reworded ones too, install the add-on [`dsh-feiyu-memgate-embed`](https://github.com/N0rmal-114/feiyu-memgate/tree/main/plugins/dsh-feiyu-memgate-embed). It runs a local embedding model on the CPU only, serves it on `127.0.0.1`, and offers it to this plugin. Models are downloaded only when you ask for it, and texts do not leave your machine. On Windows its engine needs the Microsoft Visual C++ 2015-2022 runtime (x64), which most Windows installations already have.

What installing it changes:

- With `retrieval.tier` set to `auto`, the plugin switches from `keyword` to `local` once a model is ready in the add-on. The gateway restarts with the new store. When the model is no longer ready, `auto` goes back to `keyword`.
- **Existing memories are copied into the vector store on first activation.** This is the migration module, switched by `modules.migrateOnTierChange`. After the gateway is running on a vector tier (`local` or `endpoint`), the plugin copies the memories of the keyword store (`memories.db`) into the vector store in the background, in bounded steps (at most 500 memories per step, 120 seconds per step) and repeats while each step makes progress. A state file `migration-<store id>.json` in the data folder records what was copied, so a copy that was interrupted resumes after a restart. The keyword store is only read, never changed.
- Watch the progress in `/memgate status` (the line "Copy from the keyword store": copied of total, and the number refused) or in the status block of the settings page. Until the copy has passed a memory, it is not found by semantic search.
- **A memory longer than the model's input is searched by its beginning only.** The gateway accepts up to 4000 characters per memory; the models take 512 tokens (the two bge-zh models, roughly 500 Chinese characters), 2048 (bge-m3) or 4095 (qwen3-embedding). By default the add-on embeds the part that fits, the way the gateway's calibration measured long texts, and the full text is still stored and returned. With the add-on's setting `model.overflow` at `reject`, such a text is refused instead: the copy counts it as refused, it stays only in the keyword store and is not offered to that vector store again. If the model accepts no text at all in a step, the copy stops and tries again when the gateway next starts.
- **Memories written under one tier are stored separately from those of another tier.** Each tier and each model has its own file. Texts saved under a vector tier are not copied back to the keyword store, and a new model starts with a copy of the keyword store only, not of the previous model's store. Switching back to a tier finds that tier's memories again.

The `endpoint` tier works the same way with an embeddings API you choose, for example a local Ollama or a cloud service. Memory texts and queries are then sent to that API (see the next section).

### Installing from the settings page

The settings page has a section "Local embedding models", shown whether or not the add-on is installed. Each row gives a model's tier, download size, memory while running, languages, licence and a one-line note, with a link to the [gateway's deployment tiers](https://github.com/N0rmal-114/feiyu-memgate#部署档位) for measured quality. The memory is what the add-on measured for the model process after loading (bge-small-zh-v1.5: a 48 MB file, about 190 MB while running), not the file size. Nothing is downloaded until you press a button. `/memgate models` and the other model subcommands do the same from chat.

What one press of "Download and enable" does:

1. **Without the add-on, the page first asks you to confirm** what is installed: the package `dsh-feiyu-memgate-embed` from npm, through the host's plugin manager, which adds a dependency with a native binary (`node-llama-cpp`, about 90 MB for the CPU build, up to about 740 MB on Windows and Linux x64, where pnpm also fetches the GPU builds). The model runs on the CPU only; on Windows the engine needs the Microsoft Visual C++ 2015-2022 runtime (x64). The host itself shows no dialog for this, so this confirmation is the only one.
2. **pnpm stops the install script of `node-llama-cpp` until you decide.** Nothing is installed at that point: the host puts the profile's `package.json` back and only writes the line `node-llama-cpp: set this to true or false` under `allowBuilds` in the profile's `pnpm-workspace.yaml`. The page asks whether to allow the script. According to the add-on's author both answers work, because the engine does not need the script.
   - **Allow** records a lasting permission in this DSH profile, installs again and switches the add-on on.
   - **Deny** has no button in DSH. The page names the file, `<DSH home>/profiles/<profile>/pnpm-workspace.yaml` (with the real path of your profile), and the line: change `node-llama-cpp: set this to true or false` to `node-llama-cpp: false`, save, then press "Download and enable" again. The add-on is then installed with the script skipped and switched on.
   - Leaving the question unanswered changes nothing else: nothing of the add-on is installed or switched on, and the host reads that line only during a later installation. "Cancel" removes the question from the page.

   The plugin never allows a script without your answer, and it does not edit the profile's files.
3. **The model downloads** with its progress shown; a model whose licence needs acceptance has a checkbox for it. When it is ready, `auto` switches to `local` and the keyword memories are copied as described above. The status block shows the tier and the copy.

If the add-on is already a dependency of the profile but not switched on, for example after a `dsh plugin add` that stopped at the install script, the button switches it on through the plugin manager instead of installing it again; if its files are gone, it is removed and installed anew.

The setup survives closing or reloading the page. After a DSH restart in the middle of a download, the add-on keeps the partial file and the row offers "Resume download". A failure is shown in plain words with what to do: no network or mirror unreachable, not enough disk space, a download that failed verification, an engine that cannot load (on Windows: install the Microsoft Visual C++ 2015-2022 Redistributable (x64), `vc_redist.x64.exe` from Microsoft, then restart DSH; the add-on's status shows the error `runtime-missing`), no Python for the gateway, a declined confirmation, an add-on the host cannot switch on, or a model pinned in the add-on's own settings. Only you can start an installation: the model's tools cannot, and the settings route accepts only the signed-in owner with an explicit confirmation.

## Data and privacy

- **Everything stays on your machine in the default mode.** The bundled gateway, the stores and the log are local. The plugin makes no network request other than to the loopback address of its own gateway. Nothing is downloaded unless you install the add-on or a model from the settings page or with `/memgate install`; the host's plugin manager then fetches the add-on from npm and the add-on fetches the model.
- **The files.** All of them are in the data folder, `<DSH home>/data/feiyu-memgate/` unless `gateway.dataDir` is set:

| File | What it is |
|---|---|
| `memories.db` | The keyword store. |
| `memories-endpoint-<model>.db`, `memories-local-<model>-<hash>.db` | The stores of the vector tiers, one per model. |
| `migration-<store id>.json` | Copy progress for a vector store: counts and references, no texts. |
| `model-setup.json` | Progress of a model setup started from the settings page or `/memgate install`: the model, its step, the install scripts waiting for your decision and the last failure code. |
| `forgetting-ledger-<store id>.db`, `forgetting-archive-<store id>.db` | Only with forgetting on. The archive holds the full text of removed memories, unencrypted. |
| `gateway.log`, `gateway.log.1` | The gateway log, rotated at about 1 MiB. |
| `gateway-<pid>-<n>.toml` | The gateway configuration of the running process, removed when it stops. It holds no token and no key. |
| `pycache/` | Python byte-code caches of the gateway. |

- **No encryption by the plugin.** The stores are plain SQLite files. Protect the folder as you would any file that holds personal notes.
- **The gateway is local only.** It listens on `127.0.0.1` on a random free port, chosen at each start. At each start the plugin makes a new random token (32 bytes) that exists only in memory and in the gateway's environment; it is never written to a file or a log. The gateway process gets a copy of DSH's environment without names that contain `KEY`, `PASSWORD`, `SECRET` or `TOKEN` and without names that start with `DSH_`, plus the token and, for `endpoint`, the key it needs. It exits when DSH exits.
- **Recalled memories go to your model provider.** A memory block is part of the prompt, so it is sent to whichever model service the conversation uses, like any other message.
- **What leaves the machine in `endpoint` mode.** The text of every memory that is saved or copied, and every recall query, go to the embeddings API you configured in `retrieval.url`. The API key is handed to the gateway through an environment variable, not a file. In `external` mode, requests go to the address in `external.url`.
- **Logs name kinds of failure only.** No query, memory text or token is logged by the plugin.
- **Delete all data.** Quit DSH (or switch the plugin off), then delete the data folder. If you changed `gateway.dataDir`, delete that folder. The folder also holds the add-on's models and state file when you use it with its default folder, so delete only the files you mean to.

## Limits and what is not verified

- **Keyword search does not find reworded questions.** In the gateway's own benchmark (240 memories, 160 questions, synthetic Chinese text) the keyword store delivered a right memory for 0 of 55 reworded questions at the default settings, and for 95.6% of 45 questions that use the memory's own words. Semantic search is much better on reworded questions. See the [benchmark](https://github.com/N0rmal-114/feiyu-memgate#基准测试) (in Chinese). Languages other than Chinese were not measured.
- **The memory block stays in the history.** The context grows with every turn that gets a block (see step 4 above).
- **Only memories that the model chose to save exist**, unless you switch on `modules.autoCapture`. The gateway stores the text as it is passed, with secrets removed when redaction is on. It does not extract facts, merge memories or mark old ones as outdated. A newer memory is only ranked before an older one.
- **The daily write cap is counted in memory, per DSH process.** It starts again whenever the gateway restarts, and a second DSH process has its own gateway and its own count.
- **One store per tier.** The plugin uses a single gateway client and a single store per tier. It does not keep separate memories per user or per project.
- **The settings page depends on host UI interfaces that are still changing.** The plugin works without it: every setting can be changed with `/memgate` or in the profile file.
- **Forgetting is experimental** and has been tested only in simulations.
- **Real-host testing.** Tested in the installed DeepSeek Harness 0.2.2-rc.1 on Windows 11 with an isolated profile: the plugin loads, the bundled gateway starts with a Python from PATH, the settings page renders and saves, the status and model routes answer only to the owner session, the chat command works, and with the add-on and bge-small-zh-v1.5 the local tier is used and reworded questions find their memories. Installing the add-on from npm through the settings page was run too, with the dependency's install script refused: the add-on is installed and enabled, the model is downloaded and the local tier takes over (the path that allows the script was not run). Not tested: macOS and Linux desktops, the Desktop application's own window (the web profile of the same host was used), and a conversation with a real model (automatic recall and the two tools were exercised in tests and against the gateway, not in a live chat).
- **Other versions and systems.** Other DSH versions in the accepted ranges, other operating systems and other Python builds are not covered by a real-host test.
- This is version 0.2.0. It changes one default: `advanced.profile` is now `broad` (it was `precise`).

## Troubleshooting

Start with `/memgate status`. It shows the state of the gateway, the Python that was found, the store and its memory count, the retrieval tier, the embedding add-on and the copy progress.

- **No Python found.** `/memgate status` shows Python as not found. Install Python 3.11 or later, or set `gateway.python` to its full path. Changing the setting searches again; if you only installed Python, restart DSH, because the search result is kept until then. Or use an external gateway.
- **The gateway does not start or keeps stopping.** Read `gateway.log` in the data folder. The plugin writes lines such as "using Python", "gateway starting on port", "gateway healthy", "gateway did not become healthy in time" and "gateway exited", and the gateway's own output follows them. The plugin waits 20 seconds for a start. After a crash it restarts the gateway up to 5 times, with longer pauses (1 second, doubling, at most 30). Then the state is "stopped after repeated failures" until a change that restarts the gateway, or a restart of DSH.
- **Port or permission problems.** There is no fixed port to clash with: a free port on `127.0.0.1` is chosen at each start. A security tool that blocks starting Python or listening on the loopback address makes the log say "did not become healthy in time". If the plugin cannot create or write the data folder, DSH logs "cannot create the data folder" or "cannot write the gateway configuration" and memory is off; set `gateway.dataDir` to a folder you can write to.
- **The retrieval tier is not ready.** `/memgate tiers` names what is missing, for example `retrieval.url` for `endpoint`, or a ready model in the add-on for `local`. Memory stays off until it is provided. `/memgate use keyword` needs nothing.
- **The local model does not load.** The model list shows the reason and what to do. If the add-on's status (`/memgate-embed status`) shows `runtime-missing` on Windows, install the Microsoft Visual C++ 2015-2022 Redistributable (x64) from Microsoft and restart DSH. `auto` keeps using `keyword` meanwhile.
- **No memory appears in a turn.** Possible reasons: the store is empty; the message was too short or a command; the lookup took longer than `advanced.recallTimeoutMs`; the same block is already in the history; or keyword search could not match a reworded question.
- **The tool says "not saved: today's limit".** Raise `advanced.dailyWrites`, or set it to 0 for no cap.
- **Switch the plugin off.** On the Plugins page, turn off the row of the plugin. Or set `disabled: true` on the row `feiyu-memgate` in the profile's `cordis.patch.yml`.
- **Switch one module off.** `/memgate off <feature>`, or the switch on the settings page. For example `/memgate off autoRecall` keeps the tools and stops the automatic lookup.

## Licence

MIT, see [LICENSE](LICENSE). The bundled gateway in `python/` is the same author's project, [feiyu-memgate](https://github.com/N0rmal-114/feiyu-memgate), under the same licence ([python/LICENSE](python/LICENSE)). It is version 0.1.0 at commit `403931fee02961435abe9b09245781afcdcb19de` as recorded in [python/GATEWAY_VERSION.json](python/GATEWAY_VERSION.json).
