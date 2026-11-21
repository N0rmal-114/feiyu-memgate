// Brings the settings page script up to date with src/config.js, in two steps:
//   1. writes client/src/fields.cjs, the page's copy of the settings field table (FIELDS and INDEX);
//   2. assembles client/client.js from client/src (scripts/build-client.mjs).
//
// The browser half is a plain script the page loads as it is, so it cannot import the host half's
// modules; the generated fields.cjs keeps its one copy of the table in step. Run it after changing a setting:
//   npm run sync-client            rewrite whatever is stale
//   npm run sync-client -- --check exit 1 when either file is stale, change nothing (tests/client-page.test.js does the same check)
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { FIELDS } from '../src/config.js'
import { build } from './build-client.mjs'

const FIELDS_FILE = fileURLToPath(new URL('../client/src/fields.cjs', import.meta.url))

/** The text of client/src/fields.cjs for a field table. */
export function renderFields(fields = FIELDS) {
  return `'use strict'

// GENERATED from src/config.js by scripts/sync-client.mjs: do not edit; change the settings there and run
// npm run sync-client. FIELDS is the settings field table of the page; INDEX is the same fields by their
// "group.key" name, each with its group added.

const FIELDS = ${JSON.stringify(fields, null, 2)}
const INDEX = {}
for (const group of FIELDS) for (const field of group.fields) INDEX[group.group + '.' + field.key] = Object.assign({ group: group.group }, field)

module.exports = { FIELDS, INDEX }
`
}

/**
 * Write (or with `check`, only compare) client/src/fields.cjs, then do the same for client/client.js.
 * @returns {{ fields: { current: boolean, written: boolean }, client: { current: boolean, written: boolean } }}
 */
export function sync({ check = false, fieldsFile = FIELDS_FILE } = {}) {
  const next = renderFields()
  const previous = fs.existsSync(fieldsFile) ? fs.readFileSync(fieldsFile, 'utf8').replace(/\r\n?/g, '\n') : null
  const current = previous === next
  if (!check && !current) fs.writeFileSync(fieldsFile, next, 'utf8')
  const client = build({ check })
  return { fields: { current, written: !check && !current }, client: { current: client.current, written: client.written } }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1])) {
  const check = process.argv.includes('--check')
  const result = sync({ check })
  if (check) {
    const stale = []
    if (!result.fields.current) stale.push('client/src/fields.cjs')
    if (!result.client.current) stale.push('client/client.js')
    if (stale.length) {
      console.error(`${stale.join(' and ')}: stale; run npm run sync-client`)
      process.exit(1)
    }
  } else {
    const changed = [result.fields.written && 'client/src/fields.cjs', result.client.written && 'client/client.js'].filter(Boolean)
    console.log(changed.length ? `updated ${changed.join(' and ')}` : 'client/src/fields.cjs and client/client.js already current')
  }
}
