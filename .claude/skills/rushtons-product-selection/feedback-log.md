# Product Selection Feedback Log

Dated record of the client's corrections about which products to pitch. Entries here override
anything in `SKILL.md` that predates them — always read this before picking, don't rely on
`SKILL.md` alone once it's out of sync. (Tone/voice feedback lives in the separate `rushtons-comms`
skill's own feedback log.)

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
- **Exotic fruit for a bar.** *"I'd swap lemongrass for something more commonly used in cocktails,
  mint for example."*
- **Hawksmoor + panko crumbs.** *"seems an odd suggestion unless there's something specific on
  their menu it ties to, they're a British steak chain, so we'd need a bit more context internally
  to make that kind of suggestion relevant."*
- And, importantly: *"Some of the picks are spot on though, so definitely on the right track."*

**What changed as a result:** product selection moved out of `selector.py` and into this step. Code
now hands over a wider *pool* of eligible products (step 2) and the drafter picks the final few with
the customer researched (step 3) — including a mandatory live web search of every venue, precisely
because the lemongrass and panko mistakes came from picking without looking. Treat the four examples
above as the calibration set for what "fits the venue" means.

## Data problems found on the first real run (2026-07-14) — raised with the client

Surfaced by the first step-3 pass and recorded in `data_notes`; awaiting Hannah/Harry:

- **Tokyo Turnips (`2885-EA`) are filed under `Vegetables`, not `Baby Vegetables`** — a genuine
  Fresho mislabelling, exactly the internal-labelling issue Hannah suspected. While mis-filed they
  can never appear in a Baby Vegetables pitch.
- **"Remfresh" appears nowhere in Fresho product names** — it's an internal grower/brand name; the
  beetroot itself exists as `1610`/`1613`/`1614`. Can't be matched on the brand.
- **`S. Thorogood & Sons Ltd` is a salad & veg wholesaler** (New Covent Garden Market, est. 1922) —
  a trade counterparty, not an end kitchen, yet ranks top-10 and was offered baby vegetables and
  exotic produce. Strong candidate for the permanent exclusion list; the selector can't know this
  from Fresho data alone.
- **Mint sits in `Herbs`, lemongrass in `Exotic Fruit & Veg`** — which is why the cocktail bar got
  lemongrass. Any account already buying Herbs can never be pitched mint, since Herbs isn't a gap
  for them.
- The four `FCC …` accounts (Fine Cheese Co) all classify identically as venue_type `Unknown`, and
  several cheese retailers were offered `Dairy and Chilled` as a gap — a poor fit by construction.
