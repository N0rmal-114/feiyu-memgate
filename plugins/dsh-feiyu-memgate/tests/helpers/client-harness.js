// Shared by the tests of the settings page (tests/client-page.test.js, tests/client-preview.test.js):
// loads the assembled script client/client.js the way the host does, with a small stand-in for React that
// expands function components with inert hooks, and a few helpers to look into the element trees it makes.
//
// The stand-in has no state: `useState` returns its initial value and the setter does nothing, so a
// rendered tree always shows the page in its first state. A test that needs a hook to start from
// another value passes `states`, a function that gets the initial value and returns the one to use.
import fs from 'node:fs'
import vm from 'node:vm'

const SOURCE = fs.readFileSync(new URL('../../client/client.js', import.meta.url), 'utf8')

/** Values made inside the script's own realm, copied into this one (deepEqual compares prototypes). */
export const plain = (value) => JSON.parse(JSON.stringify(value))

export function fakeReact({ states } = {}) {
  const createElement = (type, props, ...children) => ({ type, props: { ...(props ?? {}), children: children.flat(Infinity) } })
  return {
    createElement,
    useState: (init) => {
      const first = typeof init === 'function' ? init() : init
      return [states ? states(first) : first, () => {}]
    },
    useRef: (value) => ({ current: value }),
    useEffect: () => {},
  }
}

/** Run the script and return what its factory returns, with the ids of every module it asked the host for. */
export function load({ states, globals } = {}) {
  let registered = null
  const context = { window: { __ModuleLoader__: { load: (record) => { registered = record } } }, console, setTimeout, clearTimeout, setInterval, clearInterval, ...globals }
  vm.runInNewContext(SOURCE, context, { filename: 'client.js' })
  const required = []
  const React = fakeReact({ states })
  const exports = registered.factory((id) => {
    required.push(id)
    if (id === 'react') return React
    throw new Error(`not in the shared module table: ${id}`)
  })
  return { source: SOURCE, registered, exports, required, internals: exports.internals }
}

/** Expand function components; keep host elements and text. */
export function expand(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return null
  if (Array.isArray(node)) return node.map(expand)
  if (typeof node !== 'object') return node
  if (typeof node.type === 'function') return expand(node.type(node.props))
  return { type: node.type, props: { ...node.props, children: node.props.children.map(expand) } }
}

export const walk = (node, visit) => {
  if (Array.isArray(node)) return node.forEach((n) => walk(n, visit))
  if (node && typeof node === 'object') {
    visit(node)
    walk(node.props.children, visit)
  }
}

/** The text of a tree, one piece per line. */
export const textOf = (tree) => {
  const out = []
  const collect = (node) => {
    if (Array.isArray(node)) node.forEach(collect)
    else if (node && typeof node === 'object') collect(node.props.children)
    else if (node !== null && node !== undefined) out.push(String(node))
  }
  collect(tree)
  return out.join('\n')
}

/** The text of a tree as it reads: pieces joined with nothing between them. */
export const flatText = (tree) => textOf(tree).split('\n').join('')

export const findAll = (tree, test) => {
  const out = []
  walk(tree, (node) => { if (test(node)) out.push(node) })
  return out
}

/** Elements with a class, by whole class name. */
export const withClass = (tree, name) => findAll(tree, (n) => typeof n.props.className === 'string' && n.props.className.split(' ').includes(name))

/** Elements with an ARIA role. */
export const withRole = (tree, role) => findAll(tree, (n) => n.props.role === role)

export function translator(dict) {
  return (key, params = {}) => {
    const template = dict[key]
    if (template === undefined) return key
    return template.replace(/\{(\w+)\}/g, (match, name) => (name in params ? String(params[name]) : match))
  }
}

/** The translate function of a language, from the script's own dictionaries. */
export function translatorFor(lang, loaded = load()) {
  return translator(plain(loaded.internals.dictionaries())[lang])
}
