
import csv
import json
import re
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE = "https://www.luxe1927.com"
SITEMAP = BASE + "/sitemap.xml"
MAX_URLS = 0  # Diagnostic batch; set to 0 only after a successful test

SIZES = ("XXL", "XL", "L", "M", "S", "FREE SIZE")

FIELDS = [
    "id",
    "item_group_id",
    "title",
    "description",
    "availability",
    "condition",
    "price",
    "link",
    "image_link",
    "brand",
    "size",
]

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; LUXE1927Catalog/1.0)",
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
})

# Retry transient server errors, but never retry a genuine 404.
retry = Retry(
    total=3,
    backoff_factor=1.5,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET"]),
    respect_retry_after_header=True,
)
session.mount("https://", HTTPAdapter(max_retries=retry))
session.mount("http://", HTTPAdapter(max_retries=retry))


def fetch(url):
    response = session.get(url, timeout=35)
    response.raise_for_status()
    return response


def product_urls():
    response = fetch(SITEMAP)
    root = ET.fromstring(response.content)

    urls = []

    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "loc":
            continue

        url = (node.text or "").strip()

        if urlparse(url).netloc not in (
            "www.luxe1927.com",
            "luxe1927.com",
        ):
            continue

        if "/product/" in url:
            urls.append(url)
    # Discover product links from category pages
    category_queue = [BASE + "/category"]
    visited_categories = set()

    while category_queue and len(visited_categories) < 100:
        category_url = category_queue.pop(0)
        if category_url in visited_categories:
            continue
        visited_categories.add(category_url)

        try:
            page = fetch(category_url)
            soup = BeautifulSoup(page.text, "html.parser")
        except requests.RequestException as exc:
            print(f"Category fetch failed: {category_url}: {exc}", flush=True)
            continue

        for link in soup.select("a[href]"):
            href = urljoin(category_url, link.get("href", ""))
            parsed = urlparse(href)

            if parsed.netloc not in ("www.luxe1927.com", "luxe1927.com"):
                continue

            if parsed.path.startswith("/product/"):
                urls.append(href.split("#")[0].split("?")[0])
            elif parsed.path.startswith("/category"):
                if href not in visited_categories and href not in category_queue:
                    category_queue.append(href)
                # Check whether MXSB2 is discovered
    mxsb2_urls = [u for u in urls if "/product/57005" in u]
    print(f"MXSB2 URLs found: {len(mxsb2_urls)}", flush=True)
    for u in mxsb2_urls:
        print(f"MXSB2 URL: {u}", flush=True)
    urls = list(dict.fromkeys(urls))
    from collections import defaultdict

product_urls_by_id = defaultdict(list)

for url in urls:
    match = re.search(r"/product/(\d+)(?:-(\d+))?/", url)
    if match:
        product_urls_by_id[match.group(1)].append(url)

for product_id, links in product_urls_by_id.items():
    main_links = [
        u for u in links
        if re.search(rf"/product/{product_id}/", u)
    ]
    variant_links = [
        u for u in links
        if re.search(rf"/product/{product_id}-\d+/", u)
    ]
    if main_links and not variant_links:
        print(
            f"NEEDS VARIANT DISCOVERY: product {product_id}",
            flush=True
        )
    print(f"URLs in sitemap: {len(urls)}", flush=True)

    if not urls:
        raise RuntimeError("No product URLs found in sitemap")

    return urls[:MAX_URLS] if MAX_URLS else urls


def product_schema(soup):
    def search_object(obj):
        if isinstance(obj, list):
            for item in obj:
                found = search_object(item)
                if found:
                    return found

        if isinstance(obj, dict):
            kind = obj.get("@type", "")
            kinds = kind if isinstance(kind, list) else [kind]

            if "Product" in kinds:
                return obj

            for value in obj.values():
                if isinstance(value, (dict, list)):
                    found = search_object(value)
                    if found:
                        return found

        return None

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):
        try:
            data = json.loads(
                script.string or script.get_text()
            )
        except (ValueError, TypeError):
            continue

        found = search_object(data)
        if found:
            return found

    return {}


def clean_text(value):
    if isinstance(value, dict):
        value = value.get("@value") or ""

    if isinstance(value, list):
        value = " ".join(map(str, value))

    return re.sub(
        r"\s+", " ", str(value or "")
    ).strip()


def valid_description(value, title):
    value = clean_text(value)

    if not value:
        return False

    if len(value) < 30:
        return False

    if value.strip().lower() == title.strip().lower():
        return False

    promotional_only = [
        r"^(?:โอน|เงินโอน|โอนเงิน)\s*ลด\s*[\d,.]+\s*[.-]*$",
        r"^เก็บเงินปลายทาง$",
    ]

    for pattern in promotional_only:
        if re.fullmatch(pattern, value, re.IGNORECASE):
            return False

    return True


def get_description(product, soup, title):
    candidates = []

    # First choice: structured product description
    candidates.append(product.get("description"))

    # Second choice: social sharing description
    for selector in (
        'meta[property="og:description"]',
        'meta[name="description"]',
        'meta[name="twitter:description"]',
    ):
        tag = soup.select_one(selector)

        if tag:
            candidates.append(tag.get("content"))

    # Third choice: visible product-detail sections
    for selector in (
        '[itemprop="description"]',
        '.product-description',
        '.product-detail-description',
        '.product-details-description',
        '.product-description-content',
        '.tab-description',
    ):
        for element in soup.select(selector)[:2]:
            candidates.append(
                element.get_text(" ", strip=True)
            )

    for candidate in candidates:
        value = clean_text(candidate)

        if valid_description(value, title):
            return value[:5000]

    # Safe fallback: descriptive product name, not payment text
    return f"{title} | ชุดเดรสแฟชั่นผู้หญิง แบรนด์ LUXE"


def get_image(product, soup):
    image = product.get("image")

    if isinstance(image, list):
        image = image[0] if image else None

    if isinstance(image, dict):
        image = (
            image.get("url")
            or image.get("contentUrl")
        )

    if not image:
        tag = soup.find(
            "meta", property="og:image"
        )
        if tag:
            image = tag.get("content")

    if not image or not isinstance(image, str):
        raise ValueError("Missing product image")

    return urljoin(BASE, image)


def get_price(product):
    offers = product.get("offers", {})

    if isinstance(offers, list):
        offers = offers[0] if offers else {}

    if not isinstance(offers, dict):
        offers = {}

    price = offers.get("price")

    if price is None:
        raise ValueError("Missing product price")

    currency = offers.get(
        "priceCurrency", "THB"
    )

    if currency != "THB":
        raise ValueError(
            f"Unexpected currency: {currency}"
        )

    value = float(
        str(price).replace(",", "")
    )

    if value <= 0:
        raise ValueError("Invalid product price")

    return f"{value:.2f} THB"


def selected_variant(soup, product):
    """Identify a variant without guessing when page data is ambiguous."""
    sku = clean_text(product.get("sku")).upper()
    text = soup.get_text(" ", strip=True)
    if not sku:
        match = re.search(r"\bSKU\s*[:：]\s*([A-Za-z0-9_-]+)", text, re.I)
        if match:
            sku = match.group(1).upper()
    if not sku:
        raise ValueError("Cannot identify SKU in schema or visible page")

    # The page may have extra whitespace/markup between the SKU and options.
    match = re.search(r"\bSKU\s*[:：]\s*" + re.escape(sku) + r"\b", text, re.I)
    if not match:
        raise ValueError(f"SKU exists in schema but is not visible beside options: {sku}")

    # Use only the local SKU option text. Never select a payment option
    # from a different product/variant elsewhere on the page.
    segment = text[match.end():match.end() + 250]
    payment_match = re.search(r"เก็บเงินปลายทาง|เงินโอน|โอนเงิน|โอนลด|โอน", segment)
    if not payment_match:
        raise ValueError(f"Payment cannot be confirmed near SKU {sku}: {segment[:110]!r}")
    payment = payment_match.group(0)

    before_payment = segment[:payment_match.start()]
    size_match = re.search(
        r"(?<![A-Za-z0-9])(?:XXL|XL|L|M|S|FREE\s*SIZE|FREESIZE)(?![A-Za-z0-9])",
        before_payment, re.I,
    )
    size_buttons = [
        b.get_text(" ", strip=True).upper()
        for b in soup.select(".text-select-button-2")
        if b.get_text(" ", strip=True).upper() in ("XXL", "XL", "L", "M", "S", "FREE SIZE", "F")
    ]
    if size_match:
        size = size_match.group(0).upper().replace(" ", "")
        if size == "FREESIZE":
            size = "FREE SIZE"
        if size_buttons and size not in size_buttons:
            raise ValueError(f"SKU size {size} not in page size options: {sku}")
    elif not size_buttons:
        size = "FREE SIZE"
    else:
        raise ValueError(f"Cannot identify selected size for SKU {sku}: {before_payment[:90]!r}")
    return sku, size, payment


def diagnostic(soup, product, url):
    """Log small, non-sensitive snippets to reveal the site's real HTML layout."""
    text = soup.get_text(" ", strip=True)
    matches = list(re.finditer(r"\bSKU\b", text, re.I))
    print(f"DIAGNOSTIC URL: {url}", flush=True)
    print(f"DIAGNOSTIC schema SKU: {product.get('sku')!r}", flush=True)
    print(f"DIAGNOSTIC SKU occurrences: {len(matches)}", flush=True)
    for match in matches[:2]:
        print(f"DIAGNOSTIC SKU context: {text[max(0, match.start()-60):match.end()+180]!r}", flush=True)
    print("DIAGNOSTIC size buttons:", [
        (b.get_text(" ", strip=True), b.get("class", []))
        for b in soup.select(".text-select-button-2")[:8]
    ], flush=True)


def size_availability(soup, size, product=None):
    if size == "FREE SIZE":
        # Free-size items have no size button; use the selected offer's stock state.
        offers = (product or {}).get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if not isinstance(offers, dict):
            offers = {}
        status = str(offers.get("availability") or "").lower()
        if "outofstock" in status or "soldout" in status:
            return "out of stock"
        if "instock" in status or "limitedavailability" in status:
            return "in stock"
        raise ValueError("Free Size: stock status not available in product schema")

    buttons = [
        button for button in soup.select(".text-select-button-2")
        if button.get_text(" ", strip=True).upper() == size
    ]
    if len(buttons) != 1:
        raise ValueError(f"Expected one size button for {size}, found {len(buttons)}")
    button = buttons[0]
    classes = button.get("class", [])
    unavailable = (
        "disabled" in classes
        or button.has_attr("disabled")
        or button.get("aria-disabled") == "true"
    )
    return "out of stock" if unavailable else "in stock"


def parent_id(url):
    match = re.search(
        r"/product/(\d+)(?:-\d+)?(?:/|$)",
        urlparse(url).path,
    )

    if not match:
        raise ValueError(
            "Cannot identify parent product ID"
        )

    return match.group(1)


def build_row(url, soup, product, sku, size):
    group = parent_id(url)

    name = clean_text(
        product.get("name")
    )

    if not name:
        raise ValueError(
            "Missing product name"
        )

    description = get_description(
        product, soup, name
    )

    image = get_image(
        product, soup
    )

    price = get_price(product)

    availability = size_availability(
        soup, size, product
    )

    return {
        "id": f"LUXE-{group}-{size}",
        "item_group_id": f"LUXE-{group}",
        "title": name,
        "description": description,
        "availability": availability,
        "condition": "new",
        "price": price,
        "link": url,
        "image_link": image,
        "brand": "LUXE",
        "size": size,
    }


def main():
    urls = product_urls()

    rows = {}
    problems = []
    ignored = 0
    skipped_404 = []
    fallback_descriptions = 0
    diagnostics_printed = 0

    for index, url in enumerate(urls, 1):
        soup = None
        product = None
        try:
            response = fetch(url)

            soup = BeautifulSoup(
                response.text, "lxml"
            )

            product = product_schema(soup)

            if not product:
                raise ValueError(
                    "Product schema not found"
                )

            sku, size, payment = selected_variant(
                soup, product
            )

            if payment != "เก็บเงินปลายทาง":
                ignored += 1
                print(
                    f"[{index}] IGNORED: transfer option",
                    flush=True,
                )
                continue

            row = build_row(
                url, soup, product, sku, size
            )

            item_id = row["id"]

            if item_id in rows:
                old = rows[item_id]

                if (
                    old["price"] != row["price"]
                    or old["availability"] != row["availability"]
                ):
                    raise ValueError(
                        f"Conflicting duplicate ID: {item_id}"
                    )

                print(
                    f"[{index}] DUPLICATE: {item_id}",
                    flush=True,
                )
                continue

            rows[item_id] = row

            if row["description"].endswith(
                "ชุดเดรสแฟชั่นผู้หญิง แบรนด์ LUXE"
            ):
                fallback_descriptions += 1

            print(
                f"[{index}] OK: {item_id} "
                f"{row['price']} "
                f"{row['availability']} "
                f"SKU={sku}",
                flush=True,
            )

        except requests.exceptions.HTTPError as exc:
            if (
                exc.response is not None
                and exc.response.status_code == 404
            ):
                skipped_404.append(url)

                print(
                    f"[{index}] SKIPPED 404: {url}",
                    flush=True,
                )
            else:
                problems.append((url, str(exc)))

                print(
                    f"[{index}] REVIEW: {exc}",
                    flush=True,
                )

        except Exception as exc:
            if diagnostics_printed < 3 and soup is not None and isinstance(product, dict):
                diagnostic(soup, product, url)
                diagnostics_printed += 1
            problems.append((url, str(exc)))

            print(
                f"[{index}] REVIEW: {exc}",
                flush=True,
            )

        finally:
            time.sleep(0.4)

    result = list(rows.values())

    print(f"Unique items: {len(result)}")
    print(f"Transfer options ignored: {ignored}")
    print(f"Skipped 404 URLs: {len(skipped_404)}")
    print(f"URLs needing review: {len(problems)}")
    print(
        f"Fallback descriptions: "
        f"{fallback_descriptions}"
    )

    for url in skipped_404:
        print(f"SKIPPED 404: {url}")

    for url, issue in problems:
        print(f"REVIEW: {url} | {issue}")

    if not result:
        raise RuntimeError(
            "No validated products found"
        )

    if problems:
        raise RuntimeError(
            "Some variants need review. "
            "Feed withheld to prevent incorrect catalog data."
        )

    groups = Counter(
        row["item_group_id"]
        for row in result
    )

    print(
        f"Product groups: {len(groups)}"
    )

    with open(
        "products.csv",
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=FIELDS,
        )

        writer.writeheader()
        writer.writerows(result)

    print(
        f"SUCCESS: products.csv created "
        f"with {len(result)} rows."
    )


if __name__ == "__main__":
    main()
