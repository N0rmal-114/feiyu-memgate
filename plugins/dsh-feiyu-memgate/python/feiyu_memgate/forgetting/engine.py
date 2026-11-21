"""The forgetting engine: the one object the recall stage, the gateway and the commands talk to.

It joins the ledger (when was each memory used), the curve (how fast it fades) and the
archive (where forgotten memories wait before they are really gone). Deleting is only
done by sweep(), and never before the archive write succeeded.

Two clocks, two questions. RANKING retention (the recall prior, the reported "demoted" status) counts
the age since the memory was last used, else since its own date: an old memory nobody uses ranks a
little lower (never below the freshness floor). DELETION retention (what makes a memory "due") counts the
age since the later of its last use and the time the ledger first managed it: a memory is only deleted
after going unused for the curve's whole lifetime *under management*, so turning forgetting on never
deletes an existing store on the first day.
A rate limit (max_forget_fraction, max_forget_min) bounds how many memories one sweep forgets per store,
lowest deletion-retention first: a cohort that expires together leaves over several sweeps, and a wrong
clock or a mis-set half-life costs a small, archived, restorable slice per sweep instead of the store.

Logs and reports hold numbers and store names only, never memory text.
Standard library only.
"""
import calendar
import fnmatch
import math
import time
from typing import Any

DAY = 86400.0
REQUIRED = ("refs", "delete", "put")


def date_to_epoch(date):
    """Midnight UTC of "YYYY-MM-DD" as unix seconds, or None when it is not such a date."""
    if not isinstance(date, str):
        return None
    try:
        return float(calendar.timegm(time.strptime(date[:10], "%Y-%m-%d")))
    except ValueError:
        return None


def supports(store):
    """True when the backend has the three optional capabilities forgetting needs."""
    return all(callable(getattr(store, name, None)) for name in REQUIRED)


class Forgetting:
    def __init__(self, ledger, archive, curve, *, stores=(), demote_below=0.3, forget_below=0.05,
                 archive_days=90, pin_refs=(), max_forget_fraction=0.02, max_forget_min=5, wall=time.time):
        self.ledger = ledger
        self.archive = archive
        self.curve = curve
        self.stores = tuple(stores)
        self.demote_below = demote_below
        self.forget_below = forget_below
        self.archive_days = archive_days
        self.pin_refs = tuple(pin_refs)
        self.max_forget_fraction = max_forget_fraction
        self.max_forget_min = max_forget_min
        self.wall = wall

    # ---------------------------------------------------------------- keys and scores

    def now(self):
        return self.wall()

    def manages(self, name):
        return name in self.stores

    @staticmethod
    def key(source, tenant_id=None):
        return source if tenant_id is None else "%s:%s" % (source, tenant_id)

    def _retention(self, key, start, uses, now):
        age_days = 0.0 if start is None else max(now - start, 0.0) / DAY
        value = self.curve.retention(age_days, uses, key.partition(":")[0])
        return min(max(value, 0.0), 1.0)

    @staticmethod
    def ranking_start(entry, date=None):
        """When the RANKING age starts: the last use, else the memory's own date (else None = no age)."""
        if entry is None:
            return date_to_epoch(date)
        return entry.last_used if entry.last_used is not None else entry.first_seen

    @staticmethod
    def deletion_start(entry, now):
        """When the DELETION age starts: the later of the last use and managed_since. A memory the
        ledger does not know yet (or a row without managed_since) starts now: never due."""
        if entry is None or entry.managed_since is None:
            return entry.last_used if entry is not None and entry.last_used is not None else now
        return entry.managed_since if entry.last_used is None else max(entry.last_used, entry.managed_since)

    @staticmethod
    def uses_of(entry):
        return 0 if entry is None else entry.uses

    def ranking_retention(self, key, entry, date=None, now=None):
        """Retention for the recall prior (freshness) and for the reported "demoted" status."""
        now = self.wall() if now is None else now
        return self._retention(key, self.ranking_start(entry, date), self.uses_of(entry), now)

    def deletion_retention(self, key, entry, now=None):
        """Retention for deciding "due": counted from the later of last use and managed_since."""
        now = self.wall() if now is None else now
        return self._retention(key, self.deletion_start(entry, now), self.uses_of(entry), now)

    def retention(self, key, entry, date=None, now=None):
        """Alias of ranking_retention (the name the recall stage and older callers use)."""
        return self.ranking_retention(key, entry, date, now)

    def _is_pinned(self, ref, entry):
        if entry is not None and entry.pinned:
            return True
        return any(fnmatch.fnmatchcase(ref, pattern) for pattern in self.pin_refs)

    def status(self, key, ref, entry, date=None, now=None):
        """"pinned", "due" (to be forgotten: DELETION retention under the line), "demoted" (RANKING
        retention under the line: a report only, "at the freshness floor soon, a candidate for deletion
        later"; it changes nothing in ranking) or "active"."""
        if self._is_pinned(ref, entry):
            return "pinned"
        if self.deletion_retention(key, entry, now) < self.forget_below:
            return "due"
        if self.ranking_retention(key, entry, date, now) < self.demote_below:
            return "demoted"
        return "active"

    def freshness(self, key, ref, entry, floor, date=None, now=None):
        """The multiplier for a recall score, one bounded formula for every state: 1 when pinned,
        else floor + (1 - floor) * ranking retention. Relevance dominates; freshness only settles
        near-ties, so a strong match is never buried by how long ago it was last returned."""
        if self._is_pinned(ref, entry):
            return 1.0
        value = self.ranking_retention(key, entry, date, now)
        return floor + (1.0 - floor) * value

    # ---------------------------------------------------------------- ledger bookkeeping

    def record_use(self, key, items, now=None):
        """items: (ref, date) of the memories that were returned to a caller."""
        now = self.wall() if now is None else now
        seen: dict[Any, float | None] = {}
        for ref, date in items:
            seen.setdefault(ref, date_to_epoch(date))
        if seen:
            self.ledger.use(key, list(seen.items()), now)

    def _sync_listing(self, key, listing, now):
        known = {entry.ref for entry in self.ledger.entries(key)}
        present: dict[Any, Any] = {}
        for ref, date in listing:
            present.setdefault(ref, date)
        added = [(ref, date_to_epoch(date)) for ref, date in present.items() if ref not in known]
        gone = [ref for ref in known if ref not in present]
        if added:
            self.ledger.touch(key, added, now)
        if gone:
            self.ledger.remove(key, gone)
        return {"added": len(added), "removed": len(gone)}

    def sync(self, key, store, now=None):
        """Make the ledger match the store: add memories it does not know, drop rows of gone ones."""
        now = self.wall() if now is None else now
        return self._sync_listing(key, list(store.refs()), now)

    def pin(self, key, refs):
        self.ledger.pin(key, list(refs))

    def unpin(self, key, refs):
        self.ledger.unpin(key, list(refs))

    def pin_receipt(self, key, store, receipt):
        """Pin the memories one write produced. Returns how many, or None when the store
        cannot say (yet)."""
        lookup = getattr(store, "receipt_refs", None)
        if not callable(lookup):
            return None
        refs = lookup(receipt)
        if refs is None:
            return None
        refs = list(refs)
        if refs:
            self.ledger.pin(key, refs)
        return len(refs)

    # ---------------------------------------------------------------- sweep and restore

    def sweep(self, targets, *, dry_run=False, force=False, now=None):
        """Forget what is due in every target ({key: store}). Numbers only in the report.

        Rate limit: per store, at most max(max_forget_min, max_forget_fraction * memories in the
        store) are forgotten in one run, the lowest deletion-retention first; the rest stay and are
        reported as `held` (they go in later sweeps). force=True lifts the limit for this one call
        (the gateway's background sweep never passes it).

        Order per memory: read the text, write the archive, delete from the store, drop the
        ledger row. Any failure leaves that memory for the next sweep. A dry run reads
        listings but writes nothing anywhere.
        """
        now = self.wall() if now is None else now
        report: dict[str, Any] = {"unsupported": []}
        for key, store in targets.items():
            if not supports(store):
                report["unsupported"].append(key)
                continue
            report[key] = self._sweep_one(key, store, dry_run, now, force)
        report["purged"] = 0 if dry_run else self.archive.purge(now)
        return report

    def _sweep_one(self, key, store, dry_run, now, force=False):
        row = {"total": 0, "pinned": 0, "active": 0, "demoted": 0,
               "due" if dry_run else "forgotten": 0, "held": 0, "failed": 0}
        try:
            listing = list(store.refs())
        except Exception:  # noqa: BLE001 - a store that cannot list is retried next time
            row["failed"] = 1
            return row
        dates: dict[Any, Any] = {}
        for ref, date in listing:
            dates.setdefault(ref, date)
        if dry_run:
            entries = {e.ref: e for e in self.ledger.entries(key)}
        else:
            self._sync_listing(key, listing, now)
            entries = {e.ref: e for e in self.ledger.entries(key)}
        row["total"] = len(dates)
        due = []
        for ref, date in dates.items():
            entry = entries.get(ref)
            state = self.status(key, ref, entry, date, now)
            if state == "due":
                due.append((ref, date, entry))
            else:
                row[state] += 1
        # Lowest deletion-retention first; the ref breaks ties so a run is reproducible.
        due.sort(key=lambda item: (self.deletion_retention(key, item[2], now), item[0]))
        allowed = len(due) if force else min(len(due), self.forget_limit(len(dates)))
        row["held"] = len(due) - allowed  # a dry run reports what a real run would hold back
        if dry_run:
            row["due"] = len(due)
            return row
        for ref, date, entry in due[:allowed]:
            if self._forget(key, store, ref, date, entry, now):
                row["forgotten"] += 1
            else:
                row["failed"] += 1
        return row

    def forget_limit(self, size):
        """How many memories one sweep may forget in a store holding `size` memories."""
        return max(int(self.max_forget_min), int(math.floor(self.max_forget_fraction * size + 1e-9)))

    def _forget(self, key, store, ref, date, entry, now):
        try:
            text = store.read(ref)
            if not isinstance(text, str):
                return False
            self.archive.add(key, ref, text, date, entry.uses if entry else 0, now,
                             now + self.archive_days * DAY)
        except Exception:  # noqa: BLE001 - nothing was deleted, so nothing is lost
            return False
        try:
            deleted = store.delete(ref)
        except Exception:  # noqa: BLE001
            deleted = False
        if not deleted:
            # The memory is still there: do not leave an archive copy that claims it is gone.
            try:
                self.archive.remove(key, ref)
            except Exception:  # noqa: BLE001
                pass
            return False
        try:
            self.ledger.remove(key, [ref])
        except Exception:  # noqa: BLE001 - the next sync drops the stale row
            pass
        return True

    def restore(self, key, ref, store):
        """Write an archived memory back to its store and address. False when it is not archived."""
        record = self.archive.get(key, ref)
        if record is None:
            return False
        store.put(record.text, ref=ref, date=record.date)
        self.ledger.revive(key, ref, self.wall())
        self.archive.remove(key, ref)
        return True

    def close(self):
        for piece in (self.ledger, self.archive):
            try:
                piece.close()
            except Exception:  # noqa: BLE001
                pass
