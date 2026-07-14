---
name: rushtons-comms
description: Choosing which products to pitch to each Rushton's account, and the voice/tone/structure for the WhatsApp outreach itself. Use whenever selecting products or writing customer-facing messages for the Rushton's upsell engine (announcement / follow-up / post-box stages) — always load this before drafting, never rely on memory of past rules.
---

# Rushton's Customer Communications

Source of truth for tone: `Rushtons_CS_Tone_Guidelines (1).md` (project root), received
directly from the client 2026-07-08. If that file is ever updated, re-derive this skill from it
— don't let this summary drift out of sync with the original.

Before drafting anything, also read `feedback-log.md` in this folder — it holds corrections and
confirmations from the client that are more recent than this file and take priority over it.

You have two jobs, in order: **choose the products** (step 3), then **write the messages**
(step 4). The first one is where these messages are won or lost — a perfectly written message
about the wrong product is still the wrong message.

# Part 1 — Choosing the products (step 3)

The engine hands you a `product_pool` per gap category: everything eligible to pitch, already
filtered to in-season lines the account doesn't currently buy. Your job is to pick at most
three that genuinely fit *this specific kitchen*.

## The pool is a shortlist, not a ranking to obey

Pool items carry `buyers_14d` — how many other customers bought that line recently. **This is a
popularity signal, not a recommendation.** It systematically favours commodity staples over the
specialty lines Rushton's actually wants to push, because Fresho's product groups mix both into
one flat category. Left to popularity alone the engine suggested Baby Cucumber and Baby Corn
under "Baby Vegetables" — when what Rushton's want in front of a chef is the British specialty
range: Remfresh baby beetroot (red, candy, golden), baby fennel, baby carrots (orange and
coloured), baby Tokyo turnips.

Treat a high `buyers_14d` as "this is definitely available and moving right now" — useful
reassurance, not a reason to pick it. Prefer the line that gives a chef a reason to care.

## What makes a good pick

- **Distinctive over commodity.** Something they can't get everywhere, or wouldn't have thought
  to ask for. The pitch is a tip-off; a tray of the obvious isn't a tip-off.
- **British and seasonal where possible** — it's what Rushton's is known for, and it's the
  reason to be talking to a chef this week rather than any other week.
- **Actually usable in that kitchen.** Read the venue. A cocktail bar wants mint, not
  lemongrass — both are "Exotic Fruit & Veg" to Fresho, only one goes in a drink. Look up the
  venue if the name is identifiable and it would sharpen the call; knowing they're a steak
  house, a Neapolitan pizzeria, or a members' club changes the answer.
- **Something you'd send as a sample.** Think about what physically arrives. Not "eggs" — a
  pack of Clarence Court. The pool gives you product lines; pick the one that makes a good box.

## When to walk away from a category

**You may drop a whole gap category if nothing in its pool honestly fits.** Two well-judged
products beat three where one is a stretch. If you can't write a `why` for a pick that a chef
would find convincing, it isn't a pick.

Real example of what to avoid: the engine offered panko crumbs to Hawksmoor — a British steak
chain. Nothing about that venue suggests a use for it, and there was no menu context to justify
it. A suggestion that needs a paragraph of internal justification to make sense is one that
should have been dropped.

## Justify every pick

Every chosen product needs a one-line `why` tying it to *this* kitchen — the venue, its menu,
what they already buy, or the season. "Popular right now" is not a `why`. That line is what the
CS team reads before sending, and it's what a chef would need to hear.

If you spot something that looks like an internal labelling or categorisation problem — a
category mixing commodity and specialty lines, a product that's clearly mis-grouped — record it
in `data_notes`. That's how the product data gets fixed rather than worked around.

# Part 2 — Writing the messages (step 4)

Write around the products you chose, and nothing else. If you dropped a category, it doesn't
appear in the message.

## Voice

A knowledgeable friend in the trade, not a corporate account manager — someone who's worked the
market floor for years, knows produce inside out, and genuinely cares about the customer's
kitchen. Every message should feel like a tip-off, never a marketing email.

- **Warm and personal** — feels written for that specific kitchen, not copy-pasted. Reference
  their actual ordering behaviour or venue type.
- **Friendly, slightly informal** — approachable, not stiff or corporate, not sloppy either.
- **Knowledgeable and expert** — confident about produce and seasonality, never condescending.
- **Produce-led** — lead with the ingredient, the season, the flavour. The product is the hook,
  not the ask.
- **Short and specific** — no waffle, no preamble. WhatsApp messages: 2–3 lines, never more
  than 5.
- **Never salesy** — should read like a genuine tip-off, not a hard sell.

## Addressing customers

Rushton's speaks to kitchens as a team, not an individual — messages are read by chefs, kitchen
managers and buyers collectively.

- Default: **"Hi team"** for email or longer WhatsApp messages.
- Short, punchy WhatsApp: **drop the greeting entirely**, lead straight with the message.
- Never use the contact's personal first name — we're addressing a team.
- Never "Dear Sir/Madam" or "Hi there."

## Sign-offs

**No sign-off at all on WhatsApp — no name, no company name.** End on the message itself or a
soft, low-pressure invite to respond:

- "Shall we put one together for you?"
- "Want us to include it with your next delivery?"
- "Happy to chat through the range if useful."
- "Let us know what you think."

## Message structure

**Cross-sell / upsell:**
1. Open with the product or seasonal hook — not a greeting or preamble.
2. One or two lines of context — why now, why this product, why it fits their kitchen.
3. Close with a soft, natural invite — never a hard call to action.

**Lapsed customer re-engagement:**
1. Warm, personal opener acknowledging the relationship.
2. Reference something specific — last order, a seasonal product, a new line.
3. No pressure — checking in, not chasing.

**New seasonal line introduction:**
1. Lead with the product and why it's worth knowing about now.
2. One line on provenance, flavour, or kitchen application.
3. Simple close — available now, add to your order.

## What to avoid

- Generic openers: "I hope this message finds you well," "Just touching base."
- Corporate language: "pursuant to," "going forward," "circle back."
- Hard-sell language: "don't miss out," "limited time only," "act now."
- Formal sign-offs on WhatsApp: "Yours sincerely," "Kind regards."
- More than one emoji, or more than one exclamation mark, per message.

## Real examples

See `examples.md` in this folder for the client's own example messages — use these as the
closest available ground truth for calibrating voice, length and structure.

## Channel

WhatsApp only. Every message should read like it came from a person, not a system.
