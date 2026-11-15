// A host process for the "parent dies" test: starts the engine with the fake worker, prints the
// worker's pid once it is ready, then idles until it is killed.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createEngine } from '../../src/engine.js'
import { nodeSpawner } from '../../src/spawn.js'

const stateDir = process.argv[2]
const fake = fileURLToPath(new URL('./fake-worker.mjs', import.meta.url))
const engine = createEngine({ spawnProcess: nodeSpawner(), workerPath: fake, extraEnv: { FAKE_STATE_DIR: stateDir } })
engine.onChange((s) => {
  if (s.phase === 'ready') process.stdout.write(`ready ${fs.readFileSync(path.join(stateDir, 'pid'), 'utf8')}\n`)
})
await engine.load({ modelId: 'bge-small-zh-v1.5', modelPath: path.join(stateDir, 'model.gguf'), dimensions: 8, contextTokens: 64 })
setInterval(() => {}, 60000)
