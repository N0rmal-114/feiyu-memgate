// Local embedding models in one click: the chain behind the model list of the settings page and the
// /memgate models, install, use, remove and cancel commands.
//
//   click -> (add-on absent) the host's plugin manager installs dsh-feiyu-memgate-embed by its npm
//            name (pluginManager.installBundle: the host's compatibility check, progress events,
//            cancellation and Plugins page apply)
//         -> the add-on's service appears -> its install(id, { acceptLicence }) downloads, verifies
//            and activates the model
//         -> the add-on reports the model ready -> the `auto` tier becomes `local` and the copy of the
//            keyword store runs (src/plugin.js reconcile; nothing here touches the gateway).
//
// Only a person starts it: the owner-only route of the settings page and the /memgate command call
// install(). The tools the model can call never reach this module. Every step is written to
// <data folder>/model-setup.json, so a closed or reloaded page or a host restart in the middle finds
// the chain where it was (resume()); the page shows that state.
//
// The add-on is another package: only its service name and shape couple the two (version 2 of its
// service, docs/SERVICE.md of dsh-feiyu-memgate-embed: list(), install(id, { acceptLicence }),
// cancel(), use(id), remove(id), job(), results { ok, code, message }). Every value it returns is
// checked before use; nothing here imports it.
//
// Host APIs (DeepSeek Harness 0.2.2-rc.3 source; the installed 0.2.2-rc.1 has the same calls):
//   ctx.get('pluginManager')            packages/boot/plugin-manager/src/index.ts:317-326 (rc.1
//                                       dsh-plugin-manager lib/index.js:1941), present in profiles
//                                       built on dsh-base (bundle/base/cordis.patch.yml:20-22)
//   installBundle(spec, { requestId, enabled, approvedBuilds })  index.ts:976-1121 -> ChangeResult
//                                       (types.ts:171-200)
//   setBundleEnabled(name, true)        index.ts:955-960: selects an installed bundle and starts it
//   removeBundle(name)                  index.ts:1156-1188
//   listBundles()                       index.ts:506-606: { name, installed (a dependency of the
//                                       profile), enabled (selected), availability, error }
//   waitForInstall(requestId) / cancelInstall(requestId)  index.ts:1128, :1138
//   'plugin-manager/install-state' { requestId, phase }   types.ts:440-477
//
// What an installation leaves behind when pnpm stops an install script (node-llama-cpp has one;
// pnpm 11 refuses to finish without a decision), read from the source:
//   - installBundle fails with pendingBuilds; it restores package.json and pnpm-lock.yaml (index.ts
//     :1073-1102), so the add-on is neither a dependency nor selected and nothing loads; it writes
//     `<name>: set this to true or false` under allowBuilds in the profile's pnpm-workspace.yaml and
//     keeps it (build-approval.ts:63-71). Downloaded files may stay in node_modules.
//   - "allow": installBundle again with approvedBuilds sets those keys to true (build-approval.ts
//     :90-96, refused as stale-approval unless they are still undecided), pnpm succeeds, the package is
//     new to package.json and installBundle selects it (index.ts:1110-1117): the bundle is active.
//   - "deny": the host has no call for it. With `<name>: false` in that file, the same installBundle
//     succeeds and selects the package for the same reason. (The CLI's `dsh plugin add` differs: it
//     does not restore package.json, and its activation only selects packages new to package.json
//     (operations.ts:96-128), which is how "installed but not selected" arises; that state is
//     repaired with setBundleEnabled, or removeBundle and a new installBundle when the files are gone.)
//   - an unanswered question changes nothing more: the undecided key is read only by a later
//     installation (pendingBuilds lists every undecided key) and by an approval.
import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { ADDON_PACKAGE, CATALOGUE, TIER_LABELS, TIER_NOTES, TIERS_LINK, offeredModels } from './catalogue.js'
import { readEmbed } from './tiers.js'

export const SETUP_FILE = 'model-setup.json'
export const ADDON_CONTRACT = 2
// idle, then: addon (the host installs the add-on) -> builds (waiting for the user's decision on an
// install script pnpm stopped) -> addon-starting | restart -> model (downloading) | paused (a host
// restart interrupted the download: resumable) -> activating -> done; or failed.
const PHASES = new Set(['idle', 'addon', 'builds', 'addon-starting', 'restart', 'model', 'paused', 'activating', 'done', 'failed'])
const BUSY = new Set(['addon', 'addon-starting', 'model', 'activating'])
const ID = /^[a-z0-9][a-z0-9._-]{0,79}$/
const CODE = /^[a-z][a-z0-9-]{0,39}$/

// ------------------------------------------------------------------ what each failure means

/** Every state the page and the command explain, with what the user can do about it. */
export const FAILURES = Object.freeze({
  network: {
    en: ['The download source could not be reached (no network, or the mirror does not answer).', 'Check the connection. In the embedding add-on’s settings you can pick another download source (hf-mirror or your own mirror); then press the button again: the download resumes where it stopped.'],
    zh: ['无法连接下载来源（没有网络，或镜像无响应）。', '请检查网络连接。可在向量插件的设置中更换下载来源（hf-mirror 或自建镜像），然后再次点击按钮，下载将从中断处继续。'],
  },
  'disk-full': {
    en: ['Not enough disk space for the model.', 'Free some space on the drive that holds the DSH data folder (the model size plus a margin), then press the button again.'],
    zh: ['磁盘空间不足，无法存放该模型。', '请在存放 DSH 数据目录的磁盘上释放空间（模型大小再加一定余量），然后再次点击按钮。'],
  },
  integrity: {
    en: ['The downloaded file did not pass verification (its size or checksum is wrong).', 'Press the button again to download it anew. If it happens again, choose another download source in the embedding add-on’s settings.'],
    zh: ['下载的文件未通过校验（大小或校验和不符）。', '请再次点击按钮重新下载。若再次出现，请在向量插件的设置中更换下载来源。'],
  },
  'runtime-unavailable': {
    en: ['The embedding add-on cannot load its engine on this computer (its status shows the error runtime-missing).', 'On Windows this almost always means the Microsoft Visual C++ 2015-2022 Redistributable (x64) is missing: install it from Microsoft (vc_redist.x64.exe, https://aka.ms/vs/17/release/vc_redist.x64.exe), then restart DSH. Elsewhere the engine has no CPU build for this platform: keep the keyword tier, or use an embeddings service through the endpoint tier.'],
    zh: ['向量插件无法在本机加载其运行引擎（状态显示错误 runtime-missing）。', '在 Windows 上，这几乎都是因为缺少 Microsoft Visual C++ 2015-2022 运行库（x64）：请从微软官网安装（vc_redist.x64.exe，https://aka.ms/vs/17/release/vc_redist.x64.exe），然后重启 DSH。在其他系统上，引擎没有适用于该平台的 CPU 版本：请继续使用关键词检索，或通过 endpoint 检索方式接入向量服务。'],
  },
  'python-missing': {
    en: ['Python 3.11 or newer was not found, so the managed memory gateway is off and a local model cannot be used for memory yet.', 'Install Python 3.11 or newer (or set gateway.python to its path); the gateway starts by itself afterwards.'],
    zh: ['未找到 Python 3.11 或更高版本，内置网关未运行，暂时无法将本地模型用于记忆。', '请安装 Python 3.11 或更高版本（或将 gateway.python 设为其路径），网关随后会自动启动。'],
  },
  'install-cancelled': {
    en: ['The installation of the embedding add-on was declined or cancelled; nothing was installed.', 'Press the button again when you want to install it.'],
    zh: ['向量插件的安装已被拒绝或取消，未安装任何内容。', '需要时可再次点击按钮安装。'],
  },
  pinned: {
    en: ['The embedding add-on’s own settings pin a model (activeModel), and that setting wins.', 'Set activeModel back to none on the embedding add-on’s settings page, or use the model it pins.'],
    zh: ['向量插件自身的设置中指定了模型（activeModel），以该设置为准。', '请在向量插件的设置页将 activeModel 改回 none，或使用其指定的模型。'],
  },
  'addon-incompatible': {
    en: ['This version of the embedding add-on does not support the running DSH version.', 'Update DSH, or wait for an add-on version that supports it; the Plugins page shows the details.'],
    zh: ['当前版本的向量插件不支持正在运行的 DSH 版本。', '请升级 DSH，或等待支持该版本的插件更新；详情见插件页面。'],
  },
  'build-approval': {
    en: ['The host still waits for a decision on an install script, or the decision went stale.', 'Press the button again and answer the install-script question that follows.'],
    zh: ['宿主仍在等待对安装脚本的决定，或之前的决定已失效。', '请再次点击按钮，并回答随后出现的安装脚本问题。'],
  },
  'builds-denied': {
    en: ['You did not allow the install script, so nothing was installed and nothing loads.', 'DSH has no button to record a refusal; record it in a file. Open {file}. Under “allowBuilds:”, DSH wrote the line “{name}: set this to true or false”; change it to “{name}: false” and save. Then press “Download and enable” again (or run /memgate install with --confirm): the add-on installs with the script skipped and is switched on. Or press the button again and allow the script.'],
    zh: ['你未允许安装脚本，因此未安装任何内容，也不会加载。', 'DSH 没有记录“拒绝”的按钮，需在文件中手动记录。请打开 {file}，在“allowBuilds:”下找到 DSH 写入的“{name}: set this to true or false”，改为“{name}: false”并保存。然后再次点击“下载并启用”（或执行带 --confirm 的 /memgate install）：插件将跳过该脚本完成安装并启用。也可再次点击按钮，选择允许脚本。'],
  },
  'addon-enable-failed': {
    en: ['The embedding add-on is installed but DSH could not switch it on.', 'The Plugins page shows why (for example a version this DSH does not support). Fix that, or remove dsh-feiyu-memgate-embed there, then press the button again.'],
    zh: ['向量插件已安装，但 DSH 无法启用。', '原因见插件页面（例如 DSH 不支持的版本）。请先处理，或在插件页面移除 dsh-feiyu-memgate-embed，然后再次点击按钮。'],
  },
  'confirm-required': {
    en: ['Installing the embedding add-on needs your explicit confirmation.', 'Confirm on the settings page, or repeat the command with --confirm.'],
    zh: ['安装向量插件需要你明确确认。', '请在设置页面确认，或在命令后加上 --confirm 再次执行。'],
  },
  'builds-pending': {
    en: ['The host waits for your decision on an install script.', 'Answer the install-script question on the settings page (or with /memgate builds allow|deny).'],
    zh: ['宿主正在等待你对安装脚本做出决定。', '请在设置页面回答安装脚本的问题（或使用 /memgate builds allow|deny）。'],
  },
  'restart-required': {
    en: ['The embedding add-on is installed and starts after DSH restarts.', 'Restart DSH; the setup continues by itself afterwards.'],
    zh: ['向量插件已安装，DSH 重启后才会启动。', '请重启 DSH，之后设置会自动继续。'],
  },
  'addon-not-found': {
    en: ['The package dsh-feiyu-memgate-embed was not found in the package registry.', 'Check the registry and network settings of the Plugins page, or try again later.'],
    zh: ['包仓库中未找到 dsh-feiyu-memgate-embed。', '请检查插件页面的仓库和网络设置，或稍后重试。'],
  },
  'addon-install-failed': {
    en: ['The host could not install the embedding add-on.', 'The Plugins page has the installation log; fix what it names and press the button again.'],
    zh: ['宿主未能安装向量插件。', '插件页面提供安装日志；请按日志处理后再次点击按钮。'],
  },
  'no-plugin-manager': {
    en: ['This DSH profile has no plugin manager, so the add-on cannot be installed from here.', 'Install dsh-feiyu-memgate-embed with the dsh plugin command or the profile’s package.json, then come back.'],
    zh: ['当前 DSH 配置没有插件管理器，无法在此安装向量插件。', '请使用 dsh plugin 命令或配置的 package.json 安装 dsh-feiyu-memgate-embed，然后返回此处。'],
  },
  'addon-outdated': {
    en: ['The installed embedding add-on is too old for one-click setup.', 'Update dsh-feiyu-memgate-embed on the Plugins page, or use its /memgate-embed command.'],
    zh: ['已安装的向量插件版本过旧，不支持一键设置。', '请在插件页面更新 dsh-feiyu-memgate-embed，或使用其 /memgate-embed 命令。'],
  },
  'addon-not-started': {
    en: ['The embedding add-on is installed but has not started.', 'Check on the Plugins page that dsh-feiyu-memgate-embed is turned on, then press the button again.'],
    zh: ['向量插件已安装，但未启动。', '请在插件页面确认 dsh-feiyu-memgate-embed 已开启，然后再次点击按钮。'],
  },
  'needs-acceptance': {
    en: ['This model’s weights come under a licence you must accept first.', 'Read the licence, tick the box next to the model, then press the button.'],
    zh: ['该模型的权重适用一份需先同意的许可。', '请阅读许可，勾选模型旁的复选框，再点击按钮。'],
  },
  busy: {
    en: ['Another model is being set up.', 'Wait for it to finish, or cancel it first.'],
    zh: ['另一个模型正在设置。', '请等待其完成，或先取消。'],
  },
  interrupted: {
    en: ['The installation was interrupted (DSH stopped in the middle).', 'Press the button again; what is already done is kept.'],
    zh: ['安装被中断（DSH 中途停止）。', '请再次点击按钮，已完成的部分会保留。'],
  },
  storage: {
    en: ['The model file could not be written.', 'Check that the DSH data folder is writable, then press the button again.'],
    zh: ['无法写入模型文件。', '请确认 DSH 数据目录可写，然后再次点击按钮。'],
  },
  'model-failed': {
    en: ['The model could not be loaded.', 'Remove the model and download it again; if that does not help, choose another model.'],
    zh: ['模型加载失败。', '请删除该模型后重新下载；仍无法解决时，请更换模型。'],
  },
  cancelled: {
    en: ['The download was cancelled. The part already downloaded stays on disk.', 'Press the button to continue, or remove the model to delete the part.'],
    zh: ['下载已取消，已下载的部分保留在磁盘上。', '点击按钮可继续下载，或删除模型以清除这部分。'],
  },
  'unknown-model': {
    en: ['There is no such model in the list.', 'Pick one of the listed models.'],
    zh: ['列表中没有该模型。', '请从列表中选择模型。'],
  },
  'not-running': {
    en: ['Nothing is being installed or downloaded.', 'There is nothing to cancel.'],
    zh: ['没有正在进行的安装或下载。', '没有可取消的内容。'],
  },
  'too-late': {
    en: ['Too late to cancel: the host is applying the add-on.', 'Wait a moment; you can remove the add-on on the Plugins page afterwards.'],
    zh: ['已无法取消：宿主正在应用插件。', '请稍候；之后可在插件页面移除该插件。'],
  },
  active: {
    en: ['This model is in use.', 'Switch to another model before deleting it.'],
    zh: ['该模型正在使用。', '请先切换到其他模型，再删除。'],
  },
  downloading: {
    en: ['This model is downloading.', 'Cancel the download first.'],
    zh: ['该模型正在下载。', '请先取消下载。'],
  },
  'not-installed': {
    en: ['This model is not downloaded.', 'Use “Download and enable”.'],
    zh: ['该模型尚未下载。', '请点击“下载并启用”。'],
  },
  'addon-absent': {
    en: ['The embedding add-on is not installed yet.', 'Use “Download and enable” on a model: it installs the add-on first.'],
    zh: ['尚未安装向量插件。', '请在某个模型上点击“下载并启用”，将先安装向量插件。'],
  },
  unknown: {
    en: ['The step failed.', 'Press the button again; the Plugins page and the add-on’s /memgate-embed status have details.'],
    zh: ['该步骤失败。', '请再次点击按钮；详细信息见插件页面和向量插件的 /memgate-embed status。'],
  },
})

/**
 * What the person confirms before the add-on is installed ({model} and {size} are filled in). The
 * sizes are the add-on author's figures for its dependency node-llama-cpp.
 */
export const ADDON_CONFIRM = Object.freeze({
  en: 'This installs the plugin dsh-feiyu-memgate-embed from npm through the DSH plugin manager (it checks that the plugin supports this DSH first), then downloads {model} ({size}) and enables it. The plugin adds a dependency with a native binary (node-llama-cpp): about 90 MB on disk for the CPU build, up to about 740 MB on Windows and Linux x64, where pnpm also fetches the GPU builds. The model runs on the CPU only. On Windows the engine needs the Microsoft Visual C++ 2015-2022 runtime (x64), which most Windows installations already have.',
  zh: '将通过 DSH 的插件管理器从 npm 安装插件 dsh-feiyu-memgate-embed（先检查其是否支持当前 DSH），然后下载 {model}（{size}）并启用。该插件依赖含原生二进制文件的 node-llama-cpp：CPU 版本约占用 90 MB 磁盘；在 Windows 和 Linux x64 上，pnpm 还会获取 GPU 版本，最多约 740 MB。模型仅使用 CPU 运行。在 Windows 上，引擎需要 Microsoft Visual C++ 2015-2022 运行库（x64），大多数 Windows 系统已自带。',
})

/** The question when pnpm stopped an install script ({names} is filled in). */
export const BUILDS_NOTE = Object.freeze({
  en: 'The embedding add-on depends on node-llama-cpp, which has an install script, and pnpm stopped the installation until you decide; nothing is installed yet. Both answers work: according to the add-on’s author, the script downloads and builds nothing, and the engine runs with it skipped. Allowing installs again and records a lasting permission in this DSH profile for: {names}. Denying has no button in DSH: the page then names the line to change in the profile’s pnpm-workspace.yaml, after which you press the button again.',
  zh: '向量插件依赖 node-llama-cpp，它带有安装脚本，pnpm 已暂停安装，等待你做出决定；目前尚未安装任何内容。两种选择均可：据插件作者测试，该脚本不下载也不编译任何内容，跳过后引擎仍可运行。选择允许将重新安装，并在当前 DSH 配置中为以下项目留下长期许可：{names}。DSH 中没有“拒绝”按钮：页面会指明需在配置的 pnpm-workspace.yaml 中修改的行，修改后再次点击按钮。',
})

/**
 * A failure code as { code, title: { en, zh }, todo: { en, zh } } for the page and the command, with
 * {placeholders} filled from `values` (a value per language, or one for both).
 */
export function describeFailure(code, values = {}) {
  const known = Object.hasOwn(FAILURES, code) ? FAILURES[code] : FAILURES.unknown
  const put = (s, lang) => s.replace(/\{(\w+)\}/g, (m, k) => {
    const v = values[k]
    return v === undefined || v === null ? m : String(typeof v === 'object' ? v[lang] ?? v.en : v)
  })
  return {
    code: CODE.test(String(code)) ? code : 'unknown',
    title: { en: put(known.en[0], 'en'), zh: put(known.zh[0], 'zh') },
    todo: { en: put(known.en[1], 'en'), zh: put(known.zh[1], 'zh') },
  }
}

/** Both languages of a text with {placeholders} filled in. */
export function fill(text, values) {
  const one = (s) => s.replace(/\{(\w+)\}/g, (m, k) => (k in values ? String(values[k]) : m))
  return { en: one(text.en), zh: one(text.zh) }
}

/** The failure an add-on or download reason stands for. */
export function failureOf(reason) {
  const r = String(reason ?? '')
  if (['network', 'dns', 'timeout', 'certificate', 'http', 'origin-unreachable', 'mirror-unreachable', 'no-network', 'offline'].includes(r)) return 'network'
  if (['disk-full', 'no-space', 'enospc'].includes(r.toLowerCase())) return 'disk-full'
  if (['integrity', 'file-corrupt', 'verify-failed', 'checksum'].includes(r)) return 'integrity'
  if (['runtime-missing', 'runtime-unavailable', 'unsupported-platform'].includes(r)) return 'runtime-unavailable'
  if (['storage', 'permission'].includes(r)) return 'storage'
  if (['load-failed', 'crashed', 'endpoint-failed', 'bad-options', 'port-unavailable', 'not-installed'].includes(r)) return 'model-failed'
  if (Object.hasOwn(FAILURES, r)) return r
  return 'unknown'
}

/** The failure of a plugin manager ChangeResult that did not install the add-on. */
export function addonFailureOf(result) {
  const code = result?.error?.code
  if (code === 'incompatible-version' || result?.packageResult?.incompatible) return 'addon-incompatible'
  if (code === 'unsupported-platform') return 'runtime-unavailable'
  switch (result?.packageResult?.kind) {
    case 'network':
    case 'timeout':
      return 'network'
    case 'disk-full':
      return 'disk-full'
    case 'build-blocked':
      return 'build-approval'
    case 'not-found':
    case 'no-matching-version':
      return 'addon-not-found'
    case 'integrity':
      return 'integrity'
    case 'permission':
      return 'storage'
    default:
      return 'addon-install-failed'
  }
}

// ------------------------------------------------------------------ reading the add-on

function field(value, key) {
  try {
    return value?.[key]
  } catch {
    return undefined
  }
}
const text = (v, max = 300) => (typeof v === 'string' && v.length <= max ? v : '')
const count = (v) => (Number.isSafeInteger(v) && v >= 0 ? v : null)
const isObject = (v) => v !== null && (typeof v === 'object' || typeof v === 'function')

/** What the add-on's service offers: { version, oneClick, service }, or null when it is absent. */
export function addonContract(service) {
  if (!isObject(service)) return null
  const version = field(service, 'version')
  const has = (name) => typeof field(service, name) === 'function'
  return {
    version: Number.isInteger(version) ? version : null,
    oneClick: has('install') && has('list') && has('job') && has('use') && has('cancel') && has('remove'),
    service,
  }
}

// One call into the add-on, bounded in time; a throw or a hang becomes a result, never an exception.
async function invoke(service, method, args, timeoutMs) {
  let timer
  try {
    return await Promise.race([
      Promise.resolve().then(() => field(service, method).apply(service, args)),
      new Promise((resolve) => {
        timer = setTimeout(() => resolve({ ok: false, code: 'timeout' }), timeoutMs)
        timer.unref?.()
      }),
    ])
  } catch {
    return { ok: false, code: 'addon-error' }
  } finally {
    clearTimeout(timer)
  }
}

/**
 * An add-on result as { ok, code, message, active, licence }: unknown codes become 'unknown'.
 * `active` is the model a successful use() or immediate install() made active; `licence` comes with
 * needs-acceptance.
 */
export function readResult(value) {
  if (!isObject(value)) return { ok: false, code: 'unknown', message: '', active: null, licence: null }
  const ok = field(value, 'ok') === true
  const code = field(value, 'code')
  const active = field(value, 'active')
  return {
    ok,
    code: typeof code === 'string' && CODE.test(code) ? code : ok ? '' : 'unknown',
    message: text(field(value, 'message')),
    active: typeof active === 'string' && ID.test(active) ? active : null,
    licence: licenceOf(field(value, 'licence')),
  }
}

function licenceOf(value, url) {
  if (typeof value === 'string' && value) return { name: text(value, 200), url: /^https:\/\//.test(url) ? text(url, 500) : '' }
  if (isObject(value) && typeof field(value, 'name') === 'string') {
    const u = field(value, 'url')
    return { name: text(field(value, 'name'), 200), url: typeof u === 'string' && /^https:\/\//.test(u) ? text(u, 500) : '' }
  }
  return null
}

// The memory the add-on measured for a model process, { loaded, peak } in MB, or null when it has no
// measurement. Never estimated from the file size: the process holds the runtime and its context too
// (bge-small-zh-v1.5: a 48 MB file, about 190 MB in use).
function readMemory(value) {
  if (!isObject(value)) return null
  const mb = (v) => (Number.isFinite(v) && v > 0 && v < 1e6 ? Math.round(v) : null)
  const loaded = mb(field(value, 'loaded'))
  const peak = mb(field(value, 'peak'))
  return loaded === null && peak === null ? null : { loaded, peak }
}

/**
 * The add-on's list() as { pinned, rows }. Version 2 answers { ok, active, pinned, models: [row] }
 * with rows { id, title, tier, sizeBytes, memoryMB: { loaded, peak } | null, license, licenseUrl,
 * requiresAcceptance: { name, url } | null, status, installed, active, job }.
 */
export function readList(value) {
  const models = Array.isArray(value) ? value : isObject(value) ? field(value, 'models') : null
  const pinned = isObject(value) ? field(value, 'pinned') : null
  const rows = !Array.isArray(models) ? [] : models.slice(0, 50).filter(isObject).map((m) => {
    const id = field(m, 'id')
    const accept = field(m, 'requiresAcceptance')
    const languages = field(m, 'languages')
    return {
      id: typeof id === 'string' && ID.test(id) ? id : '',
      title: text(field(m, 'title'), 120),
      tier: text(field(m, 'tier'), 40),
      sizeBytes: count(field(m, 'sizeBytes')),
      memoryMB: readMemory(field(m, 'memoryMB')),
      languages: Array.isArray(languages) ? languages.filter((l) => typeof l === 'string' && l.length <= 20).slice(0, 10) : [],
      license: licenceOf(field(m, 'license'), field(m, 'licenseUrl')),
      requiresAcceptance: accept === true ? licenceOf(field(m, 'license'), field(m, 'licenseUrl')) : licenceOf(accept, field(m, 'licenseUrl')),
      status: field(m, 'status') === 'verified' ? 'verified' : field(m, 'status') === 'unverified' ? 'unverified' : '',
      installed: field(m, 'installed') === true,
      active: field(m, 'active') === true,
    }
  }).filter((m) => m.id)
  return { pinned: typeof pinned === 'string' && ID.test(pinned) ? pinned : null, rows }
}

/**
 * The add-on's job as { id, state, bytes, total, percent, reason, activated }, or null. Accepts the
 * job itself (the snapshot's `progress`) or job()'s { ok, job } answer. Version 2 jobs are
 * { id, state, bytes, total, speed, source, activateWhenDone, error, activated }.
 */
export function readJob(value) {
  if (!isObject(value)) return null
  const one = field(value, 'id') === undefined && field(value, 'job') !== undefined ? field(value, 'job') : value
  if (!isObject(one)) return null
  const id = field(one, 'id')
  if (typeof id !== 'string' || !ID.test(id)) return null
  const state = text(field(one, 'state'), 40)
  const bytes = count(field(one, 'bytes'))
  const total = count(field(one, 'total'))
  const percent = bytes !== null && total ? Math.max(0, Math.min(100, Math.floor((bytes * 100) / total))) : null
  const reason = field(one, 'error') ?? field(one, 'reason')
  return {
    id, state, bytes, total, percent,
    reason: typeof reason === 'string' && CODE.test(reason) ? reason : '',
    activated: field(one, 'activated') === true,
  }
}

// Job states after which nothing more happens (SERVICE.md: done, failed or cancelled).
const FINISHED = new Set(['done', 'failed', 'cancelled'])

/**
 * The install scripts pnpm stopped, as the plugin manager names them (ChangeResult.pendingBuilds:
 * exact allowBuilds keys, a package name or a git key). Kept only to show them and to approve exactly
 * these, when the user says so.
 */
export function buildNames(value) {
  if (!Array.isArray(value)) return []
  return value.filter((n) => typeof n === 'string' && n.length > 0 && n.length <= 300 && !/[\r\n]/.test(n)).slice(0, 20)
}

/**
 * The add-on in the plugin manager's listBundles(), or null when it is listed neither as a dependency
 * nor as selected: { installed (a dependency in the profile's package.json), files (its package is in
 * node_modules: availability is not 'missing'), enabled (selected in dsh.profile.bundles), error (the
 * listing's error code, or null) }. "Installed but not selected" is installed && !enabled.
 */
export function addonBundle(list) {
  if (!Array.isArray(list)) return null
  const entry = list.find((b) => isObject(b) && field(b, 'name') === ADDON_PACKAGE)
  if (!entry) return null
  const availability = field(entry, 'availability')
  const installed = field(entry, 'installed')
  const error = field(entry, 'error')
  const code = isObject(error) ? field(error, 'code') : undefined
  return {
    installed: typeof installed === 'boolean' ? installed : availability !== 'missing',
    files: availability !== 'missing',
    enabled: field(entry, 'enabled') === true,
    error: typeof code === 'string' && CODE.test(code) ? code : null,
  }
}

/** What a plugin manager refusal to switch the add-on on means for the person. */
export function enableFailureOf(code) {
  if (code === 'incompatible-version') return 'addon-incompatible'
  if (code === 'unsupported-platform') return 'runtime-unavailable'
  return 'addon-enable-failed'
}

// ------------------------------------------------------------------ the chain

/**
 * The model setup chain.
 *   addon()          the add-on's service value now (ctx.get), or undefined
 *   pluginManager()  the host's plugin manager service (ctx.get), or undefined
 *   dataDir()        the data folder the chain file lives in
 *   python()         (async) { found } for the managed gateway's interpreter, or null when no
 *                    managed gateway is in use
 *   profileDir()     the folder of the running DSH profile (profileContext.dir), or undefined: only
 *                    shown, to name the file a refused install script is recorded in
 *   log              { info, warn }: counts and codes only
 */
export function createModelSetup({
  addon, pluginManager, dataDir, python = () => null, profileDir = () => undefined, log = { info() {}, warn() {} },
  now = Date.now, newRequestId = () => `feiyu-memgate-${randomUUID()}`,
  callTimeoutMs = 15000, addonWaitMs = 180000, tickMs = 3000,
}) {
  // Read from disk on first use, not when the plugin is built.
  let chain = null
  let installState = null
  let ticker = null
  let waitTimer = null
  let advancing = null
  let again = false
  let disposed = false

  function file() {
    return path.join(dataDir(), SETUP_FILE)
  }

  function blank() {
    // `repairs`: how often this setup switched on, or reinstalled, an add-on that was installed but
    // not selected; bounded so a host that keeps refusing is reported, not retried forever.
    return { version: 1, target: null, licence: null, phase: 'idle', requestId: null, pendingBuilds: [], repairs: 0, error: null, updated: null }
  }

  function load() {
    try {
      const data = JSON.parse(fs.readFileSync(path.join(dataDir(), SETUP_FILE), 'utf8'))
      if (!data || data.version !== 1 || !PHASES.has(data.phase)) return blank()
      return {
        version: 1,
        target: typeof data.target === 'string' && ID.test(data.target) ? data.target : null,
        licence: typeof data.licence === 'string' && data.licence.length <= 200 ? data.licence : null,
        phase: data.phase,
        requestId: typeof data.requestId === 'string' && data.requestId.length <= 100 ? data.requestId : null,
        pendingBuilds: buildNames(data.pendingBuilds),
        repairs: Number.isSafeInteger(data.repairs) && data.repairs >= 0 ? Math.min(data.repairs, 9) : 0,
        error: data.error && CODE.test(String(data.error.code)) ? { code: data.error.code, stage: text(data.error.stage, 20) } : null,
        updated: Number.isSafeInteger(data.updated) ? data.updated : null,
      }
    } catch {
      return blank()
    }
  }

  function ensure() {
    if (chain === null) chain = load()
    return chain
  }

  function save(change) {
    chain = { ...ensure(), ...change, updated: now() }
    try {
      fs.mkdirSync(dataDir(), { recursive: true })
      const target = file()
      fs.writeFileSync(`${target}.tmp`, JSON.stringify(chain), 'utf8')
      fs.renameSync(`${target}.tmp`, target)
    } catch (error) {
      log.warn(`feiyu-memgate: the model setup state could not be saved (${error?.code ?? 'error'})`)
    }
    keepTicking()
  }

  function fail(code, stage) {
    log.warn(`feiyu-memgate: model setup stopped at ${stage} (${code})`)
    save({ phase: 'failed', error: { code, stage } })
    return { ok: false, code }
  }

  const busy = () => BUSY.has(ensure().phase)
  const contract = () => addonContract(addon())
  const manager = () => {
    const pm = pluginManager()
    return pm && typeof pm.installBundle === 'function' ? pm : null
  }

  // While a step runs, look again now and then: a download finishes without a new service value.
  function keepTicking() {
    if (disposed) return
    if (busy() && !ticker) {
      ticker = setInterval(() => { advance() }, tickMs)
      ticker.unref?.()
    } else if (!busy() && ticker) {
      clearInterval(ticker)
      ticker = null
    }
  }

  // The add-on was installed (or should be there after a restart): wait for its service, not forever.
  function awaitAddon(phase = 'addon-starting') {
    save({ phase, error: phase === 'restart' ? { code: 'restart-required', stage: 'addon' } : null })
    clearTimeout(waitTimer)
    waitTimer = setTimeout(() => {
      if (!disposed && (chain.phase === 'addon-starting') && !contract()) fail('addon-not-started', 'addon')
    }, addonWaitMs)
    waitTimer.unref?.()
    return advance()
  }

  let pinned = null // the model the add-on's own settings pin, from its last list()

  async function rows() {
    const c = contract()
    let list = []
    if (c?.oneClick) {
      const answer = readList(await invoke(c.service, 'list', [], callTimeoutMs))
      list = answer.rows
      pinned = answer.pinned
    } else {
      pinned = null
    }
    const snapshot = c ? readEmbed(c.service) : null
    const flags = new Map((snapshot?.models ?? []).map((m) => [m.id, m]))
    if (!list.length) {
      // No list from the add-on (absent, too old, or failing): the shipped catalogue, with whatever
      // the version 1 snapshot says about installed and active models.
      list = offeredModels().map((m) => ({
        ...m, installed: flags.get(m.id)?.installed === true, active: flags.get(m.id)?.active === true,
      }))
    }
    return list.map((m) => {
      const known = CATALOGUE.find((e) => e.id === m.id)
      const tier = m.tier || known?.tier || ''
      return {
        ...m,
        title: m.title || known?.title || '',
        tier,
        tierLabel: TIER_LABELS[tier] ?? null,
        sizeBytes: m.sizeBytes ?? known?.sizeBytes ?? null,
        languages: m.languages.length ? m.languages : [...(known?.languages ?? [])],
        license: m.license ?? known?.license ?? null,
        requiresAcceptance: m.requiresAcceptance ?? known?.requiresAcceptance ?? null,
        // The add-on's measurement (its list(), else the shipped copy of its registry); null shows as
        // "not measured", never as the file size.
        memoryMB: m.memoryMB ?? known?.memoryMB ?? null,
        note: TIER_NOTES[tier] ?? null,
      }
    })
  }

  async function job(c = contract()) {
    if (!c?.oneClick) return null
    return readJob(await invoke(c.service, 'job', [], callTimeoutMs))
  }

  async function startModel() {
    const c = contract()
    if (!c) return awaitAddon()
    if (!c.oneClick) return fail('addon-outdated', 'addon')
    save({ phase: 'model', error: null })
    const options = chain.licence ? { acceptLicence: chain.licence } : {}
    // install(): download, verify, then make active; the job belongs to the add-on and goes on when
    // the page closes. A file already verified is made active at once ({ active }).
    const r = readResult(await invoke(c.service, 'install', [chain.target, options], callTimeoutMs))
    if (r.ok) {
      if (r.active) {
        save({ phase: 'activating' })
        queueMicrotask(() => { advance() })
        return { ok: true, code: 'activating' }
      }
      return { ok: true, code: 'started' }
    }
    if (r.code === 'busy') return fail('busy', 'model') // another model downloads (the same one joins: ok)
    if (r.code === 'unavailable') return awaitAddon() // the add-on is stopping; wait for it to come back
    return fail(r.code === 'needs-acceptance' || r.code === 'pinned' ? r.code : failureOf(r.code), 'model')
  }

  /** Move the chain on from what the add-on says now. Safe to call at any time and repeatedly. */
  function advance() {
    if (disposed) return Promise.resolve()
    if (advancing) {
      // Asked again while a look is running: look once more when it ends.
      again = true
      return advancing
    }
    advancing = (async () => {
      do {
        again = false
        try {
          await step()
        } catch (error) {
          log.warn(`feiyu-memgate: model setup check failed (${error?.name ?? 'error'})`)
        }
      } while (again && !disposed)
    })().finally(() => { advancing = null })
    return advancing
  }

  async function step() {
    if (!ensure().target) return
    const c = contract()
    if (chain.phase === 'addon-starting' || chain.phase === 'restart') {
      if (c) {
        await startModel()
        return
      }
      // Not running yet: a selected bundle is starting (or waits for the restart); anything else
      // would leave the page waiting for nothing, so it is repaired or reported now.
      const bundle = await bundleState()
      if (bundle === undefined || contract()) return // the host cannot say, or the add-on just came
      if (!bundle || (!bundle.installed && !bundle.enabled)) fail('addon-absent', 'addon')
      else if (bundle.installed && (!bundle.enabled || !bundle.files)) repair(bundle)
      else if (bundle.error) fail(enableFailureOf(bundle.error), 'addon')
      return
    }
    if (chain.phase !== 'model' && chain.phase !== 'activating') return
    if (!c) return // the add-on is restarting or was turned off: keep the chain and wait
    if (!c.oneClick) {
      fail('addon-outdated', 'addon')
      return
    }
    const snapshot = readEmbed(c.service)
    if (snapshot?.state === 'ready' && snapshot.model === chain.target) {
      log.info(`feiyu-memgate: local model ${chain.target} is ready`)
      save({ phase: 'done', error: null })
      return
    }
    const running = await job(c)
    const mine = running?.id === chain.target ? running : null
    if (mine && !FINISHED.has(mine.state)) return
    if (mine?.state === 'failed') {
      fail(failureOf(mine.reason), 'model')
      return
    }
    if (mine?.state === 'cancelled') {
      save({ phase: 'idle', error: { code: 'cancelled', stage: 'model' } })
      return
    }
    const entry = (await rows()).find((m) => m.id === chain.target)
    if (entry?.active) {
      if (snapshot?.state === 'error') fail(failureOf(snapshot.error), 'model')
      else if (chain.phase !== 'activating') save({ phase: 'activating' })
      return
    }
    if (entry?.installed) {
      // Downloaded and verified but not active (a finished add(), or an install() whose activation a
      // host restart cut off): make it active now.
      save({ phase: 'activating' })
      const r = readResult(await invoke(c.service, 'use', [chain.target], callTimeoutMs))
      if (!r.ok) fail(r.code === 'pinned' ? 'pinned' : failureOf(r.code), 'activate')
      return
    }
    // Not downloaded and no job for it: a host restart stopped the download. The add-on keeps the
    // part and resumes it on the next install(), which the page offers as "resume".
    if (chain.phase === 'model') save({ phase: 'paused', error: null })
  }

  // ---------------------------------------------------------------- the add-on through the host

  // One plugin manager call, bounded in time; a throw or a hang becomes undefined.
  async function hostCall(method, args) {
    const pm = manager()
    if (!pm || typeof pm[method] !== 'function') return undefined
    let timer
    try {
      return await Promise.race([
        Promise.resolve().then(() => pm[method](...args)),
        new Promise((resolve) => {
          timer = setTimeout(() => resolve(undefined), callTimeoutMs)
          timer.unref?.()
        }),
      ])
    } catch {
      return undefined
    } finally {
      clearTimeout(timer)
    }
  }

  // The add-on as the plugin manager lists it (addonBundle), or undefined when it cannot be asked.
  async function bundleState() {
    const list = await hostCall('listBundles', [])
    return Array.isArray(list) ? addonBundle(list) : undefined
  }

  // A host operation that changes the profile (switch on, remove) runs in the background like the
  // installation; `operation` tells a late answer from a superseded one.
  let operation = 0
  function background(start, settle) {
    const mine = ++operation
    Promise.resolve().then(start).then(
      (result) => { if (!disposed && mine === operation && chain.phase === 'addon') settle(result) },
      () => { if (!disposed && mine === operation && chain.phase === 'addon') fail('addon-enable-failed', 'addon') },
    )
  }

  const MAX_REPAIRS = 2 // switch on, then remove and install again

  /**
   * The add-on is a dependency of the profile but not running from it: installed but not selected
   * (what the CLI leaves after a stopped install script, or a bundle switched off), or its files are
   * gone. Selected again with setBundleEnabled when its files are there; otherwise removed and
   * installed again. Only on behalf of a confirmed click, and at most MAX_REPAIRS times per click.
   */
  function repair(bundle) {
    if (ensure().repairs >= MAX_REPAIRS) return fail('addon-enable-failed', 'addon')
    const pm = manager()
    if (bundle.files && typeof pm?.setBundleEnabled === 'function') {
      save({ phase: 'addon', requestId: null, repairs: chain.repairs + 1, error: null })
      log.info('feiyu-memgate: the embedding add-on is installed but not selected; switching it on')
      background(() => pm.setBundleEnabled(ADDON_PACKAGE, true), (result) => {
        const application = result?.application
        if ((application === 'applied' || application === 'overridden') && !result?.error) return awaitAddon()
        if (application === 'restart-required') return awaitAddon('restart')
        // Selecting needs the package's files (selectBundle: not-bundle without them).
        if (result?.error?.code === 'not-bundle') return reinstall()
        return fail(enableFailureOf(result?.error?.code), 'addon')
      })
      return { ok: true, code: 'enabling-addon' }
    }
    return reinstall()
  }

  // Remove the add-on's dependency and install it anew: the way the host's own documentation and the
  // add-on's author give for a package the CLI left installed but not switched on.
  function reinstall() {
    if (ensure().repairs >= MAX_REPAIRS) return fail('addon-enable-failed', 'addon')
    const pm = manager()
    if (typeof pm?.removeBundle !== 'function') return fail('addon-enable-failed', 'addon')
    save({ phase: 'addon', requestId: null, repairs: chain.repairs + 1, error: null })
    log.info('feiyu-memgate: reinstalling the embedding add-on')
    background(() => pm.removeBundle(ADDON_PACKAGE), (result) => {
      if (result?.application === 'failed' || result?.application === 'cancelled' || result?.error) return fail('addon-enable-failed', 'addon')
      return startAddonInstall()
    })
    return { ok: true, code: 'installing-addon' }
  }

  /** The add-on is not running: take the step the plugin manager's listing calls for. */
  async function bringUpAddon() {
    const bundle = await bundleState()
    if (!bundle || (!bundle.installed && !bundle.enabled)) return startAddonInstall()
    if (bundle.installed && (!bundle.enabled || !bundle.files)) return repair(bundle)
    if (!bundle.installed) return startAddonInstall() // selected, but no dependency holds it
    if (bundle.error) return fail(enableFailureOf(bundle.error), 'addon')
    await awaitAddon() // selected and fine: it is starting, or waits for a restart
    return { ok: true, code: 'starting-addon' }
  }

  function onAddonResult(requestId, result) {
    if (disposed || ensure().requestId !== requestId || chain.phase !== 'addon') return
    const application = result?.application
    const pending = buildNames(result?.pendingBuilds)
    if (pending.length) {
      // pnpm stopped an install script (node-llama-cpp has one). The host restored package.json, so
      // nothing is installed or selected; the undecided key it wrote stays in pnpm-workspace.yaml.
      // Only the user decides; the decision arrives through decideBuilds().
      log.info(`feiyu-memgate: the add-on installation waits for a decision on ${pending.length} install script(s)`)
      save({ phase: 'builds', pendingBuilds: pending, error: null })
      return
    }
    if (result?.error?.code === 'stale-approval') return fail('build-approval', 'addon')
    if (result?.error?.code === 'already-installed') {
      // With HMR a package already in package.json is refused before pnpm runs: it is installed but
      // not running from this profile, so switch it on instead.
      return bundleState().then((bundle) => {
        if (chain.phase !== 'addon') return undefined
        if (bundle?.installed && (!bundle.enabled || !bundle.files)) return repair(bundle)
        if (bundle?.enabled) return awaitAddon()
        return fail('addon-install-failed', 'addon')
      })
    }
    if ((application === 'applied' || application === 'overridden') && !result?.error) {
      log.info('feiyu-memgate: the embedding add-on is installed')
      return awaitAddon()
    }
    if (application === 'restart-required') return awaitAddon('restart')
    if (application === 'cancelled') return fail('install-cancelled', 'addon')
    return fail(addonFailureOf(result), 'addon')
  }

  /**
   * Ask the host's plugin manager to install the add-on by its npm name: the same operation as the
   * Plugins page (compatibility check before anything downloads, progress events under requestId,
   * cancelInstall). `approvedBuilds` only when the user allowed exactly these install scripts.
   */
  function startAddonInstall(approvedBuilds) {
    const pm = manager()
    if (!pm) return fail('no-plugin-manager', 'addon')
    const requestId = newRequestId()
    save({ phase: 'addon', requestId, pendingBuilds: [], error: null })
    installState = null
    let run
    try {
      const options = { requestId, enabled: true }
      if (approvedBuilds?.length) options.approvedBuilds = [...approvedBuilds]
      run = Promise.resolve(pm.installBundle(ADDON_PACKAGE, options))
    } catch {
      return fail('addon-install-failed', 'addon')
    }
    run.then((result) => onAddonResult(requestId, result), () => {
      if (chain.requestId === requestId && chain.phase === 'addon') fail('addon-install-failed', 'addon')
    })
    return { ok: true, code: 'installing-addon' }
  }

  // ---------------------------------------------------------------- what callers use

  /**
   * Start (or resume) setting up a model: the add-on first when it is missing. Installing the add-on
   * needs `confirmed: true`, given only after the person saw what will be installed (the page's own
   * confirmation, or --confirm on the command): a direct plugin manager call shows no host dialog.
   */
  async function install(id, { acceptLicence, confirmed = false } = {}) {
    ensure()
    const list = await rows()
    const entry = list.find((m) => m.id === id)
    if (!entry) return { ok: false, code: 'unknown-model' }
    if (entry.requiresAcceptance && acceptLicence !== entry.requiresAcceptance.name) {
      return { ok: false, code: 'needs-acceptance', licence: entry.requiresAcceptance }
    }
    if (chain.phase === 'builds') return { ok: false, code: 'builds-pending' }
    if (busy()) return chain.target === id ? { ok: true, code: 'running' } : { ok: false, code: 'busy' }
    if (entry.active) {
      save({ target: id, licence: null, phase: 'activating', requestId: null, error: null })
      await advance()
      return { ok: true, code: 'active' }
    }
    const c = contract()
    if (c) {
      save({ target: id, licence: entry.requiresAcceptance ? acceptLicence : null, phase: 'model', requestId: null, pendingBuilds: [], error: null })
      return c.oneClick ? startModel() : fail('addon-outdated', 'addon')
    }
    if (confirmed !== true) return { ok: false, code: 'confirm-required' }
    if (!manager()) {
      save({ target: id, licence: null, phase: 'failed', requestId: null, error: { code: 'no-plugin-manager', stage: 'addon' } })
      return { ok: false, code: 'no-plugin-manager' }
    }
    save({ target: id, licence: entry.requiresAcceptance ? acceptLicence : null, phase: 'addon', requestId: null, pendingBuilds: [], repairs: 0, error: null })
    // Installed already but not running (not selected, or waiting for a restart): installing it again
    // would fail as already-installed with HMR, so look first.
    return bringUpAddon()
  }

  /**
   * The person's answer to the install scripts pnpm stopped. allow: install again approving exactly
   * the names the host reported (a lasting permission in this profile, recorded by the host). deny:
   * stop; the host has no call that records a refusal (build-approval.ts writes approvals only).
   */
  async function decideBuilds(allow, { confirmed = false } = {}) {
    if (ensure().phase !== 'builds') return { ok: false, code: 'not-running' }
    if (confirmed !== true) return { ok: false, code: 'confirm-required' }
    // Deny: nothing is installed or selected (the host restored package.json), so stopping leaves
    // the profile as it was apart from the undecided key the host wrote; the failure names the line
    // to change, after which the same button installs with the script skipped.
    if (allow !== true) return fail('builds-denied', 'addon')
    return startAddonInstall(chain.pendingBuilds)
  }

  // Values for the {placeholders} of a failure: the file and key a refused install script is
  // recorded under.
  function failureValues() {
    let dir
    try {
      dir = profileDir()
    } catch {
      dir = undefined
    }
    const file = typeof dir === 'string' && dir
      ? path.join(dir, 'pnpm-workspace.yaml')
      : { en: 'the pnpm-workspace.yaml in the folder of this DSH profile (<DSH home>/profiles/<profile>)', zh: '当前 DSH 配置文件夹（<DSH home>/profiles/<配置名>）中的 pnpm-workspace.yaml' }
    return { file, name: chain?.pendingBuilds?.[0] ?? 'node-llama-cpp' }
  }

  /** Stop what runs: the add-on installation or the model download. */
  async function cancel() {
    if (ensure().phase === 'addon' && chain.requestId) {
      const pm = manager()
      let status = 'not-running'
      try {
        status = (await pm?.cancelInstall?.(chain.requestId))?.status ?? 'not-running'
      } catch {
        status = 'not-running'
      }
      // 'cancelled': the install call settles as cancelled and records install-cancelled itself.
      return status === 'cancelled' ? { ok: true, code: 'cancelled' } : { ok: false, code: status === 'too-late' ? 'too-late' : 'not-running' }
    }
    if (chain.phase === 'builds' || chain.phase === 'paused') {
      // Nothing runs: forget the pending step (the part of a paused download stays with the add-on).
      save({ phase: 'idle', pendingBuilds: [], error: { code: chain.phase === 'builds' ? 'install-cancelled' : 'cancelled', stage: 'cancel' } })
      return { ok: true, code: 'cancelled' }
    }
    if (chain.phase === 'model' || chain.phase === 'activating') {
      const c = contract()
      const r = c?.oneClick ? readResult(await invoke(c.service, 'cancel', [], callTimeoutMs)) : { ok: false, code: 'not-running' }
      save({ phase: 'idle', error: { code: 'cancelled', stage: 'model' } })
      return r.ok ? { ok: true, code: 'cancelled' } : { ok: false, code: r.code || 'not-running' }
    }
    return { ok: false, code: 'not-running' }
  }

  /** Make an installed model the active one. */
  async function use(id) {
    ensure()
    const c = contract()
    if (!c) return { ok: false, code: 'addon-absent' }
    if (!c.oneClick) return { ok: false, code: 'addon-outdated' }
    if (!(await rows()).some((m) => m.id === id)) return { ok: false, code: 'unknown-model' }
    if (busy() && chain.target !== id) return { ok: false, code: 'busy' }
    const r = readResult(await invoke(c.service, 'use', [id], callTimeoutMs))
    if (!r.ok) return { ok: false, code: r.code === 'pinned' || r.code === 'not-installed' ? r.code : failureOf(r.code) }
    save({ target: id, licence: null, phase: 'activating', requestId: null, error: null })
    await advance()
    return { ok: true, code: 'activating' }
  }

  /** Delete a downloaded model that is not active. */
  async function remove(id) {
    ensure()
    const c = contract()
    if (!c) return { ok: false, code: 'addon-absent' }
    if (!c.oneClick) return { ok: false, code: 'addon-outdated' }
    if (!(await rows()).some((m) => m.id === id)) return { ok: false, code: 'unknown-model' }
    const r = readResult(await invoke(c.service, 'remove', [id], callTimeoutMs))
    if (!r.ok) return { ok: false, code: ['active', 'downloading', 'not-installed'].includes(r.code) ? r.code : failureOf(r.code) }
    if (chain.target === id && !busy()) save({ ...blank(), updated: null })
    return { ok: true, code: 'removed' }
  }

  /** Where everything stands, for the page and the command. Reading it also moves the chain on. */
  async function view() {
    ensure()
    await advance()
    const c = contract()
    const snapshot = c ? readEmbed(c.service) : null
    const running = await job(c)
    const list = await rows()
    const models = list.map((m) => {
      let state = m.active ? 'active' : m.installed ? 'installed' : 'absent'
      let percent = null
      if (running?.id === m.id && !FINISHED.has(running.state)) {
        state = 'downloading'
        percent = running.percent
      } else if (m.active && snapshot?.model === m.id && snapshot.state === 'loading') {
        state = 'loading'
      } else if (state === 'absent' && chain.phase === 'paused' && chain.target === m.id) {
        state = 'paused'
      }
      return { ...m, state, percent, pinned: pinned === m.id }
    })
    const target = models.find((m) => m.id === chain.target)
    let p = null
    try {
      p = await python()
    } catch {
      p = null
    }
    return {
      version: 1,
      addon: {
        package: ADDON_PACKAGE,
        state: c ? (c.oneClick ? 'present' : 'outdated') : chain.phase === 'addon' ? 'installing' : chain.phase === 'addon-starting' || chain.phase === 'restart' ? 'starting' : 'absent',
        contract: c?.version ?? null,
        modelState: snapshot?.state ?? null,
      },
      installer: { available: Boolean(manager()) },
      python: { found: p ? p.found !== false : null, problem: p && p.found === false ? describeFailure('python-missing') : null },
      link: TIERS_LINK,
      busy: busy(),
      chain: {
        target: chain.target,
        phase: chain.phase,
        percent: chain.phase === 'model' ? target?.percent ?? null : null,
        installState: chain.phase === 'addon' ? installState : null,
        error: chain.error ? describeFailure(chain.error.code, failureValues()) : null,
      },
      // The install scripts waiting for the person's decision (phase builds), exactly as the host
      // named them.
      builds: chain.phase === 'builds'
        ? { pending: [...chain.pendingBuilds], note: fill(BUILDS_NOTE, { names: chain.pendingBuilds.join(', ') }) }
        : null,
      // What installing the add-on means, shown before the person confirms ({model}/{size} filled
      // in by the page for the model it was asked for).
      confirm: c ? null : ADDON_CONFIRM,
      pinned,
      models,
    }
  }

  /** The chain in a few words for the status block. */
  function summary() {
    const c = ensure()
    return { target: c.target, phase: c.phase, error: c.error?.code ?? null }
  }

  /** The host's install progress for our request (plugin-manager/install-state). */
  function onInstallState(payload) {
    if (!payload || payload.requestId !== ensure().requestId || chain.phase !== 'addon') return
    const phase = ['installing', 'cancelling', 'applying'].includes(payload.phase) ? payload.phase : null
    const attempt = payload.attempt && Number.isSafeInteger(payload.attempt.index) ? { index: payload.attempt.index, of: count(payload.attempt.of) } : null
    installState = phase ? { phase, attempt } : null
  }

  /** After a reload or restart: pick the chain up where the file says it stopped. */
  async function resume() {
    chain = load()
    if (chain.phase === 'addon') {
      const pm = manager()
      let result = null
      if (chain.requestId && typeof pm?.waitForInstall === 'function') {
        try {
          result = await pm.waitForInstall(chain.requestId)
        } catch {
          result = null
        }
      }
      if (result) return onAddonResult(chain.requestId, result)
      // The install (or switch-on) call is gone with the host process: a selected add-on is starting,
      // anything else was cut off before the host applied it.
      const bundle = contract() ? null : await bundleState()
      if (contract() || (bundle?.enabled && bundle.installed && !bundle.error)) return awaitAddon()
      return fail('interrupted', 'addon')
    }
    if (chain.phase === 'addon-starting') return awaitAddon()
    keepTicking()
    return advance()
  }

  function dispose() {
    disposed = true
    clearInterval(ticker)
    clearTimeout(waitTimer)
    ticker = null
  }

  /** A failure code explained for the page and the command, with this profile's file names. */
  function describe(code) {
    ensure()
    return describeFailure(code, failureValues())
  }

  return { install, decideBuilds, cancel, use, remove, view, summary, describe, advance, resume, onInstallState, dispose, state: () => ensure() }
}
