// The /memgate-embed chat command: parsing and the replies, in Chinese or English.
//
// The host passes everything after the command name as `rawInput` and has no argument parser
// (packages/interaction/commands/src/index.ts:40-58, :126-131), so this module parses it and refuses
// anything it does not expect. The command API has no progress stream (a result is shown only when
// the handler settles), so `add` starts a background download and returns at once; `status` shows
// the progress.
//
// The name is prefixed: command names are unique per scope and a duplicate registration throws
// (commands/src/index.ts:100-104); a bare `embed` could collide with another plugin. Names must match
// /^[a-z][a-z0-9_-]*$/ (commands/src/index.ts:32).
//
// The actions themselves live in src/plugin.js and reach this module as `api`; nothing here touches
// files or the network. No model-callable tool is registered for any of this: the model must not be
// able to start a download.
import { MODELS } from './models.js'

export const COMMAND_NAME = 'memgate-embed'
// Accepts the licence terms of a model that has them (`requiresAcceptance` in the registry). No
// shipped model needs it at present; the flag stays so such a model can be added to the registry.
export const ACCEPT_FLAG = '--accept-licence'
const MAX_INPUT = 400

/**
 * Parse the text after the command name.
 * @param {string} rawInput
 * @param {readonly object[]} [registry]  the plugin's registry (MODELS unless a test replaces it)
 * @returns {{ action: string, id?: string, accept?: boolean, all?: boolean, value?: string } | { error: string, detail?: string }}
 */
export function parseCommand(rawInput, registry = MODELS) {
  const text = typeof rawInput === 'string' ? rawInput.trim() : ''
  if (text.length > MAX_INPUT) return { error: 'too-long' }
  const tokens = text ? text.split(/\s+/) : []
  if (tokens.length === 0) return { action: 'status' }
  const [sub, ...rest] = tokens
  const action = sub.toLowerCase()
  const flags = rest.filter((t) => t.startsWith('-'))
  const words = rest.filter((t) => !t.startsWith('-'))
  const noMore = (allowedFlags = []) => {
    const unknown = flags.find((f) => !allowedFlags.includes(f))
    if (unknown) return { error: 'unknown-option', detail: unknown }
    if (new Set(flags).size !== flags.length) return { error: 'unknown-option', detail: flags.find((f, i) => flags.indexOf(f) !== i) }
    return null
  }
  const modelId = (word) => {
    const id = word.toLowerCase()
    return registry.some((m) => m.id === id) ? id : null
  }
  switch (action) {
    case 'status':
    case 'cancel':
    case 'help': {
      const bad = noMore()
      if (bad) return bad
      if (words.length) return { error: 'unexpected-argument', detail: words[0] }
      return { action }
    }
    case 'list': {
      const bad = noMore(['--all'])
      if (bad) return bad
      if (words.length) return { error: 'unexpected-argument', detail: words[0] }
      return { action, all: flags.includes('--all') }
    }
    case 'add': {
      const bad = noMore([ACCEPT_FLAG])
      if (bad) return bad
      if (words.length === 0) return { error: 'missing-model' }
      if (words.length > 1) return { error: 'unexpected-argument', detail: words[1] }
      const id = modelId(words[0])
      if (!id) return { error: 'unknown-model', detail: words[0] }
      const accept = flags.includes(ACCEPT_FLAG)
      if (accept && !registry.find((m) => m.id === id).requiresAcceptance) return { error: 'option-not-applicable', detail: ACCEPT_FLAG }
      return { action, id, accept }
    }
    case 'use':
    case 'remove': {
      const bad = noMore()
      if (bad) return bad
      if (words.length === 0) return { error: 'missing-model' }
      if (words.length > 1) return { error: 'unexpected-argument', detail: words[1] }
      if (action === 'use' && words[0].toLowerCase() === 'none') return { action, id: null }
      const id = modelId(words[0])
      if (!id) return { error: 'unknown-model', detail: words[0] }
      return { action, id }
    }
    case 'origin': {
      const bad = noMore()
      if (bad) return bad
      if (words.length > 1) return { error: 'unexpected-argument', detail: words[1] }
      return words.length ? { action, value: words[0] } : { action }
    }
    default:
      return { error: 'unknown-action', detail: sub }
  }
}

// ---------------------------------------------------------------------------------- formatting

export function formatSize(bytes) {
  if (!Number.isFinite(bytes)) return '?'
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
  if (bytes >= 1e3) return `${Math.round(bytes / 1e3)} kB`
  return `${bytes} B`
}

const speedText = (bps) => (bps > 0 ? `${formatSize(bps)}/s` : '-')
const percent = (a, b) => (b > 0 ? `${Math.floor((a / b) * 1000) / 10}%` : '0%')
// Inputs echoed in a reply are cut short and stripped of control characters.
const echo = (text) => String(text ?? '').replace(/[\u0000-\u001f\u007f]/g, '').slice(0, 60)

const T = {
  zh: {
    help: [
      '用法：',
      '/memgate-embed status：查看状态和下载进度',
      '/memgate-embed list{all}：列出可用的向量模型',
      '/memgate-embed add <id>{accept}：在后台下载向量模型',
      '/memgate-embed use <id|none>：切换当前模型（none：不使用模型）',
      '/memgate-embed remove <id>：删除已下载的模型文件',
      '/memgate-embed cancel：取消正在进行的下载',
      '/memgate-embed origin [auto|huggingface|hf-mirror|<镜像地址>]：查看或设置下载来源',
    ],
    errors: {
      'too-long': '输入太长。',
      'unknown-action': (d) => `未知的子命令：${d}。`,
      'unknown-option': (d) => `未知的选项：${d}。`,
      'option-not-applicable': (d) => `${d} 仅用于需先同意许可条款的模型。`,
      'unexpected-argument': (d) => `多余的参数：${d}。`,
      'missing-model': '缺少模型 id。可用 /memgate-embed list 查看。',
      'unknown-model': (d, ids) => `未找到模型：${d}。可用的 id：${ids}。`,
    },
    state: { 'no-model': '未选择模型', loading: '正在加载', ready: '就绪', error: '错误', off: '已关闭（设置中的 engine 已关闭）' },
    errorKind: {
      'not-installed': '模型文件未下载',
      'file-corrupt': '模型文件校验失败，请先 remove 再重新 add',
      'load-failed': '模型加载失败',
      'runtime-missing': 'node-llama-cpp 或其 CPU 二进制包不可用',
      crashed: '模型进程反复退出',
      'port-unavailable': '无可用的本地端口',
      'bad-options': '内部参数错误',
      'endpoint-failed': '本地向量接口无法启动',
      'unknown-model': '设置中指定的模型不在列表中',
    },
    reason: {
      http: 'HTTP 错误', network: '网络错误', dns: '域名解析失败', timeout: '超时', certificate: '证书错误',
      integrity: '大小或 SHA-256 校验不符', storage: '无法写入磁盘', 'disk-full': '磁盘空间不足', cancelled: '已取消', unknown: '未知错误',
    },
  },
  en: {
    help: [
      'Usage:',
      '/memgate-embed status: state and download progress',
      '/memgate-embed list{all}: the models on offer',
      '/memgate-embed add <id>{accept}: download a model in the background',
      '/memgate-embed use <id|none>: switch the active model (none: no model)',
      '/memgate-embed remove <id>: delete a downloaded model file',
      '/memgate-embed cancel: cancel the running download',
      '/memgate-embed origin [auto|huggingface|hf-mirror|<mirror URL>]: show or set the download source',
    ],
    errors: {
      'too-long': 'The input is too long.',
      'unknown-action': (d) => `Unknown subcommand: ${d}.`,
      'unknown-option': (d) => `Unknown option: ${d}.`,
      'option-not-applicable': (d) => `${d} applies only to a model whose licence must be accepted first.`,
      'unexpected-argument': (d) => `Unexpected argument: ${d}.`,
      'missing-model': 'A model id is missing. See /memgate-embed list.',
      'unknown-model': (d, ids) => `No such model: ${d}. Known ids: ${ids}.`,
    },
    state: { 'no-model': 'no model', loading: 'loading', ready: 'ready', error: 'error', off: 'off (engine is switched off in the settings)' },
    errorKind: {
      'not-installed': 'the model file has not been downloaded',
      'file-corrupt': 'the model file failed verification; remove it and add it again',
      'load-failed': 'the model did not load',
      'runtime-missing': 'node-llama-cpp or its CPU binary package is not available',
      crashed: 'the model process keeps exiting',
      'port-unavailable': 'no local port was available',
      'bad-options': 'internal options were rejected',
      'endpoint-failed': 'the local endpoint could not start',
      'unknown-model': 'the model in the settings is not in the list',
    },
    reason: {
      http: 'HTTP error', network: 'network error', dns: 'name resolution failed', timeout: 'timed out', certificate: 'certificate error',
      integrity: 'size or SHA-256 mismatch', storage: 'cannot write to disk', 'disk-full': 'not enough disk space', cancelled: 'cancelled', unknown: 'unknown error',
    },
  },
}

// The usage lines. An option no registry model can use is left out, so the help never offers
// `--all` with nothing hidden or the licence flag with no model under such terms.
export function helpText(lang, registry = MODELS) {
  const all = registry.some((m) => m.status !== 'verified') ? ' [--all]' : ''
  const accept = registry.some((m) => m.requiresAcceptance) ? ` [${ACCEPT_FLAG}]` : ''
  return T[lang].help.map((line) => line.replace('{all}', all).replace('{accept}', accept)).join('\n')
}

const reasonText = (lang, d) => {
  const base = T[lang].reason[d.reason] ?? d.reason
  return `${base}${d.status ? ` (HTTP ${d.status})` : ''}${d.code ? ` (${d.code})` : ''}`
}

function originText(lang, origin) {
  const how = origin.value === 'auto'
    ? (lang === 'zh' ? 'auto（比较 huggingface.co 和 hf-mirror.com）' : 'auto (compare huggingface.co and hf-mirror.com)')
    : origin.value
  const where = origin.pinned ? (lang === 'zh' ? '，由设置指定' : ', set in the settings') : ''
  return `${how}${where}`
}

function sourcesText(lang, sources) {
  if (sources.length === 1) return sources[0]
  return lang === 'zh' ? `${sources.join(' 或 ')}，使用最先响应的一个` : `${sources.join(' or ')}, whichever answers first`
}

function licenceLine(lang, model) {
  return lang === 'zh' ? `许可：${model.license.name}（${model.license.url}）` : `Licence: ${model.license.name} (${model.license.url})`
}

function progressLine(lang, p) {
  if (p.state === 'checking' || p.state === 'probing' || p.state === 'starting') {
    return lang === 'zh' ? `正在准备下载 ${p.id}` : `Preparing to download ${p.id}`
  }
  if (p.state === 'verifying') return lang === 'zh' ? `正在校验 ${p.id}` : `Finishing ${p.id}`
  const line = `${p.id}: ${formatSize(p.bytes)} / ${formatSize(p.total)} (${percent(p.bytes, p.total)}), ${speedText(p.speed)}${p.source ? `, ${p.source}` : ''}`
  return lang === 'zh' ? `正在下载 ${line}` : `Downloading ${line}`
}

function lastLine(lang, last) {
  if (!last) return null
  if (last.state === 'done') return lang === 'zh' ? `上次下载：${last.id} 已完成并校验。` : `Last download: ${last.id} finished and verified.`
  if (last.state === 'cancelled') return lang === 'zh' ? `上次下载：${last.id} 已取消，已下载的部分会在下次 add 时续传。` : `Last download: ${last.id} was cancelled; the downloaded part resumes on the next add.`
  return lang === 'zh'
    ? `上次下载：${last.id} 失败：${reasonText(lang, last)}${last.source ? `（来源 ${last.source}）` : ''}。`
    : `Last download: ${last.id} failed: ${reasonText(lang, last)}${last.source ? ` (source ${last.source})` : ''}.`
}

/** The reply for a status() result. */
export function formatStatus(lang, s) {
  const zh = lang === 'zh'
  const lines = []
  const state = s.engineOn ? (T[lang].state[s.state] ?? s.state) : T[lang].state.off
  lines.push(zh ? `状态：${state}` : `State: ${state}`)
  if (s.model) {
    const pin = s.activePinned ? (zh ? '（由设置指定）' : ' (set in the settings)') : ''
    const loaded = s.state === 'ready' ? (s.loaded ? (zh ? '，已在内存中' : ', in memory') : (zh ? '，未加载，下次请求时加载' : ', not loaded; the next request loads it')) : ''
    lines.push(zh ? `当前模型：${s.model}${pin}${loaded}` : `Active model: ${s.model}${pin}${loaded}`)
  } else {
    lines.push(zh ? '当前模型：无。请先执行 /memgate-embed add <id>，再执行 /memgate-embed use <id>。' : 'Active model: none. Run /memgate-embed add <id>, then /memgate-embed use <id>.')
  }
  if (s.error) lines.push(zh ? `问题：${T[lang].errorKind[s.error] ?? s.error}` : `Problem: ${T[lang].errorKind[s.error] ?? s.error}`)
  if (s.url && s.state === 'ready') lines.push(zh ? `本地接口：${s.url}（仅限本机访问，需要密钥）` : `Local endpoint: ${s.url} (this computer only, key required)`)
  if (s.model && Number.isInteger(s.maxInputTokens)) {
    const n = s.truncatedInputs ?? 0
    if (s.overflow === 'reject') {
      lines.push(zh ? `超过 ${s.maxInputTokens} 个 token 的文本：拒绝处理` : `Text over ${s.maxInputTokens} tokens: refused`)
    } else {
      lines.push(zh
        ? `超过 ${s.maxInputTokens} 个 token 的文本：仅对开头部分生成向量；本次启动以来已截断 ${n} 段`
        : `Text over ${s.maxInputTokens} tokens: embedded by its beginning; ${n} input${n === 1 ? '' : 's'} truncated since start`)
    }
  }
  if (s.download) lines.push(progressLine(lang, s.download))
  const last = lastLine(lang, s.lastDownload)
  if (last && !s.download) lines.push(last)
  lines.push(zh ? `下载来源：${originText(lang, s.origin)}` : `Download source: ${originText(lang, s.origin)}`)
  lines.push(zh ? `模型文件夹：${s.modelsDir}` : `Model folder: ${s.modelsDir}`)
  return lines.join('\n')
}

/** The reply for list() rows. `hidden`: how many unverified models the default list leaves out. */
export function formatList(lang, rows, { all = false, hidden = 0 } = {}) {
  const zh = lang === 'zh'
  const lines = [zh ? '向量模型（id、文件大小、维度、许可、校准）：' : 'Models (id, file size, dimensions, licence, calibration):']
  for (const r of rows) {
    const flags = []
    if (r.active) flags.push(zh ? '当前' : 'active')
    if (r.installed) flags.push(zh ? '已下载' : 'downloaded')
    if (r.downloading) flags.push(zh ? '下载中' : 'downloading')
    if (r.requiresAcceptance) flags.push(zh ? `需同意许可，请加 ${ACCEPT_FLAG}` : `needs ${ACCEPT_FLAG}`)
    if (r.status !== 'verified') flags.push(zh ? '未验证' : 'unverified')
    const parity = r.parity === 'exact' ? (zh ? '与校准文件相同' : 'calibrated file') : (zh ? '近似' : 'approximate')
    lines.push(`- ${r.id}: ${formatSize(r.sizeBytes)}, ${r.dimensions} d, ${r.license.name}, ${parity}${flags.length ? ` [${flags.join(', ')}]` : ''}`)
  }
  if (!all && hidden > 0) lines.push(zh ? '使用 --all 可同时列出未验证的模型。' : 'Add --all to include unverified models.')
  lines.push(zh ? '各档位的检索效果：https://github.com/N0rmal-114/feiyu-memgate#部署档位' : 'Retrieval quality per tier: https://github.com/N0rmal-114/feiyu-memgate#部署档位')
  return lines.join('\n')
}

/** The reply for an add() result. */
export function formatAdd(lang, r) {
  const zh = lang === 'zh'
  if (r.ok) {
    const m = r.model
    const lines = [
      zh ? `开始在后台下载 ${m.id}。` : `Started downloading ${m.id} in the background.`,
      zh ? `文件：${m.repository}/${m.file}（revision ${m.revision.slice(0, 12)}）` : `File: ${m.repository}/${m.file} (revision ${m.revision.slice(0, 12)})`,
      zh ? `大小：${formatSize(m.sizeBytes)}，下载后校验 SHA-256` : `Size: ${formatSize(m.sizeBytes)}, SHA-256 checked after download`,
      licenceLine(lang, m),
      zh ? `来源：${sourcesText(lang, r.sources)}` : `Source: ${sourcesText(lang, r.sources)}`,
    ]
    if (r.resumeFrom > 0) lines.push(zh ? `续传：已有 ${formatSize(r.resumeFrom)}` : `Resuming: ${formatSize(r.resumeFrom)} already on disk`)
    if (m.status !== 'verified') {
      lines.push(zh ? '注意：该文件尚未在本插件的引擎上测试，可能无法加载。' : 'Note: this file has not been tested with this add-on\'s engine yet and may fail to load.')
    }
    if (r.accepted) lines.push(zh ? `已记录对 ${r.accepted.name} 的同意（${r.accepted.acceptedAt}）。` : `Recorded your acceptance of the ${r.accepted.name} (${r.accepted.acceptedAt}).`)
    lines.push(zh ? `进度：/memgate-embed status。完成后：/memgate-embed use ${m.id}` : `Progress: /memgate-embed status. When it is done: /memgate-embed use ${m.id}`)
    return lines.join('\n')
  }
  switch (r.code) {
    case 'needs-acceptance':
      return zh
        ? `${r.model.id} 的权重受 ${r.model.requiresAcceptance.name} 约束：${r.model.requiresAcceptance.url}\n请阅读并同意后，执行 /memgate-embed add ${r.model.id} ${ACCEPT_FLAG} 下载（${formatSize(r.model.sizeBytes)}）。同意前不会下载任何内容。`
        : `The ${r.model.id} weights are under the ${r.model.requiresAcceptance.name}: ${r.model.requiresAcceptance.url}\nIf you have read and accept them, run /memgate-embed add ${r.model.id} ${ACCEPT_FLAG} to download it (${formatSize(r.model.sizeBytes)}). Nothing is downloaded before that.`
    case 'busy':
      return zh ? `已有下载正在进行：${progressLine(lang, r.current)}。请等待其完成，或先执行 /memgate-embed cancel。` : `A download is already running: ${progressLine(lang, r.current)}. Wait for it, or run /memgate-embed cancel first.`
    case 'installed':
      return zh ? `${r.model.id} 已下载并校验。切换：/memgate-embed use ${r.model.id}` : `${r.model.id} is already downloaded and verified. Switch to it: /memgate-embed use ${r.model.id}`
    default:
      return zh ? '无法开始下载。' : 'The download could not start.'
  }
}

export function formatUse(lang, r) {
  const zh = lang === 'zh'
  if (r.ok) {
    if (r.id === null) return zh ? '已停用向量模型，记忆插件将改用关键词检索。' : 'No model is active now. The memory plugin goes back to keyword search.'
    return zh ? `当前模型已切换为 ${r.id}，正在加载。可用 /memgate-embed status 查看状态。` : `The active model is now ${r.id}; it is loading. See /memgate-embed status.`
  }
  switch (r.code) {
    case 'pinned':
      return zh ? `设置项 activeModel 已指定 ${r.pinned}，以设置为准。如需用命令切换，请先将该设置改回 none。` : `The activeModel setting pins ${r.pinned}, and the setting wins. Set it back to none to switch with the command.`
    case 'not-installed':
      return zh ? `${r.id} 尚未下载。请先执行 /memgate-embed add ${r.id}。` : `${r.id} has not been downloaded. Run /memgate-embed add ${r.id} first.`
    default:
      return zh ? '无法切换。' : 'The model could not be switched.'
  }
}

export function formatRemove(lang, r) {
  const zh = lang === 'zh'
  if (r.ok) return zh ? `已删除 ${r.id} 的文件，释放 ${formatSize(r.freedBytes)}。` : `Deleted the files of ${r.id}, freeing ${formatSize(r.freedBytes)}.`
  switch (r.code) {
    case 'active':
      return zh ? `${r.id} 是当前模型。请先切换到其他模型，或执行 /memgate-embed use none。` : `${r.id} is the active model. Switch to another model or run /memgate-embed use none first.`
    case 'downloading':
      return zh ? `${r.id} 正在下载。请先执行 /memgate-embed cancel。` : `${r.id} is downloading. Run /memgate-embed cancel first.`
    case 'not-installed':
      return zh ? `${r.id} 没有已下载的文件。` : `There are no downloaded files of ${r.id}.`
    default:
      return zh ? '无法删除。' : 'The files could not be deleted.'
  }
}

export function formatCancel(lang, r) {
  const zh = lang === 'zh'
  if (r.ok) return zh ? `已取消下载 ${r.id}。已下载的部分保留在磁盘上，下次 add 时续传，remove 可将其删除。` : `Cancelled the download of ${r.id}. The downloaded part stays on disk and resumes on the next add; remove deletes it.`
  return zh ? '没有正在进行的下载。' : 'No download is running.'
}

export function formatOrigin(lang, r) {
  const zh = lang === 'zh'
  if (r.ok) return zh ? `下载来源：${originText(lang, r.origin)}` : `Download source: ${originText(lang, r.origin)}`
  if (r.code === 'pinned') return zh ? `设置项 origin 已指定 ${r.origin.value}，以设置为准。如需用命令设置，请先将该设置改回 auto。` : `The origin setting is ${r.origin.value}, and the setting wins. Set it back to auto to choose with the command.`
  return zh
    ? `无效的来源：${echo(r.value)}。可用：auto、huggingface、hf-mirror，或 http(s)://主机[:端口] 形式的 Hugging Face 兼容镜像地址（不含路径）。`
    : `Invalid source: ${echo(r.value)}. Use auto, huggingface, hf-mirror, or a Hugging Face compatible mirror as http(s)://host[:port] without a path.`
}

/**
 * The command definition for ctx.commands.register (commands/src/index.ts:61-78, result shape
 * commands/src/types.ts:34-41).
 * @param {object} api       the actions (src/plugin.js)
 * @param {() => 'zh'|'en'} language
 * @param {readonly object[]} [registry]  the plugin's registry
 */
export function createCommandDefinition(api, language, registry = MODELS) {
  const ids = registry.map((m) => m.id).join(', ')
  const hidden = registry.filter((m) => m.status !== 'verified').length
  const lang0 = language()
  return {
    name: COMMAND_NAME,
    description: lang0 === 'zh'
      ? '管理 feiyu-memgate 的本地向量模型：列出、下载、切换、删除'
      : 'Manage the local embedding models of feiyu-memgate: list, download, switch, remove',
    input: { hint: 'status | list | add <id> | use <id|none> | remove <id> | cancel | origin [value]' },
    async handler({ rawInput }) {
      const lang = language()
      const parsed = parseCommand(rawInput, registry)
      if (parsed.error) {
        const message = T[lang].errors[parsed.error]
        const text = typeof message === 'function' ? message(echo(parsed.detail), ids) : message
        return { kind: 'error', text: `${text}\n${helpText(lang, registry)}` }
      }
      try {
        switch (parsed.action) {
          case 'help':
            return { kind: 'success', text: helpText(lang, registry) }
          case 'status':
            return { kind: 'success', text: formatStatus(lang, api.status()) }
          case 'list':
            return { kind: 'success', text: formatList(lang, api.list({ all: parsed.all }), { all: parsed.all, hidden }) }
          case 'add': {
            const r = await api.add(parsed.id, { accept: parsed.accept })
            return { kind: r.ok ? 'success' : 'error', text: formatAdd(lang, r) }
          }
          case 'use': {
            const r = await api.use(parsed.id)
            return { kind: r.ok ? 'success' : 'error', text: formatUse(lang, r) }
          }
          case 'remove': {
            const r = await api.remove(parsed.id)
            return { kind: r.ok ? 'success' : 'error', text: formatRemove(lang, r) }
          }
          case 'cancel': {
            const r = api.cancel()
            return { kind: r.ok ? 'success' : 'error', text: formatCancel(lang, r) }
          }
          case 'origin': {
            const r = await api.origin(parsed.value)
            return { kind: r.ok ? 'success' : 'error', text: formatOrigin(lang, r) }
          }
          default:
            return { kind: 'error', text: helpText(lang, registry) }
        }
      } catch {
        return { kind: 'error', text: lang === 'zh' ? '命令执行失败。' : 'The command failed.' }
      }
    },
  }
}
