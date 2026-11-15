# The `feiyuMemgateEmbed` service (version 2)

`dsh-feiyu-memgate-embed` provides one Cordis service, `feiyuMemgateEmbed`. Its value is a frozen
object: a snapshot that says whether a local embedding model is ready and where and how to call it,
plus methods to list, download, activate and remove models. The feiyu-memgate memory plugin reads the
snapshot for its `local` retrieval tier, and its settings page drives the methods for the one-click
download. Any other plugin in the same host may use it the same way. The two packages are coupled only
through the service name and the shape below; neither imports the other.

Version 2 keeps every field of version 1 unchanged and adds `overflow`, `progress`, a `title` per model
and the control methods.

## Reading it

Do not list the service in your plugin's `inject`: every name there is required, and your plugin
would not start without the add-on. `ctx.get` reads a service without that requirement.

```js
export function apply(ctx) {
  const read = () => ctx.get('feiyuMemgateEmbed') ?? null   // undefined while the add-on is absent

  // Provided or withdrawn (the add-on was started, stopped, or its engine part switched on or off).
  ctx.on('internal/service', (name) => {
    if (name === 'feiyuMemgateEmbed') onChange(read())
  })
  // Every later change of the value. Replacing a service value with ctx.set does not fire
  // internal/service, so the add-on announces each new snapshot with this event.
  ctx.on('feiyu-memgate-embed/update', (snapshot) => onChange(snapshot))
}
```

Treat the value as data from another package: check every field before you use it. Read it again
when you need it; do not keep it for long. Its methods stay valid across snapshots: any snapshot's
`install` is the same function.

## Snapshot

| Field | Type | Meaning |
|---|---|---|
| `version` | `2` | The shape version. Version 1 fields keep their meaning in later versions. |
| `state` | `'no-model' \| 'loading' \| 'ready' \| 'error'` | See below. |
| `error` | string or `null` | Why `state` is `error`. |
| `url` | string or `null` | Base URL of the endpoint, `http://127.0.0.1:<port>/v1`. Set while `loading` or `ready`. |
| `key` | string or `null` | The bearer key for `url`. Set together with `url`. |
| `model` | string or `null` | The model id to send as `model`, for example `bge-m3`. It contains the memory gateway's calibration preset name. |
| `dimensions` | integer or `null` | Length of every vector. |
| `maxInputTokens` | integer or `null` | The longest input, in tokens with the model's special tokens, that is embedded whole. Longer input is truncated or refused, see `overflow`. |
| `overflow` | `'truncate' \| 'reject'` | (2) What happens to longer input: embedded by its beginning (default), or refused with HTTP 413. |
| `fingerprint` | string or `null` | `<model>\|<quantization>\|<first 12 hex of the file's SHA-256>`. Vectors from different fingerprints must not share an index. |
| `calibration` | object or `null` | `{ preset, parity, floor, ceil, min_score, ratio, broad_min_score, broad_ratio, query_prefix, document_prefix }`, copied from the memory gateway's preset for this model. `parity` is `exact` when the file is byte-identical to the one the preset was measured with, `approximate` otherwise. |
| `loaded` | boolean | The model is in memory now. `false` while `ready` means it was unloaded after idle time and loads again on the next request. |
| `downloading` | string or `null` | The model id being downloaded, if any. |
| `progress` | object or `null` | (2) The running download, else the last finished one: what `job()` returns. Re-published about once a second while a download runs. |
| `models` | array | Every model of the registry: `{ id, title, installed, active, sizeBytes, dimensions, license, licenseUrl, parity, status, requiresAcceptance }`. `license` is the licence name as a string; `title` is new in 2. |

States:

- `no-model`: no model is chosen. Nothing listens and no process runs. `url` and `key` are `null`.
- `loading`: a model is chosen and installed and is being checked or loaded. `url` and `key` are
  already set; requests wait up to 8 seconds for the load and then get HTTP 503 `model_loading` with
  `Retry-After: 2`. Wait for `ready` before you depend on it.
- `ready`: the model passed its self-test once. Requests are served. It stays `ready` across an idle
  unload and a restart after a crash.
- `error`: `error` is one of `not-installed`, `file-corrupt` (the file's size or SHA-256 is wrong),
  `unknown-model`, `endpoint-failed`, `bad-options`, `runtime-missing` (node-llama-cpp or its
  prebuilt binary for this platform is not installed), `load-failed`, `crashed`,
  `port-unavailable`. `url` and `key` are `null`.

`url` and `key` stay the same while a model is chosen and installed, through model loads, idle unloads,
restarts of the model process, and a switch to another model. They change when the add-on restarts or
after a `no-model` or `error` state. Compare them on every update.

## Control methods (version 2)

For plugins in the same host process, such as the memory plugin's settings page through its host
side. They are plain functions on the service object: not a tool the model can call, and not
reachable over the network. Every method returns a promise of a plain object and never throws:

- success: `{ ok: true, ... }`
- refusal: `{ ok: false, code, message }`, where `message` is a short English explanation and `code`
  is one of the codes in the table after the method list.

| Method | Result on success |
|---|---|
| `list()` | `{ ok, active, pinned, models: [row] }`. `active` is the active model id or `null`; `pinned` is the id a setting pins, or `null`. A row is `{ id, title, tier, sizeBytes, dimensions, memoryMB: { loaded, peak } \| null, maxInputTokens, license, licenseUrl, requiresAcceptance: { name, url } \| null, parity, status, installed, active, job }`; `job` is the running download of that model, else `null`. |
| `add(id, { acceptLicence })` | Starts the download in the background and returns at once: `{ ok, id, job, acceptedAt }`. Same refusals as `/memgate-embed add`. |
| `use(id)` | Makes a downloaded model active, `null` for none: `{ ok, active }`. |
| `install(id, { acceptLicence })` | The one-click path: `add`, then `use` once the download is verified, `{ ok, id, job, acceptedAt }`. When the file is already verified it activates at once: `{ ok, id, job: null, active }`. |
| `cancel()` | Stops the running download, `{ ok, id }`. The downloaded part is kept and the next `add` or `install` resumes it. |
| `remove(id)` | Deletes a downloaded model that is not active and not being downloaded, `{ ok, id, freedBytes }`. |
| `job()` | `{ ok, job }`. `job` is the running download, else the last finished one, else `null`. |
| `setOrigin(value)` | Sets the download source: `auto`, `huggingface`, `hf-mirror` or an `http(s)` origin. Returns `{ ok, origin: { value, pinned } }`. |

A job is `{ id, state, bytes, total, speed, source, activateWhenDone, error, activated }`:

- `state`: `starting`, `checking`, `probing`, `downloading` or `verifying` while running; then `done`,
  `failed` or `cancelled`.
- `speed` is in bytes per second; `source` is the origin used, one of the configured origins. It stays
  that origin when the origin redirects the file to another host (huggingface.co redirects to its
  CDN).
- `error`, for a failed job, is one of `network`, `http`, `timeout`, `integrity`, `disk-full`,
  `storage` or `unknown`.
- `activated`, for a finished `install`, is `true` once the model was made active, else `false`.

Codes:

| `code` | Meaning |
|---|---|
| `unknown-model` | Not a registry id, or not a string. |
| `needs-acceptance` | The model's licence must be accepted. The result has `licence: { name, url }`; call again with `acceptLicence` set to that name or URL, after the user has accepted it. No current model needs it. |
| `busy` | Another download is running (`job` shows it). An `install` of the model being downloaded joins that job instead. |
| `already-installed` | `add` of a model already downloaded and verified. |
| `not-installed` | `use` or `remove` of a model that is not downloaded. |
| `pinned` | The add-on's `activeModel` setting pins another model (`pinned` names it). The setting wins; tell the user to change it there. `install` of the pinned model itself succeeds. |
| `active` | `remove` of the active model. |
| `downloading` | `remove` of the model being downloaded. |
| `no-job` | `cancel` with no download running. |
| `invalid-origin` | `setOrigin` with anything else than the four forms. |
| `origin-pinned` | The add-on's `origin` setting is not `auto`; it wins. |
| `unavailable` | The add-on is stopping. |
| `failed` | Anything unexpected; only the kind is logged. |

How the methods behave:

- The job belongs to the add-on, not to the caller. A page that called `install` may close; the
  download finishes, is verified, and the model becomes active. Follow it through `progress` in the
  update event rather than by polling.
- A choice made with `use` or `install` is kept in the add-on's own state file, `embed-state.json` in
  its data folder under the DSH home, and survives restarts. The user's DSH profile is never written.
  A model pinned in the add-on's settings wins over it.
- Licence acceptance: an entry with `requiresAcceptance` is refused until `acceptLicence` names its
  licence. Nothing of it is fetched before that, not even a size check. The acceptance is recorded
  with its time in the state file, as with the chat command. No current entry needs it; a page should
  still handle the code, for a later model under such terms.
- The registry has four models, one for each of four of the gateway's tiers. The gateway's middle
  tier, embeddinggemma, is not offered: the GGUF checked lacks the model's two Dense projection
  layers, so its scores would not match the gateway's calibration.
- Downloads are one at a time, revision-pinned, resumed after an interruption, and checked against
  size and SHA-256 before the file gets its final name.
- The methods log nothing but ids and codes.

## The endpoint

`POST <url>/embeddings`, OpenAI-compatible:

```
Authorization: Bearer <key>
Content-Type: application/json

{ "model": "<model>", "input": "text" | ["text", ...], "encoding_format": "float" }
```

The response is `{ object: "list", data: [{ object: "embedding", index, embedding: number[] }], model,
usage: { prompt_tokens, total_tokens } }`. Vectors have unit length.

- The endpoint adds nothing to the input. Query and document prefixes, when a model needs them, are
  the caller's job; the memory gateway adds them itself from its preset, and `calibration` lists them.
- Input longer than `maxInputTokens` is truncated by default:
  - The beginning of the text is kept, with the model's special tokens at both ends, so the input is
    exactly `maxInputTokens` long. This is how Ollama truncates by default, and the memory gateway's
    calibration went through Ollama.
  - Measured with one over-long text per model against Ollama serving the same file: cosine
    1.000000 and the same token count for all four verified models.
  - `usage` counts the tokens embedded.
  - Every 200 answer has the header `x-truncated-inputs: <n>`, the number of inputs of that request
    that were cut. The memory gateway's client reads only the status and the body.
  - For a memory, this means a memory longer than the model's limit is found by its beginning only.
    The gateway still stores and returns its full text.
- With `overflow` set to `reject`, such input gets 413 `context_length_exceeded` instead, and the
  gateway reports the write or search as failed.
- The limits match what the calibrated Ollama setup embeds: 512 tokens for the two bge-zh models,
  2048 for bge-m3, 4095 for qwen3-embedding.
- At most 64 inputs and 4 MiB per request; more gets 413. Requests are embedded one at a time; more
  than 8 waiting requests get 503 `busy`.
- Errors use the OpenAI-compatible shape: `{ error: { message, type, code } }`. 401 for a missing or wrong key,
  404 for another path or another model, 405 for another method, 400 for a malformed body, 502 when
  the model process failed during the request.
- `encoding_format` other than `float` and `dimensions` other than the model's own are refused (400).

## The key

The key is random per start of the endpoint and protects the endpoint from other programs on the
computer. Plugins in the same host can read it from the service on purpose. Pass it on through an
environment variable, as the memory plugin does with its gateway; do not write it to a file, a log or a
message. The model process behind the endpoint has a key of its own that never leaves the add-on.
