// Made-up answers of the plugin's routes for the page preview (scripts/preview-client.mjs).
// Each scenario is one state a person can meet; none of the values comes from a real installation.

const PYTHON = { found: true, version: '3.12.4', source: 'python3', executable: '/usr/bin/python3' }

const base = (overrides = {}) => ({
  version: '0.0.0-preview',
  language: 'zh',
  gateway: { mode: 'managed', state: 'running' },
  secrets: { 'external.token': false, 'retrieval.apiKey': false },
  python: PYTHON,
  retrieval: { setting: 'auto', active: 'keyword', hybrid: { setting: false, active: false } },
  store: { tier: 'keyword', type: 'sqlite', file: 'memories.db', count: 37 },
  embed: null,
  migration: null,
  relevance: { enabled: false, calls: 0, kept: 0, rejected: 0, skipped: {} },
  ...overrides,
})

const LOCAL = {
  retrieval: { setting: 'auto', active: 'local', hybrid: { setting: true, active: true } },
  store: { tier: 'local', type: 'embedding', file: 'memories-3f9a1c.json', keywordFile: 'memories-3f9a1c-keywords.db', count: 1284 },
  embed: { state: 'ready', error: null, model: 'bge-m3', dimensions: 1024, parity: 'exact' },
}

/** name → { label, status } where status is the answer of the status route, or a number for an HTTP error. */
export const SCENARIOS = {
  'ready-local': { label: 'Working, local model with hybrid', status: base({ ...LOCAL, migration: { state: 'done', copied: 1284, total: 1284, refused: 0 }, relevance: { enabled: true, calls: 212, kept: 318, rejected: 590, skipped: { timeout: 3 } } }) },
  'ready-keyword': { label: 'Working, fresh install (keyword)', status: base() },
  starting: { label: 'Gateway starting', status: base({ gateway: { mode: 'managed', state: 'starting' }, store: { tier: 'keyword', type: 'sqlite', file: 'memories.db', count: null } }) },
  copying: { label: 'Copying memories into the vector store', status: base({ ...LOCAL, migration: { state: 'running', copied: 420, total: 1284, refused: 2 } }) },
  asleep: { label: 'Nothing switched on', status: base({ gateway: { mode: 'managed', state: 'unused' } }) },
  'no-python': { label: 'No Python found', status: base({ gateway: { mode: 'managed', state: 'idle' }, python: { found: false }, store: null }) },
  failed: { label: 'Gateway stopped after failures', status: base({ gateway: { mode: 'managed', state: 'failed' } }) },
  external: { label: 'External gateway', status: base({ gateway: { mode: 'external', state: 'configured', url: 'http://127.0.0.1:8080' }, python: null, store: null, retrieval: { setting: 'auto', active: null, hybrid: { setting: false, active: false } }, secrets: { 'external.token': true, 'retrieval.apiKey': false } }) },
  offline: { label: 'Status route does not answer', status: 503 },
}

const MIT = { name: 'MIT', url: 'https://opensource.org/license/mit' }
const model = (overrides) => ({
  tier: 'lightest', tierLabel: { en: 'lightest tier', zh: '最轻档' }, sizeBytes: 47886240, memoryMB: { loaded: 188, peak: 219 },
  languages: ['zh'], license: MIT, requiresAcceptance: null, status: 'verified', state: 'absent', percent: null,
  note: { en: 'Small and quick; Chinese only.', zh: '小而快，只支持中文。' }, ...overrides,
})

/** The model list that goes with a scenario (the answer of the models route). */
export function modelsFor(name) {
  const local = name === 'ready-local' || name === 'copying'
  const downloading = name === 'starting'
  const licence = { name: 'Example Model Licence', url: 'https://example.invalid/licence' }
  return {
    version: 1,
    addon: { package: 'dsh-feiyu-memgate-embed', state: local || downloading ? 'ready' : 'absent' },
    installer: { available: true },
    python: { found: name !== 'no-python', problem: name === 'no-python'
      ? { code: 'no-python', title: { en: 'No Python 3.11 or newer was found.', zh: '没有找到 Python 3.11 或更新的版本。' }, todo: { en: 'Install Python and press Refresh.', zh: '安装 Python 后点“刷新”。' } }
      : null },
    link: 'https://github.com/N0rmal-114/feiyu-memgate#部署档位',
    busy: downloading,
    chain: downloading
      ? { target: 'bge-m3', phase: 'model', percent: 42, installState: null, error: null }
      : { target: null, phase: 'idle', percent: null, installState: null, error: null },
    models: [
      model({ id: 'bge-small-zh-v1.5', state: local ? 'installed' : 'absent' }),
      model({
        id: 'bge-m3', tier: 'balanced', tierLabel: { en: 'balanced tier', zh: '均衡档' }, sizeBytes: 437778496, memoryMB: { loaded: 612, peak: 700 },
        languages: ['multilingual'], note: { en: 'Many languages; the model the measurements were made with.', zh: '多语言；实测数据用的就是它。' },
        state: local ? 'active' : downloading ? 'downloading' : 'absent', percent: downloading ? 42 : null,
      }),
      model({
        id: 'licensed-model-example', tier: 'top', tierLabel: { en: 'top tier', zh: '最高档' }, sizeBytes: 612429792, memoryMB: null,
        languages: ['multilingual'], license: licence, requiresAcceptance: licence, status: 'unverified',
        note: { en: 'Shown to preview a model whose licence must be accepted first.', zh: '用来预览“需要先同意许可”的模型。' },
      }),
    ],
  }
}

/** Settings the preview's form starts with, so that "changed" marks and open sub-rows can be seen. */
export const USER_SETTINGS = {
  advanced: { recallLimit: 7, afterthought: true },
  retrieval: { hybrid: true },
}
