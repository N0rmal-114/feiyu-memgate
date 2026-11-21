'use strict'
// The settings of the page arranged for drawing: the layout table (layout.cjs) joined with the field
// table (fields.cjs) and the state of the form (model/form.cjs). Pure: no React, no DOM.
//
// Two ideas live here.
//   - Dependence. A setting with `needs` (src/config.js) only has an effect while another setting has
//     a fitting value; the page shows it as a sub-row of that setting, and only then. The value that
//     counts is the one on screen (a staged edit included), so flipping a switch opens its sub-rows
//     before anything is saved.
//   - Change marks. A tab or a group that is closed still has to say that something inside it was
//     edited or cannot be saved, so both are counted per section and per tab.

const { INDEX } = require('../fields.cjs')
const { TABS } = require('../layout.cjs')
const { fieldView } = require('./form.cjs')

/**
 * Split an option label of the form "name: what it means" (both the ASCII and the full-width colon
 * are used in src/config.js) into its two parts. A label without a colon is all name.
 * @returns {{ name: string, note: string }}
 */
function splitOption(label) {
  const text = String(label === undefined || label === null ? '' : label)
  const match = /^([^:：]{1,24})[:：]\s*(.+)$/.exec(text)
  return match ? { name: match[1].trim(), note: match[2].trim() } : { name: text, note: '' }
}

/** Whether a setting's `needs` is met by the values on screen (`values`: key → value). */
function isMet(field, values) {
  if (!field.needs) return true
  return field.needs.in.includes(values[field.needs.key])
}

/**
 * One section ready to draw.
 * @param {object} section a section of layout.cjs
 * @param {object} state the form state of the host ({ value, base, user })
 * @param {object} draft the staged edits (key → { value } | { unset })
 * @param {object | null} secrets which secrets hold a value (key → boolean), from the status route
 * @returns {{ section: object, rows: object[], staged: number, invalid: number, overridden: number }}
 *   rows: { view, parent, shown }: `view` is fieldView()'s answer, `parent` the key this row depends
 *   on (or null), `shown` whether its `needs` is met. Counts cover shown and hidden rows alike:
 *   a staged edit of a row that has since been hidden is still saved.
 */
function sectionView(section, state, draft, secrets) {
  const views = section.fields.map((key) => {
    const field = INDEX[key]
    return fieldView(field, field.group, state, draft, secrets)
  })
  // The values on screen for every setting some row of this section depends on.
  const values = {}
  for (const view of views) {
    const needs = view.field.needs
    if (!needs || needs.key in values) continue
    const other = INDEX[needs.key]
    values[needs.key] = fieldView(other, other.group, state, draft, secrets).value
  }
  const rows = views.map((view) => ({
    view,
    parent: view.field.needs && section.fields.includes(view.field.needs.key) ? view.field.needs.key : null,
    shown: isMet(view.field, values),
  }))
  return {
    section,
    rows,
    staged: views.filter((view) => draft[view.k] !== undefined).length,
    invalid: rows.filter((row) => row.shown && row.view.invalid).length,
    overridden: views.filter((view) => view.overridden).length,
  }
}

/**
 * Every settings tab ready to draw: { tab, sections: sectionView[], staged, invalid }.
 */
function pageView(state, draft, secrets) {
  return TABS.map((tab) => {
    const sections = tab.sections.map((section) => sectionView(section, state, draft, secrets))
    return {
      tab,
      sections,
      staged: sections.reduce((sum, s) => sum + s.staged, 0),
      invalid: sections.reduce((sum, s) => sum + s.invalid, 0),
    }
  })
}

/**
 * The staged edits a save may write: all of them, except an edit the field would refuse on a row
 * that is closed right now. Such a draft cannot be seen, so it cannot be corrected; dropping it
 * keeps one forgotten entry from blocking every later save.
 * @param {object} draft the staged edits
 * @param {object[]} tabViews pageView()'s answer for the same draft
 */
function savable(draft, tabViews) {
  const out = Object.assign({}, draft)
  for (const tabView of tabViews) {
    for (const section of tabView.sections) {
      for (const row of section.rows) if (!row.shown && row.view.invalid) delete out[row.view.k]
    }
  }
  return out
}

module.exports = { splitOption, isMet, sectionView, pageView, savable }
