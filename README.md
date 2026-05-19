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
```

`uv sync` reads `pyproject.toml` and installs all dependencies into an isolated virtual environment automatically.

## Usage

```
uv run main.py <amazon-product-url> [--output DIR] [--dry-run]
```

### Arguments

| Argument | Description |
|---|---|
| `url` | Full Amazon product page URL (required) |
| `-o`, `--output DIR` | Base folder for output (default: `./images`) |
| `--dry-run` | Print extracted product JSON to stdout — no files written |

### Examples

```bash
# Extract product details and download images
uv run main.py "https://www.amazon.com/dp/B085383P7M"

# Save to a custom folder
uv run main.py "https://www.amazon.com/dp/B085383P7M" --output ./products

# UK storefront
uv run main.py "https://www.amazon.co.uk/dp/B085FYSG5K" --output ./products

# Preview extracted data without writing any files
uv run main.py "https://www.amazon.com/dp/B085383P7M" --dry-run
```

You can also run the installed entry point directly after `uv sync`:

```bash
uv run amazon-extract "https://www.amazon.com/dp/B085383P7M"
```

## Output

For each product URL a subfolder named after the ASIN is created inside the output directory, keeping every product's files separate:

```
images/
  B085383P7M/
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
  "name": "2020 HP 15.6\" Laptop Computer, 10th Gen Intel Quard-Core i7 1065G7 up to 3.9GHz, 16GB DDR4 RAM, 512GB PCIe SSD",
  "price": "$959.00",
  "short_description": "Powered by latest 10th Gen Intel Core i7-1065G7 Processor @ 1.30GHz...\n15.6\" diagonal HD SVA BrightView micro-edge WLED-backlit...",
  "images": "{\"https://images-na.ssl-images-amazon.com/images/I/61CBqERgZ7L._AC_SX425_.jpg\":[425,425],...}",
  "variants": [
    { "name": "Click to select 4GB DDR4 RAM, 128GB PCIe SSD", "asin": "B01MCZ4LH1" },
    { "name": "Click to select 16GB DDR4 RAM, 512GB PCIe SSD", "asin": "B085383P7M" }
  ],
  "product_description": "Capacity: 16GB DDR4 RAM, 512GB PCIe SSD\n\nProcessor\n  Intel Core i7-1065G7...",
  "link_to_all_reviews": "https://www.amazon.com/HP-Computer.../product-reviews/B085383P7M/..."
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

The script fetches the page HTML with a browser-like set of headers (matching Chrome on macOS, including `sec-fetch-*` fields and a matching `referer`) to pass Amazon's bot checks. Image URLs are then pulled from three sources in order of preference:

1. The `colorImages` block embedded in inline scripts — the authoritative gallery including all colour variants.
2. The `imageGalleryData` array used by some product pages.
3. The `data-a-dynamic-image` attribute on `<img>` tags — final fallback.

Each image URL is normalized by stripping Amazon's size token (e.g. `._AC_SX466_`) so the largest available version is downloaded.

## Notes

- Amazon actively blocks automated requests. If you see a bot-block error, wait a few minutes or try from a different network.
- Only the images for the default variant are downloaded. To extract a specific variant, pass its own product URL.
- This tool is intended for personal use (research, archiving your own purchases). Respect Amazon's Terms of Service.
