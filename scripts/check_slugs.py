"""
One-off script: probe TradeIndia manufacturer listing pages for a list of product slugs.
Uses __NEXT_DATA__ JSON – same strategy as the main scraper.
"""

import time
import json
import requests

UA = "AuralEssenceScraper/1.0 (+research-bot)"
DELAY = 4  # seconds between requests

SLUGS = [
    ("Hair Growth Oil",          "hair-growth-oil"),
    ("Hair Growth Serum",        "hair-growth-serum"),
    ("Hair Serum",               "hair-serum"),
    ("Hair Mist",                "hair-mist"),
    ("Hair Spray",               "hair-spray"),
    ("Hair Tonic",               "hair-tonic"),
    ("Scalp Serum",              "scalp-serum"),
    ("Scalp Spray",              "scalp-spray"),
    ("Hair Conditioner",         "hair-conditioner"),
    ("Hair Mask",                "hair-mask"),
    ("Shampoo",                  "shampoo"),
    ("Herbal Shampoo",           "herbal-shampoo"),
    ("Anti Hair Fall Shampoo",   "anti-hair-fall-shampoo"),
]

GENERIC_PATHS = {
    "/manufacturers/",
    "/manufacturers",
}

def extract_next_data(html: str) -> dict | None:
    import re
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None

def probe(product_name: str, slug: str) -> dict:
    url = f"https://www.tradeindia.com/manufacturers/{slug}.html"
    result = {"product": product_name, "slug": slug, "url": url}
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": UA},
            timeout=20,
            allow_redirects=True,
        )
        result["http_status"] = resp.status_code

        # Check for redirect to generic manufacturers page
        final_path = resp.url.rstrip("/").replace("https://www.tradeindia.com", "")
        if final_path in GENERIC_PATHS or final_path == "":
            result["verdict"] = "REDIRECT → generic manufacturers page"
            result["total_pages"] = 0
            result["seller_count_page1"] = 0
            return result

        if resp.status_code != 200:
            result["verdict"] = f"HTTP {resp.status_code}"
            result["total_pages"] = 0
            result["seller_count_page1"] = 0
            return result

        nd = extract_next_data(resp.text)
        if nd is None:
            result["verdict"] = "NO __NEXT_DATA__"
            result["total_pages"] = 0
            result["seller_count_page1"] = 0
            return result

        try:
            mcat = (
                nd["props"]["pageProps"]["initialState"]
                  ["manufacturer"]["manufacturer_page"]
                  ["manufacturer_page_res"]["mcatData"]
                  ["listing_data"]
            )
            total_pages   = mcat["pagination"]["total_pages"]
            listing_items = mcat["listing_data"]
            seller_count  = len(listing_items) if isinstance(listing_items, list) else 0
            result["total_pages"]        = total_pages
            result["seller_count_page1"] = seller_count
            result["verdict"]            = "OK" if seller_count > 0 else "EMPTY (0 sellers)"
        except (KeyError, TypeError) as e:
            # Maybe it redirected server-side to a different page type
            # Try to detect if we landed on the generic manufacturers index
            if "manufacturer_page" not in resp.text and slug not in resp.url:
                result["verdict"] = "REDIRECT (server-side) or wrong page"
            else:
                result["verdict"] = f"PARSE_ERROR: {e}"
            result["total_pages"] = 0
            result["seller_count_page1"] = 0

    except requests.RequestException as e:
        result["http_status"] = "ERROR"
        result["verdict"] = str(e)
        result["total_pages"] = 0
        result["seller_count_page1"] = 0

    return result


def main():
    print(f"\n{'='*80}")
    print(f"{'Product':<28} {'Slug':<30} {'Status':>6}  {'Pages':>5}  {'Sellers/p1':>10}  Verdict")
    print(f"{'='*80}")

    results = []
    for i, (name, slug) in enumerate(SLUGS):
        if i > 0:
            time.sleep(DELAY)
        r = probe(name, slug)
        results.append(r)
        print(
            f"{r['product']:<28} {r['slug']:<30} "
            f"{str(r.get('http_status','?')):>6}  "
            f"{str(r.get('total_pages','?')):>5}  "
            f"{str(r.get('seller_count_page1','?')):>10}  "
            f"{r.get('verdict','')}"
        )

    # Save full results to JSON
    out_path = "data/slug_probe_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    main()
