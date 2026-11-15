// The model catalogue shipped for the settings page (src/catalogue.js) is a copy of the facts in the
// embedding add-on's model registry (dsh-feiyu-memgate-embed src/models.js). When an add-on checkout
// sits next to this one, the copy must be current (npm run sync-catalogue); otherwise only its shape
// is checked.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { ADDON_PACKAGE, CATALOGUE, TIER_LABELS, TIER_NOTES, TIERS_LINK, offeredModels } from '../src/catalogue.js'
import { GATEWAY_PRESETS } from '../src/tiers.js'
import { DEFAULT_SOURCE, TARGET, loadModels, pick, render } from '../scripts/sync-catalogue.mjs'

const addonPresent = fs.existsSync(DEFAULT_SOURCE)

test('the catalogue is the add-on registry (npm run sync-catalogue)', { skip: addonPresent ? false : 'no dsh-feiyu-memgate-embed checkout next to this one' }, async () => {
  const models = await loadModels()
  const source = fs.readFileSync(TARGET, 'utf8')
  assert.equal(render(source, models), source, 'src/catalogue.js is stale: run npm run sync-catalogue')
  assert.deepEqual(JSON.parse(JSON.stringify(CATALOGUE)), models.map(pick))
})

test('the catalogue holds only what a user decides on, and no download details', () => {
  assert.equal(ADDON_PACKAGE, 'dsh-feiyu-memgate-embed')
  assert.match(TIERS_LINK, /^https:\/\/github\.com\/N0rmal-114\/feiyu-memgate#/)
  assert.ok(CATALOGUE.length >= 4)
  for (const m of CATALOGUE) {
    assert.deepEqual(Object.keys(m).sort(), ['id', 'languages', 'license', 'memoryMB', 'requiresAcceptance', 'sizeBytes', 'status', 'tier', 'title'])
    assert.match(m.id, /^[a-z0-9][a-z0-9._-]+$/)
    assert.ok(Number.isSafeInteger(m.sizeBytes) && m.sizeBytes > 0)
    assert.ok(m.license.name && /^https:\/\//.test(m.license.url))
    assert.ok(TIER_NOTES[m.tier]?.en && TIER_NOTES[m.tier]?.zh, `a note for tier ${m.tier}`)
    assert.ok(TIER_LABELS[m.tier]?.en && TIER_LABELS[m.tier]?.zh, `a label for tier ${m.tier}`)
    // The memory of the model process is the add-on's measurement, never derived from the file: the
    // process holds the runtime and its context besides the weights.
    assert.ok(m.memoryMB && Number.isFinite(m.memoryMB.loaded) && m.memoryMB.peak >= m.memoryMB.loaded, m.id)
    assert.ok(m.memoryMB.loaded * 1e6 > m.sizeBytes, `${m.id}: the process needs more than the file`)
    // Every id carries the gateway preset it is calibrated with (the gateway matches by substring).
    assert.ok(GATEWAY_PRESETS.some((p) => m.id.includes(p)), m.id)
  }
  assert.ok(Object.isFrozen(CATALOGUE) && Object.isFrozen(CATALOGUE[0]))
  assert.ok(offeredModels().every((m) => m.status === 'verified'))
  assert.equal(CATALOGUE.find((m) => m.id === 'bge-small-zh-v1.5').memoryMB.loaded, 188, 'the measured figure, not the 47.9 MB file')
  for (const note of Object.values(TIER_NOTES)) assert.ok(!/\d+(\.\d+)?\s*%|recall@|ndcg/i.test(note.en + note.zh), 'no benchmark numbers in the notes')
})

test('a registry entry without a measurement keeps memory unknown instead of estimating it', () => {
  const base = { id: 'x-model', title: 'X', tier: 'top', sizeBytes: 10, languages: ['zh'], license: { name: 'MIT', url: 'https://opensource.org/license/mit' }, status: 'verified' }
  assert.equal(pick({ ...base, memoryMB: null }).memoryMB, null)
  assert.equal(pick(base).memoryMB, null)
  assert.deepEqual(pick({ ...base, memoryMB: { loaded: 0, peak: 30 } }).memoryMB, { loaded: null, peak: 30 })
})
