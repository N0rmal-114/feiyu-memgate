"""Text helpers: normalising, near-duplicate detection for recall results, and the character grams (with their
IDF weights) the keyword backends score on.

Standard library only.
"""
import math
import unicodedata


class _Dropper(dict):
    """str.translate table: characters of category P, Z or S and whitespace are deleted, the rest kept.
    Each character is decided once and remembered, so normalising is a C-speed pass over the text."""

    def __missing__(self, codepoint):
        char = chr(codepoint)
        drop = unicodedata.category(char)[0] in "PZS" or char.isspace()
        self[codepoint] = None if drop else codepoint
        return self[codepoint]


_DROP = _Dropper()


def normalize(text):
    """NFKC, drop whitespace, punctuation and symbols (categories P, Z, S), lowercase."""
    return unicodedata.normalize("NFKC", text).translate(_DROP).lower()


def bigrams(text):
    return {text[i:i + 2] for i in range(len(text) - 1)}


def near_duplicate(a, b, overlap=0.8):
    """True when one text contains the other, or their character bigrams overlap enough."""
    a, b = normalize(a), normalize(b)
    if len(a) < 2 or len(b) < 2:
        return a == b
    if a in b or b in a:
        return True
    ga, gb = bigrams(a), bigrams(b)
    return len(ga & gb) / len(ga | gb) >= overlap


def is_han(char):
    """True for a CJK ideograph (a "content character": one such character already carries meaning,
    so a lone one is worth indexing; a lone Latin letter or digit is not)."""
    code = ord(char)
    return 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF  # unified ideographs and extension A


def index_grams(text, unigrams=True):
    """The grams the keyword backends index and score on, from an already normalised text: every
    character bigram, plus (when `unigrams`) every single CJK ideograph. The unigrams let a paraphrase
    that shares one meaningful character with a memory, but no whole pair, still count for something."""
    grams = {text[i:i + 2] for i in range(len(text) - 1)}
    if unigrams:
        grams.update(c for c in text if is_han(c))
    return grams


def idf_weight(df, n_docs):
    """How much a gram that is in `df` of `n_docs` memories says about relevance (always > 0).

    The BM25 inverse document frequency, ln(1 + (N - df + 0.5) / (df + 0.5)). A gram no memory
    holds yet (df 0) counts as held by one, so a typo or a new word does not dominate the query."""
    n = max(1, int(n_docs))
    df = min(max(1, int(df)), n)
    return math.log(1.0 + (n - df + 0.5) / (df + 0.5))


def gram_weights(norm, df_of, n_docs, unigrams=True, limit=None):
    """{gram: idf weight} for the grams of the normalised query `norm`. `df_of(gram)` gives the number
    of memories holding it (None or 0 when none does). `limit` keeps only that many grams (sorted, so
    the choice is the same every time)."""
    grams = sorted(index_grams(norm, unigrams))
    if limit is not None:
        grams = grams[:limit]
    return {g: idf_weight(df_of(g) or 0, n_docs) for g in grams}


class PreparedQuery:
    """A query normalised once: its text and character bigrams, for scoring many memories.

    Without `weights`, `score(text)` returns exactly what `bigram_similarity(query, text)` returns.

    With `weights` ({gram: weight > 0}, see gram_weights) it is the IDF-weighted containment:
    the weight of the query grams the text holds divided by the weight of all query grams, 0..1.
    A text holding every gram scores 1, adding a matching gram never lowers a score, and a gram
    that most memories hold counts for little next to one that few do. Only the grams named in
    `weights` take part (the others are ignored on both sides)."""

    __slots__ = ("norm", "grams", "weights", "total")

    def __init__(self, query, weights=None):
        self.norm = normalize(query)
        self.grams = bigrams(self.norm) if len(self.norm) >= 2 else frozenset()
        if weights is None:
            self.weights, self.total = None, 0.0
        else:
            self.weights = {g: float(w) for g, w in weights.items() if w > 0}
            self.total = math.fsum(self.weights.values())

    def score(self, text):
        t = normalize(text)
        if not self.norm or not t:
            return 0.0
        if len(self.norm) < 2:
            return 1.0 if self.norm in t else 0.0
        if self.weights is None:
            return len(self.grams & bigrams(t)) / len(self.grams)
        if not self.total:
            return 0.0
        held = index_grams(t)
        return min(1.0, math.fsum(w for g, w in self.weights.items() if g in held) / self.total)


def bigram_similarity(query, text):
    """How much of the query's character bigrams the text holds, 0..1.

    A model-free relevance score that works for Chinese without word segmentation.
    A one-character query matches when the text contains it. Scoring many texts against one
    query: build a PreparedQuery once and call its score(). (The keyword backends weight the
    grams by rarity instead: see PreparedQuery's `weights`.)
    """
    return PreparedQuery(query).score(text)
