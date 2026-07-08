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

DATA_DIR = Path(os.environ.get("DATA_DIR", ENGINE_DIR / "data"))
EXPORT_DIR = Path(os.environ.get("EXPORT_DIR", ENGINE_DIR / "output"))

# --- order data --------------------------------------------------------------
# Every row in every export to date is 'Invoiced'. Anything else is new and
# gets excluded with a loud warning until a human decides (open decision 4).
COUNTED_ORDER_STATES = {"Invoiced"}

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

EXCLUDED_VENUE_TYPES = {"Internal/Non-customer", "Manufacturing"}

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

# Meeting PDF upsell focus per segment, re-expressed in real Fresho categories
# ("Bar and Room Service" / "Breakfast lines" do not exist in the data).
# Awaiting Rushton's sign-off; when a segment is missing or the intersection is
# empty, all targetable gaps are used.
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
SUGGESTIONS_PER_GAP = 3
MAX_GAPS_PER_ACCOUNT = 3   # a sample box is themed; don't pitch 8 categories

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
