// The /memgate chat command: status, settings and retrieval tiers, in the configured language.
//
// The host hands the handler the exact text after the command name (rawInput,
// packages/interaction/commands/src/index.ts:41-58); this module parses it itself and refuses
// anything it does not expect. Settings are written through the host's Settings service (the same
// path the settings page takes, so the change persists in the profile and arrives as a live update),
// by the `write` function the plugin passes in.
//
// Replies never contain a secret: secret settings show as "set" or "not set", and a value typed for
// one is never repeated. Words a person typed are repeated only when they look like a name.
import { FIELDS, parseSetting, SETTING_KEYS } from './config.js'
import { ADDON_CONFIRM, describeFailure } from './setup.js'
import { TIER_CHOICES, TIERS } from './tiers.js'

export const COMMAND_NAME = 'memgate'

const PLAIN = /^[A-Za-z0-9._-]{1,48}$/
const quote = (word) => (PLAIN.test(word) ? `"${word}"` : '')
const MODULE_KEYS = FIELDS.find((g) => g.group === 'modules').fields.map((f) => f.key)

const TEXT = {
  en: {
    usage: [
      'Usage: /memgate <subcommand>',
      '  status              gateway, Python, store, retrieval tier, embedding add-on, copy progress',
      '  config              every setting with its current value and default',
      '  set <key> <value>   change a setting (keys as /memgate config lists them)',
      '  reset <key>         return a setting to its default',
      '  on <feature>        switch a feature on (see the modules.* settings)',
      '  off <feature>       switch a feature off',
      '  tiers               the retrieval tiers: availability and what each needs, and whether hybrid is on',
      '  use <tier>          choose the retrieval tier: auto, keyword, endpoint or local',
      '  models              local embedding models: size, memory, languages, licence and state',
      '  install <model> [--accept-licence <licence name>] [--confirm]',
      '                      download and enable a model; installing the embedding add-on first',
      '                      needs --confirm after reading what it installs',
      '  builds allow|deny   answer the install-script question of the add-on installation',
      '  use <model>         switch to a downloaded model',
      '  remove <model>      delete a downloaded model that is not in use',
      '  cancel              stop the running add-on installation or model download',
      '  changelog           what the newest released version changed (the text is in Chinese)',
    ].join('\n'),
    oneLine: 'The command takes one line.',
    unknownSub: (w) => `Unknown subcommand${w ? ` ${w}` : ''}.`,
    noArgs: (sub) => `/memgate ${sub} takes no arguments.`,
    noChangelog: 'The changelog could not be shown.',
    needKeyValue: 'Usage: /memgate set <key> <value>',
    needKey: (sub) => `Usage: /memgate ${sub} <key>`,
    needModule: (sub) => `Usage: /memgate ${sub} <feature>. Features: ${MODULE_KEYS.join(', ')}.`,
    needTier: `Usage: /memgate use <tier>. Tiers: ${TIER_CHOICES.join(', ')}.`,
    unknownKey: (w) => `Unknown setting${w ? ` ${w}` : ''}. /memgate config lists them.`,
    ambiguous: (w, list) => `${w} names more than one setting: ${list.join(', ')}. Use the full name.`,
    unknownModule: (w) => `Unknown feature${w ? ` ${w}` : ''}. Features: ${MODULE_KEYS.join(', ')}.`,
    unknownTier: (w) => `Unknown tier${w ? ` ${w}` : ''}. Tiers: ${TIER_CHOICES.join(', ')}.`,
    invalid: {
      boolean: (k) => `${k} takes on or off.`,
      integer: (k, f) => `${k} takes a whole number from ${f.min} to ${f.max}.`,
      choice: (k, f) => `${k} takes one of: ${f.options.map((o) => o.value).join(', ')}.`,
      url: (k) => `${k} takes an http:// or https:// address.`,
      empty: (k) => `${k} needs a value; /memgate reset ${k} clears it.`,
    },
    noSettings: 'Not saved: this host has no Settings service, so settings can only be changed in the profile files.',
    refused: (kind) => `Not saved: the host refused the change (${kind}).`,
    saved: (k, v, was) => `Saved: ${k} = ${v} (was ${was}).`,
    savedSecret: (k) => `Saved: ${k} is set (the value is not shown).`,
    resetDone: (k, v) => `Reset: ${k} is back to its default (${v}).`,
    resetSecret: (k) => `Reset: ${k} is cleared.`,
    already: (k, v) => `${k} is already ${v}; nothing changed.`,
    on: 'on', off: 'off', set: 'set', notSet: 'not set', empty: '(empty)', secret: 'secret',
    tierNow: (t) => `auto uses ${t} right now.`,
    tierUnavailable: (needs) => `Not available right now: it needs ${needs}. Memory stays off until then.`,
    configTitle: 'feiyu-memgate settings (value, then default):',
    statusTitle: 'feiyu-memgate status',
    tiersTitle: (setting, active) => `Retrieval tier: ${setting}${active ? `, active: ${active}` : ''}.`,
    tiersExternal: 'Retrieval tiers apply to the managed gateway; an external gateway uses its own configuration.',
    hybridWord: 'keyword (hybrid)',
    hybridLine: (on, active) => `Hybrid (keyword and vector search together, retrieval.hybrid): ${on ? (active ? 'on' : 'on, but it does nothing under the current tier') : 'off'}.`,
    tierLine: (t) => `- ${t.name}: ${t.available ? 'available' : 'not available'}${t.active ? ' (active)' : ''}. Needs: ${t.needs}.${t.missing.length ? ` Missing: ${t.missing.join(', ')}.` : ''}`,
    labels: {
      gateway: 'Gateway', python: 'Python', retrieval: 'Retrieval', store: 'Store', embed: 'Embedding add-on',
      migration: 'Copy from the keyword store', features: 'Features on',
    },
    states: {
      idle: 'not started', starting: 'starting', running: 'running', restarting: 'restarting', off: 'off',
      failed: 'stopped after repeated failures', stopped: 'stopped', unused: 'not running (no memory feature is on)',
      configured: 'configured', incomplete: 'needs external.url and external.token',
      ready: 'ready', loading: 'loading', 'no-model': 'no model installed', error: 'error',
      done: 'done', partial: 'in progress', 'not-started': 'not started',
    },
    pythonNone: 'not found (3.11 or newer needed)',
    pythonUnknown: 'not checked',
    notInstalled: 'not installed',
    memories: (n) => `${n} memories`,
    countUnknown: 'count unavailable',
    progress: (m) => `${m.copied ?? 0} of ${m.total ?? 0} copied${m.refused ? `, ${m.refused} refused by the embedding model` : ''}`,
    approximate: 'scores not calibrated',
    none: 'none',
  },
  zh: {
    usage: [
      '用法：/memgate <子命令>',
      '  status              网关、Python、存储、检索方式、向量插件、复制进度',
      '  config              每项设置的当前值和默认值',
      '  set <键> <值>       修改一项设置（键名见 /memgate config）',
      '  reset <键>          把一项设置恢复默认',
      '  on <功能>           打开一项功能（见 modules.* 设置）',
      '  off <功能>          关闭一项功能',
      '  tiers               各种检索方式：是否可用，各需要什么，混合检索是否开着',
      '  use <方式>          选择检索方式：auto、keyword、endpoint 或 local',
      '  models              本地向量模型：大小、内存、语言、许可和状态',
      '  install <模型> [--accept-licence <许可名称>] [--confirm]',
      '                      下载并启用一个模型；需要先装向量插件时，看过说明后加 --confirm',
      '  builds allow|deny   回答插件安装时的安装脚本问题',
      '  use <模型>          切换到一个已下载的模型',
      '  remove <模型>       删除一个没在使用的已下载模型',
      '  cancel              停止正在进行的插件安装或模型下载',
      '  changelog           最新发布的版本改了什么',
    ].join('\n'),
    oneLine: '命令只能写一行。',
    unknownSub: (w) => `没有这个子命令${w ? ` ${w}` : ''}。`,
    noArgs: (sub) => `/memgate ${sub} 不带参数。`,
    noChangelog: '更新日志现在无法显示。',
    needKeyValue: '用法：/memgate set <键> <值>',
    needKey: (sub) => `用法：/memgate ${sub} <键>`,
    needModule: (sub) => `用法：/memgate ${sub} <功能>。功能有：${MODULE_KEYS.join('、')}。`,
    needTier: `用法：/memgate use <方式>。方式有：${TIER_CHOICES.join('、')}。`,
    unknownKey: (w) => `没有这项设置${w ? ` ${w}` : ''}。/memgate config 会列出全部设置。`,
    ambiguous: (w, list) => `${w} 对应多项设置：${list.join('、')}。请写完整的键名。`,
    unknownModule: (w) => `没有这项功能${w ? ` ${w}` : ''}。功能有：${MODULE_KEYS.join('、')}。`,
    unknownTier: (w) => `没有这种检索方式${w ? ` ${w}` : ''}。方式有：${TIER_CHOICES.join('、')}。`,
    invalid: {
      boolean: (k) => `${k} 只能是 on 或 off。`,
      integer: (k, f) => `${k} 只能是 ${f.min} 到 ${f.max} 之间的整数。`,
      choice: (k, f) => `${k} 只能是：${f.options.map((o) => o.value).join('、')}。`,
      url: (k) => `${k} 需要 http:// 或 https:// 开头的地址。`,
      empty: (k) => `${k} 需要一个值；要清空请用 /memgate reset ${k}。`,
    },
    noSettings: '没有保存：这个宿主没有设置服务，只能在配置文件里修改设置。',
    refused: (kind) => `没有保存：宿主拒绝了这次修改（${kind}）。`,
    saved: (k, v, was) => `已保存：${k} = ${v}（原来是 ${was}）。`,
    savedSecret: (k) => `已保存：${k} 已设置（不显示内容）。`,
    resetDone: (k, v) => `已恢复：${k} 回到默认值（${v}）。`,
    resetSecret: (k) => `已恢复：${k} 已清空。`,
    already: (k, v) => `${k} 已经是 ${v}，没有改动。`,
    on: '开', off: '关', set: '已设置', notSet: '未设置', empty: '（空）', secret: '秘密',
    tierNow: (t) => `auto 现在用的是 ${t}。`,
    tierUnavailable: (needs) => `现在还不能用：需要${needs}。在那之前记忆功能是关闭的。`,
    configTitle: 'feiyu-memgate 设置（当前值，然后是默认值）：',
    statusTitle: 'feiyu-memgate 状态',
    tiersTitle: (setting, active) => `检索方式设置：${setting}${active ? `，正在使用：${active}` : ''}。`,
    tiersExternal: '检索方式只用于自带网关；外部网关按它自己的配置。',
    hybridWord: '关键词（混合）',
    hybridLine: (on, active) => `混合检索（关键词和向量一起查，retrieval.hybrid）：${on ? (active ? '开' : '已开，但当前检索方式下不起作用') : '关'}。`,
    tierLine: (t) => `- ${t.name}：${t.available ? '可用' : '不可用'}${t.active ? '（正在使用）' : ''}。需要：${t.needs}。${t.missing.length ? `还缺：${t.missing.join('、')}。` : ''}`,
    labels: {
      gateway: '网关', python: 'Python', retrieval: '检索', store: '存储', embed: '向量插件',
      migration: '从关键词存储复制', features: '已开的功能',
    },
    states: {
      idle: '未启动', starting: '启动中', running: '运行中', restarting: '重启中', off: '关闭',
      failed: '多次失败后已停止', stopped: '已停止', unused: '没有运行（没有开启需要记忆的功能）',
      configured: '已配置', incomplete: '需要 external.url 和 external.token',
      ready: '就绪', loading: '加载中', 'no-model': '没有安装模型', error: '出错',
      done: '完成', partial: '进行中', 'not-started': '未开始',
    },
    pythonNone: '没有找到（需要 3.11 或更新）',
    pythonUnknown: '未检查',
    notInstalled: '未安装',
    memories: (n) => `${n} 条记忆`,
    countUnknown: '条数未知',
    progress: (m) => `已复制 ${m.copied ?? 0} / ${m.total ?? 0}${m.refused ? `，向量模型拒收 ${m.refused} 条` : ''}`,
    approximate: '分数未校准',
    none: '无',
  },
}

const word = (t, state) => t.states[state] ?? state

function show(t, field, value) {
  if (field.secret) return value ? t.set : t.notSet
  if (field.kind === 'boolean') return value ? t.on : t.off
  if (value === '') return t.empty
  return String(value)
}

/** The setting a typed name means: { key, field } or { error }. Accepts `group.key` and an unambiguous bare key, in any case. */
export function resolveSetting(name, t = TEXT.en) {
  const lower = String(name).toLowerCase()
  const keys = Object.keys(SETTING_KEYS)
  const exact = keys.find((k) => k.toLowerCase() === lower)
  if (exact) return { key: exact, field: SETTING_KEYS[exact] }
  const bare = keys.filter((k) => k.split('.')[1].toLowerCase() === lower)
  if (bare.length === 1) return { key: bare[0], field: SETTING_KEYS[bare[0]] }
  if (bare.length > 1) return { error: t.ambiguous(quote(name) || '…', bare) }
  return { error: t.unknownKey(quote(name)) }
}

/** One changelog entry ({ version, title, body }) as plain text: the Markdown marks are left out. */
export function formatChangelog(entry, lang = 'en') {
  const body = String(entry?.body ?? '')
    .replace(/\r\n?/g, '\n')
    .replace(/^#{1,6}[ \t]+/gm, '')
    .replace(/\*\*([^*\n]+)\*\*/g, '$1')
    .replace(/`([^`\n]+)`/g, '$1')
    .replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, '$1 ($2)')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  const head = lang === 'zh' ? `更新日志 ${entry?.title ?? ''}` : `Changelog ${entry?.title ?? ''} (written in Chinese)`
  return `${head.trim()}\n\n${body}`
}

/** The status object (src/plugin.js, status()) as text. */
export function formatStatus(s, lang) {
  const t = TEXT[lang] ?? TEXT.en
  const lines = [t.statusTitle]
  const g = s.gateway
  if (g.mode === 'external') lines.push(`${t.labels.gateway}: external, ${word(t, g.state)}${g.url ? ` (${g.url})` : ''}`)
  else lines.push(`${t.labels.gateway}: managed, ${word(t, g.state)}`)
  if (g.mode === 'managed') {
    const p = s.python
    lines.push(`${t.labels.python}: ${!p ? t.pythonUnknown : p.found ? `${p.version} (${p.source}) ${p.executable}` : t.pythonNone}`)
    const r = s.retrieval
    lines.push(`${t.labels.retrieval}: ${r.setting}${r.active ? ` → ${r.active}${r.hybrid?.active ? ` + ${t.hybridWord}` : ''}` : ''}`)
    if (s.store) {
      const count = s.store.count === null ? t.countUnknown : t.memories(s.store.count)
      lines.push(`${t.labels.store}: ${s.store.type}, ${s.store.file}${s.store.keywordFile ? ` + ${s.store.keywordFile}` : ''}, ${count}`)
    }
  }
  const e = s.embed
  if (!e) lines.push(`${t.labels.embed}: ${t.notInstalled}`)
  else {
    const parts = [word(t, e.state)]
    if (e.error) parts.push(e.error)
    if (e.model) parts.push(e.model)
    if (e.dimensions) parts.push(`${e.dimensions}d`)
    if (e.parity === 'approximate') parts.push(t.approximate)
    lines.push(`${t.labels.embed}: ${parts.join(', ')}`)
  }
  if (s.migration) {
    const m = s.migration
    lines.push(`${t.labels.migration}: ${word(t, m.state === 'running' ? 'partial' : m.state)}, ${t.progress(m)}${m.kind ? ` (${m.kind})` : ''}`)
  }
  const on = Object.entries(s.modules).filter(([, v]) => v).map(([k]) => k)
  lines.push(`${t.labels.features}: ${on.length ? on.join(', ') : t.none}`)
  return lines.join('\n')
}

// ------------------------------------------------------------------ local embedding models

const MODEL_TEXT = {
  en: {
    title: (addon, link) => `Local embedding models (embedding add-on: ${addon}). Retrieval quality per tier: ${link}`,
    addon: { present: 'installed', outdated: 'installed, too old for one-click setup', installing: 'being installed', starting: 'installed, starting', absent: 'not installed' },
    states: { absent: 'not downloaded', downloading: 'downloading', installed: 'downloaded', active: 'in use', loading: 'loading', paused: 'interrupted; /memgate install resumes it' },
    languages: { zh: 'Chinese', en: 'English', multilingual: 'many languages' },
    line: (m, tier, size, memory, langs, licence, state) => `- ${m.id}${tier ? ` (${tier})` : ''}: ${size} download, ${memory}, ${langs}, ${licence}. State: ${state}.`,
    memory: (mb) => (mb ? `about ${mb} MB of memory while running` : 'memory not measured'),
    acceptance: (l) => `  Needs acceptance of the ${l.name}${l.url ? ` (${l.url})` : ''}.`,
    phases: { addon: 'installing the embedding add-on', builds: 'waiting for your decision on an install script', 'addon-starting': 'waiting for the embedding add-on to start', restart: 'waiting for a DSH restart', model: 'downloading', paused: 'download interrupted', activating: 'enabling', done: 'in use', idle: '', failed: 'stopped' },
    needBuilds: 'Usage: /memgate builds allow | /memgate builds deny',
    buildsAllowed: 'Allowed. Installing the embedding add-on again with the script permitted. Progress: /memgate models',
    noBuilds: 'No install script is waiting for a decision.',
    buildsAsk: 'Answer with /memgate builds allow or /memgate builds deny.',
    confirmWith: (command) => `To go ahead: ${command}`,
    pinnedMark: '  Pinned in the embedding add-on’s settings.',
    chain: (phase, id, percent) => `Setup of ${id}: ${phase}${percent !== null && percent !== undefined ? ` ${percent}%` : ''}.`,
    failure: (f) => `${f.title.en} ${f.todo.en}`,
    hint: 'Download and enable one: /memgate install <model>',
    noModels: 'Local models are not available in this host.',
    needModel: (sub) => `Usage: /memgate ${sub} <model>. /memgate models lists them.`,
    needAccept: (id, l, size) => `The ${id} weights come under the ${l.name}${l.url ? `: ${l.url}` : ''}.\nIf you have read and accept it: /memgate install ${id} --accept-licence ${l.name}\nNothing is downloaded before that (${size}).`,
    replies: {
      'installing-addon': (id, size) => `Installing the embedding add-on (dsh-feiyu-memgate-embed) through the host’s plugin manager; then ${id} downloads (${size}). Progress: /memgate models`,
      started: (id, size) => `Downloading ${id} (${size}); it is enabled when done. Progress: /memgate models`,
      running: (id) => `${id} is being set up already. Progress: /memgate models`,
      'starting-addon': (id) => `The embedding add-on is installed; once it starts, ${id} downloads. Progress: /memgate models`,
      'enabling-addon': (id) => `The embedding add-on is installed but was not switched on in this profile; switching it on through the host’s plugin manager, then ${id} downloads. Progress: /memgate models`,
      installed: (id) => `${id} is downloaded already; enabling it.`,
      active: (id) => `${id} is in use.`,
      activating: (id) => `Switching to ${id}; it is loading.`,
      removed: (id) => `Deleted the files of ${id}.`,
      cancelled: () => 'Cancelled.',
    },
    notRunning: 'Nothing is being installed or downloaded.',
    tooLate: 'Too late to cancel: the add-on is being applied.',
    isActive: (id) => `${id} is in use. Switch to another model first.`,
    isDownloading: (id) => `${id} is downloading. Cancel it first: /memgate cancel`,
    notInstalled: (id) => `${id} is not downloaded. Download it: /memgate install ${id}`,
  },
  zh: {
    title: (addon, link) => `本地向量模型（向量插件：${addon}）。各档位的检索效果：${link}`,
    addon: { present: '已安装', outdated: '已安装，但版本太旧，不支持一键设置', installing: '正在安装', starting: '已安装，正在启动', absent: '未安装' },
    states: { absent: '未下载', downloading: '下载中', installed: '已下载', active: '使用中', loading: '加载中', paused: '中断了，/memgate install 会继续下载' },
    languages: { zh: '中文', en: '英文', multilingual: '多语言' },
    line: (m, tier, size, memory, langs, licence, state) => `- ${m.id}${tier ? `（${tier}）` : ''}：下载 ${size}，${memory}，${langs}，${licence}。状态：${state}。`,
    memory: (mb) => (mb ? `运行内存约 ${mb} MB` : '运行内存未测'),
    acceptance: (l) => `  需要先同意 ${l.name}${l.url ? `（${l.url}）` : ''}。`,
    phases: { addon: '正在安装向量插件', builds: '等你对安装脚本做决定', 'addon-starting': '等待向量插件启动', restart: '等待 DSH 重启', model: '下载中', paused: '下载中断了', activating: '正在启用', done: '使用中', idle: '', failed: '已停止' },
    needBuilds: '用法：/memgate builds allow 或 /memgate builds deny',
    buildsAllowed: '已允许。正在带着脚本许可重新安装向量插件。进度：/memgate models',
    noBuilds: '没有等待决定的安装脚本。',
    buildsAsk: '用 /memgate builds allow 或 /memgate builds deny 回答。',
    confirmWith: (command) => `确认要装的话：${command}`,
    pinnedMark: '  向量插件的设置里指定了这个模型。',
    chain: (phase, id, percent) => `${id} 的设置：${phase}${percent !== null && percent !== undefined ? ` ${percent}%` : ''}。`,
    failure: (f) => `${f.title.zh}${f.todo.zh}`,
    hint: '下载并启用：/memgate install <模型>',
    noModels: '这个宿主里用不了本地模型。',
    needModel: (sub) => `用法：/memgate ${sub} <模型>。/memgate models 会列出模型。`,
    needAccept: (id, l, size) => `${id} 的权重受 ${l.name} 约束${l.url ? `：${l.url}` : ''}。\n读过并同意后：/memgate install ${id} --accept-licence ${l.name}\n同意之前不会下载任何东西（${size}）。`,
    replies: {
      'installing-addon': (id, size) => `正在通过宿主的插件管理器安装向量插件（dsh-feiyu-memgate-embed），之后下载 ${id}（${size}）。进度：/memgate models`,
      started: (id, size) => `正在下载 ${id}（${size}），完成后自动启用。进度：/memgate models`,
      running: (id) => `${id} 已经在设置中。进度：/memgate models`,
      'starting-addon': (id) => `向量插件已经安装，启动后开始下载 ${id}。进度：/memgate models`,
      'enabling-addon': (id) => `向量插件已经安装，但没有在这个配置里启用；正在通过宿主的插件管理器启用它，之后下载 ${id}。进度：/memgate models`,
      installed: (id) => `${id} 已经下载过了，正在启用。`,
      active: (id) => `${id} 正在使用。`,
      activating: (id) => `正在切换到 ${id}，模型加载中。`,
      removed: (id) => `已删除 ${id} 的文件。`,
      cancelled: () => '已取消。',
    },
    notRunning: '没有正在进行的安装或下载。',
    tooLate: '来不及取消了：插件正在应用。',
    isActive: (id) => `${id} 正在使用，先切换到别的模型。`,
    isDownloading: (id) => `${id} 正在下载，先取消：/memgate cancel`,
    notInstalled: (id) => `${id} 还没下载。下载：/memgate install ${id}`,
  },
}

/** A byte count for people: MB below a gigabyte, GB above, one decimal. */
export function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '?'
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${(bytes / 1e6).toFixed(1)} MB`
}

/** The model view (src/setup.js view()) as text. */
export function formatModels(view, lang) {
  const t = MODEL_TEXT[lang] ?? MODEL_TEXT.en
  const lines = [t.title(t.addon[view.addon.state] ?? view.addon.state, view.link)]
  for (const m of view.models) {
    const size = formatSize(m.sizeBytes)
    // The add-on's measurement of the running model process (loaded); never the file size.
    const memory = t.memory(m.memoryMB?.loaded ?? m.memoryMB?.peak ?? null)
    const tier = m.tierLabel ? m.tierLabel[lang] ?? m.tierLabel.en : ''
    const langs = m.languages.map((l) => t.languages[l] ?? l).join(lang === 'zh' ? '、' : ', ') || '?'
    const state = (t.states[m.state] ?? m.state) + (m.state === 'downloading' && m.percent !== null ? ` ${m.percent}%` : '')
    lines.push(t.line(m, tier, size, memory, langs, m.license?.name ?? '?', state))
    if (m.note) lines.push(`  ${m.note[lang] ?? m.note.en}`)
    if (m.requiresAcceptance && m.state === 'absent') lines.push(t.acceptance(m.requiresAcceptance))
    if (m.pinned) lines.push(t.pinnedMark)
  }
  const c = view.chain
  if (c.target && c.phase !== 'idle' && c.phase !== 'failed') lines.push(t.chain(t.phases[c.phase] ?? c.phase, c.target, c.percent))
  if (view.builds) lines.push(view.builds.note[lang] ?? view.builds.note.en, t.buildsAsk)
  if (c.error) lines.push(t.failure(c.error))
  if (view.python?.found === false) lines.push(t.failure(describeFailure('python-missing')))
  if (!view.models.some((m) => m.state === 'active')) lines.push(t.hint)
  return lines.join('\n')
}

function formatConfig(values, t) {
  const lines = [t.configTitle]
  for (const group of FIELDS) {
    for (const field of group.fields) {
      const key = `${group.group}.${field.key}`
      const value = show(t, field, values[group.group][field.key])
      const fallback = field.secret ? t.notSet : show(t, field, field.default)
      lines.push(`  ${key} = ${value}  (${fallback})${field.secret ? ` [${t.secret}]` : ''}`)
    }
  }
  return lines.join('\n')
}

function formatTiers(s, lang) {
  const t = TEXT[lang] ?? TEXT.en
  const r = s.retrieval
  const lines = [t.tiersTitle(r.setting, r.active)]
  if (s.gateway.mode === 'external') lines.push(t.tiersExternal)
  for (const tier of r.tiers) lines.push(t.tierLine({ ...tier, needs: TIERS[tier.name].needs[lang] ?? TIERS[tier.name].needs.en }))
  if (r.hybrid && s.gateway.mode !== 'external') lines.push(t.hybridLine(r.hybrid.setting, r.hybrid.active))
  return lines.join('\n')
}

/**
 * The command definition for ctx.commands.register().
 *   settings()  readConfig() now
 *   language()  'zh' | 'en'
 *   status()    Promise of the status object
 *   write(ops)  Promise of { ok: true } | { ok: false, reason: 'unavailable' | 'refused', kind? }
 *   models      the model setup (src/setup.js): view, install, use, remove, cancel; the same
 *               functions the settings page calls
 */
export function createMemgateCommand({ settings, language, status, write, models = null, changelog = null }) {
  const ok = (text) => ({ kind: 'success', text })
  const fail = (text) => ({ kind: 'error', text })
  // A failure with this profile's file names filled in (the setup knows them).
  const explain = (code) => models?.describe?.(code) ?? describeFailure(code)

  // The reply to a model action: what started, or what failed and what to do about it.
  async function modelReply(lang, id, result) {
    const t = MODEL_TEXT[lang] ?? MODEL_TEXT.en
    const view = await models.view()
    const entry = view.models.find((m) => m.id === id)
    const size = formatSize(entry?.sizeBytes)
    if (result.ok) return ok((t.replies[result.code] ?? t.replies.running)(id, size))
    switch (result.code) {
      case 'needs-acceptance':
        return fail(t.needAccept(id, result.licence ?? entry?.requiresAcceptance ?? { name: '?', url: '' }, size))
      case 'not-running':
        return fail(t.notRunning)
      case 'too-late':
        return fail(t.tooLate)
      case 'active':
        return fail(t.isActive(id))
      case 'downloading':
        return fail(t.isDownloading(id))
      case 'not-installed':
        return fail(t.notInstalled(id))
      default:
        return fail(t.failure(explain(result.code)))
    }
  }

  async function modelCommand(lang, t, sub, rest, words) {
    const mt = MODEL_TEXT[lang] ?? MODEL_TEXT.en
    if (!models) return fail(mt.noModels)
    if (sub === 'models') {
      if (words.length) return fail(t.noArgs(sub))
      return ok(formatModels(await models.view(), lang))
    }
    if (sub === 'cancel') {
      if (words.length) return fail(t.noArgs(sub))
      const result = await models.cancel()
      return result.ok ? ok(mt.replies.cancelled()) : modelReply(lang, '', result)
    }
    if (sub === 'builds') {
      // The answer to the install scripts pnpm stopped; typing it is the person's own decision.
      if (words.length !== 1 || !['allow', 'deny'].includes(words[0].toLowerCase())) return fail(mt.needBuilds)
      const result = await models.decideBuilds(words[0].toLowerCase() === 'allow', { confirmed: true })
      if (result.ok) return ok(mt.buildsAllowed)
      return result.code === 'not-running' ? fail(mt.noBuilds) : fail(mt.failure(explain(result.code)))
    }
    // install <model> [--accept-licence <name, may hold spaces>] [--confirm]
    let spec = rest
    const confirmed = /(^|\s)--confirm(\s|$)/.test(spec)
    if (confirmed) spec = spec.replace(/(^|\s)--confirm(?=\s|$)/g, ' ').trim()
    const match = /^(\S+)(?:\s+--accept-licen[cs]e\s+(.+))?$/s.exec(spec)
    if (!match || (sub !== 'install' && (match[2] !== undefined || confirmed))) return fail(mt.needModel(sub))
    const id = match[1]
    if (!PLAIN.test(id)) return fail(mt.needModel(sub))
    const result = sub === 'install' ? await models.install(id, { acceptLicence: match[2]?.trim(), confirmed })
      : sub === 'use' ? await models.use(id)
        : await models.remove(id)
    if (result.code === 'unknown-model') return fail(mt.failure(describeFailure('unknown-model')))
    if (result.code === 'confirm-required') {
      const view = await models.view()
      const entry = view.models.find((m) => m.id === id)
      const text = (ADDON_CONFIRM[lang] ?? ADDON_CONFIRM.en).replace('{model}', id).replace('{size}', formatSize(entry?.sizeBytes))
      const again = `/memgate install ${id}${match[2] ? ` --accept-licence ${match[2].trim()}` : ''} --confirm`
      return fail(`${text}\n${mt.confirmWith(again)}`)
    }
    return modelReply(lang, id, result)
  }

  async function save(t, ops) {
    let result
    try {
      result = await write(ops)
    } catch {
      result = { ok: false, reason: 'refused', kind: 'error' }
    }
    if (result?.ok) return null
    return fail(result?.reason === 'unavailable' ? t.noSettings : t.refused(result?.kind ?? 'error'))
  }

  async function setValue(t, key, field, value) {
    const current = settings()[field.group][field.key]
    if (!field.secret && current === value) return ok(t.already(key, show(t, field, value)))
    const refused = await save(t, [{ op: 'set', path: [field.group, field.key], value }])
    if (refused) return refused
    return ok(field.secret ? t.savedSecret(key) : t.saved(key, show(t, field, value), show(t, field, current)))
  }

  async function handler(invocation) {
    const lang = language()
    const t = TEXT[lang] ?? TEXT.en
    const raw = typeof invocation?.rawInput === 'string' ? invocation.rawInput : ''
    if (/[\r\n]/.test(raw.trim())) return fail(t.oneLine)
    const input = raw.trim()
    if (!input) return ok(t.usage)
    const [, head, tail = ''] = /^(\S+)(?:\s+(.*))?$/s.exec(input)
    const sub = head.toLowerCase()
    const rest = tail.trim()
    const words = rest ? rest.split(/\s+/) : []
    try {
      switch (sub) {
        case 'help':
          return ok(t.usage)
        case 'status':
          if (words.length) return fail(t.noArgs(sub))
          return ok(formatStatus(await status(), lang))
        case 'config':
          if (words.length) return fail(t.noArgs(sub))
          return ok(formatConfig(settings(), t))
        case 'changelog': {
          if (words.length) return fail(t.noArgs(sub))
          const found = typeof changelog === 'function' ? changelog() : null
          const entry = found?.entries?.[0]
          return ok(entry ? formatChangelog(entry, lang) : t.noChangelog)
        }
        case 'tiers':
          if (words.length) return fail(t.noArgs(sub))
          return ok(formatTiers(await status(), lang))
        case 'set': {
          const match = /^(\S+)\s+(.+)$/s.exec(rest)
          if (!match) return fail(t.needKeyValue)
          const target = resolveSetting(match[1], t)
          if (target.error) return fail(target.error)
          const parsed = parseSetting(target.field, match[2])
          if (!parsed.ok) return fail(t.invalid[parsed.reason](target.key, target.field))
          return await setValue(t, target.key, target.field, parsed.value)
        }
        case 'reset': {
          if (words.length !== 1) return fail(t.needKey(sub))
          const target = resolveSetting(words[0], t)
          if (target.error) return fail(target.error)
          const refused = await save(t, [{ op: 'unset', path: [target.field.group, target.field.key] }])
          if (refused) return refused
          return ok(target.field.secret ? t.resetSecret(target.key) : t.resetDone(target.key, show(t, target.field, target.field.default)))
        }
        case 'on':
        case 'off': {
          if (words.length !== 1) return fail(t.needModule(sub))
          const name = words[0].replace(/^modules\./i, '').toLowerCase()
          const module = MODULE_KEYS.find((k) => k.toLowerCase() === name)
          if (!module) return fail(t.unknownModule(quote(words[0])))
          return await setValue(t, `modules.${module}`, SETTING_KEYS[`modules.${module}`], sub === 'on')
        }
        case 'models':
        case 'install':
        case 'builds':
        case 'remove':
        case 'cancel':
          return await modelCommand(lang, t, sub, rest, words)
        case 'use': {
          if (words.length !== 1) return fail(t.needTier)
          const tier = words[0].toLowerCase()
          // A word that is not a tier names a local model (ids never equal a tier name).
          if (!TIER_CHOICES.includes(tier) && models && PLAIN.test(words[0])) return await modelCommand(lang, t, sub, rest, words)
          if (!TIER_CHOICES.includes(tier)) return fail(t.unknownTier(quote(words[0])))
          const reply = await setValue(t, 'retrieval.tier', SETTING_KEYS['retrieval.tier'], tier)
          if (reply.kind !== 'success') return reply
          // Availability does not depend on the setting, so it holds even before the host has applied
          // the write; what auto resolves to is derived from it for the same reason.
          const now = await status()
          const available = (name) => Boolean(now.retrieval.tiers.find((x) => x.name === name)?.available)
          const lines = [reply.text]
          if (now.gateway.mode === 'external') lines.push(t.tiersExternal)
          else if (tier === 'auto') lines.push(t.tierNow(available('local') ? 'local' : 'keyword'))
          else if (!available(tier)) lines.push(t.tierUnavailable(TIERS[tier].needs[lang] ?? TIERS[tier].needs.en))
          return ok(lines.join('\n'))
        }
        default:
          return fail(`${t.unknownSub(quote(head))}\n${t.usage}`)
      }
    } catch {
      // A broken status read or settings write must not throw into the host's command runner.
      return fail(t.refused('error'))
    }
  }

  return {
    name: COMMAND_NAME,
    description: 'Long-term memory: status, settings, retrieval tiers and local models (feiyu-memgate)',
    input: { hint: 'status | config | set <key> <value> | reset <key> | on|off <feature> | tiers | use <tier|model> | models | install <model> [--confirm] | builds allow|deny | remove <model> | cancel | changelog' },
    // The input may hold a secret (`set external.token ...`): keep it out of the session log; the
    // reply says what changed without repeating it (commands/src/index.ts:70-75, :376).
    recordInput: false,
    handler,
  }
}
