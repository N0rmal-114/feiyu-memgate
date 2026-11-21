'use strict'

// The local embedding models list as plain data: texts, sizes, which buttons a row offers, the line
// on where the setup stands. Pure; the view decides how to draw it. Never touches React or the DOM.

/** A { en, zh } text from the host half in the page's language. */
function pickText(text, lang) {
  if (!text || typeof text !== 'object') return ''
  return text[lang] || text.en || ''
}

/** A download size as "1.2 GB" or "345.0 MB" (decimal units); "?" when the size is not a positive number. */
function formatSize(bytes) {
  if (typeof bytes !== 'number' || !(bytes > 0)) return '?'
  return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + ' GB' : (bytes / 1e6).toFixed(1) + ' MB'
}

// The add-on's measurement { loaded, peak } (MB) as "about N MB": the loaded figure, the memory the
// model process holds while it serves; "not measured" when there is none.
function memoryText(memoryMB, t) {
  const mb = memoryMB && typeof memoryMB === 'object' ? (memoryMB.loaded > 0 ? memoryMB.loaded : memoryMB.peak > 0 ? memoryMB.peak : null) : null
  return mb ? t('memoryAbout', { mb: String(Math.round(mb)) }) : t('memoryUnknown')
}

/**
 * What a model row offers: the primary button (label, action, whether it can be pressed) and a
 * secondary one (cancel while downloading, delete when downloaded and not in use).
 */
function modelActions(model, view, accepted) {
  const otherBusy = view.busy && view.chain.target !== model.id
  const needsTick = Boolean(model.requiresAcceptance) && model.state === 'absent' && !accepted
  switch (model.state) {
    case 'downloading':
      return {
        primary: { label: model.percent === null || model.percent === undefined ? ['actDownloadingPlain'] : ['actDownloading', { percent: model.percent }], action: null, enabled: false },
        secondary: { label: ['actCancel'], action: 'cancel' },
      }
    case 'installed':
      return { primary: { label: ['actUse'], action: 'use', enabled: !otherBusy }, secondary: { label: ['actRemove'], action: 'remove' } }
    case 'active':
      return { primary: { label: ['actActive'], action: null, enabled: false }, secondary: null }
    case 'loading':
      return { primary: { label: ['actLoading'], action: null, enabled: false }, secondary: null }
    case 'paused':
      return { primary: { label: ['actResume'], action: 'install', enabled: !otherBusy }, secondary: { label: ['actCancel'], action: 'cancel' } }
    default: {
      const mine = view.busy && view.chain.target === model.id
      return {
        primary: { label: ['actInstall'], action: 'install', enabled: !otherBusy && !mine && !needsTick },
        secondary: mine && view.chain.phase === 'addon' ? { label: ['actCancel'], action: 'cancel' } : null,
      }
    }
  }
}

/** The line on where the setup stands, or null. */
function chainLine(view, t) {
  const c = view.chain
  if (!c || !c.target) return null
  const model = c.target
  switch (c.phase) {
    case 'addon': {
      const s = c.installState && c.installState.phase ? ' · ' + t('installState.' + c.installState.phase) : ''
      return t('phase.addon') + s
    }
    case 'addon-starting':
      return t('phase.addon-starting')
    case 'paused':
      return t('phase.paused', { model })
    case 'model':
      return c.percent === null || c.percent === undefined ? t('phase.model', { model }) : t('phase.modelPercent', { model, percent: c.percent })
    case 'activating':
      return t('phase.activating', { model })
    case 'done':
      return t('phase.done', { model })
    default:
      return null
  }
}

// The answer to a click and the stored state of the chain often carry the same failure (a declined
// install script is both). Found in a real host: the message was shown twice. Show the stored one.
function sameProblem(a, b) {
  return Boolean(a && b && a.code && a.code === b.code)
}

module.exports = { pickText, formatSize, memoryText, modelActions, chainLine, sameProblem }
