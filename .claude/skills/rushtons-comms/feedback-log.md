# Comms Feedback Log

Dated record of the client's corrections about comms tone and voice. Entries here override anything
in `SKILL.md` that predates them — always read this before writing, don't rely on `SKILL.md` alone
once it's out of sync. (Feedback about *which products to pitch* lives in the separate
`rushtons-product-selection` skill's own feedback log.)

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

## 2026-10-09 — "Too AI": simpler, season-led, no dish suggestions (Laurence, client review)

Laurence Copeman (Account Manager, who now sends the messages) at the 9 Oct review:
the messages read as AI-written. What he wants instead, in his words: *"We're in
the season of Delica squash - can we make you a sample box of that"* - i.e.
**simple**: say what's in season (or what they're not getting from us) and offer the
box. Specifically:

- **No dish or menu suggestions.** Don't propose a new dish, special or recipe
  ("a jaeger sauce for the schnitzel", "a squash and sage special"). Name the
  produce and the season; the chef decides what to cook.
- **Shorter and plainer.** One short line on the produce, one on the offer.
  Drop flourishes ("made for", "cracking", "beautiful", "proper").
- Keep: "Hi team", no sign-off, soft close.

He'll send original-vs-rewritten pairs over the coming weeks; add the best to
`examples.md` and revise this entry when they arrive. Laurence reviews every
message before sending, so these are a baseline, not final copy.

Also discussed (not yet a rule): if a customer doesn't reply to the sample-box
offer, Rushton's may instead "pop a few items into their next order" with a
separate samples invoice. Pending Harry confirming the invoice/night-team process
- do NOT write that variant until it's confirmed.

## 2026-10-12 — Two options per account (Jose, overriding the "no dish ideas" rule above)

Jose: Laurence asked for no dish ideas, **but the dish-idea message is the one that
actually got chefs replying** (the squash-and-sage special at the 9 Oct review).
So until the team has evidence either way, write BOTH for every account:

- `messages` — **Option A, standard**: produce-led, may carry ONE short, simple
  dish idea ("lovely in a curd for the pastry menu", "buttered girolles as a steak
  side"). Never a full recipe or a menu rewrite.
- `messages_simple` — **Option B, simpler**: Laurence's style — what's in season,
  then the box offer. No dish idea.

Laurence picks per account and records which he sent; the tracker's 'Customer
Replied' column will show which option gets replies. Revisit once there's data.

## 2026-10-14 — Follow-up becomes a definite send, not "add it to your next order" (Hannah)

Supersedes the "pop a few items into their next order" idea above. Laurence: that
version is "quite a manual process" because "we don't know when the next order
would be". Hannah's fix: *"instead of asking them to add it themselves, we could say
we're sending it tomorrow and we'd love for them to sample x, y, z. That way it
becomes a definite send rather than an open follow-up."*

So the **follow-up** (stage 2, for chefs who haven't replied to the announcement),
in BOTH options, now:

- says plainly that Rushton's is sending a few samples over **tomorrow**;
- names the two or three picked products (no new ones — same picks as the
  announcement);
- invites them to try, without asking them to order or reply. A soft "let us know
  what you think" is fine.

E.g. *"Hi team, we're sending a few bits over tomorrow for you to try - some
Delica squash and Pink Fir potatoes, both at their best right now. Let us know
what you think."*

Still "Hi team", no sign-off, one short line on the produce. Hannah put this to
Laurence as a question ("What do you think?"); write it this way from the 19 Oct
pull and revise when he replies.

## 2026-10-20 — Results: Option B wins again; name the person; drop "a few to try" (Laurence)

Laurence sent 10 of the 19 Oct pull, **all as Option B (simple)**: *"Again this week
I'm going to stick to the simple whatsapp message. Last week it got us the best
results in terms of people replying saying they wanted the sample box."* Three yeses
(Fresh Hot Pizza, Royal Lancaster, Hons BBQ); Hannah counted 4 of 10 boxes going out.

- **Keep writing both options**, but Option B is now the proven default - make it
  the strongest version. Option A stays as the alternative.
- **One-person chats:** *"if in the group chat there is only 1 person from the
  business, I am addressing them by name instead of 'team' or 'guys'."* We don't
  have contact names, so keep "Hi team" in drafts; Laurence swaps in the name.
- **Wording:** *"'can we send you a few to try' - the language doesn't quite work for
  me here."* Use "Can we send you a sample box to try?" instead.
- His rewrite for Equal Parts (sent): *"Hi team, we've got leafy lemons in, plus fresh
  lemon juice & Miyagawas. Can we send you a sample box to try?"* - short, product
  list, plain offer. That's the Option B target shape.
