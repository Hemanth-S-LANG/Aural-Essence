"""
CSV importer for manually collected leads.

Usage:
    python -m app.main --import-csv meesho_manual.csv --source meesho

Expected CSV columns (any order, extra columns ignored):
    company_name     REQUIRED
    contact_person
    phone_number
    email
    address
    city
    state
    gst_no
    product_name
    product_url
    designation

Rules applied:
  - Rows without company_name are skipped (logged).
  - phone_number and email are validated with the same validators as scraped leads.
  - phone_source = "MANUAL", email_source = "MANUAL" on every imported row.
  - source is set to the --source argument (e.g. "Meesho").
  - Duplicates are merged with existing DB leads by the deduplicator.
  - NEVER attempts to reveal hidden numbers or bypass any protection.
"""
import csv
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from app.core.logger import get_logger
from app.models.lead import Lead
from app.processors.normalizer import normalise_lead
from app.validators.email_validator import validate_email_address
from app.validators.phone_validator import validate_phone

log = get_logger(__name__)

# Columns we care about (all optional except company_name)
_KNOWN_COLS = {
    "company_name", "contact_person", "phone_number", "email",
    "address", "city", "state", "gst_no", "product_name",
    "product_url", "designation",
}


def import_csv(csv_path: Path, source: str) -> Tuple[List[Lead], int, int]:
    """
    Parse a CSV file and return validated Lead objects.

    Returns:
        (leads, imported_count, skipped_count)
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    leads: List[Lead] = []
    skipped = 0
    imported = 0

    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        # Normalise header names: strip whitespace, lowercase
        if reader.fieldnames is None:
            log.warning("CSV has no headers", extra={"stage": "import", "status": "WARN"})
            return [], 0, 0

        for row_num, raw_row in enumerate(reader, start=2):
            # Normalise keys
            row = {k.strip().lower(): (v.strip() if v else "") for k, v in raw_row.items()}

            co = row.get("company_name", "").strip()
            if not co:
                log.warning(f"Row {row_num}: skipped — no company_name",
                            extra={"stage": "import", "status": "SKIP",
                                   "row": row_num})
                skipped += 1
                continue

            # Phone validation
            raw_phone = row.get("phone_number") or None
            norm_phone, phone_status = validate_phone(raw_phone)

            # Email validation
            raw_email = row.get("email") or None
            norm_email, email_status = validate_email_address(raw_email)

            try:
                lead = Lead(
                    company_name   = co,
                    contact_person = row.get("contact_person") or None,
                    designation    = row.get("designation") or None,
                    phone_number   = norm_phone,
                    email          = norm_email,
                    address        = row.get("address") or None,
                    city           = row.get("city") or None,
                    state          = row.get("state") or None,
                    gst_no         = row.get("gst_no") or None,
                    product_name   = row.get("product_name") or None,
                    product_url    = row.get("product_url") or None,
                    source         = source,
                    phone_status   = phone_status,
                    email_status   = email_status,
                    phone_source   = "MANUAL",
                    email_source   = "MANUAL",
                    scraped_at     = datetime.utcnow(),
                    raw_url        = csv_path.name,
                )
                lead = normalise_lead(lead)
                # Re-apply MANUAL source tag after normalise (normalise doesn't touch it)
                lead = lead.model_copy(update={
                    "phone_source": "MANUAL",
                    "email_source": "MANUAL",
                })
                leads.append(lead)
                imported += 1
                log.debug(f"Row {row_num}: imported {co}",
                          extra={"stage": "import", "status": "OK", "row": row_num})
            except Exception as exc:
                log.warning(f"Row {row_num}: validation error — {exc}",
                            extra={"stage": "import", "status": "SKIP",
                                   "row": row_num, "error": str(exc)})
                skipped += 1

    log.info(f"CSV import complete: {imported} imported, {skipped} skipped",
             extra={"stage": "import", "status": "OK",
                    "imported": imported, "skipped": skipped,
                    "source": source, "file": str(csv_path)})
    return leads, imported, skipped
