'use strict'
// Where each setting sits on the page: tabs, the sections of a tab, the settings of a section.
//
// The settings themselves (keys, kinds, defaults, texts, `needs`) come from src/config.js through
// fields.cjs. This table is only about presentation, which is why it lives here and not there: the
// stored keys group settings by what they configure (`modules`, `advanced`, ...), the page groups them
// by what a person is trying to do. A setting therefore may sit in a tab that does not match its key
// (advanced.captureMinChars sits under the capture switch it belongs to).
//
// Rules, checked by tests/client-modules.test.js:
//   - every setting of FIELDS is placed exactly once;
//   - a setting with `needs` sits in the same section as the setting it depends on, after it
//     (the page draws it as a sub-row that appears while the other setting has a fitting value).
//
// Three tabs hold no settings and are not listed here: the overview (status), the changelog and feedback.
// `panel` names a part of the page that is not a settings row and is drawn after the section's rows.

const TABS = [
  {
    id: 'features',
    label: { en: 'Features', zh: '功能' },
    sections: [
      {
        id: 'recall',
        label: { en: 'Recall', zh: '召回' },
        help: {
          en: 'What is provided without a model request: related memories each turn, and a note on how to use them.',
          zh: '无需模型请求即可提供的内容：每轮对话的相关记忆，以及记忆的使用说明。',
        },
        fields: ['modules.autoRecall', 'modules.promptSection'],
      },
      {
        id: 'tools',
        label: { en: 'Tools', zh: '工具' },
        help: {
          en: 'What the model and you can use on your own initiative.',
          zh: '模型和你可以主动使用的功能。',
        },
        fields: ['modules.rememberTool', 'modules.searchTool', 'modules.commands'],
      },
      {
        id: 'capture',
        label: { en: 'Capture', zh: '记录' },
        help: {
          en: 'Saving what you say without the model deciding.',
          zh: '不经模型判断，直接保存用户发送的消息。',
        },
        fields: ['modules.autoCapture', 'advanced.captureMinChars'],
      },
    ],
  },
  {
    id: 'retrieval',
    label: { en: 'Retrieval', zh: '检索' },
    sections: [
      {
        id: 'tier',
        label: { en: 'Retrieval method', zh: '检索方式' },
        help: {
          en: 'Each way keeps its own store file: memories saved under one are not searched under another unless they were copied, and switching back finds them again.',
          zh: '各检索方式分别使用独立的存储文件：在一种方式下记录的记忆，切换方式后不会被检索到，除非已复制过去；切换回来后仍可找到。',
        },
        fields: [
          'retrieval.tier', 'retrieval.url', 'retrieval.model', 'retrieval.apiKey',
          'retrieval.hybrid', 'modules.migrateOnTierChange',
        ],
      },
      {
        id: 'models',
        label: { en: 'Local embedding models', zh: '本地向量模型' },
        panel: 'models',
        fields: [],
      },
      {
        id: 'trial',
        label: { en: 'Try a recall', zh: '试一下召回' },
        panel: 'trial',
        fields: [],
      },
    ],
  },
  {
    id: 'advanced',
    label: { en: 'Advanced', zh: '高级' },
    note: {
      en: 'The defaults suit most conversations. Each group opens on its own and shows how many of its settings were changed.',
      zh: '默认值适用于大多数对话。各组可单独展开，组名右侧显示已修改的设置项数量。',
    },
    collapsible: true,
    sections: [
      {
        id: 'tuning',
        label: { en: 'Recall details', zh: '召回细节' },
        help: { en: 'How many memories a turn gets and how strict the match is.', zh: '每轮提供的记忆条数，以及匹配的严格程度。' },
        fields: [
          'advanced.recallLimit', 'advanced.profile', 'advanced.maxNewPerTurn', 'advanced.recallTimeoutMs',
          'advanced.linkedRecall', 'advanced.skipRepeats',
        ],
      },
      {
        id: 'block',
        label: { en: 'Memory block', zh: '记忆块' },
        help: { en: 'How the recalled memories are presented to the model.', zh: '召回的记忆交给模型时的呈现方式。' },
        fields: ['advanced.language', 'advanced.blockTone', 'advanced.showAge', 'advanced.failureNote'],
      },
      {
        id: 'experiments',
        label: { en: 'Experimental features', zh: '实验性功能' },
        help: {
          en: 'Measured offline only, not yet verified in everyday chats. All off by default; each states its cost.',
          zh: '仅做过离线测量，尚未在日常对话中验证。默认全部关闭，每项都注明了代价。',
        },
        fields: [
          'advanced.relevanceCheck', 'advanced.relevanceProvider', 'advanced.relevanceModel', 'advanced.relevanceTimeoutMs',
          'advanced.afterthought', 'advanced.afterthoughtMax',
          'advanced.conversationPool', 'advanced.poolSize',
          'advanced.recentPool', 'advanced.recentSize', 'advanced.recentDays',
        ],
      },
      {
        id: 'gateway',
        label: { en: 'Gateway', zh: '网关' },
        help: {
          en: 'Where memories are kept: the gateway bundled with the plugin, or an external one you run yourself.',
          zh: '记忆的存放位置：插件内置的网关，或你自己运行的外部网关。',
        },
        fields: [
          'gateway.mode', 'gateway.python', 'gateway.dataDir', 'external.url', 'external.token',
          'advanced.dailyWrites', 'advanced.redaction', 'advanced.newerFirst', 'advanced.forgetting',
        ],
      },
    ],
  },
]

/** Every placed setting key, in page order. */
function placedKeys() {
  const keys = []
  for (const tab of TABS) for (const section of tab.sections) for (const key of section.fields) keys.push(key)
  return keys
}

module.exports = { TABS, placedKeys }
