// The local embedding models the settings page offers while the embedding add-on is not installed.
//
// When the add-on runs, its service's list() is the authority (src/setup.js prefers it); this copy
// only lets the page show the choice before anything is installed. The table between the markers is
// generated from the add-on's src/models.js (scripts/sync-catalogue.mjs; tests/catalogue.test.js
// fails when it is stale), including the memory the add-on measured for each model process
// (`memoryMB.loaded` after loading, `memoryMB.peak` with an input at the limit). The notes below are
// this plugin's own words: what a tier buys, never benchmark numbers; the measured retrieval numbers
// live in the gateway's README (TIERS_LINK).

/** The npm package of the embedding add-on, installed through the host's plugin manager. */
export const ADDON_PACKAGE = 'dsh-feiyu-memgate-embed'

/** Where the measured retrieval quality of every tier is documented. */
export const TIERS_LINK = 'https://github.com/N0rmal-114/feiyu-memgate#部署档位'

function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) deepFreeze(child)
    Object.freeze(value)
  }
  return value
}

// <catalogue>
// Generated from dsh-feiyu-memgate-embed src/models.js by scripts/sync-catalogue.mjs; do not edit.
export const CATALOGUE = deepFreeze([
  {
    "id": "bge-small-zh-v1.5",
    "title": "BGE small zh v1.5",
    "tier": "lightest",
    "sizeBytes": 47886240,
    "memoryMB": {
      "loaded": 188,
      "peak": 219
    },
    "languages": [
      "zh"
    ],
    "license": {
      "name": "MIT",
      "url": "https://opensource.org/license/mit"
    },
    "requiresAcceptance": null,
    "status": "verified"
  },
  {
    "id": "bge-base-zh-v1.5",
    "title": "BGE base zh v1.5",
    "tier": "light",
    "sizeBytes": 110001568,
    "memoryMB": {
      "loaded": 251,
      "peak": 284
    },
    "languages": [
      "zh"
    ],
    "license": {
      "name": "MIT",
      "url": "https://opensource.org/license/mit"
    },
    "requiresAcceptance": null,
    "status": "verified"
  },
  {
    "id": "qwen3-embedding-0.6b",
    "title": "Qwen3 Embedding 0.6B",
    "tier": "upper-middle",
    "sizeBytes": 639150592,
    "memoryMB": {
      "loaded": 1350,
      "peak": 1767
    },
    "languages": [
      "multilingual"
    ],
    "license": {
      "name": "Apache-2.0",
      "url": "https://www.apache.org/licenses/LICENSE-2.0"
    },
    "requiresAcceptance": null,
    "status": "verified"
  },
  {
    "id": "bge-m3",
    "title": "BGE-M3",
    "tier": "top",
    "sizeBytes": 1157671200,
    "memoryMB": {
      "loaded": 1577,
      "peak": 1863
    },
    "languages": [
      "multilingual"
    ],
    "license": {
      "name": "MIT",
      "url": "https://opensource.org/license/mit"
    },
    "requiresAcceptance": null,
    "status": "verified"
  }
])
// </catalogue>

/** One line per tier on what it buys, per language. */
export const TIER_NOTES = deepFreeze({
  lightest: {
    en: 'Smallest download, Chinese only: recall by meaning instead of shared words, at the lowest cost.',
    zh: '下载最小，仅支持中文：使用语义检索而非关键词检索，开销最低。',
  },
  light: {
    en: 'Chinese only, a larger model than the lightest tier for steadier Chinese recall.',
    zh: '仅支持中文，模型比最轻档更大，中文召回更稳定。',
  },
  'upper-middle': {
    en: 'Multilingual with long inputs: a balance of size and quality for mixed Chinese and English memories.',
    zh: '支持多语言和长文本：兼顾体积与效果，适合中英文混合的记忆。',
  },
  top: {
    en: 'Multilingual with long inputs: the largest download and the strongest recall of the list.',
    zh: '支持多语言和长文本：下载最大，召回效果为列表中最好。',
  },
})

/**
 * The tier names as the page and the command show them. The add-on's registry names tiers with
 * internal words (lightest, light, upper-middle, top); a tier missing here is shown without a label
 * rather than as an untranslated word.
 */
export const TIER_LABELS = deepFreeze({
  lightest: { en: 'lightest tier', zh: '最轻档' },
  light: { en: 'light tier', zh: '轻量档' },
  'upper-middle': { en: 'upper-middle tier', zh: '中高档' },
  top: { en: 'top tier', zh: '最高档' },
})

/** Catalogue entries offered by default: the ones the add-on's engine has run. */
export function offeredModels() {
  return CATALOGUE.filter((m) => m.status === 'verified')
}

/** A catalogue entry by id, or undefined. */
export function catalogueEntry(id) {
  return CATALOGUE.find((m) => m.id === id)
}
