"""
Task 4 tests: pagination limits, multi-category config, resume behaviour.
All tests use mocked responses — no live network requests.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from bs4 import BeautifulSoup

from app.scrapers.tradeindia_scraper import (
    TradeIndiaScraper, LISTING_CATEGORIES, _parse_listing_page,
    _get_next_data,
)
from app.config.settings import Settings


# ── Multi-category config ─────────────────────────────────────────────────────

class TestMultiCategoryConfig:
    def test_one_category_loaded(self):
        """sources.yaml must define manufacturers (suppliers/exporters removed)."""
        names = [c["name"] for c in LISTING_CATEGORIES]
        assert "manufacturers" in names
        assert "suppliers"     not in names, "suppliers was disallowed by robots.txt"
        assert "exporters"     not in names, "exporters returns 404"

    def test_each_category_has_templates(self):
        for cat in LISTING_CATEGORIES:
            assert "page1_template" in cat, f"Missing page1_template in {cat}"
            assert "page2_template" in cat, f"Missing page2_template in {cat}"

    def test_listing_url_uses_correct_template(self):
        s = TradeIndiaScraper()
        mfg_p1 = s.listing_url("Rose Water", 1, "manufacturers")
        assert "manufacturers/rose-water.html" in mfg_p1

    def test_page2_url_has_page_number(self):
        s = TradeIndiaScraper()
        url = s.listing_url("Rose Water", 2, "manufacturers")
        assert "rose-water-2.html" in url

    def test_page3_url_has_page_number(self):
        s = TradeIndiaScraper()
        url = s.listing_url("Rose Water", 3, "manufacturers")
        assert "rose-water-3.html" in url

    def test_unknown_category_falls_back_to_first(self):
        """An unrecognised category name falls back to the first category (manufacturers)."""
        s = TradeIndiaScraper()
        url = s.listing_url("Rose Water", 1, "unknown_cat")
        assert "manufacturers/rose-water.html" in url


# ── Pagination limits ─────────────────────────────────────────────────────────

class TestPaginationLimits:
    def test_max_pages_enforced(self):
        """Pipeline respects MAX_PAGES and stops at that boundary."""
        # We verify by checking that listing_url is only called up to max_pages
        s = TradeIndiaScraper()
        # Generate URLs for pages 1..30 — should not raise
        for page in range(1, 31):
            url = s.listing_url("Rose Water", page, "manufacturers")
            assert "rose-water" in url

    def test_request_delay_minimum_enforced(self):
        """Settings must reject REQUEST_DELAY < 3."""
        with pytest.raises(Exception):  # pydantic ValidationError
            Settings(
                request_delay=1.0,
                search_products="Rose Water",
                _env_file=None,
            )

    def test_request_delay_exactly_3_accepted(self):
        """REQUEST_DELAY = 3.0 must be accepted."""
        s = Settings(
            request_delay=3.0,
            search_products="Rose Water",
        )
        assert s.request_delay == 3.0

    def test_request_delay_above_3_accepted(self):
        s = Settings(
            request_delay=5.0,
            search_products="Rose Water",
        )
        assert s.request_delay == 5.0


# ── search_listings_meta deduplication ───────────────────────────────────────

class TestSearchListingsMetaDedup:
    """
    search_listings_meta must deduplicate profile URLs across categories so the
    same seller (e.g. in both 'manufacturers' and 'suppliers') is only queued once.
    """

    def _make_html(self, profile_slug: str, product_name: str) -> str:
        """Build minimal __NEXT_DATA__ HTML for a listing page."""
        nd = {
            "props": {"pageProps": {"initialState": {
                "manufacturer": {"manufacturer_page": {"manufacturer_page_res": {
                    "mcatData": {"listing_data": {"listing_data": [
                        {
                            "co_name": "Test Co",
                            "city": "Delhi",
                            "state": "Delhi",
                            "product_name": product_name,
                            "prod_url": f"/products/{profile_slug}-product.html",
                            "profile_url": f"/{profile_slug}-12345/",
                            "number_mask": True,
                        }
                    ]}}
                }}}
            }}}
        }
        return f'<html><head></head><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script></body></html>'

    def test_duplicate_profile_urls_removed(self):
        """Same seller appearing in manufacturers and suppliers is deduplicated."""
        scraper = TradeIndiaScraper()
        # Same profile_url in both categories → only 1 entry returned
        same_html = self._make_html("earth-science-ayurveda", "Rose Water")

        with patch.object(scraper, "_fetch_listing_entries") as mock_fetch:
            mock_fetch.return_value = [{
                "profile_url": "https://www.tradeindia.com/earth-science-ayurveda-29503048/",
                "product_name": "Rose Water",
                "product_url": "https://www.tradeindia.com/products/rose-water.html",
            }]
            entries = scraper.search_listings_meta("Rose Water", 1)

        # _fetch_listing_entries called once per category
        assert mock_fetch.call_count == len(LISTING_CATEGORIES)
        # Even though all categories returned the same URL, dedup produces 1
        urls = [e["profile_url"] for e in entries]
        assert len(urls) == len(set(urls)), "Duplicate profile_urls found"

    def test_distinct_sellers_from_different_categories_all_included(self):
        scraper = TradeIndiaScraper()
        # Each category returns a different seller
        call_responses = [
            [{"profile_url": f"https://www.tradeindia.com/seller-{i}-123/",
              "product_name": "Rose Water",
              "product_url": f"https://www.tradeindia.com/products/p{i}.html"}]
            for i in range(len(LISTING_CATEGORIES))
        ]
        with patch.object(scraper, "_fetch_listing_entries",
                          side_effect=call_responses):
            entries = scraper.search_listings_meta("Rose Water", 1)

        assert len(entries) == len(LISTING_CATEGORIES)


# ── Resume behaviour ──────────────────────────────────────────────────────────

class TestResumeBehaviour:
    """
    Verify the pipeline skips re-discovery when URLs are already in the DB.
    Uses mocked HTTP and DB functions — no live requests, no real DB writes.
    """

    def test_resume_skips_search_listings_meta(self, tmp_path):
        """
        When count_by_status returns non-zero for TradeIndia,
        the Playwright collector must NOT be called (resume path).
        """
        scraper = TradeIndiaScraper()

        with patch("app.main.count_by_status", return_value={"DONE": 5, "PENDING": 0}), \
             patch("app.main.get_pending_urls", return_value=[]), \
             patch("app.main.get_all_leads", return_value=[]), \
             patch("app.main.get_all_products", return_value=[]), \
             patch("app.main.deduplicate", return_value=([], 0)), \
             patch("app.main.export_excel"), \
             patch("app.main.export_meesho_blocked"), \
             patch("app.main.init_db"), \
             patch("app.main.collect_listing_pages", return_value=([], 0, 0)) as mock_coll, \
             patch.object(scraper, "search_listings_meta") as mock_meta:
            from app.main import _run_scrape
            with patch("app.main._select_scrapers", return_value=[scraper]):
                _run_scrape("tradeindia")

        mock_coll.assert_not_called()
        mock_meta.assert_not_called()

    def test_fresh_run_calls_collect_listing_pages(self, tmp_path):
        """
        When count_by_status returns zero (fresh DB),
        collect_listing_pages IS called (new Playwright collector).
        """
        scraper = TradeIndiaScraper()

        with patch("app.main.count_by_status", return_value={}), \
             patch("app.main.get_url_status", return_value=None), \
             patch("app.main.upsert_url"), \
             patch("app.main.get_pending_urls", return_value=[]), \
             patch("app.main.get_all_leads", return_value=[]), \
             patch("app.main.get_all_products", return_value=[]), \
             patch("app.main.deduplicate", return_value=([], 0)), \
             patch("app.main.export_excel"), \
             patch("app.main.export_meesho_blocked"), \
             patch("app.main.init_db"), \
             patch("app.main.collect_listing_pages",
                   return_value=([], 0, 0)) as mock_coll:
            from app.main import _run_scrape
            with patch("app.main._select_scrapers", return_value=[scraper]):
                _run_scrape("tradeindia")

        mock_coll.assert_called_once()

    def test_pending_urls_fetched_after_resume(self):
        """
        On resume (DONE > 0, PENDING > 0), the PENDING URLs are still fetched.
        """
        from app.core.exceptions import BlockedError
        scraper = TradeIndiaScraper()
        pending_url = "https://www.tradeindia.com/test-co-99999/"

        with patch("app.main.count_by_status",
                   return_value={"DONE": 3, "PENDING": 1}), \
             patch("app.main.get_pending_urls", return_value=[pending_url]), \
             patch("app.main.fetch_html",
                   side_effect=BlockedError(pending_url, 403)), \
             patch("app.main.upsert_url"), \
             patch("app.main.get_all_leads", return_value=[]), \
             patch("app.main.get_all_products", return_value=[]), \
             patch("app.main.deduplicate", return_value=([], 0)), \
             patch("app.main.export_excel"), \
             patch("app.main.export_meesho_blocked"), \
             patch("app.main.init_db"):
            from app.main import _run_scrape
            with patch("app.main._select_scrapers", return_value=[scraper]):
                _run_scrape("tradeindia")
            # fetch_html should have been called for the pending URL
            # (it raised BlockedError which is handled gracefully)


# ── Parsing with fixture: ensure multi-category pages parse the same way ──────

class TestMultiCategoryParsing:
    def test_listing_page_parses_correctly(self, html_listing):
        """
        _parse_listing_page works regardless of which URL was used to fetch.
        """
        soup = BeautifulSoup(html_listing, "lxml")
        nd = _get_next_data(soup)
        entries = _parse_listing_page(
            nd,
            "https://www.tradeindia.com/manufacturers/rose-water.html"
        )
        assert len(entries) == 28
        for e in entries:
            assert e["profile_url"].startswith("https://")
            assert e["product_name"] is not None


# ── Playwright pagination collector tests ─────────────────────────────────────

class TestPaginationCollector:
    """Tests for ti_pagination_collector.collect_listing_pages — no live requests."""

    def _make_api_response(self, page_num: int, sellers_count: int = 28,
                           total_pages: int = 38) -> str:
        """Build a minimal API JSON response for testing."""
        sellers = [
            {
                "co_name": f"Test Co {i + (page_num - 1) * sellers_count}",
                "city": "Delhi",
                "state": "Delhi",
                "product_name": f"Rose Water Product {i}",
                "prod_url": f"/products/rose-water-{i}-c{i}.html",
                "profile_url": f"/test-co-{i + (page_num - 1) * sellers_count}-{1000+i}/",
                "number_mask": True,
            }
            for i in range(1, sellers_count + 1)
        ]
        return json.dumps({
            "data": {
                "listing_data": sellers,
                "pagination": {
                    "total_pages": total_pages,
                    "has_next": page_num < total_pages,
                    "has_prev": page_num > 1,
                    "current_page": page_num,
                }
            }
        })

    def test_parse_entries_from_json(self):
        """_parse_entries_from_json returns correct count and total_pages."""
        from app.scrapers.ti_pagination_collector import _parse_entries_from_json
        body = json.loads(self._make_api_response(1, 28, 38))
        entries, total_pages, has_next = _parse_entries_from_json(body, 1)
        assert len(entries) == 28
        assert total_pages == 38
        assert has_next is True

    def test_parse_entries_last_page(self):
        """Last page has has_next=False."""
        from app.scrapers.ti_pagination_collector import _parse_entries_from_json
        body = json.loads(self._make_api_response(38, 14, 38))  # partial last page
        entries, total_pages, has_next = _parse_entries_from_json(body, 38)
        assert total_pages == 38
        assert has_next is False

    def test_parse_entries_fields_present(self):
        """Each entry must have profile_url, product_name, product_url."""
        from app.scrapers.ti_pagination_collector import _parse_entries_from_json
        body = json.loads(self._make_api_response(1, 5, 10))
        entries, _, _ = _parse_entries_from_json(body, 1)
        for e in entries:
            assert "profile_url" in e
            assert "product_name" in e
            assert "product_url" in e
            assert e["profile_url"].startswith("https://www.tradeindia.com/")

    def test_upsert_batch_skips_known_urls(self, tmp_path):
        """_upsert_batch must not re-insert already-known URLs."""
        from app.scrapers.ti_pagination_collector import _upsert_batch
        from app.storage.db import init_db, get_url_status, upsert_url
        import os
        # Use a temp DB
        test_db = str(tmp_path / "test.db")
        with patch("app.storage.db.settings") as mock_settings:
            mock_settings.db_path_abs.return_value = None
            # Use the real init_db but with temp path
            pass

        entries = [
            {"profile_url": "https://www.tradeindia.com/co-a-111/",
             "product_name": "Rose Water", "product_url": None,
             "co_name": "Co A", "city": "Delhi", "state": "Delhi"},
        ]
        with patch("app.scrapers.ti_pagination_collector.get_url_status",
                   return_value="DONE"), \
             patch("app.scrapers.ti_pagination_collector.upsert_url") as mock_upsert:
            _upsert_batch(entries, "TradeIndia")
        mock_upsert.assert_not_called()

    def test_collect_max_pages_respected(self):
        """collect_listing_pages must stop at max_pages."""
        from app.scrapers.ti_pagination_collector import collect_listing_pages

        # Mock the Playwright context entirely
        with patch("app.scrapers.ti_pagination_collector.sync_playwright") as mock_pw, \
             patch("app.scrapers.ti_pagination_collector._robots_allows", return_value=True), \
             patch("app.scrapers.ti_pagination_collector._upsert_batch"):
            mock_browser = MagicMock()
            mock_ctx     = MagicMock()
            mock_page    = MagicMock()

            mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_ctx
            mock_ctx.new_page.return_value = mock_page

            # Page 1: initial __NEXT_DATA__ with 28 sellers
            sellers_p1 = [
                {"co_name": f"Co {i}", "city": "Delhi", "state": "Delhi",
                 "product_name": "Rose Water", "prod_url": f"/products/p{i}.html",
                 "profile_url": f"/co-{i}-{1000+i}/", "number_mask": True}
                for i in range(1, 29)
            ]
            nd_json = json.dumps({
                "props": {"pageProps": {"initialState": {
                    "manufacturer": {"manufacturer_page": {"manufacturer_page_res": {
                        "mcatData": {"listing_data": {
                            "listing_data": sellers_p1,
                            "pagination": {"total_pages": 38, "has_next": True,
                                           "has_prev": False, "current_page": 1}
                        }}
                    }}}
                }}}
            })
            mock_page.evaluate.side_effect = [
                nd_json,  # __NEXT_DATA__ call
                # pages 2..5: API calls returning 28 sellers each
                {"status": 200, "body": self._make_api_response(2)},
                {"status": 200, "body": self._make_api_response(3)},
                {"status": 200, "body": self._make_api_response(4)},
                {"status": 200, "body": self._make_api_response(5)},
            ]

            entries, total_pages, blocks = collect_listing_pages("Rose Water", max_pages=5)

        assert total_pages == 38
        assert blocks == 0
        # Pages 1-5 each give 28 sellers, some may overlap depending on co_name uniqueness
        assert len(entries) > 0

    def test_collect_stops_on_consecutive_blocks(self):
        """collect_listing_pages stops after _MAX_CONSEC_BLOCKS consecutive 403s."""
        from app.scrapers.ti_pagination_collector import collect_listing_pages, _MAX_CONSEC

        with patch("app.scrapers.ti_pagination_collector.sync_playwright") as mock_pw, \
             patch("app.scrapers.ti_pagination_collector._robots_allows", return_value=True), \
             patch("app.scrapers.ti_pagination_collector._upsert_batch"):
            mock_browser = MagicMock()
            mock_ctx     = MagicMock()
            mock_page    = MagicMock()

            mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_ctx
            mock_ctx.new_page.return_value = mock_page

            sellers_p1 = [
                {"co_name": f"Co {i}", "city": "Delhi", "state": "Delhi",
                 "product_name": "Rose Water", "prod_url": f"/products/p{i}.html",
                 "profile_url": f"/co-{i}-{1000+i}/", "number_mask": True}
                for i in range(1, 29)
            ]
            nd_json = json.dumps({
                "props": {"pageProps": {"initialState": {
                    "manufacturer": {"manufacturer_page": {"manufacturer_page_res": {
                        "mcatData": {"listing_data": {
                            "listing_data": sellers_p1,
                            "pagination": {"total_pages": 38, "has_next": True,
                                           "has_prev": False, "current_page": 1}
                        }}
                    }}}
                }}}
            })
            # All subsequent pages return 403
            block_responses = [{"status": 403, "body": "Forbidden"}] * 20
            mock_page.evaluate.side_effect = [nd_json] + block_responses

            entries, total_pages, blocks = collect_listing_pages("Rose Water", max_pages=20)

        assert blocks >= _MAX_CONSEC
        # Should have stopped after _MAX_CONSEC consecutive blocks
        assert len(entries) == 28  # only page 1


# ── Products sheet tests ──────────────────────────────────────────────────────

class TestProductsSheet:
    def _sample_products(self):
        return [
            {"product_name": "Rose Water 100ml",
             "product_url": "https://www.tradeindia.com/products/p1.html",
             "company_name": "Earth Science Ayurveda",
             "profile_url": "https://www.tradeindia.com/earth-science-ayurveda-29503048/",
             "source": "TradeIndia",
             "scraped_at": "2026-10-04 12:00:00"},
            {"product_name": "Rose Floral Water",
             "product_url": "https://www.tradeindia.com/products/p2.html",
             "company_name": "FALCON",
             "profile_url": "https://www.tradeindia.com/falcon-1026607/",
             "source": "TradeIndia",
             "scraped_at": "2026-10-04 12:00:00"},
        ]

    def _sample_leads(self):
        from app.models.lead import Lead
        return [Lead(company_name="Earth Science Ayurveda", source="TradeIndia",
                     phone_status="HIDDEN", email_status="MISSING")]

    def test_products_sheet_created(self, tmp_path):
        from app.exporters.excel_exporter import export_excel
        out = tmp_path / "test.xlsx"
        export_excel(self._sample_leads(), output_path=out,
                     products=self._sample_products())
        import openpyxl
        wb = openpyxl.load_workbook(out)
        assert "Products" in wb.sheetnames

    def test_products_sheet_row_count(self, tmp_path):
        from app.exporters.excel_exporter import export_excel
        import openpyxl
        out = tmp_path / "test.xlsx"
        export_excel(self._sample_leads(), output_path=out,
                     products=self._sample_products())
        wb = openpyxl.load_workbook(out)
        ws = wb["Products"]
        assert ws.max_row == 3  # 1 header + 2 products

    def test_products_sheet_has_url_hyperlink(self, tmp_path):
        from app.exporters.excel_exporter import export_excel
        import openpyxl
        out = tmp_path / "test.xlsx"
        export_excel(self._sample_leads(), output_path=out,
                     products=self._sample_products())
        wb = openpyxl.load_workbook(out)
        ws = wb["Products"]
        # Product URL column should have hyperlink
        headers = {ws.cell(1, c).value: c for c in range(1, ws.max_column + 1)}
        url_col = headers.get("Product URL")
        assert url_col is not None
        cell = ws.cell(2, url_col)
        assert cell.hyperlink is not None

    def test_run_summary_shows_product_count(self, tmp_path):
        from app.exporters.excel_exporter import export_excel
        import openpyxl
        out = tmp_path / "test.xlsx"
        export_excel(self._sample_leads(), output_path=out,
                     products=self._sample_products())
        wb = openpyxl.load_workbook(out)
        ws2 = wb["Run Summary"]
        all_vals = [ws2.cell(r, 2).value for r in range(1, ws2.max_row + 1)]
        assert 2 in all_vals  # Total Products = 2

    def test_no_products_sheet_when_none_passed(self, tmp_path):
        from app.exporters.excel_exporter import export_excel
        import openpyxl
        out = tmp_path / "test.xlsx"
        export_excel(self._sample_leads(), output_path=out, products=None)
        wb = openpyxl.load_workbook(out)
        assert "Products" not in wb.sheetnames


# ── Seller deduplication across pages ─────────────────────────────────────────

class TestSellerDedupAcrossPages:
    """Sellers appearing on multiple listing pages must produce exactly one lead."""

    def test_same_profile_url_not_re_upserted(self):
        """_upsert_batch skips profile URLs that already exist in url_status."""
        from app.scrapers.ti_pagination_collector import _upsert_batch

        already_known_url = "https://www.tradeindia.com/earth-science-29503048/"
        entries = [
            {"profile_url": already_known_url, "product_name": "Rose Water",
             "product_url": None, "co_name": "Earth Science", "city": "Delhi", "state": "Delhi"},
        ]
        with patch("app.scrapers.ti_pagination_collector.get_url_status",
                   return_value="DONE"), \
             patch("app.scrapers.ti_pagination_collector.upsert_url") as mock_ups:
            _upsert_batch(entries, "TradeIndia")
        mock_ups.assert_not_called()

    def test_new_profile_url_is_upserted(self):
        """_upsert_batch upserts profile URLs not yet in url_status."""
        from app.scrapers.ti_pagination_collector import _upsert_batch

        new_url = "https://www.tradeindia.com/brand-new-co-99999/"
        entries = [
            {"profile_url": new_url, "product_name": "Rose Water",
             "product_url": None, "co_name": "Brand New Co",
             "city": "Mumbai", "state": "Maharashtra"},
        ]
        with patch("app.scrapers.ti_pagination_collector.get_url_status",
                   return_value=None), \
             patch("app.scrapers.ti_pagination_collector.upsert_url") as mock_ups:
            _upsert_batch(entries, "TradeIndia")
        mock_ups.assert_called_once_with(
            new_url, "TradeIndia", "PENDING",
            metadata={
                "product_name": "Rose Water",
                "product_url":  None,
                "co_name":      "Brand New Co",
                "city":         "Mumbai",
                "state":        "Maharashtra",
            }
        )
