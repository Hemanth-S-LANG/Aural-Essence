"""
Lead model — one row in the output Excel sheet.

Fields added beyond the original spec:
  - gst_no      : GST number, visible in TradeIndia __NEXT_DATA__
  - designation : Contact person's role (owner / CEO / marketing manager …)

Phone/email status enums are kept as plain string literals so SQLite
can store them directly without serialisation overhead.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator


# ── Status literals ───────────────────────────────────────────────────────────
PHONE_STATUS  = {"VALID", "INVALID", "HIDDEN", "MISSING"}
EMAIL_STATUS  = {"VALID", "INVALID", "MISSING"}


class Lead(BaseModel):
    # ── Identity ──────────────────────────────────────────────────────────────
    company_name:   str                     # required; records without it are skipped
    contact_person: Optional[str] = None   # owner_name from TradeIndia
    designation:    Optional[str] = None   # desg (Proprietor / CEO / …)

    # ── Contact ───────────────────────────────────────────────────────────────
    phone_number:   Optional[str] = None   # None when hidden or absent
    email:          Optional[str] = None

    # ── Location ──────────────────────────────────────────────────────────────
    address:        Optional[str] = None   # full street address
    city:           Optional[str] = None
    state:          Optional[str] = None

    # ── Business ──────────────────────────────────────────────────────────────
    gst_no:         Optional[str] = None

    # ── Product / source ──────────────────────────────────────────────────────
    product_name:   Optional[str] = None
    product_url:    Optional[str] = None   # absolute URL to the product/listing page
    source:         str = ""               # "TradeIndia" | "Meesho"

    # ── Validation status ─────────────────────────────────────────────────────
    phone_status:   str = "MISSING"        # VALID | INVALID | HIDDEN | MISSING
    email_status:   str = "MISSING"        # VALID | INVALID | MISSING

    # ── Data provenance ───────────────────────────────────────────────────────
    phone_source:   str = "SCRAPED"        # SCRAPED | MANUAL
    email_source:   str = "SCRAPED"        # SCRAPED | MANUAL

    # ── Housekeeping ──────────────────────────────────────────────────────────
    scraped_at:     datetime = None        # set by scraper on creation
    raw_url:        Optional[str] = None   # the page URL this lead was parsed from

    # Extra product URLs collected when deduplication merges records
    extra_product_urls: List[str] = []

    def __init__(self, **data):
        if data.get("scraped_at") is None:
            data["scraped_at"] = datetime.utcnow()
        super().__init__(**data)

    @field_validator("company_name")
    @classmethod
    def _require_company_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("company_name must not be empty")
        return v

    @field_validator("phone_status")
    @classmethod
    def _valid_phone_status(cls, v: str) -> str:
        if v not in PHONE_STATUS:
            raise ValueError(f"phone_status must be one of {PHONE_STATUS}")
        return v

    @field_validator("email_status")
    @classmethod
    def _valid_email_status(cls, v: str) -> str:
        if v not in EMAIL_STATUS:
            raise ValueError(f"email_status must be one of {EMAIL_STATUS}")
        return v

    @field_validator("source")
    @classmethod
    def _valid_source(cls, v: str) -> str:
        allowed = {"TradeIndia", "Meesho", ""}
        if v not in allowed:
            raise ValueError(f"source must be one of {allowed}")
        return v
