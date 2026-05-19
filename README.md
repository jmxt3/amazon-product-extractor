# Amazon Product Extractor

Extracts product details and images from an Amazon product page URL. Each product is saved to its own subfolder (named by ASIN) so runs for different products never mix.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (recommended)

## Installation

```bash
git clone <repo-url>
cd amazon_product_extractor
uv sync
uv run playwright install chromium
```

`uv sync` installs all Python dependencies. The second command downloads the Chromium browser used to bypass Amazon's bot detection.

## Usage

```
uv run amazon-extract <amazon-product-url> [--output DIR] [--dry-run] [--mode MODE]
```

### Arguments

| Argument | Description |
|---|---|
| `url` | Full Amazon product page URL (required) |
| `-o`, `--output DIR` | Base folder for output (default: `./images`) |
| `--dry-run` | Print extracted product JSON to stdout — no files written |
| `--mode MODE` | Fetch strategy: `auto` (default), `requests`, or `browser` |

### Examples

```bash
# Extract product details and download all images (auto mode: tries fast first)
uv run amazon-extract "https://www.amazon.com/dp/B07MHJFRBJ"

# Save to a custom folder
uv run amazon-extract "https://www.amazon.com/dp/B07MHJFRBJ" --output ./products

# Brazilian storefront
uv run amazon-extract "https://www.amazon.com.br/dp/B09JQMJHXY" --output ./products

# UK storefront
uv run amazon-extract "https://www.amazon.co.uk/dp/B085FYSG5K" --output ./products

# Preview extracted data without writing any files
uv run amazon-extract "https://www.amazon.com/dp/B08N5WRWNW" --dry-run

# Force fast requests-only mode (skips Playwright even if blocked)
uv run amazon-extract "https://www.amazon.com/dp/B08N5WRWNW" --mode requests

# Force browser-only mode (skips the fast attempt)
uv run amazon-extract "https://www.amazon.com/dp/B08N5WRWNW" --mode browser
```

## Output

For each product URL a subfolder named after the ASIN is created inside the output directory, keeping every product's files separate:

```
images/
  B08N5WRWNW/
    product.json
    01_61CBqERgZ7L.jpg
    02_71xhv...jpg
    ...
  B085FYSG5K/
    product.json
    01_61IJRqB4dlL.jpg
    ...
```

### product.json

```json
{
  "name": "Apple AirPods Pro (2nd Generation)",
  "price": "$249.00",
  "short_description": "Active Noise Cancellation reduces unwanted background noise...\nTransparency mode for hearing and connecting with the world around you...",
  "images": "{\"https://m.media-amazon.com/images/I/71bhWgQK-cL.jpg\":[679,679],...}",
  "variants": [
    { "name": "AirPods Pro", "asin": "B0BDHWDR12" }
  ],
  "product_description": "Rebuilt from the ground up, AirPods Pro...",
  "link_to_all_reviews": "https://www.amazon.com/product-reviews/B0BDHWDR12/..."
}
```

| Field | Description |
|---|---|
| `name` | Full product title |
| `price` | Displayed price, including currency symbol |
| `short_description` | Feature bullet points, newline-separated |
| `images` | Stringified JSON map of each image URL to its `[width, height]` |
| `variants` | Array of `{ name, asin }` for size/colour variants; `null` if none exist |
| `product_description` | Long-form product description text |
| `link_to_all_reviews` | Direct URL to the full customer reviews page |

The downloaded image files are the highest-resolution versions of the URLs listed in `images`.

## How it works

By default the tool uses an **auto strategy** — it tries the fast path first and only escalates to the slow path if needed:

| Step | Method | Speed | Reliability |
|---|---|---|---|
| **[1/2] Fast path** | Plain HTTP (`requests`) | ~1–2 s | Blocked on some networks |
| **[2/2] Slow path** | Headless Chromium (Playwright) | ~10–15 s | Bypasses bot detection |

If the fast path succeeds and returns a real product page, Playwright is never launched. If it's blocked (by status code, bot-block phrases, or returning a page with no product data), the browser fallback kicks in automatically.

You can override this with `--mode requests` (fast only) or `--mode browser` (browser only).

The browser fetch sequence:
1. Visits the Amazon homepage first to collect session cookies and detect any **geo-redirect** (e.g. `amazon.com` → `amazon.com.br` based on your IP). The product URL is automatically rewritten to the resolved regional domain.
2. Navigates to the product page, masks the automation fingerprint, and waits for the DOM to load.

Image URLs are pulled from three sources in order of preference:

1. The `colorImages` block embedded in inline scripts — the authoritative gallery including all colour variants.
2. The `imageGalleryData` array used by some product pages.
3. The `data-a-dynamic-image` attribute on `<img>` tags — final fallback.

Each image URL is normalized by stripping Amazon's size token (e.g. `._AC_SX466_`) so the largest available version is downloaded.

## Notes

- **"Page Not Found" errors** mean the ASIN is invalid, region-restricted, or the product has been removed — not a bot-block.
- **CAPTCHA errors** mean Amazon is blocking requests from your IP. Try again later or from a different network.
- Only the images for the default variant are downloaded. To extract a specific variant, pass its own product URL.
- This tool is intended for personal use (research, archiving your own purchases). Respect Amazon's Terms of Service.
