// The worker process: loads one GGUF model with node-llama-cpp and serves it on loopback.
//
// Started by src/engine.js as `<node or Electron binary> worker.js '<json options>'`, with
// ELECTRON_RUN_AS_NODE=1 so the Desktop's Electron binary runs it as plain Node (the host's
// SenseVoice worker is started the same way: speech-to-text-sensevoice/src/recognizer.ts:262-266).
// A native crash here ends only this process; the parent restarts it.
//
// Protocol with the parent:
//   - options arrive as JSON in argv[2]; the bearer key in the environment variable named by
//     KEY_ENV, which is deleted as soon as it is read and is never printed;
//   - stdout carries exactly one JSON line: {"event":"ready", port, dimensions, maxInputTokens,
//     trainContextTokens, loadMs} once the model is loaded and the port is bound, or
//     {"event":"error","kind":...} before a non-zero exit;
//   - stdin is a pipe the parent never writes to: when it closes, the parent is gone (stopped us, or
//     died however it died) and this process exits.
// Exit codes: 2 bad options, 3 the model did not load, 4 the port was taken, 5 node-llama-cpp or its
// binary is not available, 1 anything else.
//
// CPU only, no downloads, no compiler: gpu:false, build:'never', usePrebuiltBinaries, skipDownload
// (node-llama-cpp 3.22.3, dist/bindings/getLlama.d.ts). The parent also sets NODE_LLAMA_CPP_GPU=false
// and NODE_LLAMA_CPP_SKIP_DOWNLOAD=true, which the same options read as defaults (dist/config.js).
import { EXIT, KEY_ENV } from './protocol.js'
import { createEmbeddingServer } from './server.js'
import { inputTokenizer } from './tokens.js'

function emit(value) {
  process.stdout.write(JSON.stringify(value) + '\n')
}

// Print a reason, then exit once stderr is flushed: a loaded native model can keep the process alive.
function fail(kind, code, detail) {
  try {
    emit({ event: 'error', kind })
  } catch {
    // stdout is gone: the exit code still tells the parent
  }
  process.stderr.write(`worker: ${kind}${detail ? `: ${detail}` : ''}\n`, () => process.exit(code))
}

function watchParent() {
  process.stdin.on('end', () => process.exit(0))
  process.stdin.on('close', () => process.exit(0))
  process.stdin.on('error', () => process.exit(0))
  process.stdin.resume()
}

function parseOptions(raw) {
  const options = JSON.parse(raw)
  const ok = options && typeof options === 'object'
    && typeof options.modelId === 'string' && options.modelId
    && typeof options.modelPath === 'string' && options.modelPath
    && Number.isInteger(options.port) && options.port > 0 && options.port < 65536
    && Number.isInteger(options.contextTokens) && options.contextTokens >= 8
    && Number.isInteger(options.dimensions) && options.dimensions > 0
  if (!ok) throw new Error('missing or invalid options')
  return options
}

async function main() {
  let options
  try {
    options = parseOptions(process.argv[2])
  } catch (error) {
    return fail('options', EXIT.options, error?.message)
  }
  const key = process.env[KEY_ENV]
  delete process.env[KEY_ENV]
  if (typeof key !== 'string' || key.length < 32) return fail('options', EXIT.options, 'no key')
  if (options.watchStdin !== false) watchParent()

  const started = performance.now()
  let llamaCpp
  try {
    llamaCpp = await import('node-llama-cpp')
  } catch (error) {
    return fail('runtime', EXIT.runtime, error?.code || error?.name)
  }
  const { getLlama, LlamaLogLevel } = llamaCpp
  let llama
  try {
    llama = await getLlama({
      gpu: false,
      build: 'never',
      usePrebuiltBinaries: true,
      skipDownload: true,
      progressLogs: false,
      logLevel: LlamaLogLevel.warn,
      logger: (level, message) => process.stderr.write(`llama.cpp ${level}: ${String(message).trimEnd()}\n`),
      ...(options.threads > 0 ? { maxThreads: options.threads } : {}),
    })
  } catch (error) {
    return fail('runtime', EXIT.runtime, error?.name)
  }
  let model
  let context
  try {
    model = await llama.loadModel({ modelPath: options.modelPath, gpuLayers: 0 })
    // getEmbeddingFor refuses a sequence whose length reaches the context size
    // (dist/evaluator/LlamaEmbeddingContext.js), so the context is one token larger than the longest
    // input. A non-causal model (BERT) needs the whole input in one batch, so the batch is the context.
    context = await model.createEmbeddingContext({
      contextSize: options.contextTokens + 1,
      batchSize: options.contextTokens + 1,
      ...(options.threads > 0 ? { threads: options.threads } : {}),
    })
  } catch (error) {
    return fail('load', EXIT.load, error?.name)
  }
  const dimensions = model.embeddingVectorSize
  if (dimensions !== options.dimensions) return fail('load', EXIT.load, `the model has ${dimensions} dimensions, ${options.dimensions} expected`)
  // Never more than the registry's limit, the model's trained context, or what the context holds.
  const contextSize = context._llamaContext?.contextSize ?? options.contextTokens + 1
  const maxInputTokens = Math.min(options.contextTokens, contextSize - 1, model.trainContextSize || Infinity)
  // Special tokens and truncation as Ollama does them (src/tokens.js).
  const tokenizeInput = inputTokenizer(model)
  const embedder = {
    dimensions,
    maxInputTokens,
    prepare(text, limit) {
      const { tokens, count, truncated } = tokenizeInput(text, limit)
      return { count, used: tokens.length, truncated, input: tokens }
    },
    embed: async (tokens) => (await context.getEmbeddingFor(tokens)).vector,
  }
  const endpoint = createEmbeddingServer({
    key, modelId: options.modelId, embedder, limits: options.limits ?? {}, overflow: options.overflow === 'reject' ? 'reject' : 'truncate',
  })
  try {
    await endpoint.listen(options.port, '127.0.0.1')
  } catch (error) {
    return fail(error?.code === 'EADDRINUSE' ? 'port' : 'listen', error?.code === 'EADDRINUSE' ? EXIT.port : EXIT.other, error?.code)
  }
  emit({
    event: 'ready', port: options.port, dimensions, maxInputTokens, trainContextTokens: model.trainContextSize,
    loadMs: Math.round(performance.now() - started),
  })
}

main().catch((error) => fail('other', EXIT.other, error?.name))
