// The published package: manifest, bundle patch, locale files, the bundled gateway copy and the
// plugin entry's exports.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'))
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8')

// Just enough of semver to check engines.dsh the way the host does (includePrerelease, see
// packages/boot/app-boot/src/plugin-compatibility.ts:51-53) and the way npm does by default (a
// prerelease only matches a comparator of the same version that has a prerelease too).
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
    const comparators = branch.trim().split(/\s+/).map((c) => /^(>=|<=|>|<|=)?(.+)$/.exec(c))
    const ok = comparators.every(([, op = '=', target]) => {
      const c = compare(v, parse(target))
      return { '>=': c >= 0, '<=': c <= 0, '>': c > 0, '<': c < 0, '=': c === 0 }[op]
    })
    if (!ok || includePrerelease || !v.pre.length) return ok
    return comparators.some(([, , target]) => {
      const t = parse(target)
      return t.pre.length > 0 && t.nums.join('.') === v.nums.join('.')
    })
  })
}

test('manifest fields', () => {
  assert.equal(pkg.name, 'dsh-feiyu-memgate')
  assert.equal(pkg.version, '0.3.0')
  assert.equal(pkg.type, 'module')
  assert.equal(pkg.license, 'MIT')
  assert.equal(pkg.author, 'N0rmal')
  assert.equal(pkg.repository.url, 'git+https://github.com/N0rmal-114/feiyu-memgate.git')
  // The community list links a monorepo subpackage through this field.
  assert.equal(pkg.repository.directory, 'plugins/dsh-feiyu-memgate')
  assert.equal(pkg.homepage, 'https://github.com/N0rmal-114/feiyu-memgate/tree/main/plugins/dsh-feiyu-memgate')
  assert.ok(pkg.keywords.includes('dsh-plugin'))
  assert.equal(pkg.scripts.test, 'node --test')
  assert.equal(pkg.icon, 'icon.png')
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml')
  assert.deepEqual(Object.keys(pkg.dependencies ?? {}), [], 'no runtime dependency: the host provides everything')
  assert.deepEqual(Object.keys(pkg.devDependencies ?? {}), [], 'no build tool: the browser half is a plain script')
  for (const key of ['.', './client', './package.json', './cordis.patch.yml', './locale/*.json']) assert.ok(pkg.exports[key], key)
  assert.equal(pkg.scripts['sync-client'], 'node scripts/sync-client.mjs')
  assert.equal(pkg.scripts['sync-catalogue'], 'node scripts/sync-catalogue.mjs')
})

test('the browser half is declared the way the client module system reads it', () => {
  // packages/client/modules/src/client/manifest.ts:186 (dsh.client.inject), README "Declaring a client plugin".
  assert.equal(pkg.dsh.client.platform, 'web')
  assert.deepEqual(pkg.dsh.client.inject, ['@deepseek-ai/dsh-client-locale', '@deepseek-ai/dsh-client-ui-plugin-manager'])
  assert.equal(pkg.dsh.client.external, undefined, 'only modules of the shared table are requested')
  assert.equal(pkg.exports['./client'], './client/client.js')
  assert.ok(pkg.files.includes('client/'))
  assert.ok(fs.existsSync(path.join(ROOT, 'client', 'client.js')))
})

test('engines.dsh and the host peers cover 0.2.0 to any 0.2.2 release, prereleases included', () => {
  const inside = ['0.2.0-rc.1', '0.2.0-rc.2', '0.2.0', '0.2.1-alpha.1', '0.2.1-rc.5', '0.2.1', '0.2.2-alpha.1', '0.2.2-rc.1', '0.2.2-rc.3', '0.2.2-rc.9', '0.2.2', '0.2.2-beta.2']
  const outside = ['0.1.9', '0.2.0-alpha.1', '0.2.3-alpha.1', '0.2.3-0', '0.2.3', '0.3.0']
  for (const range of [pkg.engines.dsh, pkg.peerDependencies['@deepseek-ai/dsh-llm']]) {
    for (const version of inside) {
      assert.ok(satisfies(version, range, true), `${version} (host check)`)
      assert.ok(satisfies(version, range, false), `${version} (npm default: needs an explicit prerelease branch)`)
    }
    for (const version of outside) assert.ok(!satisfies(version, range, true), `${version} must be outside`)
  }
  assert.ok(satisfies('3.19.0', pkg.peerDependencies['@deepseek-ai/schemastery'].replace('^', '>=') + ' <4.0.0', false))
  assert.equal(pkg.peerDependenciesMeta['@deepseek-ai/dsh-llm'].optional, true)
})

test('peers are exactly the host packages the code imports', () => {
  const sources = ['index.js', ...fs.readdirSync(path.join(ROOT, 'src')).map((f) => 'src/' + f)].map(read).join('\n')
  const imported = new Set([...sources.matchAll(/import\(\s*'(@deepseek-ai\/[^']+)'\s*\)|from\s+'(@deepseek-ai\/[^']+)'/g)].map((m) => m[1] ?? m[2]))
  assert.deepEqual([...imported].sort(), Object.keys(pkg.peerDependencies).sort())
  assert.ok(!/from\s+'@deepseek-ai\//.test(sources), 'host packages are imported lazily, never statically')
})

test('the bundle patch inserts one row named like the package', () => {
  const patch = read('cordis.patch.yml')
  const names = [...patch.matchAll(/^\s+name:\s*(\S+)\s*$/gm)].map((m) => m[1])
  assert.deepEqual(names, [pkg.name])
  assert.match(patch, /^- insert:/m)
  assert.match(patch, /^\s+- id: feiyu-memgate$/m)
})

test('every path in files exists', (t) => {
  for (const entry of pkg.files) {
    if (entry === 'docs/' && !fs.existsSync(path.join(ROOT, 'docs'))) {
      t.diagnostic('docs/ is written separately and is not there yet')
      continue
    }
    assert.ok(fs.existsSync(path.join(ROOT, entry)), entry)
  }
  assert.ok(pkg.files.indexOf('docs/') === pkg.files.indexOf('locale/') + 1, 'docs comes right after the locale resources')
  assert.ok(fs.statSync(path.join(ROOT, pkg.icon)).size <= 256 * 1024, 'the icon is at most 256 KiB')
})

test('locale files: a title, one factual sentence and the detail document in its own language', () => {
  const details = { en: 'docs/DETAIL.en.md', zh: 'docs/DETAIL.md' }
  for (const lang of ['en', 'zh']) {
    const { meta } = JSON.parse(read(`locale/${lang}.json`))
    assert.ok(meta.title.trim(), `${lang} title`)
    assert.match(meta.description, /feiyu-memgate/)
    assert.match(meta.description, /DeepSeek Harness/)
    assert.match(meta.description, /[.。]$/)
    assert.equal(meta.detail, details[lang])
    // The host's detail reader (packages/util/plugin-detail, document.ts detailPath and
    // readPackageDocument): a `/`-separated path inside the package naming a Markdown file, no empty,
    // `.` or `..` segment, no backslash or scheme; a named file that is missing fails the page instead
    // of falling back to the README. The document is read up to 1 MiB (plugin-manager detailMaxBytes),
    // without a byte order mark or front matter, and the package publishes it.
    assert.ok(!meta.detail.includes('\\') && !/^[A-Za-z][A-Za-z\d+.-]*:/.test(meta.detail), meta.detail)
    assert.ok(meta.detail.split('/').every((s) => s !== '' && s !== '.' && s !== '..'), meta.detail)
    assert.match(meta.detail, /\.(md|markdown)$/i)
    const file = path.join(ROOT, ...meta.detail.split('/'))
    assert.ok(fs.existsSync(file), `${meta.detail} exists`)
    const bytes = fs.readFileSync(file)
    assert.ok(bytes.length > 0 && bytes.length <= 1024 * 1024, `${meta.detail} size`)
    assert.ok(!(bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf), 'no byte order mark')
    assert.ok(!bytes.toString('utf8').startsWith('---'), 'no front matter')
    assert.ok(pkg.files.includes(meta.detail.split('/')[0] + '/'), 'the package publishes the folder')
  }
  // Both detail pages carry the real-host summary, not a placeholder.
  for (const file of ['README.md', 'README.zh.md', 'docs/DETAIL.md', 'docs/DETAIL.en.md']) {
    assert.ok(!read(file).includes('REAL-HOST-TEST'), `${file}: placeholder left`)
  }
  assert.match(read('docs/DETAIL.en.md'), /Tested in the installed DeepSeek Harness 0\.2\.2-rc\.1 on Windows 11/)
})

test('the python copy matches GATEWAY_VERSION.json', () => {
  const record = JSON.parse(read('python/GATEWAY_VERSION.json'))
  assert.equal(record.package, 'feiyu-memgate')
  assert.match(record.version, /^\d+\.\d+\.\d+/)
  assert.match(record.commit, /^[0-9a-f]{40}$/)
  assert.equal(record.repository, 'https://github.com/N0rmal-114/feiyu-memgate')
  const onDisk = []
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        if (entry.name !== '__pycache__') walk(full)
      } else if (!/\.py[co]$/.test(entry.name) && entry.name !== 'GATEWAY_VERSION.json') {
        onDisk.push(path.relative(path.join(ROOT, 'python'), full).split(path.sep).join('/'))
      }
    }
  }
  walk(path.join(ROOT, 'python'))
  assert.deepEqual(onDisk.sort(), Object.keys(record.files).sort(), 'no file added or missing')
  for (const [rel, sha] of Object.entries(record.files)) {
    const actual = createHash('sha256').update(fs.readFileSync(path.join(ROOT, 'python', ...rel.split('/')))).digest('hex')
    assert.equal(actual, sha, rel)
  }
  const version = /__version__\s*=\s*"([^"]+)"/.exec(read('python/feiyu_memgate/__init__.py'))[1]
  assert.equal(version, record.version)
  assert.ok(!JSON.stringify(record).match(/[A-Za-z]:\\|\/Users\/|\/home\//), 'no local path in the record')
})

test('the entry exports name, inject, Config and apply, and no default export', async () => {
  const entry = await import('../index.js')
  assert.deepEqual(Object.keys(entry).sort(), ['Config', 'apply', 'inject', 'name'])
  assert.equal(entry.name, pkg.name)
  assert.deepEqual(entry.inject, ['tools', 'systemPrompt'])
  assert.equal(typeof entry.apply, 'function')
  assert.equal(entry.Config, undefined, 'without the host schema library the plugin loads with defaults')
})

test('the keyword index helper ships with the plugin and imports only the standard library at the top (the gateway package is added by path inside main)', () => {
  assert.ok(pkg.files.includes('src/'), 'src/ is published, and with it src/mirror.py')
  const source = read('src/mirror.py')
  const imports = [...source.matchAll(/^(?:import|from)\s+([A-Za-z_][\w.]*)/gm)].map((m) => m[1].split('.')[0])
  assert.deepEqual([...new Set(imports)].sort(), ['json', 'os', 'pathlib', 'sqlite3', 'sys', 'threading', 'time'])
  assert.match(source, /deadline_seconds/)
})

test('files of this task hold no local paths, private data or vendor names', () => {
  const own = [
    'index.js', 'package.json', 'cordis.patch.yml', '.gitignore', '.npmignore', 'locale/en.json', 'locale/zh.json',
    'scripts/sync-gateway.mjs', 'scripts/sync-client.mjs', 'scripts/sync-catalogue.mjs', 'client/client.js',
    'src/plugin.js', 'src/config.js', 'src/gateway.js', 'src/tiers.js', 'src/commands.js', 'src/migrate.js', 'src/migrate.py',
    'src/mirror.py', 'src/setup.js', 'src/catalogue.js',
    'tests/plugin.test.js', 'tests/plugin-host.test.js', 'tests/gateway.test.js', 'tests/config.test.js', 'tests/package.test.js',
    'tests/tiers.test.js', 'tests/commands.test.js', 'tests/migrate.test.js', 'tests/mirror.test.js', 'tests/client-page.test.js',
    'tests/setup.test.js', 'tests/catalogue.test.js',
    'README.md', 'README.zh.md', 'docs/DETAIL.md', 'docs/DETAIL.en.md',
    ...fs.readdirSync(path.join(ROOT, 'tests', 'helpers')).map((f) => 'tests/helpers/' + f),
  ]
  // Assembled from pieces so this file does not contain the words it looks for.
  const vendors = new RegExp(['cl' + 'aude', 'anth' + 'ropic', 'co' + 'dex', 'g' + 'pt', 'cop' + 'ilot', 'gem' + 'ini', 'ai-' + 'generated'].join('|'), 'i')
  // The one allowed use: the technical term for the embeddings API shape, in English or Chinese.
  const vendorPlain = new RegExp('open' + 'ai(?!-compatible| 兼容)', 'i')
  const localPath = /[A-Za-z]:\\(Users|workspace|Deepseek)|\/Users\/|\/home\/(?!u\/)/
  const email = /[\w.+-]+@[\w-]+\.[\w.]+/
  for (const rel of own) {
    const text = read(rel)
    assert.ok(!vendors.test(text) && !vendorPlain.test(text), `${rel}: vendor name`)
    assert.ok(!localPath.test(text), `${rel}: local path`)
    assert.ok(!email.test(text), `${rel}: e-mail address`)
  }
})
