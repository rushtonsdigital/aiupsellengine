"""export.py: mainly the locked-file fallback behaviour, since that's the
part with real logic (the rest is openpyxl cell-writing)."""

import export


class _FakeWorkbook:
    """Stands in for an openpyxl Workbook whose .save() we control. Only the
    original `locked_path` ever raises — a fallback path (a fresh filename)
    always succeeds, same as in reality."""
    def __init__(self, locked_path=None, fails_on_locked: int = 0):
        self.locked_path = locked_path
        self.fails_on_locked = fails_on_locked
        self.calls = 0
        self.saved_to = []

    def save(self, path):
        self.calls += 1
        if path == self.locked_path and self.calls <= self.fails_on_locked:
            raise PermissionError(f"locked: {path}")
        self.saved_to.append(path)


def test_save_succeeds_on_first_try(tmp_path):
    path = tmp_path / "tracker.xlsx"
    wb = _FakeWorkbook(locked_path=path, fails_on_locked=0)
    result = export._save_with_fallback(wb, path, max_retries=3, retry_delay=0)
    assert result == path
    assert wb.calls == 1


def test_save_recovers_after_transient_lock(tmp_path):
    path = tmp_path / "tracker.xlsx"
    wb = _FakeWorkbook(locked_path=path, fails_on_locked=2)  # locked twice, then frees up
    result = export._save_with_fallback(wb, path, max_retries=3, retry_delay=0)
    assert result == path
    assert wb.calls == 3


def test_save_falls_back_when_still_locked(tmp_path):
    path = tmp_path / "rushtons_upsell_tracker_2026-06-30.xlsx"
    wb = _FakeWorkbook(locked_path=path, fails_on_locked=99)  # never frees up
    result = export._save_with_fallback(wb, path, max_retries=3, retry_delay=0)
    assert result != path
    assert result.parent == path.parent
    assert result.stem.startswith("rushtons_upsell_tracker_2026-06-30_UPDATED_")
    assert result.suffix == ".xlsx"
    assert wb.calls == 4  # 3 retries against the real path + 1 fallback save
    assert wb.saved_to == [result]


def test_wrapped_row_height_scales_with_longest_message():
    short = export._wrapped_row_height(["hi"])
    long = export._wrapped_row_height(["x" * 200, "y"])
    assert long > short
    assert export._wrapped_row_height([]) >= 15  # empty list doesn't crash
