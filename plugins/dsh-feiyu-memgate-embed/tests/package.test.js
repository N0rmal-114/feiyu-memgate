// The published package: manifest, bundle patch, locale and detail files, the entry's exports, the
// dependency pin, and a scan of every text file for local paths, private data and vendor names.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'
import { MODELS } from '../src/models.js'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8')
const pkg = JSON.parse(read('package.json'))

// Just enough of semver to check engines.dsh the way the host does (includePrerelease,
// packages/boot/app-boot/src/plugin-compatibility.ts:51-53) and the way npm does by default (a
// prerelease matches only a comparator of the same version that has a prerelease too).
function parse(version) {
  const m = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/.exec(version)
  assert.ok(m, `not a version: ${version}`)
  return { nums: [Number(m[1]), Number(m[2]), Number(m[3])], pre: m[4] ? m[4].split('.') : [] }
}
function compare(a, b) {
  for (let i = 0; i < 3; i++) if (a.nums[i] !== b.nums[i]) return a.nums[i] - b.nums[i]
  if (!a.pre.length || !b.pre.length) return (a.pre.length ? -1 : 0) + (b.pre.length ? 1 : 0)
  for (let i = 0; i < Math.max(a.pre.length, b.pre.length); i++) {
    const x = a.pre[i]
    const y = b.pre[i]
    if (x === undefined) return -1
    if (y === undefined) return 1
    const nx = /^\d+$/.test(x)
    const ny = /^\d+$/.test(y)
    if (nx && ny && Number(x) !== Number(y)) return Number(x) - Number(y)
    if (nx !== ny) return nx ? -1 : 1
    if (x !== y) return x < y ? -1 : 1
  }
  return 0
}
function satisfies(version, range, includePrerelease) {
  const v = parse(version)
  return range.split('||').some((branch) => {
    const comparators = branch.trim().split(/\s+/).map((c) => /^(>=|<=|>|<|=|\^)?(.+)$/.exec(c))
    const ok = comparators.every(([, op = '=', target]) => {
      const t = parse(target)
      const c = compare(v, t)
      if (op === '^') return c >= 0 && v.nums[0] === t.nums[0]
      return { '>=': c >= 0, '<=': c <= 0, '>': c > 0, '<': c < 0, '=': c === 0 }[op]
    })
    if (!ok || includePrerelease || !v.pre.length) return ok
    return comparators.some(([, , target]) => {
      const t = parse(target)
      return t.pre.length > 0 && t.nums.join('.') === v.nums.join('.')
    })
  })
}

describe('package', () => {
  it('has the manifest fields the host and npm read', () => {
    assert.equal(pkg.name, 'dsh-feiyu-memgate-embed')
    assert.equal(pkg.version, '0.1.0')
    assert.equal(pkg.type, 'module')
    assert.equal(pkg.main, './index.js')
    assert.equal(pkg.license, 'MIT')
    assert.equal(pkg.author, 'N0rmal')
    assert.equal(pkg.repository.url, 'git+https://github.com/N0rmal-114/feiyu-memgate.git')
    // The community list links a monorepo subpackage through this field.
    assert.equal(pkg.repository.directory, 'plugins/dsh-feiyu-memgate-embed')
    assert.equal(pkg.homepage, 'https://github.com/N0rmal-114/feiyu-memgate/tree/main/plugins/dsh-feiyu-memgate-embed')
    assert.equal(pkg.bugs.url, 'https://github.com/N0rmal-114/feiyu-memgate/issues')
    assert.ok(pkg.keywords.includes('dsh-plugin'))
    assert.equal(pkg.scripts.test, 'node --test')
    assert.equal(pkg.icon, 'icon.png')
    assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml')
    for (const key of ['.', './package.json', './cordis.patch.yml', './locale/*.json']) assert.ok(pkg.exports[key], key)
    assert.equal(pkg.engines.node, '>=22.19')
  })

  it('pins node-llama-cpp exactly and imports nothing else from outside', () => {
    assert.deepEqual(pkg.dependencies, { 'node-llama-cpp': '3.22.3' })
    const sources = ['index.js', ...fs.readdirSync(path.join(ROOT, 'src')).map((f) => 'src/' + f)]
    const external = new Set()
    for (const rel of sources) {
      for (const m of read(rel).matchAll(/(?:from\s+|import\(\s*)'([^']+)'/g)) {
        if (!m[1].startsWith('.') && !m[1].startsWith('node:')) external.add(m[1])
      }
    }
    assert.deepEqual([...external].sort(), ['@deepseek-ai/schemastery', 'node-llama-cpp'])
    // Both are loaded lazily, so the module loads without them (tests, a failed native install).
    for (const rel of sources) assert.ok(!/from\s+'(@deepseek-ai\/|node-llama-cpp)/.test(read(rel)), rel)
    assert.match(read('src/worker.js'), /await import\('node-llama-cpp'\)/)
  })

  it('declares the host package it imports as a peer, satisfied by the installed 3.19.0', () => {
    assert.deepEqual(Object.keys(pkg.peerDependencies), ['@deepseek-ai/schemastery'])
    assert.ok(satisfies('3.19.0', pkg.peerDependencies['@deepseek-ai/schemastery'], false))
  })

  it('engines.dsh covers the 0.2.2 line from rc.1 on, also for npm without includePrerelease', () => {
    for (const version of ['0.2.2-rc.1', '0.2.2-rc.2', '0.2.2-rc.3', '0.2.2-rc.4', '0.2.2']) {
      assert.ok(satisfies(version, pkg.engines.dsh, true), `${version} (host check)`)
      assert.ok(satisfies(version, pkg.engines.dsh, false), `${version} (npm default)`)
    }
    for (const version of ['0.2.1', '0.2.2-alpha.1', '0.2.2-rc.0', '0.2.3-rc.1', '0.2.3', '0.3.0']) {
      assert.ok(!satisfies(version, pkg.engines.dsh, true), `${version} must be outside`)
    }
  })

  it('the bundle patch inserts one row named like the package, with default settings', () => {
    const patch = read('cordis.patch.yml')
    assert.deepEqual([...patch.matchAll(/^\s+name:\s*(\S+)\s*$/gm)].map((m) => m[1]), [pkg.name])
    assert.match(patch, /^- insert:/m)
    assert.match(patch, /^\s+- id: feiyu-memgate-embed$/m)
    assert.match(patch, /^\s+config: \{\}$/m)
  })

  it('every path in files exists and nothing of the tests is published', () => {
    for (const entry of pkg.files) assert.ok(fs.existsSync(path.join(ROOT, entry)), entry)
    assert.ok(!pkg.files.some((f) => f.startsWith('tests')))
    for (const doc of ['docs/DETAIL.md', 'docs/DETAIL.en.md', 'docs/SERVICE.md']) assert.ok(fs.existsSync(path.join(ROOT, doc)), doc)
    assert.ok(fs.statSync(path.join(ROOT, pkg.icon)).size <= 256 * 1024, 'the icon is at most 256 KiB')
  })

  it('locale files: a title, one factual sentence and the detail document of their language', () => {
    const detail = { en: 'docs/DETAIL.en.md', zh: 'docs/DETAIL.md' }
    for (const lang of ['en', 'zh']) {
      const { meta } = JSON.parse(read(`locale/${lang}.json`))
      assert.ok(meta.title.trim(), `${lang} title`)
      assert.match(meta.description, /feiyu-memgate/)
      assert.match(meta.description, /[.。]$/)
      assert.equal(meta.detail, detail[lang])
      assert.ok(fs.existsSync(path.join(ROOT, meta.detail)), meta.detail)
    }
    assert.match(read('docs/DETAIL.md'), /[一-鿿]/)
  })

  it('the documents name every model, why embeddinggemma is not offered, and the tier comparison', () => {
    for (const rel of ['README.md', 'README.zh.md', 'docs/DETAIL.md', 'docs/DETAIL.en.md']) {
      const text = read(rel)
      for (const m of MODELS) {
        assert.ok(text.includes(`\`${m.id}\``), `${rel}: ${m.id}`)
        assert.ok(text.includes(m.file), `${rel}: ${m.file}`)
      }
      assert.match(text, /embeddinggemma[\s\S]{0,400}Dense/, `${rel}: why embeddinggemma is not offered`)
      assert.equal(text.includes('--accept-gemma-terms'), false, `${rel}: the old acceptance flag`)
      assert.equal(text.includes('`embeddinggemma-300m`'), false, `${rel}: embeddinggemma offered as a model id`)
      assert.ok(text.includes('https://github.com/N0rmal-114/feiyu-memgate#部署档位'), `${rel}: tier link`)
      assert.match(text, /macOS/, `${rel}: macOS in the not-tested list`)
    }
    const service = read('docs/SERVICE.md')
    assert.match(service, /feiyuMemgateEmbed/)
    assert.match(service, /embeddinggemma[\s\S]{0,400}Dense/)
  })

  it('the entry exports name, inject, Config and apply, and no default export', async () => {
    const entry = await import('../index.js')
    assert.deepEqual(Object.keys(entry).sort(), ['Config', 'apply', 'inject', 'name'])
    assert.equal(entry.name, pkg.name)
    assert.deepEqual(entry.inject, [])
    assert.equal(typeof entry.apply, 'function')
    assert.equal(entry.Config, undefined, 'without the host schema library the add-on loads with defaults')
  })
})

describe('repository text', () => {
  const files = []
  const walk = (dir) => {
    for (const entry of fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true })) {
      const rel = dir ? `${dir}/${entry.name}` : entry.name
      if (entry.isDirectory()) {
        if (!['.git', 'node_modules', '__pycache__'].includes(entry.name)) walk(rel)
      } else if (!/\.(png|jpg|gguf|tgz)$/i.test(entry.name)) {
        files.push(rel)
      }
    }
  }
  walk('')

  it('holds no local paths, e-mail addresses or vendor names', () => {
    assert.ok(files.length > 20)
    // Assembled from pieces so this file does not contain the words it looks for.
    const vendors = new RegExp(['cl' + 'aude', 'anth' + 'ropic', 'co' + 'dex', 'g' + 'pt', 'cop' + 'ilot', 'gem' + 'ini', 'ai-' + 'generated'].join('|'), 'i')
    // The one allowed use: the technical term for the embeddings API shape, in English or Chinese.
    const vendorPlain = new RegExp('open' + 'ai(?!-compatible| 兼容)', 'i')
    const localPath = /\b[A-Za-z]:[\\/](?!\/)|\/Users\/|\/home\/|\/(?:c|d|e)\/(?:workspace|Users)\b/
    // `.example` is the reserved test domain (a refused user:password origin in the plugin tests).
    const email = /[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.(?!example\b)[A-Za-z]{2,}\b/
    for (const rel of files) {
      const text = read(rel)
      assert.ok(!vendors.test(text) && !vendorPlain.test(text), `${rel}: vendor name`)
      assert.ok(!localPath.test(text), `${rel}: local path`)
      assert.ok(!email.test(text), `${rel}: e-mail address`)
    }
  })

  it('test keys are generated, never written into the code', () => {
    for (const rel of files) {
      const text = read(rel)
      assert.ok(!/\b(?:sk|hf)_[A-Za-z0-9]{20,}/.test(text), `${rel}: token-shaped string`)
      assert.ok(!/Bearer [A-Za-z0-9_-]{30,}/.test(text), `${rel}: literal bearer key`)
    }
  })
})
