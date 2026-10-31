"""The Help section reads its thresholds from config, so the page can't drift
from the rules the engine applies."""

import config
import help_section

MONTHS = [{"m": f"2026-{i:02d}", "end": f"2026-{i:02d}-28", "mtd": i == 10} for i in range(1, 11)]


def test_help_uses_live_config(monkeypatch):
    monkeypatch.setattr(config, "TOP_N", 17)
    monkeypatch.setattr(config, "COOLDOWN_WEEKS", 9)
    html = help_section.help_html(MONTHS, "2026-06-02")
    assert "<b>17 accounts</b>" in html and "9 weeks" in html
    assert "July 2026 to September 2026" in html      # last 3 complete months
    assert "2 June 2026" in html
    assert html.count("<details") == 9
