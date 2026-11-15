// Plugin entry for the DeepSeek Harness (DSH) host.
//
// Named exports only: the Loader drops name/inject/Config when a module also has a default export
// (docs/postmortem/0001-acp-default-export-drops-inject.md; entry contract in
// docs/user/develop/basic/index.md and docs/user/develop/basic/config.md).
import { buildConfigSchema } from './src/config.js'
import { applyAddon } from './src/plugin.js'

export const name = 'dsh-feiyu-memgate-embed'

// No required service: the add-on runs wherever the host runs plugins. The chat command needs the
// `commands` service, which exists only in interactive hosts; it is registered from an optional
// ctx.inject() child (src/plugin.js), so a host without it still gets the embedding service.
export const inject = []

// The host's schema library is a peer: imported at load time, so the module still loads without it
// (tests, tooling) and the add-on then runs with default settings.
let Schema
try {
  ;({ default: Schema } = await import('@deepseek-ai/schemastery'))
} catch {
  Schema = undefined
}

export const Config = Schema ? buildConfigSchema(Schema) : undefined

export function apply(ctx, config) {
  applyAddon(ctx, config)
}
