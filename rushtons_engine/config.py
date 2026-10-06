"""Central configuration: every threshold and weight the engine uses.

All selection behaviour must be tunable from here — nothing hard-coded in
select.py/classify.py. Values agreed in the Phase 1 plan (2026-07-07).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ENGINE_DIR = Path(__file__).resolve().parent
load_dotenv(ENGINE_DIR / ".env")

# --- storage -----------------------------------------------------------------
# Supabase: postgresql+psycopg2://postgres:<password>@db.<ref>.supabase.co:5432/postgres
# Falls back to a local SQLite file so the engine runs before Supabase is provisioned.
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{ENGINE_DIR / 'rushtons.db'}")

# The read-only role used by Metabase and the Claude Code chat path (query.py).
# A separate Supabase login (rushtons_readonly) with SELECT-only grants; the
# writer pipeline keeps using DATABASE_URL. Falls back to DATABASE_URL so local
# SQLite dev/test — where DB roles don't exist — still works unchanged.
REPORTING_DATABASE_URL = os.environ.get("REPORTING_DATABASE_URL", DATABASE_URL)

DATA_DIR = Path(os.environ.get("DATA_DIR", ENGINE_DIR / "data"))
EXPORT_DIR = Path(os.environ.get("EXPORT_DIR", ENGINE_DIR / "output"))

# --- order data --------------------------------------------------------------
# Every row counts only if it is invoiced. Anything else (e.g. 'accepted', an
# order not yet invoiced) is excluded with a loud warning until a human decides
# (open decision 4).
#
# Fresho changed the casing of this field mid-2026: June exports say 'Invoiced',
# August exports say 'invoiced'. Match case-insensitively and store the canonical
# spelling below, so old and new data land identically in the DB and every
# downstream `order_state IN (COUNTED_ORDER_STATES)` filter keeps matching both.
COUNTED_ORDER_STATES = {"Invoiced"}
_COUNTED_CANONICAL = {s.casefold(): s for s in COUNTED_ORDER_STATES}


def canonical_order_state(state: str) -> str | None:
    """Canonical spelling if `state` is a counted state (case-insensitive), else None."""
    return _COUNTED_CANONICAL.get((state or "").strip().casefold())

# --- non-produce lines (dropped at ingest) ------------------------------------
# Fresho product codes that are NOT produce and must never enter the system: a
# fee/admin line, not something Rushton's could pitch. 'Delivery Charge' (15) is
# the important one: third-party courier/distributor accounts (Smokin' Brothers /
# 'SMOBRO', FCC, FERM, Provenance, Fine Cheese Co) only ever carry a weekly
# delivery charge and no produce, which made 67 phantom "engaged-but-narrow"
# accounts flood the weekly selection. Dropping the line at ingest means those
# accounts never get an order, never get a stub, and never become candidates.
# Client instruction 2026-09-07: remove from current data and discard from all
# future uploads. (Other Miscellaneous noise — VAT, Query Product, generic
# 'Miscellaneous - X' catch-alls — is left for the team to rule on; see the
# handover questions. Genuine produce mis-filed under Miscellaneous, e.g. Bobby
# Beans, is NOT dropped.)
NON_PRODUCT_CODES = {"15"}

# --- activity status (decision 6) ---------------------------------------------
LAPSED_DAYS = 7            # regular-cadence account with no order in 7 days -> lapsed
LONG_LAPSED_DAYS = 21      # ... in 21 days -> long_lapsed
ADHOC_LAPSED_DAYS = 28     # sporadic accounts get a longer leash
ADHOC_LONG_LAPSED_DAYS = 56
REGULAR_MIN_ORDERS = 5     # >=5 orders with tight cadence = "regular pattern"
REGULAR_MAX_MEDIAN_GAP = 7 # median days between orders

# --- size band (decision 1: volume proxy, replicates prior classification) ----
# Percentile banding on total order lines, matching the existing
# rushtons_customer_classification.csv (81 gold / 204 silver / 123 bronze).
GOLD_PERCENTILE = 0.20     # top 20% by order lines
SILVER_PERCENTILE = 0.70   # next 50%; remainder bronze

# --- candidate selection (decisions 2, 3) --------------------------------------
LOW_ORDER_METRIC = "sku"   # 'sku' now, flip to 'category' later (decision 2)
LOW_ORDER_MAX = 4          # <=4 distinct SKUs/categories over history
MIN_ORDERS_EVER = 2        # one-off buyers are not "engaged-but-narrow"
COOLDOWN_WEEKS = 8         # do not re-recommend within this window
TOP_N = 10

# Fill the weekly TOP_N from the narrowest basket upward instead of hard-cutting
# at LOW_ORDER_MAX. A hard <=4-SKU cutoff matched only ~4 accounts in the whole
# base (real kitchens all buy more than 4 lines), so the list never filled.
# With backfill the ultra-narrow accounts (<=LOW_ORDER_MAX) still lead, then we
# widen: fewest distinct SKUs first, then fewest distinct CATEGORIES (most
# cross-sell headroom), then engagement score. Every eligible account still
# needs >=MIN_ORDERS_EVER orders, an active status, a gap to pitch, and to be
# out of cooldown and off the exclusion list. Set False to restore the old
# hard cutoff at LOW_ORDER_MAX. (Client direction 2026-09-07.)
BACKFILL_TO_TOP_N = True

EXCLUDED_VENUE_TYPES = {"Internal/Non-customer", "Manufacturing"}

# Split accounts: many pubs/hotels run their bar as a separate Fresho account
# ("Prince Regent - Bar" beside "Prince Regent"). On its own a bar account
# buys 4-5 categories, so it looks like the narrowest, gappiest basket in the
# base and wins selection — on 2026-09-28 all ten picks were bar accounts whose
# kitchen we already supply. When a name matches this suffix AND an account
# with the bare name exists, the two are one venue: the bar's purchases count
# towards the main account's gaps/pool, the bar never competes on its own, and
# cooldown on either covers both. A bar with no matching main account is left
# as a normal candidate.
SPLIT_ACCOUNT_SUFFIX = r"\s*-\s*bar\s*$"   # case-insensitive
# Name matching ignores case, a leading "The" and "Street"/"St". Pairs that
# still don't match by name are listed by hand: {bar_code: main_code}.
SPLIT_ACCOUNT_ALIASES = {
    "C HAMBAR": "C HAM",        # Ham Yard Hotel - Bar -> - Main Kitchen
    "C HAMEVBAR": "C HAMEV",    # Ham Yard Hotel - Event Bar -> - Event Kitchen
}

# Trade counterparties to exclude by hand: produce wholesalers / resellers that
# buy from Rushton's but are NOT end kitchens, so a produce upsell never fits.
# The selector can't tell these from Fresho data alone (they buy real produce).
# classify.py forces prestige='Excluded' for these, which the selector drops.
# Client-confirmed 2026-09-07: S. Thorogood & Sons (New Covent Garden / New
# Spitalfields produce wholesaler). Extend as the team names more.
EXCLUDED_CUSTOMER_CODES = {
    "C THOROGOODCG",   # S. Thorogood & Sons Ltd
    "C THOROGOODS",    # S Thorogood & Sons - New Spitalfields Market (F&W)
    # Zepbrook: office-refreshments reseller delivering to its clients' offices
    # (one buyer behind every site). Excluded 2026-10-06 at Jose's direction.
    # New Zepbrook site codes must be added here by hand.
    "R ZEPBROOK",      # Zepbrook Limited
    "R ZEPMPCBURL",    # Zepbrook - Millennium Capital Partners, Old Burlington St
    "R ZEPMPCBUCK",    # Zepbrook - Millennium Capital Partners, Buckingham Gate
    "R ZEPMPCDUKE",    # Zepbrook - Millennium Capital Partners, Duke St
    "R ZEPMPCRYD",     # Zepbrook - Millennium Capital Partners, Ryder St
}

# Fresho account-stage tags that mark trade/wholesale buyers, not end kitchens.
# 'market customer' = a New Covent Garden / New Spitalfields market trade buyer
# (Primeur, Entremettier, La Sovrana, Vincenzo, Fermary, S. Thorogood...). These
# buy real produce so the selector can't spot them by volume — classify.py forces
# prestige='Excluded' for any account carrying one of these stages. Compared
# case-insensitively. Client-confirmed 2026-09-07.
EXCLUDED_ACCOUNT_STAGES = {"market customer"}

# Ranking weights: score components are each normalised to 0..1 (see select.py).
WEIGHT_ENGAGEMENT = 3.0    # orders per week, capped
WEIGHT_HEADROOM = 2.0      # count of gap categories
WEIGHT_SEGMENT = 1.0       # meeting-PDF segment priority
WEIGHT_VOLUME = 0.5        # avg lines per order, capped
ENGAGEMENT_CAP_ORDERS_PER_WEEK = 6.0
VOLUME_CAP_LINES_PER_ORDER = 10.0

# Meeting PDF segmentation priority: Restaurants > Hotels > Pubs/Bars > Catering.
SEGMENT_PRIORITY_BONUS = {
    "Restaurants": 1.0,
    "Hotels": 0.75,
    "Members Club": 0.75,
    "Pubs": 0.5,
    "Bars": 0.5,
    "Event catering": 0.25,
    "Contract catering": 0.25,
}
DEFAULT_SEGMENT_BONUS = 0.25  # Cafe, Retail, Bakery, Unknown, ...

# --- gaps & suggestions --------------------------------------------------------
# Canonical categories worth pitching a sample box around. Excludes
# Miscellaneous / Miscellaneous Prep / Foraged / Out of Season noise buckets.
TARGETABLE_CATEGORIES = [
    "Vegetables", "Potatoes", "Salads", "Tomatoes", "Fruits", "Italian",
    "Baby Vegetables", "Exotic Fruit & Veg", "Mushroom", "Herbs",
    "Micros, Leaves & Flowers", "Dry Stores & Non Food",
    "Prep Fruit & Juices", "Prep Vegetables", "Frozen Produce",
    "Dairy and Chilled",
]

# Per-segment relevance ORDER for the gap categories offered to the drafter.
# This is a sort hint, NOT a gate: every gap category with stock is offered to
# step 3 regardless: these just come first in the brief so the usually-relevant
# ones are near the top. A wrong or missing entry can only mis-order, never
# exclude — the venue-researched step 3 makes the real category call. (Earlier
# this was a hard filter capping the pitch to 3 lookup-table categories, which
# silently hid a trattoria's baby veg / micros / mushroom behind a "Bars" map.)
SEGMENT_FOCUS_CATEGORIES = {
    "Restaurants": ["Dairy and Chilled", "Dry Stores & Non Food", "Fruits",
                    "Micros, Leaves & Flowers"],
    "Hotels": ["Dairy and Chilled", "Fruits", "Frozen Produce",
               "Prep Fruit & Juices"],
    "Members Club": ["Dairy and Chilled", "Fruits", "Frozen Produce",
                     "Prep Fruit & Juices"],
    "Pubs": ["Dairy and Chilled", "Herbs", "Exotic Fruit & Veg",
             "Prep Vegetables"],
    "Bars": ["Dairy and Chilled", "Herbs", "Exotic Fruit & Veg",
             "Prep Vegetables"],
    "Event catering": ["Frozen Produce", "Prep Vegetables", "Potatoes",
                       "Dry Stores & Non Food"],
    "Contract catering": ["Frozen Produce", "Prep Vegetables", "Potatoes",
                          "Dry Stores & Non Food"],
}

GAP_LOOKBACK_DAYS = None   # decision 10: full history (set to e.g. 84 later)
SUGGESTION_WINDOW_DAYS = 14  # "in season now" = ordered in the last 2 weeks

# How many gap CATEGORIES to hand the drafter, in relevance order. Code no
# longer guesses which few to pitch — it offers this many (those with stock),
# and the venue-researched step 3 decides which actually fit. Set high enough
# that the ideal category is effectively never pre-excluded; the narrowest
# accounts have ~12-15 gaps, so 12 covers almost all of them.
MAX_GAP_CATEGORIES_OFFERED = 12

# Step 2 builds a *pool* of eligible products per gap; step 3 (the drafter)
# picks the final few from it, with the customer in view.
#
# The pool comes from the catalogue, not from what's selling — the specialty
# lines Rushton's most wants to pitch (baby candy beetroot, Yukon baby fennel,
# heritage carrots) are low-volume by definition, so a pool sourced from recent
# orders can never surface them. That is exactly how three commodity staples
# (Baby Cucumber, Baby Corn, Rainbow Yukon carrots) became the entire Baby
# Vegetables pitch. Client feedback 2026-07-14.
#
# 30 covers every fresh-produce category where the commodity-vs-specialty split
# actually bites (Baby Vegetables has 22 distinct products, Tomatoes 25,
# Italian 29). The handful of much larger categories — Dry Stores (389),
# Dairy (123), Vegetables (110) — are still truncated by popularity, so a rare
# line deep in one of those tails can be missed. The durable fix for that is a
# curated hero-line list per category from the sales team, not a bigger number.
POOL_PER_GAP = 30          # eligible candidates handed to the drafter per gap
# The drafter shortlists up to this many products per account — a menu of
# options for the human running the campaign to pick the final box from, not a
# fixed box. Favour a spread across categories over near-duplicates (see the
# rushtons-product-selection skill). Client feedback 2026-07-14.
MAX_CHOSEN_PRODUCTS = 5

# Internal / non-customer accounts, detected from the customer name.
INTERNAL_NAME_KEYWORDS = [
    "waste account", "staff account", "cash sales", "samples",
    "staff warehouse", "demo", "price list", "rushton",  # own-name accounts
    "example customer",
]

# --- prestige (VIP) ------------------------------------------------------------
# VIP is partly hand-curated (prestige hotel groups etc.) and cannot be fully
# derived from Fresho data. Seed from the existing classification file; new
# accounts default to VIP when Gold, Standard otherwise. Team maintains overrides.
PRESTIGE_SEED_FILE = ENGINE_DIR.parent / "rushtons_customer_classification.csv"

# --- weekly file patterns -------------------------------------------------------
ORDERS_FILE_GLOB = "*product_totals_by_customer_*.csv"
CUSTOMERS_FILE_GLOB = "*customers_*.csv"
