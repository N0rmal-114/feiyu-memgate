'use strict'
// One setting as one row: its name, what it does, and the control that fits its kind.
//
//   boolean  a switch at the right end of the row
//   integer  a stepper with its unit at the right end
//   choice   the options with their meanings, under the text
//   string   a text line under the text; a secret is write-only and shows only whether one is stored
//
// The row follows the host's settings conventions: an edit is staged, not written; a setting that
// differs from its inherited value carries a tag and a "Reset" that stages the return; a draft the
// field cannot accept shows why instead of the help text being lost (both are shown).

const { h } = require('../react.cjs')
const { ID_PREFIX } = require('../constants.cjs')
const { splitOption } = require('../model/page.cjs')
const { cx } = require('../theme/styles.cjs')
const { Button, Tag, Switch, TextInput, Stepper, Choices } = require('../kit/controls.cjs')

/** The element id of a setting's control: 'advanced.recallLimit' → 'feiyu-memgate-advanced-recallLimit'. */
function fieldId(k) {
  return ID_PREFIX + '-' + k.replace('.', '-')
}

/** Why a draft is refused, in words. */
function invalidText(t, field) {
  return field.kind === 'integer' ? t('invalidInteger', { min: field.min, max: field.max }) : t('invalidUrl')
}

/**
 * @param {{ t: Function, lang: 'en' | 'zh', row: { view: object, parent: string | null }, disabled: boolean,
 *   edit: (k: string, value: unknown) => void, reset: (k: string) => void }} props
 *   `row` comes from model/page.cjs sectionView(); `edit` stages a value, `reset` stages a return to
 *   the inherited value (for a secret: clearing it).
 */
function FieldRow(props) {
  const { t, lang, row, disabled, edit, reset } = props
  const view = row.view
  const field = view.field
  const k = view.k
  const id = fieldId(k)
  const labelId = id + '-label'
  const helpId = id + '-help'
  const label = t('f.' + k)

  let control
  let stacked = false
  let labelled = true // whether the label element can point at one control with htmlFor
  let help = t('h.' + k)
  const tags = []
  if (field.experimental) tags.push(h(Tag, { key: 'experimental', tone: 'blush' }, t('experimental')))

  if (view.secret) {
    stacked = true
    if (view.configured !== null) tags.push(h(Tag, { key: 'stored', tone: view.configured ? 'ok' : undefined }, t(view.configured ? 'secretSet' : 'secretUnset')))
    help = view.clearing ? t('secretClearing') : help + ' ' + t('secretHint')
    control = h('div', { className: 'fmg-input-line' },
      h(TextInput, { id, type: 'password', autoComplete: 'new-password', value: view.text, disabled, describedBy: helpId, onChange: (text) => edit(k, text) }),
      view.configured || view.text ? h(Button, { variant: 'text', disabled: disabled || view.clearing, onClick: () => reset(k) }, t('secretClear')) : null)
  } else if (field.kind === 'boolean') {
    control = h(Switch, { id, checked: view.value === true, label, describedBy: helpId, disabled, onChange: (next) => edit(k, next) })
  } else if (field.kind === 'integer') {
    control = [
      h(Stepper, {
        key: 'stepper', id, text: view.text, min: field.min, max: field.max, invalid: view.invalid, disabled, describedBy: helpId,
        labels: { down: t('stepDown'), up: t('stepUp') }, onChange: (text) => edit(k, text),
      }),
      field.unit ? h('span', { key: 'unit', className: 'fmg-quiet' }, field.unit[lang] || field.unit.en) : null,
    ]
  } else if (field.kind === 'choice') {
    stacked = true
    labelled = false
    control = h(Choices, {
      labelledBy: labelId, describedBy: helpId, value: view.value, disabled,
      options: field.options.map((option) => Object.assign({ value: option.value }, splitOption(t('o.' + k + '.' + option.value)))),
      onChange: (value) => edit(k, value),
    })
  } else {
    stacked = true
    control = h('div', { className: 'fmg-input-line' },
      h(TextInput, { id, value: view.text, invalid: view.invalid, disabled, describedBy: helpId, onChange: (text) => edit(k, text) }))
  }

  if (view.overridden) {
    tags.push(h(Tag, { key: 'changed', tone: 'sea' }, t('overridden')))
    tags.push(h(Button, { key: 'reset', variant: 'text', disabled, onClick: () => reset(k) }, t('reset')))
  }

  return h('div', { className: cx('fmg-row', stacked && 'fmg-row--stack', row.parent && 'fmg-row--sub') },
    h('div', { className: 'fmg-row__text' },
      h('div', { className: 'fmg-row__label' },
        labelled ? h('label', { id: labelId, htmlFor: id }, label) : h('span', { id: labelId }, label),
        tags),
      h('p', { className: 'fmg-row__help', id: helpId }, help),
      view.invalid ? h('p', { className: 'fmg-row__error', role: 'alert' }, invalidText(t, field)) : null),
    h('div', { className: 'fmg-row__control' }, control))
}

module.exports = { fieldId, invalidText, FieldRow }
