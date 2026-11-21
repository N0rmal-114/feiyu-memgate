'use strict'

// Entry point: the module the host loads. It registers the page's dictionaries, its stylesheet and
// the settings page in the Plugins page's row slot, and exposes `internals` for tests. Wiring only;
// no page logic here.

const { NS, ROW_KEY, STATUS_ROUTE, MODELS_ROUTE, CHANGELOG_ROUTE, ART_ROUTE } = require('./constants.cjs')
const { FIELDS, INDEX } = require('./fields.cjs')
const { dictionaries } = require('./i18n.cjs')
const { TABS, placedKeys } = require('./layout.cjs')
const { fetchChangelog, fetchStatus, fetchModels, postModels, sendFeedback } = require('./net.cjs')
const { copyText } = require('./clipboard.cjs')
const { entryDate, releases } = require('./model/changelog.cjs')
const feedback = require('./model/feedback.cjs')
const { fieldView, buildOps, saveDraft, checkText } = require('./model/form.cjs')
const { pickText, formatSize, memoryText, modelActions, chainLine, sameProblem } = require('./model/models.cjs')
const { formatCount, summarise } = require('./model/overview.cjs')
const { splitOption, isMet, sectionView, pageView, savable } = require('./model/page.cjs')
const { statusRows } = require('./model/status.cjs')
const { STYLE_ID, installStyles } = require('./theme/styles.cjs')
const { stepText } = require('./kit/controls.cjs')
const { emptyFeedback, FeedbackTab, Nag } = require('./view/feedback.cjs')
const { FieldRow, fieldId } = require('./view/field-row.cjs')
const { Hero, artUrl } = require('./view/hero.cjs')
const { inlineMarkdown, renderMarkdown } = require('./view/markdown.cjs')
const { ModelRow, ModelsPanel } = require('./view/models.cjs')
const { Footer, SaveBar, SettingsBody, SettingsPage } = require('./view/page.cjs')
const { SettingsTab } = require('./view/settings-tab.cjs')
const { ChangelogTab, OverviewTab } = require('./view/tabs.cjs')

const name = 'dsh-feiyu-memgate'
const inject = ['slots', 'locale']

function apply(ctx) {
  ctx.effect(() => ctx.locale.register(NS, dictionaries()), 'dsh-feiyu-memgate: dictionaries')
  // The stylesheet lives as long as the plugin is loaded: the effect's return value removes it.
  ctx.effect(() => installStyles(typeof document === 'undefined' ? undefined : document), 'dsh-feiyu-memgate: stylesheet')
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
  // For tests and the preview (scripts/preview-client.mjs): the page's pure parts and its components.
  internals: {
    // tables and names
    FIELDS, INDEX, TABS, NS, ROW_KEY, STATUS_ROUTE, MODELS_ROUTE, CHANGELOG_ROUTE, ART_ROUTE, STYLE_ID,
    dictionaries, placedKeys,
    // calls to the host half
    fetchStatus, fetchChangelog, fetchModels, postModels, sendFeedback,
    // logic
    fieldView, buildOps, saveDraft, checkText, splitOption, isMet, sectionView, pageView, savable,
    statusRows, summarise, formatCount, entryDate, releases,
    pickText, formatSize, memoryText, modelActions, chainLine, sameProblem, stepText, fieldId, artUrl,
    feedback, emptyFeedback, copyText,
    // view
    installStyles, inlineMarkdown, renderMarkdown,
    Hero, FieldRow, SettingsTab, OverviewTab, ChangelogTab, FeedbackTab, Nag, ModelRow, ModelsPanel, SaveBar, Footer, SettingsBody, SettingsPage,
  },
}
