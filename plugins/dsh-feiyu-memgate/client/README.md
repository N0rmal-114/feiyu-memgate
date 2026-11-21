# The settings page (browser half)

`client/client.js` is the browser half of dsh-feiyu-memgate: the settings page of the plugin's row on the
Plugins page. It is a **generated file**. The source is the set of small modules under `client/src/`;
`npm run build-client` joins them into `client/client.js`. Edit the modules, never the generated file.

## What the host loads

The host serves a package's `./client` export to the page as one script in the client module system's
lazy-CJS form: it registers the package name and a `factory(require)` with the page's module loader, and
`require` answers only the page's shared module table (`docs/cookbook/adding-a-settings-card.md`
section 5; `packages/client/modules/README.md` "What the browser loads" and "Sharing modules"). So the
script must be one file, written as plain JavaScript (React elements are built with `createElement`, no
JSX), and the only modules it may ask for are `react` and `@deepseek-ai/dsh-client-ui-primitives`, both in
the shared table.

## How the file is assembled

`scripts/build-client.mjs` reads every `*.cjs` and `*.css` file under `client/src` (sorted by path, line
endings turned into LF) and writes `client/client.js`: a header comment, the
`window.__ModuleLoader__.load({ id: 'dsh-feiyu-memgate', factory })` call, a small registry (`define`,
`load` with a cache, a `require` per module that resolves `./x` and `../x` against the requiring module's
path and passes every other name to the host's `require`), one `define` per file with its text indented,
and `return load('index.cjs')`. Nothing is transpiled and nothing is minified; the output does not depend
on the platform, the date or the path of the checkout.

A `.css` file becomes a module that exports its text as a string.

Because the text of a module is indented, a module must not contain a template literal that spans lines.

## The modules

Ids are paths relative to `client/src`.

| module | holds |
|---|---|
| `index.cjs` | the entry point: `name`, `inject`, `apply(ctx)` and the `internals` export for tests |
| `constants.cjs` | the settings namespace, the slot key, the three routes, the poll intervals |
| `fields.cjs` | **generated** by `npm run sync-client` from `src/config.js`: `FIELDS` and `INDEX` |
| `i18n.cjs` | the `UI` dictionary (en, zh) and `dictionaries()` |
| `react.cjs` | the one place that asks the host for React: `{ React, h, useEffect, useRef, useState }` |
| `host-ui.cjs` | the one place that asks for the host's UI primitives; `{}` when the host has none |
| `net.cjs` | `fetchStatus`, `fetchChangelog`, `fetchModels`, `postModels`, over one `getJson` / `postJson` pair |
| `model/form.cjs` | the settings form as data: `at`, `checkText`, `fieldView`, `buildOps`, `saveDraft` |
| `model/status.cjs` | the status block as text rows: `stateText`, `statusRows` |
| `model/models.cjs` | the local model list as data: `pickText`, `formatSize`, `memoryText`, `modelActions`, `chainLine`, `sameProblem` |
| `model/markdown.cjs` | the changelog Markdown subset, text in and React elements out: `inlineMarkdown`, `joinLines`, `renderMarkdown`, `entryDate` |
| `hooks.cjs` | `useStatus`, `useChangelog` |
| `view/legacy.cjs` | everything visual, as first written: styles, components, `SettingsPage` (to be replaced by the redesigned view) |

## The page contract

The Plugins page renders entries of its keyed slot `plugins.row.config` under the key
`<package name>#<row id>` and passes `view` (`'summary'` | `'page'`) and `form` (`{ state, mutate }`) for
the row's settings namespace (`packages/client/ui-plugin-manager/src/client/slot-contract.ts:160-167`,
`:190-195`; `PluginManagerPage.tsx:1568-1572`, `:714`). `view/legacy.cjs` exports `SettingsPage`, which
`index.cjs` registers in that slot; `apply(ctx)` also registers the dictionaries.

The page follows the host's settings form conventions (`packages/client/ui-primitives/src/settings-form`):
edits are staged, only Save writes, all staged edits go in one
`form.mutate(ops, revision)` with the revision they were made against, a changed field shows a tag and a
reset that stages a return to the inherited value, and a secret is write-only (an empty draft keeps the
stored value). The host primitives are used when this host exports them; otherwise plain elements with the
host's style variables stand in.

Status. The status block reads `GET feiyu-memgate/status` (document-relative, like the first-party pages'
host routes), which the host half serves to the owner only. It shows no secret, address or memory: secrets
arrive as booleans.

Changelog. The last section of the page reads `GET feiyu-memgate/changelog` (owner only, like the status):
the released entries of the package's `CHANGELOG.md`. Their Markdown is drawn by a small subset
(`model/markdown.cjs`) that builds React elements from text (headings, lists, paragraphs, code, bold,
https links), so no HTML from the file can reach the page.

The host half never depends on this page: without it every setting still works, through `/memgate` and
the profile's patch file.

## Rules for the modules

- One responsibility per file. Each file starts with `'use strict'` and a header comment that says what it
  is responsible for and what it must not do.
- A module gets another module with `require('./x.cjs')` or `require('../x.cjs')`: a relative path with the
  `.cjs` extension. Host modules are reached only through `react.cjs` and `host-ui.cjs`; no other module
  requires `react` or the primitives.
- `model/*` never touches React or the DOM and requires neither `react.cjs` nor `host-ui.cjs`. The one
  exception is `model/markdown.cjs`, which builds React elements through `react.cjs` and says so.
- One `module.exports = { ... }` at the end of a file, with the names in the order they are defined.
- No `import` or `export` syntax, no JSX, no dependency that is not in the host's shared module table.
- `fields.cjs` is generated; change the settings in `src/config.js` instead.
- Keep a comment with the code it explains.

## Commands

Run them in `plugins/dsh-feiyu-memgate`.

| command | what it does |
|---|---|
| `npm run build-client` | assemble `client/client.js` from `client/src`; rewrite it when it differs |
| `npm run build-client -- --check` | exit 1 when `client/client.js` is stale; change nothing |
| `npm run sync-client` | write `client/src/fields.cjs` from `src/config.js`, then do `build-client` |
| `npm run sync-client -- --check` | exit 1 when `fields.cjs` or `client/client.js` is stale (CI runs this) |

`tests/client-page.test.js` loads the assembled file and fails when either generated file is stale;
`tests/client-build.test.js` tests the registry; `tests/client-modules.test.js` loads the pure modules on
their own and checks the import rules above.
