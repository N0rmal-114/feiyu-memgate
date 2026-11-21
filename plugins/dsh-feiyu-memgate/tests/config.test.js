// Settings: defaults, validation, typed values, the schema handed to the host and the language choice.
import test from 'node:test'
import assert from 'node:assert/strict'
import { DEFAULTS, FIELDS, SETTING_KEYS, buildConfigSchema, parseSetting, readConfig, resolveLanguage } from '../src/config.js'
import { TIER_CHOICES } from '../src/tiers.js'
import { volatile } from './helpers/fake-host.js'

test('defaults', () => {
  assert.deepEqual(readConfig(undefined), DEFAULTS)
  assert.deepEqual(readConfig({}), DEFAULTS)
  assert.deepEqual(DEFAULTS, {
    modules: { autoRecall: true, rememberTool: true, searchTool: true, promptSection: true, autoCapture: false, commands: true, migrateOnTierChange: true },
    retrieval: { tier: 'auto', hybrid: false, url: '', model: '', apiKey: '' },
    gateway: { mode: 'managed', python: '', dataDir: '' },
    external: { url: '', token: '' },
    advanced: {
      recallLimit: 5, recallTimeoutMs: 4000, language: 'auto', skipRepeats: true, linkedRecall: true, showAge: true, failureNote: true, captureMinChars: 4, blockTone: 'normal', maxNewPerTurn: 0, relevanceCheck: false, relevanceProvider: '', relevanceModel: '', relevanceTimeoutMs: 3000,
      afterthought: false, afterthoughtMax: 2, conversationPool: false, poolSize: 3, recentPool: false, recentSize: 3, recentDays: 7,
      profile: 'broad', dailyWrites: 50, redaction: true, newerFirst: true, forgetting: false,
    },
  })
  assert.ok(Object.isFrozen(DEFAULTS.modules))
})

test('bad values fall back to the default, strings are trimmed', () => {
  const s = readConfig({
    modules: { autoRecall: 'yes', autoCapture: true, commands: 0 },
    retrieval: { tier: 'vector' },
    gateway: { mode: 'remote' },
    external: { url: '  http://127.0.0.1:8080  ', token: 42 },
    advanced: { recallLimit: 11, recallTimeoutMs: 4000.5, language: 'fr', profile: 'wide', dailyWrites: -1, forgetting: 1 },
  })
  assert.equal(s.modules.autoRecall, true)
  assert.equal(s.modules.autoCapture, true)
  assert.equal(s.modules.commands, true)
  assert.equal(s.retrieval.tier, 'auto')
  assert.equal(s.gateway.mode, 'managed')
  assert.equal(s.external.url, 'http://127.0.0.1:8080')
  assert.equal(s.external.token, '')
  assert.equal(s.advanced.recallLimit, 5)
  assert.equal(s.advanced.recallTimeoutMs, 4000)
  assert.equal(s.advanced.language, 'auto')
  assert.equal(s.advanced.profile, 'broad')
  assert.equal(s.advanced.dailyWrites, 50)
  assert.equal(s.advanced.forgetting, false)
  assert.equal(readConfig({ advanced: { dailyWrites: 0 } }).advanced.dailyWrites, 0, '0 means no cap')
})

test('volatile groups are read through get() every time', () => {
  const modules = volatile({ autoCapture: true })
  const config = { modules }
  assert.equal(readConfig(config).modules.autoCapture, true)
  modules.set({ autoCapture: false })
  assert.equal(readConfig(config).modules.autoCapture, false)
  // A plain object with a get() of its own is not mistaken for a reference.
  assert.equal(readConfig({ advanced: { get: () => ({ recallLimit: 9 }), recallLimit: 3 } }).advanced.recallLimit, 3)
})

test('typed text becomes a value only when the field accepts it', () => {
  const f = (key) => SETTING_KEYS[key]
  assert.deepEqual(parseSetting(f('modules.autoRecall'), ' OFF '), { ok: true, value: false })
  assert.deepEqual(parseSetting(f('modules.autoRecall'), '开'), { ok: true, value: true })
  assert.deepEqual(parseSetting(f('modules.autoRecall'), 'maybe'), { ok: false, reason: 'boolean' })
  assert.deepEqual(parseSetting(f('advanced.recallLimit'), '7'), { ok: true, value: 7 })
  for (const bad of ['0', '11', '7.5', '1e1', 'seven', '']) assert.equal(parseSetting(f('advanced.recallLimit'), bad).ok, false, bad)
  assert.deepEqual(parseSetting(f('advanced.dailyWrites'), '0'), { ok: true, value: 0 })
  assert.deepEqual(parseSetting(f('retrieval.tier'), 'local'), { ok: true, value: 'local' })
  assert.deepEqual(parseSetting(f('retrieval.tier'), 'Local'), { ok: false, reason: 'choice' })
  assert.deepEqual(parseSetting(f('retrieval.hybrid'), 'on'), { ok: true, value: true })
  assert.deepEqual(parseSetting(f('retrieval.hybrid'), '关'), { ok: true, value: false })
  assert.deepEqual(parseSetting(f('external.url'), 'http://127.0.0.1:8080'), { ok: true, value: 'http://127.0.0.1:8080' })
  assert.deepEqual(parseSetting(f('external.url'), 'ftp://x'), { ok: false, reason: 'url' })
  assert.deepEqual(parseSetting(f('gateway.dataDir'), '  D:/memory store  '), { ok: true, value: 'D:/memory store' })
  assert.deepEqual(parseSetting(f('gateway.python'), '   '), { ok: false, reason: 'empty' })
})

test('language: auto follows the locale, zh and en are taken as they are', () => {
  assert.equal(resolveLanguage('zh', 'en-US'), 'zh')
  assert.equal(resolveLanguage('en', 'zh-CN'), 'en')
  assert.equal(resolveLanguage('auto', 'zh-CN'), 'zh')
  assert.equal(resolveLanguage('auto', 'zh'), 'zh')
  assert.equal(resolveLanguage('auto', 'en-GB'), 'en')
  assert.equal(resolveLanguage('auto', 'ja-JP'), 'en')
  assert.ok(['zh', 'en'].includes(resolveLanguage('auto')))
})

// A recording stand-in for Schemastery: every builder returns a node whose chained calls are kept,
// enough to check what the host's Settings service will receive.
function recordingSchema() {
  const node = (type, extra = {}) => {
    const n = { type, meta: {}, ...extra }
    for (const key of ['default', 'description', 'role', 'min', 'max', 'step']) {
      n[key] = (value) => {
        n.meta[key] = value
        return n
      }
    }
    n.i18n = (messages) => {
      n.meta.i18n = messages
      return n
    }
    n.volatile = () => {
      n.meta.volatile = true
      return n
    }
    return n
  }
  return {
    object: (dict) => node('object', { dict }),
    boolean: () => node('boolean'),
    number: () => node('number'),
    string: () => node('string'),
    union: (list) => node('union', { list }),
    const: (value) => node('const', { value }),
  }
}

test('the schema: volatile groups in form order, every field described in en and zh, secrets masked', () => {
  const schema = buildConfigSchema(recordingSchema())
  assert.deepEqual(Object.keys(schema.dict), ['modules', 'retrieval', 'gateway', 'external', 'advanced'])
  for (const [groupKey, group] of Object.entries(schema.dict)) {
    assert.equal(group.meta.volatile, true, `${groupKey} must be volatile to be editable`)
    assert.ok(group.meta.i18n.en.$description && group.meta.i18n.zh.$description, groupKey)
    assert.deepEqual(Object.keys(group.dict), Object.keys(DEFAULTS[groupKey]), `${groupKey} field order`)
    for (const [key, field] of Object.entries(group.dict)) {
      assert.equal(field.meta.volatile, undefined, 'no volatile field inside a volatile group')
      assert.deepEqual(field.meta.default, DEFAULTS[groupKey][key], `${groupKey}.${key} default`)
      assert.ok(field.meta.i18n.en.$description && field.meta.i18n.zh.$description, `${groupKey}.${key} help text`)
      assert.equal(field.meta.description, field.meta.i18n.en.$description)
    }
  }
  assert.equal(schema.dict.external.dict.token.meta.role, 'secret')
  assert.equal(schema.dict.retrieval.dict.apiKey.meta.role, 'secret')
  const secrets = Object.entries(SETTING_KEYS).filter(([, f]) => f.secret).map(([k]) => k)
  assert.deepEqual(secrets, ['retrieval.apiKey', 'external.token'])
  assert.deepEqual(schema.dict.retrieval.dict.tier.list.map((n) => n.value), [...TIER_CHOICES])
  assert.deepEqual(TIER_CHOICES, ['auto', 'keyword', 'endpoint', 'local'])
  assert.deepEqual(schema.dict.gateway.dict.mode.list.map((n) => n.value), ['managed', 'external'])
  assert.deepEqual([schema.dict.advanced.dict.recallLimit.meta.min, schema.dict.advanced.dict.recallLimit.meta.max], [1, 10])
  assert.match(schema.dict.retrieval.meta.description, /own store file/)
})

test('the field table lists every setting once, labelled and explained in both languages', () => {
  const names = FIELDS.flatMap((g) => g.fields.map((f) => `${g.group}.${f.key}`))
  assert.equal(new Set(names).size, names.length)
  assert.deepEqual(Object.keys(SETTING_KEYS), names)
  for (const group of FIELDS) {
    assert.ok(group.label.en && group.label.zh && group.help.en && group.help.zh, group.group)
    for (const field of group.fields) {
      assert.ok(field.label.en && field.label.zh && field.help.en && field.help.zh, `${group.group}.${field.key}`)
      for (const option of field.options ?? []) assert.ok(option.label.en && option.label.zh, `${field.key}=${option.value}`)
    }
  }
})

test('the chat settings: three switches that are on and a character count of 4, each with help in both languages', () => {
  const group = FIELDS.find((g) => g.group === 'advanced')
  assert.deepEqual(group.fields.map((f) => f.key), [
    'recallLimit', 'recallTimeoutMs', 'language', 'skipRepeats', 'linkedRecall', 'showAge', 'failureNote', 'captureMinChars', 'blockTone', 'maxNewPerTurn', 'relevanceCheck', 'relevanceProvider', 'relevanceModel', 'relevanceTimeoutMs',
    'afterthought', 'afterthoughtMax', 'conversationPool', 'poolSize', 'recentPool', 'recentSize', 'recentDays',
    'profile', 'dailyWrites', 'redaction', 'newerFirst', 'forgetting',
  ])
  for (const key of ['advanced.skipRepeats', 'advanced.showAge', 'advanced.failureNote']) {
    const field = SETTING_KEYS[key]
    assert.equal(field.kind, 'boolean', key)
    assert.equal(field.default, true, key)
    assert.ok(field.label.en && field.label.zh && field.help.en && field.help.zh, key)
  }
  const chars = SETTING_KEYS['advanced.captureMinChars']
  assert.deepEqual([chars.kind, chars.default, chars.min, chars.max], ['integer', 4, 0, 50])
  assert.match(chars.help.en, /0 turns the length check off/)
  assert.match(chars.help.zh, /0 表示不按字数过滤/)
  // Values are checked like every other setting: a wrong type or a value out of range gives the default.
  const s = readConfig({ advanced: { skipRepeats: 'no', showAge: 0, failureNote: null, captureMinChars: 51 } }).advanced
  assert.deepEqual([s.skipRepeats, s.showAge, s.failureNote, s.captureMinChars], [true, true, true, 4])
  assert.equal(readConfig({ advanced: { captureMinChars: 0 } }).advanced.captureMinChars, 0)
  assert.equal(readConfig({ advanced: { captureMinChars: 50 } }).advanced.captureMinChars, 50)
  assert.equal(readConfig({ advanced: { captureMinChars: -1 } }).advanced.captureMinChars, 4)
  assert.equal(readConfig({ advanced: { captureMinChars: 2.5 } }).advanced.captureMinChars, 4)
  const off = readConfig({ advanced: { skipRepeats: false, showAge: false, failureNote: false } }).advanced
  assert.deepEqual([off.skipRepeats, off.showAge, off.failureNote], [false, false, false])
  // Typed in /memgate set.
  assert.deepEqual(parseSetting(chars, '0'), { ok: true, value: 0 })
  assert.deepEqual(parseSetting(chars, '50'), { ok: true, value: 50 })
  for (const bad of ['51', '-1', '4.5', 'four', '']) assert.equal(parseSetting(chars, bad).ok, false, bad)
  assert.deepEqual(parseSetting(SETTING_KEYS['advanced.skipRepeats'], 'off'), { ok: true, value: false })
  assert.deepEqual(parseSetting(SETTING_KEYS['advanced.showAge'], '关'), { ok: true, value: false })
  // The schema for the host carries the range.
  const schema = buildConfigSchema(recordingSchema())
  assert.deepEqual([schema.dict.advanced.dict.captureMinChars.meta.min, schema.dict.advanced.dict.captureMinChars.meta.max], [0, 50])
})

test('advanced.linkedRecall: a boolean, on by default, right after skipRepeats, with help in both languages', () => {
  const group = FIELDS.find((g) => g.group === 'advanced')
  const keys = group.fields.map((f) => f.key)
  assert.equal(keys[keys.indexOf('skipRepeats') + 1], 'linkedRecall')
  const field = SETTING_KEYS['advanced.linkedRecall']
  assert.equal(field.kind, 'boolean')
  assert.equal(field.default, true)
  assert.equal(field.label.en, 'Recall linked memories')
  assert.equal(field.label.zh, '顺带想起相连的记忆')
  for (const language of ['en', 'zh']) {
    assert.match(field.help[language], /advanced\.recallLimit/)
    assert.match(field.help[language], /retrieval\.hybrid/)
    assert.match(field.help[language], /keyword/)
  }
  assert.equal(readConfig({}).advanced.linkedRecall, true)
  assert.equal(readConfig({ advanced: { linkedRecall: false } }).advanced.linkedRecall, false)
  for (const bad of ['no', 0, null, 'false']) assert.equal(readConfig({ advanced: { linkedRecall: bad } }).advanced.linkedRecall, true, String(bad))
  assert.deepEqual(parseSetting(field, 'off'), { ok: true, value: false })
  assert.deepEqual(parseSetting(field, '关'), { ok: true, value: false })
  assert.deepEqual(parseSetting(field, 'on'), { ok: true, value: true })
  assert.equal(parseSetting(field, 'maybe').ok, false)
  const schema = buildConfigSchema(recordingSchema())
  assert.ok(schema.dict.advanced.dict.linkedRecall, 'the host schema has the field')
})

test('retrieval.hybrid: a boolean, off by default, right after the tier, with help in both languages', () => {
  const group = FIELDS.find((g) => g.group === 'retrieval')
  assert.deepEqual(group.fields.map((f) => f.key), ['tier', 'hybrid', 'url', 'model', 'apiKey'])
  const field = SETTING_KEYS['retrieval.hybrid']
  assert.equal(field.kind, 'boolean')
  assert.equal(field.default, false)
  assert.equal(field.label.en, 'Combine with keyword search (hybrid)')
  assert.equal(field.label.zh, '同时用关键词检索（混合）')
  assert.match(field.help.en, /vector tiers/)
  assert.match(field.help.en, /under keyword it does nothing/)
  assert.match(field.help.en, /loses no memory/)
  assert.match(field.help.zh, /不会丢记忆/)
  assert.equal(readConfig({ retrieval: { hybrid: true } }).retrieval.hybrid, true)
  assert.equal(readConfig({ retrieval: { hybrid: 'on' } }).retrieval.hybrid, false, 'only a real boolean counts')
})

test('the prototype settings: afterthought and conversationPool are off, the two counts are 1 to 5, each with help in both languages', () => {
  for (const key of ['advanced.afterthought', 'advanced.conversationPool']) {
    const field = SETTING_KEYS[key]
    assert.equal(field.kind, 'boolean', key)
    assert.equal(field.default, false, key)
    assert.ok(field.label.en && field.label.zh && field.help.en && field.help.zh, key)
  }
  assert.deepEqual([SETTING_KEYS['advanced.afterthought'].label.zh, SETTING_KEYS['advanced.afterthought'].label.en], ['回复之后再想一想', 'Recall again after replying'])
  assert.deepEqual([SETTING_KEYS['advanced.conversationPool'].label.zh, SETTING_KEYS['advanced.conversationPool'].label.en], ['对话池', 'Conversation pool'])
  const counts = [['advanced.afterthoughtMax', 2, '再想一想最多加几条', 'Memories a second lookup may add'], ['advanced.poolSize', 3, '对话池一次带回几条', 'Memories the pool brings back']]
  for (const [key, value, zh, en] of counts) {
    const field = SETTING_KEYS[key]
    assert.deepEqual([field.kind, field.default, field.min, field.max, field.label.zh, field.label.en], ['integer', value, 1, 5, zh, en], key)
    assert.ok(field.help.en && field.help.zh, key)
    assert.equal(readConfig({ advanced: { [key.slice(9)]: 0 } }).advanced[key.slice(9)], value, 'below the range: the default')
    assert.equal(readConfig({ advanced: { [key.slice(9)]: 6 } }).advanced[key.slice(9)], value, 'above the range: the default')
    assert.equal(readConfig({ advanced: { [key.slice(9)]: 1 } }).advanced[key.slice(9)], 1)
    assert.equal(readConfig({ advanced: { [key.slice(9)]: 5 } }).advanced[key.slice(9)], 5)
    assert.equal(readConfig({ advanced: { [key.slice(9)]: 2.5 } }).advanced[key.slice(9)], value)
    assert.deepEqual(parseSetting(field, '5'), { ok: true, value: 5 })
    for (const bad of ['0', '6', '2.5', 'two', '']) assert.equal(parseSetting(field, bad).ok, false, `${key} ${bad}`)
  }
  assert.equal(readConfig({ advanced: { afterthought: 'on', conversationPool: 1 } }).advanced.afterthought, false, 'a wrong type gives the default')
  assert.equal(readConfig({ advanced: { afterthought: true, conversationPool: true } }).advanced.conversationPool, true)
  assert.deepEqual(parseSetting(SETTING_KEYS['advanced.afterthought'], 'on'), { ok: true, value: true })
  for (const key of ['advanced.afterthought', 'advanced.conversationPool']) {
    assert.match(SETTING_KEYS[key].help.en, /Experimental/)
    assert.match(SETTING_KEYS[key].help.zh, /实验/)
    assert.match(SETTING_KEYS[key].help.en, /modules\.autoRecall/)
  }
  const schema = buildConfigSchema(recordingSchema())
  const dict = schema.dict.advanced.dict
  assert.deepEqual([dict.afterthoughtMax.meta.min, dict.afterthoughtMax.meta.max, dict.poolSize.meta.min, dict.poolSize.meta.max], [1, 5, 1, 5])
})

test('the recent pool settings: a switch that is off, a count of 1 to 5 and a number of days from 1 to 30, with help in both languages', () => {
  const pool = SETTING_KEYS['advanced.recentPool']
  assert.deepEqual([pool.kind, pool.default, pool.label.zh, pool.label.en], ['boolean', false, '近期池', 'Recent pool'])
  assert.match(pool.help.en, /Experimental/)
  assert.match(pool.help.en, /recent-pool\.json/)
  assert.match(pool.help.zh, /recent-pool\.json/)
  const size = SETTING_KEYS['advanced.recentSize']
  assert.deepEqual([size.kind, size.default, size.min, size.max, size.label.zh, size.label.en], ['integer', 3, 1, 5, '近期池一次带上几条', 'Memories the recent pool carries'])
  const days = SETTING_KEYS['advanced.recentDays']
  assert.deepEqual([days.kind, days.default, days.min, days.max, days.label.zh, days.label.en], ['integer', 7, 1, 30, '近期池保留几天', 'Days the recent pool is kept'])
  for (const field of [pool, size, days]) assert.ok(field.help.en && field.help.zh)
  assert.equal(readConfig({ advanced: { recentDays: 31 } }).advanced.recentDays, 7)
  assert.equal(readConfig({ advanced: { recentDays: 30 } }).advanced.recentDays, 30)
  assert.equal(readConfig({ advanced: { recentDays: 0 } }).advanced.recentDays, 7)
  assert.equal(readConfig({ advanced: { recentSize: 6 } }).advanced.recentSize, 3)
  assert.equal(readConfig({ advanced: { recentPool: 'on' } }).advanced.recentPool, false)
  assert.deepEqual(parseSetting(days, '30'), { ok: true, value: 30 })
  for (const bad of ['0', '31', '1.5', 'week', '']) assert.equal(parseSetting(days, bad).ok, false, bad)
  const dict = buildConfigSchema(recordingSchema()).dict.advanced.dict
  assert.deepEqual([dict.recentSize.meta.min, dict.recentSize.meta.max, dict.recentDays.meta.min, dict.recentDays.meta.max], [1, 5, 1, 30])
})

test('the block wording and the cap on new memories: a choice that is normal and a count from 0 to 5, with help in both languages', () => {
  const tone = SETTING_KEYS['advanced.blockTone']
  assert.deepEqual([tone.kind, tone.default, tone.options.map((o) => o.value), tone.label.zh, tone.label.en], ['choice', 'normal', ['normal', 'strict'], '记忆块的说明', 'Wording of the memory block'])
  for (const option of tone.options) assert.ok(option.label.en && option.label.zh)
  assert.ok(tone.help.en && tone.help.zh)
  assert.match(tone.help.en, /memgate_search/)
  const cap = SETTING_KEYS['advanced.maxNewPerTurn']
  assert.deepEqual([cap.kind, cap.default, cap.min, cap.max, cap.label.zh, cap.label.en], ['integer', 0, 0, 5, '每轮最多新给几条', 'New memories per turn'])
  assert.ok(cap.help.en && cap.help.zh)
  assert.equal(readConfig({ advanced: { blockTone: 'strict' } }).advanced.blockTone, 'strict')
  assert.equal(readConfig({ advanced: { blockTone: 'loud' } }).advanced.blockTone, 'normal')
  assert.equal(readConfig({ advanced: { maxNewPerTurn: 5 } }).advanced.maxNewPerTurn, 5)
  assert.equal(readConfig({ advanced: { maxNewPerTurn: 6 } }).advanced.maxNewPerTurn, 0)
  assert.equal(readConfig({ advanced: { maxNewPerTurn: -1 } }).advanced.maxNewPerTurn, 0)
  assert.equal(readConfig({ advanced: { maxNewPerTurn: 1.5 } }).advanced.maxNewPerTurn, 0)
  assert.deepEqual(parseSetting(tone, 'strict'), { ok: true, value: 'strict' })
  assert.equal(parseSetting(tone, 'loud').ok, false)
  assert.deepEqual(parseSetting(cap, '0'), { ok: true, value: 0 })
  for (const bad of ['6', '-1', '1.5', 'two', '']) assert.equal(parseSetting(cap, bad).ok, false, bad)
  const dict = buildConfigSchema(recordingSchema()).dict.advanced.dict
  assert.deepEqual([dict.maxNewPerTurn.meta.min, dict.maxNewPerTurn.meta.max], [0, 5])
})

test('the relevance check: a switch that is off, a provider and a model that are empty and a time limit from 500 to 10000, with help in both languages', () => {
  const on = SETTING_KEYS['advanced.relevanceCheck']
  assert.deepEqual([on.kind, on.default, on.label.zh, on.label.en], ['boolean', false, '回复前让模型先挑一遍', 'Let a model pick the related memories first'])
  for (const key of ['relevanceCheck', 'relevanceProvider', 'relevanceModel', 'relevanceTimeoutMs']) {
    const field = SETTING_KEYS[`advanced.${key}`]
    assert.ok(field.label.en && field.label.zh && field.help.en && field.help.zh, key)
  }
  assert.match(SETTING_KEYS['advanced.relevanceCheck'].help.en, /one more model call/)
  assert.match(SETTING_KEYS['advanced.relevanceCheck'].help.en, /sent to the provider/)
  assert.deepEqual(['relevanceProvider', 'relevanceModel'].map((key) => [SETTING_KEYS[`advanced.${key}`].kind, SETTING_KEYS[`advanced.${key}`].default]), [['string', ''], ['string', '']])
  const ms = SETTING_KEYS['advanced.relevanceTimeoutMs']
  assert.deepEqual([ms.kind, ms.default, ms.min, ms.max], ['integer', 3000, 500, 10000])
  assert.equal(readConfig({ advanced: { relevanceCheck: true } }).advanced.relevanceCheck, true)
  assert.equal(readConfig({ advanced: { relevanceTimeoutMs: 499 } }).advanced.relevanceTimeoutMs, 3000)
  assert.equal(readConfig({ advanced: { relevanceTimeoutMs: 10001 } }).advanced.relevanceTimeoutMs, 3000)
  assert.equal(readConfig({ advanced: { relevanceTimeoutMs: 500 } }).advanced.relevanceTimeoutMs, 500)
  assert.equal(readConfig({ advanced: { relevanceProvider: 'p', relevanceModel: 'm' } }).advanced.relevanceModel, 'm')
  assert.equal(readConfig({ advanced: { relevanceModel: 7 } }).advanced.relevanceModel, '')
  assert.deepEqual(parseSetting(on, 'on'), { ok: true, value: true })
  assert.deepEqual(parseSetting(SETTING_KEYS['advanced.relevanceModel'], 'small-model'), { ok: true, value: 'small-model' })
  for (const bad of ['499', '10001', 'fast', '']) assert.equal(parseSetting(ms, bad).ok, false, bad)
  assert.deepEqual(parseSetting(ms, '10000'), { ok: true, value: 10000 })
  const dict = buildConfigSchema(recordingSchema()).dict.advanced.dict
  assert.deepEqual([dict.relevanceTimeoutMs.meta.min, dict.relevanceTimeoutMs.meta.max], [500, 10000])
})

// The optional attributes of a field row (needs, unit, experimental) are for the settings page only.
const possibleValues = (field) => (field.kind === 'boolean' ? [true, false] : field.kind === 'choice' ? field.options.map((option) => option.value) : null)

test('needs: every field that depends on another names a real setting, a value it can hold, and never loops', () => {
  let count = 0
  for (const [name, field] of Object.entries(SETTING_KEYS)) {
    if (!field.needs) continue
    count += 1
    const target = SETTING_KEYS[field.needs.key]
    assert.ok(target, `${name} needs ${field.needs.key}, which is not a setting`)
    assert.notEqual(field.needs.key, name, `${name} needs itself`)
    assert.ok(Array.isArray(field.needs.in) && field.needs.in.length > 0, `${name}: needs.in is a non-empty list`)
    const possible = possibleValues(target)
    assert.ok(possible, `${name} needs ${field.needs.key}, which is neither a boolean nor a choice`)
    for (const value of field.needs.in) assert.ok(possible.includes(value), `${name}: ${JSON.stringify(value)} is not a value of ${field.needs.key}`)
    // Walk the chain of needs: it must end, without meeting a field twice.
    const seen = new Set([name])
    for (let next = field.needs.key; next; next = SETTING_KEYS[next].needs?.key) {
      assert.ok(!seen.has(next), `${name}: needs loop through ${next}`)
      seen.add(next)
    }
  }
  assert.equal(count, 20, 'the fields that depend on another one')
  const needs = (name) => SETTING_KEYS[name].needs
  assert.deepEqual(needs('retrieval.hybrid'), { key: 'retrieval.tier', in: ['auto', 'endpoint', 'local'] })
  assert.deepEqual(needs('retrieval.apiKey'), { key: 'retrieval.tier', in: ['endpoint'] })
  assert.deepEqual(needs('external.token'), { key: 'gateway.mode', in: ['external'] })
  assert.deepEqual(needs('advanced.captureMinChars'), { key: 'modules.autoCapture', in: [true] })
  assert.deepEqual(needs('advanced.forgetting'), { key: 'gateway.mode', in: ['managed'] })
  assert.equal(needs('advanced.recallLimit'), undefined)
})

test('unit is a word in both languages on integer fields only; experimental marks four boolean switches', () => {
  const units = {}
  for (const [name, field] of Object.entries(SETTING_KEYS)) {
    if (field.unit !== undefined) {
      assert.equal(field.kind, 'integer', `${name}: unit on a field that is not an integer`)
      assert.ok(field.unit.en && field.unit.zh, `${name}: unit has en and zh`)
      units[name] = [field.unit.en, field.unit.zh]
    }
  }
  assert.deepEqual(units, {
    'advanced.recallLimit': ['memories', '条'], 'advanced.recallTimeoutMs': ['ms', '毫秒'], 'advanced.captureMinChars': ['characters', '字'],
    'advanced.maxNewPerTurn': ['memories', '条'], 'advanced.relevanceTimeoutMs': ['ms', '毫秒'], 'advanced.afterthoughtMax': ['memories', '条'],
    'advanced.poolSize': ['memories', '条'], 'advanced.recentSize': ['memories', '条'], 'advanced.recentDays': ['days', '天'], 'advanced.dailyWrites': ['per day', '条/天'],
  })
  const experimental = Object.entries(SETTING_KEYS).filter(([, field]) => field.experimental !== undefined)
  for (const [name, field] of experimental) assert.deepEqual([name, field.kind, field.experimental], [name, 'boolean', true])
  assert.deepEqual(experimental.map(([name]) => name), ['advanced.relevanceCheck', 'advanced.afterthought', 'advanced.conversationPool', 'advanced.recentPool'])
})

test('the schema does not carry the page-only attributes of a field', () => {
  const dict = buildConfigSchema(recordingSchema()).dict
  for (const group of FIELDS) {
    for (const field of group.fields) {
      const meta = JSON.stringify(dict[group.group].dict[field.key])
      for (const word of ['needs', 'unit', 'experimental']) assert.ok(!meta.includes(`"${word}"`), `${group.group}.${field.key}: ${word} in the schema`)
    }
  }
})
