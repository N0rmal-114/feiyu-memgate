'use strict'
// The two tabs that hold no settings: the overview (what the plugin reports about itself) and the
// changelog (what each update changed).

const { h, useState } = require('../react.cjs')
const { ID_PREFIX } = require('../constants.cjs')
const { releases } = require('../model/changelog.cjs')
const { statusRows } = require('../model/status.cjs')
const { Button, Tag } = require('../kit/controls.cjs')
const { Facts, Group, Reveal } = require('../kit/layout.cjs')
const { Icon } = require('../kit/icons.cjs')
const { renderMarkdown } = require('./markdown.cjs')

/**
 * The overview: the details behind the summary at the top of the page.
 * @param {{ t: Function, status: { data: object | null, loading: boolean, error: string | null, refresh: Function } }} props
 */
function OverviewTab(props) {
  const { t, status } = props
  const title = t('detailsTitle')
  let body
  if (status.data) {
    body = [
      // A failed read keeps the last answer on screen: say that it is not fresh.
      status.error ? h('p', { key: 'stale', className: 'fmg-note', role: 'status' }, t('statusFailed', { code: status.error })) : null,
      h(Group, { key: 'rows' }, h(Facts, { rows: statusRows(status.data, t) })),
    ]
  } else {
    body = h('p', { className: 'fmg-note', role: 'status' }, status.loading ? t('statusLoading') : t('statusFailed', { code: status.error }))
  }
  return h('section', { className: 'fmg-section', 'aria-label': title },
    h('div', { className: 'fmg-section__head' },
      h('div', { className: 'fmg-actions' },
        h('h4', { className: 'fmg-section__title' }, title),
        h(Button, { variant: 'text', onClick: status.refresh }, h(Icon, { name: 'refresh', size: 13 }), t('refresh'))),
      h('p', { className: 'fmg-note' }, t('detailsHint'))),
    body)
}

/**
 * The changelog: one release per row, the newest open.
 * @param {{ t: Function, state: { loading: boolean, data: object | null } }} props
 */
function ChangelogTab(props) {
  const { t, state } = props
  const list = releases(state.data)
  // Which releases the person opened or closed; a release not in the map is open when it is the newest.
  const [toggled, setToggled] = useState({})
  if (!list.length) {
    return h('p', { className: 'fmg-note', role: 'status' }, state.loading ? t('changelogLoading') : t('changelogFailed'))
  }
  return h('section', { className: 'fmg-section', 'aria-label': t('changelogTitle') },
    h('div', { className: 'fmg-section__head' },
      h('p', { className: 'fmg-note' }, t('changelogIntro'), t('lang') === 'zh' ? null : ' ' + t('changelogChinese'))),
    h(Group, null, list.map((release, index) => {
      const open = toggled[release.id] !== undefined ? toggled[release.id] : index === 0
      const bodyId = ID_PREFIX + '-release-' + index
      return h('div', { key: release.id, className: 'fmg-release' },
        h('button', {
          type: 'button', className: 'fmg-release__head', 'aria-expanded': open ? 'true' : 'false', 'aria-controls': bodyId,
          onClick: () => setToggled((previous) => Object.assign({}, previous, { [release.id]: !open })),
        },
        h('span', { className: 'fmg-release__version' }, release.name),
        release.date ? h('span', { className: 'fmg-release__date' }, release.date) : null,
        release.installed ? h(Tag, { tone: 'sea' }, t('changelogInstalled')) : null,
        h(Icon, { name: 'chevron', className: 'fmg-fold__chevron' })),
        h(Reveal, { open, id: bodyId }, h('div', { className: 'fmg-md' }, renderMarkdown(release.body))))
    })))
}

module.exports = { OverviewTab, ChangelogTab }
