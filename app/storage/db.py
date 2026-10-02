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
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
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
    source          TEXT NOT NULL,
    phone_status    TEXT NOT NULL DEFAULT 'MISSING',
    email_status    TEXT NOT NULL DEFAULT 'MISSING',
    scraped_at      TEXT NOT NULL,
    raw_url         TEXT,
    created_at      TEXT NOT NULL
);
"""


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
    log.info("DB initialised", extra={"stage": "db", "status": "OK",
                                       "path": str(settings.db_path_abs)})


# ── url_status table ──────────────────────────────────────────────────────────

def upsert_url(url: str, source: str, status: str = "PENDING",
               error: Optional[str] = None) -> None:
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute("""
            INSERT INTO url_status (url, source, status, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                status     = excluded.status,
                error      = excluded.error,
                updated_at = excluded.updated_at
        """, (url, source, status, error, now, now))


def get_url_status(url: str) -> Optional[str]:
    with _conn() as con:
        row = con.execute("SELECT status FROM url_status WHERE url = ?", (url,)).fetchone()
    return row["status"] if row else None


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
                product_name, product_url, extra_product_urls,
                source, phone_status, email_status,
                scraped_at, raw_url, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            lead.company_name, lead.contact_person, lead.designation,
            lead.phone_number, lead.email,
            lead.address, lead.city, lead.state, lead.gst_no,
            lead.product_name, lead.product_url,
            json.dumps(lead.extra_product_urls),
            lead.source, lead.phone_status, lead.email_status,
            lead.scraped_at.isoformat() if lead.scraped_at else now,
            lead.raw_url, now,
        ))
    return cur.lastrowid


def get_all_leads() -> List[Lead]:
    """Return all leads as Lead objects (for export)."""
    with _conn() as con:
        rows = con.execute("SELECT * FROM leads ORDER BY id").fetchall()
    leads = []
    for r in rows:
        d = dict(r)
        d["extra_product_urls"] = json.loads(d.get("extra_product_urls") or "[]")
        # scraped_at stored as ISO string
        try:
            d["scraped_at"] = datetime.fromisoformat(d["scraped_at"])
        except (TypeError, ValueError):
            d["scraped_at"] = datetime.utcnow()
        # Remove DB-only columns
        for col in ("id", "created_at"):
            d.pop(col, None)
        try:
            leads.append(Lead(**d))
        except Exception as exc:
            log.warning("Skipping malformed DB row", extra={
                "stage": "db_read", "status": "WARN", "error": str(exc)
            })
    return leads


def count_leads() -> int:
    with _conn() as con:
        row = con.execute("SELECT COUNT(*) as n FROM leads").fetchone()
    return row["n"]
