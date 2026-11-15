// The tokens a text is embedded as, and how an over-long text is cut.
//
// The worker builds the whole token sequence itself (beginning token, the text's tokens, end token)
// and hands it to node-llama-cpp's getEmbeddingFor, which then adds nothing: it adds a beginning or
// end token only when the sequence does not already start or end with it
// (dist/evaluator/LlamaEmbeddingContext.js, _prepareInput). Two reasons:
//
// 1. node-llama-cpp 3.22.3 puts no beginning token before input of a SentencePiece unigram ("ugm")
//    vocabulary (dist/utils/tokenizerUtils.js, resolveBeginningTokenToPrepend), which suits T5. The
//    XLM-RoBERTa models (bge-m3) use that vocabulary too, but their GGUF asks for <s> first
//    (tokenizer.ggml.add_bos_token), llama.cpp's own tokenizer adds it, and so does Ollama. Without
//    it the CLS-pooled vector comes from the first word: measured against Ollama with the same file,
//    two tokens fewer and a cosine down to 0.43 on short text.
// 2. Truncation the way Ollama does it, which the memory gateway's calibration went through: the
//    head of the text is kept and the special tokens at both ends stay, so the sequence is exactly
//    `limit` tokens long. Measured against Ollama with one over-long text per model (both
//    /api/embed and /v1/embeddings truncate by default): this cut gives cosine 1.000000; keeping the
//    tail gives 0.87-0.95, dropping the end token 0.52-0.98, one token fewer 0.998-0.9996.
//
// The other vocabularies get the same beginning and end tokens node-llama-cpp would give them.

function resolveEnds(model) {
  const { vocabularyType: type, tokens } = model
  const id = (token) => (token === null || token === undefined || token === -1 ? null : token)
  if (type === 'rwkv') return { begin: null, end: null }
  if (type === 'wpm') return { begin: id(tokens.bos), end: id(tokens.sep) }
  if (type === 'ugm') return { begin: tokens.shouldPrependBosToken === true ? id(tokens.bos) : null, end: id(tokens.eos) }
  return {
    begin: tokens.shouldPrependBosToken === true ? id(tokens.bos) : null,
    end: tokens.shouldAppendEosToken === true ? id(tokens.eos) : null,
  }
}

/**
 * A function (text, limit) -> { tokens, count, truncated } for a loaded node-llama-cpp model.
 *   tokens     the sequence to embed, special tokens included, at most `limit` long
 *   count      the length of the whole sequence before any cut
 *   truncated  whether the text's tail was cut off
 * @param {{ vocabularyType: string, tokens: { bos: number | null, eos: number | null, sep: number | null,
 *           shouldPrependBosToken: boolean, shouldAppendEosToken: boolean },
 *           tokenize: (text: string, specialTokens?: boolean) => number[] }} model
 */
export function inputTokenizer(model) {
  const { begin, end } = resolveEnds(model)
  const fixed = (begin === null ? 0 : 1) + (end === null ? 0 : 1)
  return (text, limit = Infinity) => {
    // specialTokens false: text that looks like a special token is embedded as plain text
    let body = Array.from(model.tokenize(text, false))
    const count = body.length + fixed
    const truncated = count > limit
    if (truncated) body = body.slice(0, Math.max(0, limit - fixed))
    const tokens = []
    if (begin !== null) tokens.push(begin)
    for (const token of body) tokens.push(token)
    if (end !== null) tokens.push(end)
    return { tokens, count, truncated }
  }
}
