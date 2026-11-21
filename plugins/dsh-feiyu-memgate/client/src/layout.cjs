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
        label: { en: 'Recall', zh: '回忆' },
        help: {
          en: 'What the model is given without asking: related memories each turn, and a short note on how to treat them.',
          zh: '不用模型开口就给它的东西：每一轮的相关记忆，以及怎么对待这些记忆的一小段说明。',
        },
        fields: ['modules.autoRecall', 'modules.promptSection'],
      },
      {
        id: 'tools',
        label: { en: 'Tools', zh: '工具' },
        help: {
          en: 'What the model and you can do on your own initiative.',
          zh: '模型和你可以主动做的事。',
        },
        fields: ['modules.rememberTool', 'modules.searchTool', 'modules.commands'],
      },
      {
        id: 'capture',
        label: { en: 'Capture', zh: '记录' },
        help: {
          en: 'Saving what you say without the model deciding.',
          zh: '不经过模型判断，直接把你说的话记下来。',
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
        label: { en: 'How memories are found', zh: '怎么找记忆' },
        help: {
          en: 'Each way keeps its own store file: memories saved under one are not searched under another unless they were copied, and switching back finds them again.',
          zh: '每种方式各用一个存储文件：在一种方式下记的内容，换成另一种方式后不会被查到，除非已经复制过去；切回来还能找到。',
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
    ],
  },
  {
    id: 'advanced',
    label: { en: 'Advanced', zh: '高级' },
    note: {
      en: 'The defaults suit most conversations. Each group opens on its own and says how many of its settings were changed.',
      zh: '默认值适合大多数对话。每一组可以单独展开，组名右边会标出里面改过几项。',
    },
    collapsible: true,
    sections: [
      {
        id: 'tuning',
        label: { en: 'Recall details', zh: '召回细节' },
        help: { en: 'How many memories a turn gets and how strict the match is.', zh: '每轮给几条记忆，以及匹配有多严格。' },
        fields: [
          'advanced.recallLimit', 'advanced.profile', 'advanced.maxNewPerTurn', 'advanced.recallTimeoutMs',
          'advanced.linkedRecall', 'advanced.skipRepeats',
        ],
      },
      {
        id: 'block',
        label: { en: 'Memory block', zh: '记忆块' },
        help: { en: 'How the recalled memories are presented to the model.', zh: '召回的记忆以什么样子交给模型。' },
        fields: ['advanced.language', 'advanced.blockTone', 'advanced.showAge', 'advanced.failureNote'],
      },
      {
        id: 'experiments',
        label: { en: 'Experiments', zh: '实验功能' },
        help: {
          en: 'Measured offline only, not yet in everyday chats. All off by default; each says what it costs.',
          zh: '只做过离线测量，还没有在日常聊天里验证。默认全部关闭，每一项都写了它的代价。',
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
          en: 'Where memories are kept: the gateway that ships with the plugin, or one you run yourself.',
          zh: '记忆放在哪里：插件自带的网关，或你自己运行的网关。',
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
