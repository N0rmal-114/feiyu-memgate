# The feedback receiver

The Feedback tab of the settings page can hand a report to GitHub's new-issue page, which needs a GitHub
account and makes the report public. This folder is the other way: a small Cloudflare Worker that takes a
report straight from the settings page and files it as an issue in a **private** repository, so a person
needs no account anywhere and only the maintainers read what they wrote.

The Worker is run by the maintainers of the project. It is not part of the published plugin package
(`files` in `package.json` does not list this folder); the package holds only the Worker's address
(`FEEDBACK_ENDPOINT` in `client/src/constants.cjs`). The tab posts a report to that address when a person presses Send; with an
empty address it has no Send button and the page calls nothing outside the host.

| file | what it is |
|---|---|
| `worker.js` | the Worker: one file, no dependency, no build step |
| `wrangler.toml` | its deployment settings, with the two rate limits; nothing secret |

## What it does with a report

`POST <address>` with a JSON body:

```json
{ "v": 1, "kind": "idea", "text": "…", "environment": [["plugin", "dsh-feiyu-memgate 0.3.2"]] }
```

| field | rule |
|---|---|
| `v` | `1` |
| `kind` | `idea`, `problem` or `other` |
| `text` | 1 to 4000 characters after tidying (line breaks unified, control characters removed) |
| `environment` | optional; at most 12 pairs of a lowercase name and a value made of letters, digits, spaces and `. _ + , > ? -` |

Any other field, shape or content type is refused, as is a body over 32 KB. A report that passes becomes one
issue: the title is `[idea]`, `[bug]` or `[feedback]` followed by the first line of the text; the body is
the text, the environment lines and the time it was received.

| answer | meaning |
|---|---|
| `200 { "ok": true }` | filed |
| `400 { "ok": false, "code": "invalid", "field": "…" }` | not a report in the shape above; `field` names the part, never its content |
| `413 too-large`, `415 not-json`, `405 method-not-allowed` | as they say |
| `429 rate-limited` | too many reports in the last minute; `retry-after: 60` |
| `502 upstream` | GitHub refused the issue or could not be reached (the Worker's log has the status) |
| `503 not-configured` | the token or the repository name is missing |

`GET <address>` answers `{ "ok": true, "service": "feiyu-memgate-feedback", "v": 1, "configured": true }`: a
way to see that the Worker is up and has its two settings without sending anything. While a setting is
absent or unusable the answer is `"configured": false` with `"missing"` naming it (`GITHUB_TOKEN`,
`FEEDBACK_REPO`). It cannot tell whether GitHub accepts the token; only a report shows that.

The answer never repeats what was sent and never says where it was filed. The Worker keeps nothing itself.
The sender's address is the key of the rate limit and is neither stored nor logged; the log holds only
GitHub's status code when filing failed.

## Setting it up

You need a Cloudflare account, Node.js, and a GitHub account that owns the private repository.

1. **The inbox.** Create a private repository on GitHub, for example `feedback-inbox`. Issues must be
   enabled in it (they are by default).
2. **The token.** On GitHub, create a fine-grained personal access token that can do one thing: repository
   access limited to the inbox repository only, and under the repository permissions "Issues" set to read
   and write. Give it an expiry date and note when to renew it.
3. **Deploy.** In this folder:

   ```sh
   npx wrangler login
   npx wrangler deploy
   npx wrangler secret put GITHUB_TOKEN
   npx wrangler secret put FEEDBACK_REPO
   ```

   Each `secret put` asks for the value: the token, then `owner/name` of the inbox (its address on
   github.com is understood as well). The rate limits of
   `wrangler.toml` need wrangler 4.36.0 or later. `deploy` prints the Worker's address.
4. **Check.** Open the address in a browser. It must say `"configured": true`. Then send one report by hand
   and look for the issue in the inbox. If the answer is `502 upstream`, run `npx wrangler tail`, send again
   and read the line `feedback: GitHub answered <status>`: 401 means GitHub does not know the token (copy it
   again), 403 that the token may not write issues, 404 that the repository name is wrong or the token has
   no access to it.

   ```sh
   curl -X POST <address> -H "content-type: application/json" --data "{\"v\":1,\"kind\":\"other\",\"text\":\"first test\"}"
   ```

5. **Tell the page.** Put the address into `FEEDBACK_ENDPOINT` in `client/src/constants.cjs`, run
   `npm run build-client` and the tests. The Send button appears on the Feedback tab from then on. Try it in
   the preview first (`scripts/preview-client.mjs`; with Feedback set to "as shipped" the page posts to the
   real address).

The Worker can also be created in the Cloudflare dashboard by pasting `worker.js` into the editor and
adding the two secrets under the Worker's settings. That way has no rate limits: the bindings are set
through `wrangler.toml` only.

An address under `workers.dev` is not reachable from every network. If people report that Send fails with
"network", give the Worker a route on a domain of your own in the Cloudflare dashboard and put that
address into `FEEDBACK_ENDPOINT`. Whatever happens to Send, the link to GitHub and the copy button stay on
the tab.

## Limits

- `wrangler.toml` allows 3 reports a minute from one sender and 30 a minute from everybody. Cloudflare
  counts per location and approximately, so these are a brake on a flood and not exact numbers.
- Anybody who reads the package can call the address. What stands between that and the inbox is the
  validation above, the rate limits, and the fact that the inbox is private: a junk report costs a
  maintainer a click, and nothing a stranger sends is published.
- Changing what a report may hold means changing `validate()` here and `payload()` in
  `client/src/model/feedback.cjs` together, and raising `v` on both sides if an old page must be told apart.

## Tests

`tests/feedback-worker.test.js` runs `handle()` in Node with stand-ins for the settings and for GitHub; it
runs with the plugin's other tests (`node --test`). It also checks that what the settings page posts is
what the Worker takes.
