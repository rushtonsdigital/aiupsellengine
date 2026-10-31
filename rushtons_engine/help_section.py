"""The dashboard's Help section — plain-English definitions for people who
weren't part of building it. Every threshold is read from config (or passed in)
so the page can't drift from the rules the engine actually applies."""

import html

import config

MONTHS = "January February March April May June July August September October November December".split()


def _m(ym: str) -> str:
    return f"{MONTHS[int(ym[5:]) - 1]} {ym[:4]}"


def _d(ymd: str) -> str:
    """2026-06-02 -> 2 June 2026"""
    return f"{int(ymd[8:10])} {MONTHS[int(ymd[5:7]) - 1]} {ymd[:4]}" if len(ymd) >= 10 else ymd


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def help_html(value_months: list, orders_from: str) -> str:
    """value_months: the dashboard's 12-month £ window ([{m, end, mtd}, ...]);
    orders_from: first order date held, YYYY-MM-DD."""
    e = html.escape
    full = [m for m in value_months if not m.get("mtd")]
    band_months = full[-config.VALUE_BAND_MONTHS:]
    band_span = (f"{_m(band_months[0]['m'])} to {_m(band_months[-1]['m'])}"
                 if band_months else "the latest three complete months")
    gold, silver = config.GOLD_PERCENTILE, config.SILVER_PERCENTILE - config.GOLD_PERCENTILE
    excluded_groups = ", ".join(sorted(g.title() for g in config.EXCLUDED_GROUPS))

    def item(title, body):
        return (f'<details class="help"><summary>{title}</summary>'
                f'<div class="helpbody">{body}</div></details>')

    parts = [
        item("What this dashboard is, and how fresh the numbers are", f"""
<p>A weekly view of Rushton's existing customers: who is worth more attention,
who is slipping away, and what they could be buying from us. It is rebuilt every
Monday from Fresho.</p>
<ul>
<li><b>Orders</b> (counts, order lines, products) come from Fresho's weekly
order export and go back to {e(_d(orders_from))}.</li>
<li><b>&#163; sales and gross profit</b> come from Fresho's &#8220;Sales Totals and
Gross Profit Margin&#8221; report, loaded month by month. Fresho holds this from
October 2025 (when Rushton's moved onto it); earlier history sits in the old system.</li>
<li>Anything you do on the page &#8212; assigning an account, adding a note,
marking a message sent, marking an action done &#8212; is saved for everyone
who opens the link.</li>
</ul>"""),
        item("Gold, silver and bronze accounts", f"""
<p>Every account is ranked on its <b>net &#163; sales over the last
{config.VALUE_BAND_MONTHS} complete months</b> (currently {e(band_span)}):</p>
<ul>
<li><b>Gold</b> &#8212; the top {_pct(gold)} of accounts.</li>
<li><b>Silver</b> &#8212; the next {_pct(silver)}.</li>
<li><b>Bronze</b> &#8212; everyone else.</li>
</ul>
<p>The ranking is recalculated every week, so an account moves up or down as its
spending changes. Accounts we don't upsell to (see &#8220;Who is left
out&#8221;) still get a tier, but they don't take places in the ranking.
Where the team has confirmed a different tier by hand, the hand rating wins.</p>
<p><b>VIP</b> marks prestige accounts &#8212; agreed by hand with the sales team
(hotel groups, flagship venues) &#8212; plus any gold account not otherwise
rated. Treat a VIP with extra care whatever its tier.</p>"""),
        item("Account status: active, lapsed, long lapsed", f"""
<ul>
<li><b>Active regular</b> &#8212; orders at least weekly ({config.REGULAR_MIN_ORDERS}+
orders, usually a week or less apart) and has ordered in the last
{config.LAPSED_DAYS} days.</li>
<li><b>Active ad hoc</b> &#8212; orders less regularly, and has ordered in the last
{config.ADHOC_LAPSED_DAYS} days.</li>
<li><b>Lapsed</b> &#8212; a regular account quiet for {config.LAPSED_DAYS + 1}&#8211;{config.LONG_LAPSED_DAYS}
days, or an ad-hoc one quiet for {config.ADHOC_LAPSED_DAYS + 1}&#8211;{config.ADHOC_LONG_LAPSED_DAYS} days.</li>
<li><b>Long lapsed</b> &#8212; quiet for longer than that.</li>
<li><b>Temporarily closed</b> &#8212; switched off in Fresho and not ordering.</li>
</ul>"""),
        item("The sections: This week, Protect, Recover, Peer gaps, Manage", """
<ul>
<li><b>This week</b> &#8212; every suggested action in one list, one card per
account. Gold before silver, biggest losses first; each card says why it is there.
Mark it Done (or Dismiss it) when handled.</li>
<li><b>Protect (silent decline)</b> &#8212; gold and silver accounts that are still
ordering, but whose orders over the last 4 weeks fell by 35% or more against
the 4 weeks before (and placed at least 6 orders before). They look fine on
every other report &#8212; a quick call to ask what changed is the action.</li>
<li><b>Recover (win-back)</b> &#8212; gold and silver accounts that have lapsed.
Further right on the chart = quiet for longer; higher = bigger account.
Each comes with a ready-to-send &#8220;we've missed you&#8221; message.</li>
<li><b>Peer gaps</b> &#8212; a category an account doesn't buy from us although most
similar venues do (the named list shows gold accounts missing a category that
85%+ of venues of the same type buy). Ask &#8220;who supplies this today?&#8221;</li>
<li><b>Manage</b> &#8212; each account manager's book and how much of it is lapsing.
For balancing workload and coaching, not a league table.</li>
<li><b>Sales &amp; margin</b> &#8212; &#163; sales and gross profit by month, the
biggest &#163; falls, top accounts, lowest margins and totals by account manager.</li>
</ul>"""),
        item("Money terms: net sales, gross profit, GP%, credits", """
<ul>
<li><b>Net sales</b> &#8212; the value of orders after credits (refunds and returns).</li>
<li><b>Gross profit (GP)</b> &#8212; net sales minus the cost of the goods.
<b>GP%</b> is gross profit as a share of net sales.</li>
<li><b>Credits</b> &#8212; refunds and returns. Shown as a % of sales on each
account; a high figure is a rough sign of quality or service problems.</li>
<li><b>Average order value</b> &#8212; last full month's net sales divided by the
number of orders that month.</li>
<li><b>vs 3-month average</b> &#8212; last full month compared with the average of
the three months before it.</li>
<li><b>Sales per account</b> covers the last 12 months up to the latest data;
the current month is shown separately as &#8220;to date&#8221;.</li>
<li>&#163; is per account per month only &#8212; Fresho's report doesn't split it by
product or category yet. Order counts and order lines are <i>volume</i>, not &#163;.</li>
</ul>"""),
        item("The weekly upsell pull (the tracker)", f"""
<p>Every Monday the engine picks <b>{config.TOP_N} accounts</b> to offer a sample box:
engaged accounts buying a narrow range, with clear categories they could add.
An account isn't picked again for {config.COOLDOWN_WEEKS} weeks, and brand-new
accounts are left alone for their first {config.NEW_ACCOUNT_GRACE_DAYS // 7} weeks.</p>
<p>For each account the AI researches the venue online, picks products from the
account's eligible list &#8212; preferring what's in season this month (from the
monthly market report) &#8212; and writes two WhatsApp options: <b>A</b> with a
simple dish idea and <b>B</b> simpler. The team reviews every message before
sending. Please fill in the tracker columns (Valid suggestion, Option sent,
Replied, Box sent, Outcome) &#8212; that is how we learn what works.</p>"""),
        item("Who is left out, and why", f"""
<ul>
<li><b>Never upsold:</b> {e(excluded_groups)}; market and trade customers
(Will's accounts); delivery sites served on behalf of Fine Cheese Co and
Smokin' Brothers; internal and staff accounts.</li>
<li><b>No sample boxes for now:</b> St James' Court, Dukes (until the
refurbishment is finished), event caterers, and groups the team is already
handling directly. These still appear in the other sections.</li>
<li><b>Not shown on purpose:</b> delivery instructions (they contain gate and
alarm codes) and customer contact details, which arrive with the logged-in
version of the dashboard.</li>
</ul>"""),
        item("Leads: stages, follow-ups and who did what", f"""
<p>Website enquiries land in <b>Leads</b> within seconds, with an AI write-up
and a 1&#8211;5 rating; the sales team gets the same by email.</p>
<ul>
<li><b>Stages</b>: {e(' &#8594; '.join(lbl for key, lbl in config.LEAD_STAGES if key != 'Discarded'))},
or <b>Lost</b> (with a reason). Move a lead on its page; logging a call, email,
WhatsApp or visit on a New lead moves it to Contacted, and &#8220;Sample sent&#8221;
moves it to Sample / trial.</li>
<li><b>Follow-ups</b>: an open lead is due {config.LEAD_FOLLOW_UP_DAYS['New']} day after
it arrives, {config.LEAD_FOLLOW_UP_DAYS['Contacted']} days after the last contact,
{config.LEAD_FOLLOW_UP_DAYS['Qualified']} days once qualified and
{config.LEAD_FOLLOW_UP_DAYS['Trial']} days after a sample &#8212; unless someone sets a
date by hand. Leads untouched for {config.LEAD_QUIET_DAYS}+ days are listed as
&#8220;gone quiet&#8221;: revive them or mark them Lost.</li>
<li><b>The funnel</b> counts how far each lead got, so a lead that was qualified
and then lost still counts at Qualified.</li>
<li><b>Real conversion</b> is leads linked to an actual Fresho account, not just
marked Won.</li>
<li>Choose your name in <b>&#8220;You are&#8221;</b> once per device: every stage
change, note and call you log carries it. (Logins will replace this.)</li>
</ul>"""),
        item("Something looks wrong?", """
<p>Venue type, account manager and group all come from the customer's tags in
Fresho &#8212; &#8220;Unknown&#8221; means the tag is missing. Fix it in Fresho
and it flows through on the next Monday rebuild. For anything else &#8212; a wrong
tier, an account that should be excluded, a figure that doesn't match &#8212;
tell GoodBytes (Jose) and it will be corrected at the source.</p>"""),
    ]
    return "".join(parts)
