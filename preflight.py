"""Pre-flight checks — runs all module imports and quick unit assertions."""
from app.config.settings import settings
from app.models.lead import Lead
from app.storage.db import init_db
from app.scrapers.tradeindia_scraper import TradeIndiaScraper, _keyword_slug
from app.scrapers.meesho_scraper import MeeshoScraper
from app.validators.phone_validator import validate_phone
from app.validators.email_validator import validate_email_address
from app.processors.normalizer import normalise_lead, dedup_key
from app.processors.deduplicator import deduplicate
from app.exporters.excel_exporter import export_excel

# Keyword slug
assert _keyword_slug("Rose Water") == "rose-water", "slug failed"

# Phone validator
assert validate_phone("+919876543210") == ("+919876543210", "VALID"), "valid phone failed"
assert validate_phone("View Number") == (None, "HIDDEN"), "hidden phone failed"
assert validate_phone(None)           == (None, "MISSING"), "missing phone failed"
assert validate_phone("9876543210")   == ("+919876543210", "VALID"), "10-digit phone failed"

# Email validator — use a real registered domain for VALID test
norm, status = validate_email_address("user@gmail.com")
assert status == "VALID", f"gmail email expected VALID, got {status}"
assert validate_email_address("test@example.com") == (None, "INVALID"), "example.com should be INVALID"
assert validate_email_address(None) == (None, "MISSING"), "None email failed"

# Dedup key strips legal suffixes
l = Lead(company_name="GIRNAR AYURVEDIC PHARMACY PVT. LTD.", source="TradeIndia")
key = dedup_key(l)
assert "pvt" not in key, f"pvt not stripped: {key}"
assert "ltd" not in key, f"ltd not stripped: {key}"

# Deduplication merge
l1 = Lead(company_name="Earth Science Ayurveda", city="Faridabad",
          phone_number="+919876543210", phone_status="VALID",
          product_url="https://ti.com/p1", source="TradeIndia")
l2 = Lead(company_name="Earth Science Ayurveda", city="Faridabad",
          phone_number="+919876543210", phone_status="VALID",
          product_url="https://ti.com/p2", source="TradeIndia")
deduped, merged = deduplicate([l1, l2])
assert len(deduped) == 1, f"expected 1 after dedup, got {len(deduped)}"
assert merged == 1, f"expected 1 merge, got {merged}"
all_urls = [deduped[0].product_url] + deduped[0].extra_product_urls
assert "https://ti.com/p1" in all_urls, f"p1 missing from {all_urls}"
assert "https://ti.com/p2" in all_urls, f"p2 missing from {all_urls}"

print("All pre-flight checks passed.")
print("settings.products      :", settings.products)
print("TI listing URL (page 1):", TradeIndiaScraper().listing_url("Rose Water", 1))
print("TI listing URL (page 2):", TradeIndiaScraper().listing_url("Rose Water", 2))
