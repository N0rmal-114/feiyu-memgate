# feiyu-memgate local embeddings

This add-on runs an embedding model on your computer's CPU and offers it to the feiyu-memgate memory
plugin. With it, the memory plugin's `local` retrieval tier compares memories by meaning instead of
by shared words, without any online service. Without a model, the memory plugin keeps its keyword
search.

## Nothing is downloaded until you ask

Installing or enabling the add-on downloads no model; no model ships with it. The normal way to get one
is the button on the feiyu-memgate memory plugin's settings page. It downloads the model you pick,
checks it, and makes it active in one step; the job carries on if you close the page. The
chat command does the same in steps:

```
/memgate-embed list
/memgate-embed add bge-small-zh-v1.5
/memgate-embed status
/memgate-embed use bge-small-zh-v1.5
```

The model you choose either way is remembered in the add-on's own state file and runs again after a
restart. A model picked in this add-on's `activeModel` setting wins over both.

## The models

Each file is pinned to one commit of its Hugging Face repository and checked against its size and
SHA-256 before it is used. Memory is the model process's resident memory measured on Linux x64
(CPU only); "peak" includes an input at the model's limit. "Embedded whole" is the longest text, in
tokens, that is embedded in full (see "Long memories" below).

| id | Size | Memory after load / peak | Embedded whole | Licence | Source file |
|---|---|---|---|---|---|
| `bge-small-zh-v1.5` | 47.9 MB | 188 MB / 219 MB | 512 tokens | MIT | `CompendiumLabs/bge-small-zh-v1.5-gguf`, `bge-small-zh-v1.5-f16.gguf` |
| `bge-base-zh-v1.5` | 110 MB | 251 MB / 284 MB | 512 tokens | MIT | `CompendiumLabs/bge-base-zh-v1.5-gguf`, `bge-base-zh-v1.5-q8_0.gguf` |
| `qwen3-embedding-0.6b` | 639 MB | 1.35 GB / 1.77 GB | 4095 tokens | Apache-2.0 | `Qwen/Qwen3-Embedding-0.6B-GGUF`, `Qwen3-Embedding-0.6B-Q8_0.gguf` |
| `bge-m3` | 1.16 GB | 1.58 GB / 1.86 GB | 2048 tokens | MIT | `gpustack/bge-m3-GGUF`, `bge-m3-FP16.gguf` |

- All four files are byte-identical to the files the memory gateway's scores were calibrated with, so
  its thresholds apply unchanged. How well each tier recalls:
  https://github.com/N0rmal-114/feiyu-memgate#部署档位
- The gateway's middle tier, embeddinggemma, is not offered. The GGUF checked lacks the model's two
  Dense projection layers, so its scores would not match the gateway's calibration.
- On Windows (DSH Desktop runtime), `bge-small-zh-v1.5` used 139 MB working set after loading and
  174 MB at its peak. The host's subprocess runner above it adds about 75 MB.

## Where downloads come from and go

- Source: `auto` (default) asks huggingface.co and hf-mirror.com and uses the first that answers;
  `huggingface`, `hf-mirror` or the address of a Hugging Face compatible mirror picks one. Set it with
  `/memgate-embed origin <value>` or in the settings. Downloads use the host's network settings,
  including its proxy.
- Files: `data/feiyu-memgate/models/<id>/` in the DSH home folder, unless the `dataDir` setting names
  another folder. An interrupted download resumes from where it stopped; a file is renamed to its
  final name only after its size and SHA-256 match. `/memgate-embed remove <id>` deletes it.
- One download runs at a time; `/memgate-embed cancel` stops it.

## Install size

node-llama-cpp, which runs the models, lists a prebuilt binary for every platform and GPU kind as
optional dependencies, and pnpm installs every one it cannot rule out. Measured on Linux x64 with
pnpm 11.28.5 and the profile settings DSH writes:

| Profile settings | node_modules |
|---|---|
| DSH's defaults | 738 MB: CUDA builds 577 MB, Vulkan build 52 MB, ARM builds 21 MB, the CPU build 33 MB, node-llama-cpp 43 MB (745 MB on Windows x64) |
| with the `ignoredOptionalDependencies` list below | 90 MB |

The add-on itself uses only the CPU build. A package cannot keep pnpm from installing the others:
the same list in the add-on's own package.json, `overrides`, and `supportedArchitectures` changed
nothing in the test. Only the profile can. To save the space, add this to the profile's
`pnpm-workspace.yaml` (in `profiles/<profile>/` of the DSH home folder) before you install, or
reinstall afterwards. This list is for Linux x64 and Windows x64; on another platform keep the
package for it (Apple silicon: `mac-arm64-metal`; Intel Mac: `mac-x64`; ARM Linux: `linux-arm64`;
Windows on ARM: `win-arm64`).

```yaml
ignoredOptionalDependencies:
  - "@node-llama-cpp/linux-x64-cuda"
  - "@node-llama-cpp/linux-x64-cuda-ext"
  - "@node-llama-cpp/linux-x64-vulkan"
  - "@node-llama-cpp/linux-arm64"
  - "@node-llama-cpp/linux-armv7l"
  - "@node-llama-cpp/win-x64-cuda"
  - "@node-llama-cpp/win-x64-cuda-ext"
  - "@node-llama-cpp/win-x64-vulkan"
  - "@node-llama-cpp/win-arm64"
  - "@node-llama-cpp/mac-arm64-metal"
```

node-llama-cpp also has an install script, which DSH holds back until you decide. Either decision
works. Allowed, the script finished at once in the test and downloaded or built nothing. Denied, the
add-on ran normally on Linux and Windows. The add-on never downloads or builds binaries itself.

- The Plugins page offers to allow the script and retry.
- `dsh plugin add` stops with `ERR_PNPM_IGNORED_BUILDS` and exit code 1. The package stays on disk
  but is not switched on, and the same `add` again does not switch it on.

To deny the script, add `allowBuilds:` with `node-llama-cpp: false` to the same file before you
install. After an install that already stopped, `dsh plugin --profile <profile> remove
dsh-feiyu-memgate-embed` and then the `add` again switch it on (measured on Windows with DSH
0.2.2-rc.1).

On Windows the prebuilt binary loads the Microsoft Visual C++ 2015-2022 runtime (MSVCP140.dll,
VCRUNTIME140.dll) from the system; most Windows installations have it. llama.cpp picks the CPU
variant for the processor itself.

## Long memories

The memory gateway accepts memories of up to 4000 characters. That is more than a model embeds whole:
512 tokens for the two bge-zh models (roughly 500 Chinese characters), 2048 for bge-m3, 4095 for
qwen3-embedding.

- **`truncate` (default).** A longer memory is stored and returned in full, but searched by its
  beginning only. The vector is made from the first part that fits. Ollama does the same by default,
  and the gateway's scores were calibrated through Ollama: for an over-long text the add-on's vector
  matched Ollama's exactly (cosine 1.000000) with each of the four tested models.
- **`reject`.** With the `overflow` setting at `reject`, such a memory is refused and the gateway
  reports the write as failed.

`/memgate-embed status` shows the limit and how many inputs were truncated since the add-on started.

## Limits

- CPU only. One model runs at a time, in its own process; a crash of that process does not affect
  DSH, and the add-on restarts it.
- Tested on Linux x64 (Node 24) and on Windows 11 x64 in the Desktop app's runtime (DSH
  0.2.2-rc.1). macOS could not be tested on the machines available; Linux and Windows on ARM were
  not tested either.

## Removing the add-on

Removing the package leaves its data in the DSH home folder:

- `data/feiyu-memgate/models/`, the downloaded models;
- `data/feiyu-memgate/embed-state.json` and `embed-worker.log`.

Delete these by hand, or remove the models first with `/memgate-embed use none` and
`/memgate-embed remove <id>`. Keep the rest of `data/feiyu-memgate` while you use the memory plugin:
its memories are there. Delete any `feiyu-memgate-embed` entries in the profile's `cordis.patch.yml`,
or DSH warns `patch: entry "feiyu-memgate-embed" not found` at each start.
