// Correctness-only lint, no style rules. ESLint 10 (flat config), run through npx with the exact version
// 10.15.0 and nothing installed for it: this file imports nothing and spells out the rules and
// the global names.

const nodeGlobals = Object.fromEntries([
  'process', 'Buffer', 'console', 'global', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval',
  'setImmediate', 'clearImmediate', 'queueMicrotask', 'structuredClone', 'URL', 'URLSearchParams',
  'TextEncoder', 'TextDecoder', 'AbortController', 'AbortSignal', 'fetch', 'Headers', 'Request', 'Response',
  'FormData', 'Blob', 'performance', 'EventTarget', 'Event', 'atob', 'btoa', 'crypto', 'DOMException',
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
    // Nothing generated or vendored is tracked here; model files (.gguf) and node_modules are not JavaScript.
    ignores: ['node_modules/**'],
  },
  {
    files: ['**/*.js', '**/*.mjs'],
    languageOptions: { ecmaVersion: 'latest', sourceType: 'module', globals: nodeGlobals },
    linterOptions: { reportUnusedDisableDirectives: 'error' },
    rules,
  },
]
