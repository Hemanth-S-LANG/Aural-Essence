"""
Read sellers from any Excel file, write enriched contact data back.
Handles all 5 Excel files with different column layouts.
"""
import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple


# Column names we look for (case-insensitive)
_COL_MAP = {
    "company_name":   ["Company Name", "company_name"],
    "contact_person": ["Contact Person", "contact_person"],
    "city":           ["City", "city"],
    "state":          ["State", "state"],
    "address":        ["Address", "address"],
    "gst_no":         ["GST No", "gst_no", "GST Number"],
    "profile_url":    ["Profile URL", "profile_url"],
    "product_url":    ["Product URL", "product_url"],
    "phone_number":   ["Phone Number", "phone_number", "Mobile Number"],
    "phone_status":   ["Phone Status", "phone_status"],
    "email":          ["Email", "email"],
    "email_status":   ["Email Status", "email_status"],
    "product_searched": ["Product Searched", "product_searched"],
}

# New columns we'll add if not present
NEW_COLS = [
    "Phone Number",
    "Email",
    "Phone Status",
    "Email Status",
    "Phone Source",
    "Source URL",
    "Fetch Notes",
    "Fetched At",
]


def _find_col(headers: List, names: List[str]) -> Optional[int]:
    """Find column index (1-based) by trying multiple name variants."""
    for name in names:
        for i, h in enumerate(headers, 1):
            if h and str(h).strip().lower() == name.lower():
                return i
    return None


def load_sellers(xlsx_path: Path, missing_only: bool = True) -> Tuple[List[Dict], dict]:
    """
    Load sellers from Excel.
    Returns (sellers_list, col_map) where col_map has column indices.
    Each seller dict has all available fields + row_number for writing back.
    """
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.active
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]

    # Build column index map
    col_idx = {}
    for field, names in _COL_MAP.items():
        col_idx[field] = _find_col(headers, names)

    sellers = []
    for r in range(2, ws.max_row + 1):
        row = {k: (ws.cell(r, v).value if v else None)
               for k, v in col_idx.items()}
        row["_row"] = r
        row["_wb"]  = wb
        row["_ws"]  = ws
        row["_path"] = xlsx_path

        co = str(row.get("company_name") or "").strip()
        if not co:
            continue   # skip empty rows

        if missing_only:
            # Only process rows where phone is hidden/missing
            status = str(row.get("phone_status") or "").upper()
            phone  = str(row.get("phone_number") or "").strip()
            if status == "VALID" and phone:
                continue   # already has a phone

        sellers.append(row)

    print(f"  Loaded {len(sellers)} sellers to process from {xlsx_path.name}")
    return sellers, col_idx


def write_result(seller: Dict, result: Dict, xlsx_path: Path) -> None:
    """
    Write contact result back to the Excel file for one seller.
    result keys: phone, email, phone_source, source_url, notes, status
    Opens the file fresh each time to avoid memory issues at scale.
    """
    pass  # deferred — batch_write does this more efficiently


def batch_write_results(xlsx_path: Path, results: Dict[int, Dict]) -> Path:
    """
    Write all results back to Excel at once.
    results: {row_number: {phone, email, phone_source, source_url, notes, status}}
    Returns path of updated file.
    """
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.active
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]

    # Ensure new columns exist
    existing_headers = {str(h).strip(): i+1 for i, h in enumerate(headers) if h}
    next_col = ws.max_column + 1

    def ensure_col(name: str) -> int:
        nonlocal next_col
        if name in existing_headers:
            return existing_headers[name]
        # Add new column header
        cell = ws.cell(1, next_col, name)
        cell.font  = Font(color="FFFFFF", bold=True)
        cell.fill  = PatternFill("solid", fgColor="1F4E79")
        existing_headers[name] = next_col
        idx = next_col
        next_col += 1
        return idx

    phone_col  = ensure_col("Phone Number")
    email_col  = ensure_col("Email")
    pstatus_col = ensure_col("Phone Status")
    estatus_col = ensure_col("Email Status")
    psource_col = ensure_col("Phone Source")
    surl_col   = ensure_col("Source URL")
    notes_col  = ensure_col("Fetch Notes")
    fat_col    = ensure_col("Fetched At")

    green_fill = PatternFill("solid", fgColor="C6EFCE")
    grey_fill  = PatternFill("solid", fgColor="F2F2F2")
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    for row_num, res in results.items():
        phone  = res.get("phone", "")
        email  = res.get("email", "")
        source = res.get("phone_source", "")
        surl   = res.get("source_url", "")
        notes  = res.get("notes", "")
        status = "VALID" if phone else "HIDDEN"

        ws.cell(row_num, phone_col,  phone)
        ws.cell(row_num, email_col,  email)
        ws.cell(row_num, pstatus_col, status)
        ws.cell(row_num, estatus_col, "VALID" if email else "MISSING")
        ws.cell(row_num, psource_col, source)
        ws.cell(row_num, fat_col,    ts)

        # Source URL as hyperlink
        if surl:
            cell = ws.cell(row_num, surl_col, surl)
            cell.hyperlink = surl
            cell.font = Font(color="0563C1", underline="single", size=10)
        else:
            ws.cell(row_num, surl_col, "")

        ws.cell(row_num, notes_col, notes)

        # Colour the phone cell
        phone_cell = ws.cell(row_num, phone_col)
        phone_cell.fill = green_fill if phone else grey_fill
        if phone:
            phone_cell.number_format = "@"   # store as text (preserve +91)

    # Auto-size new columns
    for col_name, col_idx in existing_headers.items():
        if col_idx >= next_col - (len(NEW_COLS)):
            col_vals = [str(ws.cell(r, col_idx).value or "")
                        for r in range(2, ws.max_row + 1)]
            w = min(max(len(col_name) + 2,
                        max((len(v) for v in col_vals if v), default=0) + 2), 50)
            ws.column_dimensions[get_column_letter(col_idx)].width = w

    # Save with PermissionError fallback
    try:
        wb.save(xlsx_path)
        print(f"  Saved → {xlsx_path}")
        return xlsx_path
    except PermissionError:
        ts2 = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fb  = xlsx_path.with_name(f"{xlsx_path.stem}_enriched_{ts2}.xlsx")
        wb.save(fb)
        print(f"  File locked — saved to {fb}")
        return fb
