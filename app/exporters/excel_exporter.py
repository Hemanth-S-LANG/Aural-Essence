"""
Excel exporter.

Sheet 1 "Leads"       — one row per lead, frozen header, autofilter, auto-width columns,
                         clickable Product URL, phone stored as text (+ prefix preserved).
Sheet 2 "Run Summary" — counts by source/status.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.config.settings import settings
from app.core.exceptions import ExportError
from app.core.logger import get_logger
from app.models.lead import Lead

log = get_logger(__name__)

# ── Column definitions ────────────────────────────────────────────────────────
# (header, lead_attribute, is_url, store_as_text)
_COLUMNS = [
    ("Company Name",    "company_name",    False, False),
    ("Contact Person",  "contact_person",  False, False),
    ("Designation",     "designation",     False, False),
    ("Phone Number",    "phone_number",     False, True),   # text to keep +91
    ("Email",           "email",           False, False),
    ("Address",         "address",         False, False),
    ("City",            "city",            False, False),
    ("State",           "state",           False, False),
    ("GST No",          "gst_no",          False, False),
    ("Product Name",    "product_name",    False, False),
    ("Product URL",     "product_url",     True,  False),   # clickable hyperlink
    ("Source",          "source",          False, False),
    ("Phone Status",    "phone_status",    False, False),
    ("Email Status",    "email_status",    False, False),
    ("Scraped At",      "scraped_at",      False, False),
]

# Status → background colour (light tints)
_STATUS_COLOURS = {
    "VALID":   "C6EFCE",   # light green
    "HIDDEN":  "FFEB9C",   # light yellow
    "MISSING": "F2F2F2",   # light grey
    "INVALID": "FFC7CE",   # light red
}

_HEADER_FILL  = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT  = Font(color="FFFFFF", bold=True, size=10)
_LINK_FONT    = Font(color="0563C1", underline="single", size=10)
_DEFAULT_FONT = Font(size=10)


def _col_width(values: List[str], header: str, max_w: int = 60) -> int:
    """Auto-size: max of header length and sample values, capped at max_w."""
    sample = max((len(str(v)) for v in values if v), default=0)
    return min(max(len(header) + 2, sample + 2), max_w)


def _status_fill(status: Optional[str]) -> Optional[PatternFill]:
    colour = _STATUS_COLOURS.get(status or "")
    return PatternFill("solid", fgColor=colour) if colour else None


def export_excel(
    leads: List[Lead],
    run_stats: Optional[Dict] = None,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Write leads to Excel and return the output path.

    Args:
        leads:       normalised, deduplicated Lead list
        run_stats:   optional dict with counts for the Run Summary sheet
        output_path: override the path from settings
    """
    if output_path is None:
        output_path = settings.output_file_abs
        if not settings.overwrite_output:
            stem = output_path.stem
            ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output_path = output_path.with_name(f"{stem}_{ts}.xlsx")

    try:
        wb = openpyxl.Workbook()

        # ── Sheet 1: Leads ────────────────────────────────────────────────────
        ws = wb.active
        ws.title = "Leads"

        # Header row
        for col_idx, (header, _, _, _) in enumerate(_COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font  = _HEADER_FONT
            cell.fill  = _HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center",
                                       wrap_text=False)

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(_COLUMNS))}1"

        # Data rows
        for row_idx, lead in enumerate(leads, start=2):
            for col_idx, (header, attr, is_url, as_text) in enumerate(_COLUMNS, start=1):
                raw_val = getattr(lead, attr, None)

                # Format datetimes
                if isinstance(raw_val, datetime):
                    display = raw_val.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    display = raw_val

                cell = ws.cell(row=row_idx, column=col_idx)
                cell.font = _DEFAULT_FONT

                if is_url and display:
                    # Clickable hyperlink
                    cell.value     = display
                    cell.hyperlink = display
                    cell.font      = _LINK_FONT
                elif as_text and display:
                    # Prefix with apostrophe trick: store as text
                    cell.value        = str(display)
                    cell.number_format = "@"
                else:
                    cell.value = display

                # Colour phone/email status columns
                if attr == "phone_status":
                    fill = _status_fill(str(raw_val) if raw_val else "MISSING")
                    if fill:
                        cell.fill = fill
                elif attr == "email_status":
                    fill = _status_fill(str(raw_val) if raw_val else "MISSING")
                    if fill:
                        cell.fill = fill

        # Auto-size columns
        for col_idx, (header, attr, _, _) in enumerate(_COLUMNS, start=1):
            values = [getattr(l, attr, None) for l in leads]
            width  = _col_width(values, header)
            ws.column_dimensions[get_column_letter(col_idx)].width = width

        # Row height for data rows
        for row_idx in range(2, len(leads) + 2):
            ws.row_dimensions[row_idx].height = 15

        # ── Sheet 2: Run Summary ──────────────────────────────────────────────
        ws2 = wb.create_sheet("Run Summary")
        _write_summary(ws2, leads, run_stats or {})

        wb.save(output_path)
        log.info(f"Excel exported: {output_path} ({len(leads)} rows)",
                 extra={"stage": "export", "status": "OK",
                        "path": str(output_path), "rows": len(leads)})
        return output_path

    except Exception as exc:
        raise ExportError(f"Excel export failed: {exc}") from exc


def _write_summary(ws, leads: List[Lead], run_stats: Dict) -> None:
    """Populate the Run Summary sheet."""
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 15

    def _hdr(row, text):
        cell = ws.cell(row=row, column=1, value=text)
        cell.font = Font(bold=True, size=11)

    def _row(row, label, value):
        ws.cell(row=row, column=1, value=label)
        ws.cell(row=row, column=2, value=value)

    r = 1
    _hdr(r, "Run Summary"); r += 1
    _row(r, "Generated at",
         datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")); r += 1
    r += 1

    _hdr(r, "Leads"); r += 1
    _row(r, "Total leads",     len(leads)); r += 1
    _row(r, "TradeIndia",
         sum(1 for l in leads if "TradeIndia" in (l.source or ""))); r += 1
    _row(r, "Meesho",
         sum(1 for l in leads if "Meesho" in (l.source or ""))); r += 1
    r += 1

    _hdr(r, "Phone status"); r += 1
    for status in ("VALID", "HIDDEN", "MISSING", "INVALID"):
        _row(r, f"  {status}",
             sum(1 for l in leads if l.phone_status == status)); r += 1
    r += 1

    _hdr(r, "Email status"); r += 1
    for status in ("VALID", "MISSING", "INVALID"):
        _row(r, f"  {status}",
             sum(1 for l in leads if l.email_status == status)); r += 1
    r += 1

    # Pipeline stats passed in from main.py
    if run_stats:
        _hdr(r, "Pipeline stats"); r += 1
        for k, v in run_stats.items():
            _row(r, f"  {k}", v); r += 1
