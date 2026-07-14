# Feedback Log

Dated record of client corrections and confirmations about product selection and comms
tone/voice. Entries here override anything in `SKILL.md` that predates them — always check this
file for the latest guidance before drafting, don't rely on `SKILL.md` alone once it's out of
sync.

## 2026-07-14 — Product picks were wrong: commodity lines, and no sense of the venue

Hannah reviewed the first set of suggestions and flagged that the products themselves — not the
writing — were off. Her examples, verbatim:

- **Baby veg.** The engine surfaced Baby Cucumber (1670-EA, 26 recent buyers), Carrots - Baby
  Rainbow Yukon (1643-EA, 13), Baby Corn (1660-EA, 9). Her response: *"These aren't the baby veg
  lines we'd actually want to push. We'd be looking at British baby veg, likely Remfresh baby
  beetroot (red, candy, golden), baby fennel, baby carrots (orange and coloured) and baby Tokyo
  turnips. I'm wondering if this has come about because of how we store and label products
  internally, worth checking."* — She was right. Fresho's `B070./S070. Baby Vegetables` groups
  commodity and specialty lines together, and the engine was ranking within that flat bucket by
  recent-buyer count, which always surfaces the staples.
- **Eggs.** *"we wouldn't send a tray, it'd be a pack of something like Clarence Court."*
- **Exotic fruit for a bar.** *"I'd swap lemongrass for something more commonly used in
  cocktails, mint for example."*
- **Hawksmoor + panko crumbs.** *"seems an odd suggestion unless there's something specific on
  their menu it ties to, they're a British steak chain, so we'd need a bit more context
  internally to make that kind of suggestion relevant."*
- And, importantly: *"Some of the picks are spot on though, so definitely on the right track."*

**What changed as a result:** product selection moved out of `selector.py` and into the drafting
step. Code now hands over a wider *pool* of eligible products (step 2) and the drafter picks the
final few with the customer in view (step 3). The judgement guidance in Part 1 of `SKILL.md` is
derived directly from the feedback above — treat those four examples as the calibration set.

## 2026-07-08 — Real tone guidelines received, superseding earlier assumptions

The client provided `Rushtons_CS_Tone_Guidelines (1).md`, the first real, authoritative tone
spec for this project. Before this, comms had been drafted from tone principles in the original
kickoff meeting-notes PDF, which turned out to differ from the real guidelines in several
important ways:

- **No sign-off name on WhatsApp.** The kickoff PDF assumed messages sign off with the
  account manager's first name (e.g. "Rob," "Ben"). The real guidelines explicitly list this
  under *Avoid*. The June 2026 test drafts (in the tracker / Supabase `comms` table) all violate
  this and need to be redrafted before being relied on.
- **Address as "Hi team," never a personal name or the business name as a greeting.**
- **2–3 lines, never more than 5.** The June test drafts run considerably longer.
- **Open with the product/season, not a preamble.** The June drafts open with "been meaning to
  mention something..." — a phrase pulled from the kickoff PDF, not the real guidelines.

**Action needed:** redraft the June 2026 comms (10 accounts × 3 stages) against `SKILL.md` and
`examples.md` before the client meeting relies on them as representative.
