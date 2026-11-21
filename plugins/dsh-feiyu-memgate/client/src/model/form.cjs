'use strict'

// The settings form as plain data: what each field shows, which staged edits are valid, and the
// Settings operations a save sends. Pure functions of their arguments; never touches React or the DOM.

const { INDEX } = require('../fields.cjs')

// A value inside nested objects by path, or undefined when any step is missing (own properties only).
function at(value, path) {
  let node = value
  for (const key of path) {
    if (node === null || typeof node !== 'object' || !Object.prototype.hasOwnProperty.call(node, key)) return undefined
    node = node[key]
  }
  return node
}

/** Whether a staged text is a value the field accepts (the host validates again on save). */
function checkText(field, text) {
  const raw = String(text).trim()
  if (field.kind === 'integer') {
    if (!/^-?\d{1,9}$/.test(raw)) return false
    const n = Number(raw)
    return n >= field.min && n <= field.max
  }
  if (field.kind === 'string' && field.format === 'url' && raw) return /^https?:\/\/[^\s/]+\S*$/i.test(raw)
  return true
}

/** What one field shows: its current or staged value, whether it is changed, invalid or being cleared. */
function fieldView(field, groupKey, state, draft, secrets) {
  const k = groupKey + '.' + field.key
  const staged = draft[k]
  const path = [groupKey, field.key]
  if (field.secret) {
    return {
      k, field, secret: true,
      text: staged && !staged.unset ? staged.value : '',
      clearing: Boolean(staged && staged.unset),
      configured: secrets && typeof secrets[k] === 'boolean' ? secrets[k] : null,
      invalid: false, overridden: false,
    }
  }
  const inherited = at(state.base, path) !== undefined ? at(state.base, path) : field.default
  const stored = at(state.value, path) !== undefined ? at(state.value, path) : field.default
  const value = staged ? (staged.unset ? inherited : staged.value) : stored
  const text = field.kind === 'integer' || field.kind === 'string' ? String(value) : value
  return {
    k, field, secret: false, value, text,
    overridden: staged ? !staged.unset : at(state.user, path) !== undefined,
    invalid: (field.kind === 'integer' || field.kind === 'string') && !checkText(field, text),
  }
}

/** The staged edits as Settings path operations, in field order. */
function buildOps(draft) {
  const ops = []
  for (const k of Object.keys(INDEX)) {
    const staged = draft[k]
    if (!staged) continue
    const field = INDEX[k]
    const path = [field.group, field.key]
    if (staged.unset) {
      ops.push({ op: 'unset', path })
      continue
    }
    if (field.secret && staged.value === '') continue // an empty secret draft keeps the stored value
    let value = staged.value
    if (field.kind === 'integer') value = Number(String(value).trim())
    else if (field.kind === 'string') value = String(value).trim()
    ops.push({ op: 'set', path, value })
  }
  return ops
}

/** Write every staged edit in one mutation, fenced by the revision the edits were made against. */
async function saveDraft(form, draft) {
  const ops = buildOps(draft)
  if (!ops.length) return true
  return (await form.mutate(ops, form.state.revision)) === true
}

module.exports = { at, checkText, fieldView, buildOps, saveDraft }
