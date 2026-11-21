'use strict'
// Puts the page's stylesheet (theme/page.css) into the document, once, and takes it out again.
//
// The page cannot use inline styles alone: hover, keyboard focus, container widths and reduced
// motion only exist in a stylesheet. The host has no rule against a plugin adding a <style> element
// (it installs its own plugins' styles the same way), so that is what this does. The element is
// found again by its id, so loading the plugin twice never adds a second copy.

const CSS = require('./page.css')

const STYLE_ID = 'dsh-feiyu-memgate-styles'

/**
 * Add the stylesheet to a document.
 * @param {Document | undefined} doc the page's document; undefined outside a browser (tests)
 * @returns {() => void} removes the stylesheet again (called when the plugin is unloaded)
 */
function installStyles(doc) {
  if (!doc || !doc.head || typeof doc.createElement !== 'function') return () => {}
  let element = doc.getElementById(STYLE_ID)
  if (!element) {
    element = doc.createElement('style')
    element.id = STYLE_ID
    doc.head.appendChild(element)
  }
  // Written every time: after an update of the plugin the old element may still hold the old rules.
  element.textContent = CSS
  return () => {
    if (element.parentNode) element.parentNode.removeChild(element)
  }
}

/** Class names joined, skipping whatever is empty: cx('fmg-row', sub && 'fmg-row--sub'). */
function cx() {
  let out = ''
  for (let i = 0; i < arguments.length; i += 1) {
    const part = arguments[i]
    if (part) out += (out ? ' ' : '') + part
  }
  return out
}

module.exports = { STYLE_ID, installStyles, cx }
