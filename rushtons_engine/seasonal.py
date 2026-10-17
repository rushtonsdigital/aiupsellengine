"""The monthly seasonal list — what's at its best now, and what isn't.

Fresho has no in-season tag (Hannah 2026-10-14), and the "Z888. Out of Season"
group only catches lines Rushton's has already switched off. So, as agreed on
2026-10-15, a first list is built each month from the full produce list and
that month's market report (with AI help) and refined from the team's
corrections — e.g. Laurence swapping Scottish girolles and IOW heritage
tomatoes out on 2026-10-14.

One CSV per month in seasonal/: seasonal_YYYY-MM.csv with columns
    pattern  case-insensitive regex matched against the product name
    status   peak  -> at its best: floated to the top of the pool and flagged
             out   -> out of season: removed from the pool
    note, source  why, and who said so
First matching row wins. No file for the run's month = no seasonal effect
(a stale month's list would do more harm than none), logged as a warning.
"""

import csv
import functools
import logging
import re
from datetime import date

import config

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=None)
def rules_for(month: str) -> tuple:
    """((compiled pattern, status, note), ...) for a 'YYYY-MM' month."""
    path = config.SEASONAL_DIR / f"seasonal_{month}.csv"
    if not path.exists():
        log.warning("no seasonal list for %s (%s) - pools carry no season flags",
                    month, path.name)
        return ()
    rules = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            status = (row.get("status") or "").strip().lower()
            if status not in ("peak", "out"):
                raise ValueError(f"{path.name}: status must be peak or out, got {status!r}")
            rules.append((re.compile(row["pattern"].strip(), re.I), status,
                          (row.get("note") or "").strip()))
    log.info("seasonal list %s: %d rules", path.name, len(rules))
    return tuple(rules)


def status(product_name: str, as_of: date) -> tuple[str | None, str]:
    """('peak' | 'out' | None, note) for a product in the run's month."""
    name = (product_name or "").strip()
    for pattern, st, note in rules_for(as_of.strftime("%Y-%m")):
        if pattern.search(name):
            return st, note
    return None, ""
