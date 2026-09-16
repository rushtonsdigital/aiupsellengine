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

Every measure is VOLUME (orders, order lines) — there is no price data anywhere.
Outreach drafts are written by Claude against the rushtons-comms skill and read
from output/outreach_drafts.json; Python never invents customer-facing words.
"""

import html
import json
import logging
from datetime import date, timedelta

import config
import query

TARGETABLE = list(config.TARGETABLE_CATEGORIES)

log = logging.getLogger(__name__)

OUT_NAME = "commercial_dashboard.html"
DRAFTS_NAME = "outreach_drafts.json"

GOLD, SILVER = "#c98500", "#2a78d6"
AMBER, RED, GREY = "#c98500", "#d03b3b", "#898781"

RAMP = [(91, "#0C447C", "#E6F1FB"), (76, "#185FA5", "#E6F1FB"),
        (56, "#378ADD", "#042C53"), (31, "#85B7EB", "#042C53"),
        (11, "#B5D4F4", "#0C447C"), (0, "#E6F1FB", "#0C447C")]

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
  select customer_code, customer_name, venue_type, size_band, sales_rep
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
order by n.pct desc, a.customer_name
"""

Q_PEER_TOTAL = _PEER_BASE + """
select count(*) as gaps, count(distinct a.customer_code) as accounts
from norm n
join active a on a.venue_type = n.venue_type
left join customer_category_metrics c
       on c.customer_code = a.customer_code and c.category = n.category
where c.customer_code is null and n.pct >= 70 and a.size_band in ('gold','silver')
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
       c.internal_notes, c.group_affiliation, c.account_stage
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
       matched_customer_code, match_method
from leads order by submitted_at desc nulls last
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
            f'<polygon points="{area}" fill="{SILVER}" opacity="0.13"/>'
            f'<polyline points="{line}" fill="none" stroke="{SILVER}" '
            f'stroke-width="1.6" stroke-linejoin="round"/>{zeros}'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="{SILVER}"/></svg>')


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
                 f'fill="{SILVER}" opacity="0.35"/>')
        p.append(f'<rect x="{lab_w}" y="{y}" width="{lw:.0f}" height="18" rx="4" fill="{RED}"/>')
        p.append(f'<text x="{lab_w+bw+10:.0f}" y="{y+13}" class="tick">'
                 f'{acc} accounts &#183; {lap} lapsed ({int(r["lapsed_pct"] or 0)}%) &#183; '
                 f'{r["avg_open_gaps"]} avg gaps</text>')
    p.append("</svg>")
    return "".join(p)


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


def _load_leads() -> list:
    """Leads for the dashboard, analysis trimmed (full text stays in the DB)."""
    try:
        rows = _rows(Q_LEADS, limit=2000)
    except Exception:
        log.warning("leads table not readable yet - leads pane empty")
        return []
    out = []
    for r in rows:
        analysis = (r["analysis"] or "").strip()
        out.append({
            "sid": r["source_id"], "name": r["name"] or "",
            "biz": r["business"] or "", "email": r["email"] or "",
            "phone": r["phone"] or "", "src": r["lead_source"] or "",
            "date": str(r["submitted_at"] or ""), "status": r["status"] or "",
            "rating": r["rating"], "type": r["type"] or "",
            "excl": r["excluded_reason"] or "",
            "matched": r["matched_customer_code"] or "",
            "method": r["match_method"] or "",
            "analysis": analysis[:420] + ("…" if len(analysis) > 420 else ""),
            "notes": (r["notes"] or "")[:200],
            "details": (r["details"] or "")[:200],
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
    } for r in _rows(Q_DIRECTORY, limit=1000)]

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
    }

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
family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&\
family=IBM+Plex+Serif:wght@400;500&display=swap">
</head><body>"""

TAIL_SRC = "</body></html>"

FRAG_SRC = r"""<title>Rushton's Commercial Dashboard</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@400;500&display=swap">
<style>
:root {
  --bg:#f6f8f4; --surface:#ffffff; --raise:#fbfcfa; --ink:#151915; --muted:#5f6862;
  --line:rgba(20,28,22,.13); --grid:#dfe4dc; --accent:#185FA5;
  --sans:"IBM Plex Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  --serif:"IBM Plex Serif",Georgia,"Times New Roman",serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Consolas,monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#111512; --surface:#1a1f1b; --raise:#212722; --ink:#eaeee8; --muted:#98a29a;
    --line:rgba(255,255,255,.13); --grid:#2a312c; --accent:#85B7EB;
  }
}
:root[data-theme="dark"] {
  --bg:#111512; --surface:#1a1f1b; --raise:#212722; --ink:#eaeee8; --muted:#98a29a;
  --line:rgba(255,255,255,.13); --grid:#2a312c; --accent:#85B7EB;
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font-family:var(--sans);
  line-height:1.6; -webkit-font-smoothing:antialiased; }
.wrap { max-width:1080px; margin:0 auto; padding:2.2rem 1.25rem 4rem; }
h1 { font-family:var(--serif); font-size:28px; font-weight:500; margin:0 0 .3rem;
  letter-spacing:-.01em; text-wrap:balance; }
h2 { font-family:var(--serif); font-size:20px; font-weight:500; margin:0 0 .35rem;
  text-wrap:balance; }
.sub { color:var(--muted); font-size:14px; margin:0 0 1.6rem; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:12px; margin:0 0 1.6rem; }
.kpi { background:var(--surface); border:.5px solid var(--line); border-radius:12px;
  padding:1rem 1.1rem; cursor:pointer; text-align:left; font:inherit;
  color:inherit; }
.kpi:hover { border-color:var(--accent); }
.kpi:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.kpi .n { font-family:var(--mono); font-size:31px; font-weight:500;
  letter-spacing:-.03em; font-variant-numeric:tabular-nums; line-height:1.2; }
.kpi .l { font-size:13px; color:var(--muted); line-height:1.45; }
.layout { display:block; }
.nav { display:flex; gap:6px; overflow-x:auto; position:sticky; top:0;
  background:var(--bg); padding:.6rem 0; z-index:5; margin:0 0 1rem; }
.nav button { font:inherit; font-size:13px; padding:7px 14px; border-radius:20px;
  border:.5px solid var(--line); background:var(--surface); color:var(--ink);
  cursor:pointer; white-space:nowrap; }
.nav button:hover { border-color:var(--accent); }
.nav button:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
.nav button[aria-current="true"] { background:var(--ink); color:var(--bg);
  border-color:var(--ink); }
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
.tag { display:inline-block; font-family:var(--mono); font-size:10.5px;
  letter-spacing:.11em; color:var(--muted); margin-bottom:.5rem; }
.lede { font-size:14px; color:var(--muted); margin:0 0 1.1rem; }
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
.tlk.good { color:#16a34a; }
.tls { color:var(--muted); min-width:0; }
.pend { font-size:10.5px; color:var(--muted); opacity:.75; }
.noteinput { flex:1; min-width:180px; cursor:text; }
.status { font-family:var(--mono); font-size:10.5px; padding:2px 8px;
  border-radius:20px; white-space:nowrap; border:.5px solid var(--line);
  color:var(--muted); font-variant-numeric:tabular-nums; }
.status.sent { color:#2a78d6; border-color:#2a78d6; }
.status.good { color:#16a34a; border-color:#16a34a; }
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
@media (prefers-reduced-motion: reduce) { .chev { transition:none; } }
</style>

<div class="wrap">
  <h1>Commercial dashboard</h1>
  <p class="sub">Growing value from existing accounts &#183; data to {{ASOF}} &#183;
     every measure is order volume, not revenue</p>

  <div class="kpis">
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
    <nav class="nav" aria-label="Sections">
      <span class="ng">CUSTOMERS</span>
      <button data-tab="accounts">Accounts</button>
      <button data-tab="contacts">Contacts</button>
      <span class="ng">ACQUIRE</span>
      <button data-tab="leads">Leads</button>
      <button data-tab="leadrecs">Lead recommendations</button>
      <span class="ng">GROW</span>
      <button data-tab="actions">This week</button>
      <button data-tab="protect">Protect</button>
      <button data-tab="recover">Recover</button>
      <button data-tab="grow">Peer gaps</button>
      <button data-tab="manage">Manage</button>
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
          <span class="sw" style="background:#E6F1FB"></span>
          <span class="sw" style="background:#B5D4F4"></span>
          <span class="sw" style="background:#85B7EB"></span>
          <span class="sw" style="background:#378ADD"></span>
          <span class="sw" style="background:#185FA5"></span>
          <span class="sw" style="background:#0C447C"></span>
          <span>saturated</span><span>&#183; % of active accounts buying</span></div>
        <div class="scroll"><table>
          <tr><th>Gold account</th><th>Type</th><th>Not buying</th>
              <th>Peer adoption</th><th>Rep</th></tr>
          {{GAP_ROWS}}
        </table></div>
      </div>
    </div>

    <div class="section" id="sec-accounts">
      <div class="play">
        <div class="tag">ACCOUNTS</div>
        <h2>All accounts</h2>
        <p class="lede">Every customer, with contacts and the full interaction
          history. Add a contact here and it saves for everyone (and unlocks the
          send buttons). Volume is order lines, never revenue.</p>
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
        <div class="tag">ACQUIRE &#183; RECOMMENDATIONS</div>
        <h2>Lead recommendations</h2>
        <p class="lede">What to do next with the pipeline, in priority order:
          confirm leads that already look like customers, link acquired leads,
          contact the highest-rated new leads, chase the stale ones.</p>
        <div id="lrlist"></div>
      </div>
    </div>

    <div class="section" id="sec-leads">
      <div class="play">
        <div class="tag">LEADS</div>
        <h2>Lead pipeline</h2>
        <p class="lede">Mirrored from the marketing leads sheet (n8n keeps it
          fed; update Status there). Acquired leads link to their Fresho account
          — automatically when the name matches, by hand below when it doesn't —
          and pull ongoing order volume. There is no price data, so value is
          orders and lines, not &#163;.</p>
        <div class="kpis" id="lead-kpis"></div>
        <div class="scroll" id="lead-ratings"></div>
        <div class="dirbar">
          <select class="act" id="leadstatus" aria-label="Filter by lead status">
            <option value="">All statuses</option>
            <option value="New">New</option>
            <option value="Contacted">Contacted</option>
            <option value="Acquired">Acquired</option>
            <option value="Discarded">Discarded</option>
          </select>
          <label class="said" style="display:flex;align-items:center;gap:6px;">
            <input type="checkbox" id="leadexcl"> show excluded (test/personal)
          </label>
        </div>
        <div class="list" id="leadlist"></div>
        <p class="note">Leads still New or Contacted after 21 days are flagged
          stale. Excluded rows (test entries, personal sample-box asks,
          internal addresses) stay out of the numbers above.</p>
      </div>
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

  <footer>Rushton's &#183; generated from the reporting layer &#183; volume measures
    only (no price data exists in the source) &#183; rebuilt with each weekly run.
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
              'leads','contacts','leadrecs'];
  var curAcct = null;
  function showTab(name){
    if(TABS.indexOf(name) < 0) name = 'actions';
    curAcct = null;
    var ap = document.getElementById('sec-account');
    if(ap) ap.classList.remove('on');
    TABS.forEach(function(t){
      var s = document.getElementById('sec-' + t);
      if(s) s.classList.toggle('on', t === name);
    });
    document.querySelectorAll('.nav button').forEach(function(b){
      b.setAttribute('aria-current', b.getAttribute('data-tab') === name
        ? 'true' : 'false');
    });
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
    curAcct = code;
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
    renderAccount(code);
    window.scrollTo(0, 0);
  }
  function route(){
    var h = location.hash.slice(1);
    if(h.indexOf('acct/') === 0){
      showAccount(decodeURIComponent(h.slice(5)));
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
    var col = g ? '#c98500' : '#2a78d6';
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
      + '<polygon points="' + area + '" fill="#2a78d6" opacity="0.13"/>'
      + '<polyline points="' + line + '" fill="none" stroke="#2a78d6" '
      + 'stroke-width="2" stroke-linejoin="round"/>' + zeros
      + '<circle cx="' + lp[0].toFixed(1) + '" cy="' + lp[1].toFixed(1)
      + '" r="3.5" fill="#2a78d6"/></svg>';
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
      + '<span class="chip">' + esc(String(d.s).replace(/_/g, ' ')) + '</span>'
      + (d.grp ? '<span class="chip">' + esc(d.grp) + '</span>' : '')
      + (d.stage ? '<span class="chip">' + esc(d.stage) + '</span>' : '')
      + '</div>';
    h += '<div class="agridfacts">'
      + fact('REP', d.r)
      + fact('FIRST SEEN', d.first)
      + fact('LAST ORDER', d.last
             + (d.ds !== null && d.ds !== undefined ? ' (' + d.ds + 'd ago)' : ''))
      + fact('TOTAL ORDERS', orders ? orders + ' (volume, not \u00a3)' : '')
      + fact('ORDER LINES', d.l ? String(d.l) : '')
      + fact('DELIVERY RUN', d.run)
      + fact('PAYMENT TERMS', d.terms !== null && d.terms !== undefined
             ? d.terms + ' days' : '')
      + fact('PRICING LEVEL', d.pricing)
      + fact('LEGAL ENTITY', d.legal)
      + fact('DELIVERY ADDRESS', d.addr)
      + (d.baddr && d.baddr !== d.addr ? fact('BILLING ADDRESS', d.baddr) : '')
      + fact('INTERNAL NOTES', d.inotes)
      + '</div>';
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
    var cnt = document.getElementById('dircount');
    if(cnt) cnt.textContent = rows.length + ' of '
      + (DATA.directory || []).length + ' accounts \u2014 click a card to open it';
    host.innerHTML = rows.map(function(d){
      return '<button class="acard" data-goacct="' + esc(d.c) + '">'
        + '<span class="an">' + esc(d.n) + '</span>'
        + '<span class="am">' + esc(d.v) + (d.t ? ' \u00b7 ' + esc(d.t) : '')
        + ' \u00b7 ' + esc(String(d.s).replace(/_/g, ' ')) + '</span>'
        + '<span class="ar">' + statusChip(d.c)
        + (STATE.assign[d.c]
           ? '<span class="owner">' + esc(STATE.assign[d.c]) + '</span>' : '')
        + (d.ds !== null && d.ds !== undefined
           ? '<span class="am">' + d.ds + 'd since order</span>' : '')
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
      var tc = c.tier === 'gold' ? '#c98500' : '#2a78d6';
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

  var openLead = null;
  var nameToCode = {};
  (DATA.directory || []).forEach(function(d){ nameToCode[d.n.toLowerCase()] = d.c; });

  function effMatch(l){
    return Object.prototype.hasOwnProperty.call(STATE.leadMatch, l.sid)
      ? (STATE.leadMatch[l.sid] || '') : (l.matched || '');
  }
  function sugFor(l){
    if(effMatch(l)) return null;
    if(Object.prototype.hasOwnProperty.call(STATE.leadMatch, l.sid)) return null;
    return l.sug || null;   // [code, score, how] — user dismissed = key present
  }
  function leadIsStale(l){
    if(l.status !== 'New' && l.status !== 'Contacted') return false;
    if(!l.date) return false;
    var cut = new Date(DATA.asOf); cut.setDate(cut.getDate() - 21);
    return new Date(l.date) < cut;
  }
  function leadChip(st){
    var c = st === 'Acquired' ? '#16a34a' : st === 'Contacted' ? '#2a78d6'
      : st === 'New' ? '#c98500' : '#898781';
    return '<span class="pill" style="background:' + c + '1f;color:' + c + '">'
      + esc(st || '—') + '</span>';
  }
  function stars(r){
    if(!r) return '<span class="muted">unrated</span>';
    return '<span class="stars">' + '★'.repeat(r) + '☆'.repeat(5 - r) + '</span>';
  }

  function renderLeads(){
    var host = document.getElementById('leadlist');
    if(!host) return;
    var all = DATA.leads || [];
    var included = all.filter(function(l){ return !l.excl; });
    var acq = included.filter(function(l){ return l.status === 'Acquired'; });
    var matched = included.filter(function(l){ return effMatch(l); });
    var stale = included.filter(leadIsStale);
    var sugs = included.filter(sugFor);
    var kp = document.getElementById('lead-kpis');
    if(kp) kp.innerHTML =
      '<div class="kpi" style="cursor:default"><span class="n">' + included.length
      + '</span><span class="l">commercial leads (' + (all.length - included.length)
      + ' excluded)</span></div>'
      + '<div class="kpi" style="cursor:default"><span class="n">' + acq.length
      + '</span><span class="l">marked acquired in the sheet</span></div>'
      + '<div class="kpi" style="cursor:default"><span class="n">' + matched.length
      + (sugs.length ? ' <span class="muted" style="font-size:15px">+'
         + sugs.length + '?</span>' : '')
      + '</span><span class="l">actual customers — '
      + (included.length ? Math.round(100 * matched.length / included.length) : 0)
      + '% real conversion' + (sugs.length ? ' · ' + sugs.length
      + ' suggested match' + (sugs.length > 1 ? 'es' : '') + ' to confirm' : '')
      + '</span></div>'
      + '<div class="kpi" style="cursor:default"><span class="n">' + stale.length
      + '</span><span class="l">stale — no movement in 21+ days</span></div>';
    var rt = document.getElementById('lead-ratings');
    if(rt){
      var rows = '';
      [5, 4, 3, 2, 1, null].forEach(function(band){
        var pool = included.filter(function(l){
          return band === null ? !l.rating : l.rating === band; });
        if(!pool.length) return;
        var a = pool.filter(function(l){ return l.status === 'Acquired'; }).length;
        rows += '<tr><td>' + (band === null ? 'Unrated'
          : '★'.repeat(band)) + '</td><td class="num">' + pool.length
          + '</td><td class="num">' + a + '</td><td class="num">'
          + Math.round(100 * a / pool.length) + '%</td></tr>';
      });
      rt.innerHTML = '<table><tr><th>Rating</th><th>Leads</th>'
        + '<th>Acquired</th><th>Conversion</th></tr>' + rows + '</table>';
    }
    var sf = (document.getElementById('leadstatus') || {}).value || '';
    var showEx = !!(document.getElementById('leadexcl') || {}).checked;
    var list = all.filter(function(l){
      return (showEx || !l.excl) && (!sf || l.status === sf);
    });
    host.innerHTML = list.map(function(l){
      var open = openLead === l.sid;
      var m = effMatch(l);
      var md = m ? dirRow(m) : null;
      var head = '<button class="rowhead" data-leadtoggle="' + esc(l.sid)
        + '" aria-expanded="' + (open ? 'true' : 'false')
        + '"><span class="chev">▶</span>'
        + '<span class="rname">' + esc(l.biz || l.name || l.email || '(no name)')
        + '</span>' + stars(l.rating) + leadChip(l.status)
        + (leadIsStale(l) ? '<span class="stale">STALE</span>' : '')
        + (l.excl ? '<span class="pill">' + esc(l.excl) + '</span>' : '')
        + '<span class="dmeta">' + esc(l.src) + (l.date ? ' · ' + esc(l.date) : '')
        + '</span></button>';
      var body = '';
      if(open){
        body += '<div class="facts">'
          + (l.name ? '<span>contact <b>' + esc(l.name) + '</b></span>' : '')
          + (l.email ? '<span><a href="mailto:' + esc(l.email) + '">'
             + esc(l.email) + '</a></span>' : '')
          + (l.phone ? '<span>' + esc(l.phone) + '</span>' : '')
          + (l.type ? '<span>' + esc(l.type) + '</span>' : '') + '</div>';
        if(l.analysis) body += '<div class="dh">AI ANALYSIS</div>'
          + '<div class="draft">' + esc(l.analysis) + '</div>';
        if(l.notes) body += '<div class="dh">NOTES</div><p class="said">'
          + esc(l.notes) + '</p>';
        if(l.details) body += '<p class="said">' + esc(l.details) + '</p>';
        body += '<div class="dh">FRESHO ACCOUNT</div>';
        if(md){
          body += '<div class="facts"><span>linked to <button class="bk" '
            + 'data-goacct="' + esc(md.c) + '">' + esc(md.n) + '</button>'
            + (l.method && !STATE.leadMatch.hasOwnProperty(l.sid)
               ? ' <span class="muted">(' + esc(l.method) + ')</span>' : '')
            + '</span><span>orders since <b>' + ordersTotal(md.c)
            + '</b></span><span>lines <b>' + md.l + '</b></span>'
            + (md.first ? '<span>first order <b>' + esc(md.first) + '</b></span>' : '')
            + '</div><div class="acts"><button class="act mini" data-leadclear="'
            + esc(l.sid) + '">Clear link</button>'
            + '<span class="said" id="say-l-' + esc(l.sid) + '"></span></div>'
            + '<p class="said">Order value is volume (orders and lines) — '
            + 'there is no price data.</p>';
        } else {
          var sg = sugFor(l);
          if(sg){
            var sd = dirRow(sg[0]);
            if(sd) body += '<div class="facts"><span>looks like existing '
              + 'customer <button class="bk" data-goacct="' + esc(sd.c) + '">'
              + esc(sd.n) + '</button> <span class="muted">('
              + Math.round(sg[1] * 100) + '% ' + esc(sg[2]) + ' · account is '
              + esc(String(sd.s).replace(/_/g, ' ')) + ')</span></span></div>'
              + '<div class="acts"><button class="act" data-leadconfirm="'
              + esc(l.sid) + '" data-code="' + esc(sd.c)
              + '">Confirm — same business</button>'
              + '<button class="act mini" data-leadclear="' + esc(l.sid)
              + '">Not a match</button>'
              + '<span class="said" id="say-l-' + esc(l.sid) + '"></span></div>';
          }
          body += '<div class="matchform">'
            + '<input class="act" list="acctnames" id="lm-' + esc(l.sid)
            + '" placeholder="Type the account name…">'
            + '<button class="act" data-leadmatch="' + esc(l.sid)
            + '">Link account</button>'
            + (sg ? '' : '<span class="said" id="say-l-' + esc(l.sid)
               + '"></span>') + '</div>';
        }
      }
      return '<div class="lrow row' + (open ? ' open' : '')
        + (l.excl ? ' excl' : '') + '">' + head
        + '<div class="detail">' + body + '</div></div>';
    }).join('') || '<p class="said">No leads match this filter.</p>';
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

  function renderLeadRecs(){
    var host = document.getElementById('lrlist');
    if(!host) return;
    var recs = [];
    (DATA.leads || []).forEach(function(l){
      if(l.excl) return;
      var sg = sugFor(l);
      var m = effMatch(l);
      if(sg){
        var sd = dirRow(sg[0]);
        if(sd) recs.push({p: 0, verb: 'Confirm', name: l.biz || l.name,
          why: 'looks like existing customer ' + sd.n + ' ('
            + Math.round(sg[1] * 100) + '% ' + sg[2] + ') — sheet still says '
            + (l.status || 'nothing'), sid: l.sid});
      } else if(l.status === 'Acquired' && !m){
        recs.push({p: 1, verb: 'Link', name: l.biz || l.name,
          why: 'marked acquired but not linked to any Fresho account yet',
          sid: l.sid});
      } else if(l.status === 'New' && (l.rating || 0) >= 4){
        recs.push({p: 2, verb: 'Contact', name: l.biz || l.name,
          why: '★'.repeat(l.rating) + ' new lead from ' + (l.src || 'unknown')
            + (l.date ? ', ' + l.date : ''), sid: l.sid});
      } else if(leadIsStale(l)){
        recs.push({p: 3, verb: 'Chase', name: l.biz || l.name,
          why: (l.status || '') + ' since ' + (l.date || '?')
            + ' with no movement', sid: l.sid});
      }
    });
    recs.sort(function(a, b){ return a.p - b.p; });
    host.innerHTML = recs.slice(0, 25).map(function(r){
      return '<div class="card"><div class="crow">'
        + '<span class="cverb">' + esc(r.verb) + '</span>'
        + '<button class="cname" data-goleadrec="' + esc(r.sid) + '">'
        + esc(r.name || '(no name)') + '</button></div>'
        + '<div class="cwhy">' + esc(r.why) + '</div></div>';
    }).join('') || '<p class="said">Nothing waiting — the pipeline is tidy.</p>';
    if(recs.length > 25){
      host.innerHTML += '<p class="said">+ ' + (recs.length - 25)
        + ' more in the Leads list.</p>';
    }
  }

  function rerenderAll(keepRowOpen){
    renderActions();
    render('list-decline', DATA.declineOrder);
    render('list-winback', DATA.winbackOrder);
    renderDir();
    renderLeads();
    renderContacts();
    renderLeadRecs();
    if(curAcct) renderAccount(curAcct);
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

  document.addEventListener('click', function(ev){
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
    var lt = ev.target.closest && ev.target.closest('[data-leadtoggle]');
    if(lt){
      var lsid = lt.getAttribute('data-leadtoggle');
      openLead = openLead === lsid ? null : lsid;
      renderLeads();
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
      renderLeads(); renderLeadRecs();
      scheduleSave(msid, 'l');
      return;
    }
    var lcf = ev.target.closest && ev.target.closest('[data-leadconfirm]');
    if(lcf){
      STATE.leadMatch[lcf.getAttribute('data-leadconfirm')] =
        lcf.getAttribute('data-code');
      renderLeads(); renderLeadRecs();
      scheduleSave(lcf.getAttribute('data-leadconfirm'), 'l');
      return;
    }
    var lc = ev.target.closest && ev.target.closest('[data-leadclear]');
    if(lc){
      STATE.leadMatch[lc.getAttribute('data-leadclear')] = '';
      renderLeads(); renderLeadRecs();
      scheduleSave(lc.getAttribute('data-leadclear'), 'l');
      return;
    }
    var glr = ev.target.closest && ev.target.closest('[data-goleadrec]');
    if(glr){
      openLead = glr.getAttribute('data-goleadrec');
      showTab('leads');
      renderLeads();
      window.scrollTo(0, 0);
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
  });

  var dq = document.getElementById('dirq');
  if(dq) dq.addEventListener('input', function(){ renderDir(); });
  ['dirstatus', 'dirtier'].forEach(function(id){
    var el = document.getElementById(id);
    if(el) el.addEventListener('change', function(){ renderDir(); });
  });
  var ls = document.getElementById('leadstatus');
  if(ls) ls.addEventListener('change', function(){ renderLeads(); });
  var lx = document.getElementById('leadexcl');
  if(lx) lx.addEventListener('change', function(){ renderLeads(); });
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
