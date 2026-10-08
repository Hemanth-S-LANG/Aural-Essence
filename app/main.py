"""
Pipeline orchestrator.

Usage:
    python -m app.main                              # scrape all sources
    python -m app.main --source tradeindia          # scrape TradeIndia only
    python -m app.main --source meesho              # Meesho no-op (blocked sheet only)
    python -m app.main --source all                 # scrape all + combined Excel
    python -m app.main --import-csv data.csv --source meesho  # manual CSV import

Flow (scrape mode):
  1. Init DB.
  2. For each selected scraper:
       a. Resume check: skip re-discovery if URLs already in DB.
       b. Phase A: discover profile URLs, cache product metadata.
       c. Phase B: fetch → parse → normalise → store.
  3. Deduplicate all leads in DB.
  4. Export per-source Excel files (+ combined when --source all).
  5. Print end-of-run summary.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.config.settings import settings
from app.core.exceptions import BlockedError, RobotsDisallowedError
from app.core.http_client import fetch_html
from app.core.logger import get_logger
from app.exporters.excel_exporter import export_excel, export_meesho_blocked
from app.models.lead import Lead
from app.processors.deduplicator import deduplicate
from app.processors.normalizer import normalise_lead
from app.scrapers.meesho_scraper import MeeshoScraper
from app.scrapers.tradeindia_scraper import TradeIndiaScraper
from app.scrapers.ti_pagination_collector import collect_listing_pages
from app.storage.db import (
    count_by_status, get_all_leads, get_all_products, get_leads_by_source,
    get_pending_urls, init_db, insert_lead, upsert_url, upsert_product,
    get_url_status, get_url_metadata, count_products,
)

log = get_logger(__name__)

_MAX_CONSEC_BLOCKS = 5


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m app.main",
        description="Rose Water seller scraper",
    )
    parser.add_argument(
        "--source",
        choices=["tradeindia", "meesho", "all"],
        default="all",
        help="Which source(s) to run (default: all)",
    )
    parser.add_argument(
        "--import-csv",
        metavar="FILE",
        dest="import_csv",
        help="Path to a CSV file of manually collected leads to import",
    )
    return parser.parse_args(argv)


# ── Scraper selection ─────────────────────────────────────────────────────────

def _select_scrapers(source: str):
    """Return the list of scraper instances for the given --source value."""
    all_scrapers = [TradeIndiaScraper(), MeeshoScraper()]
    if source == "all":
        return all_scrapers
    return [s for s in all_scrapers if s.source_name.lower() == source.lower()]


# ── CSV import mode ───────────────────────────────────────────────────────────

def _run_import(csv_path: Path, source: str) -> None:
    from app.importers.csv_importer import import_csv

    log.info(f"=== CSV import: {csv_path} → source={source} ===",
             extra={"stage": "main", "status": "START"})
    init_db()

    leads, imported, skipped = import_csv(csv_path, source)
    if not leads:
        print(f"No valid rows imported from {csv_path}. Skipped: {skipped}")
        return

    # Merge with existing DB leads and deduplicate
    existing = get_all_leads()
    all_leads = existing + leads
    deduped, merged = deduplicate(all_leads)

    # Re-insert all (clear + reload is simplest for a small dataset)
    # For safety we just insert the new ones; dedup handles overlap on export
    for lead in leads:
        try:
            insert_lead(lead)
        except Exception as exc:
            log.warning(f"Insert error: {exc}",
                        extra={"stage": "import", "status": "WARN"})

    # Export updated file for that source
    source_leads = get_leads_by_source(source)
    deduped_src, _ = deduplicate(source_leads)
    out = export_excel(deduped_src, source_label=source)

    print()
    print("=" * 60)
    print("  CSV IMPORT SUMMARY")
    print("=" * 60)
    print(f"  File             : {csv_path}")
    print(f"  Source           : {source}")
    print(f"  Rows imported    : {imported}")
    print(f"  Rows skipped     : {skipped}")
    print(f"  Duplicates merged: {merged}")
    print(f"  Final lead count : {len(deduped_src)}")
    print(f"  Output file      : {out}")
    print("=" * 60)


# ── Scrape pipeline ───────────────────────────────────────────────────────────

def _run_scrape(source: str) -> None:
    start_time = datetime.utcnow()
    log.info(f"=== Run started (--source {source}) ===",
             extra={"stage": "main", "status": "START"})

    init_db()

    scrapers = _select_scrapers(source)
    products = settings.products

    stats = {
        "listings_found":    0,
        "listings_fetched":  0,
        "leads_parsed":      0,
        "leads_skipped":     0,
        "blocked_pages":     0,
        "failed_pages":      0,
        "duplicates_merged": 0,
        "selector_broken":   0,
        "resumed_from_db":   0,
        "is_resumed_run":    False,
    }
    block_count = 0   # cumulative blocks across all sources (for TradeIndia collector)

    for scraper in scrapers:
        src = scraper.source_name
        log.info(f"--- Source: {src} ---",
                 extra={"source": src, "stage": "main", "status": "START"})

        consecutive_blocks = 0

        # ── Phase A: URL discovery ────────────────────────────────────────
        do_discovery = scraper.is_active
        if do_discovery:
            existing_counts = count_by_status(src)
            already_known   = sum(existing_counts.values())
            if already_known > 0:
                done_n    = existing_counts.get("DONE", 0)
                pending_n = existing_counts.get("PENDING", 0)
                stats["resumed_from_db"] += done_n
                stats["is_resumed_run"]   = True
                log.info(
                    f"{src}: resuming — {done_n} DONE, {pending_n} PENDING "
                    "in DB (skipping re-discovery)",
                    extra={"source": src, "stage": "main", "status": "RESUME",
                           "done": done_n, "pending": pending_n},
                )
                do_discovery = False

        if do_discovery:
            if src == "TradeIndia":
                # Use Playwright pagination collector (Option A)
                # One browser session, pages 1..N via browser fetch with session cookies
                for keyword in products:
                    entries, total_pages, blk = collect_listing_pages(
                        keyword, max_pages=settings.max_pages
                    )
                    stats["listings_found"] += len(entries)
                    block_count             += blk
                    if total_pages:
                        log.info(
                            f"TradeIndia: {len(entries)} unique sellers across "
                            f"{min(settings.max_pages, total_pages)}/{total_pages} pages",
                            extra={"source": src, "stage": "main",
                                   "total_pages": total_pages},
                        )
                    # collect_listing_pages already upserted URLs;
                    # count how many are new vs already known
                    new_pending = count_by_status(src).get("PENDING", 0)
                    log.info(
                        f"TradeIndia: {new_pending} profile URLs now PENDING",
                        extra={"source": src, "stage": "main"},
                    )
            else:
                # Fallback: requests-based search_listings_meta (Meesho no-op)
                outer_break = False
                for keyword in products:
                    if outer_break:
                        break
                    for page in range(1, settings.max_pages + 1):
                        if hasattr(scraper, "search_listings_meta"):
                            entries      = scraper.search_listings_meta(keyword, page)
                            profile_urls = [e["profile_url"] for e in entries]
                        else:
                            entries      = []
                            profile_urls = scraper.search_listings(keyword, page)

                        if not profile_urls:
                            consecutive_blocks += 1
                            stats["selector_broken"] += 1
                            log.warning(
                                f"SELECTOR_BROKEN {src} — page {page} returned 0 URLs",
                                extra={"source": src, "stage": "search",
                                       "status": "SELECTOR_BROKEN", "page": page},
                            )
                            if consecutive_blocks >= _MAX_CONSEC_BLOCKS:
                                log.warning(
                                    f"{src}: {_MAX_CONSEC_BLOCKS} consecutive empty "
                                    "pages — stopping source",
                                    extra={"source": src, "stage": "main",
                                           "status": "SOURCE_STOPPED"},
                                )
                                outer_break = True
                                break
                            continue

                        consecutive_blocks = 0
                        stats["listings_found"] += len(profile_urls)
                        meta_by_url = {e["profile_url"]: e for e in entries}

                        for url in profile_urls:
                            if get_url_status(url) is None:
                                meta = meta_by_url.get(url)
                                upsert_url(url, src, "PENDING", metadata=meta)
        else:
            # Inactive no-op: log Meesho block once
            if not scraper.is_active:
                scraper.search_listings("", 1)

        # ── Phase B: fetch → parse → normalise → store ────────────────────
        pending = get_pending_urls(src)
        log.info(
            f"{src}: {len(pending)} URLs to fetch",
            extra={"source": src, "stage": "main",
                   "status": "PENDING", "count": len(pending)},
        )

        for i, url in enumerate(pending, start=1):
            # Progress log every 10 URLs
            if i % 10 == 0 or i == 1 or i == len(pending):
                log.info(
                    f"{src}: {i}/{len(pending)} processed "
                    f"(parsed={stats['leads_parsed']}, "
                    f"blocked={stats['blocked_pages']}, "
                    f"failed={stats['failed_pages']})",
                    extra={"source": src, "stage": "progress",
                           "processed": i, "total": len(pending)},
                )
            try:
                html = fetch_html(url)
                stats["listings_fetched"] += 1
            except RobotsDisallowedError:
                upsert_url(url, src, "BLOCKED", "robots.txt disallowed")
                stats["blocked_pages"] += 1
                log.warning("robots.txt blocked",
                            extra={"source": src, "url": url,
                                   "stage": "fetch", "status": "BLOCKED"})
                continue
            except BlockedError as exc:
                upsert_url(url, src, "BLOCKED", f"HTTP {exc.status_code}")
                stats["blocked_pages"] += 1
                consecutive_blocks += 1
                log.warning("Page blocked",
                            extra={"source": src, "url": url, "stage": "fetch",
                                   "status": "BLOCKED", "http_code": exc.status_code})
                if consecutive_blocks >= _MAX_CONSEC_BLOCKS:
                    log.warning(
                        f"{src}: too many consecutive blocks — stopping source",
                        extra={"source": src, "stage": "main",
                               "status": "SOURCE_STOPPED"},
                    )
                    break
                continue
            except Exception as exc:
                upsert_url(url, src, "FAILED", str(exc))
                stats["failed_pages"] += 1
                log.error("Fetch error",
                          extra={"source": src, "url": url, "stage": "fetch",
                                 "status": "FAILED", "error": str(exc)})
                continue

            consecutive_blocks = 0

            # Parse
            lead = scraper.parse_listing(html, url)
            if lead is None:
                upsert_url(url, src, "DONE")
                stats["leads_skipped"] += 1
                continue

            # Merge listing-time metadata (product_name, product_url)
            meta = get_url_metadata(url)
            if meta and not lead.product_name:
                try:
                    lead = lead.model_copy(update={
                        "product_name": meta.get("product_name") or lead.product_name,
                        "product_url":  lead.product_url or meta.get("product_url"),
                    })
                except Exception:
                    pass

            # Normalise
            try:
                lead = normalise_lead(lead)
            except Exception as exc:
                log.warning("Normalisation failed",
                            extra={"source": src, "url": url,
                                   "stage": "normalise", "status": "WARN",
                                   "error": str(exc)})

            # Store
            try:
                insert_lead(lead)
                upsert_url(url, src, "DONE")
                stats["leads_parsed"] += 1
                log.info(f"Stored: {lead.company_name}",
                         extra={"source": src, "url": url,
                                "stage": "store", "status": "OK"})
                # Record product in the products table
                if lead.product_name:
                    upsert_product(
                        product_name = lead.product_name,
                        product_url  = lead.product_url,
                        company_name = lead.company_name,
                        profile_url  = lead.raw_url,
                        source       = src,
                    )
                # Also record any extra product URLs from metadata
                meta = get_url_metadata(url)
                if meta and meta.get("product_name") and lead.product_name != meta.get("product_name"):
                    upsert_product(
                        product_name = meta["product_name"],
                        product_url  = meta.get("product_url"),
                        company_name = lead.company_name,
                        profile_url  = url,
                        source       = src,
                    )
            except Exception as exc:
                upsert_url(url, src, "FAILED", str(exc))
                log.error("DB insert failed",
                          extra={"source": src, "url": url, "stage": "store",
                                 "status": "FAILED", "error": str(exc)})

    # ── Deduplicate ───────────────────────────────────────────────────────
    all_db_leads = get_all_leads()
    deduped_all, merged = deduplicate(all_db_leads)
    stats["duplicates_merged"] = merged

    # ── Fetch products for export ─────────────────────────────────────────
    all_products = get_all_products()

    # Update products_count on each lead (in-memory, not stored back to DB)
    prod_count_by_co = {}
    for p in all_products:
        co = p["company_name"]
        prod_count_by_co[co] = prod_count_by_co.get(co, 0) + 1
    deduped_all_with_counts = []
    for lead in deduped_all:
        n = prod_count_by_co.get(lead.company_name, 0)
        if n and n != lead.products_count:
            lead = lead.model_copy(update={"products_count": n})
        deduped_all_with_counts.append(lead)
    deduped_all = deduped_all_with_counts

    # ── Per-source exports ────────────────────────────────────────────────
    output_paths: List[Path] = []

    if source in ("tradeindia", "all"):
        ti_leads    = [l for l in deduped_all if "TradeIndia" in (l.source or "")]
        ti_products = [p for p in all_products if "TradeIndia" in (p.get("source",""))]
        if ti_leads:
            p = export_excel(ti_leads, run_stats=stats, source_label="tradeindia",
                             products=ti_products)
            output_paths.append(p)

    if source in ("meesho", "all"):
        me_leads    = [l for l in deduped_all if "Meesho" in (l.source or "")]
        me_products = [p for p in all_products if "Meesho" in (p.get("source",""))]
        if me_leads:
            p = export_excel(me_leads, run_stats=stats, source_label="meesho",
                             products=me_products)
            output_paths.append(p)
        else:
            p = export_meesho_blocked()
            output_paths.append(p)

    if source == "all":
        p = export_excel(deduped_all, run_stats=stats, source_label="all",
                         products=all_products)
        output_paths.append(p)

    # Legacy combined file
    export_excel(deduped_all, run_stats=stats,
                 output_path=settings.output_file_abs,
                 products=all_products)

    # ── Summary ───────────────────────────────────────────────────────────
    elapsed      = (datetime.utcnow() - start_time).total_seconds()
    phone_valid  = sum(1 for l in deduped_all if l.phone_status == "VALID")
    phone_hidden = sum(1 for l in deduped_all if l.phone_status == "HIDDEN")
    email_valid  = sum(1 for l in deduped_all if l.email_status == "VALID")

    run_label = ("  END-OF-RUN SUMMARY (RESUMED RUN)"
                 if stats["is_resumed_run"] else "  END-OF-RUN SUMMARY")
    print()
    print("=" * 60)
    print(run_label)
    print("=" * 60)
    print(f"  Source filter        : --source {source}")
    print(f"  Listings found       : {stats['listings_found']}")
    print(f"  Listings fetched     : {stats['listings_fetched']}")
    print(f"  Leads parsed         : {stats['leads_parsed']}")
    print(f"  Leads skipped        : {stats['leads_skipped']}")
    print(f"  Blocked pages        : {stats['blocked_pages']}")
    print(f"  Failed pages         : {stats['failed_pages']}")
    print(f"  Selector broken      : {stats['selector_broken']}")
    print(f"  Resumed from DB      : {stats['resumed_from_db']}")
    print(f"  Duplicates merged    : {merged}")
    print(f"  Final lead count     : {len(deduped_all)}")
    print(f"  Total products       : {len(all_products)}")
    print(f"  Phone: VALID/HIDDEN  : {phone_valid} / {phone_hidden}")
    print(f"  Email: VALID         : {email_valid}")
    for p in output_paths:
        print(f"  Output file          : {p}")
    print(f"  Elapsed              : {elapsed:.1f}s")
    print("=" * 60)
    print()

    log.info("=== Run complete ===", extra={
        "stage": "main", "status": "DONE",
        "leads": len(deduped_all),
        "outputs": [str(p) for p in output_paths],
        "elapsed_s": elapsed, **stats,
    })


# ── Entry point ───────────────────────────────────────────────────────────────

def run(argv=None) -> None:
    args = _parse_args(argv)

    if args.import_csv:
        csv_path = Path(args.import_csv)
        _run_import(csv_path, args.source if args.source != "all" else "Meesho")
    else:
        _run_scrape(args.source)


if __name__ == "__main__":
    run()
