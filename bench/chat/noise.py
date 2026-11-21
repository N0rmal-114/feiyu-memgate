"""Filters that cut unrelated memories from a lookup (experimental, not product code). Standard library only.

Imported by model_lab.py and session_lab.py the way they import their siblings. Everything the product would deliver
stays the baseline; these filters can only REMOVE memories from it (none raises a score or adds a candidate), so none
can make a quiet turn talk. One spec part, `noise:`, with options that are all off by default and may be combined:

    hub=<lambda> k=10   A memory that is close to many other memories is generic. For every memory m of a world,
                        h(m) = the mean, over its k most similar OTHER memories, of the similarity on the store's score
                        scale (scale.score of the cosine); ref = the median of h over the world. Before the score lines
                        the cosine of m to every query is lowered by lambda * max(0, h(m) - ref) * (ceil - floor) of the
                        scale (the lookup still receives cosines, and a cosine is never raised).
    top=<T>             The cosines of ALL memories of the world to the query (before any penalty) have a mean and a
                        population standard deviation; z(m) = (cos - mean) / sd (sd 0: z is 0 for all). When the largest
                        z is below T the lookup delivers nothing: nothing in the store stands out for this query.
    each=<Z>            Of what the lookup delivers (linked additions included) only memories with z >= Z are kept.
    lead=<ratio>        Sessions only. When the best memory of a conversation's main lookup (its first item, before the
                        memories already shown are taken out) is one the history already shows, a NEW memory of that
                        lookup is kept only if its score is at least ratio x that best score.
    cap=<n>             Sessions only. From the second turn of a conversation on, at most n new memories per turn from
                        the main lookup, the first n in the lookup's own order.

Order within a lookup: the hub penalty on the cosines, the product lookup, then top, then each; in sessions then the
skip of memories already shown, lead, cap. The functions here are pure over lists; `lookup` runs the first part on an
engine of model_lab (an object with `lookup(cosines)`, `ids`, `base.vectors` and `scale`).
"""
import math
import statistics

try:
    _dot = math.sumprod  # Python 3.12+
except AttributeError:  # pragma: no cover - Python 3.11
    def _dot(a, b):
        return sum(x * y for x, y in zip(a, b))

DEFAULTS = {"hub": None, "k": 10, "top": None, "each": None, "lead": None, "cap": None}
INTS = {"k", "cap"}
LOOKUP_KEYS = ("hub", "k", "top", "each")  # the options that act inside one lookup (the others act on a session)


def active(values):
    """Whether a parsed `noise` part has any filter switched on."""
    return bool(values) and any(values.get(name) is not None for name in ("hub", "top", "each", "lead", "cap"))


def check(values, sessions):
    """SystemExit when a parsed `noise` part holds a bad value; lead and cap need `sessions` (they act on conversations)."""
    for name, value in values.items():
        if value is not None and not math.isfinite(value):
            raise SystemExit("noise: %s must be a finite number" % name)
    if values["hub"] is not None and values["hub"] < 0:
        raise SystemExit("noise: hub must be at least 0")
    for name in ("k", "cap"):
        if values[name] is not None and values[name] < 1:
            raise SystemExit("noise: %s must be at least 1" % name)
    if values["lead"] is not None and not values["lead"] > 0:
        raise SystemExit("noise: lead must be above 0")
    if not sessions and (values["lead"] is not None or values["cap"] is not None):
        raise SystemExit("noise: lead and cap act on conversations and exist only in session_lab.py")


def label(values):
    """The canonical text of the options that are on (k only when hub is)."""
    return ",".join("%s=%s" % (name, values[name]) for name in DEFAULTS
                    if values[name] is not None and (name != "k" or values["hub"] is not None))


# ------------------------------------------------------------------ pure functions

def hub_penalties(vectors, scale, k):
    """The hub penalty of every memory in cosine units for lambda = 1: max(0, h - ref) * (ceil - floor), with h the mean
    score of the memory's k most similar other memories and ref the median of h (a world of one memory has none)."""
    n = len(vectors)
    heights = []
    for i in range(n):
        cosines = sorted((_dot(vectors[i], vectors[j]) for j in range(n) if j != i), reverse=True)[:k]
        heights.append(statistics.fmean(scale.score(c) for c in cosines) if cosines else 0.0)
    if not heights:
        return []
    ref = statistics.median(heights)
    return [max(0.0, h - ref) * (scale.ceil - scale.floor) for h in heights]


def zscores(cosines):
    """(cos - mean) / sd for every cosine, the population standard deviation; all 0 when sd is 0."""
    if not cosines:
        return []
    mean = math.fsum(cosines) / len(cosines)
    sd = math.sqrt(math.fsum((c - mean) ** 2 for c in cosines) / len(cosines))
    if sd == 0.0:
        return [0.0] * len(cosines)
    return [(c - mean) / sd for c in cosines]


def restrict(result, keep):
    """A lookup result with only the memories in `keep` (a set of ids) left, in the same order."""
    delivered = [(mid, score) for mid, score in result["delivered"] if mid in keep]
    added = [a for a in result["added"] if a["id"] in keep]
    return {**result, "delivered": delivered, "added": added,
            "items": delivered + [(a["id"], a["score"]) for a in added]}


def lookup(engine, cosines, settings):
    """engine.lookup for `cosines` with the filters of `settings` (a parsed `noise` part or None) that act inside a
    lookup: hub before it, then top and each on the unpenalised cosines. Without them it is engine.lookup itself."""
    if not settings or all(settings.get(name) is None for name in ("hub", "top", "each")):
        return engine.lookup(cosines)
    shifted = cosines
    if settings["hub"] is not None:
        key = ("_noise_hub", settings["k"])
        if not hasattr(engine, "_noise_cache"):
            engine._noise_cache = {}
        if key not in engine._noise_cache:
            engine._noise_cache[key] = hub_penalties(engine.base.vectors, engine.scale, settings["k"])
        shifted = [c - settings["hub"] * p for c, p in zip(cosines, engine._noise_cache[key])]
    result = engine.lookup(shifted)
    if settings["top"] is None and settings["each"] is None:
        return result
    z = zscores(cosines)
    if settings["top"] is not None and (not z or max(z) < settings["top"]):
        return restrict(result, set())
    if settings["each"] is not None:
        position = {mid: i for i, mid in enumerate(engine.ids)}
        return restrict(result, {mid for mid, _ in result["items"] if z[position[mid]] >= settings["each"]})
    return result


def keep_session_part(found, shown, settings, position):
    """The NEW memories [(id, score)] of a conversation's main lookup `found` (its items in the lookup's order) after
    skip-repeats, lead and cap: not in `shown`; with lead, when the best item is already shown only those scoring at
    least ratio x its score; with cap, from the second turn of the conversation (`position` > 1) at most n."""
    fresh = [(mid, score) for mid, score in found if mid not in shown]
    if settings and settings.get("lead") is not None and found and found[0][0] in shown:
        fresh = [(mid, score) for mid, score in fresh if score >= settings["lead"] * found[0][1]]
    if settings and settings.get("cap") is not None and position > 1:
        fresh = fresh[:settings["cap"]]
    return fresh


# ------------------------------------------------------------------ diagnose

def quantile(values, fraction):
    """The linear-interpolated quantile of a list (None when it is empty)."""
    if not values:
        return None
    ordered = sorted(values)
    spot = (len(ordered) - 1) * fraction
    low = int(math.floor(spot))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (spot - low)


def auc(positives, negatives):
    """The ROC-AUC of a score for telling `positives` from `negatives` (a tie counts half); None when one side is empty."""
    if not positives or not negatives:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))
