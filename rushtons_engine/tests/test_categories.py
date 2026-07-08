import pytest

import categories

# Every raw group value observed in the June 2026 exports (33 + blank).
OBSERVED_GROUPS = [
    "", "B010. Vegetables - BULK", "B010. Vegeteables - BULK",
    "B020. Potatoes - BULK", "B030. Salads - BULK", "B040. Tomatoes - BULK",
    "B050. Fruits - BULK", "B060. Italian - BULK", "B070. Baby Vegetables - BULK",
    "B080. Exotic Fruit & Veg - BULK", "B090. Mushroom - BULK",
    "B100. Herbs - BULK", "B110. Micros, Leaves & Flowers - BULK",
    "B130. Dry Stores & Non Food - BULK", "S. Miscellaneous",
    "S. Miscellaneous Prep", "S010. Vegetables - SPLIT", "S020. Potatoes - SPLIT",
    "S030. Salads - SPLIT", "S040. Tomatoes - SPLIT", "S050. Fruits - SPLIT",
    "S060. Italian - SPLIT", "S070. Baby Vegetables - SPLIT",
    "S080. Exotic Fruit & Veg - SPLIT", "S090. Mushroom - SPLIT",
    "S100. Herbs - SPLIT", "S110. Micros, Leaves & Flowers - SPLIT",
    "S120. Foraged - SPLIT", "S130. Dry Stores & Non Food - SPLITs",
    "S140. Prep Fruit & Juices", "S145. Prep Vegetables",
    "S150. Frozen Produce", "S160. Dairy and Chilled", "Z888. Out of Season",
]


def test_every_observed_group_maps():
    for g in OBSERVED_GROUPS:
        assert categories.to_category(g)  # no exception, non-empty


def test_bulk_and_split_collapse_to_same_category():
    assert (categories.to_category("B010. Vegetables - BULK")
            == categories.to_category("S010. Vegetables - SPLIT")
            == "Vegetables")


def test_typo_variant_folds_into_vegetables():
    assert categories.to_category("B010. Vegeteables - BULK") == "Vegetables"


def test_splits_trailing_s_maps():
    assert categories.to_category("S130. Dry Stores & Non Food - SPLITs") \
        == "Dry Stores & Non Food"


def test_unknown_group_raises():
    with pytest.raises(categories.UnknownProductGroupError):
        categories.to_category("B999. Brand New Group - BULK")


def test_blank_group_falls_back():
    assert categories.to_category("") == "Miscellaneous"
    assert categories.to_category(None) == "Miscellaneous"


def test_out_of_season_is_not_informative():
    assert not categories.is_informative("Z888. Out of Season")
    assert not categories.is_informative("")
    assert categories.is_informative("S050. Fruits - SPLIT")
