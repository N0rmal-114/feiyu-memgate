// Settings: the schema the host turns into its Settings form, the plain values the add-on works
// with, and where its files live.
//
// Every group is declared `.volatile()`, as in the base plugin: the host's Settings form shows only
// volatile fields (packages/settings/settings/README.md:33; settings/src/schema.ts:37-47,
// volatileForm), and a change of volatile values only is committed without remounting the plugin and
// announced through `loader/volatile-update` (vendor/loader/src/config/entry.ts:194; installed
// 0.2.2-rc.1: cordis-plugin-loader/lib/index.js). The add-on reads its settings live and reconciles on
// that event.
//
// A choice field is a Schemastery union of constants, each with an en/zh description through
// i18n(), keyed by the host's locale ids (packages/client/locale/src/locale-settings.ts:15).
//
// readConfig() repeats the validation in plain JavaScript, so nothing reaches the add-on unchecked,
// and tests run without the host's schema library.
import os from 'node:os'
import path from 'node:path'
import { MODELS } from './models.js'

const VOLATILE_WRITE = Symbol.for('cosmokit.volatile.write') // vendor/cosmokit/src/volatile.ts, isVolatile()

export const ORIGINS = Object.freeze({ huggingface: 'https://huggingface.co', 'hf-mirror': 'https://hf-mirror.com' })
// A Hugging Face compatible origin: scheme, host and optional port, nothing else (the same rule as the
// host's SenseVoice plugin, packages/experimental/speech-to-text-sensevoice/src/config.ts:190).
const ORIGIN_RE = /^https?:\/\/[^/\s?#@]+\/?$/

/**
 * An origin setting as { kind, origins }: 'auto' compares both public origins, 'huggingface' and
 * 'hf-mirror' name one, and a URL names a mirror of your own. Returns null for anything else.
 */
export function parseOrigin(value) {
  if (typeof value !== 'string') return null
  const text = value.trim()
  if (text === 'auto') return { kind: 'auto', origins: [ORIGINS.huggingface, ORIGINS['hf-mirror']] }
  if (Object.hasOwn(ORIGINS, text)) return { kind: text, origins: [ORIGINS[text]] }
  if (!ORIGIN_RE.test(text)) return null
  try {
    const url = new URL(text)
    if ((url.protocol !== 'https:' && url.protocol !== 'http:') || url.username || url.password) return null
    return { kind: 'custom', origins: [url.origin] }
  } catch {
    return null
  }
}

const sizeLabel = (bytes) => (bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2)} GB` : `${Math.round(bytes / 1e6)} MB`)

// One row per field, in form order. kind: boolean | integer | choice | string.
const GROUPS = [
  {
    key: 'modules',
    en: 'Parts of the add-on. Each one is switched on or off on its own; a switched-off part leaves nothing behind in the host.',
    zh: '向量插件的各个功能，可分别开启或关闭。关闭的功能不会在宿主中留下任何内容。',
    fields: [
      { key: 'engine', kind: 'boolean', default: true,
        en: 'Run the active embedding model on this computer and offer it to the feiyu-memgate memory plugin. Off: the memory plugin keeps its keyword search.',
        zh: '在本机运行当前向量模型，供 feiyu-memgate 记忆插件使用。关闭后，记忆插件继续使用关键词检索。' },
      { key: 'command', kind: 'boolean', default: true,
        en: 'The /memgate-embed chat command, to list, download, switch and remove models.',
        zh: '聊天命令 /memgate-embed，用于列出、下载、切换和删除向量模型。' },
    ],
  },
  {
    key: 'model',
    en: 'Which model runs and how.',
    zh: '选择运行的模型及其运行方式。',
    fields: [
      { key: 'activeModel', kind: 'choice', default: 'none',
        options: [
          { value: 'none', en: 'none: nothing pinned here; the model chosen with /memgate-embed use runs, if any', zh: 'none：不在此处指定；运行通过 /memgate-embed use 选择的模型（如已选择）' },
          ...MODELS.map((m) => ({
            value: m.id,
            en: `${m.id} (${sizeLabel(m.sizeBytes)}, ${m.dimensions} dimensions, ${m.license.name})`,
            zh: `${m.id}（${sizeLabel(m.sizeBytes)}，${m.dimensions} 维，${m.license.name}）`,
          })),
        ],
        en: 'Active model. A model chosen here wins over /memgate-embed use. It must have been downloaded with /memgate-embed add first.',
        zh: '当前模型。在此处选择后以此处为准，/memgate-embed use 不再生效。需先用 /memgate-embed add 下载该模型。' },
      { key: 'autoStart', kind: 'boolean', default: true,
        en: 'Load the active model when the host starts. Off: it loads on the first request, which then waits for the load.',
        zh: '宿主启动时加载当前模型。关闭后，首次请求时才加载，该请求需等待加载完成。' },
      { key: 'idleUnloadSeconds', kind: 'integer', default: 0, min: 0, max: 86400,
        en: 'Unload the model after this many seconds without a request, to free its memory; the next request loads it again. 0: never unload.',
        zh: '无请求超过该秒数后卸载模型并释放内存，下一次请求时重新加载。0：从不卸载。' },
      { key: 'threads', kind: 'integer', default: 0, min: 0, max: 64,
        en: 'CPU threads for the model. 0: chosen automatically.',
        zh: '模型使用的 CPU 线程数。0：自动。' },
      { key: 'overflow', kind: 'choice', default: 'truncate',
        options: [
          { value: 'truncate', en: 'truncate: embed the beginning that fits; the memory is still stored and returned in full', zh: 'truncate：仅对可容纳的开头部分生成向量；记忆本身仍完整保存和返回' },
          { value: 'reject', en: 'reject: refuse the text, so the memory gateway reports the write or search as failed', zh: 'reject：拒绝该文本，记忆网关将此次写入或检索报告为失败' },
        ],
        en: 'Text longer than the model accepts. truncate (what Ollama does by default, and what the gateway\'s calibration went through): a long memory is found by its beginning only. reject: such a memory is not saved.',
        zh: '超过模型上限的文本的处理方式。truncate（Ollama 的默认做法，网关的校准也按此测得）：长记忆仅按开头部分检索。reject：此类记忆无法保存。' },
    ],
  },
  {
    key: 'download',
    en: 'Where models come from and where they are kept.',
    zh: '模型的下载来源和存放位置。',
    fields: [
      { key: 'origin', kind: 'string', default: 'auto',
        en: 'Download source: auto (compare huggingface.co and hf-mirror.com, use the first that answers), huggingface, hf-mirror, or the address of a Hugging Face compatible mirror such as https://mirror.example. Anything other than auto wins over /memgate-embed origin.',
        zh: '下载来源：auto（比较 huggingface.co 和 hf-mirror.com，使用最先响应的一个）、huggingface、hf-mirror，或 Hugging Face 兼容镜像的地址，例如 https://mirror.example。不是 auto 时以此处为准，/memgate-embed origin 不再生效。' },
      { key: 'dataDir', kind: 'string', default: '',
        en: 'Folder for the models and the add-on state. Empty: data/feiyu-memgate in the DSH home folder (models in its models subfolder).',
        zh: '存放模型和向量插件状态的文件夹。留空：DSH 主目录下的 data/feiyu-memgate（模型位于其中的 models 子文件夹）。' },
    ],
  },
]

function deepFreeze(value) {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) deepFreeze(child)
    Object.freeze(value)
  }
  return value
}

/** The default value of every setting, grouped as in the form. */
export const DEFAULTS = deepFreeze(Object.fromEntries(GROUPS.map((group) => [
  group.key, Object.fromEntries(group.fields.map((field) => [field.key, field.default])),
])))

/** The field table, for tests and for anything that lists the settings. */
export const FIELDS = deepFreeze(GROUPS.map((group) => ({ group: group.key, fields: group.fields.map((f) => ({ ...f })) })))

const describe = (schema, row) => schema.description(row.en).i18n({ en: { $description: row.en }, zh: { $description: row.zh } })

function fieldSchema(Schema, field) {
  let schema
  switch (field.kind) {
    case 'boolean':
      schema = Schema.boolean()
      break
    case 'integer':
      schema = Schema.number().min(field.min).max(field.max).step(1)
      break
    case 'choice':
      schema = Schema.union(field.options.map((option) => describe(Schema.const(option.value), option)))
      break
    default:
      schema = Schema.string()
  }
  return describe(schema.default(field.default), field)
}

/**
 * The add-on's Config schema, built with the host's Schemastery (passed in: the host package is a
 * peer that tests do not have).
 * @param {any} Schema the default export of @deepseek-ai/schemastery
 */
export function buildConfigSchema(Schema) {
  return Schema.object(Object.fromEntries(GROUPS.map((group) => [
    group.key,
    describe(Schema.object(Object.fromEntries(group.fields.map((field) => [field.key, fieldSchema(Schema, field)]))), group).volatile(),
  ])))
}

// A group as the host hands it over: a volatile reference (read it now, never keep the snapshot) or,
// outside the host, a plain object.
function unwrap(value) {
  if (value !== null && typeof value === 'object' && VOLATILE_WRITE in value && typeof value.get === 'function') return value.get()
  return value
}

function checked(field, value) {
  switch (field.kind) {
    case 'boolean':
      return typeof value === 'boolean' ? value : field.default
    case 'integer':
      return Number.isInteger(value) && value >= field.min && value <= field.max ? value : field.default
    case 'choice':
      return field.options.some((option) => option.value === value) ? value : field.default
    default:
      return typeof value === 'string' ? value.trim() : field.default
  }
}

/**
 * The current settings as plain, checked values. Call it whenever a value is needed: volatile groups
 * change in place while the add-on runs. An origin that is not valid reads as 'auto' and is reported in
 * `invalid`.
 * @param {object} [config] the `config` argument of apply()
 */
export function readConfig(config) {
  const out = {}
  for (const group of GROUPS) {
    const raw = unwrap(config?.[group.key])
    const source = raw !== null && typeof raw === 'object' ? raw : {}
    out[group.key] = Object.fromEntries(group.fields.map((field) => [field.key, checked(field, source[field.key])]))
  }
  out.invalid = []
  if (!parseOrigin(out.download.origin)) {
    out.invalid.push('download.origin')
    out.download.origin = 'auto'
  }
  return out
}

function expandHome(p, home = os.homedir()) {
  return p === '~' || p.startsWith('~/') || p.startsWith('~\\') ? path.join(home, p.slice(1)) : p
}

/** The DSH home: $DSH_HOME (blank counts as unset), else ~/.dsh (packages/util/home-paths/src/index.ts:135-149). */
export function resolveDshHome(env = process.env, home = os.homedir()) {
  const fromEnv = env.DSH_HOME
  return path.resolve(expandHome(fromEnv !== undefined && fromEnv.trim() ? fromEnv : path.join(home, '.dsh'), home))
}

/**
 * The add-on's data folder. An explicit setting wins. Otherwise `$DSH_HOME/data/feiyu-memgate`, the
 * folder the base plugin uses too: 0.2.2-rc.3 puts new durable records under a category of the DSH
 * home, `dshPath('data', owner, ...)` (packages/util/home-paths/src/index.ts:176-214), and the host
 * provides that function as the `dshPath` service (packages/boot/app-boot/src/index.ts:1238).
 * 0.2.2-rc.1 has neither, so the same path is built from the `dshHomePath` service (rc.3:
 * app-boot/src/index.ts:1237; rc.1: dsh-app-boot/lib/index.js:5284) or, outside the host, from the
 * environment.
 * @param {string} setting  download.dataDir
 * @param {{ get?: (name: string) => any }} [ctx]
 */
export function resolveDataDir(setting, ctx, env = process.env) {
  if (setting) return path.resolve(expandHome(setting))
  const service = (name) => {
    try {
      const fn = ctx?.get?.(name)
      return typeof fn === 'function' ? fn : null
    } catch {
      return null
    }
  }
  try {
    const dshPath = service('dshPath')
    if (dshPath) return path.resolve(dshPath('data', 'feiyu-memgate'))
    const dshHomePath = service('dshHomePath')
    if (dshHomePath) return path.resolve(dshHomePath('data', 'feiyu-memgate'))
  } catch {
    // fall back to the environment below
  }
  return path.join(resolveDshHome(env), 'data', 'feiyu-memgate')
}

/**
 * 'en' or 'zh' for command replies: the UI language the user picked in the host settings (the
 * `locale` section's `preference`, read the way packages/experimental/timeline/src/index.ts:39 does),
 * else Chinese.
 */
export function uiLanguage(ctx) {
  try {
    const settings = ctx?.get?.('settings')
    const row = settings?.describe?.({ redactSecrets: true })?.find?.((item) => item?.ns === 'locale')
    const preference = row?.value?.preference
    if (typeof preference === 'string' && /^en\b/i.test(preference)) return 'en'
  } catch {
    // the host does not expose it: fall through
  }
  return 'zh'
}
