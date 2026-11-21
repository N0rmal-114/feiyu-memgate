'use strict'
// The top of the page: how memory is doing, said three ways at once. A headline with a coloured dot,
// one line of explanation, and the mascot in the mood that fits (model/overview.cjs decides which).
//
// The drawing is the one decorative thing on the page and is treated as such: it is loaded from the
// plugin's own route, fades in when it has arrived, and when it cannot be loaded (an older host, a
// blocked request) the card simply has no drawing. Nothing a person needs is only in the picture.

const { h, useState } = require('../react.cjs')
const { ART_ROUTE } = require('../constants.cjs')
const { cx } = require('../theme/styles.cjs')

/** The address of the drawing for a mood ('ready' | 'busy' | 'asleep' | 'trouble'). */
function artUrl(mood) {
  return ART_ROUTE + 'mascot-' + mood + '.webp'
}

/**
 * @param {{ t: Function, summary: { mood: string, headline: object, line: object, facts: object[] } }} props
 *   `summary` is model/overview.cjs summarise().
 */
function Hero(props) {
  const { t, summary } = props
  const mood = summary.mood
  // Which drawings have arrived, and whether one failed (then none is tried again on this page).
  const [art, setArt] = useState({ loaded: {}, failed: false })
  const drawing = art.failed ? null : h('div', { className: 'fmg-hero__art' },
    h('img', {
      key: mood,
      src: artUrl(mood),
      alt: t('mascot.' + mood),
      draggable: false,
      'data-shown': art.loaded[mood] ? 'true' : 'false',
      onLoad: () => setArt((previous) => (previous.loaded[mood] ? previous : { loaded: Object.assign({}, previous.loaded, { [mood]: true }), failed: false })),
      onError: () => setArt({ loaded: {}, failed: true }),
    }))
  return h('header', { className: 'fmg-hero' },
    h('div', { className: 'fmg-hero__sea', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
    // role="status": a change of state is read out; the poll re-renders the same text without a change.
    h('div', { className: 'fmg-hero__text', role: 'status' },
      h('h4', { className: 'fmg-hero__head' },
        h('span', { className: 'fmg-dot', 'data-mood': mood, 'aria-hidden': 'true' }),
        t(summary.headline.key, summary.headline.params)),
      h('p', { className: 'fmg-hero__line' }, t(summary.line.key, summary.line.params)),
      summary.facts.length
        ? h('ul', { className: 'fmg-hero__facts' }, summary.facts.map((fact) => h('li', {
          key: fact.id,
          className: cx('fmg-fact', fact.tone === 'warn' && 'fmg-fact--warn'),
        }, t(fact.key, fact.params))))
        : null),
    drawing)
}

module.exports = { artUrl, Hero }
