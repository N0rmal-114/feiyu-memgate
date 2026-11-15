// The model registry: the only files this add-on can download and run.
//
// One entry per tier. Every entry is pinned: the Hugging Face repository, a commit hash, the file
// name, its exact size and its SHA-256 (the `lfs.sha256` of the Hugging Face file metadata, read on
// 2026-11-15). There is no other way to name a file: a download URL is always built from an entry.
//
// Four of the gateway's five tiers have an entry. The middle tier (embeddinggemma) has none: the GGUF
// checked (unsloth/embeddinggemma-300m-GGUF, BF16) lacks the model's two Dense projection layers
// after mean pooling (314 tensors against the 316 of the calibrated Ollama file, without
// dense.0.weight and dense.1.weight), so its vectors lie in another space (cosine about 0 to Ollama's)
// and the gateway's calibration would not apply. Applying Ollama's two matrices to this engine's
// output matched Ollama (cosine 0.99996): the engine is right, the file is not.
//
// `id` is the model name the memory gateway sees. The gateway picks its calibration preset by the
// first pattern contained in the lower-cased model name (feiyu-memgate, backends/embedding.py,
// PRESETS and preset_for), so every id contains its preset string, and no id contains a pattern that
// comes earlier in that table. `calibration` repeats the gateway's numbers for that preset, so the
// base plugin can show or apply them; tests compare them with the gateway's own table.
//
// `parity`: "exact" when the file is byte-identical to the one the gateway was calibrated with (the
// Ollama blob whose digest equals this sha256), "approximate" when it is another file of the same
// model, so scores can shift a little.
//
// `contextTokens`: the longest input, in tokens including the model's special tokens, the endpoint
// embeds; longer input is truncated to it (or refused, with the overflow setting "reject"). It is
// what the Ollama setup the gateway was calibrated with embeds, measured on 2026-11-15 with one
// over-long text per model (prompt_eval_count, Ollama 0.40.15 with its default context and
// OLLAMA_NUM_PARALLEL=2): 512 for the bge v1.5 models (their trained context), 2048 for bge-m3 and
// 4095 for qwen3-embedding. Matching it keeps long texts at calibration parity (cosine 1.000000
// against Ollama for those texts), and a smaller context costs less memory.
//
// `status`: "verified" when the engine ran this exact model in a test (load, vectors compared with
// Ollama); "unverified" otherwise. Unverified entries are left out of the default list.
//
// `requiresAcceptance` ({ name, url }, optional): licence terms the user must accept before anything
// of the model is fetched. No current entry needs it; the add-on keeps the mechanism for models
// under such terms.
//
// `title`: a display name. `memoryMB`: the model process's resident memory after loading and its
// peak with an input at the limit, measured on Linux x64 (CPU only, node-llama-cpp 3.22.3); null
// where not measured.

const MIT = Object.freeze({ name: 'MIT', url: 'https://opensource.org/license/mit' })
const APACHE = Object.freeze({ name: 'Apache-2.0', url: 'https://www.apache.org/licenses/LICENSE-2.0' })

function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) deepFreeze(child)
    Object.freeze(value)
  }
  return value
}

export const MODELS = deepFreeze([
  {
    id: 'bge-small-zh-v1.5',
    title: 'BGE small zh v1.5',
    tier: 'lightest',
    memoryMB: { loaded: 188, peak: 219 },
    preset: 'bge-small-zh',
    repository: 'CompendiumLabs/bge-small-zh-v1.5-gguf',
    revision: '5bf683e6a1bd454bbb60ba051088c50731d63fcb',
    file: 'bge-small-zh-v1.5-f16.gguf',
    quant: 'f16',
    sizeBytes: 47886240,
    sha256: 'ab9b81d9cd329c712eee379cf0068eabe6a5e2a01d0def61535eba9384085e2c',
    dimensions: 512,
    contextTokens: 512,
    languages: ['zh'],
    upstream: 'BAAI/bge-small-zh-v1.5',
    license: MIT,
    parity: 'exact',
    status: 'verified',
    calibration: {
      floor: 0.40, ceil: 0.80, min_score: 0.30, ratio: 0.90, broad_min_score: 0.10, broad_ratio: 0.65,
      query_prefix: '', document_prefix: '',
    },
  },
  {
    id: 'bge-base-zh-v1.5',
    title: 'BGE base zh v1.5',
    tier: 'light',
    memoryMB: { loaded: 251, peak: 284 },
    preset: 'bge-base-zh',
    repository: 'CompendiumLabs/bge-base-zh-v1.5-gguf',
    revision: 'd7a5acaef6b15190a0fd5d2983f7ace368ca9db1',
    file: 'bge-base-zh-v1.5-q8_0.gguf',
    quant: 'q8_0',
    sizeBytes: 110001568,
    sha256: '893a0f07100c135b35196fc95058f5933c3435905b167ac29c58d77d7cef4be7',
    dimensions: 768,
    contextTokens: 512,
    languages: ['zh'],
    upstream: 'BAAI/bge-base-zh-v1.5',
    license: MIT,
    parity: 'exact',
    status: 'verified',
    calibration: {
      floor: 0.35, ceil: 0.80, min_score: 0.25, ratio: 0.75, broad_min_score: 0.05, broad_ratio: 0.50,
      query_prefix: '为这个句子生成表示以用于检索相关文章：', document_prefix: '',
    },
  },
  {
    id: 'qwen3-embedding-0.6b',
    title: 'Qwen3 Embedding 0.6B',
    tier: 'upper-middle',
    memoryMB: { loaded: 1350, peak: 1767 },
    preset: 'qwen3-embedding',
    repository: 'Qwen/Qwen3-Embedding-0.6B-GGUF',
    revision: '370f27d7550e0def9b39c1f16d3fbaa13aa67728',
    file: 'Qwen3-Embedding-0.6B-Q8_0.gguf',
    quant: 'q8_0',
    sizeBytes: 639150592,
    sha256: '06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439',
    dimensions: 1024,
    contextTokens: 4095,
    languages: ['multilingual'],
    upstream: 'Qwen/Qwen3-Embedding-0.6B',
    license: APACHE,
    parity: 'exact',
    status: 'verified',
    calibration: {
      floor: 0.35, ceil: 0.80, min_score: 0.20, ratio: 0.95, broad_min_score: 0.15, broad_ratio: 0.65,
      query_prefix: 'Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:',
      document_prefix: '',
    },
  },
  {
    id: 'bge-m3',
    title: 'BGE-M3',
    tier: 'top',
    memoryMB: { loaded: 1577, peak: 1863 },
    preset: 'bge-m3',
    repository: 'gpustack/bge-m3-GGUF',
    revision: '2d48f1737679ad900d5c26c5aad5410e9c70fdca',
    file: 'bge-m3-FP16.gguf',
    quant: 'f16',
    sizeBytes: 1157671200,
    sha256: 'daec91ffb5dd0c27411bd71f29932917c49cf529a641d0168496c3a501e3062c',
    dimensions: 1024,
    contextTokens: 2048,
    languages: ['multilingual'],
    upstream: 'BAAI/bge-m3',
    license: MIT,
    parity: 'exact',
    status: 'verified',
    calibration: {
      floor: 0.40, ceil: 0.80, min_score: 0.50, ratio: 0.85, broad_min_score: 0.30, broad_ratio: 0.60,
      query_prefix: '', document_prefix: '',
    },
  },
])

export const MODEL_IDS = Object.freeze(MODELS.map((m) => m.id))

/** The suggested first model: the smallest calibrated one. */
export const DEFAULT_SUGGESTION = 'bge-small-zh-v1.5'

/** The registry entry for an id, or undefined. Ids are matched exactly (they are lower case). */
export function getModel(id) {
  return typeof id === 'string' ? MODELS.find((m) => m.id === id) : undefined
}

/** Entries offered by default: everything a test has run. */
export function listedModels({ all = false } = {}) {
  return MODELS.filter((m) => all || m.status === 'verified')
}

/** The revision-pinned download URL of an entry on a Hugging Face compatible origin. */
export function fileUrl(model, origin) {
  const base = new URL(origin)
  const repo = model.repository.split('/').map(encodeURIComponent).join('/')
  return new URL(`/${repo}/resolve/${model.revision}/${encodeURIComponent(model.file)}`, base.origin).href
}

/** The page of an entry's repository, for people. */
export function repositoryUrl(model) {
  return `https://huggingface.co/${model.repository}`
}

/** The identity of the vectors an entry produces: model, file format and the start of the file hash. */
export function fingerprint(model) {
  return `${model.id}|${model.quant}|${model.sha256.slice(0, 12)}`
}
