'use strict'
// The controls of the page: button, tag, switch, text input, text area, number stepper, list of choices.
//
// They are the page's own (not the host's primitives) so that the page looks the same on every host
// version it supports and can be drawn outside a host (scripts/preview-client.mjs). Their sizes
// follow the host's: 13px text, a 36 by 20 switch, 30 to 34px high controls.
//
// Each control is a plain function of its props: it keeps no state, the form does. All of them use
// native elements and ARIA roles, so the keyboard and a screen reader get them without extra code:
// a switch is a button with role="switch", a list of choices is a radio group with arrow keys.

const { h } = require('../react.cjs')
const { cx } = require('../theme/styles.cjs')
const { Icon } = require('./icons.cjs')

/**
 * @param {{ variant?: 'primary' | 'text', disabled?: boolean, onClick?: () => void, label?: string,
 *   children?: any }} props `label` is the accessible name of a button that shows only an icon.
 */
function Button(props) {
  return h('button', {
    type: 'button',
    className: cx('fmg-button', props.variant && 'fmg-button--' + props.variant),
    disabled: props.disabled || undefined,
    'aria-label': props.label,
    onClick: props.onClick,
  }, props.children)
}

/** A small label. tone: undefined (neutral) | 'sea' | 'blush' | 'warn' | 'ok'. */
function Tag(props) {
  return h('span', { className: cx('fmg-tag', props.tone && 'fmg-tag--' + props.tone) }, props.children)
}

/**
 * An on/off switch.
 * @param {{ id: string, checked: boolean, label: string, describedBy?: string, disabled?: boolean,
 *   onChange: (next: boolean) => void }} props `label` names it for a screen reader; the visible
 *   label of the row points at it with htmlFor, so a click on the text flips it too.
 */
function Switch(props) {
  return h('button', {
    type: 'button',
    id: props.id,
    className: 'fmg-switch',
    role: 'switch',
    'aria-checked': props.checked ? 'true' : 'false',
    'aria-label': props.label,
    'aria-describedby': props.describedBy,
    disabled: props.disabled || undefined,
    onClick: () => props.onChange(!props.checked),
  })
}

/**
 * A line of text. `onChange` gets the text, not the event. `label` names it for a screen reader when
 * no visible label points at it.
 * @param {{ id: string, value: string, type?: string, invalid?: boolean, disabled?: boolean, maxLength?: number,
 *   placeholder?: string, label?: string, describedBy?: string, autoComplete?: string, onChange: (text: string) => void }} props
 */
function TextInput(props) {
  return h('input', {
    id: props.id,
    className: 'fmg-input',
    type: props.type || 'text',
    value: props.value,
    maxLength: props.maxLength,
    placeholder: props.placeholder,
    autoComplete: props.autoComplete || 'off',
    spellCheck: false,
    disabled: props.disabled || undefined,
    'aria-label': props.label,
    'aria-invalid': props.invalid ? 'true' : undefined,
    'aria-describedby': props.describedBy,
    onChange: (event) => props.onChange(event.target.value),
  })
}

/**
 * Several lines of text; the person can drag it taller. `onChange` gets the text, not the event.
 * `label` names it for a screen reader when no visible label points at it.
 * @param {{ id: string, value: string, rows?: number, maxLength?: number, placeholder?: string,
 *   label?: string, describedBy?: string, disabled?: boolean, onChange: (text: string) => void }} props
 */
function TextArea(props) {
  return h('textarea', {
    id: props.id,
    className: 'fmg-input fmg-input--area',
    value: props.value,
    rows: props.rows || 6,
    maxLength: props.maxLength,
    placeholder: props.placeholder,
    autoComplete: 'off',
    disabled: props.disabled || undefined,
    'aria-label': props.label,
    'aria-describedby': props.describedBy,
    onChange: (event) => props.onChange(event.target.value),
  })
}

/**
 * The text a step leads to: one more or one less than the number on screen, kept inside the range.
 * A text that is no number steps from the nearer end of the range, so the buttons always lead out
 * of an invalid entry.
 */
function stepText(text, delta, min, max) {
  const raw = String(text).trim()
  const current = /^-?\d{1,9}$/.test(raw) ? Number(raw) : (delta > 0 ? min - 1 : max + 1)
  return String(Math.min(max, Math.max(min, current + delta)))
}

/**
 * A whole number with a minus and a plus. The text is kept as typed (the form decides whether it is
 * valid), so a half-typed number is never rewritten under the cursor.
 * @param {{ id: string, text: string, min: number, max: number, invalid?: boolean, disabled?: boolean,
 *   describedBy?: string, labels: { down: string, up: string }, onChange: (text: string) => void }} props
 */
function Stepper(props) {
  const { text, min, max, disabled } = props
  const number = /^-?\d{1,9}$/.test(String(text).trim()) ? Number(text) : null
  const step = (delta) => () => props.onChange(stepText(text, delta, min, max))
  return h('span', { className: 'fmg-stepper', 'data-invalid': props.invalid ? 'true' : undefined },
    h('button', { type: 'button', 'aria-label': props.labels.down, disabled: disabled || (number !== null && number <= min) || undefined, onClick: step(-1) },
      h(Icon, { name: 'minus', size: 14 })),
    h('input', {
      id: props.id,
      type: 'text',
      inputMode: 'numeric',
      value: text,
      autoComplete: 'off',
      disabled: disabled || undefined,
      'aria-invalid': props.invalid ? 'true' : undefined,
      'aria-describedby': props.describedBy,
      onChange: (event) => props.onChange(event.target.value),
    }),
    h('button', { type: 'button', 'aria-label': props.labels.up, disabled: disabled || (number !== null && number >= max) || undefined, onClick: step(1) },
      h(Icon, { name: 'plus', size: 14 })))
}

// Arrow keys move the choice, as in a native radio group: the neighbour is chosen and focused.
function onChoiceKey(event, props) {
  const forward = event.key === 'ArrowRight' || event.key === 'ArrowDown'
  const backward = event.key === 'ArrowLeft' || event.key === 'ArrowUp'
  if (!forward && !backward) return
  event.preventDefault()
  const values = props.options.map((option) => option.value)
  const at = Math.max(0, values.indexOf(props.value))
  const next = (at + (forward ? 1 : values.length - 1)) % values.length
  props.onChange(values[next])
  const buttons = event.currentTarget.querySelectorAll('[role="radio"]')
  if (buttons[next]) buttons[next].focus()
}

/**
 * One of a few options, each shown with what it means. A radio group: Tab reaches the chosen option,
 * the arrow keys change it.
 * @param {{ labelledBy: string, describedBy?: string, value: string, disabled?: boolean,
 *   options: { value: string, name: string, note: string }[], onChange: (value: string) => void }} props
 */
function Choices(props) {
  const known = props.options.some((option) => option.value === props.value)
  return h('div', {
    className: 'fmg-choices',
    // The stylesheet lays four options out two by two, so that none is left alone on a line.
    'data-count': String(props.options.length),
    role: 'radiogroup',
    'aria-labelledby': props.labelledBy,
    'aria-describedby': props.describedBy,
    onKeyDown: props.disabled ? undefined : (event) => onChoiceKey(event, props),
  }, props.options.map((option, index) => {
    const checked = option.value === props.value
    return h('button', {
      key: option.value,
      type: 'button',
      className: 'fmg-choice',
      role: 'radio',
      'aria-checked': checked ? 'true' : 'false',
      // Roving focus: the chosen option takes Tab; with no known choice the first one does.
      tabIndex: checked || (!known && index === 0) ? 0 : -1,
      disabled: props.disabled || undefined,
      onClick: () => props.onChange(option.value),
    },
    h('span', { className: 'fmg-choice__dot', 'aria-hidden': 'true' }),
    h('span', { className: 'fmg-choice__name' }, option.name,
      option.note ? h('span', { className: 'fmg-choice__note' }, option.note) : null))
  }))
}

module.exports = { Button, Tag, Switch, TextInput, TextArea, stepText, Stepper, Choices }
