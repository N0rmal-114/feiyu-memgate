// Retrieval tiers: how the managed gateway's memory store is built.
//
// One entry per tier. The settings choice (src/config.js), the generated gateway configuration
// (src/gateway.js), the /memgate command and the settings page all read this table. An entry says:
//   label      the name shown in settings and commands, per language
//   needs      what the tier needs before it can run, per language (for the tier list)
//   vector     whether the store is an embedding store (a migration target, see src/migrate.js)
//   missing    which settings or facts the tier still lacks (an empty list when it can start); the
//              gateway is not started while something is missing, and the warning names it
//   store      the [stores.memories] table of the gateway configuration, plus an id that names the
//              files of this store (the forgetting ledger, the archive and the migration state follow
//              the store they belong to)
//   env        secrets the store reads from the environment (key_env); never written to a file
//
// Every tier keeps its own store file in the data folder. Memories saved under one tier are not
// searched under another, and switching back finds them again; the copy of keyword memories into a
// vector store (migrateOnTierChange) is the only thing that moves texts between files.
//
// `facts` is what the plugin knows about its surroundings at the moment: { embed } is the snapshot
// of the embedding add-on's service (readEmbed below), or null when the add-on is absent.
import { createHash } from 'node:crypto'
import path from 'node:path'

// The environment variable through which the gateway receives an embeddings key (see `env`).
export const EMBEDDINGS_KEY_ENV = 'FEIYU_MEMGATE_EMBEDDINGS_KEY'

// The service the dsh-feiyu-memgate-embed add-on provides. Only the name and the shape couple the
// two packages: this plugin never imports the add-on.
export const EMBED_SERVICE = 'feiyuMemgateEmbed'
// The event the add-on emits with every new snapshot: replacing a service value notifies no one.
export const EMBED_UPDATE_EVENT = 'feiyu-memgate-embed/update'

// The model-name patterns of the gateway's own calibration presets (PRESETS in
// python/feiyu_memgate/backends/embedding.py; tests/tiers.test.js keeps this list in step with the
// bundled copy). The gateway matches a pattern contained in the lowercased model name.
export const GATEWAY_PRESETS = Object.freeze(['bge-m3', 'bge-small-zh', 'bge-base-zh', 'embeddinggemma', 'qwen3-embedding'])

export const hasGatewayPreset = (model) => GATEWAY_PRESETS.some((pattern) => String(model).toLowerCase().includes(pattern))

const MAX_PREFIX = 512 // the gateway's limit for a prefix (embedding.py, MAX_PREFIX)
const EMBED_STATES = new Set(['ready', 'loading', 'no-model', 'error'])

// A model name as part of a file name: the gateway refuses a store written by another model, so
// each model gets its own file and changing the model never breaks the old one.
function slug(text) {
  const clean = String(text).toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '')
  return clean.slice(0, 60) || 'model'
}

const shortHash = (text) => createHash('sha256').update(String(text)).digest('hex').slice(0, 12)

const isObject = (v) => v !== null && typeof v === 'object'
const finite = (v) => typeof v === 'number' && Number.isFinite(v)
const text = (v, max = 2048) => (typeof v === 'string' && v.length <= max ? v : '')

// One property of a value another plugin handed over: a getter that throws must not break this one.
function property(value, key) {
  try {
    return value[key]
  } catch {
    return undefined
  }
}

/**
 * The add-on's service value as a plain, checked snapshot, or null when it is absent or unusable.
 * Contract: docs/SERVICE.md of dsh-feiyu-memgate-embed (version 1). Only the fields this plugin uses
 * are kept: { version, state, error, url, key, model, dimensions, maxInputTokens, fingerprint,
 * calibration: { preset, parity, floor, ceil, query_prefix, document_prefix }, loaded, downloading,
 * models: [{ id, installed, active, sizeBytes, license }] } (license: the licence name, a string).
 */
export function readEmbed(value) {
  if (!isObject(value) && typeof value !== 'function') return null
  const state = property(value, 'state')
  if (!EMBED_STATES.has(state)) return null
  const rawCalibration = property(value, 'calibration')
  let calibration = null
  if (isObject(rawCalibration)) {
    const pick = (key) => property(rawCalibration, key)
    calibration = {
      preset: text(pick('preset'), 200),
      parity: pick('parity') === 'exact' || pick('parity') === 'approximate' ? pick('parity') : '',
      floor: finite(pick('floor')) ? pick('floor') : null,
      ceil: finite(pick('ceil')) ? pick('ceil') : null,
      query_prefix: typeof pick('query_prefix') === 'string' && pick('query_prefix').length <= MAX_PREFIX ? pick('query_prefix') : null,
      document_prefix: typeof pick('document_prefix') === 'string' && pick('document_prefix').length <= MAX_PREFIX ? pick('document_prefix') : null,
    }
  }
  const rawModels = property(value, 'models')
  const models = Array.isArray(rawModels)
    ? rawModels.slice(0, 50).filter(isObject).map((m) => ({
      id: text(property(m, 'id'), 200),
      installed: property(m, 'installed') === true,
      active: property(m, 'active') === true,
      sizeBytes: finite(property(m, 'sizeBytes')) ? property(m, 'sizeBytes') : null,
      license: text(property(m, 'license'), 200),
    })).filter((m) => m.id)
    : []
  const version = property(value, 'version')
  const dimensions = property(value, 'dimensions')
  const maxInputTokens = property(value, 'maxInputTokens')
  const error = property(value, 'error')
  return {
    version: Number.isInteger(version) ? version : null,
    state,
    // A short error code (SERVICE.md lists them); anything else is dropped, never shown.
    error: state === 'error' && typeof error === 'string' && /^[a-z][a-z-]{0,39}$/.test(error) ? error : '',
    url: text(property(value, 'url')),
    key: text(property(value, 'key')),
    model: text(property(value, 'model'), 200),
    dimensions: Number.isInteger(dimensions) && dimensions > 0 ? dimensions : null,
    maxInputTokens: Number.isInteger(maxInputTokens) && maxInputTokens > 0 ? maxInputTokens : null,
    fingerprint: text(property(value, 'fingerprint'), 500),
    calibration,
    loaded: property(value, 'loaded') === true,
    downloading: text(property(value, 'downloading'), 200),
    models,
  }
}

/** Whether the add-on can serve embeddings now: state ready, an http(s) address and a model id. */
export function embedReady(embed) {
  return Boolean(embed && embed.state === 'ready' && /^https?:\/\/[^\s/]+/i.test(embed.url) && embed.model)
}

/**
 * What identifies the add-on's current offer: a change of any of these restarts a gateway that uses it.
 * The add-on keeps url and key across model switches (SERVICE.md), so model, fingerprint and state
 * carry those; url and key still count, because they change when the add-on restarts. loaded,
 * downloading and the model list change often and mean nothing to a running gateway.
 */
export function embedKey(embed) {
  if (!embed) return null
  return JSON.stringify([embed.state, embed.url, embed.key, embed.model, embed.fingerprint, embed.calibration])
}

export const TIERS = Object.freeze({
  // Keyword search over SQLite, inside the gateway. Needs no model and no network.
  keyword: Object.freeze({
    label: { en: 'keyword (SQLite, no model)', zh: '关键词（SQLite，不需要模型）' },
    needs: { en: 'nothing; always available', zh: '无，始终可用' },
    vector: false,
    missing: () => [],
    store: (retrieval, dataDir) => ({
      id: 'keyword',
      table: { type: 'sqlite', path: path.join(dataDir, 'memories.db') },
    }),
    env: () => ({}),
  }),
  // Semantic search through an OpenAI-compatible embeddings endpoint (the gateway's `embedding` backend).
  endpoint: Object.freeze({
    label: { en: 'embeddings endpoint (OpenAI-compatible)', zh: '向量接口（OpenAI 兼容）' },
    needs: {
      en: 'retrieval.url and retrieval.model (and retrieval.apiKey when the endpoint wants one)',
      zh: 'retrieval.url 和 retrieval.model（接口需要密钥时还要 retrieval.apiKey）',
    },
    vector: true,
    missing: (retrieval) => [!retrieval.url && 'retrieval.url', !retrieval.model && 'retrieval.model'].filter(Boolean),
    store: (retrieval, dataDir) => {
      const id = 'endpoint-' + slug(retrieval.model)
      const table = { type: 'embedding', path: path.join(dataDir, `memories-${id}.db`), url: retrieval.url, model: retrieval.model }
      if (retrieval.apiKey) table.key_env = EMBEDDINGS_KEY_ENV
      return { id, table }
    },
    env: (retrieval) => (retrieval.apiKey ? { [EMBEDDINGS_KEY_ENV]: retrieval.apiKey } : {}),
  }),
  // Semantic search with a model the dsh-feiyu-memgate-embed add-on runs on this machine and serves
  // on a loopback address.
  local: Object.freeze({
    label: { en: 'local model (dsh-feiyu-memgate-embed add-on)', zh: '本地模型（dsh-feiyu-memgate-embed 插件）' },
    needs: {
      en: 'the dsh-feiyu-memgate-embed add-on, installed, switched on and with a model ready',
      zh: '安装并启用 dsh-feiyu-memgate-embed 插件，并且模型已就绪',
    },
    vector: true,
    missing: (retrieval, facts) => (embedReady(facts?.embed) ? [] : ['dsh-feiyu-memgate-embed (ready)']),
    store: (retrieval, dataDir, facts) => {
      const embed = facts.embed
      // The fingerprint names the exact vectors (model, quantization, file hash): two builds of one
      // model id never share an index file.
      const id = `local-${slug(embed.model)}-${shortHash(embed.fingerprint || embed.model)}`
      const table = { type: 'embedding', path: path.join(dataDir, `memories-${id}.db`), url: embed.url, model: embed.model }
      if (embed.key) table.key_env = EMBEDDINGS_KEY_ENV
      // The gateway's own preset wins: the add-on's numbers are used only for a model it does not know.
      const c = embed.calibration
      if (c && !hasGatewayPreset(embed.model)) {
        if (c.floor !== null && c.ceil !== null && c.ceil > c.floor) {
          table.floor = c.floor
          table.ceil = c.ceil
        }
        if (c.query_prefix !== null) table.query_prefix = c.query_prefix
        if (c.document_prefix !== null) table.document_prefix = c.document_prefix
      }
      return { id, table }
    },
    env: (retrieval, facts) => (facts?.embed?.key ? { [EMBEDDINGS_KEY_ENV]: facts.embed.key } : {}),
  }),
})

export const TIER_NAMES = Object.freeze(Object.keys(TIERS))
/** The values of the retrieval.tier setting: `auto` and every tier. */
export const TIER_CHOICES = Object.freeze(['auto', ...TIER_NAMES])
export const DEFAULT_TIER = 'auto'

/** The tier that runs for a setting: `auto` is local while the add-on is ready, keyword otherwise. */
export function effectiveTier(setting, facts) {
  if (setting !== 'auto' && Object.hasOwn(TIERS, setting)) return setting
  return embedReady(facts?.embed) ? 'local' : 'keyword'
}

/**
 * The store the managed gateway runs for these settings: { tier, missing, id, table, env }. While
 * something is missing, id and table are null and nothing may start.
 */
export function resolveStore(retrieval, dataDir, facts) {
  const tier = effectiveTier(retrieval.tier, facts)
  const entry = TIERS[tier]
  const missing = entry.missing(retrieval, facts)
  if (missing.length) return { tier, missing, id: null, table: null, env: {} }
  const { id, table } = entry.store(retrieval, dataDir, facts)
  return { tier, missing, id, table, env: entry.env(retrieval, facts) }
}
