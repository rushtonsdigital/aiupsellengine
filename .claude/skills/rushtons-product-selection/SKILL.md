---
name: rushtons-product-selection
description: Choosing which products to pitch to each Rushton's account in the weekly upsell run (step 3). Enforces a live web search of every venue before picking, so the selection fits the actual kitchen. Load this before choosing products; the separate rushtons-comms skill covers writing the messages afterwards.
---

# Rushton's Product Selection (step 3)

This is the first of your two jobs in the weekly run: **pick the products** for each account. The
second job — writing the WhatsApp messages around them — is a separate skill (`rushtons-comms`),
loaded after this one. Get this one right first: a perfectly written message about the wrong
product is still the wrong message.

Before anything else, read `feedback-log.md` in this folder — it holds the client's own
corrections about product choices and overrides this file where they conflict.

## The input: a pool, not a recommendation

The engine hands you a `product_pool` per gap category for each account: everything eligible to
pitch, already filtered to in-season lines the account doesn't currently buy. Your job is to pick
**at most three per account** that genuinely fit *that specific kitchen* — and to drop anything
that doesn't.

Pool items carry two trade signals, both of which you weigh rather than obey:

- `buyers_14d` — how many other customers bought that line in the last two weeks. **This is a
  popularity signal, NOT a recommendation.** It systematically favours commodity staples over the
  specialty lines Rushton's actually wants to push, because Fresho's product groups mix both into
  one flat category. Left to popularity alone the engine pitched Baby Cucumber and Baby Corn under
  "Baby Vegetables" — when what Rushton's want in front of a chef is the British specialty range:
  baby beetroot (red, candy, golden), baby fennel, heritage carrots, baby turnips. Treat a high
  number as "this is definitely available and moving right now" — reassurance, not a reason to
  pick. A `buyers_14d` of 0 is normal for a specialty line and often the very reason it's worth a
  tip-off.
- `last_sold` — when the line last moved at all. Old date **and** zero recent buyers is your cue
  to sanity-check it's genuinely still stocked before pitching it.

## Step one, always: search the venue

**You must run a live web search for every account before picking its products.** This is not
optional and not "only if the name looks findable" — the whole reason product selection moved to
this step is that the pick has to fit the real venue, and you cannot know the venue from a Fresho
customer code. The Hawksmoor-panko and cocktail-bar-lemongrass mistakes both come from picking
without looking.

For each account, search the customer name (add "London" or the area if you have it) and establish:

- **What kind of place it actually is** — steak house, ramen counter, Neapolitan pizzeria,
  cocktail bar, members' club, cheese retailer, wholesaler. The `venue_type` in the brief is often
  thin or "Unknown"; the web is where you find the truth.
- **What they cook or sell** — the menu, the cuisine, the signature dishes, the drinks program.
  This is what tells you a cocktail bar wants passionfruit not lemongrass, or that a British steak
  house has no use for panko.
- **Anything that changes the call** — a relocation or closure, a rebrand, a group vs. a single
  site, a trade counterparty rather than an end kitchen.

Write one line capturing what you found into the `customer_review` field for that account. This
field is **required** — it's the evidence the research happened, and it's what the CS team reads to
sanity-check the pick. If a venue genuinely can't be found (generic name, no web presence), say so
explicitly in `customer_review` ("no clear web presence; picked on venue_type and order history
only") and pick conservatively — don't guess a cuisine you can't confirm.

## What makes a good pick

- **Distinctive over commodity.** Something they can't get everywhere, or wouldn't have thought to
  ask for. The pitch is a tip-off; a tray of the obvious isn't a tip-off.
- **British and seasonal where possible** — it's what Rushton's is known for, and it's the reason
  to be talking to a chef *this* week rather than any other.
- **Actually usable in that kitchen** — judged against what your search told you. A cocktail bar
  wants mint, not lemongrass; both are "Exotic Fruit & Veg" to Fresho, only one goes in a drink.
- **Something you'd send as a sample.** Think about what physically arrives. Not "eggs" — a pack of
  Clarence Court. The pool gives you product lines; pick the one that makes a good box.

## When to walk away from a category

**You may drop a whole gap category if nothing in its pool honestly fits.** Two well-judged
products beat three where one is a stretch. If you can't write a `why` for a pick that a chef would
find convincing, it isn't a pick.

Real example of what to avoid: the engine offered panko crumbs to Hawksmoor — a British steak
chain. Nothing about that venue suggests a use for it, and there was no menu context to justify it.
A suggestion that needs a paragraph of internal justification to make sense is one that should have
been dropped.

## Justify every pick

Every chosen product needs a one-line `why` tying it to *this* kitchen — the venue, its menu, what
they already buy, or the season. "Popular right now" is not a `why`. That line is what the CS team
reads before sending, and it's what a chef would need to hear.

## Flag data problems, don't work around them

If you spot something that looks like an internal labelling or categorisation problem — a category
mixing commodity and specialty lines, a product clearly mis-grouped, a venue mis-typed, a
wholesaler that shouldn't be a target at all — record it in `data_notes` for that account. That's
how the source data gets fixed rather than silently patched over.

## Output

Per account, produce: a `customer_review` (from your search), `chosen_products` (each with `code`,
`name`, `category`, `why` — codes must come from that account's pool, never invented), and optional
`data_notes`. The exact JSON shape is in the drafting brief's `instructions` field. Then hand over
to the `rushtons-comms` skill to write the messages around your picks.
