// Correctness-only lint, no style rules. ESLint 10 (flat config), run through npx with the exact version
// 10.15.0 and nothing installed: this file imports nothing and spells out the rules and the global names.

const nodeGlobals = Object.fromEntries([
  'process', 'Buffer', 'console', 'global', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval',
  'setImmediate', 'clearImmediate', 'queueMicrotask', 'structuredClone', 'URL', 'URLSearchParams',
  'TextEncoder', 'TextDecoder', 'AbortController', 'AbortSignal', 'fetch', 'Headers', 'Request', 'Response',
  'FormData', 'Blob', 'performance', 'EventTarget', 'Event', 'atob', 'btoa', 'crypto', 'DOMException',
].map((name) => [name, 'readonly']))

// Browser globals used by the settings page (client/src) and the page preview.
const browserGlobals = Object.fromEntries([
  'window', 'document', 'navigator', 'location', 'history', 'console', 'fetch', 'setTimeout', 'clearTimeout',
  'setInterval', 'clearInterval', 'requestAnimationFrame', 'cancelAnimationFrame', 'queueMicrotask',
  'URL', 'URLSearchParams', 'TextEncoder', 'TextDecoder', 'AbortController', 'AbortSignal', 'Headers',
  'Request', 'Response', 'FormData', 'Blob', 'FileReader', 'performance', 'Event', 'CustomEvent', 'EventTarget',
  'localStorage', 'sessionStorage', 'getComputedStyle', 'matchMedia', 'ResizeObserver', 'MutationObserver',
  'IntersectionObserver', 'HTMLElement', 'Node', 'Element', 'atob', 'btoa', 'structuredClone', 'alert',
  'confirm', 'prompt', 'crypto',
].map((name) => [name, 'readonly']))

// A Cloudflare Worker module: web-standard APIs, no Node.
const workerGlobals = Object.fromEntries([
  'console', 'fetch', 'Request', 'Response', 'Headers', 'URL', 'URLSearchParams', 'TextEncoder', 'TextDecoder',
  'AbortController', 'AbortSignal', 'crypto', 'atob', 'btoa', 'setTimeout', 'clearTimeout', 'setInterval',
  'clearInterval', 'queueMicrotask', 'structuredClone', 'FormData', 'Blob', 'ReadableStream', 'WritableStream',
  'TransformStream', 'CompressionStream', 'DecompressionStream', 'Event', 'EventTarget',
].map((name) => [name, 'readonly']))

const rules = {
  'no-undef': 'error',
  'no-unused-vars': ['error', { args: 'after-used', argsIgnorePattern: '^_', caughtErrors: 'none', ignoreRestSiblings: true }],
  'no-unreachable': 'error',
  'no-dupe-keys': 'error',
  'no-dupe-args': 'error',
  'no-dupe-class-members': 'error',
  'no-dupe-else-if': 'error',
  'no-duplicate-case': 'error',
  'no-const-assign': 'error',
  'no-func-assign': 'error',
  'no-import-assign': 'error',
  'no-self-assign': 'error',
  'no-self-compare': 'error',
  'no-unsafe-finally': 'error',
  'no-unsafe-negation': 'error',
  'no-unsafe-optional-chaining': 'error',
  'no-unused-private-class-members': 'error',
  'no-useless-catch': 'error',
  'no-fallthrough': 'error',
  'no-cond-assign': 'error',
  'no-constant-binary-expression': 'error',
  'no-loss-of-precision': 'error',
  'no-redeclare': 'error',
  'no-shadow-restricted-names': 'error',
  'no-sparse-arrays': 'error',
  'no-template-curly-in-string': 'error',
  'no-var': 'error',
  'prefer-const': 'error',
  'use-isnan': 'error',
  'valid-typeof': 'error',
  eqeqeq: ['error', 'always', { null: 'ignore' }],
  'no-async-promise-executor': 'error',
  'no-compare-neg-zero': 'error',
  'no-ex-assign': 'error',
  'no-invalid-regexp': 'error',
  'no-irregular-whitespace': 'error',
  'no-prototype-builtins': 'error',
  'getter-return': 'error',
  'for-direction': 'error',
  'require-yield': 'error',
}

export default [
  {
    // Generated or not ours: client/client.js is built from client/src, python/ is the gateway,
    // .wrangler/ is Wrangler's local state.
    ignores: ['client/client.js', 'python/**', 'feedback-worker/.wrangler/**', 'node_modules/**'],
  },
  {
    files: ['**/*.js', '**/*.mjs'],
    languageOptions: { ecmaVersion: 'latest', sourceType: 'module', globals: nodeGlobals },
    linterOptions: { reportUnusedDisableDirectives: 'error' },
    rules,
  },
  {
    // The settings page: CommonJS modules joined into one browser script (client/README.md).
    files: ['client/src/**/*.cjs'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'commonjs',
      globals: { ...browserGlobals, require: 'readonly', module: 'writable', exports: 'writable' },
    },
    linterOptions: { reportUnusedDisableDirectives: 'error' },
    rules,
  },
  {
    // Page preview, browser half: a classic script, not a module.
    files: ['scripts/preview/page.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'script',
      globals: browserGlobals, // React and ReactDOM come from the file's own /* global */ comment
    },
  },
  {
    files: ['feedback-worker/worker.js'],
    languageOptions: { globals: workerGlobals },
  },
]
