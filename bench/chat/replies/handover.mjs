// The memory blocks of a hand-over, worded exactly as the plugin writes them (reply-quality evaluation).
//
//   node bench/chat/replies/handover.mjs [--plugin <plugin folder>] < items.json > blocks.json
//
// stdin: a JSON list of {id, language, today, tone, main: [{text, date}], extra: [{text, date}], more}
//   language  'zh' or 'en' (the plugin's language)      today  "YYYY-MM-DD" (the plugin's showAge, always on here)
//   tone      'normal' or 'strict' (advanced.blockTone, main block only)
//   main      the memories of the automatic block      extra  the memories of the second block (pool form)
//   more      optional: how many related memories did not fit (the "more" line of the main block; default 0)
// stdout: {id: {main: string|null, extra: string|null}}, null when the block would be empty.
//
// The wording is not written here. The formatter is createFormatter of src/block.js. The heads of the strict tone and
// of the second block are constants inside src/plugin.js that it does not export, so they are read out of that file's
// text (nothing in the plugin is changed or run). The calls mirror plugin.js: blockFormatter() for the main block (the
// strict head under blockTone 'strict', else the formatter's own head), poolFormatter() for the second block, both
// format(results, more, {today}); the second block never has a "more" line.
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

function pluginFolder(argv) {
  const at = argv.indexOf('--plugin')
  if (at >= 0 && argv[at + 1]) return resolve(argv[at + 1])
  return resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'plugins', 'dsh-feiyu-memgate')
}

// The object literal of `const NAME = Object.freeze({...})` in the plugin source.
function constantOf(source, name) {
  const match = new RegExp(`const ${name} = Object\\.freeze\\((\\{[\\s\\S]*?\\})\\)`).exec(source)
  if (!match) throw new Error(`${name} not found in plugin.js`)
  return new Function(`return (${match[1]})`)()
}

function readStdin() {
  return new Promise((done, fail) => {
    const chunks = []
    process.stdin.on('data', (chunk) => chunks.push(chunk))
    process.stdin.on('end', () => done(Buffer.concat(chunks).toString('utf8')))
    process.stdin.on('error', fail)
  })
}

const asResults = (list) => (Array.isArray(list) ? list : []).map((m) => ({ text: m.text, date: m.date }))

const folder = pluginFolder(process.argv.slice(2))
const { createFormatter } = await import(pathToFileURL(resolve(folder, 'src', 'block.js')).href)
const source = readFileSync(resolve(folder, 'src', 'plugin.js'), 'utf8')
const STRICT_HEADS = constantOf(source, 'STRICT_HEADS')
const POOL_HEADS = constantOf(source, 'POOL_HEADS')

const formatters = new Map()
function formatterFor(kind, lang, tone) {
  const key = `${kind}:${lang}:${tone}`
  if (!formatters.has(key)) {
    if (kind === 'pool') formatters.set(key, createFormatter({ lang, head: POOL_HEADS[lang] }))
    else if (tone === 'strict') formatters.set(key, createFormatter({ lang, head: STRICT_HEADS[lang] }))
    else formatters.set(key, createFormatter({ lang }))
  }
  return formatters.get(key)
}

const items = JSON.parse(await readStdin())
const out = {}
for (const item of items) {
  const lang = item.language ?? 'zh'
  const options = item.today ? { today: item.today } : undefined
  const main = asResults(item.main)
  const extra = asResults(item.extra)
  const mainBlock = main.length ? formatterFor('main', lang, item.tone ?? 'normal').format(main, item.more ?? 0, options) : ''
  const extraBlock = extra.length ? formatterFor('pool', lang, 'normal').format(extra, 0, options) : ''
  out[item.id] = { main: mainBlock || null, extra: extraBlock || null }
}
process.stdout.write(JSON.stringify(out) + '\n')
