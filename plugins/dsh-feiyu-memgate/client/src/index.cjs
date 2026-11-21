'use strict'

// Entry point: the module the host loads. It registers the page's dictionaries and the settings page
// in the Plugins page's row slot, and exposes `internals` for tests. Wiring only; no page logic here.

const { NS, ROW_KEY, STATUS_ROUTE, MODELS_ROUTE, CHANGELOG_ROUTE } = require('./constants.cjs')
const { FIELDS } = require('./fields.cjs')
const { dictionaries } = require('./i18n.cjs')
const { fetchChangelog, fetchStatus, fetchModels, postModels } = require('./net.cjs')
const { fieldView, buildOps, saveDraft, checkText } = require('./model/form.cjs')
const { statusRows } = require('./model/status.cjs')
const { pickText, formatSize, memoryText, modelActions, chainLine, sameProblem } = require('./model/models.cjs')
const { inlineMarkdown, renderMarkdown, entryDate } = require('./model/markdown.cjs')
const { ChangelogView, SettingsPage, SettingsBody, ModelsSection, ModelRow } = require('./view/legacy.cjs')

const name = 'dsh-feiyu-memgate'
const inject = ['slots', 'locale']

function apply(ctx) {
  ctx.effect(() => ctx.locale.register(NS, dictionaries()), 'dsh-feiyu-memgate: dictionaries')
  const t = ctx.locale.bind(NS)
  ctx.slots.inject('plugins.row.config', () => ctx.slots.register({
    name: 'plugins.row.config',
    key: ROW_KEY,
    locale: NS,
    inject: () => ({ translate: t }),
  }, SettingsPage))
}

module.exports = {
  name,
  inject,
  apply,
  // For tests: the page's pure parts and components.
  internals: {
    FIELDS, NS, ROW_KEY, STATUS_ROUTE, MODELS_ROUTE, CHANGELOG_ROUTE, dictionaries, fetchChangelog, inlineMarkdown, renderMarkdown, entryDate, ChangelogView, fieldView, buildOps, saveDraft, statusRows, checkText,
    fetchStatus, fetchModels, postModels, pickText, formatSize, memoryText, modelActions, chainLine, sameProblem, SettingsPage, SettingsBody, ModelsSection,
    ModelRowForTests: ModelRow,
  },
}
