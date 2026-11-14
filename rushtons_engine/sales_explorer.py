"""Sales explorer: the data behind the dashboard's hidden #reporting section,
a Google-Analytics-style view where the date range and the comparison range
drive everything, metrics are switched on and off, and the table can be split
by account manager, customer, category, venue type or tier.

Source: sales_daily (Fresho "Sales Line Items", daily £ and cost per customer x
category; reconciles to the GP-margin report to the pound). The page gets two
compact day-level tables and does every range / comparison / grouping in the
browser, so any dates can be compared without another export:

- `cd`: per customer per day  [day, customer, sales, gp, lines, rebate]
- `kd`: per category per account manager per day
        [day, category, manager, sales, gp, lines, rebate, deliveries]

Every £ is net of credits (as Fresho's report). Account manager is the CURRENT
one in Fresho, applied to every date ("as is"). Read-only (query.run_sql).
"""

import logging
from datetime import date, timedelta

import query

log = logging.getLogger(__name__)

NO_REP = "(no account manager)"


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _p(x) -> float | int:
    v = round(float(x or 0), 1)
    return int(v) if v == int(v) else v


def build() -> dict | None:
    span = query.run_sql("select min(sale_date) a, max(sale_date) b from sales_daily")["rows"]
    if not span or span[0]["a"] is None:
        return None
    first, last = _d(span[0]["a"]), _d(span[0]["b"])
    n_days = (last - first).days + 1

    cust = {r["customer_code"]: r for r in query.run_sql(
        "select customer_code, customer_name, sales_rep, venue_type, size_band, "
        "prestige from customers", limit=5000)["rows"]}

    cd_rows = query.run_sql(
        "select customer_code, sale_date, sum(net_sales) s, sum(net_sales - cost) g, "
        "sum(lines) l, sum(rebate) r from sales_daily group by customer_code, sale_date",
        limit=400000)
    if cd_rows["truncated"]:
        log.warning("sales explorer: customer-day rows truncated")
    kd_rows = query.run_sql(
        "select s.sale_date, s.category, c.sales_rep, sum(s.net_sales) s, "
        "sum(s.net_sales - s.cost) g, sum(s.lines) l, sum(s.rebate) r, "
        "count(distinct s.customer_code) n from sales_daily s "
        "left join customers c on c.customer_code = s.customer_code "
        "group by s.sale_date, s.category, c.sales_rep", limit=400000)["rows"]

    codes = sorted({r["customer_code"] for r in cd_rows["rows"]})
    cidx = {c: i for i, c in enumerate(codes)}
    reps = sorted({(cust.get(c, {}).get("sales_rep") or NO_REP) for c in codes}
                  | {(r["sales_rep"] or NO_REP) for r in kd_rows})
    ridx = {r: i for i, r in enumerate(reps)}
    cats = sorted({r["category"] for r in kd_rows})
    kidx = {k: i for i, k in enumerate(cats)}

    def day(x):
        return (_d(x) - first).days

    customers = [[c, (cust.get(c, {}).get("customer_name") or c),
                  ridx[cust.get(c, {}).get("sales_rep") or NO_REP],
                  cust.get(c, {}).get("venue_type") or "Unknown",
                  cust.get(c, {}).get("size_band") or "",
                  1 if cust.get(c, {}).get("prestige") == "Excluded" else 0]
                 for c in codes]
    # £ to 10p and rebates to the pound keep a year of days to a few MB on the page
    cd = [[day(r["sale_date"]), cidx[r["customer_code"]], _p(r["s"]), _p(r["g"]),
           int(r["l"] or 0), round(float(r["r"] or 0))]
          for r in cd_rows["rows"]]
    cd.sort()
    kd = [[day(r["sale_date"]), kidx[r["category"]], ridx[r["sales_rep"] or NO_REP],
           _p(r["s"]), _p(r["g"]), int(r["l"] or 0), round(float(r["r"] or 0)),
           int(r["n"] or 0)] for r in kd_rows]
    kd.sort()
    return {"first": str(first), "last": str(last), "days": n_days,
            "customers": customers, "reps": reps, "cats": cats, "cd": cd, "kd": kd,
            "noRep": NO_REP}


def missing_days(through: date) -> list[tuple[date, date]]:
    """Gaps in sales_daily up to `through` (excluding Sundays, when nothing is
    delivered), as (start, end) ranges - the Sales Line Items exports to pull."""
    rows = query.run_sql("select distinct sale_date d from sales_daily", limit=5000)["rows"]
    have = {_d(r["d"]) for r in rows}
    if not have:
        return [(through - timedelta(days=6), through)]
    gaps, cur = [], None
    d = min(have)
    while d <= through:
        hole = d not in have and d.weekday() != 6
        if hole and cur is None:
            cur = [d, d]
        elif hole:
            cur[1] = d
        elif cur is not None and d.weekday() != 6:
            gaps.append(tuple(cur))
            cur = None
        d += timedelta(days=1)
    if cur is not None:
        gaps.append(tuple(cur))
    return gaps


if __name__ == "__main__":
    # python sales_explorer.py missing [YYYY-MM-DD]  - Sales Line Items days to export
    import sys
    when = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today() - timedelta(days=1)
    if len(sys.argv) > 1 and sys.argv[1] == "missing":
        for a, b in missing_days(when):
            print(a, b)
    else:
        rep = build()
        if rep:
            print(rep["first"], "->", rep["last"], len(rep["cd"]), "customer-days",
                  len(rep["kd"]), "category rows", len(rep["customers"]), "customers")
