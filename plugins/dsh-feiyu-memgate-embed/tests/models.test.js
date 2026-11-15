// Registry integrity, and the calibration numbers against the gateway's own table.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { describe, it } from 'node:test'
import { DEFAULT_SUGGESTION, MODELS, fileUrl, fingerprint, getModel, listedModels } from '../src/models.js'

// The gateway's preset order (backends/embedding.py, PRESETS): the first pattern contained in the
// lower-cased model name wins.
const PRESET_ORDER = ['bge-m3', 'bge-small-zh', 'bge-base-zh', 'embeddinggemma', 'qwen3-embedding']
const presetFor = (name) => PRESET_ORDER.find((p) => name.toLowerCase().includes(p))

describe('model registry', () => {
  it('has four tiers with unique ids, and no embeddinggemma entry', () => {
    // The middle tier is left out: the embeddinggemma GGUF checked lacks the model's Dense layers,
    // so its vectors do not match the gateway's calibration (src/models.js).
    assert.deepEqual(MODELS.map((m) => m.id), ['bge-small-zh-v1.5', 'bge-base-zh-v1.5', 'qwen3-embedding-0.6b', 'bge-m3'])
    assert.equal(new Set(MODELS.map((m) => m.id)).size, MODELS.length)
    assert.equal(getModel(DEFAULT_SUGGESTION).id, 'bge-small-zh-v1.5')
    assert.equal(getModel('embeddinggemma-300m'), undefined)
    assert.ok(MODELS.every((m) => m.preset !== 'embeddinggemma' && !m.repository.includes('embeddinggemma')))
  })

  it('each id selects its own preset in the gateway', () => {
    for (const m of MODELS) {
      assert.equal(presetFor(m.id), m.preset, m.id)
      assert.match(m.id, /^[a-z0-9][a-z0-9._-]*$/)
    }
  })

  it('pins every file: commit hash, file name, size, sha256', () => {
    for (const m of MODELS) {
      assert.match(m.repository, /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/, m.id)
      assert.match(m.revision, /^[0-9a-f]{40}$/, m.id)
      assert.match(m.sha256, /^[0-9a-f]{64}$/, m.id)
      assert.match(m.file, /^[A-Za-z0-9_.-]+\.gguf$/, m.id)
      assert.ok(Number.isSafeInteger(m.sizeBytes) && m.sizeBytes > 1e6, m.id)
      assert.ok(Number.isInteger(m.dimensions) && m.dimensions > 0, m.id)
      assert.ok(Number.isInteger(m.contextTokens) && m.contextTokens >= 512 && m.contextTokens <= 8192, m.id)
      // the limits the calibrated Ollama setup was measured to embed (src/models.js)
      const measured = { 'bge-small-zh-v1.5': 512, 'bge-base-zh-v1.5': 512, 'qwen3-embedding-0.6b': 4095, 'bge-m3': 2048 }
      if (measured[m.id]) assert.equal(m.contextTokens, measured[m.id], m.id)
      assert.ok(typeof m.title === 'string' && m.title.trim(), m.id)
      if (m.status === 'verified') assert.ok(m.memoryMB.loaded > 0 && m.memoryMB.peak >= m.memoryMB.loaded, m.id)
      else assert.equal(m.memoryMB, null, m.id)
      assert.ok(['exact', 'approximate'].includes(m.parity), m.id)
      assert.ok(['verified', 'unverified'].includes(m.status), m.id)
      assert.ok(m.license.name && /^https:\/\//.test(m.license.url), m.id)
    }
  })

  it('builds revision-pinned URLs on any origin, never from user input', () => {
    const m = getModel('bge-small-zh-v1.5')
    assert.equal(fileUrl(m, 'https://huggingface.co'),
      'https://huggingface.co/CompendiumLabs/bge-small-zh-v1.5-gguf/resolve/5bf683e6a1bd454bbb60ba051088c50731d63fcb/bge-small-zh-v1.5-f16.gguf')
    assert.equal(fileUrl(m, 'https://hf-mirror.com/'), fileUrl(m, 'https://hf-mirror.com'))
    assert.ok(fileUrl(m, 'https://hf-mirror.com').startsWith('https://hf-mirror.com/CompendiumLabs/'))
    assert.ok(fileUrl(m, 'http://127.0.0.1:9/').startsWith('http://127.0.0.1:9/CompendiumLabs/'))
  })

  it('ships only verified, exactly calibrated models under open licences', () => {
    for (const m of MODELS) {
      assert.equal(m.requiresAcceptance, undefined, m.id)
      assert.equal(m.status, 'verified', m.id)
      assert.equal(m.parity, 'exact', m.id)
    }
  })

  it('fingerprints carry id, quantisation and the hash prefix', () => {
    assert.equal(fingerprint(getModel('bge-m3')), 'bge-m3|f16|daec91ffb5dd')
    assert.equal(new Set(MODELS.map(fingerprint)).size, MODELS.length)
  })

  it('lists only verified models by default', () => {
    assert.deepEqual(listedModels().map((m) => m.id), MODELS.filter((m) => m.status === 'verified').map((m) => m.id))
    assert.equal(listedModels({ all: true }).length, MODELS.length)
  })

  it('is frozen', () => {
    assert.throws(() => {
      MODELS[0].sizeBytes = 1
    })
    assert.throws(() => {
      MODELS[0].calibration.floor = 0
    })
  })

  it('has the calibration of the gateway checkout (FEIYU_MEMGATE_SRC)', { skip: !process.env.FEIYU_MEMGATE_SRC && 'FEIYU_MEMGATE_SRC is not set' }, () => {
    const root = process.env.FEIYU_MEMGATE_SRC
    const src = fs.existsSync(path.join(root, 'src', 'feiyu_memgate')) ? path.join(root, 'src') : root
    const script = [
      'import json, sys',
      'sys.path.insert(0, sys.argv[1])',
      'from feiyu_memgate.backends.embedding import PRESETS',
      'print(json.dumps([[p, v] for p, v in PRESETS]))',
    ].join('\n')
    const python = process.platform === 'win32' ? 'python' : 'python3'
    const out = execFileSync(python, ['-I', '-c', script, src], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } })
    const presets = JSON.parse(out.trim().split(/\r?\n/).pop())
    assert.deepEqual(presets.map(([p]) => p), PRESET_ORDER, 'preset order changed in the gateway')
    for (const m of MODELS) {
      const [, values] = presets.find(([p]) => p === m.preset)
      assert.deepEqual({ ...m.calibration }, values, m.id)
    }
  })
})
