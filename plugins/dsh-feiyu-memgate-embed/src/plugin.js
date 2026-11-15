// The add-on inside the host: the service other plugins read, the chat command, the downloads, and
// the engine that runs the active model.
//
// Every part is optional and switched on its own (src/config.js, `modules`): with the engine off
// nothing is provided and no process runs; with the command off no command is registered. With no
// model chosen, the service is provided with state `no-model`, nothing listens and no process runs.
//
// Host APIs used (file:line in DeepSeek Harness 0.2.2-rc.3; the installed 0.2.2-rc.1 has the same
// shapes unless noted):
//   ctx.provide(name, value) -> disposer    vendor/cordis/src/reflect.ts:313-349 (rc.1 cordis lib/index.js:1395)
//   ctx.set(name, value)                    vendor/cordis/src/reflect.ts:290-301 (rc.1 lib/index.js:1366); does
//                                           not notify anyone, hence the update event below
//   'internal/service' (provide/withdraw)   vendor/cordis/src/reflect.ts:357-376, declared events.ts:345
//   ctx.emit(name, ...args)                 vendor/cordis/src/events.ts:207-210; custom `<plugin>/<action>`
//                                           events: docs/user/develop/framework/events.md:27-39
//   ctx.on / ctx.effect                     vendor/cordis/src/events.ts:298, fiber.ts:446
//   ctx.inject(deps, callback) -> Fiber     vendor/cordis/src/registry.ts:300-302 (optional dependency:
//                                           the child runs while `commands` exists)
//   ctx.get(name)                           vendor/cordis/src/reflect.ts:269-271
//   ctx.commands.register(definition)       packages/interaction/commands/src/index.ts:280-292
//   ctx.subprocess.spawn(spec)              packages/subprocess/subprocess/src/index.ts:160 (src/spawn.js)
//   dshPath / dshHomePath services          packages/boot/app-boot/src/index.ts:1237-1238 (src/config.js)
//   ctx.get('settings').describe()          packages/settings/settings/src/index.ts:302 (UI language only)
//   'loader/volatile-update'                vendor/loader/src/config/entry.ts:194
//   globalThis.fetch (host proxy applies)   packages/util/http-proxy/README.md:32, :45 (src/download.js)
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createCommandDefinition } from './commands.js'
import { parseOrigin, readConfig, resolveDataDir, uiLanguage } from './config.js'
import { DownloadBusyError, createDownloader, hashFile, modelFile, partFile } from './download.js'
import { createEngine } from './engine.js'
import { createFront } from './front.js'
import { MODELS, fingerprint } from './models.js'
import { hostSpawner, nodeSpawner } from './spawn.js'
import { createStateStore } from './state.js'

export const PLUGIN_NAME = 'dsh-feiyu-memgate-embed'
/** The Cordis service name other plugins read (docs/SERVICE.md). */
export const SERVICE_NAME = 'feiyuMemgateEmbed'
/** Emitted with the new snapshot after every change of the service value. */
export const UPDATE_EVENT = 'feiyu-memgate-embed/update'
// 2: version 1's fields, plus `progress`, `overflow` and the control methods (docs/SERVICE.md).
export const SERVICE_VERSION = 2
const LOG_FILE = 'embed-worker.log'
const LOG_MAX_BYTES = 1024 * 1024

function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) deepFreeze(child)
    Object.freeze(value)
  }
  return value
}

// A kind of failure fit for a log line: an error code or name, never its message.
function kindOf(error) {
  const code = error?.code
  if (typeof code === 'string' && /^[A-Z0-9_]{1,40}$/.test(code)) return code
  const name = error?.name
  return typeof name === 'string' && /^[A-Za-z]{1,40}$/.test(name) ? name : 'error'
}

function makeLogger(ctx) {
  let base = null
  try {
    base = typeof ctx?.logger === 'function' ? ctx.logger(PLUGIN_NAME) : ctx?.logger
  } catch {
    base = ctx?.logger ?? null
  }
  const call = (level, message) => {
    try {
      base?.[level]?.(String(message).replaceAll('%', '%%'))
    } catch {
      // logging must never break the add-on
    }
  }
  return { warn: (m) => call('warn', m), info: (m) => call('info', m) }
}

// An append-only log with one rotation: never more than about twice the cap on disk.
function openLog(file, maxBytes = LOG_MAX_BYTES) {
  let size = null
  return function write(data) {
    try {
      if (size === null) {
        fs.mkdirSync(path.dirname(file), { recursive: true })
        try {
          size = fs.statSync(file).size
        } catch {
          size = 0
        }
      }
      const bytes = Buffer.isBuffer(data) ? data : Buffer.from(String(data), 'utf8')
      if (size + bytes.length > maxBytes) {
        try {
          fs.renameSync(file, file + '.1')
        } catch {
          // another process may have rotated it
        }
        size = 0
      }
      fs.appendFileSync(file, bytes, { mode: 0o600 })
      size += bytes.length
    } catch {
      // a full disk must not break the add-on
    }
  }
}

function displayPath(p) {
  const home = os.homedir()
  return home && (p === home || p.startsWith(home + path.sep)) ? '~' + p.slice(home.length) : p
}

/**
 * Wire the add-on into the host context.
 * @param {object} ctx     the plugin's Cordis context
 * @param {object} config  the parsed Config (volatile groups) or plain settings
 * @param {object} [deps]  replacements for tests: env, fetch, statfs, spawnProcess, workerPath, execArgv,
 *                         engineOptions, frontOptions, now, models (registry entries with the same ids)
 */
export function createAddon(ctx, config, deps = {}) {
  const env = deps.env ?? process.env
  const log = makeLogger(ctx)
  const registry = deps.models ?? MODELS
  const getModel = (id) => registry.find((m) => m.id === id)
  const listedModels = ({ all = false } = {}) => registry.filter((m) => all || m.status === 'verified')
  const fetchImpl = deps.fetch ?? ((...args) => globalThis.fetch(...args))
  const safeGet = (name) => {
    try {
      return ctx?.get?.(name)
    } catch {
      return undefined
    }
  }

  let settings = readConfig(config)
  let dataDir = resolveDataDir(settings.download.dataDir, ctx, env)
  let modelsDir = path.join(dataDir, 'models')
  let store = createStateStore(dataDir)
  let stateCache = store.read()
  let writeLog = openLog(path.join(dataDir, LOG_FILE))
  let activation = { id: null, status: 'none', error: null }
  let provided = null
  let lastSnapshot = null
  let commandFiber = null
  let idleTimer = null
  let disposed = false
  let warnedInvalid = false
  let op = Promise.resolve()

  const spawnProcess = deps.spawnProcess ?? ((spec) => {
    const subprocess = safeGet('subprocess')
    const spawner = subprocess && typeof subprocess.spawn === 'function' ? hostSpawner(subprocess) : nodeSpawner()
    return spawner(spec)
  })

  const engine = createEngine({
    spawnProcess,
    writeLog: (data) => writeLog(data),
    ...(deps.workerPath ? { workerPath: deps.workerPath } : {}),
    ...(deps.execArgv ? { execArgv: deps.execArgv } : {}),
    ...(deps.engineOptions ?? {}),
  })
  // Inputs cut to the model's limit since the add-on started (a count only; shown by the command).
  let truncatedInputs = 0
  const front = createFront({
    engine,
    overflow: () => settings.model.overflow,
    onTruncated: (n) => {
      truncatedInputs += n
    },
    ...(deps.frontOptions ?? {}),
  })
  // The model to make active once its download is verified (the one-click `install`). It belongs to
  // the add-on, not to the caller: closing the page that asked does not stop it.
  let pendingActivation = null
  // The outcome of the last install's activation, for job(): { id, activated, code }.
  let lastActivation = null
  // While a download runs, the snapshot (with its `progress`) is re-published once a second, so
  // a page can follow it through the update event instead of polling.
  let progressTimer = null
  const downloader = createDownloader({
    fetch: fetchImpl,
    statfs: deps.statfs,
    onChange: (view) => {
      if (downloader.busy()) {
        if (!progressTimer) {
          progressTimer = setInterval(() => publish(), deps.progressIntervalMs ?? 1000)
          progressTimer.unref?.()
        }
      } else {
        if (progressTimer) clearInterval(progressTimer)
        progressTimer = null
        if (pendingActivation && view?.id === pendingActivation && view.state !== 'done') {
          lastActivation = { id: pendingActivation, activated: false, code: view.state === 'cancelled' ? 'cancelled' : 'download-failed' }
          pendingActivation = null
        }
      }
      publish()
    },
  })

  engine.onChange(() => publish())

  // ---------------------------------------------------------------- choices

  function effectiveActive() {
    if (settings.model.activeModel !== 'none') return { id: settings.model.activeModel, pinned: true }
    return { id: getModel(stateCache.active) ? stateCache.active : null, pinned: false }
  }

  function effectiveOrigin() {
    if (settings.download.origin !== 'auto') return { value: settings.download.origin, pinned: true }
    const stored = stateCache.origin
    return { value: stored && parseOrigin(stored) ? stored : 'auto', pinned: false }
  }

  // Cheap check for lists, snapshots and `use`: the model file exists with its exact size. Only a
  // verified download is renamed to that name; a file put there by hand is hashed before it runs.
  function isInstalled(model) {
    try {
      const info = fs.statSync(modelFile(modelsDir, model))
      return info.isFile() && info.size === model.sizeBytes
    } catch {
      return false
    }
  }

  // The file is the one a verified download (or an earlier hash check) recorded.
  function isVerified(model) {
    const record = stateCache.installed[model.id]
    if (!record || record.sha256 !== model.sha256) return false
    try {
      const info = fs.statSync(modelFile(modelsDir, model))
      return info.isFile() && info.size === record.sizeBytes && info.mtimeMs === record.mtimeMs
    } catch {
      return false
    }
  }

  // Full check before a model runs. A file whose size and modification time match the record of its
  // verified download is trusted; any other file is hashed again (a copied or replaced file).
  async function verifyInstalled(model) {
    const file = modelFile(modelsDir, model)
    let info
    try {
      info = await fsp.stat(file)
    } catch {
      return 'not-installed'
    }
    if (!info.isFile() || info.size !== model.sizeBytes) return 'file-corrupt'
    const record = stateCache.installed[model.id]
    if (record && record.sha256 === model.sha256 && record.sizeBytes === info.size && record.mtimeMs === info.mtimeMs) return 'ok'
    activation = { id: model.id, status: 'verifying', error: null }
    publish()
    let sha256
    try {
      ;({ sha256 } = await hashFile(file))
    } catch {
      return 'file-corrupt'
    }
    if (sha256 !== model.sha256) return 'file-corrupt'
    stateCache = await store.update((s) => {
      s.installed[model.id] = { sha256, sizeBytes: info.size, mtimeMs: info.mtimeMs, verifiedAt: new Date().toISOString() }
    })
    return 'ok'
  }

  // ---------------------------------------------------------------- the service

  function snapshot() {
    const active = effectiveActive()
    const model = active.id ? getModel(active.id) : null
    const eng = engine.status()
    let state
    let error = null
    if (!active.id) state = 'no-model'
    else if (!model) {
      state = 'error'
      error = 'unknown-model'
    } else if (activation.id === model.id && activation.status === 'error') {
      state = 'error'
      error = activation.error
    } else if (activation.id !== model.id || activation.status !== 'ok' || eng.modelId !== model.id) state = 'loading'
    else if (eng.phase === 'failed') {
      state = 'error'
      error = eng.error
    } else if (eng.phase === 'ready' || eng.phase === 'idle' || eng.everReady) state = 'ready'
    else state = 'loading'
    const fe = front.endpoint()
    const usable = (state === 'ready' || state === 'loading') && fe !== null
    return deepFreeze({
      version: SERVICE_VERSION,
      state,
      error,
      url: usable ? fe.url : null,
      key: usable ? fe.key : null,
      model: model?.id ?? null,
      dimensions: model?.dimensions ?? null,
      maxInputTokens: model ? (eng.modelId === model.id && eng.maxInputTokens) || model.contextTokens : null,
      overflow: settings.model.overflow,
      fingerprint: model ? fingerprint(model) : null,
      calibration: model ? { preset: model.preset, parity: model.parity, ...model.calibration } : null,
      loaded: state === 'ready' && eng.phase === 'ready',
      downloading: downloader.progress()?.id ?? null,
      // What job() returns, as data (the name `job` is the method's).
      progress: jobView(),
      models: listedModels({ all: true }).map((m) => ({
        id: m.id, title: m.title, installed: isInstalled(m), active: m.id === model?.id, sizeBytes: m.sizeBytes, dimensions: m.dimensions,
        // A plain string: the base plugin's reader (src/tiers.js, readEmbed) takes `license` as text.
        license: m.license.name, licenseUrl: m.license.url, parity: m.parity, status: m.status,
        requiresAcceptance: Boolean(m.requiresAcceptance),
      })),
      // The control methods (version 2). Functions are left out of the change comparison in publish().
      ...control,
    })
  }

  /** The running download, else the last finished one, as plain numbers and codes; null before any. */
  function jobView() {
    const p = downloader.progress()
    if (p) {
      return {
        id: p.id, state: p.state, bytes: p.bytes, total: p.total, speed: p.speed, source: p.source,
        activateWhenDone: pendingActivation === p.id, error: null, activated: null,
      }
    }
    const last = downloader.last()
    if (!last) return null
    const model = getModel(last.id)
    const outcome = lastActivation?.id === last.id ? lastActivation : null
    return {
      id: last.id, state: last.state, bytes: last.state === 'done' ? model?.sizeBytes ?? null : null, total: model?.sizeBytes ?? null,
      speed: 0, source: last.source ?? null, activateWhenDone: false,
      error: last.state === 'failed' ? last.reason ?? 'unknown' : null,
      activated: outcome ? outcome.activated : null,
    }
  }

  function provideService() {
    if (provided || disposed) return
    lastSnapshot = snapshot()
    try {
      provided = ctx.provide(SERVICE_NAME, lastSnapshot)
    } catch (error) {
      log.warn(`feiyu-memgate-embed: the service could not be provided (${kindOf(error)})`)
      provided = null
    }
  }

  function withdrawService() {
    if (!provided) return
    const dispose = provided
    provided = null
    lastSnapshot = null
    try {
      Promise.resolve(dispose()).catch(() => {})
    } catch {
      // already gone with the fiber
    }
  }

  /** Replace the service value when it changed, and announce it. */
  function publish() {
    if (!provided || disposed) return
    const next = snapshot()
    if (lastSnapshot && JSON.stringify(lastSnapshot) === JSON.stringify(next)) return
    lastSnapshot = next
    try {
      ctx.set(SERVICE_NAME, next)
    } catch (error) {
      log.warn(`feiyu-memgate-embed: the service value could not be replaced (${kindOf(error)})`)
    }
    try {
      ctx.emit(UPDATE_EVENT, next)
    } catch {
      // a listener failure is the listener's problem
    }
  }

  // ---------------------------------------------------------------- the active model

  async function applyActive({ forceStart = false } = {}) {
    const active = effectiveActive()
    const model = active.id ? getModel(active.id) : null
    if (!settings.modules.engine || !active.id) {
      activation = { id: active.id, status: 'none', error: null }
      await engine.stop()
      await front.stop()
      return
    }
    if (!model) {
      activation = { id: active.id, status: 'error', error: 'unknown-model' }
      await engine.stop()
      await front.stop()
      return
    }
    const wasRunning = engine.spec()?.modelId === model.id && engine.status().phase !== 'idle'
    const result = await verifyInstalled(model)
    if (disposed) return
    if (result !== 'ok') {
      activation = { id: model.id, status: 'error', error: result }
      await engine.stop()
      await front.stop()
      return
    }
    try {
      await front.start()
    } catch (error) {
      log.warn(`feiyu-memgate-embed: the local endpoint could not start (${kindOf(error)})`)
      activation = { id: model.id, status: 'error', error: 'endpoint-failed' }
      await engine.stop()
      return
    }
    activation = { id: model.id, status: 'ok', error: null }
    await engine.load({
      modelId: model.id, modelPath: modelFile(modelsDir, model), dimensions: model.dimensions,
      contextTokens: model.contextTokens, threads: settings.model.threads,
    }, { start: forceStart || wasRunning || settings.model.autoStart })
  }

  function setIdleTimer() {
    if (idleTimer) clearInterval(idleTimer)
    idleTimer = null
    const seconds = settings.model.idleUnloadSeconds
    if (!settings.modules.engine || seconds <= 0) return
    const now = deps.now ?? Date.now
    idleTimer = setInterval(() => {
      const status = engine.status()
      if (status.phase !== 'ready') return
      const { inflight, lastActivity } = front.activity()
      const since = Math.max(lastActivity, status.readyAt ?? 0)
      if (inflight === 0 && now() - since >= seconds * 1000) {
        engine.unload().then(() => publish(), () => {})
      }
    }, Math.min(30000, Math.max(250, (seconds * 1000) / 4)))
    idleTimer.unref?.()
  }

  // ---------------------------------------------------------------- the command

  const api = {
    status() {
      const snap = lastSnapshot ?? snapshot()
      return {
        engineOn: settings.modules.engine, state: snap.state, error: snap.error, model: snap.model, loaded: snap.loaded,
        url: snap.url, activePinned: effectiveActive().pinned, download: downloader.progress(), lastDownload: downloader.last(),
        origin: effectiveOrigin(), modelsDir: displayPath(modelsDir), maxInputTokens: snap.maxInputTokens,
        overflow: settings.model.overflow, truncatedInputs,
      }
    },
    list({ all = false } = {}) {
      const active = effectiveActive().id
      const downloading = downloader.progress()?.id
      return listedModels({ all }).map((m) => ({
        id: m.id, sizeBytes: m.sizeBytes, dimensions: m.dimensions, license: m.license, parity: m.parity, status: m.status,
        installed: isInstalled(m), active: m.id === active, downloading: m.id === downloading, requiresAcceptance: Boolean(m.requiresAcceptance),
      }))
    },
    /** Start a download. activate: make the model active once it is verified (the one-click install). */
    async add(id, { accept = false, activate = false } = {}) {
      const model = getModel(id)
      if (!model) return { ok: false, code: 'unknown' }
      // Nothing of a model under licence terms is fetched, not even a probe, before the acceptance.
      if (model.requiresAcceptance && !accept) return { ok: false, code: 'needs-acceptance', model }
      if (downloader.busy()) {
        // An install of the model already downloading only adds the activation to that job.
        if (activate && downloader.progress()?.id === id) {
          pendingActivation = id
          publish()
          return { ok: true, model, joined: true, sources: [], resumeFrom: 0, accepted: null }
        }
        return { ok: false, code: 'busy', current: downloader.progress() }
      }
      stateCache = store.read()
      if (isVerified(model)) return { ok: false, code: 'installed', model }
      let accepted = null
      if (model.requiresAcceptance) {
        accepted = { name: model.requiresAcceptance.name, url: model.requiresAcceptance.url, acceptedAt: new Date().toISOString() }
        stateCache = await store.update((s) => {
          s.acceptances[model.id] = accepted
        })
      }
      const { origins } = parseOrigin(effectiveOrigin().value)
      let resumeFrom = 0
      try {
        resumeFrom = fs.statSync(partFile(modelsDir, model)).size
      } catch {
        resumeFrom = 0
      }
      const targetDir = modelsDir
      try {
        if (activate) pendingActivation = id
        else if (pendingActivation !== null) pendingActivation = null
        downloader.start(model, {
          modelsDir: targetDir,
          origins,
          onDone: async (m, result) => {
            if (targetDir !== modelsDir) return
            stateCache = await store.update((s) => {
              s.installed[m.id] = { sha256: result.sha256, sizeBytes: result.sizeBytes, mtimeMs: result.mtimeMs, verifiedAt: new Date().toISOString() }
            })
            if (pendingActivation === m.id) {
              pendingActivation = null
              // A model pinned in the settings still wins; the download is kept.
              if (settings.model.activeModel !== 'none' && settings.model.activeModel !== m.id) {
                lastActivation = { id: m.id, activated: false, code: 'pinned' }
              } else {
                stateCache = await store.update((s) => {
                  s.active = m.id
                })
                lastActivation = { id: m.id, activated: true, code: null }
                reconcile({ forceStart: true })
                return
              }
            }
            if (effectiveActive().id === m.id) reconcile()
          },
        })
      } catch (error) {
        if (activate) pendingActivation = null
        if (error instanceof DownloadBusyError) return { ok: false, code: 'busy', current: error.current }
        throw error
      }
      return { ok: true, model, sources: origins, resumeFrom: Math.min(resumeFrom, model.sizeBytes), accepted }
    },
    async use(id) {
      if (settings.model.activeModel !== 'none') return { ok: false, code: 'pinned', pinned: settings.model.activeModel }
      stateCache = store.read()
      if (id !== null) {
        const model = getModel(id)
        if (!model) return { ok: false, code: 'unknown', id }
        if (!isInstalled(model)) return { ok: false, code: 'not-installed', id }
      }
      stateCache = await store.update((s) => {
        s.active = id
      })
      reconcile({ forceStart: true })
      return { ok: true, id }
    },
    async remove(id) {
      const model = getModel(id)
      if (!model) return { ok: false, code: 'unknown', id }
      if (effectiveActive().id === id) return { ok: false, code: 'active', id }
      if (downloader.progress()?.id === id) return { ok: false, code: 'downloading', id }
      let freedBytes = 0
      let found = false
      for (const file of [modelFile(modelsDir, model), partFile(modelsDir, model)]) {
        try {
          const info = await fsp.stat(file)
          await fsp.rm(file, { force: true })
          freedBytes += info.size
          found = true
        } catch {
          // not there
        }
      }
      try {
        await fsp.rmdir(path.join(modelsDir, model.id))
      } catch {
        // not empty or not there
      }
      const hadRecord = Boolean(stateCache.installed[id])
      stateCache = await store.update((s) => {
        delete s.installed[id]
      })
      publish()
      if (!found && !hadRecord) return { ok: false, code: 'not-installed', id }
      return { ok: true, id, freedBytes }
    },
    cancel() {
      const progress = downloader.progress()
      if (!progress) return { ok: false }
      if (pendingActivation === progress.id) {
        lastActivation = { id: progress.id, activated: false, code: 'cancelled' }
        pendingActivation = null
      }
      downloader.cancel()
      return { ok: true, id: progress.id }
    },
    async origin(value) {
      stateCache = store.read()
      if (value === undefined) return { ok: true, origin: effectiveOrigin() }
      if (settings.download.origin !== 'auto') return { ok: false, code: 'pinned', origin: effectiveOrigin() }
      const parsed = parseOrigin(value)
      if (!parsed) return { ok: false, code: 'invalid', value }
      stateCache = await store.update((s) => {
        s.origin = parsed.kind === 'auto' ? null : parsed.kind === 'custom' ? parsed.origins[0] : parsed.kind
      })
      return { ok: true, origin: effectiveOrigin() }
    },
  }

  // ---------------------------------------------------------------- the control methods

  // For other plugins in the same host (the base plugin's settings page drives these): the same
  // operations as the command, as plain results. They never throw, never log more than ids and
  // codes, and are reachable only through the service object in the host process: no tool, no route.
  const MESSAGES = Object.freeze({
    'unknown-model': 'no such model in the registry',
    'needs-acceptance': 'this model needs its licence accepted: pass acceptLicence with the licence name',
    busy: 'another download is running',
    'already-installed': 'the model is already downloaded and verified',
    'not-installed': 'the model has not been downloaded',
    pinned: "the add-on's settings pin another active model; change the setting to choose here",
    active: 'the model is active; choose another one first',
    downloading: 'the model is being downloaded; cancel it first',
    'no-job': 'no download is running',
    'invalid-origin': 'the origin must be auto, huggingface, hf-mirror or an http(s) origin',
    'origin-pinned': "the add-on's settings pin the download origin",
    unavailable: 'the add-on is stopping',
    failed: 'the operation failed',
  })
  const refuse = (code, extra = {}) => ({ ok: false, code, message: MESSAGES[code] ?? MESSAGES.failed, ...extra })

  function licenceAccepted(model, acceptLicence) {
    const terms = model.requiresAcceptance
    if (!terms) return true
    return typeof acceptLicence === 'string' && (acceptLicence === terms.name || acceptLicence === terms.url)
  }

  function modelRow(m, active, job) {
    return {
      id: m.id, title: m.title, tier: m.tier, sizeBytes: m.sizeBytes, dimensions: m.dimensions,
      memoryMB: m.memoryMB ? { loaded: m.memoryMB.loaded, peak: m.memoryMB.peak } : null,
      maxInputTokens: m.contextTokens, license: m.license.name, licenseUrl: m.license.url,
      requiresAcceptance: m.requiresAcceptance ? { name: m.requiresAcceptance.name, url: m.requiresAcceptance.url } : null,
      parity: m.parity, status: m.status, installed: isInstalled(m), active: m.id === active,
      job: job && job.id === m.id ? job : null,
    }
  }

  // Wrap an operation: a closed add-on, a bad argument or an unexpected failure is a result, not a throw.
  const guarded = (name, fn) => async (...args) => {
    if (disposed) return refuse('unavailable')
    try {
      return await fn(...args)
    } catch (error) {
      log.warn(`feiyu-memgate-embed: ${name} failed (${kindOf(error)})`)
      return refuse('failed')
    }
  }

  function jobStarted(model) {
    return jobView() ?? { id: model.id, state: 'starting', bytes: 0, total: model.sizeBytes, speed: 0, source: null, activateWhenDone: pendingActivation === model.id, error: null, activated: null }
  }

  async function startDownload(id, options, activate) {
    const model = typeof id === 'string' ? getModel(id) : undefined
    if (!model) return refuse('unknown-model')
    const acceptLicence = options && typeof options === 'object' ? options.acceptLicence : undefined
    if (!licenceAccepted(model, acceptLicence)) {
      return refuse('needs-acceptance', { licence: { name: model.requiresAcceptance.name, url: model.requiresAcceptance.url } })
    }
    const r = await api.add(model.id, { accept: Boolean(model.requiresAcceptance), activate })
    if (r.ok) return { ok: true, id: model.id, job: jobStarted(model), acceptedAt: r.accepted?.acceptedAt ?? null }
    if (r.code === 'busy') return refuse('busy', { job: jobView() })
    if (r.code === 'installed') return refuse('already-installed')
    if (r.code === 'needs-acceptance') return refuse('needs-acceptance')
    return refuse(r.code === 'unknown' ? 'unknown-model' : 'failed')
  }

  async function activate(id) {
    if (settings.model.activeModel !== 'none') {
      // The pinned model is active already; any other choice is the setting's to make.
      if (id === settings.model.activeModel) return { ok: true, active: id }
      return refuse('pinned', { pinned: settings.model.activeModel })
    }
    const r = await api.use(id)
    if (r.ok) return { ok: true, active: r.id }
    if (r.code === 'pinned') return refuse('pinned', { pinned: r.pinned })
    if (r.code === 'not-installed') return refuse('not-installed')
    return refuse(r.code === 'unknown' ? 'unknown-model' : 'failed')
  }

  const control = Object.freeze({
    /** Every registry model, with what is installed and active, and the running job on its row. */
    list: guarded('list', async () => {
      const active = effectiveActive()
      const job = jobView()
      const running = job && downloader.progress() ? job : null
      return {
        ok: true, active: active.id, pinned: active.pinned ? active.id : null,
        models: listedModels({ all: true }).map((m) => modelRow(m, active.id, running)),
      }
    }),
    /** Start downloading a model in the background; returns at once with the job. */
    add: guarded('add', (id, options) => startDownload(id, options, false)),
    /** Make a downloaded model active (null: none). */
    use: guarded('use', async (id) => {
      if (id !== null && (typeof id !== 'string' || !getModel(id))) return refuse('unknown-model')
      return activate(id)
    }),
    /** Download if needed, then make it active once verified: the one-click path. */
    install: guarded('install', async (id, options) => {
      const model = typeof id === 'string' ? getModel(id) : undefined
      if (!model) return refuse('unknown-model')
      if (settings.model.activeModel !== 'none' && settings.model.activeModel !== model.id) {
        return refuse('pinned', { pinned: settings.model.activeModel })
      }
      const acceptLicence = options && typeof options === 'object' ? options.acceptLicence : undefined
      if (!licenceAccepted(model, acceptLicence)) {
        return refuse('needs-acceptance', { licence: { name: model.requiresAcceptance.name, url: model.requiresAcceptance.url } })
      }
      stateCache = store.read()
      if (isVerified(model)) {
        const r = await activate(model.id)
        return r.ok ? { ok: true, id: model.id, job: null, active: r.active } : r
      }
      // Not downloaded, or a file without a verification record: the download job checks an existing
      // file first and finishes without the network when it is the right one.
      return startDownload(model.id, options, true)
    }),
    /** Stop the running download; its downloaded part is kept for a resume. */
    cancel: guarded('cancel', async () => {
      const r = api.cancel()
      return r.ok ? { ok: true, id: r.id } : refuse('no-job')
    }),
    /** Delete a downloaded model that is not active and not downloading. */
    remove: guarded('remove', async (id) => {
      if (typeof id !== 'string' || !getModel(id)) return refuse('unknown-model')
      const r = await api.remove(id)
      if (r.ok) return { ok: true, id, freedBytes: r.freedBytes }
      return refuse(['active', 'downloading', 'not-installed'].includes(r.code) ? r.code : 'failed')
    }),
    /** The running download, else the last finished one, else null. */
    job: guarded('job', async () => ({ ok: true, job: jobView() })),
    /** Set the download origin: auto, huggingface, hf-mirror or an http(s) origin. */
    setOrigin: guarded('setOrigin', async (value) => {
      if (typeof value !== 'string') return refuse('invalid-origin')
      const r = await api.origin(value)
      if (r.ok) return { ok: true, origin: { value: r.origin.value, pinned: r.origin.pinned } }
      return refuse(r.code === 'pinned' ? 'origin-pinned' : 'invalid-origin')
    }),
  })

  function setCommand(on) {
    if (on && !commandFiber && !disposed) {
      const register = (child) => {
        child.commands.register(createCommandDefinition(api, () => uiLanguage(ctx), registry))
      }
      try {
        commandFiber = ctx.inject(['commands'], register)
      } catch (error) {
        log.warn(`feiyu-memgate-embed: the command could not be registered (${kindOf(error)})`)
        commandFiber = null
      }
    } else if (!on && commandFiber) {
      const fiber = commandFiber
      commandFiber = null
      try {
        Promise.resolve(fiber.dispose?.()).catch(() => {})
      } catch {
        // already gone
      }
    }
  }

  // ---------------------------------------------------------------- reconcile

  function schedule(task) {
    const run = op.then(() => (disposed ? undefined : task()))
    op = run.catch((error) => log.warn(`feiyu-memgate-embed: ${kindOf(error)}`))
    return run
  }

  /** Bring every part in line with the settings and the state file. */
  function reconcile({ forceStart = false } = {}) {
    return schedule(async () => {
      settings = readConfig(config)
      if (settings.invalid.length && !warnedInvalid) {
        warnedInvalid = true
        log.warn(`feiyu-memgate-embed: invalid setting ${settings.invalid.join(', ')}; using the default`)
      }
      const nextDir = resolveDataDir(settings.download.dataDir, ctx, env)
      if (nextDir !== dataDir) {
        downloader.cancel()
        await downloader.settled().catch(() => {})
        await engine.stop()
        await front.stop()
        dataDir = nextDir
        modelsDir = path.join(dataDir, 'models')
        store = createStateStore(dataDir)
        writeLog = openLog(path.join(dataDir, LOG_FILE))
      }
      stateCache = store.read()
      setCommand(settings.modules.command)
      if (settings.modules.engine) provideService()
      else withdrawService()
      await applyActive({ forceStart })
      setIdleTimer()
      publish()
    })
  }

  /** Stop everything; resolves once the worker is gone and the download has stopped. */
  async function dispose() {
    if (disposed) return
    disposed = true
    if (idleTimer) clearInterval(idleTimer)
    idleTimer = null
    pendingActivation = null
    setCommand(false)
    await Promise.allSettled([downloader.dispose(), engine.stop(), front.stop()])
    if (progressTimer) clearInterval(progressTimer)
    progressTimer = null
    withdrawService()
  }

  return {
    reconcile,
    dispose,
    api,
    snapshot: () => lastSnapshot ?? snapshot(),
    engine,
    front,
    downloader,
    settled: () => op,
    paths: () => ({ dataDir, modelsDir, stateFile: store.file, logFile: path.join(dataDir, LOG_FILE) }),
    /** Provide the service now (synchronously), before the first reconcile settles. */
    start() {
      if (readConfig(config).modules.engine) provideService()
      return reconcile()
    },
  }
}

/** The entry used by index.js: build, reconcile now and on every live settings change, clean up on unload. */
export function applyAddon(ctx, config, deps) {
  const addon = createAddon(ctx, config, deps)
  ctx.on('loader/volatile-update', () => addon.reconcile())
  if (typeof ctx.effect === 'function') ctx.effect(() => () => addon.dispose())
  else ctx.on('dispose', () => addon.dispose())
  addon.start()
  return addon
}
