// A stand-in for src/worker.js that speaks the same protocol without node-llama-cpp: same options,
// same key handling, same ready line, the real HTTP server (src/server.js) with a fake embedder.
//
// Behaviour comes from FAKE_MODE (comma-separated):
//   crash-first-N     exit with code 70 before the ready line for the first N starts
//   crash-after-ready exit with code 71 shortly after becoming ready, every time
//   fail-load         report a load failure (exit code 3)
//   port-taken-once   report the port as taken on the first start
//   slow-load-MS      wait MS milliseconds before loading
//   bad-vectors       return vectors of the wrong length (fails the self-test)
// FAKE_STATE_DIR: a folder for the start counter and the pid file.
import fs from 'node:fs'
import path from 'node:path'
import { createEmbeddingServer } from '../../src/server.js'
import { EXIT, KEY_ENV } from '../../src/protocol.js'

const options = JSON.parse(process.argv[2])
const key = process.env[KEY_ENV]
delete process.env[KEY_ENV]
const modes = (process.env.FAKE_MODE ?? '').split(',').filter(Boolean)
const stateDir = process.env.FAKE_STATE_DIR
const has = (name) => modes.some((m) => m === name || m.startsWith(name + '-'))
const arg = (name) => Number(modes.find((m) => m.startsWith(name + '-'))?.slice(name.length + 1))

let starts = 0
if (stateDir) {
  const counter = path.join(stateDir, 'starts')
  try {
    starts = Number(fs.readFileSync(counter, 'utf8')) || 0
  } catch {
    starts = 0
  }
  starts += 1
  fs.writeFileSync(counter, String(starts))
  fs.writeFileSync(path.join(stateDir, 'pid'), String(process.pid))
}

const emit = (value) => process.stdout.write(JSON.stringify(value) + '\n')
const exit = (code) => process.stderr.write(`fake worker exit ${code}\n`, () => process.exit(code))

process.stdin.on('end', () => process.exit(0))
process.stdin.on('close', () => process.exit(0))
process.stdin.on('error', () => process.exit(0))
process.stdin.resume()

if (!key || key.length < 32) exit(EXIT.options)
else if (has('crash-first') && starts <= arg('crash-first')) exit(70)
else if (has('fail-load')) {
  emit({ event: 'error', kind: 'load' })
  exit(EXIT.load)
} else if (has('port-taken-once') && starts === 1) {
  emit({ event: 'error', kind: 'port' })
  exit(EXIT.port)
} else {
  if (has('slow-load')) await new Promise((r) => setTimeout(r, arg('slow-load')))
  const dims = options.dimensions
  const embedder = {
    dimensions: dims,
    maxInputTokens: options.contextTokens,
    // One token per character plus two special tokens, like a BERT tokenizer on CJK text; a cut
    // keeps the head of the text.
    prepare(text, limit) {
      const chars = [...text]
      const count = chars.length + 2
      const kept = count > limit ? chars.slice(0, Math.max(0, limit - 2)) : chars
      return { count, used: kept.length + 2, truncated: count > limit, input: kept.join('') }
    },
    async embed(text) {
      const v = new Array(has('bad-vectors') ? dims + 1 : dims).fill(0)
      for (const [i, ch] of [...text].entries()) v[(ch.codePointAt(0) + i) % v.length] += 1
      v[0] += 0.5
      return v
    },
  }
  const server = createEmbeddingServer({
    key, modelId: options.modelId, embedder, limits: options.limits ?? {}, overflow: options.overflow === 'reject' ? 'reject' : 'truncate',
  })
  try {
    await server.listen(options.port)
  } catch (error) {
    emit({ event: 'error', kind: error?.code === 'EADDRINUSE' ? 'port' : 'listen' })
    exit(error?.code === 'EADDRINUSE' ? EXIT.port : EXIT.other)
  }
  emit({ event: 'ready', port: options.port, dimensions: dims, maxInputTokens: options.contextTokens, trainContextTokens: options.contextTokens, loadMs: 1 })
  if (has('crash-after-ready')) setTimeout(() => exit(71), 300)
}
