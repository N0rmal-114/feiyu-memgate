'use strict'
// One settings tab: its sections, each a titled list of rows (view/field-row.cjs).
//
// A row that depends on another setting (`needs`) is always in the document and opens and closes
// with that setting, so flipping a switch shows what it brings with it. In a tab marked
// `collapsible` (Advanced) every section is a folding group that starts closed, with a tag saying
// how many of its settings differ from their defaults: what was changed is found without opening
// everything.

const { h } = require('../react.cjs')
const { ID_PREFIX } = require('../constants.cjs')
const { Tag } = require('../kit/controls.cjs')
const { Fold, Group, Notice, Reveal } = require('../kit/layout.cjs')
const { cx } = require('../theme/styles.cjs')
const { FieldRow } = require('./field-row.cjs')

/** The rows of a section: plain rows as they are, dependent rows inside a part that opens and closes. */
function rowsOf(sectionView, props) {
  return sectionView.rows.map((row) => {
    const element = h(FieldRow, { key: row.view.k, t: props.t, lang: props.lang, row, disabled: props.disabled, edit: props.edit, reset: props.reset })
    return row.view.field.needs ? h(Reveal, { key: row.view.k, open: row.shown }, element) : element
  })
}

/** What a closed group says about its content: unsaved edits (a dot) and changed settings (a tag). */
function foldAccessory(sectionView, t) {
  const parts = []
  if (sectionView.overridden > 0) parts.push(h(Tag, { key: 'changed', tone: 'sea' }, t('changedCount', { count: sectionView.overridden })))
  if (sectionView.invalid > 0 || sectionView.staged > 0) {
    parts.push(h('span', {
      key: 'mark', role: 'img', 'aria-label': t(sectionView.invalid > 0 ? 'fixFirst' : 'tabEdited'),
      className: cx('fmg-mark', sectionView.invalid > 0 && 'fmg-mark--invalid'),
    }))
  }
  return parts.length ? h('span', { className: 'fmg-actions' }, parts) : null
}

/**
 * @param {{ t: Function, lang: 'en' | 'zh', tabView: object, disabled: boolean, readOnly: boolean,
 *   edit: Function, reset: Function, open: Record<string, boolean>, onToggle: (sectionId: string) => void,
 *   panels: Record<string, any> }} props
 *   `tabView` is one entry of model/page.cjs pageView(); `panels` holds the parts of the page that a
 *   section places by name (layout.cjs `panel`), already built.
 */
function SettingsTab(props) {
  const { t, lang, tabView, open, onToggle, panels } = props
  const tab = tabView.tab
  const sections = tabView.sections.map((sectionView) => {
    const section = sectionView.section
    const title = section.label[lang] || section.label.en
    const help = section.help ? (section.help[lang] || section.help.en) : ''
    const rows = rowsOf(sectionView, props)
    const panel = section.panel ? panels[section.panel] : null
    if (tab.collapsible) {
      return h(Fold, {
        key: section.id,
        id: ID_PREFIX + '-fold-' + section.id,
        title, help,
        open: open[section.id] === true,
        onToggle: () => onToggle(section.id),
        accessory: foldAccessory(sectionView, t),
      }, rows, panel)
    }
    return h('section', { key: section.id, className: 'fmg-section', 'aria-label': title },
      h('div', { className: 'fmg-section__head' },
        h('h4', { className: 'fmg-section__title' }, title),
        help ? h('p', { className: 'fmg-note' }, help) : null),
      rows.length ? h(Group, null, rows) : null,
      panel)
  })
  return [
    props.readOnly ? h(Notice, { key: 'read-only', role: 'status' }, t('readOnly')) : null,
    tab.note ? h('p', { key: 'note', className: 'fmg-note' }, tab.note[lang] || tab.note.en) : null,
    tab.collapsible ? h('div', { key: 'folds', className: 'fmg-section' }, sections) : sections,
  ]
}

module.exports = { SettingsTab }
