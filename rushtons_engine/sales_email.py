"""Weekly sales update email (Monday run): week summary, biggest movers, month to
date against last month and last year, by account manager and by category.

Every number is computed here from sales_daily (Fresho "Sales Line Items", which
reconciles to the GP-margin report). Python never writes the commentary: Claude
writes the short summary from facts_<date>.json into the {{SUMMARY}} slot, Jose
checks it, and it goes out as a Gmail draft he sends. Nothing is sent from here.

    python sales_email.py <week-ending Sunday YYYY-MM-DD>
      -> output/sales_update_<sunday>.html   (email body, inline styles)
      -> output/sales_update_<sunday>.json   (the facts the summary may use)

Definitions (repeated in the email footer):
- Week = Monday-Sunday. Same week last year = the Monday-Sunday 52 weeks earlier.
- Month to date = 1st of the month to the Sunday; compared with the same dates
  last month and last year (delivery days can differ - shown).
- Account manager = the CURRENT one in Fresho, for every period ("as is").
"""

import html
import json
import sys
from datetime import date, timedelta

import config
import query

NO_REP = "(no account manager)"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
GREEN, RED, MUTED, LINE = "#007946", "#c0392b", "#55595F", "#E4E6CC"
MOVERS_N = 8


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _lbl(a: date, b: date) -> str:
    if a == b:
        return f"{a.day} {MONTHS[a.month - 1]} {a.year}"
    if a.year != b.year:
        return f"{a.day} {MONTHS[a.month - 1]} {a.year} – {b.day} {MONTHS[b.month - 1]} {b.year}"
    if a.month != b.month:
        return f"{a.day} {MONTHS[a.month - 1]} – {b.day} {MONTHS[b.month - 1]} {b.year}"
    return f"{a.day}–{b.day} {MONTHS[a.month - 1]} {a.year}"


def _shift_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def _prev_month_same_days(start: date, end: date) -> tuple[date, date]:
    pm_end = start - timedelta(days=1)
    pm_start = pm_end.replace(day=1)
    return pm_start, min(pm_start + timedelta(days=end.day - 1), pm_end)


def delivery_days(a: date, b: date) -> int:
    return sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 6)


def periods(sunday: date) -> dict:
    ws = sunday - timedelta(days=6)
    ms = sunday.replace(day=1)
    return {
        "week": (ws, sunday), "prev_week": (ws - timedelta(days=7), sunday - timedelta(days=7)),
        "ly_week": (ws - timedelta(days=364), sunday - timedelta(days=364)),
        "mtd": (ms, sunday), "prev_mtd": _prev_month_same_days(ms, sunday),
        "ly_mtd": (_shift_year(ms), _shift_year(sunday)),
    }


def _load(a: date, b: date) -> dict:
    """{code: {s, g, d (deliveries), cat: {category: [s, g]}}} for a range."""
    rows = query.run_sql(
        "select customer_code, sale_date, category, net_sales, net_sales - cost as gp "
        f"from sales_daily where sale_date between '{a}' and '{b}'", limit=200000)["rows"]
    out: dict = {}
    for r in rows:
        c = out.setdefault(r["customer_code"], {"s": 0.0, "g": 0.0, "days": set(), "cat": {}})
        s, g = float(r["net_sales"] or 0), float(r["gp"] or 0)
        c["s"] += s
        c["g"] += g
        c["days"].add(str(r["sale_date"]))
        k = c["cat"].setdefault(r["category"], [0.0, 0.0])
        k[0] += s
        k[1] += g
    return out


def _tot(data: dict) -> dict:
    s = sum(v["s"] for v in data.values())
    g = sum(v["g"] for v in data.values())
    d = sum(len(v["days"]) for v in data.values())
    acc = sum(1 for v in data.values() if v["s"] > 0)
    return {"sales": round(s), "gp": round(g), "gp_pct": round(100 * g / s, 1) if s else None,
            "deliveries": d, "accounts": acc, "avg_delivery": round(s / d) if d else None}


def _pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1)


def build(sunday: date) -> dict:
    """All the numbers for the email, as plain data (also the summary's facts)."""
    span = query.run_sql("select min(sale_date) a, max(sale_date) b from sales_daily")["rows"][0]
    first, last = _d(span["a"]), _d(span["b"])
    if sunday - timedelta(days=1) > last:
        raise SystemExit(f"sales_daily only runs to {last}; load the Sales Line Items export "
                         f"for the week ending {sunday} first")
    per = periods(sunday)
    data = {k: _load(*v) for k, v in per.items()}
    cust = {r["customer_code"]: r for r in query.run_sql(
        "select customer_code, customer_name, sales_rep from customers", limit=5000)["rows"]}

    def name(code):
        return (cust.get(code, {}).get("customer_name") or code)

    def rep(code):
        return cust.get(code, {}).get("sales_rep") or NO_REP

    totals = {k: _tot(v) for k, v in data.items()}

    # movers: by £ change, this week vs last week
    codes = set(data["week"]) | set(data["prev_week"])
    moves = []
    for c in codes:
        # internal accounts (cash sales, staff, samples...) stay in the totals,
        # never in the movers list
        if any(k in name(c).lower() for k in config.INTERNAL_NAME_KEYWORDS):
            continue
        a = data["week"].get(c, {}).get("s", 0.0)
        b = data["prev_week"].get(c, {}).get("s", 0.0)
        ly = data["ly_week"].get(c, {}).get("s", 0.0)
        if round(a) == round(b):
            continue
        moves.append({"code": c, "name": name(c), "rep": rep(c), "week": round(a),
                      "prev_week": round(b), "ly_week": round(ly), "change": round(a - b),
                      "change_pct": _pct(a, b), "status": "new" if not b else
                      ("stopped" if not a else "")})
    up = sorted([m for m in moves if m["change"] > 0], key=lambda m: -m["change"])[:MOVERS_N]
    down = sorted([m for m in moves if m["change"] < 0], key=lambda m: m["change"])[:MOVERS_N]

    # by account manager and by category (week and month to date, vs last year)
    def group(dat, keyf):
        g: dict = {}
        for c, v in dat.items():
            k = keyf(c)
            t = g.setdefault(k, [0.0, 0.0])
            t[0] += v["s"]
            t[1] += v["g"]
        return g

    def cat_group(dat):
        g: dict = {}
        for v in dat.values():
            for k, (s, gp) in v["cat"].items():
                t = g.setdefault(k, [0.0, 0.0])
                t[0] += s
                t[1] += gp
        return g

    def table(fn):
        keys = {}
        cols = {k: fn(data[k]) for k in ("week", "ly_week", "mtd", "prev_mtd", "ly_mtd")}
        for col in cols.values():
            keys.update(dict.fromkeys(col))
        out = []
        for k in keys:
            w, wl = cols["week"].get(k, [0, 0]), cols["ly_week"].get(k, [0, 0])
            m, mp, ml = (cols["mtd"].get(k, [0, 0]), cols["prev_mtd"].get(k, [0, 0]),
                         cols["ly_mtd"].get(k, [0, 0]))
            out.append({"name": k, "week": round(w[0]), "week_gp_pct":
                        round(100 * w[1] / w[0], 1) if w[0] else None,
                        "week_vs_ly_pct": _pct(w[0], wl[0]), "mtd": round(m[0]),
                        "mtd_vs_prev_pct": _pct(m[0], mp[0]), "mtd_vs_ly_pct": _pct(m[0], ml[0]),
                        "mtd_gp_pct": round(100 * m[1] / m[0], 1) if m[0] else None})
        return sorted([r for r in out if r["week"] or r["mtd"]], key=lambda r: -r["mtd"])

    def info(k):
        a, b = per[k]
        return {"from": str(a), "to": str(b), "label": _lbl(a, b),
                "delivery_days": delivery_days(a, b), "complete": a >= first}

    return {"week_ending": str(sunday), "periods": {k: info(k) for k in per},
            "totals": totals, "movers_up": up, "movers_down": down,
            "by_manager": table(lambda d: group(d, rep)), "by_category": table(cat_group),
            "data_from": str(first), "data_to": str(last)}


# --- HTML (email-safe: tables + inline styles) --------------------------------

def _m(v):
    if v is None:
        return "—"
    return ("−" if v < 0 else "") + "£" + f"{abs(round(v)):,}"


def _chg(p, good_up=True, pts=False):
    if p is None:
        return f'<span style="color:{MUTED}">—</span>'
    bad = p < 0 if good_up else p > 0
    unit = " pts" if pts else "%"
    return (f'<span style="color:{RED if bad else GREEN};font-weight:600">'
            f'{"+" if p > 0 else ""}{p:.1f}{unit}</span>')


TD = f'style="padding:6px 8px;border-bottom:1px solid {LINE};font-size:13px"'
TDR = f'style="padding:6px 8px;border-bottom:1px solid {LINE};font-size:13px;text-align:right;white-space:nowrap"'
TH = f'style="padding:6px 8px;border-bottom:2px solid {LINE};font-size:11px;color:{MUTED};text-align:left;font-weight:600"'
THR = f'style="padding:6px 8px;border-bottom:2px solid {LINE};font-size:11px;color:{MUTED};text-align:right;font-weight:600"'


def _h(text):
    return (f'<h2 style="font-size:16px;color:{GREEN};margin:26px 0 6px;'
            f'font-family:Arial,sans-serif">{html.escape(text)}</h2>')


def _kpi_table(cur, a, b, la, lb):
    rows = [("Net sales", "sales", True, "m"), ("Gross profit (cash margin)", "gp", True, "m"),
            ("GP %", "gp_pct", True, "pct"), ("Deliveries", "deliveries", True, "n"),
            ("Accounts buying", "accounts", True, "n"), ("Average delivery", "avg_delivery", True, "m")]
    fmt = {"m": _m, "pct": lambda v: "—" if v is None else f"{v:.1f}%",
           "n": lambda v: "—" if v is None else f"{v:,}"}
    body = ""
    for lbl, k, up, f in rows:
        c, x, y = cur.get(k), a.get(k), b.get(k)
        if f == "pct":
            ca = _chg(None if c is None or x is None else round(c - x, 1), up, True)
            cb = _chg(None if c is None or y is None else round(c - y, 1), up, True)
        else:
            ca, cb = _chg(_pct(c, x), up), _chg(_pct(c, y), up)
        body += (f'<tr><td {TD}>{lbl}</td><td {TDR}><b>{fmt[f](c)}</b></td>'
                 f'<td {TDR}>{fmt[f](x)}</td><td {TDR}>{ca}</td>'
                 f'<td {TDR}>{fmt[f](y)}</td><td {TDR}>{cb}</td></tr>')
    return (f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%;'
            f'max-width:720px;font-family:Arial,sans-serif"><tr><th {TH}></th><th {THR}>This period</th>'
            f'<th {THR}>{html.escape(la)}</th><th {THR}>Change</th><th {THR}>{html.escape(lb)}</th>'
            f'<th {THR}>Change</th></tr>{body}</table>')


def _movers(lst, title):
    if not lst:
        return f'<p style="font-size:13px;color:{MUTED}">None.</p>'
    body = "".join(
        f'<tr><td {TD}>{html.escape(m["name"])}</td><td {TD} style="color:{MUTED}">'
        f'{html.escape(m["rep"].replace(" (Rushtons)", ""))}</td>'
        f'<td {TDR}>{_m(m["week"])}</td><td {TDR}>{_m(m["prev_week"])}</td>'
        f'<td {TDR}><b style="color:{GREEN if m["change"] > 0 else RED}">'
        f'{"+" if m["change"] > 0 else "−"}{_m(abs(m["change"]))}</b>'
        f'{" (" + m["status"] + ")" if m["status"] else ""}</td></tr>' for m in lst)
    return (f'<p style="font-size:13px;font-weight:600;margin:10px 0 2px;font-family:Arial,sans-serif">'
            f'{title}</p><table cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
            f'width:100%;max-width:720px;font-family:Arial,sans-serif"><tr><th {TH}>Customer</th>'
            f'<th {TH}>Manager</th><th {THR}>This week</th><th {THR}>Last week</th>'
            f'<th {THR}>Change</th></tr>{body}</table>')


def _split(rows, first_col, limit=None):
    rows = rows[:limit] if limit else rows
    body = "".join(
        f'<tr><td {TD}>{html.escape(r["name"].replace(" (Rushtons)", ""))}</td>'
        f'<td {TDR}>{_m(r["week"])}</td><td {TDR}>{_chg(r["week_vs_ly_pct"])}</td>'
        f'<td {TDR}>{_m(r["mtd"])}</td><td {TDR}>{_chg(r["mtd_vs_prev_pct"])}</td>'
        f'<td {TDR}>{_chg(r["mtd_vs_ly_pct"])}</td>'
        f'<td {TDR}>{"—" if r["mtd_gp_pct"] is None else str(r["mtd_gp_pct"]) + "%"}</td></tr>'
        for r in rows)
    return (f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%;'
            f'max-width:720px;font-family:Arial,sans-serif"><tr><th {TH}>{first_col}</th>'
            f'<th {THR}>Week</th><th {THR}>vs last year</th><th {THR}>Month to date</th>'
            f'<th {THR}>vs last month</th><th {THR}>vs last year</th><th {THR}>GP % (MTD)</th>'
            f'</tr>{body}</table>')


def render(f: dict) -> str:
    p, t = f["periods"], f["totals"]
    mtd_note = ""
    dd = (p["mtd"]["delivery_days"], p["prev_mtd"]["delivery_days"], p["ly_mtd"]["delivery_days"])
    if len(set(dd)) > 1:
        mtd_note = (f'<p style="font-size:12px;color:{MUTED};font-family:Arial,sans-serif">'
                    f'Delivery days: {dd[0]} this month to date, {dd[1]} in the same dates last '
                    f'month, {dd[2]} last year — the average delivery is the fairer read.</p>')
    return f"""<div style="font-family:Arial,sans-serif;color:#141414;max-width:760px">
<p style="font-size:14px">Hi all,</p>
<p style="font-size:14px">Here is the sales update for the week of <b>{p["week"]["label"]}</b>.</p>
{{{{SUMMARY}}}}
{_h("The week at a glance")}
<p style="font-size:12px;color:{MUTED}">{p["week"]["label"]} against the week before
({p["prev_week"]["label"]}) and the same week last year ({p["ly_week"]["label"]}).</p>
{_kpi_table(t["week"], t["prev_week"], t["ly_week"], "Last week", "Same week last year")}
{_h("Biggest movers this week")}
<p style="font-size:12px;color:{MUTED}">Customers whose sales changed most against last week, in &#163;.</p>
{_movers(f["movers_up"], "Up")}
{_movers(f["movers_down"], "Down")}
{_h("Month to date")}
<p style="font-size:12px;color:{MUTED}">{p["mtd"]["label"]} against the same dates last month
({p["prev_mtd"]["label"]}) and last year ({p["ly_mtd"]["label"]}).</p>
{_kpi_table(t["mtd"], t["prev_mtd"], t["ly_mtd"], "Same dates last month", "Same dates last year")}
{mtd_note}
{_h("By account manager")}
{_split(f["by_manager"], "Account manager")}
{_h("By category")}
{_split(f["by_category"], "Category")}
<p style="font-size:11px;color:{MUTED};margin-top:24px;line-height:1.5">Net sales are after
discounts and credits; gross profit (cash margin) is net sales minus cost of goods. Weeks run
Monday to Sunday; "same week last year" is the matching Monday&#8211;Sunday 52 weeks earlier.
Accounts are shown under their current account manager for every period. Source: Fresho
invoice lines (reconciled with Fresho's Sales Totals and Gross Profit Margin report).</p>
</div>"""


def write(sunday: date) -> tuple[str, str]:
    facts = build(sunday)
    out = config.EXPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"sales_update_{sunday}.json"
    hp = out / f"sales_update_{sunday}.html"
    jp.write_text(json.dumps(facts, indent=1, default=str), encoding="utf-8")
    hp.write_text(render(facts), encoding="utf-8")
    return str(hp), str(jp)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python sales_email.py <week-ending Sunday YYYY-MM-DD>")
    sun = date.fromisoformat(sys.argv[1])
    if sun.weekday() != 6:
        sys.exit(f"{sun} is not a Sunday")
    for path in write(sun):
        print(path)
