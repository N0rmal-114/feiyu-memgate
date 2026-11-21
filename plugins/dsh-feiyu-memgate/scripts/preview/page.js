// The browser side of the page preview (scripts/preview-client.mjs): stands in for the host.
// It gives the page script what the host would: a module loader with React in it, a translate
// function from the page's own dictionaries, a settings form, and a fetch that reaches the preview
// server's made-up routes. Everything is driven by the address:
//   ?scenario=&theme=&lang=&form=&width=   what the toolbar sets
//   &tab=features|retrieval|advanced|changelog   open that tab after loading
//   &open=all                                    open every folding group of the tab
// The last two exist so that one address shows one state (a screenshot, a link in a review).
/* global React, ReactDOM */
(function () {
  'use strict'
  const query = new URLSearchParams(location.search)
  const option = (name, fallback) => query.get(name) || fallback
  const scenario = option('scenario', 'ready-local')
  const lang = option('lang', 'zh')
  const formMode = option('form', 'ready') // ready | readonly | loading | unavailable
  const width = option('width', '')

  if (option('theme', 'light') === 'dark') document.body.setAttribute('data-ds-dark-theme', '')

  // ---- the toolbar: every control rewrites the address and reloads
  const bar = document.querySelector('.preview-bar')
  const control = (name, label, values, current) => {
    const select = document.createElement('select')
    for (const [value, text] of values) {
      const item = document.createElement('option')
      item.value = value
      item.textContent = text
      item.selected = value === current
      select.appendChild(item)
    }
    select.addEventListener('change', () => {
      query.set(name, select.value)
      location.search = query.toString()
    })
    const wrap = document.createElement('label')
    wrap.append(label, select)
    bar.appendChild(wrap)
  }
  control('scenario', 'Scenario', window.PREVIEW_SCENARIOS, scenario)
  control('theme', 'Theme', [['light', 'light'], ['dark', 'dark']], option('theme', 'light'))
  control('lang', 'Language', [['zh', '中文'], ['en', 'English']], lang)
  control('form', 'Settings form', [['ready', 'ready'], ['readonly', 'read-only'], ['loading', 'loading'], ['unavailable', 'unavailable']], formMode)
  control('width', 'Column', [['', 'full'], ['640', '640px'], ['380', '380px']], width)
  if (width) document.querySelector('.host-column').style.maxWidth = width + 'px'

  // ---- the page script, loaded the way the host loads it
  const page = window.PREVIEW_RECORD.factory((id) => {
    if (id === 'react') return React
    throw new Error('not in the shared module table: ' + id)
  })
  const dictionary = page.internals.dictionaries()[lang]
  const t = (key, params) => {
    const template = dictionary[key]
    if (template === undefined) return key
    return template.replace(/\{(\w+)\}/g, (match, name) => (params && name in params ? String(params[name]) : match))
  }
  // What apply() does in a host, minus the slot registration: the stylesheet.
  page.internals.installStyles(document)

  // ---- a settings form that keeps its values in this page
  const form = {
    state: { status: formMode === 'readonly' ? 'ready' : formMode, value: {}, base: {}, user: {}, revision: 1, writable: formMode !== 'readonly' },
    async mutate(ops) {
      await new Promise((resolve) => setTimeout(resolve, 400))
      const next = JSON.parse(JSON.stringify(form.state))
      for (const op of ops) {
        const [group, key] = op.path
        for (const part of [next.value, next.user]) {
          if (op.op === 'set') (part[group] = part[group] || {})[key] = op.value
          else if (part[group]) delete part[group][key]
        }
      }
      next.revision += 1
      form.state = next
      render()
      return true
    },
  }
  form.state.value = JSON.parse(JSON.stringify(window.PREVIEW_USER_SETTINGS))
  form.state.user = JSON.parse(JSON.stringify(window.PREVIEW_USER_SETTINGS))

  const routed = (route, init) => fetch(route + '?scenario=' + encodeURIComponent(scenario), init)
  const root = ReactDOM.createRoot(document.getElementById('slot'))
  function render() {
    root.render(React.createElement(page.internals.SettingsPage, { view: 'page', translate: t, form: formMode === 'unavailable' ? undefined : form, fetch: routed }))
  }
  render()

  // ---- ?tab= and ?open=: click what a person would click, once the page is drawn
  const wanted = option('tab', '')
  if (wanted) {
    setTimeout(() => {
      const tab = document.getElementById('feiyu-memgate-tab-' + wanted)
      if (tab) tab.click()
      if (option('open', '') === 'all') {
        setTimeout(() => document.querySelectorAll('.fmg-fold__head[aria-expanded="false"]').forEach((head) => head.click()), 50)
      }
    }, 50)
  }
})()
