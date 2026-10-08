"""
SQLite storage layer.

Two tables:
  url_status  — tracks every URL the pipeline has seen
                (PENDING → DONE | FAILED | BLOCKED)
  leads       — one row per unique lead after deduplication

The url_status table is what enables crash-resume: at startup the pipeline
skips any URL already marked DONE.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Optional

from app.config.settings import settings
from app.core.logger import get_logger
from app.models.lead import Lead

log = get_logger(__name__)

# ── SQL definitions ───────────────────────────────────────────────────────────

_DDL_URL_STATUS = """
CREATE TABLE IF NOT EXISTS url_status (
    url         TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING|DONE|FAILED|BLOCKED
    error       TEXT,
    metadata    TEXT,           -- JSON blob: listing-time data (product_name, product_url …)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

# Migration: add metadata column to existing DBs that predate this change
_MIGRATE_URL_STATUS = """
ALTER TABLE url_status ADD COLUMN metadata TEXT;
"""

_DDL_LEADS = """
CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name    TEXT NOT NULL,
    contact_person  TEXT,
    designation     TEXT,
    phone_number    TEXT,
    email           TEXT,
    address         TEXT,
    city            TEXT,
    state           TEXT,
    gst_no          TEXT,
    product_name    TEXT,
    product_url     TEXT,
    extra_product_urls TEXT,        -- JSON array
    products_count  INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL,
    phone_status    TEXT NOT NULL DEFAULT 'MISSING',
    email_status    TEXT NOT NULL DEFAULT 'MISSING',
    phone_source    TEXT NOT NULL DEFAULT 'SCRAPED',  -- SCRAPED | MANUAL
    email_source    TEXT NOT NULL DEFAULT 'SCRAPED',  -- SCRAPED | MANUAL
    scraped_at      TEXT NOT NULL,
    raw_url         TEXT,
    created_at      TEXT NOT NULL
);
"""

_DDL_PRODUCTS = """
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name    TEXT NOT NULL,
    product_url     TEXT,
    company_name    TEXT NOT NULL,
    profile_url     TEXT,
    source          TEXT NOT NULL,
    scraped_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(product_url, company_name)
);
"""

# Migrations for existing DBs
_MIGRATE_LEADS_PHONE_SOURCE   = "ALTER TABLE leads ADD COLUMN phone_source TEXT NOT NULL DEFAULT 'SCRAPED';"
_MIGRATE_LEADS_EMAIL_SOURCE   = "ALTER TABLE leads ADD COLUMN email_source TEXT NOT NULL DEFAULT 'SCRAPED';"
_MIGRATE_LEADS_PRODUCTS_COUNT = "ALTER TABLE leads ADD COLUMN products_count INTEGER NOT NULL DEFAULT 0;"


# ── Connection helper ─────────────────────────────────────────────────────────

@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    db_path = settings.db_path_abs
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── Initialisation ────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with _conn() as con:
        con.execute(_DDL_URL_STATUS)
        con.execute(_DDL_LEADS)
        con.execute(_DDL_PRODUCTS)
        # Non-destructive migrations
        url_cols  = {row[1] for row in con.execute("PRAGMA table_info(url_status)")}
        lead_cols = {row[1] for row in con.execute("PRAGMA table_info(leads)")}
        if "metadata" not in url_cols:
            con.execute(_MIGRATE_URL_STATUS)
        if "phone_source" not in lead_cols:
            con.execute(_MIGRATE_LEADS_PHONE_SOURCE)
        if "email_source" not in lead_cols:
            con.execute(_MIGRATE_LEADS_EMAIL_SOURCE)
        if "products_count" not in lead_cols:
            con.execute(_MIGRATE_LEADS_PRODUCTS_COUNT)
    log.info("DB initialised", extra={"stage": "db", "status": "OK",
                                       "path": str(settings.db_path_abs)})


# ── url_status table ──────────────────────────────────────────────────────────

def upsert_url(url: str, source: str, status: str = "PENDING",
               error: Optional[str] = None,
               metadata: Optional[dict] = None) -> None:
    now = datetime.utcnow().isoformat()
    meta_json = json.dumps(metadata) if metadata else None
    with _conn() as con:
        con.execute("""
            INSERT INTO url_status (url, source, status, error, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                status     = excluded.status,
                error      = excluded.error,
                metadata   = COALESCE(excluded.metadata, url_status.metadata),
                updated_at = excluded.updated_at
        """, (url, source, status, error, meta_json, now, now))


def get_url_status(url: str) -> Optional[str]:
    with _conn() as con:
        row = con.execute("SELECT status FROM url_status WHERE url = ?", (url,)).fetchone()
    return row["status"] if row else None


def get_url_metadata(url: str) -> Optional[dict]:
    """Return the JSON metadata stored alongside a URL, or None."""
    with _conn() as con:
        row = con.execute("SELECT metadata FROM url_status WHERE url = ?", (url,)).fetchone()
    if row and row["metadata"]:
        try:
            return json.loads(row["metadata"])
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def get_pending_urls(source: str) -> List[str]:
    with _conn() as con:
        rows = con.execute(
            "SELECT url FROM url_status WHERE source = ? AND status = 'PENDING'",
            (source,)
        ).fetchall()
    return [r["url"] for r in rows]


def count_by_status(source: str) -> dict:
    with _conn() as con:
        rows = con.execute("""
            SELECT status, COUNT(*) as n FROM url_status
            WHERE source = ? GROUP BY status
        """, (source,)).fetchall()
    return {r["status"]: r["n"] for r in rows}


# ── leads table ───────────────────────────────────────────────────────────────

def insert_lead(lead: Lead) -> int:
    """Insert a lead and return its rowid. Caller handles dedup before calling."""
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO leads (
                company_name, contact_person, designation,
                phone_number, email, address, city, state, gst_no,
                product_name, product_url, extra_product_urls, products_count,
                source, phone_status, email_status, phone_source, email_source,
                scraped_at, raw_url, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            lead.company_name, lead.contact_person, lead.designation,
            lead.phone_number, lead.email,
            lead.address, lead.city, lead.state, lead.gst_no,
            lead.product_name, lead.product_url,
            json.dumps(lead.extra_product_urls),
            lead.products_count,
            lead.source, lead.phone_status, lead.email_status,
            lead.phone_source, lead.email_source,
            lead.scraped_at.isoformat() if lead.scraped_at else now,
            lead.raw_url, now,
        ))
    return cur.lastrowid


def _rows_to_leads(rows) -> List[Lead]:
    """Convert DB rows to Lead objects, tolerating extra/missing columns."""
    leads = []
    for r in rows:
        d = dict(r)
        d["extra_product_urls"] = json.loads(d.get("extra_product_urls") or "[]")
        try:
            d["scraped_at"] = datetime.fromisoformat(d["scraped_at"])
        except (TypeError, ValueError):
            d["scraped_at"] = datetime.utcnow()
        for col in ("id", "created_at"):
            d.pop(col, None)
        try:
            leads.append(Lead(**d))
        except Exception as exc:
            log.warning("Skipping malformed DB row", extra={
                "stage": "db_read", "status": "WARN", "error": str(exc)
            })
    return leads


def get_all_leads() -> List[Lead]:
    """Return all leads ordered by id."""
    with _conn() as con:
        rows = con.execute("SELECT * FROM leads ORDER BY id").fetchall()
    return _rows_to_leads(rows)


def get_leads_by_source(source: str) -> List[Lead]:
    """Return leads filtered by source label (case-insensitive prefix match)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM leads WHERE source LIKE ? ORDER BY id",
            (f"%{source}%",)
        ).fetchall()
    return _rows_to_leads(rows)


def count_leads() -> int:
    with _conn() as con:
        row = con.execute("SELECT COUNT(*) as n FROM leads").fetchone()
    return row["n"]


# ── products table ────────────────────────────────────────────────────────────

def upsert_product(product_name: str, product_url: Optional[str],
                   company_name: str, profile_url: Optional[str],
                   source: str) -> None:
    """Insert or ignore a product entry (unique on product_url + company_name)."""
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute("""
            INSERT INTO products (product_name, product_url, company_name,
                                  profile_url, source, scraped_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_url, company_name) DO UPDATE SET
                product_name = excluded.product_name,
                scraped_at   = excluded.scraped_at
        """, (product_name, product_url or "", company_name,
              profile_url, source, now, now))


def get_all_products() -> List[dict]:
    """Return all products as plain dicts (for Excel export)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT product_name, product_url, company_name, "
            "profile_url, source, scraped_at FROM products ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def count_products() -> int:
    with _conn() as con:
        row = con.execute("SELECT COUNT(*) as n FROM products").fetchone()
    return row["n"]


def count_products_for_company(company_name: str) -> int:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) as n FROM products WHERE company_name = ?",
            (company_name,)
        ).fetchone()
    return row["n"]
