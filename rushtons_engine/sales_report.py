"""Sales report: £ by customer and by account manager, week and month, against
the previous week/month and the same period last year.

Asked for by Hannah (9 Nov 2026: "each customer's sales this week against last
week and against the same week last year, with the biggest movers") and agreed
on the 11 Nov call as a Monday-run report: week on week, this week last year,
month to date against last year, and last month against the month before and
last year. Shown on the dashboard's hidden #reporting section until the team
signs off the shape.

Source: Fresho's "Sales Totals and Gross Profit Margin" report, which takes any
date range. Each comparison period is its own export, stored under its exact
range in customer_period_values (ingest.ingest_values_files); complete months
come from customer_month_values. Nothing here sums overlapping ranges.

Definitions (repeated on the page):
- A week is Monday to Sunday: the last complete week before the as-of date.
- "Same week last year" is the Monday-Sunday 52 weeks (364 days) earlier, so the
  days of the week line up.
- Month to date runs from the 1st to the end of that week; it is compared with
  the same dates last month and last year. Those can hold a different number of
  delivery days (Mon-Sat), so the page also shows £ per delivery day.
- Account manager is the CURRENT one in Fresho, applied to every period ("as
  is"): accounts that changed hands count fully under their new manager. Fresho
  keeps no history of who managed an account when.

Read-only: every query goes through query.run_sql.
"""

import logging
import sys
from datetime import date, timedelta

import config
import query

log = logging.getLogger(__name__)

NO_REP = "(no account manager)"
MONTHS = ("January February March April May June July August September "
          "October November December").split()


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def week_bounds(as_of: date) -> tuple[date, date]:
    """The last complete Monday-Sunday week strictly before as_of."""
    end = as_of - timedelta(days=as_of.weekday() + 1)
    return end - timedelta(days=6), end


def _shift_year(d: date, years: int = -1) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:                      # 29 Feb
        return d.replace(year=d.year + years, day=28)


def _month_end(d: date) -> date:
    nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def _prev_month_same_days(start: date, end: date) -> tuple[date, date]:
    pm_end = start - timedelta(days=1)
    pm_start = pm_end.replace(day=1)
    return pm_start, min(pm_start.replace(day=1) + timedelta(days=end.day - 1), pm_end)


def delivery_days(start: date, end: date) -> int:
    """Monday-Saturday days in the range (Rushton's doesn't deliver Sundays)."""
    return sum(1 for i in range((end - start).days + 1)
               if (start + timedelta(days=i)).weekday() < 6)


def periods(as_of: date) -> dict:
    """{key: {label, cur, prev, ly}} with each range a (start, end) tuple."""
    ws, we = week_bounds(as_of)
    ms = we.replace(day=1)
    lm_end = ms - timedelta(days=1)
    lm_start = lm_end.replace(day=1)
    pm_end = lm_start - timedelta(days=1)
    return {
        "w": {"label": "Week", "cur": (ws, we),
              "prev": (ws - timedelta(days=7), we - timedelta(days=7)),
              "ly": (ws - timedelta(days=364), we - timedelta(days=364))},
        "mtd": {"label": "Month to date", "cur": (ms, we),
                "prev": _prev_month_same_days(ms, we),
                "ly": (_shift_year(ms), _shift_year(we))},
        "m": {"label": "Last month", "cur": (lm_start, lm_end),
              "prev": (pm_end.replace(day=1), pm_end),
              "ly": (_shift_year(lm_start), _month_end(_shift_year(lm_start)))},
    }


def trend_ranges(as_of: date, weeks: int | None = None) -> list[tuple]:
    """[(this year's week, the same week last year), ...] oldest first."""
    weeks = weeks or config.SALES_REPORT_TREND_WEEKS
    ws, we = week_bounds(as_of)
    out = []
    for k in range(weeks - 1, -1, -1):
        cur = (ws - timedelta(days=7 * k), we - timedelta(days=7 * k))
        out.append((cur, (cur[0] - timedelta(days=364), cur[1] - timedelta(days=364))))
    return out


def _is_full_month(r: tuple) -> bool:
    return r[0].day == 1 and r[1] == _month_end(r[0])


def _load(ranges: set) -> dict:
    """{(start, end): {code: (net, gp, name)}} for every range held. A complete
    calendar month is read from customer_month_values; anything else needs its
    own export in customer_period_values."""
    out: dict = {}
    months = sorted(r for r in ranges if _is_full_month(r))
    if months:
        lst = ", ".join(f"'{m[0]}'" for m in months)
        for row in query.run_sql(
                "select customer_code, month, period_end, net_sales, gross_profit, "
                f"customer_name from customer_month_values where month in ({lst})",
                limit=100000)["rows"]:
            r = (_d(row["month"]), _month_end(_d(row["month"])))
            if _d(row["period_end"]) != r[1]:
                continue                     # month to date only - not complete
            out.setdefault(r, {})[row["customer_code"]] = (
                float(row["net_sales"] or 0), float(row["gross_profit"] or 0),
                row["customer_name"])
    others = sorted(r for r in ranges if r not in out)
    if others:
        cond = " or ".join(f"(period_start = '{a}' and period_end = '{b}')"
                           for a, b in others)
        res = query.run_sql(
            "select customer_code, period_start, period_end, net_sales, gross_profit, "
            f"customer_name from customer_period_values where {cond}", limit=100000)
        if res["truncated"]:
            log.warning("sales report: period values truncated")
        for row in res["rows"]:
            r = (_d(row["period_start"]), _d(row["period_end"]))
            out.setdefault(r, {})[row["customer_code"]] = (
                float(row["net_sales"] or 0), float(row["gross_profit"] or 0),
                row["customer_name"])
    return out


def needed_exports(as_of: date) -> list[tuple]:
    """Date ranges to export from Fresho (GP-margin report, CSV) that aren't
    loaded yet - everything the report for this as-of date compares. Ranges
    before Fresho's data starts are left out."""
    want = set()
    for p in periods(as_of).values():
        want.update(p[k] for k in ("cur", "prev", "ly"))
    for cur, ly in trend_ranges(as_of):
        want.update((cur, ly))
    data_from = date.fromisoformat(config.VALUE_DATA_FROM)
    want = {r for r in want if r[1] >= data_from}
    held = _load(want)
    return sorted(r for r in want if r not in held)


def _fmt(r: tuple) -> str:
    a, b = r
    if a.year != b.year:
        return f"{a.day} {MONTHS[a.month - 1][:3]} {a.year} – {b.day} {MONTHS[b.month - 1][:3]} {b.year}"
    if a.month != b.month:
        return f"{a.day} {MONTHS[a.month - 1][:3]} – {b.day} {MONTHS[b.month - 1][:3]} {b.year}"
    if _is_full_month(r):
        return f"{MONTHS[a.month - 1]} {a.year}"
    return f"{a.day}–{b.day} {MONTHS[a.month - 1][:3]} {a.year}"


def build(as_of: date) -> dict:
    """Everything the #reporting section needs, compact for embedding."""
    as_of = _d(as_of)
    pers = periods(as_of)
    trend = trend_ranges(as_of)
    ranges = {p[k] for p in pers.values() for k in ("cur", "prev", "ly")}
    ranges |= {r for pair in trend for r in pair}
    held = _load(ranges)
    data_from = date.fromisoformat(config.VALUE_DATA_FROM)

    cust = {r["customer_code"]: r for r in query.run_sql(
        "select customer_code, customer_name, sales_rep, size_band, venue_type, "
        "activity_status, prestige from customers", limit=5000)["rows"]}

    def info(r):
        return {"from": str(r[0]), "to": str(r[1]), "label": _fmt(r),
                "have": r in held, "partial": r[0] < data_from,
                "days": delivery_days(*r)}

    period_meta = {k: {"label": p["label"], "cur": info(p["cur"]),
                       "prev": info(p["prev"]), "ly": info(p["ly"])}
                   for k, p in pers.items()}

    codes = set()
    for p in pers.values():
        for k in ("cur", "prev", "ly"):
            codes |= set(held.get(p[k], {}))

    def val(r, code):
        if r not in held:
            return None, None
        v = held[r].get(code)
        return (round(v[0]), round(v[1])) if v else (0, 0)

    rows = []
    for code in sorted(codes):
        c = cust.get(code, {})
        name = c.get("customer_name")
        if not name:
            name = next((held[r][code][2] for r in held if code in held[r]), code)
        p = {}
        for k, per in pers.items():
            vals = [val(per[x], code) for x in ("cur", "prev", "ly")]
            p[k] = [v[0] for v in vals] + [v[1] for v in vals]
        if all(not x for arr in p.values() for x in arr):
            continue
        rows.append({"c": code, "n": name, "r": c.get("sales_rep") or NO_REP,
                     "t": c.get("size_band") or "", "v": c.get("venue_type") or "",
                     "s": c.get("activity_status") or "",
                     "x": 1 if c.get("prestige") == "Excluded" else 0, "p": p})

    def tot(r, code_rep=None):
        if r not in held:
            return None
        return round(sum(v[0] for code, v in held[r].items()
                         if code_rep is None or
                         ((cust.get(code, {}).get("sales_rep") or NO_REP) == code_rep)))

    def tot_gp(r):
        return round(sum(v[1] for v in held[r].values())) if r in held else None

    reps = sorted({row["r"] for row in rows})
    trend_out = {
        "weeks": [str(cur[0]) for cur, _ in trend],
        "labels": [_fmt(cur) for cur, _ in trend],
        "cur": [tot(cur) for cur, _ in trend],
        "ly": [None if ly[0] < data_from else tot(ly) for _, ly in trend],
        "curGp": [tot_gp(cur) for cur, _ in trend],
        "lyGp": [None if ly[0] < data_from else tot_gp(ly) for _, ly in trend],
        "byRep": {rep: {"cur": [tot(cur, rep) for cur, _ in trend],
                        "ly": [None if ly[0] < data_from else tot(ly, rep)
                               for _, ly in trend]} for rep in reps},
    }
    return {"asOf": str(as_of), "periods": period_meta, "rows": rows,
            "trend": trend_out, "dataFrom": config.VALUE_DATA_FROM,
            "missing": [[str(a), str(b)] for a, b in sorted(r for r in ranges
                        if r not in held and r[1] >= data_from)]}


def _summary(as_of: date) -> None:
    rep = build(as_of)
    for k, p in rep["periods"].items():
        line = [f"{p['label']:<14}"]
        for x in ("cur", "prev", "ly"):
            i = ("cur", "prev", "ly").index(x)
            if p[x]["have"]:
                total = sum(r["p"][k][i] for r in rep["rows"])
                line.append(f"{x} {p[x]['label']}: £{total:,.0f} ({p[x]['days']}d)")
            else:
                line.append(f"{x} {p[x]['label']}: NOT LOADED")
        print(" | ".join(line))
    if rep["missing"]:
        print("missing exports:", rep["missing"])


if __name__ == "__main__":
    # python sales_report.py needed  [YYYY-MM-DD]  - GP exports still to download
    # python sales_report.py summary [YYYY-MM-DD]  - totals, for a sanity check
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    when = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    if cmd == "needed":
        for a, b in needed_exports(when):
            print(f"{a} {b}")
    elif cmd == "summary":
        _summary(when)
    else:
        sys.exit("usage: python sales_report.py needed|summary [YYYY-MM-DD]")
