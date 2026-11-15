// The OpenAI-compatible embeddings endpoint served by the worker process, with the model behind an
// interface so tests can run it without node-llama-cpp.
//
// What the memory gateway's client sends and expects (feiyu-memgate, backends/embedding.py,
// class _Http, lines 172-264):
//   POST <url>/embeddings, JSON {model, input: [texts]}, Authorization: Bearer <key>,
//   Connection: close; up to `batch` (default 32) texts per request; a 2xx answer
//   {data: [{index, embedding: [floats]}]} with one item per text, indexes covering 0..n-1 and all
//   vectors the same length; any other status is a failure (redirects are not followed); at most
//   16 MB of response; a socket timeout of 10 s by default. Query and document prefixes are added
//   by the gateway before the request, so this endpoint adds none.
//
// Limits: the request body, the number of texts and the queue are capped.
//
// Over-long input: the gateway does not keep text within a model's context (it allows 500
// characters per query and 4000 per memory, gateway/app.py:34-37, more than the 512 tokens of the
// bge v1.5 models), and it stores and returns the full text whatever was embedded. So by default an
// input longer than the model's limit is truncated the way Ollama does it (src/tokens.js): the
// gateway's calibration went through Ollama, which truncates by default. With overflow "reject" it is
// refused with 413 (`context_length_exceeded`) instead. The mode comes from the worker's options and
// can be set per request with the `x-embed-overflow` header (the add-on's front endpoint sends it
// from the current settings). Every 200 answer carries `x-truncated-inputs: <n>`, the number of inputs
// of that request that were cut; the gateway's client reads only the status and the body.
//
// Errors use the OpenAI-compatible shape {error: {message, type, code}} and never repeat the input text.
import http from 'node:http'
import { timingSafeEqual } from 'node:crypto'

export const DEFAULT_LIMITS = Object.freeze({ maxBatch: 64, maxBodyBytes: 4 * 1024 * 1024, maxQueue: 8 })
export const OVERFLOW_HEADER = 'x-embed-overflow'
export const TRUNCATED_HEADER = 'x-truncated-inputs'
export const OVERFLOW_MODES = Object.freeze(['truncate', 'reject'])

class HttpError extends Error {
  constructor(status, code, message, type = 'invalid_request_error') {
    super(message)
    this.status = status
    this.code = code
    this.type = type
  }
}

/** Constant-time check of an Authorization header against `Bearer <key>`. */
export function bearerMatches(header, key) {
  if (typeof header !== 'string' || typeof key !== 'string' || !key) return false
  const expected = Buffer.from(`Bearer ${key}`)
  const given = Buffer.from(header)
  return given.length === expected.length && timingSafeEqual(given, expected)
}

/** Read a request body up to `limit` bytes; HttpError 413 beyond it. */
export function readBody(request, limit) {
  return new Promise((resolve, reject) => {
    const declared = Number(request.headers['content-length'])
    if (Number.isFinite(declared) && declared > limit) {
      request.resume()
      reject(new HttpError(413, 'request_too_large', `the request body is larger than ${limit} bytes`))
      return
    }
    const chunks = []
    let size = 0
    let done = false
    request.on('data', (chunk) => {
      if (done) return
      size += chunk.length
      if (size > limit) {
        done = true
        request.resume()
        reject(new HttpError(413, 'request_too_large', `the request body is larger than ${limit} bytes`))
        return
      }
      chunks.push(chunk)
    })
    request.on('end', () => {
      if (!done) {
        done = true
        resolve(Buffer.concat(chunks))
      }
    })
    request.on('error', (error) => {
      if (!done) {
        done = true
        reject(error)
      }
    })
  })
}

export function sendJson(response, status, value, headers = {}) {
  const body = Buffer.from(JSON.stringify(value))
  response.writeHead(status, { 'content-type': 'application/json', 'content-length': body.length, ...headers })
  response.end(body)
}

export function sendError(response, status, code, message, type = status >= 500 ? 'server_error' : 'invalid_request_error', headers = {}) {
  sendJson(response, status, { error: { message, type, code } }, headers)
}

function unit(vector) {
  let sum = 0
  for (const x of vector) {
    if (!Number.isFinite(x)) return null
    sum += x * x
  }
  const norm = Math.sqrt(sum)
  if (!Number.isFinite(norm) || norm === 0) return null
  return Array.from(vector, (x) => x / norm)
}

/**
 * Validate a parsed request against the model; returns the list of texts.
 * @param {any} body  parsed JSON
 * @param {{ modelId: string, dimensions: number, maxBatch: number }} model
 */
export function parseRequest(body, { modelId, dimensions, maxBatch }) {
  if (body === null || typeof body !== 'object' || Array.isArray(body)) throw new HttpError(400, 'invalid_body', 'the body must be a JSON object')
  if (typeof body.model !== 'string' || !body.model) throw new HttpError(400, 'invalid_model', 'model must be a non-empty string')
  // A request for another model must never get this model's vectors: the gateway would store them
  // under the wrong name.
  if (body.model !== modelId) throw new HttpError(404, 'model_not_found', 'this endpoint serves another model')
  const input = typeof body.input === 'string' ? [body.input] : body.input
  if (!Array.isArray(input) || input.length === 0) throw new HttpError(400, 'invalid_input', 'input must be a string or a non-empty array of strings')
  if (input.length > maxBatch) throw new HttpError(413, 'too_many_inputs', `at most ${maxBatch} inputs per request`)
  for (let i = 0; i < input.length; i++) {
    if (typeof input[i] !== 'string') throw new HttpError(400, 'invalid_input', `input ${i} is not a string (token arrays are not supported)`)
    if (!input[i].trim()) throw new HttpError(400, 'invalid_input', `input ${i} is empty`)
  }
  if (body.encoding_format !== undefined && body.encoding_format !== 'float') throw new HttpError(400, 'invalid_encoding_format', 'only encoding_format "float" is supported')
  if (body.dimensions !== undefined && body.dimensions !== dimensions) throw new HttpError(400, 'invalid_dimensions', `this model produces ${dimensions} dimensions`)
  return input
}

/**
 * The embeddings server of one loaded model.
 *   embedder: { dimensions, maxInputTokens,
 *               prepare(text, limit) -> { count, used, truncated, input } (count: tokens of the whole
 *                 text, used: tokens embedded, at most limit; input: what embed() takes),
 *               embed(input) -> Promise<ArrayLike<number>> }; calls are serialised here
 *   overflow: 'truncate' (default) or 'reject', for requests without the x-embed-overflow header
 *   onRequest(): called for every authorised request (activity tracking)
 * Returns { server, listen(port, host) -> Promise<port>, close() }.
 */
export function createEmbeddingServer({ key, modelId, embedder, limits = {}, overflow: defaultOverflow = 'truncate', onRequest = () => {} }) {
  const { maxBatch, maxBodyBytes, maxQueue } = { ...DEFAULT_LIMITS, ...limits }
  let chain = Promise.resolve()
  let queued = 0

  // One embedding at a time: the context has one sequence, and a queue keeps memory flat.
  function serial(task) {
    if (queued >= maxQueue) throw new HttpError(503, 'busy', 'too many requests are waiting', 'server_error')
    queued += 1
    const run = chain.then(task)
    chain = run.catch(() => {}).finally(() => {
      queued -= 1
    })
    return run
  }

  async function handle(request, response) {
    if (!bearerMatches(request.headers.authorization, key)) {
      request.resume()
      return sendError(response, 401, 'invalid_api_key', 'missing or wrong bearer key')
    }
    const url = new URL(request.url ?? '/', 'http://127.0.0.1')
    if (url.pathname !== '/v1/embeddings') {
      request.resume()
      return sendError(response, 404, 'not_found', 'unknown endpoint')
    }
    if (request.method !== 'POST') {
      request.resume()
      return sendError(response, 405, 'method_not_allowed', 'use POST', 'invalid_request_error', { allow: 'POST' })
    }
    onRequest()
    const raw = await readBody(request, maxBodyBytes)
    let body
    try {
      body = JSON.parse(raw.toString('utf8'))
    } catch {
      throw new HttpError(400, 'invalid_json', 'the body is not valid JSON')
    }
    const texts = parseRequest(body, { modelId, dimensions: embedder.dimensions, maxBatch })
    const asked = request.headers[OVERFLOW_HEADER]
    const overflow = OVERFLOW_MODES.includes(asked) ? asked : defaultOverflow
    const max = embedder.maxInputTokens
    const { result, truncated } = await serial(async () => {
      // Every input is checked before any is embedded: a refused request costs no model time.
      const prepared = []
      for (let i = 0; i < texts.length; i++) {
        const p = await embedder.prepare(texts[i], max)
        if (p.count > max && overflow === 'reject') {
          throw new HttpError(413, 'context_length_exceeded', `input ${i} has ${p.count} tokens; this model accepts at most ${max}`)
        }
        prepared.push(p)
      }
      const data = []
      for (let i = 0; i < prepared.length; i++) {
        const vector = unit(await embedder.embed(prepared[i].input))
        if (!vector || vector.length !== embedder.dimensions) throw new HttpError(500, 'bad_embedding', 'the model returned an unusable vector', 'server_error')
        data.push({ object: 'embedding', index: i, embedding: vector })
      }
      // Usage counts the tokens embedded, as Ollama does for a truncated input.
      const total = prepared.reduce((sum, p) => sum + p.used, 0)
      return {
        result: { object: 'list', data, model: modelId, usage: { prompt_tokens: total, total_tokens: total } },
        truncated: prepared.filter((p) => p.truncated).length,
      }
    })
    sendJson(response, 200, result, { [TRUNCATED_HEADER]: String(truncated) })
  }

  const server = http.createServer((request, response) => {
    handle(request, response).catch((error) => {
      if (response.headersSent) {
        response.destroy()
        return
      }
      if (error instanceof HttpError) sendError(response, error.status, error.code, error.message, error.type)
      else sendError(response, 500, 'internal_error', 'the embedding failed')
    })
  })
  server.keepAliveTimeout = 5000
  server.headersTimeout = 15000
  server.requestTimeout = 120000

  return {
    server,
    listen(port, host = '127.0.0.1') {
      return new Promise((resolve, reject) => {
        const onError = (error) => reject(error)
        server.once('error', onError)
        server.listen(port, host, () => {
          server.off('error', onError)
          resolve(server.address().port)
        })
      })
    },
    close: () => new Promise((resolve) => server.close(() => resolve())),
  }
}
