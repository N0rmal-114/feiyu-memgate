// Stand-in for the host-provided @deepseek-ai/dsh-llm peer. meow-memory only uses these two
// helpers to build the chat messages it adds to the prompt; the storage/retrieval path never calls them.
export function createUserMessage(arg) { return { role: "user", ...arg }; }
export function boundContextSummary(text) { return String(text); }
