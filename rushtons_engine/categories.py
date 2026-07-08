"""Canonical category mapping.

Every raw `Product Group` value ever observed in the Fresho exports is listed
explicitly — no regex. The brief's regex (^[BSZ]\\d+[a-z]?\\.) fails on
'S. Miscellaneous' / 'S. Miscellaneous Prep', the 'B010. Vegeteables' typo and
the 'SPLITs' trailing s, so an explicit reviewed dict is safer and versioned.

An unmapped group is a hard error at ingest: new groups must be added here
deliberately, never silently bucketed.
"""

# 18 canonical categories (BULK/SPLIT collapsed, typo folded, Misc Prep kept
# separate from Misc for auditability but both non-targetable).
RAW_GROUP_TO_CATEGORY = {
    "B010. Vegetables - BULK": "Vegetables",
    "B010. Vegeteables - BULK": "Vegetables",          # typo variant in Fresho
    "S010. Vegetables - SPLIT": "Vegetables",
    "B020. Potatoes - BULK": "Potatoes",
    "S020. Potatoes - SPLIT": "Potatoes",
    "B030. Salads - BULK": "Salads",
    "S030. Salads - SPLIT": "Salads",
    "B040. Tomatoes - BULK": "Tomatoes",
    "S040. Tomatoes - SPLIT": "Tomatoes",
    "B050. Fruits - BULK": "Fruits",
    "S050. Fruits - SPLIT": "Fruits",
    "B060. Italian - BULK": "Italian",
    "S060. Italian - SPLIT": "Italian",
    "B070. Baby Vegetables - BULK": "Baby Vegetables",
    "S070. Baby Vegetables - SPLIT": "Baby Vegetables",
    "B080. Exotic Fruit & Veg - BULK": "Exotic Fruit & Veg",
    "S080. Exotic Fruit & Veg - SPLIT": "Exotic Fruit & Veg",
    "B090. Mushroom - BULK": "Mushroom",
    "S090. Mushroom - SPLIT": "Mushroom",
    "B100. Herbs - BULK": "Herbs",
    "S100. Herbs - SPLIT": "Herbs",
    "B110. Micros, Leaves & Flowers - BULK": "Micros, Leaves & Flowers",
    "S110. Micros, Leaves & Flowers - SPLIT": "Micros, Leaves & Flowers",
    "S120. Foraged - SPLIT": "Foraged",
    "B130. Dry Stores & Non Food - BULK": "Dry Stores & Non Food",
    "S130. Dry Stores & Non Food - SPLITs": "Dry Stores & Non Food",  # sic
    "S140. Prep Fruit & Juices": "Prep Fruit & Juices",
    "S145. Prep Vegetables": "Prep Vegetables",
    "S150. Frozen Produce": "Frozen Produce",
    "S160. Dairy and Chilled": "Dairy and Chilled",
    "S. Miscellaneous": "Miscellaneous",
    "S. Miscellaneous Prep": "Miscellaneous Prep",
    "Z888. Out of Season": "Out of Season",
}

# Blank product group appears on a handful of rows (3 in June 2026); the
# product master resolves the real category from the product's other rows.
BLANK_GROUP_CATEGORY = "Miscellaneous"

OUT_OF_SEASON_GROUP = "Z888. Out of Season"


class UnknownProductGroupError(ValueError):
    pass


def to_category(raw_group: str) -> str:
    """Map a raw Fresho product group to its canonical category. Strict."""
    raw = (raw_group or "").strip()
    if not raw:
        return BLANK_GROUP_CATEGORY
    try:
        return RAW_GROUP_TO_CATEGORY[raw]
    except KeyError:
        raise UnknownProductGroupError(
            f"Unmapped product group {raw!r}. Add it to "
            "categories.RAW_GROUP_TO_CATEGORY deliberately."
        ) from None


def is_informative(raw_group: str) -> bool:
    """True when the group tells us the product's real category
    (not blank, not the Out of Season parking bucket)."""
    raw = (raw_group or "").strip()
    return bool(raw) and raw != OUT_OF_SEASON_GROUP
