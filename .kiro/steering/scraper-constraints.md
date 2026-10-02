---
inclusion: always
---

# Rose Water Scraper — Hard Constraints

These rules apply to every task in this project. They are non-negotiable and must be
respected in every file you create or modify.

## 1. Scope

- **Product**: ONLY "Rose Water". The keyword comes from `SEARCH_PRODUCTS` in `.env`. Never
  hardcode "Rose Water" anywhere in scraper logic.
- **Sources**: ONLY Meesho and TradeIndia. Do not visit seller websites, search engines,
  or any third-party URL to enrich data.
- **Output**: `output/rose_water_customers.xlsx` (path from `OUTPUT_FILE` in `.env`).

## 2. Ethical & Legal Constraints

- Collect ONLY information that is publicly visible on the page **without logging in**.
- **NEVER** implement any of the following:
  - CAPTCHA solving or bypass
  - Login, OTP, or session bypass
  - Proxy rotation to evade blocks
  - Fingerprint spoofing or deceptive User-Agent strings
  - Automating "View Number", "Contact Supplier", or any reveal-on-click flow
- If a page is blocked (403, 429, connection reset), needs login, or hides contact details
  behind a click/reveal:
  - Set status to `BLOCKED` or `CONTACT_HIDDEN`
  - Log it with source, url, stage, status, error, timestamp
  - Skip and continue — **never crash the run**
- Use a clear, honest `User-Agent` string (e.g. `AuralEssenceScraper/1.0 (+research-bot)`).
- Check and log `robots.txt` for each domain at startup. Respect all `Disallow` rules.
- Apply a configurable delay between requests (`REQUEST_DELAY` from `.env`, default 3 s).
- Never store secrets in code. Use `.env` + `python-dotenv`.

## 3. Data Integrity

- **Never invent or guess values**. If a field is not on the page, set it to `None`.
- `company_name` is required. Records without it must be skipped.
- Phone numbers behind a "View Number" button or similar reveal flow are `HIDDEN`, not
  `MISSING`. Set `phone_status = HIDDEN`.
- Validation is format-only. A valid phone format does not prove the number is genuine.

## 4. robots.txt Key Findings (do not revisit unless re-spiking)

### TradeIndia (`tradeindia.com`)
| Path | Status |
|---|---|
| `/search/` | DISALLOWED |
| `*?page=*` | DISALLOWED (no query-param pagination) |
| `/Exporters_Suppliers/` | DISALLOWED |
| `/suppliers/` | DISALLOWED |
| `/manufacturers/` | **ALLOWED** |
| `/{company-slug}-{id}/` profile pages | **ALLOWED** |
| `/products/{slug}.html` | **ALLOWED** |

### Meesho (`meesho.com`)
| Path | Status |
|---|---|
| `/*/search` | DISALLOWED |
| `/*?page=*` | DISALLOWED |
| Product listing pages | Technically allowed but **WAF-blocked** (Akamai 403) |

## 5. Phase 0 Spike Findings Summary

### TradeIndia — viable source
- Pages are Next.js SSR. `__NEXT_DATA__` is embedded and contains all structured data.
- **Listing page** (`/manufacturers/rose-water.html`): 28 sellers per page, 1 063 total products.
  Each entry contains `co_name`, `city`, `state`, `product_name`, `prod_url`, `profile_url`,
  `number_mask`. **No phone/email/address** in listing data.
- **Seller profile page** (`/{slug}-{id}/`): Contains full `address`, `owner_name`, `gst_no`,
  `desg`, `city`, `state`. Phone is **always hidden** (`number_mask: true`). Email is `None`.
- **Product detail page** (`/products/{slug}.html`): Contains `product_name`, `prod_url`, plus
  the same seller fields as the profile page (`company_details` block).
- **Preferred parse path**: `__NEXT_DATA__` → `initialState` → `sellerProfile` /
  `manufacturer_page` / `PDP_page` depending on page type.
- **Do NOT use CSS selectors** as primary parse strategy; the JSON is reliable and complete.
- Pagination: `?page=` is disallowed. Investigate path-based pagination
  (`/manufacturers/rose-water-2.html`) in Phase 2 before assuming only one page.

### Meesho — NOT a viable B2B contact source
- All HTTP requests (any URL, any User-Agent) return **HTTP 403 from Akamai EdgeSuite WAF**.
- Meesho is a **D2C marketplace**, not a B2B directory. It does **not** publish seller
  phone numbers, emails, or business addresses publicly, even to logged-in users.
- The `/api/v1/products` endpoint returns a `429` with a bot-challenge token.
- `/graphql` returns the SPA HTML shell (no real GraphQL endpoint accessible).
- Even with Playwright, the contact fields required by this project are absent from their pages.
- **Decision**: Implement `MeeshoScraper` as a graceful no-op that logs
  `BLOCKED / SCOPE_MISMATCH` and returns an empty list. Do not waste retries on it.

## 6. Architecture Reminders

- All scraper logic reads selectors/URL templates from `app/config/sources.yaml`.
  Do **not** hardcode selectors or URLs in Python scraper files.
- Parsing priority order (per spec §4): (1) `__NEXT_DATA__` / embedded JSON-LD,
  (2) semantic HTML, (3) CSS selectors from `sources.yaml`.
- One failing listing never crashes the run. Wrap every per-URL call in try/except.
- SQLite (`storage/db.py`) tracks URL status: `PENDING → DONE | FAILED | BLOCKED`.
  A resumed run must not re-scrape `DONE` URLs.
- Cache raw HTML to `data/raw_html/` so parsers can be re-run without re-fetching.
