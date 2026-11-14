// Host-core stand-in: re-exports only the helpers the Memory Spaces service and the Layered strategy's
// retrieval module use at runtime. The implementations are the real files copied from the root package.
export {
  record as memoryInputRecord, text as memoryInputText, integer as memoryInputInteger,
  stringArray as memoryInputStringArray, truncate as truncateMemoryText,
} from './sdk/input.ts'
export { withMemoryStorageLock } from './sdk/storage-lock.ts'
