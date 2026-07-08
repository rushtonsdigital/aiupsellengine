"""Excel tracker: the human review surface for Phase 1.

Summary tab + one tab per recommended account. The DB stays authoritative;
this file is an export of `recommendations` + `comms`, with blank columns the
CS team fills in by hand (contact, approved, sent, outcome).
"""

import logging
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import config

log = logging.getLogger(__name__)

HEADER_FILL = PatternFill("solid", fgColor="1F6F43")   # Rushton's green
HEADER_FONT = Font(color="FFFFFF", bold=True)
LABEL_FONT = Font(bold=True)
WRAP = Alignment(wrap_text=True, vertical="top")

SUMMARY_COLS = [
    "Rank", "Customer Code", "Customer Name", "Venue Type", "Account Manager",
    "Size Band", "Orders (month)", "SKUs", "Categories Bought",
    "Gap Categories to Pitch", "Score", "Contact (phone)", "Approved (Y/N)",
    "Sent Date", "Box Sent (Y/N)", "Outcome", "Notes",
]


def _safe_tab_name(name: str, used: set) -> str:
    tab = re.sub(r"[\[\]:*?/\\]", "", name)[:28] or "Account"
    base, i = tab, 2
    while tab in used:
        tab = f"{base[:25]}_{i}"
        i += 1
    used.add(tab)
    return tab


def _header_row(ws, row, values):
    for col, value in enumerate(values, start=1):
        cell = ws.cell(row=row, column=col, value=value)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT


def write_tracker(recommendations: list[dict], run_date, out_dir: Path | None = None) -> Path:
    out_dir = Path(out_dir or config.EXPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"rushtons_upsell_tracker_{run_date}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.cell(row=1, column=1, value=f"Rushton's Upsell Engine — week of {run_date}").font = Font(bold=True, size=14)
    _header_row(ws, 3, SUMMARY_COLS)
    for i, rec in enumerate(recommendations, start=4):
        ws.cell(row=i, column=1, value=rec["rank"])
        ws.cell(row=i, column=2, value=rec["customer_code"])
        ws.cell(row=i, column=3, value=rec["customer_name"])
        ws.cell(row=i, column=4, value=rec["venue_type"])
        ws.cell(row=i, column=5, value=rec["sales_rep"])
        ws.cell(row=i, column=6, value=rec["size_band"])
        ws.cell(row=i, column=7, value=rec["num_orders"])
        ws.cell(row=i, column=8, value=rec["num_skus"])
        ws.cell(row=i, column=9, value=", ".join(rec["bought_categories"]))
        ws.cell(row=i, column=10, value=", ".join(rec["gap_categories"]))
        ws.cell(row=i, column=11, value=float(rec["score"]))
    for col, width in enumerate([6, 14, 30, 16, 18, 10, 12, 8, 28, 30, 8, 16, 12, 12, 12, 18, 30], start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A4"

    used_tabs = {"Summary"}
    for rec in recommendations:
        tab = wb.create_sheet(_safe_tab_name(rec["customer_name"], used_tabs))
        tab.column_dimensions["A"].width = 22
        tab.column_dimensions["B"].width = 90
        row = 1

        def put(label, value, wrap=False):
            nonlocal row
            tab.cell(row=row, column=1, value=label).font = LABEL_FONT
            cell = tab.cell(row=row, column=2, value=value)
            if wrap:
                cell.alignment = WRAP
            row += 1

        put("Customer", f"{rec['customer_name']} ({rec['customer_code']})")
        put("Venue type", rec["venue_type"])
        put("Account manager", rec["sales_rep"])
        put("Rank / score", f"{rec['rank']} / {rec['score']}")
        put("Why selected", rec["rationale"], wrap=True)
        put("Currently buys", ", ".join(rec["bought_categories"]))
        row += 1
        _header_row(tab, row, ["Gap category", "Suggested products (in season, popular now)"])
        row += 1
        for cat, items in rec["suggested_products"].items():
            names = "\n".join(f"{p['name']} ({p['code']}) — {p['buyers_14d']} recent buyers"
                              for p in items) or "no in-season suggestions"
            tab.cell(row=row, column=1, value=cat).font = LABEL_FONT
            cell = tab.cell(row=row, column=2, value=names)
            cell.alignment = WRAP
            tab.row_dimensions[row].height = 15 * max(len(items), 1)
            row += 1
        row += 1
        _header_row(tab, row, ["Stage", "Draft (review before sending)"])
        row += 1
        stage_labels = {"announcement": "1. WhatsApp opener",
                        "followup": "2. Follow-up (if no reply)",
                        "postbox": "3. Post-box check-in"}
        for stage, label in stage_labels.items():
            body = rec.get("drafts", {}).get(stage, "")
            tab.cell(row=row, column=1, value=label).font = LABEL_FONT
            cell = tab.cell(row=row, column=2, value=body)
            cell.alignment = WRAP
            tab.row_dimensions[row].height = max(30, 14 * (body.count("\n") + 2))
            row += 1
        row += 1
        for label in ("Contact (phone)", "Approved by", "Sent date",
                      "Box sent (Y/N)", "Outcome", "Notes"):
            put(label, "")

    wb.save(path)
    log.info("tracker written: %s (%d account tabs)", path, len(recommendations))
    return path
