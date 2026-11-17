// Retrieval tiers: the embedding add-on's service snapshot, which tier runs for each setting, and the
// store each tier builds (secrets only through the environment, calibration only where the gateway has
// no preset, one file per model build).
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'
import { PACKAGE_DIR, findPython, scrubbedEnv } from '../src/gateway.js'
import {
  DEFAULT_TIER, EMBEDDINGS_KEY_ENV, GATEWAY_PRESETS, TIERS, TIER_CHOICES, TIER_NAMES, effectiveTier, embedKey, embedReady,
  hasGatewayPreset, keywordIndexFile, readEmbed, resolveStore,
} from '../src/tiers.js'

const ISOLATED_ENV = { ...process.env, DSH_HOME: path.join(os.tmpdir(), 'dsh-memgate-no-home'), DSH_PRIMARY_RUNTIME: '' }
const python = await findPython({ cache: null, env: ISOLATED_ENV })

const service = (overrides = {}) => ({
  version: 1,
  state: 'ready',
  url: 'http://127.0.0.1:43123/v1',
  key: 'fake-addon-key',
  model: 'bge-small-zh-v1.5',
  dimensions: 512,
  calibration: { preset: 'bge-small-zh', parity: 'exact', floor: 0.4, ceil: 0.8, query_prefix: '', document_prefix: '' },
  fingerprint: 'bge-small-zh-v1.5|f16|0123456789ab',
  models: [{ id: 'bge-small-zh-v1.5', installed: true, active: true, sizeBytes: 47000000, license: 'MIT' }],
  ...overrides,
})
const RETRIEVAL = { tier: 'auto', url: '', model: '', apiKey: '' }

test('the add-on snapshot keeps the contract fields and drops anything malformed', () => {
  const embed = readEmbed(service())
  assert.equal(embed.state, 'ready')
  assert.equal(embed.dimensions, 512)
  assert.deepEqual(embed.models, [{ id: 'bge-small-zh-v1.5', installed: true, active: true, sizeBytes: 47000000, license: 'MIT' }])
  assert.equal(readEmbed(undefined), null)
  assert.equal(readEmbed({ state: 'warming up' }), null, 'an unknown state is not trusted')
  const odd = readEmbed(service({ dimensions: -1, calibration: { floor: 'low', ceil: Infinity, parity: 'close', query_prefix: 'x'.repeat(600) }, models: [null, { id: '' }, 3] }))
  assert.equal(odd.dimensions, null)
  assert.deepEqual([odd.calibration.floor, odd.calibration.ceil, odd.calibration.parity, odd.calibration.query_prefix], [null, null, '', null])
  assert.deepEqual(odd.models, [])
  // A getter that throws does not break the plugin.
  const hostile = { state: 'ready', get url() { throw new Error('nope') }, model: 'm' }
  assert.equal(readEmbed(hostile).url, '')
  assert.equal(embedReady(readEmbed(hostile)), false)
})

test('ready means state ready, an http(s) address and a model', () => {
  assert.equal(embedReady(readEmbed(service())), true)
  for (const overrides of [{ state: 'loading' }, { state: 'no-model' }, { state: 'error' }, { url: '' }, { url: 'file:///x' }, { model: '' }]) {
    assert.equal(embedReady(readEmbed(service(overrides))), false, JSON.stringify(overrides))
  }
  assert.equal(embedReady(null), false)
})

test('auto is local while the add-on is ready and keyword otherwise; explicit tiers stay as chosen', () => {
  const ready = { embed: readEmbed(service()) }
  const loading = { embed: readEmbed(service({ state: 'loading' })) }
  assert.equal(DEFAULT_TIER, 'auto')
  assert.deepEqual(TIER_CHOICES, ['auto', ...TIER_NAMES])
  assert.equal(effectiveTier('auto', ready), 'local')
  assert.equal(effectiveTier('auto', loading), 'keyword')
  assert.equal(effectiveTier('auto', {}), 'keyword')
  assert.equal(effectiveTier('keyword', ready), 'keyword')
  assert.equal(effectiveTier('endpoint', ready), 'endpoint')
  assert.equal(effectiveTier('local', {}), 'local', 'an explicit local tier waits for the add-on instead of falling back')
  assert.deepEqual(resolveStore({ ...RETRIEVAL, tier: 'local' }, '/d', {}).missing, ['dsh-feiyu-memgate-embed (ready)'])
  assert.equal(resolveStore({ ...RETRIEVAL, tier: 'local' }, '/d', {}).table, null)
})

test('every tier is labelled, says what it needs, and builds a store once it has what it needs', () => {
  const facts = { embed: readEmbed(service()) }
  const retrieval = { tier: 'x', url: 'http://127.0.0.1:9/v1', model: 'Some/Model:Large', apiKey: '' }
  for (const name of TIER_NAMES) {
    const tier = TIERS[name]
    assert.ok(tier.label.en && tier.label.zh && tier.needs.en && tier.needs.zh, name)
    assert.equal(typeof tier.vector, 'boolean')
    assert.deepEqual(tier.missing(retrieval, facts), [])
    const { id, table } = tier.store(retrieval, '/data', facts)
    assert.match(id, /^[a-z0-9._-]+$/)
    assert.ok(table.type && table.path)
    assert.equal(tier.vector, table.type === 'embedding')
  }
  assert.deepEqual(TIERS.endpoint.missing({ url: '', model: '' }), ['retrieval.url', 'retrieval.model'])
  assert.equal(TIERS.endpoint.store({ url: 'http://x/v1', model: 'bge-m3', apiKey: 'k' }, '/d').table.key_env, EMBEDDINGS_KEY_ENV)
  assert.equal(TIERS.endpoint.store({ url: 'http://x/v1', model: 'bge-m3', apiKey: '' }, '/d').table.key_env, undefined)
})

test('the local store: the key travels in the environment, the file follows the fingerprint', () => {
  const embed = readEmbed(service())
  const store = resolveStore(RETRIEVAL, path.join('/data'), { embed })
  assert.equal(store.tier, 'local')
  assert.equal(store.table.url, embed.url)
  assert.equal(store.table.model, 'bge-small-zh-v1.5')
  assert.equal(store.table.key_env, EMBEDDINGS_KEY_ENV)
  assert.deepEqual(store.env, { [EMBEDDINGS_KEY_ENV]: 'fake-addon-key' })
  assert.ok(!JSON.stringify(store.table).includes('fake-addon-key'), 'the key is never part of the configuration')
  assert.match(path.basename(store.table.path), /^memories-local-bge-small-zh-v1\.5-[0-9a-f]{12}\.db$/)
  const otherBuild = resolveStore(RETRIEVAL, '/data', { embed: readEmbed(service({ fingerprint: 'bge-small-zh-v1.5|q8|ba9876543210' })) })
  assert.notEqual(otherBuild.table.path, store.table.path, 'another build of the same model gets its own index')
  const keyless = resolveStore(RETRIEVAL, '/data', { embed: readEmbed(service({ key: '' })) })
  assert.equal(keyless.table.key_env, undefined)
  assert.deepEqual(keyless.env, {})
})

test('calibration numbers go to the gateway only for a model it has no preset for', () => {
  const preset = resolveStore(RETRIEVAL, '/d', { embed: readEmbed(service({ calibration: { floor: 0.1, ceil: 0.2, query_prefix: 'q: ', document_prefix: 'd: ', parity: 'exact' } })) })
  for (const key of ['floor', 'ceil', 'query_prefix', 'document_prefix']) assert.equal(preset.table[key], undefined, key)
  const unknown = resolveStore(RETRIEVAL, '/d', { embed: readEmbed(service({ model: 'tiny-test-embedder', calibration: { floor: 0.3, ceil: 0.7, query_prefix: 'query: ', document_prefix: '', parity: 'approximate' } })) })
  assert.deepEqual([unknown.table.floor, unknown.table.ceil, unknown.table.query_prefix, unknown.table.document_prefix], [0.3, 0.7, 'query: ', ''])
  const inverted = resolveStore(RETRIEVAL, '/d', { embed: readEmbed(service({ model: 'tiny-test-embedder', calibration: { floor: 0.7, ceil: 0.3 } })) })
  assert.equal(inverted.table.floor, undefined, 'an unusable range is left to the gateway default')
  assert.equal(hasGatewayPreset('BAAI/BGE-M3'), true)
  assert.equal(hasGatewayPreset('nomic-embed-text'), false)
})

test('the key of the add-on offer changes with what a running gateway depends on', () => {
  const base = readEmbed(service())
  assert.equal(embedKey(null), null)
  assert.equal(embedKey(base), embedKey(readEmbed(service())))
  for (const overrides of [{ model: 'bge-m3' }, { url: 'http://127.0.0.1:1/v1' }, { key: 'other' }, { state: 'loading' }, { fingerprint: 'x' }]) {
    assert.notEqual(embedKey(readEmbed(service(overrides))), embedKey(base), JSON.stringify(overrides))
  }
  assert.equal(embedKey(readEmbed(service({ models: [] }))), embedKey(base), 'the model list is display only')
})

test('the preset list matches the bundled gateway', { skip: python ? false : 'no Python 3.11 or newer on PATH' }, () => {
  const code = 'import json, sys; sys.path.insert(0, sys.argv[1]); from feiyu_memgate.backends.embedding import PRESETS; print(json.dumps([p for p, _ in PRESETS]))'
  const result = spawnSync(python.executable, ['-I', '-B', '-c', code, PACKAGE_DIR], { env: scrubbedEnv(), encoding: 'utf8', windowsHide: true, timeout: 30000 })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(JSON.parse(result.stdout), [...GATEWAY_PRESETS])
})

// ------------------------------------------------------------------ hybrid

const HYBRID = { ...RETRIEVAL, hybrid: true, url: 'http://127.0.0.1:9/v1', model: 'Some/Model:Large', apiKey: 'fake-embeddings-key' }

test('a plain store description carries its file and is not hybrid', () => {
  const store = resolveStore({ ...HYBRID, tier: 'endpoint', hybrid: false }, path.join('/data'), {})
  assert.equal(store.hybrid, false)
  assert.equal(store.file, store.table.path)
  assert.equal(store.mirror, undefined)
  const keyword = resolveStore({ ...RETRIEVAL, tier: 'keyword' }, '/data', {})
  assert.equal(keyword.file, path.join('/data', 'memories.db'))
  assert.equal(keyword.hybrid, false)
  const missing = resolveStore({ ...RETRIEVAL, tier: 'endpoint', hybrid: true }, '/data', {})
  assert.deepEqual([missing.hybrid, missing.file, missing.table, missing.id], [false, null, null, null], 'nothing missing is a condition of hybrid')
})

test('hybrid on the endpoint tier: keyword child first, the vector table unchanged, same id and file', () => {
  const plain = resolveStore({ ...HYBRID, tier: 'endpoint', hybrid: false }, path.join('/data'), {})
  const hybrid = resolveStore({ ...HYBRID, tier: 'endpoint' }, path.join('/data'), {})
  assert.equal(hybrid.tier, 'endpoint')
  assert.equal(hybrid.hybrid, true)
  assert.equal(hybrid.id, plain.id, 'the ledger, the archive and the copy state are shared with the plain tier')
  assert.equal(hybrid.file, plain.file)
  assert.equal(hybrid.table.type, 'hybrid')
  assert.equal(hybrid.table.weights, undefined, 'the benchmarked default weights')
  assert.equal(hybrid.table.children.length, 2)
  const [keyword, vector] = hybrid.table.children
  assert.deepEqual(keyword, { type: 'sqlite', path: keywordIndexFile(path.join('/data'), plain.id) })
  assert.equal(path.basename(keyword.path), `memories-${plain.id}-keywords.db`)
  assert.deepEqual(vector, plain.table, 'the vector child is the plain table, key_env and all')
  assert.equal(vector.key_env, EMBEDDINGS_KEY_ENV)
  assert.deepEqual(hybrid.mirror, { keyword, vector: plain.file })
  assert.deepEqual(hybrid.env, plain.env)
  assert.ok(!JSON.stringify(hybrid.table).includes('fake-embeddings-key'))
})

test('hybrid on the local tier keeps the calibration and prefixes of the vector child', () => {
  const facts = { embed: readEmbed(service({ model: 'tiny-test-embedder', calibration: { floor: 0.3, ceil: 0.7, query_prefix: 'query: ', document_prefix: 'passage: ', parity: 'approximate' } })) }
  const plain = resolveStore({ ...RETRIEVAL, tier: 'local' }, '/data', facts)
  const hybrid = resolveStore({ ...RETRIEVAL, tier: 'local', hybrid: true }, '/data', facts)
  assert.equal(hybrid.hybrid, true)
  assert.equal(hybrid.id, plain.id)
  assert.deepEqual(hybrid.table.children[1], plain.table)
  assert.deepEqual([plain.table.floor, plain.table.ceil, plain.table.query_prefix, plain.table.document_prefix, plain.table.key_env], [0.3, 0.7, 'query: ', 'passage: ', EMBEDDINGS_KEY_ENV])
  assert.deepEqual(hybrid.env, { [EMBEDDINGS_KEY_ENV]: 'fake-addon-key' })
  assert.equal(hybrid.file, plain.table.path)
})

test('the keyword tier ignores the hybrid switch', () => {
  const plain = resolveStore({ ...RETRIEVAL, tier: 'keyword' }, '/data', {})
  const hybrid = resolveStore({ ...RETRIEVAL, tier: 'keyword', hybrid: true }, '/data', {})
  assert.deepEqual(hybrid, plain)
  assert.equal(hybrid.hybrid, false)
  // auto without a ready add-on resolves to keyword: the switch does nothing there either.
  assert.equal(resolveStore({ ...RETRIEVAL, hybrid: true }, '/data', {}).hybrid, false)
})

test('auto follows the add-on: hybrid while it resolves to local, plain when it falls back to keyword', () => {
  const ready = { embed: readEmbed(service()) }
  const on = resolveStore({ ...RETRIEVAL, hybrid: true }, '/data', ready)
  assert.equal(on.tier, 'local')
  assert.equal(on.hybrid, true)
  assert.equal(on.table.children[1].type, 'embedding')
  const gone = resolveStore({ ...RETRIEVAL, hybrid: true }, '/data', { embed: readEmbed(service({ state: 'loading' })) })
  assert.equal(gone.tier, 'keyword')
  assert.equal(gone.hybrid, false)
  assert.equal(gone.table.type, 'sqlite')
  // The add-on's model decides the id, as without hybrid.
  assert.equal(on.id, resolveStore(RETRIEVAL, '/data', ready).id)
})
