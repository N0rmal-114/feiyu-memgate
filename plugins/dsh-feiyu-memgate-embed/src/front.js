// The endpoint the memory gateway calls: a small loopback server in the add-on's own process that
// forwards each request to the worker.
//
// Why a front and not the worker's port directly: the worker's port and key change with every
// worker start (a crash restart, an idle unload and reload), and a gateway pointed at a dead port
// would fail until it is reconfigured and restarted. The front keeps one port and one key for as
// long as a model is active, starts an unloaded worker on the first request, and waits briefly for
// a worker that is loading. The worker still checks its own per-start key and the model name, so a
// request meant for another model never gets this model's vectors.
//
// It listens on 127.0.0.1 only, on a port the system picks, and requires its own bearer key: 32
// random bytes, generated each time the front starts, kept in memory, given out only through the
// service snapshot. The same limits as the worker apply before anything is forwarded.
//
// The overflow setting travels to the worker as a request header on every forward, so a change
// applies at once without restarting the model. The worker's count of truncated inputs comes back
// as a response header; it is passed on and added to a counter (numbers only, never text).
import { randomBytes } from 'node:crypto'
import http from 'node:http'
import { DEFAULT_LIMITS, OVERFLOW_HEADER, OVERFLOW_MODES, TRUNCATED_HEADER, bearerMatches, readBody, sendError } from './server.js'

/**
 * @param {object} options
 * @param {{ ensure(ms: number): Promise<{port: number, key: string} | null>, status(): object }} options.engine
 * @param {number} [options.waitMs]  how long a request waits for a loading worker (the gateway's own
 *   socket timeout is 10 s by default, backends/embedding.py:291)
 * @param {() => string} [options.overflow]  the current overflow mode, 'truncate' or 'reject'
 * @param {(n: number) => void} [options.onTruncated]  called with the number of inputs a request had cut
 */
export function createFront({
  engine, waitMs = 8000, maxBodyBytes = DEFAULT_LIMITS.maxBodyBytes, forwardTimeoutMs = 120000, now = Date.now,
  overflow = () => 'truncate', onTruncated = () => {},
}) {
  let server = null
  let port = null
  let key = null
  let starting = null
  let inflight = 0
  let lastActivity = now()

  function forward(target, body, response) {
    let mode = 'truncate'
    try {
      const value = overflow()
      if (OVERFLOW_MODES.includes(value)) mode = value
    } catch {
      // keep the default
    }
    return new Promise((resolve) => {
      const request = http.request({
        host: '127.0.0.1', port: target.port, path: '/v1/embeddings', method: 'POST', timeout: forwardTimeoutMs,
        headers: {
          'content-type': 'application/json', 'content-length': body.length, authorization: `Bearer ${target.key}`, [OVERFLOW_HEADER]: mode,
        },
      }, (upstream) => {
        const headers = { 'content-type': upstream.headers['content-type'] ?? 'application/json' }
        if (upstream.headers['content-length']) headers['content-length'] = upstream.headers['content-length']
        const cut = Number(upstream.headers[TRUNCATED_HEADER])
        if (Number.isSafeInteger(cut) && cut >= 0) {
          headers[TRUNCATED_HEADER] = String(cut)
          if (cut > 0) {
            try {
              onTruncated(cut)
            } catch {
              // a counter must not break a request
            }
          }
        }
        response.writeHead(upstream.statusCode ?? 502, headers)
        upstream.pipe(response)
        upstream.on('end', resolve)
        upstream.on('error', () => {
          response.destroy()
          resolve()
        })
      })
      request.on('timeout', () => request.destroy(new Error('timeout')))
      request.on('error', () => {
        if (!response.headersSent) sendError(response, 502, 'worker_unavailable', 'the model process did not answer')
        else response.destroy()
        resolve()
      })
      request.end(body)
    })
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
    inflight += 1
    lastActivity = now()
    try {
      let body
      try {
        body = await readBody(request, maxBodyBytes)
      } catch (error) {
        if (error?.status) return sendError(response, error.status, error.code, error.message)
        throw error
      }
      const target = await engine.ensure(waitMs)
      if (!target) {
        const failed = engine.status()?.phase === 'failed'
        return sendError(response, 503, failed ? 'model_unavailable' : 'model_loading',
          failed ? 'the model could not be started' : 'the model is loading; try again shortly', 'server_error', failed ? {} : { 'retry-after': '2' })
      }
      await forward(target, body, response)
    } finally {
      inflight -= 1
      lastActivity = now()
    }
  }

  return {
    /** Start listening (once); resolves { port, key }. */
    start() {
      if (server && port) return Promise.resolve({ port, key })
      if (starting) return starting
      starting = new Promise((resolve, reject) => {
        const s = http.createServer((request, response) => {
          handle(request, response).catch(() => {
            if (!response.headersSent) sendError(response, 500, 'internal_error', 'the request failed')
            else response.destroy()
          })
        })
        s.keepAliveTimeout = 5000
        s.headersTimeout = 15000
        s.requestTimeout = forwardTimeoutMs + waitMs
        s.once('error', (error) => {
          starting = null
          reject(error)
        })
        s.listen(0, '127.0.0.1', () => {
          server = s
          port = s.address().port
          key = randomBytes(32).toString('base64url')
          lastActivity = now()
          starting = null
          resolve({ port, key })
        })
      })
      return starting
    },
    /** Stop listening; the next start() gets a new port and key. */
    async stop() {
      if (starting) await starting.catch(() => {})
      const s = server
      server = null
      port = null
      key = null
      if (s) {
        await new Promise((resolve) => {
          s.close(() => resolve())
          s.closeAllConnections?.()
        })
      }
    },
    endpoint: () => (server && port ? { port, key, url: `http://127.0.0.1:${port}/v1` } : null),
    activity: () => ({ inflight, lastActivity }),
  }
}
