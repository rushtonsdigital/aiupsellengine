# Rushton's Commercial Dashboard & Upsell Engine — status and roadmap

_Last updated 2026-10-25. Start here in a new session, then read `CLAUDE.md`._

## Where things stand

**Commercial terms.** Hannah approved MVP completion on 14 Oct (prototype 3 days £2,250 +
completion 3 days £2,250, £750/day, invoice 1354). **Not approved yet:** Claude seats and
training (£375 + 4×£20/month), the monthly hosting/weekly-update fee (the proposal says £375
in the text and £300 in the table — fix it before re-asking), and Phase 2 (~£6k). Rushton's
financial year runs Nov–Oct. Target: team using the dashboard by early November.

**Live today**
- **Weekly upsell engine** — runs every Monday via the `rushtons-weekly-update` skill (feedback
  review → Fresho downloads in Chrome → load → select 15 → AI picks + A/B messages → tracker →
  dashboard). Last run: 19 Oct 2026 (orders to 17 Oct, £ to 19 Oct).
- **Commercial dashboard** — Claude Artifact https://claude.ai/artifact/FawjU1zri4r2CBfEViBbTW
  ("anyone with the link"), Rushton's branding, sections: Sales & margin, Accounts (rich account
  pages: £, GP%, credits, order history, top/stopped products, preferences, venue research),
  Contacts, Leads, Lead recommendations, This week, Protect, Recover, Peer gaps, Manage, Help.
  Republished weekly with the page's saved state preserved.
- **Data** — Supabase (free tier, project `bhlgphaokfbhcetzersv`): orders from 2 Jun 2026,
  £ sales/GP per account per month from Oct 2025 (Fresho has nothing earlier).
- **Rules** — tiers by last-3-months £; exclusions and selection-only holds in `config.py`
  (St James' Court, Dukes, event caterers, new accounts 3 months, Clapham Leisure to 30 Nov);
  monthly seasonal list `rushtons_engine/seasonal/seasonal_YYYY-MM.csv`.
- **Security** — the public (anon) key is revoked on every `public` table; re-check whenever
  anything is exposed through the Supabase API.

## Suggested features, in priority order

### P0 — finish the approved MVP (early November)
1. **Logged-in dashboard.** Static page on GitHub Pages (public repo with no data) + Supabase
   Auth email login restricted to an allowlist (Hannah, Harry, Laurence, Ben, Jose); data read
   live from an `app` schema via RLS (`app.dashboard_snapshots`, written by the weekly run);
   page actions write to `app.app_events` (replaces the artifact-state harvest).
   *Blocked on Jose:* Google app password entered in Supabase → Auth → SMTP; Supabase Pro
   (~$25/mo) before the team relies on it. Plan detail: `C:\Users\goodb\.claude\plans\the-next-step-of-memoized-rainbow.md` (Step 3).
2. **In-app upsell tracking.** A "This week's 15" section: pitched products and both message
   options with copy buttons, plus buttons Valid ✓/✗ → Sent (A/B/edited text) → Replied
   (yes/no/none) → Box sent → Outcome. Fold events into `recommendations` (new columns), funnel
   tiles, A-vs-B reply rate, **conversion = bought a pitched product/category within 28 days,
   against the account's 8-week baseline**. Replaces the Excel columns and the manual
   result-recording step. Needs #1.
3. **Contacts on account pages** (behind the login only — personal data). Fresho's customer
   master field `internal_customer_contact_notes` holds "Name / Email / Phone" for ~281
   accounts; load into `customer_contacts`. Note from Hannah (19 Oct): customers aren't
   contacted by email, some have no phone, Ben has asked Fresho to add contacts. Needs a fresh
   customer master export (Jose's yes).

### P1 — high value next
4. **Monthly fresh customer master + data-quality report** — fixes missing account managers /
   venue types on new accounts (e.g. Hon's BBQ); send Harry the gaps.
5. **Margin by product/category** — test Fresho's "Invoiced Product Orders" export (line-level
   £). Would give GP by category, the £ value of each upsell, and pricing reviews (Urban Pubs
   run at ~20–26% GP vs 38% average).
6. **Year-on-year and financial year views** — Oct 2025 vs Oct 2026 from November; FY Nov–Oct
   totals; a sustained-£-decline alert (Hannah: River Cafe's dip is "the typical sign of when
   they're going to go").
7. **Leads connected properly** (Hannah's ask on 19 Oct) — £ of converted leads, an
   "Add prospect" form (needs #1), lead → customer conversion tracking.
   *Done 25 Oct:* the website forms post straight into Supabase (`lead-intake` Edge
   Function) — AI research + rating on arrival and a sales-team email; n8n and the sheet are
   retired; Status is set on the dashboard. New leads still reach the dashboard only at the
   weekly republish until #1 (live data) lands.
8. **Seasonal list from the full produce list** — waiting on Harry/Laurence; then monthly from
   the market report PDF.

### P2 — later / not approved
9. Claude seats + "ask the data" training (`rushtons-analytics` skill is ready).
10. Finer product groups (split "Dairy and Chilled": milk / cream / butter / cheese / eggs).
11. Historic sales from Freshware (on hold — Lucie).
12. Phase 2: reminders and the day-3 chaser workflow, mobile-friendly visit view.
13. Phase 3: sending from WhatsApp/email (no official WhatsApp group API — keep manual sending).
14. A scheduled Monday reminder to run the weekly update.

**Out of scope (agreed 24 Sep):** service-issues report, delivery-instructions/driver app
(and never show delivery instructions on a sales page — they contain gate/alarm codes),
line-by-line order history.

## Open questions for Jose / the client
- "Caterers" exclusion — event caterers only (current), or corporate/contract too?
- Hosting fee and Supabase Pro; Claude seats approval; in-app tracking is more work than the
  3 approved days — absorb or tell Hannah it's pulled forward from Phase 2.
- Make the GitHub repo `rushtonsdigital/aiupsellengine` private (only its owner can).
- Waiting on the team: full produce list, Laurence's original-vs-rewritten messages, Harry's
  exclusion-list check, whether last week's yes-replies (Travers Smith, Raw Press, Hotel 41)
  got their boxes, Hannah's confirmation of the 68-account tier review.
