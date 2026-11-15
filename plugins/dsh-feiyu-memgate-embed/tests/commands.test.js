// Command parsing and every refusal, independent of the add-on.
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { ACCEPT_FLAG, COMMAND_NAME, createCommandDefinition, formatList, formatSize, helpText, parseCommand } from '../src/commands.js'
import { MODELS } from '../src/models.js'

// No shipped model needs a licence acceptance or is unverified; a fixture registry keeps both paths
// tested for a model that may be added later.
const LICENSED = Object.freeze({
  id: 'licensed-model', status: 'unverified', requiresAcceptance: Object.freeze({ name: 'Example Terms', url: 'https://example.com/terms' }),
})
const FIXTURE = Object.freeze([...MODELS, LICENSED])

describe('parseCommand', () => {
  const ok = [
    ['', { action: 'status' }],
    ['   ', { action: 'status' }],
    [' status', { action: 'status' }],
    [' STATUS', { action: 'status' }],
    [' list', { action: 'list', all: false }],
    [' list --all', { action: 'list', all: true }],
    [' add bge-small-zh-v1.5', { action: 'add', id: 'bge-small-zh-v1.5', accept: false }],
    [' add BGE-M3', { action: 'add', id: 'bge-m3', accept: false }],
    [' use qwen3-embedding-0.6b', { action: 'use', id: 'qwen3-embedding-0.6b' }],
    [' use none', { action: 'use', id: null }],
    [' remove bge-base-zh-v1.5', { action: 'remove', id: 'bge-base-zh-v1.5' }],
    [' cancel', { action: 'cancel' }],
    [' origin', { action: 'origin' }],
    [' origin https://mirror.example', { action: 'origin', value: 'https://mirror.example' }],
    ['\thelp', { action: 'help' }],
  ]
  for (const [input, expected] of ok) {
    it(`parses ${JSON.stringify(input)}`, () => assert.deepEqual(parseCommand(input), expected))
  }

  const refused = [
    [' frobnicate', 'unknown-action'],
    [' status now', 'unexpected-argument'],
    [' status --verbose', 'unknown-option'],
    [' list --all --all', 'unknown-option'],
    [' list all', 'unexpected-argument'],
    [' add', 'missing-model'],
    [' add nomic-embed-text', 'unknown-model'],
    [' add bge-m3 bge-small-zh-v1.5', 'unexpected-argument'],
    [` add bge-m3 ${ACCEPT_FLAG}`, 'option-not-applicable'],
    [' add bge-m3 --force', 'unknown-option'],
    [' add embeddinggemma-300m', 'unknown-model'],
    [' add https://huggingface.co/x/y/resolve/main/z.gguf', 'unknown-model'],
    [' add ../models/x', 'unknown-model'],
    [' use', 'missing-model'],
    [' use bge-m3 now', 'unexpected-argument'],
    [' remove none', 'unknown-model'],
    [' cancel bge-m3', 'unexpected-argument'],
    [' origin a b', 'unexpected-argument'],
    [' origin --auto', 'unknown-option'],
    [` ${'a'.repeat(401)}`, 'too-long'],
  ]
  for (const [input, code] of refused) {
    it(`refuses ${JSON.stringify(input.slice(0, 50))} (${code})`, () => assert.equal(parseCommand(input).error, code))
  }

  it('accepts the licence flag only for a registry model under licence terms', () => {
    assert.deepEqual(parseCommand(` add licensed-model ${ACCEPT_FLAG}`, FIXTURE), { action: 'add', id: 'licensed-model', accept: true })
    assert.deepEqual(parseCommand(` add ${ACCEPT_FLAG} licensed-model`, FIXTURE), { action: 'add', id: 'licensed-model', accept: true })
    assert.deepEqual(parseCommand(' add licensed-model', FIXTURE), { action: 'add', id: 'licensed-model', accept: false })
    assert.equal(parseCommand(` add licensed-model ${ACCEPT_FLAG} ${ACCEPT_FLAG}`, FIXTURE).error, 'unknown-option')
    assert.equal(parseCommand(` add bge-m3 ${ACCEPT_FLAG}`, FIXTURE).error, 'option-not-applicable')
    assert.equal(parseCommand(' add licensed-model').error, 'unknown-model')
  })
})

describe('help and list texts', () => {
  it('offers --all and the licence flag only when a registry model needs them', () => {
    for (const lang of ['en', 'zh']) {
      const shipped = helpText(lang)
      assert.equal(shipped.includes('--all'), false)
      assert.equal(shipped.includes(ACCEPT_FLAG), false)
      const fixture = helpText(lang, FIXTURE)
      assert.ok(fixture.includes('list [--all]'))
      assert.ok(fixture.includes(`add <id> [${ACCEPT_FLAG}]`))
      assert.equal(/\{(all|accept)\}/.test(shipped + fixture), false)
    }
  })

  it('hints at --all only when the default list hides models', () => {
    const rows = [{ id: 'bge-m3', sizeBytes: 1, dimensions: 1024, license: { name: 'MIT' }, parity: 'exact', status: 'verified' }]
    assert.equal(formatList('en', rows).includes('--all'), false)
    assert.ok(formatList('en', rows, { hidden: 1 }).includes('--all'))
    assert.equal(formatList('en', rows, { all: true, hidden: 1 }).includes('--all'), false)
  })
})

describe('command definition', () => {
  it('matches the host rules for names and results', async () => {
    const calls = []
    const api = {
      status: () => ({ engineOn: true, state: 'no-model', model: null, origin: { value: 'auto', pinned: false }, modelsDir: '~/.dsh/data/feiyu-memgate/models', download: null, lastDownload: null }),
      list: () => [],
      add: async (id, o) => {
        calls.push(['add', id, o])
        return { ok: false, code: 'busy', current: { id: 'bge-m3', state: 'downloading', bytes: 1e6, total: 2e6, speed: 5e5, source: 'https://huggingface.co' } }
      },
      use: async () => ({ ok: true, id: null }),
      remove: async () => ({ ok: false, code: 'active', id: 'bge-m3' }),
      cancel: () => ({ ok: false }),
      origin: async () => {
        throw new Error('boom')
      },
    }
    const def = createCommandDefinition(api, () => 'en')
    assert.equal(def.name, COMMAND_NAME)
    assert.match(def.name, /^[a-z][a-z0-9_-]*$/u)
    assert.ok(def.description.trim())
    assert.ok(def.input.hint.trim())
    const run = (rawInput) => def.handler({ rawInput, signal: new AbortController().signal, attachments: [] })
    assert.deepEqual(Object.keys(await run('')), ['kind', 'text'])
    const busy = await run(' add bge-small-zh-v1.5')
    assert.equal(busy.kind, 'error')
    assert.match(busy.text, /bge-m3: 1\.0 MB \/ 2\.0 MB \(50%\), 500 kB\/s/)
    assert.deepEqual(calls, [['add', 'bge-small-zh-v1.5', { accept: false }]])
    assert.equal((await run(' use none')).kind, 'success')
    assert.equal((await run(' remove bge-m3')).kind, 'error')
    const failed = await run(' origin hf-mirror')
    assert.equal(failed.kind, 'error')
    assert.equal(failed.text.includes('boom'), false)
    const bad = await run(' add <script>\u0007')
    assert.equal(bad.kind, 'error')
    assert.equal(bad.text.includes('\u0007'), false)
  })

  it('formats sizes for any reader', () => {
    assert.equal(formatSize(47886240), '47.9 MB')
    assert.equal(formatSize(1157671200), '1.16 GB')
    assert.equal(formatSize(512), '512 B')
  })
})
