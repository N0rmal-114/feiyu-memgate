// The rules of the conversation pool (src/pool.js): entering, strengthening, decay, eviction, the mention
// rule, picking, the pending memories of a second lookup and the cap on sessions.
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DECAY, FORGET_BELOW, MAX_ITEMS, MAX_SESSIONS, PICK_FROM, RECENT_FROM, RECENT_MAX_ITEMS, RECENT_VERSION, advance, carry, parseSaved, planSave, snapshot, createPool, enter, mentions, offer, pick, reinforce, takePending,
} from '../src/pool.js'

const memory = (text, extra = {}) => ({ text, ...extra })
const fresh = () => createPool().touch('s')
const strengths = (state) => Object.fromEntries([...state.items.values()].map((i) => [i.text, Number(i.strength.toFixed(4))]))

test('a memory given in a turn enters at strength 1 with that turn; one given again gains 1 and the new turn', () => {
  const state = fresh()
  advance(state, { decay: true })
  enter(state, [memory('The user drinks oolong tea', { date: '2026-11-01', source: 'memories' }), { text: '' }, null, { id: 'no text' }])
  assert.deepEqual([...state.items.values()], [{ text: 'The user drinks oolong tea', date: '2026-11-01', source: 'memories', strength: 1, turn: 1 }])
  advance(state, { decay: true })
  advance(state, { decay: true })
  enter(state, [memory('The user drinks oolong tea', { date: '2026-11-05' })])
  const [item] = [...state.items.values()]
  assert.equal(item.turn, 3)
  assert.equal(item.date, '2026-11-05', 'the newer date')
  assert.equal(item.source, 'memories', 'a missing source keeps the known one')
  assert.ok(Math.abs(item.strength - (DECAY ** 2 + 1)) < 1e-9)
})

test('decay: every turn multiplies the strength by 0.8', () => {
  const state = fresh()
  enter(state, [memory('The user keeps a bicycle')])
  const seen = []
  for (let i = 0; i < 3; i++) {
    advance(state, { decay: true })
    seen.push(Number([...state.items.values()][0].strength.toFixed(4)))
  }
  assert.deepEqual(seen, [0.8, 0.64, 0.512])
})

test('decay: the item goes at the first turn its strength is below 0.2 (0.8 to the 8th is 0.168), not before (0.8 to the 7th is 0.21)', () => {
  const state = fresh()
  enter(state, [memory('The user keeps a bicycle')])
  for (let i = 0; i < 7; i++) advance(state, { decay: true })
  assert.equal(state.items.size, 1)
  assert.ok(Math.abs([...state.items.values()][0].strength - DECAY ** 7) < 1e-12)
  advance(state, { decay: true })
  assert.equal(state.items.size, 0)
})

test('without decay a turn is only counted: nothing weakens, nothing is dropped, nothing is boosted', () => {
  const state = fresh()
  enter(state, [memory('The user keeps a bicycle')])
  advance(state, { message: 'my bicycle' })
  assert.equal(state.turn, 1)
  assert.deepEqual(strengths(state), { 'The user keeps a bicycle': 1 })
})

test('a message that mentions an item gives it +1, after the decay', () => {
  const state = fresh()
  enter(state, [memory('The user drinks oolong tea'), memory('The user keeps a bicycle')])
  advance(state, { decay: true, message: 'Is the oolong still good?' })
  assert.deepEqual(strengths(state), { 'The user drinks oolong tea': 1.8, 'The user keeps a bicycle': 0.8 })
})

test('mentions: Chinese text shares a run of three characters', () => {
  assert.equal(mentions('今天又想喝乌龙茶了', '主人每天早上喝乌龙茶'), true)
  assert.equal(mentions('乌龙', '主人每天早上喝乌龙茶'), false, 'two characters are not a run of three')
  assert.equal(mentions('今天天气不错', '主人每天早上喝乌龙茶'), false)
  assert.equal(mentions('喝，乌，龙，茶', '主人每天早上喝乌龙茶'), false, 'punctuation ends a run')
  assert.equal(mentions('想买自行车', '主人把自行车放在车库里'), true)
  assert.equal(mentions('[图片] 😀 ~~', '主人把自行车放在车库里'), false, 'nothing meaningful')
})

test('mentions: English text shares a whole word of three or more letters, not a piece of a word and not a common word', () => {
  assert.equal(mentions('Is my oolong ready?', 'The user drinks oolong tea every morning'), true)
  assert.equal(mentions('OOLONG!', 'The user drinks oolong tea every morning'), true, 'case does not matter')
  assert.equal(mentions('I like tea', 'The user drinks oolong tea every morning'), true, 'tea is a word of three letters')
  assert.equal(mentions('What do you think about the weather?', 'The user drinks oolong tea every morning'), false, 'only common words are shared')
  assert.equal(mentions('The cat sat', 'Her sister plays the violin'), false, 'the alone is not a mention')
  assert.equal(mentions('She plays the cello', 'The user drinks oolong tea'), false)
  assert.equal(mentions('thermal', 'the user'), false, 'three letters inside another word are not a run')
  assert.equal(mentions('boat', 'The user bought a boat in 2019'), true)
  assert.equal(mentions('we went in 2019', 'The user bought a boat in 2019'), true, 'a number is a word')
  assert.equal(mentions('tea', 'The user drinks tea'), true)
  assert.equal(mentions('', 'The user drinks tea'), false)
  assert.equal(mentions('tea', ''), false)
  assert.equal(mentions(undefined, 'The user drinks tea'), false)
})

test('mentions: Chinese and English in one text are compared each their way', () => {
  assert.equal(mentions('我的 Bicycle 坏了', '主人有一辆 bicycle'), true)
  assert.equal(mentions('我的自行车坏了', '主人有一辆 bicycle，也有自行车'), true)
  assert.equal(mentions('我的 bicycle 坏了', '主人有一辆自行车'), false)
})

test('over 24 items the weakest go, of equal strength the one given longest ago', () => {
  const state = fresh()
  advance(state, { decay: true })
  enter(state, Array.from({ length: MAX_ITEMS }, (_, i) => memory(`memory number ${i} here`)))
  assert.equal(state.items.size, MAX_ITEMS)
  advance(state, { decay: true }) // every item 0.8, turn 2
  enter(state, [memory('memory number 3 here')]) // 1.8 now
  advance(state, { decay: true }) // 0.64 for 23 items, 1.44 for the one given twice, turn 3
  enter(state, [memory('a new one'), memory('another new one')]) // two more: 26 items, two go
  assert.equal(state.items.size, MAX_ITEMS)
  assert.ok(state.items.has('memory number 3 here'), 'the strong one stays')
  assert.ok(state.items.has('a new one') && state.items.has('another new one'), 'the new ones (strength 1) stay')
  assert.ok(!state.items.has('memory number 0 here') && !state.items.has('memory number 1 here'), 'the first entered of the weakest go')
  assert.ok(state.items.has('memory number 2 here'))
})

test('eviction prefers the weaker over the older', () => {
  const state = fresh()
  enter(state, Array.from({ length: MAX_ITEMS }, (_, i) => memory(`memory number ${i} here`)))
  advance(state, { decay: true })
  enter(state, [memory('memory number 0 here')]) // strength 1.8, turn 1 -> 2
  advance(state, { decay: true })
  enter(state, [memory('fresh')]) // 1; the others 0.64 and 1.44
  assert.equal(state.items.size, MAX_ITEMS)
  assert.ok(!state.items.has('memory number 1 here'), 'the weakest and oldest-entered went')
  assert.ok(state.items.has('memory number 0 here'))
  assert.ok(state.items.has('fresh'))
})

test('pick: at least strength 0.3, strongest first, then the most recently given, then in entering order; at most the limit', () => {
  const state = fresh()
  enter(state, [memory('first in'), memory('second in'), memory('third in'), memory('fourth in')])
  advance(state, { decay: true, message: 'second in' })
  advance(state, { decay: true })
  enter(state, [memory('fifth in')])
  // second in: (0.8 + 1) * 0.8 = 1.44; fifth: 1; first, third, fourth: 0.64, picked after the stronger ones
  assert.deepEqual(pick(state, { limit: 5 }).map((m) => m.text), ['second in', 'fifth in', 'first in', 'third in', 'fourth in'])
  assert.deepEqual(pick(state, { limit: 1 }).map((m) => m.text), ['second in'])
  assert.deepEqual(pick(state, { limit: 0 }), [])

  const s2 = fresh()
  advance(s2)
  enter(s2, [memory('a'), memory('b')]) // both 1, turn 1
  advance(s2)
  enter(s2, [memory('c')]) // 1, turn 2
  assert.deepEqual(pick(s2, { limit: 3 }).map((m) => m.text), ['c', 'a', 'b'], 'same strength: the newer turn first, then entering order')
})

test('pick: a skipped text is left out and does not use up the limit; date and source are carried', () => {
  const state = fresh()
  enter(state, [memory('shown in the history', { date: '2026-11-01' }), memory('not shown', { date: '2026-11-02', source: 'memories' })])
  assert.deepEqual(pick(state, { limit: 1, skip: (text) => text === 'shown in the history' }), [{ text: 'not shown', date: '2026-11-02', source: 'memories' }])
})

test('pending: kept for the turn after the one it was asked in, taken once, and a late answer is dropped', () => {
  const state = fresh()
  advance(state) // turn 1
  assert.equal(offer(state, [memory('found'), { text: 5 }], state.turn + 1), true)
  assert.deepEqual(takePending(state), [], 'it is not for this turn')
  assert.equal(state.pending, null, 'cleared whatever happened')

  assert.equal(offer(state, [memory('found')], state.turn + 1), true)
  advance(state) // turn 2
  assert.deepEqual(takePending(state), [{ text: 'found' }])
  assert.deepEqual(takePending(state), [], 'taken once')

  const forTurn = state.turn + 1
  advance(state) // turn 3 has begun when the answer arrives
  assert.equal(offer(state, [memory('late')], forTurn), false)
  assert.equal(state.pending, null)
  assert.equal(offer(state, [], state.turn + 1), false, 'nothing found, nothing kept')
  assert.equal(offer(state, [memory('x')], 'soon'), false)
})

test('a pending answer that was never taken is not given two turns later', () => {
  const state = fresh()
  advance(state)
  offer(state, [memory('found')], 2)
  advance(state)
  advance(state)
  assert.deepEqual(takePending(state), [])
})

test('sessions: at most 64, the least recently used goes first, and touching a session counts as use', () => {
  const chat = createPool()
  for (let i = 0; i < MAX_SESSIONS; i++) chat.touch(`s${i}`)
  assert.equal(chat.size, MAX_SESSIONS)
  const first = chat.touch('s0') // s0 is now the most recent
  assert.equal(chat.touch('s0'), first, 'the same state')
  chat.touch('new')
  assert.equal(chat.size, MAX_SESSIONS)
  assert.equal(chat.peek('s1'), undefined, 'the least recently used went')
  assert.ok(chat.peek('s0') && chat.peek('new') && chat.peek('s2'))
  chat.peek('s2') // a peek is not a use
  chat.touch('another')
  assert.equal(chat.peek('s2'), undefined)
  assert.equal(chat.drop('s0'), true)
  assert.equal(chat.peek('s0'), undefined)
  chat.clear()
  assert.equal(chat.size, 0)
})

test('clearPending forgets what a second lookup found in every session and nothing else', () => {
  const chat = createPool()
  const a = chat.touch('a')
  const b = chat.touch('b')
  enter(a, [memory('kept')])
  offer(a, [memory('x')], 1)
  offer(b, [memory('y')], 1)
  chat.clearPending()
  assert.equal(a.pending, null)
  assert.equal(b.pending, null)
  assert.equal(a.items.size, 1)
})

test('an item given once can be picked for about five turns: 1, 0.8, 0.64, 0.51, 0.41, 0.33, and not at 0.26', () => {
  assert.equal(PICK_FROM, 0.3)
  const state = fresh()
  enter(state, [memory('The user keeps a bicycle')])
  const picked = []
  for (let i = 0; i < 7; i++) {
    picked.push(pick(state, { limit: 3 }).length)
    advance(state, { decay: true })
  }
  // strengths 1, 0.8, 0.64, 0.51, 0.41, 0.33 are picked; 0.262 is not
  assert.deepEqual(picked, [1, 1, 1, 1, 1, 1, 0])
})

test('reinforce: every item the reply mentions gains 1, the others stay', () => {
  const state = fresh()
  enter(state, [memory('The user drinks oolong tea'), memory('The user keeps a bicycle'), memory('主人把自行车放在车库里')])
  reinforce(state, 'Oolong is a fine choice, and the garage is tidy.')
  assert.deepEqual(strengths(state), { 'The user drinks oolong tea': 2, 'The user keeps a bicycle': 1, '主人把自行车放在车库里': 1 })
  reinforce(state, '骑自行车去吧')
  assert.equal(state.items.get('主人把自行车放在车库里').strength, 2)
  reinforce(state, '')
  reinforce(state, undefined)
  reinforce(state, 'nothing in common here at all')
  assert.deepEqual(strengths(state), { 'The user drinks oolong tea': 2, 'The user keeps a bicycle': 1, '主人把自行车放在车库里': 2 })
})

test('reinforce uses the same mention rule: a common word is not a use', () => {
  const state = fresh()
  enter(state, [memory('The user drinks oolong tea')])
  reinforce(state, 'The user said that you can do what you like')
  assert.equal(state.items.get('The user drinks oolong tea').strength, 1)
})

// ---------------------------------------------------------------- the recent pool (pure parts)

const NOW = Date.UTC(2026, 10, 18, 9, 0, 0)
const DAY_MS = 24 * 60 * 60 * 1000
const stateWith = (...entries) => {
  const state = fresh()
  for (const [text, strength, extra] of entries) {
    enter(state, [memory(text, extra)])
    state.items.get(text).strength = strength
  }
  return state
}

test('snapshot: items of strength 0.5 or more, strongest first, with nothing but text, date, source and strength', () => {
  const state = stateWith(['weak', 0.49], ['edge', 0.5, { source: 'memories' }], ['strong', 2.5, { date: '2026-11-01', source: 'memories' }], ['mid', 1])
  const snap = snapshot(state, { session: 'chat-a', now: NOW })
  assert.deepEqual(Object.keys(snap), ['version', 'saved', 'session', 'items'])
  assert.deepEqual([snap.version, snap.session, snap.saved], [RECENT_VERSION, 'chat-a', '2026-11-18T09:00:00.000Z'])
  assert.deepEqual(snap.items, [
    { text: 'strong', date: '2026-11-01', source: 'memories', strength: 2.5 },
    { text: 'mid', strength: 1 },
    { text: 'edge', source: 'memories', strength: 0.5 },
  ])
  assert.equal(RECENT_FROM, 0.5)
})

test('snapshot: at most 12 items, the strongest; of equal strength the one given last, then the first entered', () => {
  const entries = Array.from({ length: 15 }, (_, i) => [`memory ${i}`, 1 + i / 100])
  const snap = snapshot(stateWith(...entries), { session: 's', now: NOW })
  assert.equal(snap.items.length, RECENT_MAX_ITEMS)
  assert.deepEqual(snap.items.map((i) => i.text).slice(0, 2), ['memory 14', 'memory 13'])
  assert.ok(!snap.items.some((i) => i.text === 'memory 0'))
  const tie = stateWith(['first', 1], ['second', 1])
  tie.items.get('second').turn = 5
  assert.deepEqual(snapshot(tie, { session: 's', now: NOW }).items.map((i) => i.text), ['second', 'first'])
})

test('snapshot: nothing without a usable session id or time; an empty state gives an empty list', () => {
  assert.equal(snapshot(fresh(), { session: '', now: NOW }), null)
  assert.equal(snapshot(fresh(), { session: undefined, now: NOW }), null)
  assert.equal(snapshot(fresh(), { session: 's', now: undefined }), null)
  assert.equal(snapshot(fresh(), { session: 's', now: Number.NaN }), null)
  assert.deepEqual(snapshot(fresh(), { session: 's', now: NOW }).items, [])
})

test('planSave: a snapshot with items is written; an empty one only replaces a file of the same session', () => {
  const full = snapshot(stateWith(['a', 1]), { session: 'one', now: NOW })
  const empty = snapshot(fresh(), { session: 'one', now: NOW })
  const other = parseSaved(snapshot(stateWith(['b', 1]), { session: 'two', now: NOW }))
  assert.equal(planSave(full, null), full)
  assert.equal(planSave(full, other), full)
  assert.equal(planSave(empty, null), null)
  assert.equal(planSave(empty, other), null, 'another session\'s file is left alone')
  assert.equal(planSave(empty, { ...other, session: 'one' }), empty, 'its own file becomes an empty list')
  assert.equal(planSave(null, other), null)
})

test('parseSaved: a good object passes, a bad item is left out, anything else is refused', () => {
  const good = { version: 1, saved: '2026-11-18T09:00:00.000Z', session: 's', items: [{ text: 'a', strength: 1, date: '2026-11-01', source: 'memories' }, { text: '', strength: 1 }, { text: 'b' }, { text: 'c', strength: 'x' }, null, { text: 'd', strength: 0.7, extra: 'dropped' }] }
  assert.deepEqual(parseSaved(good), { version: 1, saved: good.saved, session: 's', items: [{ text: 'a', strength: 1, date: '2026-11-01', source: 'memories' }, { text: 'd', strength: 0.7 }] })
  for (const bad of [null, undefined, 5, 'text', [], {}, { ...good, version: 2 }, { ...good, version: undefined }, { ...good, session: '' }, { ...good, session: 5 }, { ...good, saved: 'yesterday' }, { ...good, saved: 5 }, { ...good, items: 'no' }, { ...good, items: undefined }]) {
    assert.equal(parseSaved(bad), null, JSON.stringify(bad))
  }
  assert.equal(parseSaved({ ...good, session: 'x'.repeat(201) }), null)
})

test('carry: items of strength 0.5 or more of another session, strongest first, at most the size', () => {
  const saved = parseSaved({ version: 1, saved: new Date(NOW - DAY_MS).toISOString(), session: 'old', items: [
    { text: 'low', strength: 0.4 }, { text: 'b', strength: 1 }, { text: 'a', strength: 3, date: '2026-11-01', source: 'memories' }, { text: 'c', strength: 1 }, { text: 'd', strength: 0.5 },
  ] })
  assert.deepEqual(carry(saved, { session: 'new', now: NOW, days: 7, size: 3 }), [
    { text: 'a', date: '2026-11-01', source: 'memories' }, { text: 'b', date: undefined, source: undefined }, { text: 'c', date: undefined, source: undefined },
  ])
  assert.equal(carry(saved, { session: 'new', now: NOW, days: 7, size: 1 }).length, 1)
  assert.equal(carry(saved, { session: 'new', now: NOW, days: 7, size: 5 }).length, 4, 'the 0.4 item is under the line')
  assert.deepEqual(carry(saved, { session: 'new', now: NOW, days: 7, size: 0 }), [])
})

test('carry: nothing from the same session, nothing without a file or a time', () => {
  const saved = parseSaved({ version: 1, saved: new Date(NOW).toISOString(), session: 'same', items: [{ text: 'a', strength: 1 }] })
  assert.deepEqual(carry(saved, { session: 'same', now: NOW, days: 7, size: 3 }), [])
  assert.deepEqual(carry(null, { session: 'new', now: NOW, days: 7, size: 3 }), [])
  assert.deepEqual(carry(saved, { session: 'new', now: undefined, days: 7, size: 3 }), [])
})

test('carry: expires after the days, and a file dated more than a day ahead is not trusted', () => {
  const at = (ms) => parseSaved({ version: 1, saved: new Date(ms).toISOString(), session: 'old', items: [{ text: 'a', strength: 1 }] })
  const n = (saved, days = 7) => carry(saved, { session: 'new', now: NOW, days, size: 3 }).length
  assert.equal(n(at(NOW - 7 * DAY_MS)), 1, 'exactly seven days old is kept')
  assert.equal(n(at(NOW - 7 * DAY_MS - 1)), 0)
  assert.equal(n(at(NOW - 2 * DAY_MS), 1), 0)
  assert.equal(n(at(NOW - 30 * DAY_MS), 30), 1)
  assert.equal(n(at(NOW + DAY_MS)), 1, 'a day ahead is a clock difference')
  assert.equal(n(at(NOW + DAY_MS + 1)), 0)
  assert.equal(n(at(NOW + 400 * DAY_MS)), 0)
})
