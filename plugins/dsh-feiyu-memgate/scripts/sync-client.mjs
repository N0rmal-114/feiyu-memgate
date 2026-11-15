// Copies the settings field table of src/config.js into client/client.js, between its <fields> markers.
//
// The browser half is a plain script the page loads as it is (no bundler), so it cannot import the
// host half's modules; this keeps its one copy of the table in step. Run it after changing a setting:
//   npm run sync-client            rewrite the block
//   npm run sync-client -- --check exit 1 when the block is stale (tests/client-page.test.js does the same check)
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { FIELDS } from '../src/config.js'

const file = fileURLToPath(new URL('../client/client.js', import.meta.url))
const MARKERS = /( *)\/\/ <fields>\r?\n[\s\S]*?\/\/ <\/fields>/

export function render(source) {
  const match = MARKERS.exec(source)
  if (!match) throw new Error('client/client.js has no <fields> block')
  const indent = match[1]
  const table = JSON.stringify(FIELDS, null, 2).split('\n').join('\n' + indent)
  const block = `${indent}// <fields>\n${indent}// Generated from src/config.js by scripts/sync-client.mjs; edit the settings there.\n${indent}const FIELDS = ${table}\n${indent}// </fields>`
  return source.replace(MARKERS, () => block)
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1])) {
  const source = fs.readFileSync(file, 'utf8')
  const next = render(source)
  if (process.argv.includes('--check')) {
    if (next !== source) {
      console.error('client/client.js: the field table is stale; run npm run sync-client')
      process.exit(1)
    }
  } else if (next !== source) {
    fs.writeFileSync(file, next, 'utf8')
    console.log('client/client.js: field table updated')
  } else {
    console.log('client/client.js: field table already current')
  }
}
