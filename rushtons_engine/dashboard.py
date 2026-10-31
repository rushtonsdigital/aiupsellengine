"""Commercial dashboard — an interactive, self-contained page of the four plays.

Regenerated each weekly run from the reporting layer, then republished to the
same Artifact URL so the client always opens fresh numbers at a stable link.

Three things worth knowing before editing this file:

1. Self-contained by necessity. The Artifact sandbox blocks every external host,
   so there is no CDN charting library — charts are plain SVG emitted from
   Python. Same reason the page cannot query Supabase live; it is rebuilt weekly
   instead, which matches the weekly ingest cadence exactly.

2. The page can save assignments, action states and one-tap logs back into
   itself (the `artifact` capability), so they are shared with everyone who
   opens the link. To publish a new version the page must reproduce its own
   HTML, so the markup below is a self-reproducing template: FULL_SRC carries
   the __TEMPLATE__ and __STATE__ markers and embeds a copy of itself, markers
   intact, for the next round. Substitution order matters — state first, then
   template — in BOTH Python and the page's JS; see build().

3. Reads only pre-aggregated summary tables and the interactions ledger
   (~100ms each). Never v_order_lines, which scans every order line (~9s).

Layout: a section nav — left rail on desktop, horizontal scroll bar on phones —
switching between Actions / Protect / Recover / Grow / Manage, with the KPI
tiles always visible and deep-linkable via location.hash.

Order-based measures are VOLUME (orders, order lines). £ sales and gross profit
come from Fresho's GP-margin report, per account per month only (no product or
category split) - see reporting.refresh_values.
Outreach drafts are written by Claude against the rushtons-comms skill and read
from output/outreach_drafts.json; Python never invents customer-facing words.
"""

import html
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path

import config
import help_section
import query

TARGETABLE = list(config.TARGETABLE_CATEGORIES)

log = logging.getLogger(__name__)

OUT_NAME = "commercial_dashboard.html"
# Rushton's logo (client-supplied SVG), recoloured to currentColor so it
# follows the theme accent in light and dark mode.
LOGO_SVG = (Path(__file__).resolve().parent / "assets" / "rushtons_logo.svg").read_text(
    encoding="utf-8")
DRAFTS_NAME = "outreach_drafts.json"

# Rushton's brand (rushtonsgreengrocer.co.uk): green #007946, pistachio
# #DDE0BB, cream #FFF7EE. GREEN carries data series; GOLD/SILVER mean tiers.
GREEN = "#007946"
GOLD, SILVER = "#c98500", "#8a9199"
AMBER, RED, GREY = "#c98500", "#d03b3b", "#898781"

RAMP = [(91, "#004F2D", "#FFF7EE"), (76, "#007946", "#FFF7EE"),
        (56, "#4D9E78", "#06301C"), (31, "#9CC7AE", "#06301C"),
        (11, "#D3E5D6", "#004F2D"), (0, "#F1F4E6", "#004F2D")]

HEAT_COLS = [
    ("Fruits", "Fruit"), ("Herbs", "Herbs"), ("Salads", "Salads"),
    ("Vegetables", "Veg"), ("Dry Stores & Non Food", "Dry st"),
    ("Frozen Produce", "Frozen"), ("Dairy and Chilled", "Dairy"),
    ("Tomatoes", "Tomato"), ("Potatoes", "Potato"),
    ("Micros, Leaves & Flowers", "Micros"), ("Exotic Fruit & Veg", "Exotic"),
    ("Mushroom", "Mushrm"), ("Italian", "Italian"),
    ("Baby Vegetables", "Baby veg"), ("Prep Vegetables", "Prep veg"),
    ("Prep Fruit & Juices", "Prep frt"),
]
HEAT_ROWS = ["Restaurants", "Pubs", "Bars", "Hotels", "Corporate Catering"]

_WEEK_BOUNDS = """
with bounds as (
  select max(week_start) - interval '7 day' as last_complete from customer_week_metrics)
"""

Q_DECLINE = _WEEK_BOUNDS + """,
recent as (select customer_code, sum(order_count) o from customer_week_metrics, bounds
  where week_start > (select last_complete from bounds) - interval '28 day'
    and week_start <= (select last_complete from bounds) group by 1),
prior as (select customer_code, sum(order_count) o from customer_week_metrics, bounds
  where week_start > (select last_complete from bounds) - interval '56 day'
    and week_start <= (select last_complete from bounds) - interval '28 day' group by 1)
select h.customer_code, h.customer_name, h.venue_type, h.size_band, h.sales_rep,
       p.o as prior_orders, r.o as recent_orders,
       round(100.0*(r.o - p.o)/nullif(p.o,0)) as pct_change
from prior p
join recent r on r.customer_code = p.customer_code
join v_customer_health h on h.customer_code = p.customer_code
where h.activity_status in ('active_regular','active_adhoc')
  and h.size_band in ('gold','silver') and p.o >= 6
  and coalesce(h.prestige,'') <> 'Excluded'
  and (r.o - p.o)/nullif(p.o,0)::numeric <= -0.35
order by (p.o - r.o) desc
"""

Q_WINBACK = """
with vol as (select customer_code, sum(line_count) as total_lines
             from customer_category_metrics group by 1)
select h.customer_code, h.customer_name, h.venue_type, h.size_band, h.sales_rep,
       h.days_since_last_order, coalesce(v.total_lines, 0) as total_lines
from v_customer_health h
left join vol v on v.customer_code = h.customer_code
where h.activity_status in ('lapsed','long_lapsed')
  and h.size_band in ('gold','silver') and coalesce(h.prestige,'') <> 'Excluded'
order by (h.size_band = 'gold') desc, v.total_lines desc nulls last
"""

_PEER_BASE = """
with active as (
  select customer_code, customer_name, venue_type, size_band, sales_rep, prestige
  from v_customer_health
  where activity_status in ('active_regular','active_adhoc') and venue_type is not null
    and venue_type not in ('Unknown','Manufacturing','Internal/Non-customer')),
peers as (select venue_type, count(*) n from active group by 1 having count(*) >= 10),
buys as (select a.venue_type, ccm.category, count(distinct a.customer_code) buyers
  from active a join customer_category_metrics ccm on ccm.customer_code = a.customer_code
  group by 1,2),
norm as (select p.venue_type, b.category, p.n, round(100.0*b.buyers/p.n) pct
  from peers p join buys b on b.venue_type = p.venue_type
  where b.category in (select category from v_targetable_categories))
"""

Q_PEER_GAPS = _PEER_BASE + """
select a.customer_code, a.customer_name, a.venue_type, a.sales_rep, n.category,
       n.pct as peer_pct, n.n as peer_n
from norm n
join active a on a.venue_type = n.venue_type
left join customer_category_metrics c
       on c.customer_code = a.customer_code and c.category = n.category
where c.customer_code is null and n.pct >= 85 and a.size_band = 'gold'
  and coalesce(a.prestige,'') <> 'Excluded'
order by n.pct desc, a.customer_name
"""

Q_PEER_TOTAL = _PEER_BASE + """
select count(*) as gaps, count(distinct a.customer_code) as accounts
from norm n
join active a on a.venue_type = n.venue_type
left join customer_category_metrics c
       on c.customer_code = a.customer_code and c.category = n.category
where c.customer_code is null and n.pct >= 70 and a.size_band in ('gold','silver')
  and coalesce(a.prestige,'') <> 'Excluded'
"""

Q_HEATMAP = """
with active as (select customer_code, venue_type from v_customer_health
   where activity_status in ('active_regular','active_adhoc')
     and venue_type in ('Restaurants','Pubs','Bars','Hotels','Corporate Catering')),
tot as (select venue_type, count(*) n from active group by 1)
select t.venue_type, c.category, t.n,
       coalesce(round(100.0*count(distinct m.customer_code)/t.n),0) as pct
from tot t
cross join v_targetable_categories c
left join active a on a.venue_type = t.venue_type
left join customer_category_metrics m
       on m.customer_code = a.customer_code and m.category = c.category
group by t.venue_type, c.category, t.n
"""

Q_REPS = """
select coalesce(h.sales_rep,'(unassigned)') as rep, count(*) as accounts,
       sum(case when h.activity_status in ('lapsed','long_lapsed') then 1 else 0 end) as lapsed,
       round(100.0*sum(case when h.activity_status in ('lapsed','long_lapsed') then 1 else 0 end)
             / nullif(count(*),0)) as lapsed_pct,
       round(avg(g.gaps),1) as avg_open_gaps
from v_customer_health h
left join (select customer_code, count(*) gaps from v_account_gaps group by 1) g
       on g.customer_code = h.customer_code
where coalesce(h.size_band,'') in ('gold','silver')
group by 1 having count(*) >= 5 order by accounts desc
"""

Q_ASOF = "select max(delivery_date) as as_of from orders"
Q_WEEKS = "select distinct week_start from customer_week_metrics order by week_start"

# The Accounts directory: every customer except deliberate exclusions — the
# mini-CRM account list, enriched with the Fresho master's CRM fields.
# Volume = lifetime order lines (never revenue).
Q_DIRECTORY = """
with vol as (select customer_code, sum(line_count) as total_lines
             from customer_category_metrics group by 1)
select h.customer_code, h.customer_name, h.venue_type, h.size_band,
       h.activity_status, h.sales_rep, h.last_order_date, h.first_seen,
       h.days_since_last_order, coalesce(v.total_lines, 0) as total_lines,
       c.legal_entity_name, c.delivery_address, c.billing_address,
       c.delivery_run_code, c.payment_term_days, c.pricing_level,
       c.internal_notes, c.group_affiliation, c.account_stage, h.prestige,
       c.picking_instructions, c.negotiated_prices_group, c.rebate
from v_customer_health h
join customers c on c.customer_code = h.customer_code
left join vol v on v.customer_code = h.customer_code
where coalesce(h.activity_status, '') <> 'excluded'
order by h.customer_name
"""

Q_WEEKLY_ALL = """
select customer_code, week_start, order_count
from customer_week_metrics order by customer_code, week_start
"""

Q_CATS_ALL = """
select customer_code, category, line_count, last_bought
from customer_category_metrics order by customer_code, line_count desc
"""

Q_LEADS = """
select source_id, name, business, email, phone, lead_source, analysis, notes,
       submitted_at, status, rating, type, details, excluded_reason,
       matched_customer_code, match_method, intake, heard_about, page_url,
       received_at, status_changed_at, owner, next_follow_up, lost_reason,
       verdict, verdict_reason, verdict_source, urgency, postcode, website,
       research
from leads order by submitted_at desc nulls last, received_at desc nulls last
"""

Q_LEAD_ACTS = """
select source_id, occurred_at, kind, stage, note, follow_up_on, actor
from lead_activities order by occurred_at
"""


def _codes_sql(codes):
    return ",".join("'" + c.replace("'", "''") + "'" for c in codes)


def e(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _rows(sql, limit=500):
    return query.run_sql(sql, limit=limit)["rows"]


def _ramp(pct):
    for floor, fill, ink in RAMP:
        if pct >= floor:
            return fill, ink
    return RAMP[-1][1], RAMP[-1][2]


def _sparkline(series) -> str:
    """13-week order pattern. Zero-filled: a week with no orders has no row in
    customer_week_metrics at all, and dropping it would hide the worst weeks."""
    if not series:
        return ""
    w, h, pad = 210, 34, 3
    mx = max(v for _, v in series) or 1
    n = len(series)
    step = (w - 2 * pad) / max(n - 1, 1)

    def pt(i, v):
        return pad + i * step, h - pad - (v / mx) * (h - 2 * pad)

    pts = [pt(i, v) for i, (_, v) in enumerate(series)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pad},{h-pad} " + line + f" {pad+(n-1)*step:.1f},{h-pad}"
    lx, ly = pts[-1]
    zeros = "".join(
        f'<circle cx="{pt(i,0)[0]:.1f}" cy="{h-pad}" r="2" fill="{RED}"/>'
        for i, (_, v) in enumerate(series) if v == 0)
    return (f'<svg viewBox="0 0 {w} {h}" class="spark" role="img" '
            f'aria-label="Weekly order counts over the last {n} weeks">'
            f'<polygon points="{area}" fill="{GREEN}" opacity="0.13"/>'
            f'<polyline points="{line}" fill="none" stroke="{GREEN}" '
            f'stroke-width="1.6" stroke-linejoin="round"/>{zeros}'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="{GREEN}"/></svg>')


def _svg_dumbbell(rows) -> str:
    if not rows:
        return "<p class='empty'>No declining accounts this week.</p>"
    rows = rows[:12]
    lab_w, pad_r, row_h = 190, 54, 34
    w, h = 700, len(rows) * row_h + 34
    top = 22
    mx = max(float(r["prior_orders"]) for r in rows) or 1

    def x(v):
        return lab_w + (float(v) / mx) * (w - lab_w - pad_r)

    p = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
         f'aria-label="Accounts still active whose order counts have fallen sharply">']
    for gv in (0, 0.5, 1):
        gx = lab_w + gv * (w - lab_w - pad_r)
        p.append(f'<line x1="{gx:.0f}" y1="{top-14}" x2="{gx:.0f}" y2="{h-14}" class="grid"/>')
        p.append(f'<text x="{gx:.0f}" y="{h-2}" class="tick" text-anchor="middle">'
                 f'{round(gv*mx)}</text>')
    for i, r in enumerate(rows):
        y = top + i * row_h
        pct = float(r["pct_change"])
        col = RED if pct <= -60 else AMBER
        xp, xr = x(r["prior_orders"]), x(r["recent_orders"])
        p.append(f'<text x="{lab_w-10}" y="{y+4}" class="lab" text-anchor="end">'
                 f'{e(r["customer_name"])[:30]}</text>')
        p.append(f'<line x1="{xr:.0f}" y1="{y}" x2="{xp:.0f}" y2="{y}" '
                 f'stroke="{col}" stroke-width="3"/>')
        p.append(f'<circle cx="{xp:.0f}" cy="{y}" r="6" fill="{GREY}"/>')
        p.append(f'<circle cx="{xr:.0f}" cy="{y}" r="7" fill="{col}"/>')
        p.append(f'<text x="{w-pad_r+8}" y="{y+4}" class="tick">{int(pct)}%</text>')
    p.append("</svg>")
    return "".join(p)


def _svg_scatter(rows) -> str:
    rows = [r for r in rows if r["days_since_last_order"] is not None][:12]
    if not rows:
        return "<p class='empty'>No lapsed high-value accounts.</p>"
    w, h, ml, mb, mt, mr = 700, 330, 56, 42, 16, 130
    mxd = max(float(r["days_since_last_order"]) for r in rows) * 1.1 or 1
    mxl = max(float(r["total_lines"]) for r in rows) * 1.15 or 1

    def px(v):
        return ml + (float(v) / mxd) * (w - ml - mr)

    def py(v):
        return h - mb - (float(v) / mxl) * (h - mb - mt)

    p = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
         f'aria-label="Lapsed high-value accounts by days since last order against volume">']
    for gv in (0, 0.25, 0.5, 0.75, 1):
        gy = py(gv * mxl)
        p.append(f'<line x1="{ml}" y1="{gy:.0f}" x2="{w-mr}" y2="{gy:.0f}" class="grid"/>')
        p.append(f'<text x="{ml-8}" y="{gy+4:.0f}" class="tick" text-anchor="end">'
                 f'{round(gv*mxl)}</text>')
    for gv in (0, 0.5, 1):
        gx = px(gv * mxd)
        p.append(f'<text x="{gx:.0f}" y="{h-mb+18}" class="tick" text-anchor="middle">'
                 f'{round(gv*mxd)}d</text>')
    for r in rows:
        cx, cy = px(r["days_since_last_order"]), py(r["total_lines"])
        col = GOLD if r["size_band"] == "gold" else SILVER
        p.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="7" fill="{col}"/>')
        p.append(f'<text x="{cx+11:.0f}" y="{cy+4:.0f}" class="pt">'
                 f'{e(r["customer_name"])[:22]}</text>')
    p.append(f'<text x="{ml}" y="{h-6}" class="axis">days since last order &#8594; more urgent</text>')
    p.append(f'<text transform="translate(14,{h-mb}) rotate(-90)" class="axis">'
             f'order lines &#8594; more valuable</text>')
    p.append("</svg>")
    return "".join(p)


def _html_heatmap(matrix, ns) -> str:
    cells = ['<div class="heat">', "<div></div>"]
    for _, short in HEAT_COLS:
        cells.append(f'<div class="hcol"><span>{e(short)}</span></div>')
    for row in HEAT_ROWS:
        if row not in matrix:
            continue
        cells.append(f'<div class="hrow">{e(row)}'
                     f'<span class="muted"> ({ns.get(row,0)})</span></div>')
        for cat, _ in HEAT_COLS:
            pct = int(matrix[row].get(cat, 0))
            fill, ink = _ramp(pct)
            cells.append(f'<div class="hcell" style="background:{fill};color:{ink}" '
                         f'title="{e(row)} &#8212; {e(cat)}: {pct}%">{pct}</div>')
    cells.append("</div>")
    return "".join(cells)


def _svg_reps(rows) -> str:
    if not rows:
        return ""
    lab_w, row_h, w = 150, 40, 700
    h = len(rows) * row_h + 20
    mx = max(int(r["accounts"]) for r in rows) or 1
    p = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
         f'aria-label="Accounts held and lapsed accounts by sales rep">']
    for i, r in enumerate(rows):
        y = 14 + i * row_h
        acc, lap = int(r["accounts"]), int(r["lapsed"])
        bw = (acc / mx) * (w - lab_w - 150)
        lw = (lap / mx) * (w - lab_w - 150)
        p.append(f'<text x="{lab_w-10}" y="{y+13}" class="lab" text-anchor="end">'
                 f'{e(r["rep"])[:22]}</text>')
        p.append(f'<rect x="{lab_w}" y="{y}" width="{bw:.0f}" height="18" rx="4" '
                 f'fill="{GREEN}" opacity="0.35"/>')
        p.append(f'<rect x="{lab_w}" y="{y}" width="{lw:.0f}" height="18" rx="4" fill="{RED}"/>')
        p.append(f'<text x="{lab_w+bw+10:.0f}" y="{y+13}" class="tick">'
                 f'{acc} accounts &#183; {lap} lapsed ({int(r["lapsed_pct"] or 0)}%) &#183; '
                 f'{r["avg_open_gaps"]} avg gaps</text>')
    p.append("</svg>")
    return "".join(p)


Q_VALUES = """
select customer_code, sales_12m, gp_pct_12m, sales_last_month, gp_pct_last_month,
       sales_prev3_avg, trend_pct, sales_mtd, aov_last_month, series
from customer_value_metrics
"""

Q_VALUE_MONTHS = """
select month, max(period_end) as period_end, sum(net_sales) as net,
       sum(gross_profit) as gp
from customer_month_values group by month order by month
"""


def _num(v, nd=0):
    return None if v is None else round(float(v), nd)


def _money_short(v: float) -> str:
    """£1.12M / £874k / £950 - for tiles and chart labels."""
    if abs(v) >= 1e6:
        return f"&#163;{v / 1e6:.2f}M"
    if abs(v) >= 1e4:
        return f"&#163;{v / 1e3:.0f}k"
    return f"&#163;{v:,.0f}"


def _load_values() -> tuple[dict, list]:
    """£ per account ({code: {...}}) and the business's month totals. Empty when
    no GP-margin export has been loaded yet - the page then hides every £."""
    try:
        per = {r["customer_code"]: {
            "s": _num(r["sales_12m"]), "g": _num(r["gp_pct_12m"], 1),
            "lm": _num(r["sales_last_month"]), "gl": _num(r["gp_pct_last_month"], 1),
            "p3": _num(r["sales_prev3_avg"]), "tr": _num(r["trend_pct"]),
            "mtd": _num(r["sales_mtd"]), "aov": _num(r["aov_last_month"]),
            "ser": [[x[0], round(x[1]), round(x[2]), round(x[3]) if len(x) > 3 else 0]
                    for x in json.loads(r["series"] or "[]")],
        } for r in _rows(Q_VALUES, limit=5000)}
        months = [{
            "m": r["month"].strftime("%Y-%m") if hasattr(r["month"], "strftime")
                 else str(r["month"])[:7],
            "end": str(r["period_end"]), "net": round(float(r["net"] or 0)),
            "gp": round(float(r["gp"] or 0)),
        } for r in _rows(Q_VALUE_MONTHS, limit=100)]
    except Exception:
        log.exception("value layer unavailable - dashboard shows volume only")
        return {}, []
    for m in months:   # month to date when the export stops before month end
        y, mo = map(int, m["m"].split("-"))
        m["mtd"] = m["end"] < str(date(y + mo // 12, mo % 12 + 1, 1) - timedelta(days=1))
    # Per-account figures (sales_12m...) cover the latest 12 months; the page's
    # labels and totals must use the same window. The chart keeps every month
    # so this year's month sits beside last year's.
    return per, months


def _svg_monthly(months: list) -> str:
    """Net sales per month (bars) with GP% above each; month to date hatched."""
    if not months:
        return ""
    w, h, top, base, lab_h = 700, 230, 34, 190, 18
    mx = max(m["net"] for m in months) or 1
    slot = (w - 20) / len(months)
    bw = min(46, slot * 0.62)
    names = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    p = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label='
         f'"Net sales and gross profit margin by month">',
         '<defs><pattern id="mtd" width="6" height="6" patternUnits="userSpaceOnUse" '
         'patternTransform="rotate(45)"><rect width="3" height="6" '
         f'fill="{GREEN}" opacity="0.45"/></pattern></defs>']
    for i, m in enumerate(months):
        x = 10 + i * slot + (slot - bw) / 2
        bh = (m["net"] / mx) * (base - top)
        fill = "url(#mtd)" if m["mtd"] else GREEN
        gp_pct = 100.0 * m["gp"] / m["net"] if m["net"] else 0
        p.append(f'<rect x="{x:.0f}" y="{base - bh:.0f}" width="{bw:.0f}" '
                 f'height="{bh:.0f}" rx="3" fill="{fill}"/>')
        p.append(f'<text x="{x + bw / 2:.0f}" y="{base - bh - 18:.0f}" class="tick" '
                 f'text-anchor="middle">{_money_short(m["net"])}</text>')
        p.append(f'<text x="{x + bw / 2:.0f}" y="{base - bh - 5:.0f}" class="lab" '
                 f'text-anchor="middle">{gp_pct:.1f}%</text>')
        label = names[int(m["m"][5:]) - 1] + (" to " + m["end"][8:] if m["mtd"] else "")
        if m["m"][:4] != months[-1]["m"][:4]:
            label += " &#8217;" + m["m"][2:4]
        p.append(f'<text x="{x + bw / 2:.0f}" y="{base + lab_h}" class="tick" '
                 f'text-anchor="middle">{label}</text>')
    p.append("</svg>")
    return "".join(p)


Q_PROFILE = """
select customer_code, orders_7d, orders_28d, orders_total, last_order_date,
       last_order_lines, weekday_orders from customer_order_profile
"""

Q_TOP_PRODUCTS = """
select customer_code, product_code, line_count, last_bought from (
  select m.*, row_number() over (partition by customer_code
                                 order by line_count desc, product_code) rn
  from customer_product_metrics m) x
where rn <= 10
"""

Q_STOPPED_PRODUCTS = """
select customer_code, product_code, line_count, last_bought from (
  select m.*, row_number() over (partition by customer_code
                                 order by line_count desc, product_code) rn
  from customer_product_metrics m
  where m.line_count >= 3 and m.last_bought < date '{cutoff}') x
where rn <= 6
"""


def _load_history(as_of) -> tuple[dict, dict]:
    """Order-history overview per account: counts, last order, usual order
    days, top 10 products and lines they've stopped buying (3+ times before,
    nothing in 28 days). Product names go in a shared index to keep the page
    small. ({code: {...}}, {product_index: name})."""
    try:
        names = {r["product_code"]: r["product_name"] for r in
                 _rows("select product_code, product_name from products", limit=10000)}
        idx: dict[str, int] = {}
        pid = lambda pc: idx.setdefault(pc, len(idx))
        hist = {}
        for r in _rows(Q_PROFILE, limit=5000):
            hist[r["customer_code"]] = {
                "w": r["orders_7d"], "m": r["orders_28d"], "t": r["orders_total"],
                "last": str(r["last_order_date"] or ""), "lastn": r["last_order_lines"],
                "days": json.loads(r["weekday_orders"] or "[]"), "top": [], "stop": []}
        for r in _rows(Q_TOP_PRODUCTS, limit=20000):
            h = hist.get(r["customer_code"])
            if h is not None:
                h["top"].append([pid(r["product_code"]), r["line_count"], str(r["last_bought"])])
        cutoff = as_of - timedelta(days=28)
        for r in _rows(Q_STOPPED_PRODUCTS.format(cutoff=cutoff), limit=20000):
            h = hist.get(r["customer_code"])
            if h is not None:
                h["stop"].append([pid(r["product_code"]), r["line_count"], str(r["last_bought"])])
        return hist, {i: names.get(pc, pc) for pc, i in idx.items()}
    except Exception:
        log.exception("order-history summary unavailable - account pages skip it")
        return {}, {}


def _load_venue_notes() -> dict:
    """The live web research the drafting step records on each pitched venue
    (drafts_<date>.json -> customer_review), newest per account. {code: [date, text]}."""
    notes = {}
    for path in sorted(config.EXPORT_DIR.glob("drafts_????-??-??.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        accounts = data.get("accounts", data) if isinstance(data, dict) else {}
        for code, a in (accounts.items() if isinstance(accounts, dict) else []):
            review = isinstance(a, dict) and (a.get("customer_review") or "").strip()
            if review:
                notes[code] = [path.stem[7:], review]
    return notes


def _load_drafts() -> dict:
    path = config.EXPORT_DIR / DRAFTS_NAME
    if not path.exists():
        log.warning("no %s - dashboard will show 'draft pending' for every account",
                    DRAFTS_NAME)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("drafts", {})
    except (ValueError, OSError):
        log.exception("could not read %s - continuing without drafts", DRAFTS_NAME)
        return {}


def _load_contacts() -> dict:
    """Every contact person, grouped per account, for the mini-CRM Accounts
    section and the send buttons. {code: [{n, l, e, p}]} — name, label(role),
    email, phone. Tolerates the table not existing yet."""
    try:
        rows = _rows("select customer_code, name, label, email, phone "
                     "from customer_contacts order by customer_code, id",
                     limit=5000)
    except Exception:
        log.warning("customer_contacts not readable yet - no contacts on page")
        return {}
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["customer_code"], []).append({
            "n": r["name"] or "", "l": r["label"] or "",
            "e": r["email"] or "", "p": r["phone"] or ""})
    return out


def _load_timeline() -> dict:
    """Ledger entries per account (ALL accounts — the directory shows any
    account's history), newest first, capped per account. Tolerates the table
    not existing yet (pre-init_db local builds)."""
    try:
        rows = _rows("select customer_code, occurred_at, kind, summary, actor "
                     "from interactions order by occurred_at desc", limit=5000)
    except Exception:
        log.warning("interactions table not readable yet - timeline empty")
        return {}
    out: dict[str, list] = {}
    for r in rows:
        lst = out.setdefault(r["customer_code"], [])
        if len(lst) < 8:
            lst.append({"at": str(r["occurred_at"]), "kind": r["kind"],
                        "summary": r["summary"] or "", "actor": r["actor"] or ""})
    return out


def _load_weekly_all(all_weeks) -> dict:
    """Compact weekly order counts for EVERY account, for the JS-drawn
    sparkline on account pages: {code: [count per week index]}."""
    idx = {w: i for i, w in enumerate(all_weeks)}
    out: dict[str, list] = {}
    try:
        rows = _rows(Q_WEEKLY_ALL, limit=5000)
    except Exception:
        return {}
    for r in rows:
        series = out.setdefault(r["customer_code"], [0] * len(all_weeks))
        i = idx.get(str(r["week_start"]))
        if i is not None:
            series[i] = int(r["order_count"])
    return out


def _load_cats_all() -> tuple[list, dict]:
    """Per-account category volumes for the account page (top categories and
    derived gaps): master category list + {code: [[cat_index, lines]]}."""
    try:
        rows = _rows(Q_CATS_ALL, limit=10000)
    except Exception:
        return [], {}
    master, midx, out = [], {}, {}
    for r in rows:
        cat = r["category"]
        if cat not in midx:
            midx[cat] = len(master)
            master.append(cat)
        out.setdefault(r["customer_code"], []).append(
            [midx[cat], int(r["line_count"])])
    return master, out


def _research_sources(research) -> list:
    """Source links the AI cited (research JSON), for the lead page."""
    try:
        srcs = json.loads(research or "{}").get("sources") or []
    except (ValueError, AttributeError):
        return []
    return [u for u in srcs if isinstance(u, str) and u.startswith("http")][:10]


def _lead_team() -> list:
    """Everyone with customer accounts in Fresho, as first names ("Ben
    (Rushtons)" -> "Ben"), so the list follows Fresho with no upkeep."""
    try:
        rows = _rows("select distinct sales_rep from customers "
                     "where sales_rep is not null", limit=500)
    except Exception:
        return []
    names = {re.sub(r"\s*\(.*?\)\s*$", "", r["sales_rep"]).strip() for r in rows}
    return sorted(n for n in names if n and n not in config.LEAD_TEAM_NOT_PEOPLE)


def _load_leads() -> list:
    """Leads for the dashboard: fields, pipeline state and activity history
    (acts = [at, kind, stage, note, due, actor], oldest first)."""
    try:
        rows = _rows(Q_LEADS, limit=2000)
    except Exception:
        log.warning("leads table not readable yet - leads pane empty")
        return []
    acts = {}
    try:
        for a in _rows(Q_LEAD_ACTS, limit=50000):
            acts.setdefault(a["source_id"], []).append([
                a["occurred_at"].isoformat() if a["occurred_at"] else "",
                a["kind"], a["stage"] or "", a["note"] or "",
                str(a["follow_up_on"] or ""), a["actor"] or ""])
    except Exception:
        log.warning("lead_activities not readable yet - no lead history")
    out = []
    for r in rows:
        out.append({
            "sid": r["source_id"], "name": r["name"] or "",
            "biz": r["business"] or "", "email": r["email"] or "",
            "phone": r["phone"] or "", "src": r["lead_source"] or "",
            "date": str(r["submitted_at"] or ""), "status": r["status"] or "",
            "rating": r["rating"], "type": r["type"] or "",
            "excl": r["excluded_reason"] or "",
            "matched": r["matched_customer_code"] or "",
            "method": r["match_method"] or "",
            "analysis": (r["analysis"] or "").strip(),
            "notes": (r["notes"] or "")[:500],
            "details": (r["details"] or "")[:500],
            "web": r["intake"] == "web",
            "heard": r["heard_about"] or "",
            "page": r["page_url"] or "",
            "recv": r["received_at"].isoformat() if r["received_at"] else "",
            "chg": (r["status_changed_at"].isoformat()
                    if r["status_changed_at"] else ""),
            "owner": r["owner"] or "",
            "next": str(r["next_follow_up"] or ""),
            "lost": r["lost_reason"] or "",
            "acts": acts.get(r["source_id"], []),
            "verdict": r["verdict"] or "",
            "vwhy": r["verdict_reason"] or "",
            "vsrc": r["verdict_source"] or "",
            "urg": r["urgency"] or "",
            "pc": r["postcode"] or "",
            "site": r["website"] or "",
            "srcs": _research_sources(r["research"]),
        })
    return out


def _suggest_lead_matches(leads_data, directory, contacts) -> None:
    """Detect leads that look like ACTUAL customers regardless of sheet status
    (the sheet's Status goes stale — a Discarded lead can quietly become an
    account). Adds sug=[code, score, how] to unmatched, non-excluded leads.
    Suggestions only — a human confirms on the page; nothing auto-links here.
    """
    import re as _re
    from difflib import SequenceMatcher
    from leads import _norm_name, NAME_MATCH_THRESHOLD

    by_norm = [(d["c"], _norm_name(d["n"])) for d in directory if d["n"]]
    cemail, cphone = {}, {}
    for code, people in (contacts or {}).items():
        for p in people:
            if p.get("e"):
                cemail[p["e"].lower()] = code
            if p.get("p"):
                digits = _re.sub(r"\D", "", p["p"])[-10:]
                if digits:
                    cphone[digits] = code
    for l in leads_data:
        if l["matched"] or l["excl"]:
            continue
        em = (l["email"] or "").lower()
        ph = _re.sub(r"\D", "", l["phone"] or "")[-10:]
        if em and em in cemail:
            l["sug"] = [cemail[em], 1.0, "contact email"]
            continue
        if ph and ph in cphone:
            l["sug"] = [cphone[ph], 1.0, "contact phone"]
            continue
        target = _norm_name(l["biz"] or l["name"] or "")
        if len(target) < 4:
            continue
        best = max(((c, SequenceMatcher(None, target, nn).ratio())
                    for c, nn in by_norm), key=lambda x: x[1], default=(None, 0))
        if best[1] >= NAME_MATCH_THRESHOLD:
            l["sug"] = [best[0], round(best[1], 2), "name match"]


def _build_actions(decline, winback, gaps_named):
    """One suggested-action card per account, deduplicated across the plays.

    Priority is deliberately transparent, not a score: gold before silver;
    within a tier, decliners by orders lost (protect first), then win-backs by
    history (recover), then peer-gap upsells by peer adoption (grow). Every
    card carries its 'because' lines. Done/dismissed state lives in the page's
    saved state keyed by customer_code, so it survives weekly rebuilds while
    the account still qualifies.
    """
    cards: dict[str, dict] = {}

    def card(code, name, tier, venue, rep):
        return cards.setdefault(code, {
            "id": code, "name": name, "tier": tier or "",
            "venue": venue or "Unknown", "rep": rep or "",
            "verb": "", "reasons": []})

    for r in decline[:12]:
        c = card(r["customer_code"], r["customer_name"], r["size_band"],
                 r["venue_type"], r["sales_rep"])
        c["verb"] = "Reach out"
        c["_sort"] = (0, -(int(r["prior_orders"]) - int(r["recent_orders"])))
        c["reasons"].append(
            f'orders {int(r["prior_orders"])} → {int(r["recent_orders"])} '
            f'({int(r["pct_change"])}%) in 4 weeks — still active')
    live = [r for r in winback if r["days_since_last_order"] is not None][:12]
    for r in live:                       # same accounts as the win-back board
        c = card(r["customer_code"], r["customer_name"], r["size_band"],
                 r["venue_type"], r["sales_rep"])
        if not c["verb"]:
            c["verb"] = "Reactivate"
            c["_sort"] = (1, -int(r["total_lines"]))
        c["reasons"].append(
            f'{int(r["days_since_last_order"])} days quiet — '
            f'was {int(r["total_lines"])} order lines')
    for g in gaps_named:
        c = card(g["customer_code"], g["customer_name"], "gold",
                 g["venue_type"], g["sales_rep"])
        if not c["verb"]:
            c["verb"] = "Upsell"
            c["_sort"] = (2, -int(g["peer_pct"]))
        c["reasons"].append(
            f'not buying {g["category"]} — {int(g["peer_pct"])}% of '
            f'{int(g["peer_n"])} peer {g["venue_type"].lower()} do')

    ordered = sorted(cards.values(),
                     key=lambda c: (0 if c["tier"] == "gold" else 1,
                                    c.get("_sort", (9, 0))))
    for c in ordered:
        c.pop("_sort", None)
    # Cap the pane at a workable list; the plays hold the full detail.
    return ordered[:18], max(0, len(ordered) - 18)


def _drilldown(codes, all_weeks, as_of):
    """Per-account detail: zero-filled weekly series, categories they have
    stopped buying, and their open peer-relevant gaps."""
    if not codes:
        return {}, {}, {}
    inlist = _codes_sql(codes)

    weekly = {}
    for r in _rows(f"select customer_code, week_start, order_count "
                   f"from customer_week_metrics where customer_code in ({inlist})",
                   limit=4000):
        weekly.setdefault(r["customer_code"], {})[str(r["week_start"])] = int(r["order_count"])
    series = {c: [(w[5:], weekly.get(c, {}).get(w, 0)) for w in all_weeks] for c in codes}

    stopped = {}
    for r in _rows(f"select customer_code, category, last_bought, line_count "
                   f"from customer_category_metrics where customer_code in ({inlist}) "
                   f"and last_bought < date '{as_of}' - 28 and line_count >= 3 "
                   f"order by last_bought", limit=2000):
        stopped.setdefault(r["customer_code"], []).append(
            [r["category"], str(r["last_bought"]), (as_of - r["last_bought"]).days])

    gaps = {}
    for r in _rows(f"select customer_code, category from v_account_gaps "
                   f"where customer_code in ({inlist})", limit=3000):
        gaps.setdefault(r["customer_code"], []).append(r["category"])
    return series, stopped, gaps


def build(as_of: date | None = None, state: dict | None = None) -> str:
    decline = _rows(Q_DECLINE)
    winback = _rows(Q_WINBACK)
    gaps_named = _rows(Q_PEER_GAPS)
    totals = _rows(Q_PEER_TOTAL)[0]
    heat = _rows(Q_HEATMAP, limit=200)
    reps = _rows(Q_REPS)
    if as_of is None:
        as_of = _rows(Q_ASOF)[0]["as_of"]
    all_weeks = [str(r["week_start"]) for r in _rows(Q_WEEKS, limit=200)]

    dec, win = decline[:12], [r for r in winback
                              if r["days_since_last_order"] is not None][:12]
    codes = [r["customer_code"] for r in dec] + [r["customer_code"] for r in win]
    series, stopped, gapmap = _drilldown(codes, all_weeks, as_of)
    drafts = _load_drafts()

    accounts = {}
    for r in dec:
        c = r["customer_code"]
        d = drafts.get(c, {})
        accounts[c] = {
            "name": r["customer_name"], "venue": r["venue_type"] or "Unknown",
            "tier": r["size_band"], "rep": r["sales_rep"] or "(unassigned)",
            "kind": "decline", "prior": int(r["prior_orders"]),
            "recent": int(r["recent_orders"]), "pct": int(r["pct_change"]),
            "spark": _sparkline(series.get(c, [])),
            "stopped": stopped.get(c, [])[:5], "gaps": gapmap.get(c, [])[:6],
            "wa": d.get("whatsapp", ""), "es": d.get("email_subject", ""),
            "eb": d.get("email_body", ""),
        }
    for r in win:
        c = r["customer_code"]
        if c in accounts:
            continue
        d = drafts.get(c, {})
        accounts[c] = {
            "name": r["customer_name"], "venue": r["venue_type"] or "Unknown",
            "tier": r["size_band"], "rep": r["sales_rep"] or "(unassigned)",
            "kind": "winback", "days": int(r["days_since_last_order"]),
            "lines": int(r["total_lines"]), "spark": _sparkline(series.get(c, [])),
            "stopped": stopped.get(c, [])[:5], "gaps": gapmap.get(c, [])[:6],
            "wa": d.get("whatsapp", ""), "es": d.get("email_subject", ""),
            "eb": d.get("email_body", ""),
        }

    matrix, ns = {}, {}
    for r in heat:
        matrix.setdefault(r["venue_type"], {})[r["category"]] = int(r["pct"])
        ns[r["venue_type"]] = int(r["n"])

    rep_names = sorted({r["rep"] for r in reps if r["rep"] != "(unassigned)"})
    actions, actions_more = _build_actions(decline, winback, gaps_named)
    contacts = _load_contacts()

    directory = [{
        "c": r["customer_code"], "n": r["customer_name"],
        "v": r["venue_type"] or "Unknown", "t": r["size_band"] or "",
        "s": r["activity_status"] or "", "r": r["sales_rep"] or "",
        "last": str(r["last_order_date"] or ""),
        "first": str(r["first_seen"] or ""),
        "ds": (int(r["days_since_last_order"])
               if r["days_since_last_order"] is not None else None),
        "l": int(r["total_lines"]),
        "legal": r["legal_entity_name"] or "",
        "addr": r["delivery_address"] or "",
        "baddr": r["billing_address"] or "",
        "run": r["delivery_run_code"] or "",
        "terms": (int(r["payment_term_days"])
                  if r["payment_term_days"] is not None else None),
        "pricing": r["pricing_level"] or "",
        "inotes": r["internal_notes"] or "",
        "grp": r["group_affiliation"] or "",
        "stage": r["account_stage"] or "",
        "x": 1 if r["prestige"] == "Excluded" else 0,
        "vip": 1 if r["prestige"] == "VIP" else 0,
        "pick": r["picking_instructions"] or "",
        "npg": r["negotiated_prices_group"] or "",
        "rebate": (r["rebate"] if r["rebate"] not in (None, "", "0", "0.0") else ""),
    } for r in _rows(Q_DIRECTORY, limit=1000)]

    values, all_months = _load_values()
    value_months = all_months[-12:]
    history, product_names = _load_history(as_of)
    leads_data = _load_leads()
    _suggest_lead_matches(leads_data, directory, contacts)

    cats_master, cats = _load_cats_all()
    data = {
        "asOf": str(as_of), "reps": rep_names, "accounts": accounts,
        "declineOrder": [r["customer_code"] for r in dec],
        "winbackOrder": [r["customer_code"] for r in win],
        "actions": actions, "actionsMore": actions_more,
        "timeline": _load_timeline(),
        "contacts": contacts,
        "directory": directory,
        "weeks": [w[5:] for w in all_weeks],
        "weekly": _load_weekly_all(all_weeks),
        "catsMaster": cats_master,
        "cats": cats,
        "targetable": TARGETABLE,
        "leads": leads_data,
        "leadStages": [list(st) for st in config.LEAD_STAGES],
        "leadFollowUp": config.LEAD_FOLLOW_UP_DAYS,
        "leadQuietDays": config.LEAD_QUIET_DAYS,
        "leadLost": config.LEAD_LOST_REASONS,
        "leadTeam": _lead_team(),
        "leadVerdicts": config.LEAD_VERDICTS,
        "leadVerdictDue": config.LEAD_VERDICT_DUE_DAYS,
        "value": values,
        "valueMonths": value_months,
        "hist": history,
        "prodNames": product_names,
        "venueNotes": _load_venue_notes(),
    }

    complete = [m for m in value_months if not m["mtd"]]
    if complete:
        lm = complete[-1]
        names = ("January February March April May June July August September "
                 "October November December").split()
        lm_name = names[int(lm["m"][5:]) - 1]
        kpi_sales = (f'<button class="kpi" data-tab="sales"><span class="n">'
                     f'{_money_short(lm["net"])}</span> <span class="l">net sales in '
                     f'{lm_name} &#183; GP {100.0 * lm["gp"] / lm["net"]:.1f}%</span></button>')
        value_note = (f"&#163; sales and gross profit from Fresho to "
                      f"{e(value_months[-1]['end'])}")
    else:
        kpi_sales, value_note = "", "no &#163; data loaded yet - order volume only"

    gap_rows = "".join(
        f"<tr><td>{e(g['customer_name'])}</td><td>{e(g['venue_type'])}</td>"
        f"<td>{e(g['category'])}</td>"
        f"<td class='num'>{int(g['peer_pct'])}% of {int(g['peer_n'])}</td>"
        f"<td>{e(g['sales_rep'])}</td></tr>" for g in gaps_named[:12])

    frag = (FRAG_SRC
            .replace("{{ASOF}}", e(as_of))
            .replace("{{N_DECLINE}}", str(len(decline)))
            .replace("{{N_WINBACK}}", str(len(winback)))
            .replace("{{N_GAPS}}", str(int(totals["gaps"])))
            .replace("{{N_GAP_ACCOUNTS}}", str(int(totals["accounts"])))
            .replace("{{DECLINE_CHART}}", _svg_dumbbell(decline))
            .replace("{{WINBACK_CHART}}", _svg_scatter(winback))
            .replace("{{HEATMAP}}", _html_heatmap(matrix, ns))
            .replace("{{GAP_ROWS}}", gap_rows or
                     "<tr><td colspan='5'>No gold-tier peer gaps.</td></tr>")
            .replace("{{REPS_CHART}}", _svg_reps(reps))
            .replace("{{KPI_SALES}}", kpi_sales)
            .replace("{{LOGO}}", LOGO_SVG)
            .replace("{{VALUE_NOTE}}", value_note)
            .replace("{{SALES_CHART}}", _svg_monthly(all_months))
            .replace("{{HELP}}", help_section.help_html(
                value_months, str(_rows("select min(delivery_date) as d from orders")[0]["d"])))
            .replace("{{DATA}}", _safe_json(data)))

    # The page republishes itself to save state, so it carries a copy of its
    # own source with BOTH markers still intact for the next round.
    full = HEAD_SRC + frag + TAIL_SRC
    # Order matters: substitute the state first, then embed the template last.
    # Embedding first would inject a copy containing __STATE__, and the state
    # replace would then clobber the marker inside that copy - so the page could
    # save once and never again. The JS in FRAG_SRC substitutes in this same
    # order for the same reason. count=1 on both as belt and braces.
    return (frag
            .replace("__STATE__", _safe_json(state or {"assign": {}}), 1)
            .replace("__TEMPLATE__", _safe_json(full), 1))


def _safe_json(obj) -> str:
    """JSON safe to embed inside a <script> tag."""
    return (json.dumps(obj, default=str)
            .replace("</", "<\\/")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


HEAD_SRC = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rushton's Commercial Dashboard</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?\
family=Open+Sans:wght@400;500;600;700&display=swap">
</head><body>"""

TAIL_SRC = "</body></html>"

FRAG_SRC = r"""<title>Rushton's Commercial Dashboard</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700&display=swap">
<style>
:root {
  --bg:#ffffff; --surface:#ffffff; --raise:#FFF7EE; --ink:#141414; --muted:#55595F;
  --line:rgba(0,0,0,.12); --grid:#E4E6CC; --accent:#007946; --on-accent:#ffffff;
  --brand2:#DDE0BB;
  --sans:"Open Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;
  --serif:var(--sans);
  --mono:var(--sans);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#0E1712; --surface:#15211A; --raise:#1B2A21; --ink:#F2F1E8; --muted:#A6AE9F;
    --line:rgba(255,255,255,.14); --grid:#2A3A2F; --accent:#7CCB9F; --on-accent:#0E1712;
    --brand2:#3B4A35;
  }
}
:root[data-theme="dark"] {
  --bg:#0E1712; --surface:#15211A; --raise:#1B2A21; --ink:#F2F1E8; --muted:#A6AE9F;
  --line:rgba(255,255,255,.14); --grid:#2A3A2F; --accent:#7CCB9F; --on-accent:#0E1712;
  --brand2:#3B4A35;
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font-family:var(--sans);
  line-height:1.6; -webkit-font-smoothing:antialiased; }
.wrap { max-width:1080px; margin:0 auto; padding:2.2rem 1.25rem 4rem; }
body { border-top:6px solid var(--accent); }
.brandmark { color:var(--accent); margin:0 0 .9rem; }
.brandmark .logo { display:block; height:34px; width:auto; }
h1 { font-family:var(--serif); font-size:28px; font-weight:700; margin:0 0 .3rem;
  color:var(--accent);
  letter-spacing:-.01em; text-wrap:balance; }
h2 { font-family:var(--serif); font-size:20px; font-weight:700; margin:0 0 .35rem;
  text-wrap:balance; }
.sub { color:var(--muted); font-size:14px; margin:0 0 1.6rem; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:12px; margin:0 0 1.6rem; }
.kpi { background:var(--surface); border:.5px solid var(--line); border-radius:12px;
  border-top:4px solid var(--brand2);
  padding:1rem 1.1rem; cursor:pointer; text-align:left; font:inherit;
  color:inherit; }
.kpi:hover { border-color:var(--accent); }
.kpi:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.kpi .n { font-family:var(--mono); font-size:30px; font-weight:700; color:var(--accent);
  letter-spacing:-.03em; font-variant-numeric:tabular-nums; line-height:1.2; }
.kpi .l { display:block; font-size:13px; color:var(--muted); line-height:1.45; }
.layout { display:block; }
.nav { display:flex; gap:6px; overflow-x:auto; position:sticky; top:0;
  background:var(--bg); padding:.6rem 0; z-index:5; margin:0 0 1rem; }
.nav button { font:inherit; font-size:13px; padding:7px 14px; border-radius:20px;
  border:.5px solid var(--line); background:var(--surface); color:var(--ink);
  cursor:pointer; white-space:nowrap; }
.nav button:hover { border-color:var(--accent); }
.nav button:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
.nav button[aria-current="true"] { background:var(--accent); color:var(--on-accent);
  border-color:var(--accent); font-weight:600; }
.ng { font-family:var(--mono); font-size:10px; letter-spacing:.12em;
  color:var(--muted); align-self:center; white-space:nowrap; padding:0 4px; }
@media (min-width: 960px) {
  .ng { align-self:flex-start; margin:.9rem 0 .15rem; padding:0 2px; }
  .nav .ng:first-child { margin-top:0; }
}
@media (min-width: 960px) {
  .layout { display:grid; grid-template-columns:168px minmax(0,1fr); gap:2rem; }
  .nav { flex-direction:column; position:sticky; top:1.2rem; align-self:start;
    overflow:visible; background:none; padding:0; margin:0; }
  .nav button { text-align:left; border-radius:10px; }
}
.section { display:none; }
.section.on { display:block; }
.play { background:var(--surface); border:.5px solid var(--line); border-radius:12px;
  padding:1.4rem 1.5rem; margin:0 0 1.5rem; }
.tag { display:inline-block; font-family:var(--mono); font-size:11px; font-weight:700;
  letter-spacing:.11em; color:var(--accent); margin-bottom:.5rem; }
.lede { font-size:14px; color:var(--muted); margin:0 0 1.1rem; }
h3 { font-size:14px; font-weight:600; margin:1.6rem 0 .3rem; }
details.help { border-bottom:.5px solid var(--line); }
details.help summary { cursor:pointer; padding:.75rem 0; font-weight:600; font-size:15px;
  color:var(--ink); }
details.help summary:hover { color:var(--accent); }
details.help summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.helpbody { font-size:14px; line-height:1.65; padding:0 0 .9rem; max-width:72ch; }
.helpbody ul { padding-left:1.2rem; margin:.3rem 0; }
.helpbody li { margin:.25rem 0; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
.up { color:var(--accent); } .down { color:#d03b3b; }
.mbars { display:flex; align-items:flex-end; gap:3px; height:54px; margin:.3rem 0 .2rem; }
.mbars span { flex:1 1 0; background:var(--accent); opacity:.55; border-radius:2px 2px 0 0;
  min-height:1px; }
.mbars span.mtd { opacity:.25; }
.mlabs { display:flex; gap:3px; font-size:10px; color:var(--muted);
  font-family:var(--mono); }
.mlabs span { flex:1 1 0; text-align:center; }
.note2 { background:var(--raise); border:.5px solid var(--line); border-radius:8px;
  padding:.6rem .8rem; font-size:13px; white-space:pre-wrap; overflow-wrap:anywhere; }
.days { display:flex; gap:4px; margin:.2rem 0 .6rem; }
.days span { flex:0 0 auto; font-family:var(--mono); font-size:11px; padding:2px 7px;
  border-radius:6px; border:.5px solid var(--line); color:var(--muted); }
.days span.on { background:var(--ink); color:var(--bg); border-color:var(--ink); }
.scroll { overflow-x:auto; }
.chart { width:100%; height:auto; display:block; }
.chart .lab { font-size:11px; fill:var(--ink); font-family:var(--sans); }
.chart .tick { font-size:10px; fill:var(--muted); font-family:var(--mono);
  font-variant-numeric:tabular-nums; }
.chart .pt { font-size:10px; fill:var(--muted); font-family:var(--sans); }
.chart .axis { font-size:10px; fill:var(--muted); font-family:var(--sans);
  letter-spacing:.03em; }
.chart .grid { stroke:var(--grid); stroke-width:1; }
.heat { display:grid; grid-template-columns:132px repeat(16,minmax(30px,1fr));
  gap:2px; align-items:end; min-width:640px; }
.hcol { height:70px; display:flex; align-items:flex-end; justify-content:center; }
.hcol span { writing-mode:vertical-rl; transform:rotate(180deg); font-size:10px;
  color:var(--muted); white-space:nowrap; }
.hrow { padding-right:8px; text-align:right; font-size:11px; }
.hcell { height:32px; display:flex; align-items:center; justify-content:center;
  border-radius:3px; font-size:11px; font-family:var(--mono);
  font-variant-numeric:tabular-nums; }
.muted { color:var(--muted); }
table { width:100%; border-collapse:collapse; font-size:13px; margin-top:.5rem;
  min-width:560px; }
th { text-align:left; font-weight:500; color:var(--muted); font-size:11px;
  font-family:var(--mono); letter-spacing:.05em;
  border-bottom:.5px solid var(--line); padding:7px 8px; }
td { padding:7px 8px; border-bottom:.5px solid var(--line); }
td.num { white-space:nowrap; font-family:var(--mono);
  font-variant-numeric:tabular-nums; }
.note { font-size:13px; color:var(--muted); border-left:2px solid var(--grid);
  padding-left:.9rem; margin-top:1rem; }
.legend { display:flex; align-items:center; gap:8px; font-size:11px;
  color:var(--muted); margin-top:10px; flex-wrap:wrap; }
.sw { width:24px; height:11px; display:inline-block; }
footer { color:var(--muted); font-size:12px; margin-top:2rem; }
.empty { color:var(--muted); font-size:14px; }
.list { margin-top:1.2rem; border-top:.5px solid var(--line); }
.row { border-bottom:.5px solid var(--line); }
.rowhead { display:flex; align-items:center; gap:10px; width:100%; padding:11px 4px;
  background:none; border:0; color:inherit; font:inherit; text-align:left;
  cursor:pointer; }
.rowhead:hover { background:var(--raise); }
.rowhead:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
.chev { color:var(--muted); font-size:11px; width:12px; flex:none;
  transition:transform .15s; }
.row.open .chev, .card.open .chev { transform:rotate(90deg); }
.rname { flex:1; font-size:14px; min-width:0; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.pill { font-family:var(--mono); font-size:11px; padding:2px 7px; border-radius:20px;
  white-space:nowrap; font-variant-numeric:tabular-nums; }
.owner { font-size:11px; color:var(--muted); white-space:nowrap; }
.detail { display:none; padding:.2rem 4px 1.3rem 26px; }
.row.open .detail { display:block; }
.spark { width:210px; height:34px; display:block; margin:.4rem 0 .8rem; }
.facts { display:flex; flex-wrap:wrap; gap:6px 18px; font-size:12.5px;
  color:var(--muted); margin-bottom:.9rem; }
.facts b { color:var(--ink); font-weight:500; font-family:var(--mono);
  font-variant-numeric:tabular-nums; }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin:.3rem 0 .9rem; }
.chip { font-size:11.5px; padding:3px 9px; border-radius:20px;
  border:.5px solid var(--line); color:var(--muted); }
.dh { font-size:11px; font-family:var(--mono); letter-spacing:.06em;
  color:var(--muted); margin:.9rem 0 .3rem; }
.draft { background:var(--raise); border:.5px solid var(--line); border-radius:8px;
  padding:.75rem .9rem; font-size:13px; white-space:pre-wrap; }
.acts { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:.7rem; }
button.act, select.act, a.act, input.act { font:inherit; font-size:12.5px;
  padding:6px 12px; border-radius:8px; border:.5px solid var(--line);
  background:var(--surface); color:var(--ink); cursor:pointer;
  text-decoration:none; display:inline-block; }
button.act:hover, select.act:hover, a.act:hover { border-color:var(--accent); }
button.act:focus-visible, select.act:focus-visible, a.act:focus-visible,
input.act:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
.said { font-size:12px; color:var(--muted); }
.card { border:.5px solid var(--line); border-radius:10px; padding:.7rem .9rem;
  margin:0 0 .6rem; background:var(--raise); }
.crow { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.cverb { font-family:var(--mono); font-size:10.5px; letter-spacing:.09em;
  color:var(--accent); text-transform:uppercase; flex:none; }
.cname { background:none; border:0; padding:0; font:inherit; font-size:14px;
  font-weight:500; color:inherit; cursor:pointer; text-align:left;
  display:inline-flex; align-items:center; gap:6px; min-width:0; }
.cname:hover { text-decoration:underline; }
.cname:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.cbtns { margin-left:auto; display:flex; gap:6px; }
.cwhy { font-size:12.5px; color:var(--muted); margin-top:.3rem; line-height:1.5; }
.cbody { display:none; padding-top:.4rem; }
.card.open .cbody { display:block; }
.mini { padding:2px 8px; font-size:11px; }
#resolved-wrap { margin-top:.8rem; color:var(--muted); font-size:13px; }
#resolved-wrap summary { cursor:pointer; }
#resolved .cwhy { margin:.35rem 0; }
.tl { border-left:2px solid var(--grid); margin:.2rem 0 .9rem; padding-left:.9rem;
  display:flex; flex-direction:column; gap:5px; }
.tlrow { display:flex; gap:10px; font-size:12.5px; align-items:baseline;
  flex-wrap:wrap; }
.tld { font-family:var(--mono); color:var(--muted); font-size:11px;
  white-space:nowrap; }
.tlk { font-weight:500; white-space:nowrap; }
.tlk.good { color:var(--accent); }
.tls { color:var(--muted); min-width:0; }
.pend { font-size:10.5px; color:var(--muted); opacity:.75; }
.noteinput { flex:1; min-width:180px; cursor:text; }
.status { font-family:var(--mono); font-size:10.5px; padding:2px 8px;
  border-radius:20px; white-space:nowrap; border:.5px solid var(--line);
  color:var(--muted); font-variant-numeric:tabular-nums; }
.status.sent { color:var(--ink); border-color:var(--ink); }
.status.good { color:var(--accent); border-color:var(--accent); font-weight:600; }
.comms { border:.5px solid var(--line); border-radius:10px; padding:.7rem .9rem;
  margin-top:.3rem; }
.ctabs { display:flex; gap:6px; margin-bottom:.6rem; }
.ctabs button { font:inherit; font-size:12px; padding:5px 14px; border-radius:20px;
  border:.5px solid var(--line); background:var(--surface); color:var(--muted);
  cursor:pointer; }
.ctabs button[aria-current="true"] { background:var(--ink); color:var(--bg);
  border-color:var(--ink); }
.ctabs button:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
.cpane { display:none; }
.cpane.on { display:block; }
.dirbar { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:.4rem; }
.drow { border-bottom:.5px solid var(--line); }
.dmeta { font-size:11.5px; color:var(--muted); white-space:nowrap; }
.contact { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:12.5px;
  align-items:baseline; padding:3px 0; }
.contact b { font-weight:500; }
.contact a { color:var(--accent); text-decoration:none; }
.contact a:hover { text-decoration:underline; }
.addform { display:flex; flex-wrap:wrap; gap:6px; margin-top:.5rem; }
.addform input { min-width:0; flex:1 1 120px; }
.agrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr));
  gap:10px; margin-top:.6rem; }
.acard { border:.5px solid var(--line); border-radius:10px; padding:.75rem .9rem;
  background:var(--raise); cursor:pointer; text-align:left; font:inherit;
  color:inherit; display:flex; flex-direction:column; gap:4px; min-width:0; }
.acard:hover { border-color:var(--accent); }
.acard:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.acard .an { font-weight:500; font-size:14px; line-height:1.35; }
.acard .am { font-size:11.5px; color:var(--muted); }
.acard .ar { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
.bk { background:none; border:0; padding:0; font:inherit; color:var(--accent);
  cursor:pointer; font-size:13px; }
.bk:hover { text-decoration:underline; }
.ahead { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap;
  margin-bottom:.2rem; }
.ahead h2 { margin:0; font-size:24px; }
.agridfacts { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  gap:8px 20px; margin:.8rem 0; font-size:13px; }
.agridfacts .k { color:var(--muted); font-size:11px; font-family:var(--mono);
  letter-spacing:.05em; }
.agridfacts .v { overflow-wrap:anywhere; }
.stars { color:#c98500; letter-spacing:1px; font-size:12px; white-space:nowrap; }
.lrow { border-bottom:.5px solid var(--line); }
.stale { color:#d03b3b; font-size:10.5px; font-family:var(--mono);
  white-space:nowrap; }
.excl { opacity:.55; }
.matchform { display:flex; flex-wrap:wrap; gap:6px; margin-top:.5rem; }
.matchform input { flex:1 1 220px; min-width:0; }
/* Summary cards belong to the home page (This week) only. */
body:not(.home) .topkpis { display:none; }
/* Phones: a compact bar with a menu button; the section list opens as a
   full-screen sheet. Desktop keeps the left rail (min-width:960px). */
.mbar { display:flex; align-items:center; gap:10px; position:sticky;
  top:env(safe-area-inset-top, 0px); z-index:6; background:var(--bg);
  padding:.55rem 0; margin:0 0 .9rem; border-bottom:.5px solid var(--line); }
.hamb { position:relative; display:inline-flex; align-items:center;
  justify-content:center; width:44px; height:44px; flex:none; border-radius:10px;
  border:.5px solid var(--line); background:var(--surface); color:var(--ink);
  cursor:pointer; }
.hamb:hover { border-color:var(--accent); }
.hamb:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.hdot { position:absolute; top:8px; right:8px; width:8px; height:8px;
  border-radius:50%; background:#d03b3b; }
.mcur { font-weight:600; font-size:16px; min-width:0; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.navtop { display:none; }
body.navopen { overflow:hidden; }
@media (max-width: 959px) {
  .nav { display:none; position:fixed; inset:0; z-index:50; margin:0;
    flex-direction:column; gap:4px; overflow-y:auto; background:var(--bg);
    padding:calc(env(safe-area-inset-top, 0px) + .8rem) 1.25rem
            calc(env(safe-area-inset-bottom, 0px) + 1.5rem); }
  .nav.open { display:flex; }
  .navtop { display:flex; align-items:center; justify-content:space-between;
    margin-bottom:.3rem; }
  .nav button { text-align:left; border-radius:10px; font-size:15px;
    padding:11px 14px; }
  .nav .navclose { width:44px; height:44px; padding:0; text-align:center;
    font-size:24px; line-height:1; }
  .ng { align-self:flex-start; margin:.8rem 0 .1rem; padding:0 2px; }
  body:not(.home) .sub { display:none; }
}
@media (min-width: 960px) { .mbar { display:none; } }
.navn { font-family:var(--mono); font-size:10.5px; margin-left:6px; padding:0 6px;
  border-radius:10px; background:#d03b3b; color:#fff; }
.navn:empty { display:none; }
.whoami { display:flex; align-items:center; gap:6px; margin-left:auto; }
.funnel { display:flex; flex-direction:column; gap:5px; margin:.3rem 0 .4rem; }
.frow { display:grid; grid-template-columns:120px minmax(0,1fr) 92px; gap:10px;
  align-items:center; font-size:12.5px; background:none; border:0; padding:2px 0;
  font:inherit; color:inherit; text-align:left; cursor:pointer; width:100%; }
.frow:hover .flab { color:var(--accent); }
.frow:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.ftrack { height:16px; background:var(--raise); border-radius:4px; overflow:hidden; }
.fbar { display:block; height:100%; background:var(--accent); opacity:.75; border-radius:4px; }
.fnum { font-family:var(--mono); font-variant-numeric:tabular-nums; color:var(--muted);
  white-space:nowrap; text-align:right; }
.fnum b { color:var(--ink); font-weight:600; }
.flost { font-size:12px; color:var(--muted); margin-top:.3rem; }
.lhead { display:flex; align-items:center; gap:10px; width:100%; padding:10px 4px;
  background:none; border:0; color:inherit; font:inherit; text-align:left;
  cursor:pointer; flex-wrap:wrap; }
.lhead:hover { background:var(--raise); }
.lhead:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
.lhead .rname { flex:1 1 180px; }
.lhead .dmeta { white-space:normal; min-width:0; overflow-wrap:anywhere; }
.due { font-family:var(--mono); font-size:10.5px; padding:2px 8px; border-radius:20px;
  white-space:nowrap; border:.5px solid var(--line); color:var(--muted); }
.due.over { color:#d03b3b; border-color:#d03b3b; font-weight:600; }
.due.today { color:#c98500; border-color:#c98500; font-weight:600; }
.stepper { display:flex; flex-wrap:wrap; gap:0; margin:.4rem 0 .2rem; }
.stepper button { font:inherit; font-size:12.5px; padding:7px 14px; cursor:pointer;
  border:.5px solid var(--line); background:var(--surface); color:var(--muted);
  margin-left:-.5px; }
.stepper button:first-child { border-radius:8px 0 0 8px; margin-left:0; }
.stepper button:last-child { border-radius:0 8px 8px 0; }
.stepper button.done { color:var(--ink); background:var(--raise); }
.stepper button[aria-current="true"] { background:var(--accent); color:var(--on-accent);
  border-color:var(--accent); font-weight:600; }
.stepper button.lost[aria-current="true"] { background:#898781; border-color:#898781;
  color:#fff; }
.stepper button:hover { border-color:var(--accent); position:relative; }
.stepper button:focus-visible { outline:2px solid var(--accent); outline-offset:1px;
  position:relative; }
.lpgrid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
  gap:6px 24px; }
.lbox { border:.5px solid var(--line); border-radius:10px; padding:.7rem .9rem;
  margin:.6rem 0; }
.lbox .dh { margin-top:0; }
input[type="date"].act { cursor:text; }
@media (prefers-reduced-motion: reduce) { .chev { transition:none; } }
</style>

<div class="wrap">
  <div class="brandmark">{{LOGO}}</div>
  <h1>Commercial dashboard</h1>
  <p class="sub">Growing value from existing accounts &#183; orders to {{ASOF}} &#183;
     {{VALUE_NOTE}}</p>

  <div class="kpis topkpis">
    {{KPI_SALES}}
    <button class="kpi" data-tab="protect"><span class="n">{{N_DECLINE}}</span>
      <span class="l">active accounts in silent decline</span></button>
    <button class="kpi" data-tab="recover"><span class="n">{{N_WINBACK}}</span>
      <span class="l">good accounts to win back</span></button>
    <button class="kpi" data-tab="grow"><span class="n">{{N_GAPS}}</span>
      <span class="l">peer gaps open</span></button>
    <button class="kpi" data-tab="grow"><span class="n">{{N_GAP_ACCOUNTS}}</span>
      <span class="l">accounts with a gap</span></button>
  </div>

  <div class="layout">
    <div class="mbar">
      <button class="hamb" id="hamb" aria-expanded="false" aria-controls="sitenav"
        aria-label="Open the menu">
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" fill="none"></path></svg>
        <span class="hdot" id="hdot" hidden></span>
      </button>
      <span class="mcur" id="mcur">This week</span>
    </div>
    <nav class="nav" id="sitenav" aria-label="Sections">
      <div class="navtop">
        <span class="mcur">Menu</span>
        <button class="navclose" data-navclose aria-label="Close the menu">&#215;</button>
      </div>
      <span class="ng">CUSTOMERS</span>
      <button data-tab="sales">Sales &amp; margin</button>
      <button data-tab="accounts">Accounts</button>
      <button data-tab="contacts">Contacts</button>
      <span class="ng">ACQUIRE</span>
      <button data-tab="leads">Leads</button>
      <button data-tab="leadrecs">Follow-ups<span class="navn" id="fu-count"></span></button>
      <span class="ng">GROW</span>
      <button data-tab="actions">This week</button>
      <button data-tab="protect">Protect</button>
      <button data-tab="recover">Recover</button>
      <button data-tab="grow">Peer gaps</button>
      <button data-tab="manage">Manage</button>
      <span class="ng">HELP</span>
      <button data-tab="help">Help &amp; definitions</button>
    </nav>

    <div class="content">

    <div class="section" id="sec-actions">
      <div class="play">
        <div class="tag">THIS WEEK</div>
        <h2>Suggested actions</h2>
        <p class="lede"><span id="actions-count">&#8230;</span> open actions
          &#8212; every recommended action this week, one card per account,
          across all plays.<br>Top card first: gold before silver, biggest
          losses first; each card says why it's here.<br>Expand a card to act
          &#8212; copy, send, log &#8212; then mark it Done. Saved for
          everyone.</p>
        <div id="cards"></div>
        <details id="resolved-wrap" hidden><summary></summary>
          <div id="resolved"></div></details>
      </div>
    </div>

    <div class="section" id="sec-protect">
      <div class="play">
        <div class="tag">PLAY 1 &#183; PROTECT</div>
        <h2>Silent decline</h2>
        <p class="lede">Active accounts whose orders have dropped 35%+ over
          four weeks.<br>Grey dot = previous 4 weeks, coloured dot = last 4;
          longer line = bigger loss; red = down 60%+.<br>Call this week and ask
          what changed &#8212; these accounts look healthy on every other
          report.</p>
        <div class="scroll">{{DECLINE_CHART}}</div>
        <div class="list" id="list-decline"></div>
      </div>
    </div>

    <div class="section" id="sec-recover">
      <div class="play">
        <div class="tag">PLAY 2 &#183; RECOVER</div>
        <h2>Win-back priority</h2>
        <p class="lede">Good accounts that have stopped ordering.<br>Right =
          silent longer, up = worth more; start top-right.<br>Send each
          account's ready-made "we've missed you" message, then log it.</p>
        <div class="scroll">{{WINBACK_CHART}}</div>
        <div class="list" id="list-winback"></div>
      </div>
    </div>

    <div class="section" id="sec-grow">
      <div class="play">
        <div class="tag">PLAY 3 &#183; GROW</div>
        <h2>Peer gaps &#8212; who's buying elsewhere</h2>
        <p class="lede">Categories an account doesn't buy from us that most
          similar venues do.<br>Dark = we sell it well, pale = we don't;
          compare within a venue type only.<br>Ask the named accounts "who
          supplies this today?" &#8212; pale columns are category campaigns.</p>
        <div class="scroll">{{HEATMAP}}</div>
        <div class="legend"><span>whitespace</span>
          <span class="sw" style="background:#F1F4E6"></span>
          <span class="sw" style="background:#D3E5D6"></span>
          <span class="sw" style="background:#9CC7AE"></span>
          <span class="sw" style="background:#4D9E78"></span>
          <span class="sw" style="background:#007946"></span>
          <span class="sw" style="background:#004F2D"></span>
          <span>saturated</span><span>&#183; % of active accounts buying</span></div>
        <div class="scroll"><table>
          <tr><th>Gold account</th><th>Type</th><th>Not buying</th>
              <th>Peer adoption</th><th>Rep</th></tr>
          {{GAP_ROWS}}
        </table></div>
      </div>
    </div>

    <div class="section" id="sec-help">
      <div class="play">
        <div class="tag">HELP</div>
        <h2>How to read this dashboard</h2>
        <p class="lede">Definitions and rules behind every number, for anyone who
          wasn't part of building it. Click a question to open it.</p>
        {{HELP}}
      </div>
    </div>

    <div class="section" id="sec-sales">
      <div class="play">
        <div class="tag">SALES &amp; MARGIN</div>
        <h2>Sales and gross profit</h2>
        <p class="lede">Net sales (after credits) and gross profit from Fresho's
          Sales Totals and Gross Profit Margin report, loaded month by month.
          Figures are per account &#8212; Fresho's report has no product or
          category split, so margin by category isn't available yet.</p>
        <div class="kpis" id="sales-kpis"></div>
        <div class="scroll">{{SALES_CHART}}</div>
        <h3>Biggest &#163; falls last month</h3>
        <p class="said">Active gold/silver accounts whose last full month fell
          25%+ below their previous three-month average (and were worth
          &#163;1,000+ a month). Upsell-excluded accounts are left out.</p>
        <div class="scroll" id="sales-falls"></div>
        <h3>Top accounts by sales</h3>
        <div class="scroll" id="sales-top"></div>
        <h3>Lowest margin</h3>
        <p class="said">Accounts with &#163;10k+ sales in the period, lowest
          gross profit % first &#8212; worth a pricing look.</p>
        <div class="scroll" id="sales-lowgp"></div>
        <h3>By account manager</h3>
        <div class="scroll" id="sales-reps"></div>
      </div>
    </div>

    <div class="section" id="sec-accounts">
      <div class="play">
        <div class="tag">ACCOUNTS</div>
        <h2>All accounts</h2>
        <p class="lede">Every customer, with contacts and the full interaction
          history. Add a contact here and it saves for everyone (and unlocks the
          send buttons). &#163; is net sales from Fresho's GP report.</p>
        <div class="dirbar">
          <input class="act noteinput" id="dirq" type="search"
                 placeholder="Search accounts&#8230;" aria-label="Search accounts">
          <select class="act" id="dirstatus" aria-label="Filter by status">
            <option value="">All statuses</option>
            <option value="active_regular">Active regular</option>
            <option value="active_adhoc">Active ad hoc</option>
            <option value="lapsed">Lapsed</option>
            <option value="long_lapsed">Long lapsed</option>
            <option value="temporarily_closed">Temporarily closed</option>
          </select>
          <select class="act" id="dirtier" aria-label="Filter by tier">
            <option value="">All tiers</option>
            <option value="gold">Gold</option>
            <option value="silver">Silver</option>
            <option value="bronze">Bronze</option>
          </select>
          <select class="act" id="dirsort" aria-label="Sort accounts">
            <option value="">Sort: name</option>
            <option value="sales">Sort: sales (&#163;)</option>
          </select>
        </div>
        <p class="said" id="dircount"></p>
        <div class="agrid" id="dirlist"></div>
      </div>
    </div>

    <div class="section" id="sec-account">
      <div class="play" id="acct-page"></div>
    </div>

    <div class="section" id="sec-contacts">
      <div class="play">
        <div class="tag">CUSTOMERS &#183; CONTACTS</div>
        <h2>All contacts</h2>
        <p class="lede">Every contact person on file, across all accounts. Add
          new ones from an account's page; email opens your mail app, phone
          opens WhatsApp.</p>
        <div class="dirbar">
          <input class="act noteinput" id="conq" type="search"
                 placeholder="Search contacts or accounts&#8230;"
                 aria-label="Search contacts">
        </div>
        <p class="said" id="concount"></p>
        <div class="list" id="conlist"></div>
      </div>
    </div>

    <div class="section" id="sec-leadrecs">
      <div class="play">
        <div class="tag">ACQUIRE &#183; FOLLOW-UPS</div>
        <h2>Follow-ups</h2>
        <p class="lede">Every open lead has a next follow-up: a date someone
          set, or the stage's rule (new leads within a day, contacted leads
          chased after 4 days, qualified leads kept warm weekly, samples
          checked 3 days after the box). Log what you did on the lead's page
          and its next date moves on.</p>
        <div class="dirbar">
          <label class="said whoami">You are
            <select class="act" data-whoami aria-label="Who are you"></select></label>
          <label class="said" style="display:flex;align-items:center;gap:6px;">
            <input type="checkbox" id="fumine"> only leads I own or nobody owns
          </label>
        </div>
        <div id="lrlist"></div>
      </div>
    </div>

    <div class="section" id="sec-leads">
      <div class="play">
        <div class="tag">ACQUIRE &#183; LEADS</div>
        <h2>Lead pipeline</h2>
        <p class="lede">Website enquiries arrive here straight from the forms,
          researched and rated by AI the moment they come in (the sales team
          gets the same write-up by email). Open a lead to move it through the
          funnel, log calls and notes, and set the next follow-up. Won leads
          link to their Fresho account and pull orders and &#163; sales.</p>
        <div class="kpis" id="lead-kpis"></div>
        <div class="dh">FUNNEL &#8212; HOW FAR LEADS GET (commercial leads, excluding test/personal)</div>
        <div id="lead-funnel"></div>
        <div class="dirbar" style="margin-top:1rem">
          <input class="act noteinput" id="leadq" type="search"
            placeholder="Search business, contact, email…" aria-label="Search leads">
          <select class="act" id="leadstatus" aria-label="Filter by stage"></select>
          <select class="act" id="leadowner" aria-label="Filter by owner"></select>
          <select class="act" id="leadverdict" aria-label="Filter by AI verdict"></select>
          <select class="act" id="leadrating" aria-label="Filter by rating">
            <option value="0">Any rating</option>
            <option value="4">&#9733;4 and up</option>
            <option value="3">&#9733;3 and up</option>
          </select>
          <label class="said" style="display:flex;align-items:center;gap:6px;">
            <input type="checkbox" id="leadexcl"> show excluded (test/personal)
          </label>
          <label class="said whoami">You are
            <select class="act" data-whoami aria-label="Who are you"></select></label>
        </div>
        <div class="list" id="leadlist"></div>
        <details class="help" style="margin-top:1rem">
          <summary>Conversion by AI rating</summary>
          <div class="scroll" id="lead-ratings"></div>
        </details>
        <p class="note">Excluded rows (test entries, personal sample-box asks,
          internal addresses) stay out of every number above.</p>
      </div>
    </div>

    <div class="section" id="sec-lead">
      <div class="play" id="lead-page"></div>
    </div>

    <div class="section" id="sec-manage">
      <div class="play">
        <div class="tag">PLAY 4 &#183; MANAGE</div>
        <h2>Portfolio health by rep</h2>
        <p class="lede">Each rep's account load and how much of it is
          lapsing.<br>Bar = accounts held, red = the lapsed share; portfolios
          differ, so compare with care.<br>Use for workload balancing and
          coaching pairs &#8212; never as a league table.</p>
        <div class="scroll">{{REPS_CHART}}</div>
      </div>
    </div>

    </div>
  </div>

  <footer>Rushton's &#183; generated from the reporting layer &#183; order counts
    are volume; &#163; sales and gross profit are per account per month from
    Fresho's GP-margin report &#183; rebuilt with each weekly run.
    Outreach drafts follow the client tone guidelines; the tone doc specifies
    WhatsApp, so email variants need sign-off before use.</footer>
</div>

<script id="dash-data" type="application/json">{{DATA}}</script>
<script id="dash-state" type="application/json">__STATE__</script>
<script>
(function(){
  var TEMPLATE = __TEMPLATE__;
  var DATA = JSON.parse(document.getElementById('dash-data').textContent);
  var STATE = JSON.parse(document.getElementById('dash-state').textContent);
  if(!STATE.assign) STATE.assign = {};
  if(!STATE.actions) STATE.actions = {};
  if(!STATE.log) STATE.log = [];
  if(!STATE.contacts) STATE.contacts = [];
  if(!STATE.leadMatch) STATE.leadMatch = {};
  if(!STATE.leadLog) STATE.leadLog = [];
  var openCards = {};
  var esc = function(s){ return String(s==null?'':s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;'); };
  var cssEsc = window.CSS && CSS.escape ? CSS.escape : function(s){ return s; };
  var KINDLBL = { assigned:'Assigned', whatsapp_sent:'WhatsApp sent',
    email_sent:'Email sent', note:'Note', email_in:'Email received',
    email_out:'Email sent', ordered_again:'Ordered again ✓' };
  function ownerOf(code){ return STATE.assign[code] || ''; }

  function contactsFor(code){
    var base = (DATA.contacts && DATA.contacts[code] ? DATA.contacts[code] : []).slice();
    STATE.contacts.forEach(function(x){
      if(x.code === code)
        base.push({n:x.name||'', l:x.label||'', e:x.email||'', p:x.phone||'', pend:1});
    });
    return base;
  }
  function firstEmail(code){
    var cs = contactsFor(code);
    for(var i=0;i<cs.length;i++) if(cs[i].e) return cs[i].e;
    return '';
  }
  function firstPhone(code){
    var cs = contactsFor(code);
    for(var i=0;i<cs.length;i++) if(cs[i].p) return cs[i].p;
    return '';
  }
  function mergedEvents(code){
    var tl = (DATA.timeline && DATA.timeline[code] ? DATA.timeline[code] : []).slice();
    STATE.log.forEach(function(l){ if(l.code === code)
      tl.push({at:l.at, kind:l.kind, summary:l.note||'', actor:l.actor||'', p:1}); });
    tl.sort(function(x,y){ return String(x.at) < String(y.at) ? 1 : -1; });
    return tl;
  }
  var STATLBL = { whatsapp_sent:'WhatsApp sent', email_sent:'Email sent',
    email_out:'Emailed', email_in:'Replied', ordered_again:'Ordered ✓',
    assigned:'Assigned' };
  function statusOf(code){
    var evs = mergedEvents(code);
    for(var i=0;i<evs.length;i++)
      if(STATLBL[evs[i].kind])
        return { k:evs[i].kind, label:STATLBL[evs[i].kind],
                 date:String(evs[i].at).slice(0,10) };
    return null;
  }
  function statusChip(code){
    var st = statusOf(code);
    if(!st) return '';
    var cls = st.k === 'ordered_again' || st.k === 'email_in' ? ' good'
      : (st.k === 'assigned' ? '' : ' sent');
    return '<span class="status' + cls + '">' + esc(st.label) + ' · '
      + esc(st.date) + '</span>';
  }

  var TABS = ['actions','protect','recover','grow','manage','accounts',
              'leads','contacts','leadrecs','sales','help'];
  var curAcct = null;
  var hamb = document.getElementById('hamb');
  var sitenav = document.getElementById('sitenav');
  function openNav(){
    if(!sitenav) return;
    sitenav.classList.add('open');
    document.body.classList.add('navopen');
    if(hamb) hamb.setAttribute('aria-expanded', 'true');
    var cur = sitenav.querySelector('[aria-current="true"]')
      || sitenav.querySelector('button[data-tab]');
    if(cur) cur.focus();
  }
  function closeNav(){
    if(!sitenav || !sitenav.classList.contains('open')) return;
    sitenav.classList.remove('open');
    document.body.classList.remove('navopen');
    if(hamb) hamb.setAttribute('aria-expanded', 'false');
  }
  // Called whenever the visible page changes: which menu item is current,
  // whether the home-only summary cards show, and the phone bar's label.
  function navChanged(tab, home){
    document.body.classList.toggle('home', !!home);
    var b = document.querySelector('.nav button[data-tab="' + tab + '"]');
    var lbl = document.getElementById('mcur');
    if(lbl && b) lbl.textContent = b.firstChild.textContent;
    closeNav();
  }
  function hidePages(){
    ['sec-account', 'sec-lead'].forEach(function(id){
      var el = document.getElementById(id);
      if(el) el.classList.remove('on');
    });
  }
  function showTab(name){
    if(TABS.indexOf(name) < 0) name = 'actions';
    curAcct = null; curLead = null;
    hidePages();
    TABS.forEach(function(t){
      var s = document.getElementById('sec-' + t);
      if(s) s.classList.toggle('on', t === name);
    });
    document.querySelectorAll('.nav button').forEach(function(b){
      b.setAttribute('aria-current', b.getAttribute('data-tab') === name
        ? 'true' : 'false');
    });
    navChanged(name, name === 'actions');
    if(location.hash !== '#' + name){
      try { history.replaceState(null, '', '#' + name); } catch(err){}
    }
  }
  function dirRow(code){
    var d = (DATA.directory || []).filter(function(x){ return x.c === code; });
    return d.length ? d[0] : null;
  }
  function showAccount(code){
    if(!dirRow(code)){ showTab('accounts'); return; }
    curAcct = code; curLead = null;
    hidePages();
    TABS.forEach(function(t){
      var s = document.getElementById('sec-' + t);
      if(s) s.classList.remove('on');
    });
    var ap = document.getElementById('sec-account');
    if(ap) ap.classList.add('on');
    document.querySelectorAll('.nav button').forEach(function(b){
      b.setAttribute('aria-current',
        b.getAttribute('data-tab') === 'accounts' ? 'true' : 'false');
    });
    navChanged('accounts', false);
    renderAccount(code);
    window.scrollTo(0, 0);
  }
  function showLead(sid){
    if(!leadBySid(sid)){ showTab('leads'); return; }
    curAcct = null; curLead = sid; openLostFor = null;
    hidePages();
    TABS.forEach(function(t){
      var s = document.getElementById('sec-' + t);
      if(s) s.classList.remove('on');
    });
    var lp = document.getElementById('sec-lead');
    if(lp) lp.classList.add('on');
    document.querySelectorAll('.nav button').forEach(function(b){
      b.setAttribute('aria-current',
        b.getAttribute('data-tab') === 'leads' ? 'true' : 'false');
    });
    navChanged('leads', false);
    renderLeadPage(sid);
    window.scrollTo(0, 0);
  }
  function route(){
    var h = location.hash.slice(1);
    if(h.indexOf('acct/') === 0){
      showAccount(decodeURIComponent(h.slice(5)));
      return;
    }
    if(h.indexOf('lead/') === 0){
      showLead(decodeURIComponent(h.slice(5)));
      return;
    }
    showTab(h || 'actions');
  }

  function pill(a){
    if(a.kind === 'decline'){
      var c = a.pct <= -60 ? '#d03b3b' : '#c98500';
      return '<span class="pill" style="background:'+c+'1f;color:'+c+'">'
        + a.prior + ' \u2192 ' + a.recent + '</span>';
    }
    var g = a.tier === 'gold';
    var col = g ? '#c98500' : '#8a9199';
    return '<span class="pill" style="background:'+col+'1f;color:'+col+'">'
      + a.days + 'd quiet</span>';
  }

  function timelineHtml(code){
    var h = '<div class="dh">TIMELINE</div>';
    var tl = (DATA.timeline && DATA.timeline[code] ? DATA.timeline[code] : []).slice();
    STATE.log.forEach(function(l){ if(l.code === code)
      tl.push({at:l.at, kind:l.kind, summary:l.note||'', actor:l.actor||'', p:1}); });
    tl.sort(function(x,y){ return String(x.at) < String(y.at) ? 1 : -1; });
    if(!tl.length)
      return h + '<p class="said">No interactions logged yet — mark one below.</p>';
    h += '<div class="tl">';
    tl.slice(0,10).forEach(function(t){
      h += '<div class="tlrow"><span class="tld">' + esc(String(t.at).slice(0,10))
        + '</span><span class="tlk' + (t.kind==='ordered_again' ? ' good' : '')
        + '">' + esc(KINDLBL[t.kind] || t.kind) + '</span><span class="tls">'
        + esc(t.summary || '') + (t.actor ? ' · ' + esc(t.actor) : '')
        + (t.p ? ' <span class="pend">(saves to the ledger weekly)</span>' : '')
        + '</span></div>';
    });
    return h + '</div>';
  }

  function mailHtml(code, es, eb, to){
    var href = 'mailto:' + (to || '') + '?subject=' + encodeURIComponent(es || '')
      + '&body=' + encodeURIComponent(eb || '');
    return '<a class="act" href="' + esc(href) + '" data-log="email_sent" '
      + 'data-lognote="(opened in mail app)" data-code="' + esc(code)
      + '">Send email' + (to ? '' : ' (add recipient)') + '</a>';
  }

  function assignHtml(code, ctx){
    var h = '<select class="act" data-assign="' + esc(code)
      + '" aria-label="Assign account"><option value="">Assign to\u2026</option>';
    (DATA.reps || []).forEach(function(r){
      h += '<option value="' + esc(r) + '"'
        + (STATE.assign[code] === r ? ' selected' : '') + '>' + esc(r) + '</option>';
    });
    return h + '</select><span class="said" id="say-' + esc(ctx) + '-'
      + esc(code) + '"></span>';
  }

  function markBtn(code, kind, ctx, label){
    return '<button class="act" data-log="' + esc(kind) + '" data-code="'
      + esc(code) + '" data-ctx="' + esc(ctx) + '">' + label + '</button>';
  }

  function logCtrlsHtml(code, ctx){
    return '<div class="acts"><input class="act noteinput" id="ni-' + esc(ctx) + '-'
      + esc(code)
      + '" type="text" maxlength="200" placeholder="Add a note (spoke to chef, menu changing…)">'
      + '<button class="act" data-noteadd="' + esc(code) + '" data-ctx="'
      + esc(ctx) + '">Add note</button></div>';
  }

  function commsHtml(code, a, ctx){
    var to = firstEmail(code), ph = firstPhone(code);
    var h = '<div class="dh">COMMUNICATIONS</div><div class="comms">'
      + '<div class="ctabs">'
      + '<button data-chan="wa" aria-current="true">WhatsApp</button>'
      + '<button data-chan="em" aria-current="false">Email</button></div>';
    h += '<div class="cpane on" data-pane="wa">';
    if(a.wa){
      h += '<div class="draft">' + esc(a.wa) + '</div><div class="acts">'
        + '<button class="act" data-copy="wa" data-code="' + esc(code)
        + '" data-ctx="' + esc(ctx) + '">Copy message</button>';
      if(ph){
        var wurl = 'https://wa.me/' + ph.replace(/\D/g,'') + '?text='
          + encodeURIComponent(a.wa);
        h += '<a class="act" href="' + esc(wurl) + '" target="_blank" rel="noopener" '
          + 'data-log="whatsapp_sent" data-lognote="(opened in WhatsApp)" '
          + 'data-code="' + esc(code) + '" data-ctx="' + esc(ctx)
          + '">Open in WhatsApp</a>';
      }
      h += markBtn(code, 'whatsapp_sent', ctx, 'Mark sent') + '</div>';
    } else {
      h += '<p class="said">Draft pending — generated in the weekly run.</p>'
        + '<div class="acts">' + markBtn(code, 'whatsapp_sent', ctx, 'Mark sent')
        + '</div>';
    }
    h += '</div><div class="cpane" data-pane="em">';
    if(a.eb){
      h += '<div class="draft"><b>' + esc(a.es) + '</b>\n\n' + esc(a.eb)
        + '</div><div class="acts">'
        + '<button class="act" data-copy="em" data-code="' + esc(code)
        + '" data-ctx="' + esc(ctx) + '">Copy email</button>'
        + mailHtml(code, a.es, a.eb, to)
        + markBtn(code, 'email_sent', ctx, 'Mark sent') + '</div>';
    } else {
      h += '<p class="said">Draft pending — generated in the weekly run.</p>'
        + '<div class="acts">' + markBtn(code, 'email_sent', ctx, 'Mark sent')
        + '</div>';
    }
    return h + '</div></div>';
  }

  function detail(code, a, ctx){
    var h = '';
    if(a.spark) h += a.spark;
    h += '<div class="facts">'
      + '<span>' + esc(a.venue) + '</span>'
      + '<span>' + esc(a.tier) + '</span>'
      + '<span>rep <b>' + esc(a.rep) + '</b></span>';
    if(a.kind === 'decline')
      h += '<span>orders <b>' + a.prior + '</b> \u2192 <b>' + a.recent
        + '</b> (' + a.pct + '%)</span>';
    else
      h += '<span>quiet <b>' + a.days + '</b> days</span>'
        + '<span>history <b>' + a.lines + '</b> lines</span>';
    var v = valOf(code);
    if(v){
      h += '<span>sales <b>' + moneyK(v.s) + '</b> ' + esc(periodLabel()) + '</span>';
      if(v.p3 && lastFull()) h += '<span>' + monLabel(lastFull().m) + ' <b>'
        + money(v.lm) + '</b> vs ' + money(v.p3) + ' avg</span>';
    }
    h += '</div>';

    if(a.stopped && a.stopped.length){
      h += '<div class="dh">STOPPED BUYING</div><div class="chips">';
      a.stopped.forEach(function(s){
        h += '<span class="chip">' + esc(s[0]) + ' \u00b7 ' + s[2] + 'd ago</span>';
      });
      h += '</div>';
    }
    if(a.gaps && a.gaps.length){
      h += '<div class="dh">NEVER BOUGHT</div><div class="chips">';
      a.gaps.forEach(function(g){ h += '<span class="chip">' + esc(g) + '</span>'; });
      h += '</div>';
    }

    h += timelineHtml(code);
    h += commsHtml(code, a, ctx);
    h += '<div class="acts">' + assignHtml(code, ctx) + '</div>';
    h += logCtrlsHtml(code, ctx);
    return h;
  }

  function slimDetail(c, ctx){
    var h = '<div class="facts">'
      + '<span>' + esc(c.venue) + '</span>'
      + '<span>' + esc(c.tier) + '</span>'
      + (c.rep ? '<span>rep <b>' + esc(c.rep) + '</b></span>' : '')
      + '</div>';
    h += timelineHtml(c.id);
    h += '<div class="acts">'
      + markBtn(c.id, 'whatsapp_sent', ctx, 'Mark WhatsApp sent')
      + markBtn(c.id, 'email_sent', ctx, 'Mark email sent')
      + assignHtml(c.id, ctx) + '</div>';
    h += logCtrlsHtml(c.id, ctx);
    return h;
  }

  function sparkSvg(code){
    var series = DATA.weekly && DATA.weekly[code];
    if(!series || !series.length) return '';
    var w = 420, h = 56, pad = 4;
    var mx = Math.max.apply(null, series.concat([1]));
    var n = series.length, step = (w - 2 * pad) / Math.max(n - 1, 1);
    var pts = series.map(function(v, i){
      return [pad + i * step, h - pad - (v / mx) * (h - 2 * pad)];
    });
    var line = pts.map(function(p){
      return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' ');
    var area = pad + ',' + (h - pad) + ' ' + line + ' '
      + (pad + (n - 1) * step).toFixed(1) + ',' + (h - pad);
    var zeros = '';
    series.forEach(function(v, i){
      if(v === 0) zeros += '<circle cx="' + pts[i][0].toFixed(1) + '" cy="'
        + (h - pad) + '" r="2.5" fill="#d03b3b"/>';
    });
    var lp = pts[n - 1];
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" class="chart" '
      + 'style="max-width:460px" role="img" aria-label="Weekly order counts">'
      + '<polygon points="' + area + '" fill="#007946" opacity="0.13"/>'
      + '<polyline points="' + line + '" fill="none" stroke="#007946" '
      + 'stroke-width="2" stroke-linejoin="round"/>' + zeros
      + '<circle cx="' + lp[0].toFixed(1) + '" cy="' + lp[1].toFixed(1)
      + '" r="3.5" fill="#007946"/></svg>';
  }

  function catsOf(code){
    return (DATA.cats && DATA.cats[code] ? DATA.cats[code] : [])
      .map(function(x){ return [DATA.catsMaster[x[0]], x[1]]; });
  }
  function ordersTotal(code){
    return (DATA.weekly && DATA.weekly[code] ? DATA.weekly[code] : [])
      .reduce(function(a, b){ return a + b; }, 0);
  }

  function contactsBlock(code){
    var h = '<div class="dh">CONTACTS</div>';
    var cs = contactsFor(code);
    if(!cs.length){
      h += '<p class="said">No contacts yet \u2014 add one below.</p>';
    } else {
      cs.forEach(function(ct){
        h += '<div class="contact"><b>' + esc(ct.n || '(no name)') + '</b>'
          + (ct.l ? '<span class="muted">' + esc(ct.l) + '</span>' : '')
          + (ct.e ? '<a href="mailto:' + esc(ct.e) + '">' + esc(ct.e) + '</a>' : '')
          + (ct.p ? '<a href="https://wa.me/' + esc(String(ct.p).replace(/\D/g, ''))
             + '" target="_blank" rel="noopener">' + esc(ct.p) + '</a>' : '')
          + (ct.pend ? '<span class="pend">(saves weekly)</span>' : '')
          + '</div>';
      });
    }
    return h + '<div class="addform">'
      + '<input class="act" id="cn-' + esc(code) + '" placeholder="Name">'
      + '<input class="act" id="cl-' + esc(code) + '" placeholder="Role (chef, orders\u2026)">'
      + '<input class="act" id="ce-' + esc(code) + '" type="email" placeholder="Email">'
      + '<input class="act" id="cp-' + esc(code) + '" placeholder="Phone (+44\u2026)">'
      + '<button class="act" data-addcontact="' + esc(code) + '">Add contact</button>'
      + '</div>';
  }

  function fact(k, v){
    return v ? '<div><div class="k">' + k + '</div><div class="v">'
      + esc(v) + '</div></div>' : '';
  }

  var DAYN = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  function pname(i){ return (DATA.prodNames || {})[i] || '?'; }
  function historyBlock(code){
    var h = (DATA.hist || {})[code];
    if(!h) return '';
    var o = '<div class="dh">ORDER HISTORY</div><div class="facts">'
      + '<span>last 7 days <b>' + h.w + '</b></span>'
      + '<span>last 28 days <b>' + h.m + '</b></span>'
      + '<span>since ' + esc(firstOrderDate()) + ' <b>' + h.t + '</b> orders</span>'
      + (h.last ? '<span>last order <b>' + esc(h.last) + '</b> (' + h.lastn + ' lines)</span>' : '')
      + '</div>';
    if(h.days && h.days.length){
      var tot = h.days.reduce(function(a, b){ return a + b; }, 0) || 1;
      o += '<div class="days" aria-label="Usual order days">' + h.days.map(function(n, i){
        return '<span' + (n / tot >= 0.12 ? ' class="on"' : '') + ' title="' + n
          + ' orders">' + DAYN[i] + '</span>'; }).join('') + '</div>';
    }
    if(h.top && h.top.length){
      o += '<div class="dh">TOP PRODUCTS (order lines)</div><div class="chips">'
        + h.top.map(function(x){ return '<span class="chip">' + esc(pname(x[0]))
          + ' \u00b7 ' + x[1] + '</span>'; }).join('') + '</div>';
    }
    if(h.stop && h.stop.length){
      o += '<div class="dh">PRODUCTS THEY\u2019VE STOPPED BUYING</div><div class="chips">'
        + h.stop.map(function(x){ return '<span class="chip">' + esc(pname(x[0]))
          + ' \u00b7 last ' + esc(x[2]) + '</span>'; }).join('') + '</div>';
    }
    return o;
  }
  function firstOrderDate(){
    var f = (DATA.weeks || [])[0];
    return f ? f.split('-').reverse().join('/') : 'June';
  }
  function marginBlock(code){
    var v = valOf(code);
    if(!v || !v.ser || !v.ser.length) return '';
    var cr = v.ser.reduce(function(a, x){ return a + (x[3] || 0); }, 0);
    var net = v.ser.reduce(function(a, x){ return a + x[1]; }, 0);
    var o = '<div class="mlabs">' + (DATA.valueMonths || []).map(function(m){
      var x = v.ser.filter(function(y){ return y[0] === m.m; })[0];
      return '<span title="gross margin">' + (x && x[1] ? Math.round(100 * x[2] / x[1]) + '%' : '\u2013')
        + '</span>'; }).join('') + '</div>';
    o += '<p class="said">Gross margin % by month (row above). Credits '
      + money(cr) + (net ? ' \u2014 ' + (100 * cr / (net + cr)).toFixed(1) + '% of sales' : '')
      + ' (refunds and returns; a rough signal of service issues).</p>';
    return o;
  }
  function prefBlock(d){
    var o = '';
    if(d.pick) o += '<div class="dh">PRODUCE PREFERENCES (Fresho picking notes)</div>'
      + '<div class="note2">' + esc(d.pick) + '</div>';
    var vn = (DATA.venueNotes || {})[d.c];
    if(vn) o += '<div class="dh">ABOUT THE VENUE (web research, ' + esc(vn[0]) + ')</div>'
      + '<div class="note2">' + esc(vn[1]) + '</div>';
    return o;
  }

  function valueFacts(code){
    var v = valOf(code);
    if(!v) return '';
    var lf = lastFull();
    return fact('SALES ' + periodLabel().toUpperCase(), money(v.s)
             + (v.g !== null ? ' \u00b7 GP ' + v.g + '%' : ''))
      + (lf ? fact('NET SALES ' + monLabel(lf.m).toUpperCase(), money(v.lm)
             + (v.p3 ? ' (3-month avg ' + money(v.p3) + ')' : '')) : '')
      + (v.aov ? fact('AVG ORDER VALUE', money(v.aov)) : '');
  }

  function renderAccount(code){
    var host = document.getElementById('acct-page');
    var d = dirRow(code);
    if(!host || !d) return;
    var a = DATA.accounts && DATA.accounts[code];
    var orders = ordersTotal(code);
    var h = '<button class="bk" data-tab="accounts">&#8592; All accounts</button>';
    h += '<div class="ahead"><h2>' + esc(d.n) + '</h2>' + statusChip(code)
      + (STATE.assign[code]
         ? '<span class="owner">' + esc(STATE.assign[code]) + '</span>' : '')
      + '</div>';
    h += '<div class="ar"><span class="chip">' + esc(d.v) + '</span>'
      + (d.t ? '<span class="chip">' + esc(d.t) + '</span>' : '')
      + (d.vip ? '<span class="chip">VIP</span>' : '')
      + '<span class="chip">' + esc(String(d.s).replace(/_/g, ' ')) + '</span>'
      + (d.grp ? '<span class="chip">' + esc(d.grp) + '</span>' : '')
      + (d.stage ? '<span class="chip">' + esc(d.stage) + '</span>' : '')
      + (DATA.leads || []).filter(function(l){ return effMatch(l) === code; })
          .map(function(l){ return '<button class="chip bk" data-golead="'
            + esc(l.sid) + '">from lead ' + esc(fmtDay(dayOf(l.recv || l.date)))
            + '</button>'; }).join('')
      + '</div>';
    h += '<div class="agridfacts">'
      + fact('REP', d.r)
      + fact('FIRST SEEN', d.first)
      + fact('LAST ORDER', d.last
             + (d.ds !== null && d.ds !== undefined ? ' (' + d.ds + 'd ago)' : ''))
      + valueFacts(code)
      + fact('TOTAL ORDERS', orders ? String(orders) : '')
      + fact('ORDER LINES', d.l ? String(d.l) : '')
      + fact('DELIVERY RUN', d.run)
      + fact('PAYMENT TERMS', d.terms !== null && d.terms !== undefined
             ? d.terms + ' days' : '')
      + fact('PRICING LEVEL', d.pricing)
      + fact('NEGOTIATED PRICES', d.npg)
      + fact('REBATE', d.rebate ? d.rebate + '%' : '')
      + fact('LEGAL ENTITY', d.legal)
      + fact('DELIVERY ADDRESS', d.addr)
      + (d.baddr && d.baddr !== d.addr ? fact('BILLING ADDRESS', d.baddr) : '')
      + fact('INTERNAL NOTES', d.inotes)
      + '</div>';
    var mb = monthBars(code);
    if(mb) h += '<div class="dh">NET SALES BY MONTH</div>' + mb + marginBlock(code);
    h += prefBlock(d);
    h += historyBlock(code);
    var sp = sparkSvg(code);
    if(sp) h += '<div class="dh">13-WEEK ORDER PATTERN</div>' + sp;
    var cats = catsOf(code);
    if(cats.length){
      h += '<div class="dh">TOP CATEGORIES (order lines)</div><div class="chips">';
      cats.slice(0, 8).forEach(function(c){
        h += '<span class="chip">' + esc(c[0]) + ' \u00b7 ' + c[1] + '</span>';
      });
      h += '</div>';
      var bought = {};
      cats.forEach(function(c){ bought[c[0]] = 1; });
      var gaps = (DATA.targetable || []).filter(function(t){ return !bought[t]; });
      if(gaps.length){
        h += '<div class="dh">NEVER BOUGHT</div><div class="chips">';
        gaps.forEach(function(g){ h += '<span class="chip">' + esc(g) + '</span>'; });
        h += '</div>';
      }
    }
    h += contactsBlock(code);
    h += timelineHtml(code);
    if(a){
      h += commsHtml(code, a, 'p');
      h += '<div class="acts">' + assignHtml(code, 'p') + '</div>';
    } else {
      h += '<div class="acts">'
        + markBtn(code, 'whatsapp_sent', 'p', 'Mark WhatsApp sent')
        + markBtn(code, 'email_sent', 'p', 'Mark email sent')
        + assignHtml(code, 'p') + '</div>';
    }
    h += logCtrlsHtml(code, 'p');
    host.innerHTML = h;
  }

  function valOf(code){ return DATA.value ? DATA.value[code] : null; }
  function money(v){
    if(v === null || v === undefined) return '';
    return '\u00a3' + Math.round(v).toLocaleString('en-GB');
  }
  function moneyK(v){
    if(v === null || v === undefined) return '';
    if(Math.abs(v) >= 1e6) return '\u00a3' + (v / 1e6).toFixed(2) + 'M';
    return Math.abs(v) >= 10000 ? '\u00a3' + Math.round(v / 1000) + 'k' : money(v);
  }
  function trendHtml(t){
    if(t === null || t === undefined) return '';
    return '<span class="' + (t < 0 ? 'down' : 'up') + '">'
      + (t > 0 ? '+' : '') + Math.round(t) + '%</span>';
  }
  var MONTHS = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(' ');
  function monLabel(m){ return MONTHS[parseInt(m.slice(5), 10) - 1]; }
  function periodLabel(){
    var ms = DATA.valueMonths || [];
    if(!ms.length) return '';
    var a = ms[0], b = ms[ms.length - 1];
    return monLabel(a.m) + (a.m.slice(0, 4) !== b.m.slice(0, 4) ? ' ' + a.m.slice(0, 4) : '')
      + '\u2013' + (b.mtd ? parseInt(b.end.slice(8), 10) + ' ' : '')
      + monLabel(b.m) + ' ' + b.m.slice(0, 4);
  }
  function lastFull(){
    var ms = (DATA.valueMonths || []).filter(function(m){ return !m.mtd; });
    return ms.length ? ms[ms.length - 1] : null;
  }
  function monthBars(code){
    var v = valOf(code);
    if(!v || !v.ser || !v.ser.length) return '';
    var ms = DATA.valueMonths || [];
    var byM = {};
    v.ser.forEach(function(x){ byM[x[0]] = x[1]; });
    var mx = 0;
    ms.forEach(function(m){ mx = Math.max(mx, byM[m.m] || 0); });
    if(!mx) return '';
    return '<div class="mbars" role="img" aria-label="Net sales by month">'
      + ms.map(function(m){
          var n = byM[m.m] || 0;
          return '<span' + (m.mtd ? ' class="mtd"' : '') + ' style="height:'
            + Math.max(1, Math.round(100 * n / mx)) + '%" title="' + monLabel(m.m)
            + (m.mtd ? ' to date' : '') + ': ' + money(n) + '"></span>';
        }).join('') + '</div><div class="mlabs">'
      + ms.map(function(m){ return '<span>' + monLabel(m.m) + '</span>'; }).join('')
      + '</div>';
  }
  function acctBtn(d){
    return '<button class="bk" data-goacct="' + esc(d.c) + '">' + esc(d.n) + '</button>';
  }
  function tile(n, l){
    return '<div class="kpi" style="cursor:default"><span class="n">' + n
      + '</span><span class="l">' + l + '</span></div>';
  }
  function table(head, body){
    return body ? '<table><tr>' + head + '</tr>' + body + '</table>'
                : '<p class="said">None.</p>';
  }

  function renderSales(){
    var kp = document.getElementById('sales-kpis');
    if(!kp) return;
    var ms = DATA.valueMonths || [];
    if(!ms.length){
      kp.innerHTML = '<p class="said">No \u00a3 data loaded yet. Export Fresho\u2019s '
        + '\u201cSales Totals and Gross Profit Margin\u201d report one month per file.</p>';
      return;
    }
    var lf = lastFull(), cur = ms[ms.length - 1];
    var full = ms.filter(function(m){ return !m.mtd; });
    var prev3 = full.slice(-4, -1);
    var p3 = prev3.length
      ? prev3.reduce(function(a, m){ return a + m.net; }, 0) / prev3.length : null;
    var tot = ms.reduce(function(a, m){ return a + m.net; }, 0);
    var totgp = ms.reduce(function(a, m){ return a + m.gp; }, 0);
    var h = '';
    if(lf){
      h += tile(moneyK(lf.net), 'net sales in ' + monLabel(lf.m)
        + (p3 ? ' \u00b7 ' + trendHtml(100 * (lf.net - p3) / p3)
           + ' vs prior 3-month avg' : ''));
      h += tile((100 * lf.gp / lf.net).toFixed(1) + '%',
                'gross margin in ' + monLabel(lf.m));
    }
    if(cur.mtd) h += tile(moneyK(cur.net), monLabel(cur.m) + ' to date (to '
      + parseInt(cur.end.slice(8), 10) + ' ' + monLabel(cur.m) + ')');
    h += tile(moneyK(tot), 'net sales ' + esc(periodLabel()) + ' \u00b7 GP '
      + (100 * totgp / tot).toFixed(1) + '%');
    kp.innerHTML = h;

    var dir = {};
    (DATA.directory || []).forEach(function(d){ dir[d.c] = d; });
    var rows = Object.keys(DATA.value || {}).filter(function(c){ return dir[c]; })
      .map(function(c){ return {d: dir[c], v: DATA.value[c]}; });
    var mon = lf ? monLabel(lf.m) : 'Last month';

    var falls = rows.filter(function(r){
      return !r.d.x && /^active/.test(r.d.s) && (r.d.t === 'gold' || r.d.t === 'silver')
        && r.v.p3 >= 1000 && r.v.tr !== null && r.v.tr <= -25;
    }).sort(function(a, b){ return (b.v.p3 - b.v.lm) - (a.v.p3 - a.v.lm); })
      .slice(0, 15);
    document.getElementById('sales-falls').innerHTML = table(
      '<th>Account</th><th>Rep</th><th class="num">Prior 3-mo avg</th>'
        + '<th class="num">' + mon + '</th><th class="num">Change</th>'
        + '<th class="num">\u00a3/month down</th>',
      falls.map(function(r){
        return '<tr><td>' + acctBtn(r.d) + '</td><td>' + esc(r.d.r)
          + '</td><td class="num">' + money(r.v.p3) + '</td><td class="num">'
          + money(r.v.lm) + '</td><td class="num">' + trendHtml(r.v.tr)
          + '</td><td class="num">' + money(r.v.p3 - r.v.lm) + '</td></tr>';
      }).join(''));

    var top = rows.slice().sort(function(a, b){ return b.v.s - a.v.s; }).slice(0, 15);
    document.getElementById('sales-top').innerHTML = table(
      '<th>Account</th><th>Rep</th><th class="num">Sales ' + esc(periodLabel())
        + '</th><th class="num">GP%</th><th class="num">' + mon
        + '</th><th class="num">vs 3-mo avg</th>',
      top.map(function(r){
        return '<tr><td>' + acctBtn(r.d) + '</td><td>' + esc(r.d.r)
          + '</td><td class="num">' + money(r.v.s) + '</td><td class="num">'
          + (r.v.g !== null ? r.v.g + '%' : '') + '</td><td class="num">'
          + money(r.v.lm) + '</td><td class="num">' + trendHtml(r.v.tr) + '</td></tr>';
      }).join(''));

    var low = rows.filter(function(r){ return r.v.s >= 10000 && r.v.g !== null; })
      .sort(function(a, b){ return a.v.g - b.v.g; }).slice(0, 10);
    document.getElementById('sales-lowgp').innerHTML = table(
      '<th>Account</th><th>Venue</th><th>Rep</th><th class="num">Sales</th>'
        + '<th class="num">GP%</th>',
      low.map(function(r){
        return '<tr><td>' + acctBtn(r.d) + '</td><td>' + esc(r.d.v) + '</td><td>'
          + esc(r.d.r) + '</td><td class="num">' + money(r.v.s)
          + '</td><td class="num">' + r.v.g + '%</td></tr>';
      }).join(''));

    var reps = {};
    rows.forEach(function(r){
      var k = r.d.r || '(unassigned)';
      var a = reps[k] = reps[k] || {n: 0, s: 0, gp: 0, lm: 0, p3: 0};
      a.n += 1; a.s += r.v.s || 0; a.gp += (r.v.s || 0) * (r.v.g || 0) / 100;
      a.lm += r.v.lm || 0; a.p3 += r.v.p3 || 0;
    });
    document.getElementById('sales-reps').innerHTML = table(
      '<th>Account manager</th><th class="num">Accounts</th><th class="num">Sales '
        + esc(periodLabel()) + '</th><th class="num">GP%</th><th class="num">' + mon
        + '</th><th class="num">vs 3-mo avg</th>',
      Object.keys(reps).sort(function(a, b){ return reps[b].s - reps[a].s; })
        .map(function(k){
          var a = reps[k];
          return '<tr><td>' + esc(k) + '</td><td class="num">' + a.n
            + '</td><td class="num">' + money(a.s) + '</td><td class="num">'
            + (a.s ? (100 * a.gp / a.s).toFixed(1) + '%' : '') + '</td><td class="num">'
            + money(a.lm) + '</td><td class="num">'
            + (a.p3 ? trendHtml(100 * (a.lm - a.p3) / a.p3) : '') + '</td></tr>';
        }).join(''));
  }

  function renderDir(){
    var host = document.getElementById('dirlist');
    if(!host) return;
    var qEl = document.getElementById('dirq');
    var q = qEl && qEl.value ? qEl.value.toLowerCase() : '';
    var sf = (document.getElementById('dirstatus') || {}).value || '';
    var tf = (document.getElementById('dirtier') || {}).value || '';
    var rows = (DATA.directory || []).filter(function(d){
      return (!q || d.n.toLowerCase().indexOf(q) >= 0)
        && (!sf || d.s === sf) && (!tf || d.t === tf);
    });
    if(((document.getElementById('dirsort') || {}).value || '') === 'sales')
      rows = rows.slice().sort(function(a, b){
        return ((valOf(b.c) || {}).s || 0) - ((valOf(a.c) || {}).s || 0); });
    var cnt = document.getElementById('dircount');
    if(cnt) cnt.textContent = rows.length + ' of '
      + (DATA.directory || []).length + ' accounts \u2014 click a card to open it';
    host.innerHTML = rows.map(function(d){
      return '<button class="acard" data-goacct="' + esc(d.c) + '">'
        + '<span class="an">' + esc(d.n) + '</span>'
        + '<span class="am">' + esc(d.v) + (d.t ? ' \u00b7 ' + esc(d.t) : '')
        + (d.vip ? ' \u00b7 VIP' : '')
        + ' \u00b7 ' + esc(String(d.s).replace(/_/g, ' ')) + '</span>'
        + '<span class="ar">' + statusChip(d.c)
        + (STATE.assign[d.c]
           ? '<span class="owner">' + esc(STATE.assign[d.c]) + '</span>' : '')
        + (d.ds !== null && d.ds !== undefined
           ? '<span class="am">' + d.ds + 'd since order</span>' : '')
        + (valOf(d.c) ? '<span class="am">' + moneyK(valOf(d.c).s) + ' sales</span>' : '')
        + '</span></button>';
    }).join('') || '<p class="said">No accounts match.</p>';
  }

  function render(listId, order){
    var el = document.getElementById(listId);
    if(!el) return;
    el.innerHTML = order.map(function(code){
      var a = DATA.accounts[code];
      if(!a) return '';
      var owner = STATE.assign[code]
        ? '<span class="owner">' + esc(STATE.assign[code]) + '</span>' : '';
      return '<div class="row" data-code="' + esc(code) + '">'
        + '<button class="rowhead" aria-expanded="false"><span class="chev">\u25b6</span>'
        + '<span class="rname">' + esc(a.name) + '</span>' + statusChip(code)
        + owner + pill(a)
        + '</button><div class="detail">' + detail(code, a, 'r') + '</div></div>';
    }).join('');
  }

  function renderActions(){
    var host = document.getElementById('cards');
    if(!host) return;
    var act = [], res = [];
    (DATA.actions || []).forEach(function(c){
      var st = STATE.actions[c.id];
      (st && (st.s === 'done' || st.s === 'dism') ? res : act).push(c);
    });
    var cnt = document.getElementById('actions-count');
    if(cnt) cnt.textContent = act.length;
    host.innerHTML = act.map(function(c){
      var own = ownerOf(c.id);
      var tc = c.tier === 'gold' ? '#c98500' : '#8a9199';
      var isOpen = !!openCards[c.id];
      var a = DATA.accounts[c.id];
      var body = a ? detail(c.id, a, 'c') : slimDetail(c, 'c');
      return '<div class="card' + (isOpen ? ' open' : '') + '" data-code="'
        + esc(c.id) + '"><div class="crow">'
        + '<span class="cverb">' + esc(c.verb) + '</span>'
        + '<button class="cname" data-cardtoggle="' + esc(c.id)
        + '" aria-expanded="' + (isOpen ? 'true' : 'false')
        + '"><span class="chev">\u25b6</span>' + esc(c.name) + '</button>'
        + '<span class="pill" style="background:' + tc + '1f;color:' + tc + '">'
        + esc(c.tier || c.venue) + '</span>' + statusChip(c.id)
        + (own ? '<span class="owner">' + esc(own) + '</span>' : '')
        + '<span class="cbtns">'
        + '<button class="act mini" data-actdo="done" data-id="' + esc(c.id) + '">Done</button>'
        + '<button class="act mini" data-actdo="dism" data-id="' + esc(c.id) + '">Dismiss</button>'
        + '</span></div><div class="cwhy">'
        + c.reasons.map(esc).join(' &#183; ') + '</div>'
        + '<div class="cbody">' + body + '</div></div>';
    }).join('') || '<p class="said">All clear — nothing waiting.</p>';
    if(DATA.actionsMore){
      host.innerHTML += '<p class="said">+ ' + DATA.actionsMore
        + ' more lower-priority actions inside the plays.</p>';
    }
    var wrap = document.getElementById('resolved-wrap');
    if(!wrap) return;
    if(res.length){
      wrap.hidden = false;
      wrap.querySelector('summary').textContent = res.length + ' resolved this week';
      document.getElementById('resolved').innerHTML = res.map(function(c){
        var st = STATE.actions[c.id];
        return '<div class="cwhy">' + (st.s === 'done' ? '✓ ' : '— ')
          + esc(c.verb) + ' ' + esc(c.name)
          + ' <button class="act mini" data-actdo="undo" data-id="' + esc(c.id)
          + '">Undo</button></div>';
      }).join('');
    } else { wrap.hidden = true; }
  }

  function say(code, msg, ctx){
    var s = document.getElementById('say-' + (ctx || 'r') + '-' + code);
    if(s){ s.textContent = msg; setTimeout(function(){ s.textContent = ''; }, 3200); }
  }

  // ---------------------------------------------------------------- leads
  // A lead's stage, owner and follow-up come from the data (as of the weekly
  // rebuild) with this page's not-yet-filed events (STATE.leadLog) on top.
  var nameToCode = {};
  (DATA.directory || []).forEach(function(d){ nameToCode[d.n.toLowerCase()] = d.c; });
  var STAGES = DATA.leadStages || [];
  var STAGE_LBL = {};
  STAGES.forEach(function(s){ STAGE_LBL[s[0]] = s[1]; });
  var OPEN = ['New', 'Contacted', 'Qualified', 'Trial'];
  var FUNNEL = ['New', 'Contacted', 'Qualified', 'Trial', 'Acquired'];
  var CONTACT_KINDS = {call:'Called', email:'Emailed', whatsapp:'WhatsApp',
    visit:'Visited', sample:'Sample sent'};
  var LKIND = {stage:'Stage', note:'Note', follow_up:'Follow-up', owner:'Owner',
    verdict:'Verdict',
    call:'Called', email:'Emailed', whatsapp:'WhatsApp', visit:'Visited',
    sample:'Sample sent', received:'Enquiry'};
  var VERB = {New:'Respond', Contacted:'Chase', Qualified:'Keep warm',
    Trial:'Get feedback'};
  var curLead = null;
  var openLostFor = null;

  // "You are" — who gets the credit for a logged action, until logins.
  var meMem = '';
  function whoAmI(){
    if(meMem) return meMem;
    try { meMem = localStorage.getItem('rushtons.me') || ''; } catch(err){}
    return meMem;
  }
  function setMe(v){
    meMem = v;
    try { localStorage.setItem('rushtons.me', v); } catch(err){}
  }
  function whoamiOptions(){
    var me = whoAmI();
    return '<option value="">Choose…</option>' + (DATA.leadTeam || []).map(function(n){
      return '<option' + (me === n ? ' selected' : '') + '>' + esc(n) + '</option>';
    }).join('');
  }
  function fillWhoami(){
    document.querySelectorAll('[data-whoami]').forEach(function(s){
      s.innerHTML = whoamiOptions(); });
  }

  function isoDay(d){
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
      + '-' + String(d.getDate()).padStart(2, '0');
  }
  function todayIso(){ return isoDay(new Date()); }
  function noon(iso){ return new Date(iso.slice(0, 10) + 'T12:00:00'); }
  function addDays(iso, n){
    var d = noon(iso); d.setDate(d.getDate() + n); return isoDay(d);
  }
  function dayDiff(a, b){ return Math.round((noon(b) - noon(a)) / 864e5); }
  function dayOf(x){ return !x ? '' : (x.length > 10 ? isoDay(new Date(x)) : x); }
  function fmtDay(iso){
    if(!iso) return '';
    return noon(iso).toLocaleDateString('en-GB',
      {weekday:'short', day:'numeric', month:'short'});
  }

  function leadBySid(sid){
    var r = (DATA.leads || []).filter(function(l){ return l.sid === sid; });
    return r.length ? r[0] : null;
  }
  function effMatch(l){
    return Object.prototype.hasOwnProperty.call(STATE.leadMatch, l.sid)
      ? (STATE.leadMatch[l.sid] || '') : (l.matched || '');
  }
  function sugFor(l){
    if(effMatch(l)) return null;
    if(Object.prototype.hasOwnProperty.call(STATE.leadMatch, l.sid)) return null;
    return l.sug || null;   // [code, score, how] — user dismissed = key present
  }
  function leadActs(l){
    var a = (l.acts || []).map(function(x){
      return {at:x[0], kind:x[1], stage:x[2], note:x[3], due:x[4], actor:x[5]}; });
    STATE.leadLog.forEach(function(e){
      if(e.sid !== l.sid) return;
      a.push({at:e.at, kind:e.kind, stage:e.stage || '', due:e.due || '',
        actor:e.actor || '', p:1,
        note: e.kind === 'stage' ? (e.reason || '')
          : (e.kind === 'owner' ? (e.owner || '') : (e.note || ''))});
    });
    a.sort(function(x, y){ return x.at < y.at ? -1 : (x.at > y.at ? 1 : 0); });
    return a;
  }
  // Effective pipeline state of a lead, and when it is next due.
  function leadView(l){
    var v = {st: l.status || 'New', owner: l.owner || '', next: l.next || '',
      verdict: l.verdict || '', vsrc: l.vsrc || '',
      lost: l.lost || '', touch: dayOf(l.chg || l.recv || l.date || ''),
      acts: leadActs(l), reached: 0};
    v.acts.forEach(function(e){
      if(e.p){
        if(e.kind === 'stage'){
          v.st = e.stage; v.lost = e.stage === 'Discarded' ? e.note : '';
          v.next = e.due || '';
        } else if(e.kind === 'follow_up') v.next = e.due || '';
        else if(e.kind === 'owner') v.owner = e.note || '';
        else if(e.kind === 'verdict'){ v.verdict = e.note; v.vsrc = 'team'; }
        else if(e.due) v.next = e.due;
      }
      if(e.kind !== 'owner' && e.kind !== 'follow_up'){
        var d = dayOf(e.at);
        if(d > v.touch) v.touch = d;
      }
      if(e.kind === 'stage' && FUNNEL.indexOf(e.stage) > v.reached)
        v.reached = FUNNEL.indexOf(e.stage);
      if(CONTACT_KINDS[e.kind] && v.reached < 1) v.reached = 1;
    });
    if(FUNNEL.indexOf(v.st) > v.reached) v.reached = FUNNEL.indexOf(v.st);
    v.open = OPEN.indexOf(v.st) >= 0;
    v.due = ''; v.auto = false; v.dueIn = null; v.quiet = false;
    // An AI Reject waits for a person to confirm it; it has no follow-up.
    v.rejectCheck = v.open && v.verdict === 'Reject' && !v.next;
    if(v.open && !v.rejectCheck){
      if(v.next) v.due = v.next;
      else if(v.touch){
        var vd = (DATA.leadVerdictDue || {})[v.verdict];
        v.ruleDays = v.st === 'New' && vd !== undefined ? vd
          : ((DATA.leadFollowUp || {})[v.st] || 7);
        v.due = addDays(v.touch, v.ruleDays);
        v.auto = true;
      }
      var t = todayIso();
      v.quiet = !v.next && !!v.touch
        && dayDiff(v.touch, t) > (DATA.leadQuietDays || 60);
      if(v.due) v.dueIn = dayDiff(t, v.due);
    }
    return v;
  }

  var VLABEL = {'Pursue with urgency': 'URGENT', 'Pursue': 'Pursue',
    'Nurture': 'Nurture', 'Pass to rep': 'Pass to rep', 'Reject': 'AI: reject'};
  var VCOLOR = {'Pursue with urgency': '#d03b3b', 'Pursue': '#007946',
    'Nurture': '#5f6b7a', 'Pass to rep': '#c98500', 'Reject': '#898781'};
  function verdictChip(vd){
    if(!vd) return '';
    var c = VCOLOR[vd] || '#898781';
    return '<span class="pill" style="border:1px solid ' + c + ';color:' + c
      + (vd === 'Pursue with urgency' ? ';font-weight:700' : '') + '">'
      + esc(VLABEL[vd] || vd) + '</span>';
  }
  function lostReasonFor(why){
    if(/delivery area/i.test(why)) return 'Outside delivery area';
    if(/consumer|personal/i.test(why)) return 'Personal / not trade';
    return 'Not a fit';
  }
  function rejectBtns(l){
    return '<button class="act mini" data-vlost="' + esc(l.sid) + '">Confirm Lost</button>'
      + '<button class="act mini" data-vpursue="' + esc(l.sid) + '">Pursue anyway</button>';
  }
  function leadChip(st){
    var c = st === 'Acquired' ? '#007946' : st === 'Discarded' ? '#898781'
      : '#c98500';
    return '<span class="pill" style="background:' + c + '1f;color:' + c + '">'
      + esc(STAGE_LBL[st] || st || '—') + '</span>';
  }
  function dueBadge(v){
    if(!v.open) return '';
    if(v.quiet) return '<span class="due">quiet ' + dayDiff(v.touch, todayIso())
      + 'd</span>';
    if(v.dueIn === null) return '';
    if(v.dueIn < 0) return '<span class="due over">overdue ' + (-v.dueIn) + 'd</span>';
    if(v.dueIn === 0) return '<span class="due today">due today</span>';
    return '<span class="due">due ' + esc(fmtDay(v.due)) + '</span>';
  }
  function stars(r){
    if(!r) return '<span class="muted">unrated</span>';
    return '<span class="stars">' + '★'.repeat(r) + '☆'.repeat(5 - r) + '</span>';
  }
  function lname(l){ return l.biz || l.name || l.email || '(no name)'; }
  function plural(n, w){ return n + ' ' + w + (n === 1 ? '' : 's'); }
  function fval(id){ return (document.getElementById(id) || {}).value || ''; }

  function initLeadFilters(){
    var ls = document.getElementById('leadstatus');
    if(ls) ls.innerHTML = '<option value="open">Open (not won or lost)</option>'
      + '<option value="">All stages</option>'
      + STAGES.map(function(s){
          return '<option value="' + esc(s[0]) + '">' + esc(s[1]) + '</option>';
        }).join('');
    var lvd = document.getElementById('leadverdict');
    if(lvd) lvd.innerHTML = '<option value="">Any verdict</option>'
      + (DATA.leadVerdicts || []).map(function(x){
          return '<option>' + esc(x) + '</option>'; }).join('');
    var lo = document.getElementById('leadowner');
    if(lo) lo.innerHTML = '<option value="">Any owner</option>'
      + '<option value="-">Nobody yet</option>'
      + (DATA.leadTeam || []).map(function(n){
          return '<option>' + esc(n) + '</option>'; }).join('');
    fillWhoami();
  }

  function renderLeads(){
    var host = document.getElementById('leadlist');
    if(!host) return;
    var all = DATA.leads || [];
    var views = {};
    all.forEach(function(l){ views[l.sid] = leadView(l); });
    var included = all.filter(function(l){ return !l.excl; });

    var open = 0, dueNow = 0, won = 0, resp = [];
    included.forEach(function(l){
      var v = views[l.sid];
      if(v.open){
        open++;
        if(!v.quiet && v.dueIn !== null && v.dueIn <= 0) dueNow++;
      }
      if(v.st === 'Acquired') won++;
      if(l.recv){
        var first = null;
        v.acts.forEach(function(e){
          if(!first && (CONTACT_KINDS[e.kind]
              || (e.kind === 'stage' && e.stage !== 'New'))) first = e.at;
        });
        if(first) resp.push((new Date(first) - new Date(l.recv)) / 36e5);
      }
    });
    var matched = included.filter(function(l){ return effMatch(l); }).length;
    var sugs = included.filter(sugFor).length;
    resp.sort(function(a, b){ return a - b; });
    var med = resp.length ? resp[Math.floor(resp.length / 2)] : null;
    var kp = document.getElementById('lead-kpis');
    if(kp) kp.innerHTML =
      '<button class="kpi" data-funnel="open"><span class="n">' + open
      + '</span><span class="l">open leads (of ' + included.length
      + ' commercial)</span></button>'
      + '<button class="kpi" data-tab="leadrecs"><span class="n">' + dueNow
      + '</span><span class="l">follow-ups due now</span></button>'
      + '<button class="kpi" data-funnel="Acquired"><span class="n">' + won
      + '</span><span class="l">won · ' + matched + ' linked to a Fresho account ('
      + (included.length ? Math.round(100 * matched / included.length) : 0)
      + '% real conversion)' + (sugs ? ' · ' + sugs + ' possible match'
      + (sugs > 1 ? 'es' : '') + ' to confirm' : '') + '</span></button>'
      + '<div class="kpi" style="cursor:default"><span class="n">'
      + (med === null ? '—' : (med < 48 ? Math.round(med) + 'h'
         : Math.round(med / 24) + 'd'))
      + '</span><span class="l">typical time to first contact (website leads'
      + (resp.length ? ', ' + resp.length + ' so far' : ', none logged yet')
      + ')</span></div>';

    var fh = document.getElementById('lead-funnel');
    if(fh){
      var n = included.length || 1;
      var h = '<div class="funnel">';
      FUNNEL.forEach(function(st, i){
        var reached = included.filter(function(l){
          return views[l.sid].reached >= i; }).length;
        var now = included.filter(function(l){
          return views[l.sid].st === st; }).length;
        h += '<button class="frow" data-funnel="' + esc(st) + '" title="'
          + now + ' in this stage now">'
          + '<span class="flab">' + esc(STAGE_LBL[st]) + '</span>'
          + '<span class="ftrack"><span class="fbar" style="width:'
          + (100 * reached / n).toFixed(1) + '%"></span></span>'
          + '<span class="fnum"><b>' + reached + '</b> · '
          + Math.round(100 * reached / n) + '%</span></button>';
      });
      var lost = included.filter(function(l){ return views[l.sid].st === 'Discarded'; });
      var why = {};
      lost.forEach(function(l){
        var r = views[l.sid].lost || 'no reason recorded';
        why[r] = (why[r] || 0) + 1; });
      var whyTxt = Object.keys(why).sort(function(a, b){ return why[b] - why[a]; })
        .map(function(k){ return esc(k) + ' ' + why[k]; }).join(' · ');
      h += '</div><div class="flost"><button class="bk" data-funnel="Discarded">Lost: '
        + lost.length + '</button>' + (whyTxt ? ' — ' + whyTxt : '')
        + '. Bars count leads that got at least that far; tap a stage to list'
        + ' the leads in it now.</div>';
      fh.innerHTML = h;
    }

    var rt = document.getElementById('lead-ratings');
    if(rt){
      var rows = '';
      [5, 4, 3, 2, 1, null].forEach(function(band){
        var pool = included.filter(function(l){
          return band === null ? !l.rating : l.rating === band; });
        if(!pool.length) return;
        var a = pool.filter(function(l){
          return views[l.sid].st === 'Acquired'; }).length;
        rows += '<tr><td>' + (band === null ? 'Unrated'
          : '★'.repeat(band)) + '</td><td class="num">' + pool.length
          + '</td><td class="num">' + a + '</td><td class="num">'
          + Math.round(100 * a / pool.length) + '%</td></tr>';
      });
      rt.innerHTML = '<table><tr><th>Rating</th><th>Leads</th>'
        + '<th>Won</th><th>Conversion</th></tr>' + rows + '</table>';
    }

    var sf = fval('leadstatus'), of = fval('leadowner');
    var rf = parseInt(fval('leadrating'), 10) || 0;
    var vf = fval('leadverdict');
    var q = fval('leadq').trim().toLowerCase();
    var showEx = !!(document.getElementById('leadexcl') || {}).checked;
    var list = all.filter(function(l){
      var v = views[l.sid];
      if(!showEx && l.excl) return false;
      if(sf === 'open' && !v.open) return false;
      if(sf && sf !== 'open' && v.st !== sf) return false;
      if(of === '-' && v.owner) return false;
      if(of && of !== '-' && v.owner !== of) return false;
      if(rf && (l.rating || 0) < rf) return false;
      if(vf && v.verdict !== vf) return false;
      if(q && [l.biz, l.name, l.email, l.phone].join(' ').toLowerCase()
          .indexOf(q) < 0) return false;
      return true;
    });
    if(sf === 'open') list.sort(function(a, b){
      var x = views[a.sid], y = views[b.sid];
      if(x.quiet !== y.quiet) return x.quiet ? 1 : -1;
      return (x.dueIn === null ? 1e9 : x.dueIn) - (y.dueIn === null ? 1e9 : y.dueIn);
    });
    host.innerHTML = list.map(function(l){
      var v = views[l.sid];
      return '<div class="lrow' + (l.excl ? ' excl' : '') + '">'
        + '<button class="lhead" data-golead="' + esc(l.sid) + '">'
        + '<span class="rname">' + esc(lname(l)) + '</span>'
        + stars(l.rating) + verdictChip(v.verdict) + leadChip(v.st) + dueBadge(v)
        + (v.owner ? '<span class="owner">' + esc(v.owner) + '</span>' : '')
        + (l.excl ? '<span class="pill">' + esc(l.excl) + '</span>' : '')
        + '<span class="dmeta">' + esc(l.src) + (l.date ? ' · ' + esc(l.date) : '')
        + '</span></button></div>';
    }).join('') || '<p class="said">No leads match this filter.</p>';
  }

  function waNumber(p){
    var d = String(p || '').replace(/\D/g, '');
    if(!d) return '';
    if(d.indexOf('44') === 0) return d;
    if(d.charAt(0) === '0') return '44' + d.slice(1);
    if(d.length === 10 && d.charAt(0) === '7') return '44' + d;
    return d;
  }

  function actLine(e){
    var what = LKIND[e.kind] || e.kind, detail = '';
    if(e.kind === 'stage') detail = '→ ' + (STAGE_LBL[e.stage] || e.stage)
      + (e.stage === 'Discarded' && e.note ? ' (' + e.note + ')' : '');
    else if(e.kind === 'owner') detail = '→ ' + (e.note || 'nobody');
    else if(e.kind === 'verdict') detail = '→ ' + (e.note || '');
    else if(e.kind === 'follow_up') detail = e.due ? 'set for ' + fmtDay(e.due)
      : 'back to the stage rule';
    else {
      detail = e.note || '';
      if(e.due) detail += (detail ? ' · ' : '') + 'follow up ' + fmtDay(e.due);
    }
    return '<div class="tlrow"><span class="tld">' + esc(dayOf(e.at))
      + '</span><span class="tlk">' + esc(what) + '</span><span class="tls">'
      + esc(detail) + (e.actor ? ' · ' + esc(e.actor) : '')
      + (e.p ? ' <span class="pend">(saved for everyone; filed weekly)</span>' : '')
      + '</span></div>';
  }

  function freshoBlock(l){
    var m = effMatch(l), md = m ? dirRow(m) : null, h = '';
    if(md){
      return '<div class="facts"><span>linked to <button class="bk" '
        + 'data-goacct="' + esc(md.c) + '">' + esc(md.n) + '</button>'
        + (l.method && !STATE.leadMatch.hasOwnProperty(l.sid)
           ? ' <span class="muted">(' + esc(l.method) + ')</span>' : '')
        + '</span><span>orders since <b>' + ordersTotal(md.c)
        + '</b></span><span>lines <b>' + md.l + '</b></span>'
        + (valOf(md.c) ? '<span>sales <b>' + money(valOf(md.c).s) + '</b> '
           + esc(periodLabel()) + '</span>' : '')
        + (md.first ? '<span>first order <b>' + esc(md.first) + '</b></span>' : '')
        + '</div><div class="acts"><button class="act mini" data-leadclear="'
        + esc(l.sid) + '">Clear link</button>'
        + '<span class="said" id="say-l-' + esc(l.sid) + '"></span></div>';
    }
    var sg = sugFor(l);
    if(sg){
      var sd = dirRow(sg[0]);
      if(sd) h += '<div class="facts"><span>looks like existing '
        + 'customer <button class="bk" data-goacct="' + esc(sd.c) + '">'
        + esc(sd.n) + '</button> <span class="muted">('
        + Math.round(sg[1] * 100) + '% ' + esc(sg[2]) + ' · account is '
        + esc(String(sd.s).replace(/_/g, ' ')) + ')</span></span></div>'
        + '<div class="acts"><button class="act" data-leadconfirm="'
        + esc(l.sid) + '" data-code="' + esc(sd.c)
        + '">Confirm — same business</button>'
        + '<button class="act mini" data-leadclear="' + esc(l.sid)
        + '">Not a match</button></div>';
    }
    return h + '<div class="matchform">'
      + '<input class="act" list="acctnames" id="lm-' + esc(l.sid)
      + '" placeholder="Type the Fresho account name…">'
      + '<button class="act" data-leadmatch="' + esc(l.sid)
      + '">Link account</button><span class="said" id="say-l-' + esc(l.sid)
      + '"></span></div>';
  }

  function renderLeadPage(sid){
    var host = document.getElementById('lead-page');
    var l = leadBySid(sid);
    if(!host || !l) return;
    var v = leadView(l), e = esc(sid);
    var h = '<button class="bk" data-tab="leads">&#8592; All leads</button>';
    h += '<div class="ahead"><h2>' + esc(lname(l)) + '</h2>' + stars(l.rating)
      + leadChip(v.st) + dueBadge(v)
      + (l.excl ? '<span class="pill">' + esc(l.excl) + '</span>' : '') + '</div>';
    h += '<div class="ar">'
      + (l.type ? '<span class="chip">' + esc(l.type) + '</span>' : '')
      + (l.src ? '<span class="chip">' + esc(l.src) + '</span>' : '')
      + (l.heard ? '<span class="chip">heard via ' + esc(l.heard) + '</span>' : '')
      + '<span class="chip">enquired ' + esc(fmtDay(dayOf(l.recv || l.date)))
      + '</span></div>';

    if(v.verdict){
      h += '<div class="lbox"><div class="dh">AI VERDICT'
        + (v.vsrc === 'team' ? ' (changed by the team)' : '') + '</div>'
        + '<div>' + verdictChip(v.verdict) + ' <span class="said">'
        + (v.vsrc === 'team' ? 'Set by the team.' + (l.vwhy ? ' The AI's note: '
           + esc(l.vwhy) : '') : esc(l.vwhy)) + '</span></div>'
        + (l.urg ? '<p class="said" style="margin:.3rem 0 0">Urgency: '
           + esc(l.urg) + '</p>' : '')
        + '<div class="acts">'
        + (v.rejectCheck ? rejectBtns(l)
           : '<label class="said">Change verdict <select class="act" data-lverdict="'
             + e + '">' + (DATA.leadVerdicts || []).map(function(x){
               return '<option' + (x === v.verdict ? ' selected' : '') + '>'
                 + esc(x) + '</option>'; }).join('') + '</select></label>')
        + '</div></div>';
    }
    h += '<div class="dh">STAGE</div><div class="stepper" role="group" '
      + 'aria-label="Lead stage">';
    STAGES.forEach(function(s){
      var i = FUNNEL.indexOf(s[0]);
      var cls = s[0] === 'Discarded' ? 'lost'
        : (i >= 0 && i < v.reached ? 'done' : '');
      h += '<button class="' + cls + '" data-lstage="' + esc(s[0]) + '" data-sid="'
        + e + '" aria-current="' + (v.st === s[0] ? 'true' : 'false') + '">'
        + esc(s[1]) + '</button>';
    });
    h += '</div>';
    if(v.st === 'Discarded') h += '<p class="said">Lost'
      + (v.lost ? ' — ' + esc(v.lost) : '') + '</p>';
    if(openLostFor === sid){
      h += '<div class="acts"><select class="act" id="lr-' + e
        + '" aria-label="Why was it lost">' + (DATA.leadLost || []).map(function(r){
          return '<option>' + esc(r) + '</option>'; }).join('')
        + '</select><button class="act" data-lostconfirm="' + e
        + '">Mark lost</button><button class="act mini" data-lostcancel="' + e
        + '">Cancel</button></div>';
    }
    if(v.st === 'Acquired' && !effMatch(l)) h += '<p class="said">Won — link the'
      + ' Fresho account below so its orders and sales count for this lead.</p>';
    h += '<div><span class="said" id="say-lp-' + e + '"></span></div>';

    h += '<div class="lpgrid"><div class="lbox"><div class="dh">NEXT FOLLOW-UP</div>';
    if(v.open){
      h += '<div>' + (v.due ? '<b>' + esc(fmtDay(v.due)) + '</b> '
          + (v.dueIn > 0 ? '<span class="said">in ' + plural(v.dueIn, 'day')
             + '</span>' : dueBadge(v)) : '—') + '</div>'
        + '<p class="said" style="margin:.2rem 0 0">' + (v.auto
          ? 'Automatic — ' + plural(v.ruleDays, 'day')
            + ' after the last touch (' + esc(fmtDay(v.touch)) + ') while '
            + esc((STAGE_LBL[v.st] || v.st).toLowerCase()) + '.'
          : 'Set by hand.') + '</p>'
        + '<div class="acts"><input class="act" type="date" data-lfudate="' + e
        + '" value="' + esc(v.next) + '" aria-label="Follow-up date">'
        + '<button class="act mini" data-lfu="1" data-sid="' + e + '">Tomorrow</button>'
        + '<button class="act mini" data-lfu="3" data-sid="' + e + '">In 3 days</button>'
        + '<button class="act mini" data-lfu="7" data-sid="' + e + '">Next week</button>'
        + (v.next ? '<button class="act mini" data-lfu="clear" data-sid="' + e
           + '">Use the rule</button>' : '') + '</div>';
    } else {
      h += '<p class="said">None — this lead is '
        + esc((STAGE_LBL[v.st] || v.st).toLowerCase()) + '.</p>';
    }
    h += '</div><div class="lbox"><div class="dh">OWNER</div>'
      + '<select class="act" data-lowner="' + e + '" aria-label="Lead owner">'
      + '<option value="">Nobody yet</option>'
      + (DATA.leadTeam || []).map(function(n){
          return '<option' + (v.owner === n ? ' selected' : '') + '>' + esc(n)
            + '</option>'; }).join('')
      + '</select><div class="dh">YOU ARE</div>'
      + '<select class="act" data-whoami aria-label="Who are you">'
      + whoamiOptions() + '</select></div></div>';

    h += '<div class="lbox"><div class="dh">LOG WHAT YOU DID</div>'
      + '<input class="act noteinput" style="width:100%" id="ln-' + e
      + '" maxlength="500" placeholder="What happened? (optional for calls and emails)">'
      + '<div class="acts">'
      + Object.keys(CONTACT_KINDS).map(function(k){
          return '<button class="act" data-lact="' + k + '" data-sid="' + e + '">'
            + esc(CONTACT_KINDS[k]) + '</button>'; }).join('')
      + '<button class="act" data-lnote="' + e + '">Add note</button></div></div>';

    var wa = waNumber(l.phone);
    h += '<div class="dh">CONTACT</div><div class="facts">'
      + (l.name ? '<span><b>' + esc(l.name) + '</b></span>' : '')
      + (l.email ? '<span><a href="mailto:' + esc(l.email) + '?subject='
         + encodeURIComponent('Your enquiry with Rushton’s') + '">'
         + esc(l.email) + '</a></span>' : '')
      + (l.phone ? '<span><a href="tel:' + esc(l.phone.replace(/[^\d+]/g, ''))
         + '">' + esc(l.phone) + '</a></span>' : '')
      + (wa ? '<span><a href="https://wa.me/' + wa
         + '" target="_blank" rel="noopener">WhatsApp</a></span>' : '')
      + '</div>';

    var acts = v.acts.slice();
    acts.push({at: l.recv || l.date, kind: 'received',
      note: 'via ' + (l.src || 'unknown') + (l.web ? ' (website form)' : '')});
    acts.sort(function(x, y){ return x.at < y.at ? 1 : (x.at > y.at ? -1 : 0); });
    h += '<div class="dh">TIMELINE</div><div class="tl">'
      + acts.map(actLine).join('') + '</div>';
    if(!v.acts.length && l.status && l.status !== 'New' && !l.web)
      h += '<p class="said">Stage ' + esc(STAGE_LBL[l.status] || l.status)
        + ' came from the old leads sheet.</p>';

    if(l.pc || l.site) h += '<div class="facts">'
      + (l.pc ? '<span>postcode <b>' + esc(l.pc) + '</b></span>' : '')
      + (l.site ? '<span><a href="' + esc(l.site) + '" target="_blank" rel="noopener">'
         + esc(l.site.replace(/^https?:\/\//, '')) + '</a></span>' : '') + '</div>';
    if(l.srcs && l.srcs.length) h += '<details class="help"><summary>Sources the AI'
      + ' used (' + l.srcs.length + ')</summary><ol class="said">'
      + l.srcs.map(function(u){ return '<li><a href="' + esc(u)
        + '" target="_blank" rel="noopener">' + esc(u) + '</a></li>'; }).join('')
      + '</ol></details>';
    if(l.analysis) h += '<div class="dh">AI RESEARCH</div><div class="draft">'
      + esc(l.analysis) + '</div>';
    if(l.notes || l.details) h += '<div class="dh">NOTES FROM THE OLD SHEET</div>'
      + '<p class="said">' + esc([l.notes, l.details].filter(Boolean).join(' — '))
      + '</p>';
    h += '<div class="dh">FRESHO ACCOUNT</div>' + freshoBlock(l);
    host.innerHTML = h;
  }

  function logLead(sid, kind, extra){
    var me = whoAmI();
    if(!me){
      if(curLead) renderLeadPage(curLead);
      say(sid, 'Choose who you are (the "You are" box) first', 'lp');
      var w = document.querySelector('#sec-lead [data-whoami]');
      if(w) w.focus();
      return false;
    }
    var ev = {id: 'll|' + Date.now().toString(36)
        + Math.random().toString(36).slice(2, 7),
      sid: sid, kind: kind, at: new Date().toISOString(), actor: me};
    for(var k in extra) ev[k] = extra[k];
    STATE.leadLog.push(ev);
    rerenderLeads();
    scheduleSave(sid, 'lp');
    return true;
  }
  function rerenderLeads(){
    if(curLead) renderLeadPage(curLead);
    renderLeads();
    renderLeadRecs();
  }

  function openRow(code){
    var row = document.querySelector('.row[data-code="' + cssEsc(code) + '"]');
    if(!row) return false;
    row.classList.add('open');
    var b = row.querySelector('.rowhead');
    if(b) b.setAttribute('aria-expanded','true');
    return true;
  }

  function renderContacts(){
    var host = document.getElementById('conlist');
    if(!host) return;
    var qEl = document.getElementById('conq');
    var q = qEl && qEl.value ? qEl.value.toLowerCase() : '';
    var rows = [];
    (DATA.directory || []).forEach(function(d){
      contactsFor(d.c).forEach(function(ct){ rows.push({d: d, ct: ct}); });
    });
    rows = rows.filter(function(r){
      if(!q) return true;
      return (r.ct.n + ' ' + r.ct.e + ' ' + r.ct.p + ' ' + r.d.n)
        .toLowerCase().indexOf(q) >= 0;
    });
    var cnt = document.getElementById('concount');
    if(cnt) cnt.textContent = rows.length + ' contact'
      + (rows.length === 1 ? '' : 's') + ' on file';
    host.innerHTML = rows.map(function(r){
      return '<div class="contact" style="padding:8px 4px;border-bottom:.5px solid var(--line)">'
        + '<b>' + esc(r.ct.n || '(no name)') + '</b>'
        + (r.ct.l ? '<span class="muted">' + esc(r.ct.l) + '</span>' : '')
        + '<button class="bk" data-goacct="' + esc(r.d.c) + '">' + esc(r.d.n)
        + '</button>'
        + (r.ct.e ? '<a href="mailto:' + esc(r.ct.e) + '">' + esc(r.ct.e) + '</a>' : '')
        + (r.ct.p ? '<a href="https://wa.me/'
           + esc(String(r.ct.p).replace(/\D/g, '')) + '" target="_blank" '
           + 'rel="noopener">' + esc(r.ct.p) + '</a>' : '')
        + (r.ct.pend ? '<span class="pend">(saves weekly)</span>' : '')
        + '</div>';
    }).join('') || '<p class="said">No contacts yet — add them from any '
      + 'account page.</p>';
  }

  // Follow-ups: every open lead's next date, grouped; plus links to check.
  function renderLeadRecs(){
    var host = document.getElementById('lrlist');
    var me = whoAmI();
    var mine = !!(document.getElementById('fumine') || {}).checked;
    var g = {over: [], today: [], soon: [], quiet: []}, checks = [], dueAll = 0;
    var rejects = [];
    (DATA.leads || []).forEach(function(l){
      if(l.excl) return;
      var v = leadView(l);
      if(v.open && !v.quiet && v.dueIn !== null && v.dueIn <= 0) dueAll++;
      if(v.rejectCheck) dueAll++;
      var sg = sugFor(l);
      if(sg){
        var sd = dirRow(sg[0]);
        if(sd) checks.push({l: l, v: v, verb: 'Confirm', why: 'looks like existing'
          + ' customer ' + sd.n + ' (' + Math.round(sg[1] * 100) + '% ' + sg[2]
          + ') — confirm it on the lead page'});
      } else if(v.st === 'Acquired' && !effMatch(l)){
        checks.push({l: l, v: v, verb: 'Link',
          why: 'won but not linked to a Fresho account yet'});
      }
      if(!v.open) return;
      if(mine && v.owner && v.owner !== me) return;
      var it = {l: l, v: v};
      if(v.rejectCheck){ rejects.push(it); return; }
      if(v.quiet) g.quiet.push(it);
      else if(v.dueIn === null || v.dueIn > 7) return;
      else if(v.dueIn < 0) g.over.push(it);
      else if(v.dueIn === 0) g.today.push(it);
      else g.soon.push(it);
    });
    var badge = document.getElementById('fu-count');
    if(badge) badge.textContent = dueAll ? String(dueAll) : '';
    var hdot = document.getElementById('hdot');
    if(hdot) hdot.hidden = !dueAll;
    if(!host) return;
    var byDue = function(a, b){
      return (a.v.dueIn - b.v.dueIn) || ((b.l.rating || 0) - (a.l.rating || 0)); };
    var card = function(it, verb, why){
      var l = it.l, v = it.v;
      return '<div class="card"><div class="crow">'
        + '<span class="cverb">' + esc(verb || VERB[v.st] || 'Follow up') + '</span>'
        + '<button class="cname" data-golead="' + esc(l.sid) + '">'
        + esc(lname(l)) + '</button>' + stars(l.rating) + verdictChip(v.verdict)
        + dueBadge(v) + '</div>'
        + '<div class="cwhy">' + esc(why || ((STAGE_LBL[v.st] || v.st)
          + ', last touched ' + fmtDay(v.touch)
          + (v.auto ? ' · ' + ((DATA.leadFollowUp || {})[v.st] || 7) + '-day rule'
             : ' · date set by hand')
          + ' · ' + (v.owner || 'nobody owns it yet')
          + (l.src ? ' · ' + l.src : ''))) + '</div></div>';
    };
    var block = function(title, items){
      if(!items.length) return '';
      return '<h3>' + title + ' (' + items.length + ')</h3>'
        + items.sort(byDue).map(function(it){ return card(it); }).join('');
    };
    var h = '';
    if(rejects.length) h += '<h3>AI rejected — check (' + rejects.length + ')</h3>'
      + rejects.map(function(it){
          return '<div class="card"><div class="crow"><span class="cverb">Check</span>'
            + '<button class="cname" data-golead="' + esc(it.l.sid) + '">'
            + esc(lname(it.l)) + '</button>' + verdictChip('Reject')
            + '<span class="cbtns">' + rejectBtns(it.l) + '</span></div>'
            + '<div class="cwhy">' + esc(it.l.vwhy || 'rejected by the AI')
            + '</div></div>'; }).join('');
    h += block('Overdue', g.over) + block('Due today', g.today)
      + block('Next 7 days', g.soon);
    if(!h) h = '<p class="said">Nothing due in the next week'
      + (mine ? ' for you' : '') + '.</p>';
    if(checks.length) h += '<h3>To check (' + checks.length + ')</h3>'
      + checks.map(function(c){ return card(c, c.verb, c.why); }).join('');
    if(g.quiet.length) h += '<details class="help"><summary>Gone quiet — open but'
      + ' untouched for ' + (DATA.leadQuietDays || 60) + '+ days (' + g.quiet.length
      + '): revive or mark Lost</summary>'
      + g.quiet.sort(function(a, b){ return a.v.touch < b.v.touch ? 1 : -1; })
        .map(function(it){ return card(it, 'Revive or close'); }).join('')
      + '</details>';
    host.innerHTML = h;
  }

  function rerenderAll(keepRowOpen){
    renderActions();
    render('list-decline', DATA.declineOrder);
    render('list-winback', DATA.winbackOrder);
    renderDir();
    renderSales();
    renderLeads();
    renderContacts();
    renderLeadRecs();
    if(curAcct) renderAccount(curAcct);
    if(curLead) renderLeadPage(curLead);
    if(keepRowOpen) openRow(keepRowOpen);
  }

  function copy(code, which, ctx){
    var a = DATA.accounts[code];
    if(!a) return;
    var txt = which === 'em' ? (a.es + '\n\n' + a.eb) : a.wa;
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(txt).then(function(){ say(code, 'Copied', ctx); },
        function(){ say(code, 'Select the text above to copy', ctx); });
    } else { say(code, 'Select the text above to copy', ctx); }
  }

  function logEvent(code, kind, note, ctx){
    STATE.log.push({
      id: 'l|' + code + '|' + Date.now().toString(36)
          + Math.random().toString(36).slice(2,6),
      code: code, kind: kind, at: new Date().toISOString(),
      note: note || '', actor: ownerOf(code) });
    rerenderAll(ctx === 'r' ? code : null);
    scheduleSave(code, ctx);
  }

  var publishing = false;
  var saveTimer = null;
  function scheduleSave(code, ctx){
    if(saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(function(){ saveTimer = null; save(code, ctx); }, 700);
  }

  function assign(code, rep){
    if(rep) STATE.assign[code] = rep; else delete STATE.assign[code];
    if(rep){ logEvent(code, 'assigned', rep); }
    else { rerenderAll(code); scheduleSave(code); }
  }

  function save(code, ctx){
    if(publishing) return;
    publishing = true;
    Promise.resolve(window.claude && window.claude.use
      ? window.claude.use('artifact') : null).then(function(art){
      if(!art){ say(code, 'Saved on this screen only', ctx); publishing = false; return; }
      // sj() mirrors Python's _safe_json: JSON.stringify alone leaves "</"
      // intact, which would close this very <script> tag when re-embedded.
      var sj = function(o){ return JSON.stringify(o).replace(/<\//g, '<\\/'); };
      var doc = TEMPLATE
        .replace('__STATE__', function(){ return sj(STATE); })
        .replace('__TEMPLATE__', function(){ return sj(TEMPLATE); });
      return art.publish(doc).then(function(){ say(code, 'Saved for everyone', ctx); },
        function(){ say(code, 'Saved on this screen only', ctx); });
    }).catch(function(){ say(code, 'Saved on this screen only', ctx); })
      .then(function(){ publishing = false; });
  }

  if(hamb) hamb.addEventListener('click', openNav);
  document.addEventListener('keydown', function(ev){
    if(ev.key === 'Escape' && sitenav && sitenav.classList.contains('open')){
      closeNav();
      if(hamb) hamb.focus();
    }
  });
  document.addEventListener('click', function(ev){
    var nc = ev.target.closest && ev.target.closest('[data-navclose]');
    if(nc){ closeNav(); if(hamb) hamb.focus(); return; }
    var tb = ev.target.closest && ev.target.closest('[data-tab]');
    if(tb){ showTab(tb.getAttribute('data-tab')); return; }
    var ct = ev.target.closest && ev.target.closest('[data-cardtoggle]');
    if(ct){
      var ccode = ct.getAttribute('data-cardtoggle');
      openCards[ccode] = !openCards[ccode];
      renderActions();
      return;
    }
    var cp = ev.target.closest && ev.target.closest('[data-copy]');
    if(cp){ copy(cp.getAttribute('data-code'), cp.getAttribute('data-copy'),
                 cp.getAttribute('data-ctx') || 'r'); return; }
    var lg = ev.target.closest && ev.target.closest('[data-log]');
    if(lg){
      logEvent(lg.getAttribute('data-code'), lg.getAttribute('data-log'),
               lg.getAttribute('data-lognote') || '',
               lg.getAttribute('data-ctx') || 'c');
      return;   // mailto anchors: default navigation still fires
    }
    var na = ev.target.closest && ev.target.closest('[data-noteadd]');
    if(na){
      var ncode = na.getAttribute('data-noteadd');
      var nctx = na.getAttribute('data-ctx') || 'r';
      var inp = document.getElementById('ni-' + nctx + '-' + ncode);
      var txt = inp && inp.value ? inp.value.trim() : '';
      if(!txt){ say(ncode, 'Type the note first', nctx); return; }
      logEvent(ncode, 'note', txt, nctx);
      return;
    }
    var ab = ev.target.closest && ev.target.closest('[data-actdo]');
    if(ab){
      var id = ab.getAttribute('data-id'), op = ab.getAttribute('data-actdo');
      if(op === 'undo') delete STATE.actions[id];
      else STATE.actions[id] = { s: op, at: new Date().toISOString() };
      renderActions();
      scheduleSave(id, 'c');
      return;
    }
    var ch = ev.target.closest && ev.target.closest('[data-chan]');
    if(ch){
      var box = ch.closest('.comms');
      var which = ch.getAttribute('data-chan');
      box.querySelectorAll('.ctabs button').forEach(function(b){
        b.setAttribute('aria-current',
          b.getAttribute('data-chan') === which ? 'true' : 'false');
      });
      box.querySelectorAll('.cpane').forEach(function(p){
        p.classList.toggle('on', p.getAttribute('data-pane') === which);
      });
      return;
    }
    var acB = ev.target.closest && ev.target.closest('[data-addcontact]');
    if(acB){
      var acode = acB.getAttribute('data-addcontact');
      var g = function(pfx){
        var el = document.getElementById(pfx + '-' + acode);
        return el && el.value ? el.value.trim() : '';
      };
      var em = g('ce').toLowerCase(), phn = g('cp');
      if(!em && !phn){ say(acode, 'Email or phone required', 'a'); return; }
      if(em && em.indexOf('@') < 0){ say(acode, 'That email looks wrong', 'a'); return; }
      STATE.contacts.push({ id: 'c|' + acode + '|' + Date.now().toString(36),
        code: acode, name: g('cn'), label: g('cl'), email: em, phone: phn });
      renderDir(acode);
      scheduleSave(acode, 'a');
      return;
    }
    var ga = ev.target.closest && ev.target.closest('[data-goacct]');
    if(ga){
      location.hash = 'acct/' + encodeURIComponent(ga.getAttribute('data-goacct'));
      return;
    }
    var gl = ev.target.closest && ev.target.closest('[data-golead]');
    if(gl){
      location.hash = 'lead/' + encodeURIComponent(gl.getAttribute('data-golead'));
      return;
    }
    var fr = ev.target.closest && ev.target.closest('[data-funnel]');
    if(fr){
      var fsel = document.getElementById('leadstatus');
      if(fsel) fsel.value = fr.getAttribute('data-funnel');
      if(!document.getElementById('sec-leads').classList.contains('on'))
        showTab('leads');
      renderLeads();
      var ll = document.getElementById('leadlist');
      if(ll) ll.scrollIntoView({block: 'start'});
      return;
    }
    var lsg = ev.target.closest && ev.target.closest('[data-lstage]');
    if(lsg){
      var gsid = lsg.getAttribute('data-sid'), gst = lsg.getAttribute('data-lstage');
      if(gst === leadView(leadBySid(gsid)).st) return;
      if(gst === 'Discarded'){ openLostFor = gsid; renderLeadPage(gsid); return; }
      openLostFor = null;
      logLead(gsid, 'stage', {stage: gst});
      return;
    }
    var lok = ev.target.closest && ev.target.closest('[data-lostconfirm]');
    if(lok){
      var osid = lok.getAttribute('data-lostconfirm');
      var rsel = document.getElementById('lr-' + osid);
      openLostFor = null;
      logLead(osid, 'stage', {stage: 'Discarded', reason: rsel ? rsel.value : ''});
      return;
    }
    var lcn = ev.target.closest && ev.target.closest('[data-lostcancel]');
    if(lcn){
      openLostFor = null;
      renderLeadPage(lcn.getAttribute('data-lostcancel'));
      return;
    }
    var lfu = ev.target.closest && ev.target.closest('[data-lfu]');
    if(lfu){
      var k = lfu.getAttribute('data-lfu');
      logLead(lfu.getAttribute('data-sid'), 'follow_up',
        {due: k === 'clear' ? '' : addDays(todayIso(), parseInt(k, 10))});
      return;
    }
    var lac = ev.target.closest && ev.target.closest('[data-lact]');
    if(lac){
      var asid = lac.getAttribute('data-sid'), akind = lac.getAttribute('data-lact');
      var ain = document.getElementById('ln-' + asid);
      var before = leadView(leadBySid(asid)).st;
      if(!logLead(asid, akind, {note: ain ? ain.value.trim() : ''})) return;
      // doing something with a lead moves it along the funnel
      if(akind === 'sample' && OPEN.indexOf(before) >= 0 && before !== 'Trial')
        logLead(asid, 'stage', {stage: 'Trial'});
      else if(before === 'New') logLead(asid, 'stage', {stage: 'Contacted'});
      return;
    }
    var vlo = ev.target.closest && ev.target.closest('[data-vlost]');
    if(vlo){
      var vsid = vlo.getAttribute('data-vlost');
      logLead(vsid, 'stage', {stage: 'Discarded',
        reason: lostReasonFor((leadBySid(vsid) || {}).vwhy || '')});
      return;
    }
    var vpu = ev.target.closest && ev.target.closest('[data-vpursue]');
    if(vpu){
      logLead(vpu.getAttribute('data-vpursue'), 'verdict', {note: 'Pursue'});
      return;
    }
    var lnt = ev.target.closest && ev.target.closest('[data-lnote]');
    if(lnt){
      var nsid = lnt.getAttribute('data-lnote');
      var nin = document.getElementById('ln-' + nsid);
      var txt = nin ? nin.value.trim() : '';
      if(!txt){ say(nsid, 'Type the note first', 'lp'); return; }
      logLead(nsid, 'note', {note: txt});
      return;
    }
    var lm = ev.target.closest && ev.target.closest('[data-leadmatch]');
    if(lm){
      var msid = lm.getAttribute('data-leadmatch');
      var inp = document.getElementById('lm-' + msid);
      var typed = inp && inp.value ? inp.value.trim().toLowerCase() : '';
      if(!typed){ say(msid, 'Type the account name first', 'l'); return; }
      var mcode = nameToCode[typed];
      if(!mcode){ say(msid, 'No account with that exact name — pick from the list', 'l'); return; }
      STATE.leadMatch[msid] = mcode;
      rerenderLeads();
      scheduleSave(msid, 'l');
      return;
    }
    var lcf = ev.target.closest && ev.target.closest('[data-leadconfirm]');
    if(lcf){
      STATE.leadMatch[lcf.getAttribute('data-leadconfirm')] =
        lcf.getAttribute('data-code');
      rerenderLeads();
      scheduleSave(lcf.getAttribute('data-leadconfirm'), 'l');
      return;
    }
    var lc = ev.target.closest && ev.target.closest('[data-leadclear]');
    if(lc){
      STATE.leadMatch[lc.getAttribute('data-leadclear')] = '';
      rerenderLeads();
      scheduleSave(lc.getAttribute('data-leadclear'), 'l');
      return;
    }
    var head = ev.target.closest && ev.target.closest('.rowhead');
    if(head){
      var row = head.parentNode;
      var open = row.classList.toggle('open');
      head.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
  });
  document.addEventListener('change', function(ev){
    var sel = ev.target.closest && ev.target.closest('[data-assign]');
    if(sel) assign(sel.getAttribute('data-assign'), sel.value);
    var wh = ev.target.closest && ev.target.closest('[data-whoami]');
    if(wh){ setMe(wh.value); fillWhoami(); renderLeadRecs(); return; }
    var low = ev.target.closest && ev.target.closest('[data-lowner]');
    if(low){ logLead(low.getAttribute('data-lowner'), 'owner', {owner: low.value}); }
    var lvs = ev.target.closest && ev.target.closest('[data-lverdict]');
    if(lvs){ logLead(lvs.getAttribute('data-lverdict'), 'verdict', {note: lvs.value}); }
    var lfd = ev.target.closest && ev.target.closest('[data-lfudate]');
    if(lfd && lfd.value){
      logLead(lfd.getAttribute('data-lfudate'), 'follow_up', {due: lfd.value}); }
  });

  var dq = document.getElementById('dirq');
  if(dq) dq.addEventListener('input', function(){ renderDir(); });
  ['dirstatus', 'dirtier', 'dirsort'].forEach(function(id){
    var el = document.getElementById(id);
    if(el) el.addEventListener('change', function(){ renderDir(); });
  });
  initLeadFilters();
  ['leadstatus', 'leadowner', 'leadverdict', 'leadrating', 'leadexcl'].forEach(function(id){
    var el = document.getElementById(id);
    if(el) el.addEventListener('change', function(){ renderLeads(); });
  });
  var lq = document.getElementById('leadq');
  if(lq) lq.addEventListener('input', function(){ renderLeads(); });
  var fm = document.getElementById('fumine');
  if(fm) fm.addEventListener('change', function(){ renderLeadRecs(); });
  var cq = document.getElementById('conq');
  if(cq) cq.addEventListener('input', function(){ renderContacts(); });

  var dl = '<datalist id="acctnames">' + (DATA.directory || []).map(function(d){
    return '<option value="' + esc(d.n) + '"></option>'; }).join('')
    + '</datalist>';
  document.body.insertAdjacentHTML('beforeend', dl);

  window.addEventListener('hashchange', route);
  rerenderAll(null);
  route();
})();
</script>"""


def write(as_of: date | None = None, state: dict | None = None) -> str:
    """Build the dashboard and write it to EXPORT_DIR. Returns the path."""
    path = config.EXPORT_DIR / OUT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(as_of, state), encoding="utf-8")
    log.info("commercial dashboard written: %s", path)
    return str(path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(write())
