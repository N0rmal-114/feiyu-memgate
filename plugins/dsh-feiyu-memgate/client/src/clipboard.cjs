'use strict'
// Puts a text on the clipboard: the one place of the page that touches it. The page copies only on a
// click, and only the report the person has in front of them (view/feedback.cjs).
//
// The browser's clipboard interface needs a secure context (https or localhost), which a DSH page
// is. Where it is missing or refuses, the older way is tried: a text area outside the visible page,
// selected and copied. Whether the text arrived is reported, never assumed.

// The older way. Returns whether the browser said it copied.
function copyThroughSelection(text, doc) {
  if (!doc || !doc.body || typeof doc.createElement !== 'function' || typeof doc.execCommand !== 'function') return false
  const area = doc.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.setAttribute('aria-hidden', 'true')
  area.style.position = 'fixed'
  area.style.top = '-1000px'
  area.style.opacity = '0'
  doc.body.appendChild(area)
  let copied = false
  try {
    area.select()
    copied = doc.execCommand('copy') === true
  } catch {
    copied = false
  }
  doc.body.removeChild(area)
  return copied
}

/**
 * @param {string} text
 * @param {{ navigator?: object, document?: object }} [env] the browser's objects; tests pass their own
 * @returns {Promise<boolean>} whether the text is on the clipboard now
 */
function copyText(text, env) {
  const given = env || {}
  const nav = 'navigator' in given ? given.navigator : typeof navigator !== 'undefined' ? navigator : undefined
  const doc = 'document' in given ? given.document : typeof document !== 'undefined' ? document : undefined
  if (nav && nav.clipboard && typeof nav.clipboard.writeText === 'function') {
    let attempt
    try {
      attempt = nav.clipboard.writeText(text)
    } catch {
      attempt = Promise.reject(new Error('refused'))
    }
    return Promise.resolve(attempt).then(() => true, () => copyThroughSelection(text, doc))
  }
  return Promise.resolve(copyThroughSelection(text, doc))
}

module.exports = { copyText }
