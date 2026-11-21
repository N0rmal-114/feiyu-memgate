// Shows the settings page in a browser without a DSH: a small local server that plays the host.
//
//   node scripts/preview-client.mjs --react <folder> [--port 47931]
//
// <folder> is a node_modules folder that holds react and react-dom 18 (their umd builds are served
// to the page; the plugin itself has no dependency on them, the host brings React). Then open
// http://127.0.0.1:47931/ and pick a scenario, a theme, a language and a column width in the bar on
// top. The page script is assembled from client/src on every request, so a reload shows an edit.
// Add &tab=advanced&open=all (or another tab's id) to the address to land on a tab with its groups open.
//
// What is real: the page script, its stylesheet, the drawings of client/art, the changelog (read
// from CHANGELOG.md like the host half does). What is made up: the status and the model list
// (scripts/preview/scenarios.mjs), the settings form (kept in the page), and the host's style
// variables (scripts/preview/host-tokens.css, copied values). So the preview shows layout, texts,
// states and both themes; it does not show the page inside the real Plugins page. The feedback
// receiver is made up as well (POST /preview-feedback answers without reading what was posted).
//
// The server listens on 127.0.0.1 only and serves nothing but the files named here.
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { bundle, collect } from './build-client.mjs'
import { releasedEntries } from '../src/changelog.js'
import { SCENARIOS, USER_SETTINGS, modelsFor } from './preview/scenarios.mjs'

const here = (relative) => fileURLToPath(new URL(relative, import.meta.url))
const argument = (name, fallback) => {
  const at = process.argv.indexOf('--' + name)
  return at > 0 && process.argv[at + 1] ? process.argv[at + 1] : fallback
}

const PORT = Number(argument('port', '47931'))
const REACT_DIR = argument('react', '')
const ART = /^\/feiyu-memgate\/art\/(mascot-(?:ready|busy|asleep|trouble|nag)\.webp)$/

function reactFile(pkg, file) {
  if (!REACT_DIR) throw new Error('start the preview with --react <a node_modules folder with react and react-dom 18>')
  return fs.readFileSync(path.join(REACT_DIR, pkg, 'umd', file), 'utf8')
}

const HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dsh-feiyu-memgate settings page (preview)</title>
<link rel="stylesheet" href="host-tokens.css">
</head>
<body>
<div class="preview-bar"><strong>Preview</strong></div>
<div class="host-scroll"><div class="host-column">
  <div class="host-head"><div class="host-icon"></div><h3>feiyu-memgate</h3><p>The row's title and description, drawn by the host.</p></div>
  <div class="host-sections"><div id="slot" style="display: contents"></div></div>
</div></div>
<script src="react.js"></script>
<script src="react-dom.js"></script>
<script>
window.PREVIEW_SCENARIOS = ${JSON.stringify(Object.entries(SCENARIOS).map(([name, value]) => [name, value.label]))}
window.PREVIEW_USER_SETTINGS = ${JSON.stringify(USER_SETTINGS)}
window.__ModuleLoader__ = { load: (record) => { window.PREVIEW_RECORD = record } }
</script>
<script src="client.js"></script>
<script src="page.js"></script>
</body>
</html>
`

function changelog() {
  const version = JSON.parse(fs.readFileSync(here('../package.json'), 'utf8')).version
  try {
    return { version, entries: releasedEntries(fs.readFileSync(here('../CHANGELOG.md'), 'utf8'), version) }
  } catch {
    return { version, entries: [] }
  }
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1')
  const send = (code, type, body) => {
    res.writeHead(code, { 'content-type': type, 'cache-control': 'no-store' })
    res.end(body)
  }
  const json = (code, value) => send(code, 'application/json; charset=utf-8', JSON.stringify(value))
  try {
    const scenario = SCENARIOS[url.searchParams.get('scenario')] ? url.searchParams.get('scenario') : 'ready-local'
    const art = ART.exec(url.pathname)
    if (url.pathname === '/') return send(200, 'text/html; charset=utf-8', HTML)
    if (url.pathname === '/client.js') return send(200, 'text/javascript; charset=utf-8', bundle(collect()))
    if (url.pathname === '/page.js') return send(200, 'text/javascript; charset=utf-8', fs.readFileSync(here('./preview/page.js')))
    if (url.pathname === '/host-tokens.css') return send(200, 'text/css; charset=utf-8', fs.readFileSync(here('./preview/host-tokens.css')))
    if (url.pathname === '/react.js') return send(200, 'text/javascript; charset=utf-8', reactFile('react', 'react.development.js'))
    if (url.pathname === '/react-dom.js') return send(200, 'text/javascript; charset=utf-8', reactFile('react-dom', 'react-dom.development.js'))
    if (art) return send(200, 'image/webp', fs.readFileSync(here('../client/art/' + art[1])))
    if (url.pathname === '/feiyu-memgate/status') {
      const status = SCENARIOS[scenario].status
      return typeof status === 'number' ? json(status, { code: 'preview' }) : json(200, status)
    }
    if (url.pathname === '/feiyu-memgate/models') {
      // A click changes nothing here: the answer says so the way the host half refuses a click.
      if (req.method === 'POST') {
        return json(200, {
          result: { ok: false, code: 'preview', problem: { code: 'preview', title: { en: 'This is the preview.', zh: '这是预览。' }, todo: { en: 'Nothing is installed or downloaded here.', zh: '这里不会安装或下载任何东西。' } } },
          view: modelsFor(scenario),
        })
      }
      return json(200, modelsFor(scenario))
    }
    if (url.pathname === '/feiyu-memgate/changelog') return json(200, changelog())
    // A stand-in for the feedback receiver: it reads nothing of what is posted and files nothing.
    if (url.pathname === '/preview-feedback') {
      req.resume()
      const mode = url.searchParams.get('mode')
      const reply = () => (mode === 'fails' ? json(502, { ok: false, code: 'upstream' }) : mode === 'busy' ? json(429, { ok: false, code: 'rate-limited' }) : json(200, { ok: true }))
      return void setTimeout(reply, 600)
    }
    send(404, 'text/plain; charset=utf-8', 'not found')
  } catch (error) {
    send(500, 'text/plain; charset=utf-8', String(error && error.message))
  }
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`settings page preview: http://127.0.0.1:${PORT}/`)
})
