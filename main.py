"""Extract all product images from an Amazon product page URL.

Usage:
    uv run main.py <amazon-product-url> [--output DIR]

Example:
    uv run main.py "https://www.amazon.com/dp/B08N5WRWNW" --output ./images
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright  # type: ignore

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Headers that closely mimic a real Chrome browser on macOS.
# The sec-fetch-* set and dnt are checked by Amazon's bot detection.
HEADERS = {
    "dnt": "1",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "accept-encoding": "gzip, deflate, br",
    "accept-language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
}

# Amazon image URLs include a size token like `._AC_SX466_` or `._SL1500_`
# right before the file extension. Removing it returns the largest original.
SIZE_TOKEN_RE = re.compile(r"\._[A-Z0-9_,]+_(?=\.(?:jpg|jpeg|png|webp)$)", re.IGNORECASE)

# Matches the `'colorImages': { 'initial': [ ... ] }` block embedded in the
# product page's inline scripts. This block holds the full image gallery.
COLOR_IMAGES_RE = re.compile(
    r"['\"]colorImages['\"]\s*:\s*(\{.*?\})\s*,\s*['\"]colorToAsin['\"]",
    re.DOTALL,
)

# Fallback: a flat `'imageGalleryData' : [ { ... }, ... ]` array some pages use.
IMAGE_GALLERY_RE = re.compile(
    r"['\"]imageGalleryData['\"]\s*:\s*(\[.*?\])\s*[,}]",
    re.DOTALL,
)


def normalize_image_url(url: str) -> str:
    """Strip the size token so we download the largest available image."""
    return SIZE_TOKEN_RE.sub("", url)


def _coerce_json(blob: str) -> object | None:
    """Amazon embeds the gallery data as JS-ish JSON with single quotes.
    Try JSON first, then fall back to a single-quote -> double-quote rewrite."""
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass
    candidate = re.sub(r"'", '"', blob)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def extract_from_color_images(html: str) -> list[str]:
    """Pull image URLs out of the `colorImages` block."""
    urls: list[str] = []
    match = COLOR_IMAGES_RE.search(html)
    if not match:
        return urls
    data = _coerce_json(match.group(1))
    if not isinstance(data, dict):
        return urls
    for variant_images in data.values():
        if not isinstance(variant_images, list):
            continue
        for img in variant_images:
            if not isinstance(img, dict):
                continue
            for key in ("hiRes", "large", "mainUrl", "main"):
                value = img.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    urls.append(value)
                    break
                if isinstance(value, dict):
                    best = max(
                        value.items(),
                        key=lambda kv: (kv[1][0] * kv[1][1])
                        if isinstance(kv[1], list) and len(kv[1]) == 2
                        else 0,
                        default=(None, None),
                    )
                    if best[0]:
                        urls.append(best[0])
                        break
    return urls


def extract_from_image_gallery(html: str) -> list[str]:
    """Fallback: parse the `imageGalleryData` array."""
    urls: list[str] = []
    match = IMAGE_GALLERY_RE.search(html)
    if not match:
        return urls
    data = _coerce_json(match.group(1))
    if not isinstance(data, list):
        return urls
    for img in data:
        if isinstance(img, dict):
            value = img.get("mainUrl") or img.get("large")
            if isinstance(value, str):
                urls.append(value)
    return urls


def extract_from_img_tags(html: str) -> list[str]:
    """Final fallback: scrape `data-a-dynamic-image` from <img> tags.

    Amazon stores a JSON map of URL -> [width, height] on the gallery
    thumbnails. We pick the largest URL from each.
    """
    urls: list[str] = []
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        dynamic = img.get("data-a-dynamic-image")
        if not dynamic:
            continue
        data = _coerce_json(dynamic)
        if not isinstance(data, dict):
            continue
        best = max(
            data.items(),
            key=lambda kv: (kv[1][0] * kv[1][1])
            if isinstance(kv[1], list) and len(kv[1]) == 2
            else 0,
            default=(None, None),
        )
        if best[0]:
            urls.append(best[0])
    return urls


_ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})(?:[/?]|$)")
_VARIANT_ASIN_RE = _ASIN_RE


def asin_from_url(url: str) -> str | None:
    m = _ASIN_RE.search(url)
    return m.group(1) if m else None


def _extract_name(soup: BeautifulSoup) -> str | None:
    el = soup.find(id="productTitle")
    return el.get_text(strip=True) if el else None


def _extract_price(soup: BeautifulSoup) -> str | None:
    for sel in (
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#price_inside_buybox",
        ".a-price .a-offscreen",
        "#sns-base-price",
    ):
        el = soup.select_one(sel)
        if el:
            return el.get_text(strip=True)
    return None


def _extract_short_description(soup: BeautifulSoup) -> str | None:
    el = soup.find(id="feature-bullets")
    if not el:
        return None
    items = [
        li.get_text(" ", strip=True)
        for li in el.select("li span.a-list-item")
        if li.get_text(strip=True)
    ]
    return "\n".join(items) if items else None


def _extract_images_raw(soup: BeautifulSoup) -> str | None:
    """Return the data-a-dynamic-image JSON string from the main product image."""
    for sel in ("#landingImage", "#imgBlkFront", "#main-image"):
        img = soup.select_one(sel)
        if img:
            dynamic = img.get("data-a-dynamic-image")
            if dynamic:
                return dynamic
    return None


def _extract_variants(soup: BeautifulSoup) -> list[dict] | None:
    variants: list[dict] = []
    for li in soup.select("li[data-dp-url]"):
        span = li.find("span", title=True)
        asin_match = _VARIANT_ASIN_RE.search(li.get("data-dp-url", ""))
        if span and asin_match:
            variants.append({"name": span["title"], "asin": asin_match.group(1)})
    if variants:
        return variants
    for sel_id in ("native_dropdown_selected_size_name", "native_dropdown_selected_color_name"):
        for option in soup.select(f"select#{sel_id} option[value]"):
            val = option["value"].strip()
            if val and re.fullmatch(r"[A-Z0-9]{10}", val):
                variants.append({"name": option.get_text(strip=True), "asin": val})
    return variants or None


def _extract_product_description(soup: BeautifulSoup) -> str | None:
    for sel_id in ("productDescription", "aplus"):
        el = soup.find(id=sel_id)
        if el:
            text = el.get_text("\n", strip=True)
            if text:
                return text
    return None


def _extract_reviews_link(soup: BeautifulSoup, origin: str) -> str | None:
    for sel in (
        "a[data-hook='see-all-reviews-link-foot']",
        "#reviews-medley-footer a",
        "a[href*='product-reviews']",
    ):
        el = soup.select_one(sel)
        if el and el.get("href"):
            href = el["href"]
            return href if href.startswith("http") else origin + href
    return None


def extract_product_details(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return {
        "name": _extract_name(soup),
        "price": _extract_price(soup),
        "short_description": _extract_short_description(soup),
        "images": _extract_images_raw(soup),
        "variants": _extract_variants(soup),
        "product_description": _extract_product_description(soup),
        "link_to_all_reviews": _extract_reviews_link(soup, origin),
    }


def extract_image_urls(html: str) -> list[str]:
    """Collect image URLs from every source we know about and dedupe."""
    collected: list[str] = []
    collected.extend(extract_from_color_images(html))
    collected.extend(extract_from_image_gallery(html))
    collected.extend(extract_from_img_tags(html))

    seen: set[str] = set()
    unique: list[str] = []
    for url in collected:
        normalized = normalize_image_url(url)
        if not normalized.startswith("http"):
            continue
        if (
            "media-amazon.com/images/I/" not in normalized
            and "images-amazon.com/images/I/" not in normalized
        ):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def filename_from_url(url: str, index: int) -> str:
    """Build a safe filename from the image URL."""
    parsed = urlparse(url)
    stem = Path(parsed.path).name or f"image_{index}"
    return f"{index:02d}_{stem}"


def download_images(urls: list[str], output_dir: Path) -> list[Path]:
    """Download each URL into `output_dir`. Returns the saved file paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    with requests.Session() as session:
        session.headers.update(HEADERS)
        for i, url in enumerate(urls, start=1):
            destination = output_dir / filename_from_url(url, i)
            print(f"  [{i}/{len(urls)}] {url}")
            try:
                response = session.get(url, timeout=30)
                response.raise_for_status()
            except requests.RequestException as exc:
                print(f"    ! failed: {exc}", file=sys.stderr)
                continue
            destination.write_bytes(response.content)
            saved.append(destination)
    return saved


def fetch_page_browser(url: str) -> str:
    """Fetch the Amazon product page using a real headless Chromium browser.

    This bypasses Amazon's bot detection because Playwright renders full JS,
    handles cookies/fingerprinting, and looks identical to a real user session.
    Also handles Amazon geo-redirects (e.g. amazon.com -> amazon.com.br).
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "Playwright is not installed. Run `uv add playwright && uv run playwright install chromium`."
        )

    parsed = urlparse(url)
    original_netloc = parsed.netloc
    origin = f"{parsed.scheme}://{parsed.netloc}"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=HEADERS["user-agent"],
            locale="en-US",
            viewport={"width": 1366, "height": 768},
            extra_http_headers={
                "accept-language": HEADERS["accept-language"],
                "dnt": HEADERS["dnt"],
            },
        )
        page = context.new_page()

        # Mask the webdriver flag that Amazon looks for.
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # Warm up: visit the homepage to collect cookies and detect geo-redirect.
        try:
            page.goto(origin + "/", wait_until="domcontentloaded", timeout=20_000)
            time.sleep(1)
        except Exception:
            pass

        # Detect if Amazon redirected us to a regional domain (e.g. .com -> .com.br).
        final_home_url = page.url
        final_netloc = urlparse(final_home_url).netloc
        if final_netloc and final_netloc != original_netloc:
            print(
                f"  Note: Amazon redirected to regional domain ({final_netloc}). "
                f"Rewriting product URL."
            )
            url = url.replace(original_netloc, final_netloc)

        page.goto(url, wait_until="networkidle", timeout=45_000)
        html = page.content()
        browser.close()

    _check_for_bot_block_html(html)
    return html


def fetch_page(url: str) -> str:
    """Fallback: fetch via requests (no JS, may be blocked by Amazon)."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    with requests.Session() as session:
        session.headers.update(HEADERS)
        try:
            session.get(origin + "/", timeout=15)
        except requests.RequestException:
            pass
        session.headers["referer"] = origin + "/"
        session.headers["sec-fetch-site"] = "same-origin"
        response = session.get(url, timeout=30)

    _check_for_bot_block(response)
    response.raise_for_status()
    return response.text


def _check_for_bot_block_html(html: str) -> None:
    """Raise if the rendered HTML looks like a bot-block, CAPTCHA, or 404 page."""
    # Genuine product-not-found: tiny page with no product data.
    not_found_phrases = [
        "<title>Page Not Found</title>",
        "Page Not Found",
        "N\u00e3o foi poss\u00edvel encontrar esta p\u00e1gina",  # amazon.com.br Portuguese 404
        "p\u00e1gina n\u00e3o encontrada",
    ]
    if any(p in html for p in not_found_phrases) and len(html) < 10_000:
        raise RuntimeError(
            "Amazon returned a 'Page Not Found' page. "
            "The ASIN may be invalid, region-restricted, or the product may have been removed."
        )

    bot_phrases = [
        "To discuss automated access to Amazon data please contact",
        "Sorry, we just need to make sure you're not a robot",
        "Enter the characters you see below",
        "api-services-support@amazon.com",
        "Type the characters you see in this image",
    ]
    if any(phrase in html for phrase in bot_phrases):
        raise RuntimeError(
            "Amazon served a CAPTCHA/bot-block page even with a real browser. "
            "Try again later or from a different network."
        )


def _check_for_bot_block(response: requests.Response) -> None:
    """Raise a clear RuntimeError if Amazon returned a bot-block page."""
    code = response.status_code
    text = response.text
    bot_phrases = [
        "To discuss automated access to Amazon data please contact",
        "Sorry, we just need to make sure you're not a robot",
        "Enter the characters you see below",
        "api-services-support@amazon.com",
    ]
    if any(phrase in text for phrase in bot_phrases):
        raise RuntimeError(
            f"Page was blocked by Amazon's bot detection (HTTP {code}). "
            "Try again later, use a different network, or add a delay between requests."
        )
    if code in (404, 503) or code >= 500:
        raise RuntimeError(
            f"Amazon returned HTTP {code}. This is usually a bot-block or geo-restriction. "
            "Try again later or from a different network."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract all product images from an Amazon product page URL."
    )
    parser.add_argument("url", help="Amazon product page URL")
    parser.add_argument(
        "-o",
        "--output",
        default="images",
        help="Output folder for downloaded images (default: ./images)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print image URLs without downloading them.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Skip Playwright and use plain HTTP requests (faster but more likely to be blocked).",
    )
    args = parser.parse_args()

    use_browser = not args.no_browser and PLAYWRIGHT_AVAILABLE

    print(f"Fetching {args.url}" + (" (browser mode)" if use_browser else " (requests mode)"))
    try:
        html = fetch_page_browser(args.url) if use_browser else fetch_page(args.url)
    except (requests.RequestException, RuntimeError, Exception) as exc:
        print(f"Failed to fetch page: {exc}", file=sys.stderr)
        return 1

    details = extract_product_details(html, args.url)
    image_urls = extract_image_urls(html)

    if args.dry_run:
        print(json.dumps(details, indent=2, ensure_ascii=False))
        return 0

    asin = asin_from_url(args.url)
    base_dir = Path(args.output).resolve()
    output_dir = base_dir / asin if asin else base_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "product.json"
    json_path.write_text(json.dumps(details, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved product details to {json_path}")

    if not image_urls:
        print(
            "No product images found. Amazon may have served a bot-block page; "
            "try again later or from a different network.",
            file=sys.stderr,
        )
        return 0

    print(f"Found {len(image_urls)} unique product image(s).")
    saved = download_images(image_urls, output_dir)
    print(f"Saved {len(saved)} image(s) to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
