'use strict'
// The structural parts of the page: tabs, a part that opens and closes, a folding group, the bordered
// list of rows, label and value pairs, a notice, a progress bar. Like the controls they keep no
// state; the page owns which tab is selected and which group is open.

const { h } = require('../react.cjs')
const { cx } = require('../theme/styles.cjs')
const { Icon } = require('./icons.cjs')

// The ids that tie a tab to its panel, both ways (aria-controls and aria-labelledby).
const tabId = (prefix, id) => prefix + '-tab-' + id
const panelId = (prefix, id) => prefix + '-panel-' + id

// Arrow keys, Home and End move between tabs and select at once, as the tabs pattern asks.
function onTabKey(event, props) {
  const ids = props.tabs.map((tab) => tab.id)
  const at = ids.indexOf(props.value)
  let next = -1
  if (event.key === 'ArrowRight') next = (at + 1) % ids.length
  else if (event.key === 'ArrowLeft') next = (at + ids.length - 1) % ids.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = ids.length - 1
  if (next < 0) return
  event.preventDefault()
  props.onChange(ids[next])
  const buttons = event.currentTarget.querySelectorAll('[role="tab"]')
  if (buttons[next]) buttons[next].focus()
}

/**
 * The row of tabs.
 * @param {{ idPrefix: string, label: string, value: string, onChange: (id: string) => void,
 *   tabs: { id: string, label: string, mark?: 'edited' | 'invalid' | null, markLabel?: string }[] }} props
 *   `mark` puts a dot after the label: the tab holds unsaved edits, or one that cannot be saved;
 *   `markLabel` says so to a screen reader.
 */
function Tabs(props) {
  return h('div', { className: 'fmg-tabs', role: 'tablist', 'aria-label': props.label, onKeyDown: (event) => onTabKey(event, props) },
    props.tabs.map((tab) => {
      const selected = tab.id === props.value
      return h('button', {
        key: tab.id,
        type: 'button',
        id: tabId(props.idPrefix, tab.id),
        className: 'fmg-tab',
        role: 'tab',
        'aria-selected': selected ? 'true' : 'false',
        // Only the selected tab's panel is in the document, so only that tab can point at one.
        'aria-controls': selected ? panelId(props.idPrefix, tab.id) : undefined,
        tabIndex: selected ? 0 : -1,
        onClick: () => props.onChange(tab.id),
      },
      tab.label,
      tab.mark ? h('span', { className: cx('fmg-mark', tab.mark === 'invalid' && 'fmg-mark--invalid'), role: 'img', 'aria-label': tab.markLabel }) : null)
    }))
}

/** The content of the selected tab. */
function TabPanel(props) {
  return h('div', {
    className: 'fmg-panel',
    role: 'tabpanel',
    id: panelId(props.idPrefix, props.id),
    'aria-labelledby': tabId(props.idPrefix, props.id),
  }, props.children)
}

/**
 * A part that opens and closes with a short height animation. The content stays in the document
 * while closed (that is what makes the animation possible); the stylesheet hides it then
 * (`visibility: hidden`), which also takes it out of the tab order and away from a screen reader.
 * `aria-hidden` says the same in the markup, for a reader of the tree and for the tests.
 */
function Reveal(props) {
  return h('div', { className: 'fmg-reveal', 'data-open': props.open ? 'true' : 'false', 'aria-hidden': props.open ? undefined : 'true' },
    h('div', { id: props.id }, props.children))
}

/**
 * A group with a header that folds it.
 * @param {{ id: string, title: string, help?: string, open: boolean, onToggle: () => void,
 *   accessory?: any, children?: any }} props `accessory` sits at the right end of the header
 *   (a tag saying how many settings inside are changed).
 */
function Fold(props) {
  return h('div', { className: 'fmg-fold' },
    h('button', { type: 'button', className: 'fmg-fold__head', 'aria-expanded': props.open ? 'true' : 'false', 'aria-controls': props.id, onClick: props.onToggle },
      h(Icon, { name: 'chevron', className: 'fmg-fold__chevron' }),
      h('span', null,
        h('span', { className: 'fmg-fold__title' }, props.title),
        props.help ? h('span', { className: 'fmg-fold__help' }, props.help) : null),
      props.accessory || null),
    h(Reveal, { open: props.open, id: props.id }, h('div', { className: 'fmg-fold__body fmg-rows' }, props.children)))
}

/** The bordered list the rows of a section sit in. */
function Group(props) {
  return h('div', { className: 'fmg-group fmg-rows' }, props.children)
}

/** Label and value pairs. rows: [label, value][] */
function Facts(props) {
  return h('dl', { className: 'fmg-facts' }, props.rows.flatMap((row, index) => [
    h('dt', { key: 'dt' + index }, row[0]),
    h('dd', { key: 'dd' + index }, row[1]),
  ]))
}

/**
 * A boxed message. tone 'warn' for something the person should act on; `role` is 'status' for news
 * that can wait and 'alert' for a failure.
 */
function Notice(props) {
  return h('div', { className: cx('fmg-notice', props.tone && 'fmg-notice--' + props.tone), role: props.role, 'aria-label': props.label }, props.children)
}

/** A thin bar for a known share of work done (0 to 100). */
function Progress(props) {
  const percent = Math.min(100, Math.max(0, Number(props.percent) || 0))
  return h('div', { className: 'fmg-progress', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': percent, 'aria-label': props.label },
    h('i', { style: { width: percent + '%' } }))
}

module.exports = { tabId, panelId, Tabs, TabPanel, Reveal, Fold, Group, Facts, Notice, Progress }
