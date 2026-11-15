// Copies the model list of the embedding add-on (dsh-feiyu-memgate-embed, src/models.js) into
// src/catalogue.js, between its <catalogue> markers.
//
// The settings page lists the local embedding models also while the add-on is not installed, so this
// plugin ships a small copy of the facts a user decides on: ids, titles, tiers, download sizes, the
// measured memory of the model process (loaded and peak, in MB, as the add-on's registry states it),
// languages and licences. Nothing else is copied (no download addresses or hashes: downloading is the
// add-on's job), and memory is never estimated from the file size. The two
// packages never import each other at run time; this copy is made when the list changes:
//   npm run sync-catalogue                        read ../dsh-feiyu-memgate-embed/src/models.js
//   npm run sync-catalogue -- --from <models.js>  read another checkout
//   npm run sync-catalogue -- --check             exit 1 when the block is stale
// tests/catalogue.test.js runs the same check whenever an add-on checkout sits next to this one.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
export const TARGET = path.join(here, '..', 'src', 'catalogue.js')
export const DEFAULT_SOURCE = path.join(here, '..', '..', 'dsh-feiyu-memgate-embed', 'src', 'models.js')
const MARKERS = /\/\/ <catalogue>\r?\n[\s\S]*?\/\/ <\/catalogue>/

const licence = (value) => (value && typeof value.name === 'string' ? { name: value.name, url: typeof value.url === 'string' ? value.url : '' } : null)
const megabytes = (value) => (Number.isFinite(value) && value > 0 ? value : null)
// The registry's measurement, or null where it has none: the page then says "not measured".
const memory = (value) => {
  const loaded = megabytes(value?.loaded)
  const peak = megabytes(value?.peak)
  return loaded === null && peak === null ? null : { loaded, peak }
}

/** The facts this plugin keeps of one registry entry. */
export function pick(model) {
  return {
    id: model.id,
    title: typeof model.title === 'string' ? model.title : '',
    tier: model.tier,
    sizeBytes: model.sizeBytes,
    memoryMB: memory(model.memoryMB),
    languages: [...model.languages],
    license: licence(model.license),
    requiresAcceptance: licence(model.requiresAcceptance),
    status: model.status,
  }
}

/** The source of src/catalogue.js with its block rebuilt from `models`. */
export function render(source, models) {
  if (!MARKERS.test(source)) throw new Error('src/catalogue.js has no <catalogue> block')
  const table = JSON.stringify(models.map(pick), null, 2)
  const block = `// <catalogue>\n// Generated from dsh-feiyu-memgate-embed src/models.js by scripts/sync-catalogue.mjs; do not edit.\nexport const CATALOGUE = deepFreeze(${table})\n// </catalogue>`
  return source.replace(MARKERS, () => block)
}

/** The add-on's MODELS, read from its models.js. */
export async function loadModels(file = DEFAULT_SOURCE) {
  const { MODELS } = await import(pathToFileURL(file).href)
  if (!Array.isArray(MODELS)) throw new Error('models.js exports no MODELS array')
  return MODELS
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1])) {
  const at = process.argv.indexOf('--from')
  const from = at > 0 ? path.resolve(process.argv[at + 1]) : DEFAULT_SOURCE
  const models = await loadModels(from)
  const source = fs.readFileSync(TARGET, 'utf8')
  const next = render(source, models)
  if (process.argv.includes('--check')) {
    if (next !== source) {
      console.error('src/catalogue.js: the model catalogue is stale; run npm run sync-catalogue')
      process.exit(1)
    }
    console.log('src/catalogue.js: current')
  } else if (next !== source) {
    fs.writeFileSync(TARGET, next, 'utf8')
    console.log('src/catalogue.js: catalogue updated')
  } else {
    console.log('src/catalogue.js: catalogue already current')
  }
}
