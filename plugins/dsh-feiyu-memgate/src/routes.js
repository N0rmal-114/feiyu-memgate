// The routes of the settings page, served by the host's web server: status, the local model list, the
// trial bench, the changelog and the mascot's drawings. Every route is owner only (admission) and none
// is reachable by the model. Their small caches (the changelog's parse, the drawings' bytes) live here.
// Logs name kinds of failure only, never a query, a memory, a path or a token.
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { releasedEntries } from './changelog.js'
import { buildQuery, codePointLength, pyStrip } from './query.js'
import { describeFailure } from './setup.js'
import { kindOf, REQUEST_CONTEXT } from './shared.js'

// Read by the settings page (client/client.js) as the document-relative path `feiyu-memgate/status`,
// the same way first-party pages reach their host routes (packages/host/open-in-app/src/shared.ts:11-14).
export const STATUS_PATH = '/feiyu-memgate/status'
// The local model list of the settings page: GET reads it, POST starts, cancels, switches or removes
// (src/setup.js). Owner only, like the status.
export const MODELS_PATH = '/feiyu-memgate/models'
// The released sections of the package's own CHANGELOG.md, for the settings page (src/changelog.js). Owner only too.
export const CHANGELOG_PATH = '/feiyu-memgate/changelog'
// The trial bench of the settings page: POST { query } explains a recall (the candidates, their scores,
// the step that left one out) without changing anything. Owner only; reads no conversation state.
export const TRY_PATH = '/feiyu-memgate/try'
const CHANGELOG_FILE = new URL('../CHANGELOG.md', import.meta.url)
// The mascot's drawings on the settings page (the four moods of the summary and the one of the Feedback tab): GET <ART_PATH><name>, one exact route per file of client/art.
// Owner only like the other routes of the page: nothing here is secret, but the plugin serves nothing
// to anyone the host has not admitted.
export const ART_PATH = '/feiyu-memgate/art/'
export const ART_FILES = Object.freeze(['mascot-ready.webp', 'mascot-busy.webp', 'mascot-asleep.webp', 'mascot-trouble.webp', 'mascot-nag.webp'])
const ART_DIR = new URL('../client/art/', import.meta.url)
// A drawing is about 30 KB; a file far beyond that is not one of ours and is not served.
const MAX_ART_BYTES = 512 * 1024
const MAX_ACTION_BYTES = 8 * 1024
const TRIAL_MAX_CHARS = 500 // the gateway's query limit
const MODEL_ACTIONS = new Set(['install', 'builds', 'cancel', 'use', 'remove'])
// Actions that can make the host install a package: only with `confirmed: true`, which the page sends
// after its own confirmation step (a direct plugin manager call shows no host dialog).
const CONFIRMED_ACTIONS = new Set(['install', 'builds'])

/**
 * @param {object} deps
 * @param {Function} deps.service        service(name): a host service, or undefined
 * @param {object}   deps.log            { warn, info }
 * @param {Function} deps.status         the status answer (the settings page's status block)
 * @param {object}   deps.setup          the model setup (src/setup.js)
 * @param {Function} deps.settings       the current settings
 * @param {Function} deps.client         client(limit): a gateway client, or null while memory is unavailable
 * @param {string}   deps.version        the package version
 * @param {URL|string} [deps.changelogFile]  the CHANGELOG.md to read (replaceable in tests)
 * @param {URL|string} [deps.artDir]         the folder of the drawings (replaceable in tests)
 */
export function createRoutes({ service, log, status, setup, settings, client, version, changelogFile: changelogFileDep, artDir: artDirDep }) {
  const changelogFile = changelogFileDep ?? CHANGELOG_FILE
  const artDir = artDirDep ?? ART_DIR

  /**
   * Who may use this plugin's routes: the owner only, like the first-party host routes. Returns
   * null when the request is admitted, else the HTTP status to answer with.
   * From 0.2.2-rc.3 the web server itself applies the host's Host/Origin fence and the session check
   * to an `auth: 'required'` route and hands the handler the admitted Peer. 0.2.2-rc.1 ignores `auth`
   * and calls the handler without a Peer; there, as its open-in-app routes do, the route asks the
   * connection service, whose admit() applies the same fence and session check.
   */
  function admission(req, peer) {
    if (peer !== undefined && peer !== null) return peer.principal?.kind === 'owner' ? null : 403
    const connection = service('connection')
    if (!connection || typeof connection.admit !== 'function') return 503
    let result
    try {
      result = connection.admit(req)
    } catch {
      return 503
    }
    if (result && result.peer) return result.peer.principal?.kind === 'owner' ? null : 403
    return Number.isInteger(result?.rejection) ? result.rejection : 401
  }

  // The status for the settings page. Owner only; the response is never cached by the browser.
  function statusRoute() {
    return {
      kind: 'exact',
      auth: 'required',
      path: STATUS_PATH,
      handler: async (req, res, peer) => {
        const send = (code, body, extra = {}) => {
          res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...extra })
          res.end(req.method === 'HEAD' ? undefined : JSON.stringify(body))
        }
        try {
          const refused = admission(req, peer)
          if (refused !== null) return send(refused, { code: refused === 403 ? 'forbidden' : 'unauthorized', message: 'owner access required' })
          if (req.method !== 'GET' && req.method !== 'HEAD') return send(405, { code: 'method-not-allowed' }, { allow: 'GET, HEAD' })
          send(200, await status())
        } catch (error) {
          log.warn(`feiyu-memgate: status unavailable (${kindOf(error)})`)
          if (!res.headersSent) send(500, { code: 'status-unavailable' })
          else res.end()
        }
      },
    }
  }

  // A small JSON body, or a reason to refuse it: wrong content type, too large, not JSON.
  async function readJson(req) {
    const type = String(req.headers?.['content-type'] ?? '').split(';', 1)[0].trim().toLowerCase()
    if (type !== 'application/json') return { status: 415, code: 'unsupported-media-type' }
    const chunks = []
    let size = 0
    for await (const chunk of req) {
      size += chunk.length
      if (size > MAX_ACTION_BYTES) return { status: 413, code: 'too-large' }
      chunks.push(chunk)
    }
    try {
      return { json: JSON.parse(Buffer.concat(chunks).toString('utf8')) }
    } catch {
      return { status: 400, code: 'bad-json' }
    }
  }

  // The body of a model-list click, or a reason to refuse it.
  async function readAction(req) {
    const read = await readJson(req)
    if (!('json' in read)) return read
    const body = read.json
    if (!body || typeof body !== 'object' || !MODEL_ACTIONS.has(body.action)) return { status: 400, code: 'bad-action' }
    if (CONFIRMED_ACTIONS.has(body.action) && body.confirmed !== true) return { status: 400, code: 'confirm-required' }
    if (body.action === 'builds' && typeof body.allow !== 'boolean') return { status: 400, code: 'bad-decision' }
    if (body.action !== 'cancel' && body.action !== 'builds' && (typeof body.id !== 'string' || body.id.length > 80)) return { status: 400, code: 'bad-id' }
    if (body.acceptLicence !== undefined && (typeof body.acceptLicence !== 'string' || body.acceptLicence.length > 200)) return { status: 400, code: 'bad-licence' }
    return { body }
  }

  // The local model list of the settings page: GET reads where everything stands, POST is a click
  // (install, cancel, use, remove). Owner only; a cross-site page cannot send it (the host's
  // Host/Origin fence, a JSON body, the session cookie), and no tool reaches it.
  function modelsRoute() {
    return {
      kind: 'exact',
      auth: 'required',
      path: MODELS_PATH,
      handler: async (req, res, peer) => {
        const send = (code, body, extra = {}) => {
          res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...extra })
          res.end(req.method === 'HEAD' ? undefined : JSON.stringify(body))
        }
        try {
          const refused = admission(req, peer)
          if (refused !== null) return send(refused, { code: refused === 403 ? 'forbidden' : 'unauthorized', message: 'owner access required' })
          if (req.method === 'GET' || req.method === 'HEAD') return send(200, await setup.view())
          if (req.method !== 'POST') return send(405, { code: 'method-not-allowed' }, { allow: 'GET, HEAD, POST' })
          const parsed = await readAction(req)
          if (!parsed.body) return send(parsed.status, { code: parsed.code })
          const { action, id, acceptLicence, allow } = parsed.body
          const confirmed = parsed.body.confirmed === true
          const result = action === 'install' ? await setup.install(id, { acceptLicence, confirmed })
            : action === 'builds' ? await setup.decideBuilds(allow, { confirmed })
              : action === 'use' ? await setup.use(id)
                : action === 'remove' ? await setup.remove(id)
                  : await setup.cancel()
          const ok = result.ok === true
          send(200, {
            result: { ok, code: result.code, problem: ok ? null : (setup.describe?.(result.code) ?? describeFailure(result.code)), licence: result.licence ?? null },
            view: await setup.view(),
          })
        } catch (error) {
          log.warn(`feiyu-memgate: model list unavailable (${kindOf(error)})`)
          if (!res.headersSent) send(500, { code: 'models-unavailable' })
          else res.end()
        }
      },
    }
  }

  // The trial bench of the settings page: one sentence in, the gateway's explanation of its recall out.
  // Owner only, POST only. It touches no conversation state (the lists of memories already given, the
  // pools, the relevance check, the counters of the status) and logs neither the sentence nor a result.
  function tryRoute() {
    return {
      kind: 'exact',
      auth: 'required',
      path: TRY_PATH,
      handler: async (req, res, peer) => {
        const send = (code, body, extra = {}) => {
          res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...extra })
          res.end(JSON.stringify(body))
        }
        try {
          const refused = admission(req, peer)
          if (refused !== null) return send(refused, { code: refused === 403 ? 'forbidden' : 'unauthorized', message: 'owner access required' })
          if (req.method !== 'POST') return send(405, { code: 'method-not-allowed' }, { allow: 'POST' })
          const read = await readJson(req)
          if (!('json' in read)) return send(read.status, { code: read.code })
          const text = read.json?.query
          if (typeof text !== 'string' || !pyStrip(text) || codePointLength(text) > TRIAL_MAX_CHARS) return send(400, { code: 'bad-query' })
          const query = buildQuery(text, null)
          if (query === null) return send(200, { ok: false, code: 'no-recall' })
          const limit = settings().advanced.recallLimit
          const c = client(limit)
          if (!c) return send(200, { ok: false, code: 'unavailable' })
          const result = await c.explainRecall(query, { context: REQUEST_CONTEXT.trial })
          send(200, result?.ok === true ? { ...result, limit } : { ok: false, code: result?.code === 'unsupported' ? 'unsupported' : 'failed' })
        } catch (error) {
          log.warn(`feiyu-memgate: trial recall failed (${kindOf(error)})`)
          if (!res.headersSent) send(200, { ok: false, code: 'failed' })
          else res.end()
        }
      },
    }
  }

  // ---------------------------------------------------------------- changelog

  let changelogCache = null // { key: 'mtime:size', entries }

  /**
   * The released entries of the package's CHANGELOG.md: read on request, parsed again only when the file's
   * modification time or size changed. A file that is missing or cannot be read gives no entries (never an
   * error, never a path).
   */
  function changelog() {
    try {
      const stat = fs.statSync(changelogFile)
      const key = `${stat.mtimeMs}:${stat.size}`
      if (changelogCache?.key !== key) changelogCache = { key, entries: releasedEntries(fs.readFileSync(changelogFile, 'utf8'), version) }
      return { version: version, entries: changelogCache.entries }
    } catch {
      changelogCache = null
      return { version: version, entries: [] }
    }
  }

  // What the settings page reads under "Changelog". Owner only, never cached by the browser.
  function changelogRoute() {
    return {
      kind: 'exact',
      auth: 'required',
      path: CHANGELOG_PATH,
      handler: async (req, res, peer) => {
        const send = (code, body, extra = {}) => {
          res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...extra })
          res.end(req.method === 'HEAD' ? undefined : JSON.stringify(body))
        }
        try {
          const refused = admission(req, peer)
          if (refused !== null) return send(refused, { code: refused === 403 ? 'forbidden' : 'unauthorized', message: 'owner access required' })
          if (req.method !== 'GET' && req.method !== 'HEAD') return send(405, { code: 'method-not-allowed' }, { allow: 'GET, HEAD' })
          send(200, changelog())
        } catch (error) {
          log.warn(`feiyu-memgate: changelog unavailable (${kindOf(error)})`)
          if (!res.headersSent) send(500, { code: 'changelog-unavailable' })
          else res.end()
        }
      },
    }
  }

  // ---------------------------------------------------------------- art

  const artCache = new Map() // file name -> { key: 'mtime:size', bytes, etag }

  /**
   * One drawing of client/art: its bytes and an entity tag made from them, read again only when the
   * file changed. Null when the file is missing, unreadable or not the size of a drawing.
   */
  function art(name) {
    try {
      const file = artDir instanceof URL ? new URL(name, artDir) : path.join(String(artDir), name)
      const stat = fs.statSync(file)
      if (!stat.isFile() || stat.size === 0 || stat.size > MAX_ART_BYTES) return null
      const key = `${stat.mtimeMs}:${stat.size}`
      const hit = artCache.get(name)
      if (hit?.key === key) return hit
      const bytes = fs.readFileSync(file)
      const entry = { key, bytes, etag: `"${createHash('sha1').update(bytes).digest('hex').slice(0, 16)}"` }
      artCache.set(name, entry)
      return entry
    } catch {
      artCache.delete(name)
      return null
    }
  }

  // The settings page's <img> for one mood. The name is fixed by the route, never taken from the
  // request, so no path of the request reaches the file system. The browser may keep the picture but
  // asks each time whether it changed (the tag answers with 304), so an updated plugin shows its own.
  function artRoute(name) {
    return {
      kind: 'exact',
      auth: 'required',
      path: ART_PATH + name,
      handler: async (req, res, peer) => {
        const plain = (code, extra = {}) => {
          res.writeHead(code, { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...extra })
          res.end()
        }
        try {
          const refused = admission(req, peer)
          if (refused !== null) return plain(refused)
          if (req.method !== 'GET' && req.method !== 'HEAD') return plain(405, { allow: 'GET, HEAD' })
          const entry = art(name)
          if (!entry) return plain(404)
          const headers = { 'content-type': 'image/webp', 'cache-control': 'private, no-cache', etag: entry.etag, 'x-content-type-options': 'nosniff' }
          if (req.headers?.['if-none-match'] === entry.etag) {
            res.writeHead(304, headers)
            return res.end()
          }
          res.writeHead(200, { ...headers, 'content-length': String(entry.bytes.length) })
          res.end(req.method === 'HEAD' ? undefined : entry.bytes)
        } catch (error) {
          log.warn(`feiyu-memgate: a drawing of the settings page is unavailable (${kindOf(error)})`)
          if (!res.headersSent) plain(500)
          else res.end()
        }
      },
    }
  }

  return { statusRoute, modelsRoute, tryRoute, changelogRoute, changelog, artRoute }
}
