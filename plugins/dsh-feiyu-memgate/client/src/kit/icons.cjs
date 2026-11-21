'use strict'
// The few line icons of the page, drawn here so that the page needs no icon font and no sprite of the
// host. All share one 16-unit grid and one stroke, and take their colour from the text around them.

const { h } = require('../react.cjs')

// One path each, on a 16 by 16 grid, stroke only.
const PATHS = {
  chevron: 'M6 3.5 10.5 8 6 12.5',
  check: 'M3.5 8.5 6.5 11.5 12.5 4.5',
  minus: 'M4 8h8',
  plus: 'M8 4v8M4 8h8',
  refresh: 'M13 8a5 5 0 1 1-1.5-3.6M13 3v2.5h-2.5',
  external: 'M9.5 3H13v3.5M13 3 7.5 8.5M11 9.5V13H3V5h3.5',
  alert: 'M8 6v3M8 11.2h.01M8 2.2 14.3 13H1.7L8 2.2Z',
}

/**
 * A decorative icon: hidden from assistive technology, so the control it sits in needs its own text
 * or label.
 * @param {{ name: keyof PATHS, size?: number, className?: string }} props
 */
function Icon(props) {
  const size = props.size || 16
  return h('svg', {
    className: props.className,
    width: size,
    height: size,
    viewBox: '0 0 16 16',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.5,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    'aria-hidden': 'true',
    focusable: 'false',
  }, h('path', { d: PATHS[props.name] || '' }))
}

module.exports = { ICON_NAMES: Object.keys(PATHS), Icon }
