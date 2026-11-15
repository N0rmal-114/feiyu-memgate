// The state file: updates persist, damaged or foreign content is dropped, writes leave no temporary file.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { after, describe, it } from 'node:test'
import { STATE_FILE, createStateStore, emptyState, sanitizeState } from '../src/state.js'

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'fmg-embed-state-'))
after(() => fs.rmSync(root, { recursive: true, force: true }))

describe('state file', () => {
  it('reads an empty state when there is no file', () => {
    const store = createStateStore(path.join(root, 'none'))
    assert.deepEqual(store.read(), emptyState())
  })

  it('persists updates, one after the other', async () => {
    const dir = path.join(root, 'a')
    const store = createStateStore(dir)
    await Promise.all([
      store.update((s) => {
        s.active = 'bge-m3'
      }),
      store.update((s) => {
        s.origin = 'hf-mirror'
      }),
      store.update((s) => {
        s.acceptances['licensed-model'] = { name: 'Example Terms', url: 'https://example.com/terms', acceptedAt: '2026-11-15T00:00:00.000Z' }
      }),
    ])
    const again = createStateStore(dir).read()
    assert.equal(again.active, 'bge-m3')
    assert.equal(again.origin, 'hf-mirror')
    assert.equal(again.acceptances['licensed-model'].acceptedAt, '2026-11-15T00:00:00.000Z')
    assert.deepEqual(fs.readdirSync(dir), [STATE_FILE])
  })

  it('drops malformed and unknown parts', () => {
    const s = sanitizeState({
      version: 9, active: 3, origin: '', key: 'secret', installed: { a: { sha256: 'x', sizeBytes: 1.5, mtimeMs: 1 }, b: { sha256: 'y', sizeBytes: 2, mtimeMs: 3 } },
      acceptances: { c: { name: 'n' } },
    })
    assert.deepEqual(s, { version: 1, active: null, origin: null, installed: { b: { sha256: 'y', sizeBytes: 2, mtimeMs: 3, verifiedAt: null } }, acceptances: {} })
  })

  it('survives a damaged file', () => {
    const dir = path.join(root, 'b')
    fs.mkdirSync(dir)
    fs.writeFileSync(path.join(dir, STATE_FILE), '{not json')
    assert.deepEqual(createStateStore(dir).read(), emptyState())
  })
})
