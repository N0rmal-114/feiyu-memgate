// A stand-in host process for the orphan test: it starts a managed gateway, prints the gateway's
// process id once it is healthy, then waits to be killed. The test kills this process hard (no
// cleanup code runs) and checks that the gateway goes away by itself.
import { readConfig } from '../../src/config.js'
import { createGatewayManager } from '../../src/gateway.js'

const dataDir = process.argv[2]
const python = process.argv[3] ?? ''
const manager = createGatewayManager({ settings: readConfig({ gateway: { dataDir, python } }), logger: { warn: () => {} } })
manager.start()
const state = await manager.ready()
process.stdout.write(JSON.stringify({ state, pid: manager.pid() }) + '\n')
setInterval(() => {}, 60000)
