// Plugin entry for the DeepSeek Harness (DSH) host.
//
// Named exports only: the Loader drops name/inject/Config when a module also has a default export
// (docs/postmortem/0001-acp-default-export-drops-inject.md:110; entry contract in
// docs/user/develop/basic/index.md:87-103 and docs/user/develop/basic/config.md:23-45).
import { buildConfigSchema } from './src/config.js'
import { applyPlugin } from './src/plugin.js'

export const name = 'dsh-feiyu-memgate'

// Both services exist wherever agents run: the agent loop injects them itself
// (packages/core/agent-loop/src/index.ts:291). This Cordis version has required injections only
// (vendor/cordis/src/registry.ts:19, :106).
export const inject = ['tools', 'systemPrompt']

// The host's schema library is a peer: imported here, at load time, so the module still loads
// without it (tests, tooling) and the plugin then works with default settings.
let Schema
try {
  ;({ default: Schema } = await import('@deepseek-ai/schemastery'))
} catch {
  Schema = undefined
}

export const Config = Schema ? buildConfigSchema(Schema) : undefined

export function apply(ctx, config) {
  applyPlugin(ctx, config)
}
