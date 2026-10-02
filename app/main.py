"""
Pipeline orchestrator.

Flow:
  1. Init DB.
  2. For each source scraper:
       a. Resume check: if URLs already exist in DB skip re-discovery (Phase 7).
       b. Inactive scrapers (Meesho no-op) skip Phase A entirely.
       c. Phase A — discover profile URLs via search_listings(), register as PENDING.
       d. Phase B — fetch + parse each PENDING URL, normalise, store.
  3. Deduplicate all leads in DB.
  4. Export to Excel.
  5. Print end-of-run summary.
"""
from datetime import datetime
from typing import List

from app.config.settings import settings
from app.core.http_client import fetch_html
from app.core.exceptions import BlockedError, RobotsDisallowedError
from app.core.logger import get_logger
from app.exporters.excel_exporter import export_excel
from app.models.lead import Lead
from app.processors.deduplicator import deduplicate
from app.processors.normalizer import normalise_lead
from app.scrapers.meesho_scraper import MeeshoScraper
from app.scrapers.tradeindia_scraper import TradeIndiaScraper
from app.storage.db import (
    count_by_status, get_all_leads, get_pending_urls,
    init_db, insert_lead, upsert_url, get_url_status,
)

log = get_logger(__name__)

_MAX_CONSEC_BLOCKS = 5   # stop source after this many consecutive blocks/empties


def run() -> None:
    start_time = datetime.utcnow()
    log.info("=== Run started ===", extra={"stage": "main", "status": "START"})

    init_db()

    scrapers  = [TradeIndiaScraper(), MeeshoScraper()]
    products  = settings.products   # ["Rose Water"]

    stats = {
        "listings_found":    0,
        "listings_fetched":  0,
        "leads_parsed":      0,
        "leads_skipped":     0,
        "blocked_pages":     0,
        "failed_pages":      0,
        "duplicates_merged": 0,
        "selector_broken":   0,   # Phase 7: selector health check
        "resumed_from_db":   0,   # Phase 7: DONE URLs carried from a prior run
    }

    for scraper in scrapers:
        source = scraper.source_name
        log.info(f"--- Source: {source} ---",
                 extra={"source": source, "stage": "main", "status": "START"})

        consecutive_blocks = 0

        # ── Phase A: URL discovery ─────────────────────────────────────────
        # Skip if:  (a) scraper is an inactive no-op, OR
        #           (b) this source already has URLs in DB (resume after crash).
        do_discovery = scraper.is_active
        if do_discovery:
            existing = count_by_status(source)
            already_known = sum(existing.values())
            if already_known > 0:
                done_n    = existing.get("DONE", 0)
                pending_n = existing.get("PENDING", 0)
                stats["resumed_from_db"] += done_n
                log.info(
                    f"{source}: resuming — {done_n} DONE, {pending_n} PENDING "
                    "in DB (skipping re-discovery)",
                    extra={"source": source, "stage": "main", "status": "RESUME",
                           "done": done_n, "pending": pending_n},
                )
                do_discovery = False

        if do_discovery:
            outer_break = False
            for keyword in products:
                if outer_break:
                    break
                for page in range(1, settings.max_pages + 1):
                    profile_urls = scraper.search_listings(keyword, page)

                    if not profile_urls:
                        consecutive_blocks += 1
                        stats["selector_broken"] += 1
                        log.warning(
                            f"SELECTOR_BROKEN {source} — page {page} returned 0 URLs",
                            extra={"source": source, "stage": "search",
                                   "status": "SELECTOR_BROKEN", "page": page},
                        )
                        if consecutive_blocks >= _MAX_CONSEC_BLOCKS:
                            log.warning(
                                f"{source}: {_MAX_CONSEC_BLOCKS} consecutive empty "
                                "pages — stopping source",
                                extra={"source": source, "stage": "main",
                                       "status": "SOURCE_STOPPED"},
                            )
                            outer_break = True
                            break
                        continue

                    consecutive_blocks = 0
                    stats["listings_found"] += len(profile_urls)

                    for url in profile_urls:
                        if get_url_status(url) is None:
                            upsert_url(url, source, "PENDING")
        else:
            # Inactive no-op: trigger the one-time block log (Meesho only).
            if not scraper.is_active:
                scraper.search_listings("", 1)

        # ── Phase B: fetch → parse → normalise → store ─────────────────────
        pending = get_pending_urls(source)
        log.info(
            f"{source}: {len(pending)} URLs to fetch",
            extra={"source": source, "stage": "main",
                   "status": "PENDING", "count": len(pending)},
        )

        for url in pending:
            # Fetch
            try:
                html = fetch_html(url)
                stats["listings_fetched"] += 1
            except RobotsDisallowedError:
                upsert_url(url, source, "BLOCKED", "robots.txt disallowed")
                stats["blocked_pages"] += 1
                log.warning("robots.txt blocked",
                            extra={"source": source, "url": url,
                                   "stage": "fetch", "status": "BLOCKED"})
                continue
            except BlockedError as exc:
                upsert_url(url, source, "BLOCKED", f"HTTP {exc.status_code}")
                stats["blocked_pages"] += 1
                consecutive_blocks += 1
                log.warning("Page blocked",
                            extra={"source": source, "url": url,
                                   "stage": "fetch", "status": "BLOCKED",
                                   "http_code": exc.status_code})
                if consecutive_blocks >= _MAX_CONSEC_BLOCKS:
                    log.warning(
                        f"{source}: too many consecutive blocks — stopping source",
                        extra={"source": source, "stage": "main",
                               "status": "SOURCE_STOPPED"},
                    )
                    break
                continue
            except Exception as exc:
                upsert_url(url, source, "FAILED", str(exc))
                stats["failed_pages"] += 1
                log.error("Fetch error",
                          extra={"source": source, "url": url,
                                 "stage": "fetch", "status": "FAILED",
                                 "error": str(exc)})
                continue

            consecutive_blocks = 0

            # Parse
            lead = scraper.parse_listing(html, url)
            if lead is None:
                upsert_url(url, source, "DONE")
                stats["leads_skipped"] += 1
                continue

            # Normalise (runs phone + email validation)
            try:
                lead = normalise_lead(lead)
            except Exception as exc:
                log.warning("Normalisation failed",
                            extra={"source": source, "url": url,
                                   "stage": "normalise", "status": "WARN",
                                   "error": str(exc)})

            # Store
            try:
                insert_lead(lead)
                upsert_url(url, source, "DONE")
                stats["leads_parsed"] += 1
                log.info(f"Stored: {lead.company_name}",
                         extra={"source": source, "url": url,
                                "stage": "store", "status": "OK"})
            except Exception as exc:
                upsert_url(url, source, "FAILED", str(exc))
                log.error("DB insert failed",
                          extra={"source": source, "url": url,
                                 "stage": "store", "status": "FAILED",
                                 "error": str(exc)})

    # ── Deduplicate ───────────────────────────────────────────────────────────
    all_leads = get_all_leads()
    deduped, merged = deduplicate(all_leads)
    stats["duplicates_merged"] = merged

    # ── Export ────────────────────────────────────────────────────────────────
    output_path = export_excel(deduped, run_stats=stats)

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed     = (datetime.utcnow() - start_time).total_seconds()
    phone_valid  = sum(1 for l in deduped if l.phone_status == "VALID")
    phone_hidden = sum(1 for l in deduped if l.phone_status == "HIDDEN")
    email_valid  = sum(1 for l in deduped if l.email_status == "VALID")

    print()
    print("=" * 60)
    print("  END-OF-RUN SUMMARY")
    print("=" * 60)
    print(f"  Listings found       : {stats['listings_found']}")
    print(f"  Listings fetched     : {stats['listings_fetched']}")
    print(f"  Leads parsed         : {stats['leads_parsed']}")
    print(f"  Leads skipped        : {stats['leads_skipped']}")
    print(f"  Blocked pages        : {stats['blocked_pages']}")
    print(f"  Failed pages         : {stats['failed_pages']}")
    print(f"  Selector broken      : {stats['selector_broken']}")
    print(f"  Resumed from DB      : {stats['resumed_from_db']}")
    print(f"  Duplicates merged    : {merged}")
    print(f"  Final lead count     : {len(deduped)}")
    print(f"  Phone: VALID/HIDDEN  : {phone_valid} / {phone_hidden}")
    print(f"  Email: VALID         : {email_valid}")
    print(f"  Output file          : {output_path}")
    print(f"  Elapsed              : {elapsed:.1f}s")
    print("=" * 60)
    print()

    log.info("=== Run complete ===", extra={
        "stage": "main", "status": "DONE",
        "leads": len(deduped), "output": str(output_path),
        "elapsed_s": elapsed, **stats,
    })


if __name__ == "__main__":
    run()
