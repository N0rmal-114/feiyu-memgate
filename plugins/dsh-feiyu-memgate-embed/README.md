# dsh-feiyu-memgate-embed

[中文](README.zh.md)

An add-on for the DeepSeek Harness (DSH) plugin `dsh-feiyu-memgate`. It runs a local embedding model on the CPU with
[node-llama-cpp](https://github.com/withcatai/node-llama-cpp) and offers it to the memory plugin as an
OpenAI-compatible embeddings endpoint on `127.0.0.1`. The memory plugin's `local` retrieval tier
(and its `auto` tier, while a model is ready) then stores and searches memories as vectors. No text
leaves the computer and no online service is needed.

The add-on does nothing on its own: no model ships with it, without a model chosen it starts no
process and opens no port, and it downloads a model only when you ask for one. The normal way to get
one is the button on the memory plugin's settings page: download, check and activate in one step.
The `/memgate-embed` chat command is the alternative.

## Models

Four models, one for each of four tiers in the memory gateway's calibration table. Every file is
pinned to a commit of its Hugging Face repository and checked against its size and SHA-256.

| Tier | id | File | Size | Memory after load / peak | Embedded whole | Licence |
|---|---|---|---|---|---|---|
| lightest | `bge-small-zh-v1.5` | [CompendiumLabs/bge-small-zh-v1.5-gguf](https://huggingface.co/CompendiumLabs/bge-small-zh-v1.5-gguf) `bge-small-zh-v1.5-f16.gguf` | 47.9 MB | 188 MB / 219 MB | 512 tokens | MIT |
| light | `bge-base-zh-v1.5` | [CompendiumLabs/bge-base-zh-v1.5-gguf](https://huggingface.co/CompendiumLabs/bge-base-zh-v1.5-gguf) `bge-base-zh-v1.5-q8_0.gguf` | 110 MB | 251 MB / 284 MB | 512 tokens | MIT |
| upper-middle | `qwen3-embedding-0.6b` | [Qwen/Qwen3-Embedding-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF) `Qwen3-Embedding-0.6B-Q8_0.gguf` | 639 MB | 1.35 GB / 1.77 GB | 4095 tokens | Apache-2.0 |
| top | `bge-m3` | [gpustack/bge-m3-GGUF](https://huggingface.co/gpustack/bge-m3-GGUF) `bge-m3-FP16.gguf` | 1.16 GB | 1.58 GB / 1.86 GB | 2048 tokens | MIT |

Memory is the resident memory of the model process on Linux x64; the peak includes one input at the
model's limit. On Windows, `bge-small-zh-v1.5` measured 139 MB working set (peak 174 MB). "Embedded
whole" is the longest text embedded in full; see [Long memories](#long-memories). How well each tier
recalls memories: https://github.com/N0rmal-114/feiyu-memgate#部署档位

- All four files are byte-identical to the files the gateway's thresholds were calibrated with
  (`parity: exact`), and their vectors were compared with Ollama serving the same file: cosine at
  least 0.9996 for every one of 60 texts, and the same best memory for all 20 test queries. On
  Windows, `bge-small-zh-v1.5` matched Ollama exactly (cosine 1.000000 for all 60 texts).
- The gateway's middle tier, embeddinggemma, is not offered. The GGUF checked lacks the model's two
  Dense projection layers, so its scores would not match the gateway's calibration.

## Install

Install it like any DSH plugin, next to `dsh-feiyu-memgate`: from the Plugins page, or

```
dsh plugin --profile <profile> add dsh-feiyu-memgate-embed
```

Two things happen during the install:

- **An install-script prompt.** node-llama-cpp has an install script, and DSH holds it back. Either
  decision works for this add-on. Allowed, the script only checks for the prebuilt binary (in the
  test it finished at once and downloaded or built nothing). Denied, nothing is missing (tested on
  Linux and Windows). The add-on never downloads or compiles a binary itself.
  - On the Plugins page the install stops and the page offers to allow the script and retry.
  - With `dsh plugin add`, the install stops with `ERR_PNPM_IGNORED_BUILDS` and exit code 1. The
    package is then on disk but not switched on, and running the same `add` again does not switch it
    on.
  - To deny the script, add this to the profile's `pnpm-workspace.yaml` (in `profiles/<profile>/` of
    the DSH home folder) before you install:

    ```yaml
    allowBuilds:
      node-llama-cpp: false
    ```

    If an install already stopped, run `dsh plugin --profile <profile> remove dsh-feiyu-memgate-embed`
    and then the `add` again.
- **A large `node_modules`.** pnpm installs node-llama-cpp's prebuilt binaries for every GPU kind it
  cannot rule out: 738 MB on Linux x64 and 745 MB on Windows x64 with DSH's profile settings, of which
  the CPU build this add-on uses is 33 MB. A package cannot prevent this; the profile can, with an
  `ignoredOptionalDependencies` list in its `pnpm-workspace.yaml`, which brought it to 90 MB on Linux.
  The list and the measurements are in [docs/DETAIL.en.md](docs/DETAIL.en.md#install-size).

Requirements: DSH 0.2.2 (release candidates from rc.1 on; checked against rc.1 to rc.3), Node 22.19 or later, and a platform node-llama-cpp ships a
CPU binary for. On Windows the binary needs the Microsoft Visual C++ 2015-2022 runtime (x64), which
most Windows installations already have.

## Use

On the memory plugin's settings page, pick a model and press its button. The add-on downloads it,
checks its SHA-256, and makes it active. The job belongs to the add-on, so closing the page does
not stop it. The choice is kept across restarts.

The chat command does the same in steps:

```
/memgate-embed list                      models, with size, licence and what is downloaded
/memgate-embed add bge-small-zh-v1.5     download a model (in the background)
/memgate-embed status                    state, download progress, model folder
/memgate-embed use bge-small-zh-v1.5     run it; the memory plugin picks it up when it is ready
```

| Command | What it does |
|---|---|
| `/memgate-embed` or `status` | State of the model and the endpoint, the input limit and how many inputs were truncated, download progress, source, model folder. |
| `list` | The registry's models. |
| `add <id>` | Download one model; one download at a time, resumed after an interruption. |
| `use <id>` / `use none` | Choose the model that runs, or none. |
| `remove <id>` | Delete a downloaded model (not the active one). |
| `cancel` | Stop the running download; the part already downloaded is kept for the next `add`. |
| `origin [auto\|huggingface\|hf-mirror\|<url>]` | Show or set the download source. |

The command is for you, not for the model: no tool is offered to the model. Replies follow the UI
language (English or Chinese).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `modules.engine` | on | Run the active model and provide the service. Off: no process, no port, no service. |
| `modules.command` | on | Register `/memgate-embed`. |
| `model.activeModel` | `none` | Pin a model here; it then wins over the settings page's button and `/memgate-embed use`. |
| `model.autoStart` | on | Load the model when DSH starts; off loads it on the first request. |
| `model.idleUnloadSeconds` | 0 | Unload the model after this many idle seconds; 0 never unloads. |
| `model.threads` | 0 | CPU threads; 0 chooses automatically. |
| `model.overflow` | `truncate` | Text longer than the model embeds whole: `truncate` embeds its beginning, `reject` refuses it. See [Long memories](#long-memories). |
| `download.origin` | `auto` | `auto`, `huggingface`, `hf-mirror` or a mirror address; anything but `auto` wins over `/memgate-embed origin`. |
| `download.dataDir` | empty | Folder for models and state; empty is `data/feiyu-memgate` in the DSH home folder. |

All settings apply without restarting DSH. The add-on writes nothing to your DSH profile; choices made
on the settings page or with the command live in its own state file (`embed-state.json` in the data
folder).

## Long memories

The memory gateway accepts memories of up to 4000 characters, more than a model embeds whole (512
tokens, roughly 500 Chinese characters, for the bge-zh models).

- **`truncate` (default).** A longer memory is stored and returned in full but searched by its
  beginning only: the vector comes from the first part that fits. This is what Ollama does by
  default, and the gateway's scores were calibrated through Ollama. For one over-long text per
  model, the add-on's vector matched Ollama's exactly (cosine 1.000000, same token count) with all
  four tested models.
- **`reject`.** Set `overflow` to `reject` and such a memory is refused, so the gateway reports the
  write as failed.

The limits are what the calibrated Ollama setup embeds, measured: 512, 2048 (bge-m3) and 4095 (qwen3)
tokens.

## With the memory plugin

The add-on provides the Cordis service `feiyuMemgateEmbed`. It carries:

- a snapshot: the state, the endpoint address and its key, the model id, the vector size, a
  fingerprint of the exact file, and the gateway calibration for that model;
- control methods (`list`, `add`, `install`, `use`, `cancel`, `remove`, `job`, `setOrigin`), which
  the settings page's button uses.

`dsh-feiyu-memgate` reads the snapshot and, while it says `ready`, runs its gateway with an `embedding`
store pointed at the endpoint. The contract (version 2), for this and any other consumer, is in
[docs/SERVICE.md](docs/SERVICE.md).

## How it works

- The model runs in a child process (`src/worker.js`) started with the host's Node or Electron binary
  as plain Node, through the host's subprocess service where there is one (in the Desktop app on
  Windows the host adds a small runner process of about 75 MB above it). If it crashes it is
  restarted with growing delays; DSH is not affected. It exits when its parent goes away.
- The model process listens on a loopback port with a random key that never leaves the add-on. In
  front of it, the add-on keeps a second loopback endpoint whose address and key stay the same across
  model restarts, idle unloads and model switches; that is the address in the service. A request that
  arrives while the model loads waits for it.
- The endpoint embeds exactly the text it receives. Prefixes for queries or documents are the
  caller's job (the memory gateway adds them from its calibration). Text longer than the model's
  limit is truncated as Ollama does it, or refused with HTTP 413 when `overflow` is `reject`.
- CPU only: no GPU, no download or compilation of binaries at run time. Downloads use the host's
  `fetch`, so the host's proxy settings apply.
- Logs and errors never contain memory text, queries or keys.

## Tests

```
npm test                                              # node --test, no dependencies needed
FEIYU_MEMGATE_SRC=../.. npm test                       # also compares the registry with the gateway's presets
DSH_FEIYU_MEMGATE_SRC=../dsh-feiyu-memgate npm test   # also checks the snapshot with the memory plugin's reader
```

`tests/docker/` holds the checks that need the real runtime and model files: `install-profile.sh`
installs the package into a DSH-like pnpm profile, `compare-models.mjs` runs each model and compares
it with an Ollama server (an over-long text included), and `e2e-gateway.mjs` installs a model through
the service's `install()` and runs the memory plugin's gateway against the add-on.

## Remove

Remove it from the Plugins page, or with `dsh plugin --profile <profile> remove dsh-feiyu-memgate-embed`.
Removing the package leaves its data in the DSH home folder:

- `data/feiyu-memgate/models/`, the downloaded models (up to 1.16 GB each). To free the space, run
  `/memgate-embed use none` and `/memgate-embed remove <id>` before you remove the add-on, or delete
  the folder afterwards.
- `data/feiyu-memgate/embed-state.json` and `data/feiyu-memgate/embed-worker.log`.

Do not delete the whole `data/feiyu-memgate` folder while you keep the memory plugin: it holds that
plugin's memories too. Delete any `feiyu-memgate-embed` entries you added to the profile's
`cordis.patch.yml`, or DSH warns `patch: entry "feiyu-memgate-embed" not found` at each start. The
`allowBuilds` line in `pnpm-workspace.yaml` can stay.

Without the add-on the memory plugin goes back to its keyword store. Memories saved while a model was
active stay in that model's store and are not found there; the memory plugin's README explains why.

## Tested and not tested

Tested on Linux x64 with Node 24, and on Windows 11 x64 in the DSH Desktop app's runtime (DSH
0.2.2-rc.1). The Windows test covered the install through DSH with the install script denied, the
download, the one-click install through the service, vectors identical to Ollama's and the memory
plugin's gateway on the `local` tier. macOS could not be tested on the machines available. Linux and
Windows on ARM are not tested either.

## Licence

MIT. The models have their own licences, listed above.
