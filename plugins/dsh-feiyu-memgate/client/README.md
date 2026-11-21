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
JSX), and the only module it asks for is `react`. The page does not use the host's UI primitives: its
controls are its own (`kit/`), and its stylesheet is `theme/page.css`.

## How the file is assembled

`scripts/build-client.mjs` reads every `*.cjs` and `*.css` file under `client/src` (sorted by path, line
endings turned into LF) and writes `client/client.js`: a header comment, the
`window.__ModuleLoader__.load({ id: 'dsh-feiyu-memgate', factory })` call, a small registry (`define`,
`load` with a cache, a `require` per module that resolves `./x` and `../x` against the requiring module's
path and passes every other name to the host's `require`), one `define` per file with its text indented,
and `return load('index.cjs')`. Nothing is transpiled and nothing is minified; the output does not depend
on the platform, the date or the path of the checkout.

A `.css` file becomes a module that exports its text as a string (`theme/styles.cjs` puts it into the
document).

Because the text of a module is indented, a module must not contain a template literal that spans lines.

## The modules

Ids are paths relative to `client/src`.

**Wiring**

| module | holds |
|---|---|
| `index.cjs` | the entry point: `name`, `inject`, `apply(ctx)` (registers the dictionaries, the stylesheet and the page in the row slot) and the `internals` export for tests and the preview |
| `react.cjs` | the one place that asks the host for React: `{ React, h, useEffect, useRef, useState }` |

**Tables**

| module | holds |
|---|---|
| `constants.cjs` | the settings namespace, the slot key, the routes, the poll intervals, the element id prefix, the public links (among them GitHub's new-issue page) and the address of the project's feedback receiver (`FEEDBACK_ENDPOINT`, empty while there is none) |
| `fields.cjs` | **generated** by `npm run sync-client` from `src/config.js`: `FIELDS` and `INDEX` |
| `layout.cjs` | where each setting sits: `TABS` → sections → setting keys, and `placedKeys()` |
| `i18n.cjs` | the `UI` dictionary (en, zh) and `dictionaries()`, which adds the texts of the field table |
| `net.cjs` | `fetchStatus`, `fetchChangelog`, `fetchModels`, `postModels`, over one `getJson` / `postJson` pair; and `sendFeedback`, the one call that leaves the host |
| `clipboard.cjs` | `copyText`: the one place that touches the clipboard, and says whether the text arrived |

**Logic, `model/`** (pure: no React, no DOM)

| module | holds |
|---|---|
| `model/form.cjs` | the settings form as data: `at`, `checkText`, `fieldView`, `buildOps`, `saveDraft` |
| `model/page.cjs` | the settings arranged for drawing: `splitOption`, `isMet`, `sectionView`, `pageView`, `savable` |
| `model/overview.cjs` | the summary at the top of the page: `summarise` (mood, headline, line, facts), `formatCount`, `MOODS` |
| `model/status.cjs` | the status block as text rows: `stateText`, `statusRows` |
| `model/models.cjs` | the local model list as data: `pickText`, `formatSize`, `memoryText`, `modelActions`, `chainLine`, `sameProblem` |
| `model/changelog.cjs` | the changelog answer as a list of releases: `entryDate`, `releases` |
| `model/feedback.cjs` | a feedback report and the address that hands it to GitHub: `KINDS`, `environment`, `safe`, `titleOf`, `bodyOf`, `compose`, `reportText`, with the limits `MAX_TEXT` and `MAX_URL`; and for the receiver `payload`, `usableEndpoint`. Also loaded by the host half (see below) |

**Hooks**

| module | holds |
|---|---|
| `hooks.cjs` | `useStatus` (polled), `useChangelog` (read once), `useModels` (polled, and the clicks of the model list), `useCopy` (the clipboard, and what became of the last copy), `useSend` (a report posted to the receiver, and what became of it); they own the timers and the "still mounted" flag |

**Theme, `theme/`**

| module | holds |
|---|---|
| `theme/page.css` | the stylesheet of the page, with the one block that maps the host's style variables to the page's own |
| `theme/styles.cjs` | `installStyles(document)` (adds one `<style>` with the stylesheet, returns the function that removes it), `STYLE_ID`, `cx` (joins class names) |

**Controls and parts, `kit/`** (plain functions of props, no state of their own)

| module | holds |
|---|---|
| `kit/controls.cjs` | `Button`, `Tag`, `Switch`, `TextInput`, `TextArea`, `Stepper`, `Choices` and `stepText` |
| `kit/layout.cjs` | `Tabs`, `TabPanel`, `Reveal`, `Fold`, `Group`, `Facts`, `Notice`, `Progress` |
| `kit/icons.cjs` | the few line icons: `Icon`, `ICON_NAMES` |

**Views, `view/`** (what is drawn and what the page keeps in state)

| module | holds |
|---|---|
| `view/page.cjs` | `SettingsPage` (the slot entry), `SettingsBody` (summary, tabs, panel, save bar, footer; keeps the tab, the staged edits, the phase of a save, the open groups and the feedback being written), `SaveBar`, `Footer` |
| `view/hero.cjs` | `Hero`: the headline, the line and the facts of the summary, with the mascot's drawing |
| `view/settings-tab.cjs` | `SettingsTab`: the sections of one settings tab, sub-rows inside a `Reveal`, folding groups in the Advanced tab |
| `view/field-row.cjs` | `FieldRow`: one setting as one row, with the control that fits its kind; `fieldId` |
| `view/tabs.cjs` | `OverviewTab` and `ChangelogTab`, two of the tabs without settings |
| `view/feedback.cjs` | `FeedbackTab`, the third, and `Nag` (the fifth drawing, `mascot-nag.webp`, with its speech bubble, beside the title; none if it cannot be loaded): what it is about, the text, the environment lines, Send, the link out to GitHub and the copy button; `emptyFeedback` |
| `view/models.cjs` | `ModelsPanel` and `ModelRow`: the local embedding models and their one button |
| `view/markdown.cjs` | the changelog Markdown subset, text in and React elements out: `inlineMarkdown`, `joinLines`, `renderMarkdown` |

## The page

The Plugins page renders entries of its keyed slot `plugins.row.config` under the key
`<package name>#<row id>` and passes `view` (`'summary'` | `'page'`) and `form` (`{ state, mutate }`) for
the row's settings namespace (`packages/client/ui-plugin-manager/src/client/slot-contract.ts:160-167`,
`:190-195`; `PluginManagerPage.tsx:1568-1572`, `:714`). `view/page.cjs` exports `SettingsPage`, which
`index.cjs` registers in that slot; `apply(ctx)` also registers the dictionaries and the stylesheet.

`SettingsBody` draws, in order: the `Hero` (the state in one headline, one line and a few facts, with a
mood that picks one of four drawings of the mascot), the `Tabs` (overview, features, retrieval, advanced,
changelog, feedback), the one `TabPanel` of the selected tab, the `SaveBar` (only while edits are staged, or just
after a save) and the `Footer`. The page keeps its shape whatever the settings form is doing: while the
form is loading, unavailable or read-only, the summary, the model list, the changelog and the feedback tab
still work, and a settings tab says what is the matter instead of showing rows nobody can use.

The settings form follows the host's conventions: edits are staged, only Save writes, all staged edits go
in one `form.mutate(ops, revision)` with the revision they were made against, a changed setting shows a
tag and a reset that stages a return to the inherited value, and a secret is write-only (an empty draft
keeps the stored value). A setting with `needs` (see `src/config.js`) is drawn as a sub-row inside a
`Reveal` that is open while the setting it needs has a fitting value, judged by the value on screen, a
staged edit included. The Advanced tab's sections are folds, closed by default.

Status. The summary and the Overview tab read `GET feiyu-memgate/status` (document-relative, like the
first-party pages' host routes), which the host half serves to the owner only. It shows no secret, address
or memory: secrets arrive as booleans.

Changelog. The Changelog tab reads `GET feiyu-memgate/changelog` (owner only, like the status): the released
entries of the package's `CHANGELOG.md`. Their Markdown is drawn by a small subset (`view/markdown.cjs`)
that builds React elements from text (headings, lists, paragraphs, code, bold, https links), so no HTML
from the file can reach the page.

Feedback. The Feedback tab writes a report and either sends it to the project's receiver (Send, below) or hands it to the person's browser.
`model/feedback.cjs` builds the address of GitHub's new-issue page with the title and the text as query
parameters, and the button is a plain link to it (`target="_blank"`), so the report exists only once the
person submits it on GitHub under their own account. What is attached besides their own words is decided in
one function, `environment(status)`: version numbers and state names, each of which has to look like one
(letters, digits, dots, dashes, at most 40 characters) or becomes a question mark, so a path, an address or
a file name cannot get in whatever a later status holds. The same lines are listed on the tab under the
switch that attaches them. An address is kept under `MAX_URL` characters: a longer report is cut in the
address only, the cut is marked, and the link copies the whole report to the clipboard on the way out.
The shipped package names a receiver (`FEEDBACK_ENDPOINT`, checked by `usableEndpoint`: https, or this
computer for the preview; an empty value switches Send off and leaves the link and the copy button), so the tab is "write, then Send": one Send button, and the link
to GitHub and the copy button are shown only inside the notice of a Send that failed. Send is the only call of the
page that leaves the host: `net.cjs` `sendFeedback` posts `payload()` with `credentials: 'omit'` and no
referrer, waits `FEEDBACK_TIMEOUT_MS`, and turns every failure into a code the tab shows. The receiver is
`feedback-worker/worker.js` (a Cloudflare Worker the maintainers run, not shipped in the package); its
`validate()` and this module's `payload()` describe the same shape and are tested against each other.
The host half loads the same module for `/memgate feedback` (`src/commands.js` imports
`client/src/model/feedback.cjs`), which is why that module and `constants.cjs` must stay plain CommonJS
that runs in Node without a browser.

The mascot. The drawings are the files of `client/art/`: four moods (`mascot-<mood>.webp`) and a fifth,
`mascot-nag.webp`, for the Feedback tab. The host half serves them from its art route
(`GET feiyu-memgate/art/<file>`, `src/plugin.js`); the summary loads the one that fits the mood, the
Feedback tab shows the fifth beside its title with a speech bubble over it (the bubble is text of the page,
so it follows the language), and like the others the tab simply has no drawing, and no bubble, if it cannot
be loaded. Nothing a person needs is only in the picture.

The host half never depends on this page: without it every setting still works, through `/memgate` and
the profile's patch file.

## Rules for the modules

- One responsibility per file. Each file starts with `'use strict'` and a header comment that says what it
  is responsible for and what it must not do.
- A module gets another module with `require('./x.cjs')` or `require('../x.cjs')`: a relative path with the
  `.cjs` extension. Only `react.cjs` requires `react`; no other module asks the host for anything.
- One `module.exports = { ... }` at the end of a file, with the names in the order they are defined.
- No `import` or `export` syntax, no JSX, no dependency that is not in the host's shared module table, no
  template literal that spans lines.
- `fields.cjs` is generated; change the settings in `src/config.js` instead.
- Keep a comment with the code it explains.

### Layers

The require graph has no cycle, and each layer reaches only what is listed (`tests/client-modules.test.js`
checks every line of this):

| layer | may require |
|---|---|
| `constants.cjs`, `fields.cjs`, `layout.cjs`, `react.cjs`, `clipboard.cjs` | nothing of the page |
| `i18n.cjs` | `fields.cjs` |
| `net.cjs` | `constants.cjs` |
| `hooks.cjs` | `react.cjs`, `constants.cjs`, `net.cjs`, `clipboard.cjs` (no view, kit or theme) |
| `model/*` | other `model/*` files, `fields.cjs`, `layout.cjs`, `constants.cjs` (no React, kit, view, theme or hooks) |
| `theme/*` | `theme/*` |
| `kit/*` | `kit/*`, `react.cjs`, `theme/styles.cjs` |
| `view/*` | anything but `index.cjs` |
| `index.cjs` | anything; it is the only module that requires `view/page.cjs`, and nothing requires it |

### Design conventions

- **Tokens.** The host switches its style variables between light and dark by itself. The page reads them
  through one mapping block at the top of `theme/page.css` (`.fmg-root`): `--fmg-*` variables, each with a
  fallback for a host that does not define it. Every other rule uses `--fmg-*` only, and the host's
  variable names (`--dsw-*`, `--dsh-*`, `--ds-*`) appear nowhere else.
- **No theme branch.** There is no light rule and no dark rule; the page's own colours are mixed from the
  host's.
- **Class names** all start with `fmg-`; modifiers are `--name`. No rule reaches outside `.fmg-root`.
  Every class a module uses is defined in the stylesheet and every class the stylesheet defines is used
  (both are tested).
- **State is carried by ARIA and data attributes** the components set anyway (`aria-selected`,
  `aria-checked`, `aria-expanded`, `aria-invalid`, `data-open`), not by extra classes. Controls are native
  elements with ARIA roles: a switch is a `button` with `role="switch"`, a list of choices a radio group,
  the tabs follow the tabs pattern with arrow keys.
- **No inline styles.** The one exception is the width of a progress bar's inner element.
- Every element id starts with `feiyu-memgate-` so that none can meet an id of the host's page.

## How to add a setting

1. Add it to `FIELDS` in `src/config.js` (key, kind, default, range, label and help in both languages;
   `needs`, `unit` or `experimental` when they apply).
2. Add its key to a section in `layout.cjs`. A setting with `needs` goes in the same section as the setting
   it needs, after it.
3. Run `npm run sync-client`. It rewrites `client/src/fields.cjs` and `client/client.js`.

`tests/client-modules.test.js` fails when a setting is not placed, is placed twice or sits before the
setting it needs; the page needs no other change.

## How to preview

```bash
node scripts/preview-client.mjs --react <a node_modules folder with react and react-dom 18> [--port 47931]
```

Run it in `plugins/dsh-feiyu-memgate`, then open `http://127.0.0.1:47931/` and pick a scenario, a theme, a
language, the state of the settings form, a column width and how feedback is received in the bar on top.
The page script is assembled from `client/src` on every request, so a reload shows an edit.

The Feedback choice decides where Send goes. "As shipped" leaves it to `FEEDBACK_ENDPOINT`: with an address
there, Send really sends a report to the project's receiver. "No receiver" hides Send. The three made-up
receivers (takes it, fails, is busy) are a route of the preview server that reads nothing of what is posted.

What is real: the page script, its stylesheet, the drawings of `client/art`, the changelog (read from
`CHANGELOG.md` like the host half does). What is made up: the status and the model list
(`scripts/preview/scenarios.mjs`), the settings form (kept in the page), and the host's style variables
(`scripts/preview/host-tokens.css`, copied values). So the preview shows layout, texts, states and both
themes; it does not show the page inside the real Plugins page. The server listens on `127.0.0.1` only and
serves nothing but the files named in the script. `tests/client-preview.test.js` checks that every made-up
scenario can be drawn by the page; it does not start the server.

## Commands

Run them in `plugins/dsh-feiyu-memgate`.

| command | what it does |
|---|---|
| `npm run build-client` | assemble `client/client.js` from `client/src`; rewrite it when it differs |
| `npm run build-client -- --check` | exit 1 when `client/client.js` is stale; change nothing |
| `npm run sync-client` | write `client/src/fields.cjs` from `src/config.js`, then do `build-client` |
| `npm run sync-client -- --check` | exit 1 when `fields.cjs` or `client/client.js` is stale (CI runs this) |

## Tests

| file | what it checks |
|---|---|
| `tests/client-build.test.js` | the registry and the assembly |
| `tests/client-modules.test.js` | the pure modules on their own, the rules above (headers, imports, layers, the layout table), the stylesheet against the class names the modules use, the dictionary against the keys they ask for |
| `tests/client-page.test.js` | the assembled file: what it asks of the host, `apply`, and the page drawn with a small stand-in for React (`tests/helpers/client-harness.js`); fails when either generated file is stale |
| `tests/client-preview.test.js` | the preview's made-up answers against the page |
