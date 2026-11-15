// A small stand-in for the host's Cordis context: services (provide/set/get), events (on/emit),
// effects, ctx.inject for the optional `commands` service, and a logger that records every line.
export function createFakeCtx({ commands = true, language, subprocess, dshPath } = {}) {
  const services = new Map()
  const listeners = new Map()
  const emitted = []
  const serviceEvents = []
  const logs = []
  const effects = []
  const registry = new Map()

  function fire(name, ...args) {
    for (const fn of [...(listeners.get(name) ?? [])]) fn(...args)
  }

  if (language) services.set('settings', { describe: () => [{ ns: 'locale', value: { preference: language } }] })
  if (subprocess) services.set('subprocess', subprocess)
  if (dshPath) services.set('dshPath', dshPath)

  const ctx = {
    logger: () => ({
      warn: (message) => logs.push(['warn', String(message)]),
      info: (message) => logs.push(['info', String(message)]),
    }),
    get: (name) => services.get(name),
    provide(name, value) {
      if (services.has(name)) throw new Error(`service "${name}" has been registered`)
      services.set(name, value)
      serviceEvents.push([name, value])
      fire('internal/service', name, value)
      let done = false
      return () => {
        if (done) return
        done = true
        services.delete(name)
        serviceEvents.push([name, undefined])
        fire('internal/service', name, undefined)
      }
    },
    set(name, value) {
      if (!services.has(name)) throw new Error(`cannot set property "${name}" without provide`)
      services.set(name, value)
    },
    emit(name, ...args) {
      emitted.push([name, ...args])
      fire(name, ...args)
    },
    on(name, fn) {
      const list = listeners.get(name) ?? []
      list.push(fn)
      listeners.set(name, list)
      return () => listeners.set(name, (listeners.get(name) ?? []).filter((f) => f !== fn))
    },
    effect(fn) {
      const dispose = fn()
      effects.push(dispose)
      return dispose
    },
    inject(deps, callback) {
      const names = []
      const available = deps.every((d) => (d === 'commands' ? commands : services.has(d)))
      if (available) {
        const child = Object.create(ctx)
        child.commands = {
          register(definition) {
            if (registry.has(definition.name)) throw new Error(`command "${definition.name}" is already registered in this scope`)
            registry.set(definition.name, definition)
            names.push(definition.name)
            return () => registry.delete(definition.name)
          },
        }
        callback(child)
      }
      return {
        dispose() {
          for (const name of names) registry.delete(name)
        },
      }
    },
  }

  return {
    ctx,
    services,
    emitted,
    serviceEvents,
    logs,
    commands: registry,
    async disposeAll() {
      for (const dispose of effects.reverse()) await dispose?.()
    },
    /** Run a registered command the way the host's registry does. */
    async run(line) {
      const match = /^\/([a-z][a-z0-9_-]*)(?=$|[\t\n\r ])/u.exec(line)
      const definition = match && registry.get(match[1])
      if (!definition) throw new Error('no such command')
      return definition.handler({ rawInput: line.slice(match[0].length), signal: new AbortController().signal, attachments: [] })
    },
  }
}
