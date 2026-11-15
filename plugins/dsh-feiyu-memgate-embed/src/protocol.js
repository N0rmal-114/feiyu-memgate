// Names shared by the parent (src/engine.js) and the worker process (src/worker.js). Kept apart so
// the parent never imports the worker module, which starts working as soon as it is loaded.

/** The environment variable that hands the worker its bearer key; the worker deletes it at once. */
export const KEY_ENV = 'FEIYU_MEMGATE_EMBED_KEY'

/** Worker exit codes. */
export const EXIT = Object.freeze({ other: 1, options: 2, load: 3, port: 4, runtime: 5 })

/** Text embedded once after every start, before the worker counts as ready. */
export const SELF_TEST_TEXT = '自检 self-test'
