#!/usr/bin/env node
// Re-copy the gateway's Python package from a feiyu-memgate checkout into python/.
//
//   node scripts/sync-gateway.mjs [path to a feiyu-memgate checkout]
//
// Without a path it uses the repository this package lives in (two folders up).
//
// The copy is verbatim (no __pycache__ or .pyc files) so the plugin runs exactly the gateway that was
// released. With no uncommitted changes the bytes come from the commit itself, not from the working
// files: a Windows checkout can have CRLF line endings where the commit has LF. python/GATEWAY_VERSION.json records the source version, commit and
// a sha256 per file; tests/package.test.js checks the copy against it, so a hand edit of python/ is
// caught. The record holds no local path: only the public repository address.
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const TARGET = path.join(ROOT, 'python')
const SKIP_DIRS = new Set(['__pycache__'])
const SKIP_EXT = new Set(['.pyc', '.pyo'])

function fail(message) {
  process.stderr.write(message + '\n')
  process.exit(1)
}

function listFiles(dir, base = dir) {
  const out = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name)) out.push(...listFiles(full, base))
    } else if (entry.isFile() && !SKIP_EXT.has(path.extname(entry.name))) {
      out.push(path.relative(base, full).split(path.sep).join('/'))
    }
  }
  return out.sort()
}

function git(checkout, args) {
  try {
    return execFileSync('git', ['-C', checkout, ...args], { encoding: 'utf8', windowsHide: true }).trim()
  } catch {
    return null
  }
}

const checkout = process.argv[2] ?? path.resolve(ROOT, '..', '..')
const source = path.resolve(checkout, 'src', 'feiyu_memgate')
if (!fs.existsSync(path.join(source, '__init__.py'))) fail('not a feiyu-memgate checkout: src/feiyu_memgate/__init__.py is missing')
const license = path.resolve(checkout, 'LICENSE')
if (!fs.existsSync(license)) fail('the checkout has no LICENSE file')

const version = /__version__\s*=\s*"([^"]+)"/.exec(fs.readFileSync(path.join(source, '__init__.py'), 'utf8'))?.[1]
if (!version) fail('cannot read __version__ from src/feiyu_memgate/__init__.py')
// The last commit that changed the gateway, not HEAD: the plugin lives in the same repository, so HEAD
// moves with every plugin change while the copied code stays the same.
const commit = git(checkout, ['log', '-1', '--format=%H', '--', 'src/feiyu_memgate', 'LICENSE'])
if (!commit || !/^[0-9a-f]{40}$/.test(commit)) fail('cannot read the commit of the checkout')
// A copy of uncommitted changes cannot be traced back to a commit: say so in the record.
const dirty = Boolean(git(checkout, ['status', '--porcelain', '--', 'src/feiyu_memgate', 'LICENSE']))

// Bytes of one file: from the commit when the gateway has no uncommitted changes, else from disk.
function gitBytes(spec) {
  try {
    return execFileSync('git', ['-C', checkout, 'show', spec], { windowsHide: true, maxBuffer: 64 * 1024 * 1024 })
  } catch {
    return fail('cannot read ' + spec + ' from the checkout')
  }
}
const committed = dirty
  ? null
  : (git(checkout, ['ls-tree', '-r', '--name-only', commit, '--', 'src/feiyu_memgate']) || '')
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.startsWith('src/feiyu_memgate/'))
      .map((line) => line.slice('src/feiyu_memgate/'.length))
      .filter((rel) => !SKIP_EXT.has(path.extname(rel)) && !rel.split('/').some((part) => SKIP_DIRS.has(part)))
      .sort()
if (committed && committed.length === 0) fail('the commit holds no files under src/feiyu_memgate')

const packageTarget = path.join(TARGET, 'feiyu_memgate')
fs.rmSync(packageTarget, { recursive: true, force: true })
fs.mkdirSync(TARGET, { recursive: true })
const files = {}
for (const rel of committed ?? listFiles(source)) {
  const to = path.join(packageTarget, ...rel.split('/'))
  fs.mkdirSync(path.dirname(to), { recursive: true })
  const bytes = committed
    ? gitBytes(commit + ':src/feiyu_memgate/' + rel)
    : fs.readFileSync(path.join(source, ...rel.split('/')))
  fs.writeFileSync(to, bytes)
  files['feiyu_memgate/' + rel] = createHash('sha256').update(bytes).digest('hex')
}
const licenseBytes = committed ? gitBytes(commit + ':LICENSE') : fs.readFileSync(license)
fs.writeFileSync(path.join(TARGET, 'LICENSE'), licenseBytes)
files.LICENSE = createHash('sha256').update(licenseBytes).digest('hex')

const record = {
  package: 'feiyu-memgate',
  repository: 'https://github.com/N0rmal-114/feiyu-memgate',
  version,
  commit,
  dirty,
  files: Object.fromEntries(Object.entries(files).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))),
}
fs.writeFileSync(path.join(TARGET, 'GATEWAY_VERSION.json'), JSON.stringify(record, null, 2) + '\n')
process.stdout.write(`copied feiyu-memgate ${version} (${commit.slice(0, 12)}${dirty ? ', with uncommitted changes' : ''}): ${Object.keys(files).length} files\n`)
