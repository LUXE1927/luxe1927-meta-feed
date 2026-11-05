
import csv
import json
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.luxe1927.com"
SITEMAP = BASE + "/sitemap.xml"
MAX_URLS = 30  # ทดสอบก่อน ยังไม่เปิดทั้งร้าน

SIZES = ("XXL", "XL", "L", "M", "S")

FIELDS = [
    "id", "item_group_id", "title", "description",
    "availability", "condition", "price", "link",
    "image_link", "brand", "size"
]

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; LUXE1927Feed/1.0)"
})


def sitemap_urls():
    response = session.get(SITEMAP, timeout=40)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    urls = []

    for node in root.iter():
        if node.tag.split("}")[-1] != "loc":
            continue

        url = (node.text or "").strip()
        parsed = urlparse(url)

        if (
            parsed.netloc in ("luxe1927.com", "www.luxe1927.com")
            and parsed.path.startswith("/product/")
        ):
            urls.append(url)

    return list(dict.fromkeys(urls))


def product_schema(soup):
    def walk(obj):
        if isinstance(obj, list):
            for value in obj:
                yield from walk(value)
        elif isinstance(obj, dict):
            types = obj.get("@type", [])
            if isinstance(types, str):
                types = [types]

            if "Product" in types:
                yield obj

            if "@graph" in obj:
                yield from walk(obj["@graph"])

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue

        for product in walk(data):
            return product

    return {}


def meta(soup, key, attr="property"):
    tag = soup.find("meta", attrs={attr: key})
    return tag.get("content", "").strip() if tag else ""


def normal_price(product):
    offers = product.get("offers", {})

    if isinstance(offers, list):
        offers = offers[0] if offers else {}

    if not isinstance(offers, dict):
        return None

    if offers.get("priceCurrency", "THB") != "THB":
        return None

    price = offers.get("price") or offers.get("highPrice")

    try:
        value = float(str(price).replace(",", ""))
        return f"{value:.2f} THB" if value > 0 else None
    except (ValueError, TypeError):
        return None



def selected_variant(soup):
    product = product_schema(soup)
    text = soup.get_text(" ", strip=True)

    match = re.search(
        r"\bSKU\s*:\s*([A-Za-z0-9_-]+)\s*(?:\|\s*)?"
        r"(XXL|XL|L|M|S)\s*,\s*"
        r"(เก็บเงินปลายทาง|เงินโอน|โอนเงิน|โอน)",
        text,
        flags=re.IGNORECASE
    )

    if not match:
        return None

    sku = match.group(1).upper()
    size = match.group(2).upper()
    payment = match.group(3)

    schema_sku = str(product.get("sku") or "").upper()

    if schema_sku != sku:
        raise ValueError("SKU mismatch between page and schema")

    return sku, size, payment

    text = soup.get_text(" ", strip=True)

    match = re.search(
        r"\bSKU\s*:\s*([A-Za-z0-9_-]+)\s*\|\s*"
        r"(XXL|XL|L|M|S)\s*,\s*"
        r"(เก็บเงินปลายทาง|เงินโอน|โอนเงิน|โอน)",
        text,
        flags=re.IGNORECASE
    )

    if not match:
        return None

    sku = match.group(1).upper()
    size = match.group(2).upper()
    payment = match.group(3)

    return sku, size, payment


def size_availability(soup, size):
    buttons = [
        button
        for button in soup.select(".text-select-button-2")
        if button.get_text(" ", strip=True).upper() == size
    ]

    # Avoid guessing if size buttons are missing or duplicated.
    if len(buttons) != 1:
        return None

    button = buttons[0]
    classes = button.get("class", [])

    disabled = (
        "disabled" in classes
        or button.has_attr("disabled")
        or button.get("aria-disabled") == "true"
    )

    return "out of stock" if disabled else "in stock"


def description(soup, product, title):
    values = [
        meta(soup, "description", "name"),
        meta(soup, "og:description"),
        product.get("description", "")
    ]

    for value in values:
        if not isinstance(value, str):
            continue

        clean = BeautifulSoup(
            value, "html.parser"
        ).get_text(" ", strip=True)

        clean = re.sub(r"\s+", " ", clean)

        if len(clean) < 25:
            continue

        if re.fullmatch(
            r"(โอน|เงินโอน|ลด|ส่วนลด|COD|[\d\s.,฿-])+",
            clean,
            flags=re.IGNORECASE
        ):
            continue

        return clean[:5000]

    return title


def read_variant(url):
    response = session.get(url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    product = product_schema(soup)

    if not product:
        raise ValueError("Product schema missing")

    variant = selected_variant(soup)

    if not variant:
        raise ValueError("Cannot identify selected SKU/size/payment")

    sku, size, payment = variant

    # Only use the regular COD price.
    if payment != "เก็บเงินปลายทาง":
        return None, "transfer option ignored"

    match = re.search(r"/product/(\d+)(?:-\d+)?", url)

    if not match:
        raise ValueError("Parent product ID missing")

    parent_id = match.group(1)

    title = str(product.get("name") or "").strip()
    if not title:
        title = meta(soup, "og:title")

    image = product.get("image")

    if isinstance(image, list):
        image = image[0] if image else ""

    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")

    if not isinstance(image, str) or not image:
        image = meta(soup, "og:image")

    image = urljoin(url, image) if image else ""

    price = normal_price(product)
    availability = size_availability(soup, size)

    if not title or not image or not price:
        raise ValueError("Missing title, image or price")

    if availability is None:
        raise ValueError("Cannot confirm size availability")

    row = {
        "id": f"LUXE-{parent_id}-{size}",
        "item_group_id": f"LUXE-{parent_id}",
        "title": f"{title} - {size}",
        "description": description(soup, product, title),
        "availability": availability,
        "condition": "new",
        "price": price,
        "link": url,
        "image_link": image,
        "brand": "LUXE",
        "size": size
    }

    return row, f"SKU={sku}"


def main():
    urls = sitemap_urls()
    print(f"URLs in sitemap: {len(urls)}")

    rows = {}
    problems = []
    ignored = 0

    for index, url in enumerate(urls[:MAX_URLS], 1):
        try:
            row, note = read_variant(url)

            if row is None:
                ignored += 1
                print(f"[{index}] IGNORED: {note}")
                continue

            item_id = row["id"]

            if item_id in rows:
                if rows[item_id] != row:
                    problems.append(
                        (url, f"Conflicting duplicate {item_id}")
                    )
                continue

            rows[item_id] = row
            print(
                f"[{index}] OK: {item_id} "
                f"{row['price']} {row['availability']} {note}"
            )

        except Exception as exc:
            problems.append((url, str(exc)))
            print(f"[{index}] REVIEW: {exc}")

        finally:
            time.sleep(0.3)

    result = list(rows.values())

    print(f"Unique items: {len(result)}")
    print(f"Transfer options ignored: {ignored}")
    print(f"URLs needing review: {len(problems)}")

    for url, issue in problems[:20]:
        print(f"REVIEW: {url} | {issue}")

    if not result:
        raise RuntimeError(
            "No validated products. CSV was not created."
        )

    if problems:
        raise RuntimeError(
            "Some variants need review. "
            "Feed withheld to prevent incorrect catalog data."
        )

    counts = Counter(row["item_group_id"] for row in result)
    print(f"Product groups: {len(counts)}")

    with open(
        "products.csv", "w",
        newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(result)

    print("Created products.csv successfully")



if __name__ == "__main__":
    test_url = (
        "https://www.luxe1927.com/product/49087-60434/"
        "mutapbk-noir-silk-majesty-maxi-dress-ดำ"
    )

    response = session.get(test_url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    print("=== HTML SKU DIAGNOSTIC ===")
    print("HTTP status:", response.status_code)
    print("HTML length:", len(response.text))

    text = soup.get_text(" ", strip=True)

    for keyword in [
        "SKU", "MUTAPBKCM",
        "เก็บเงินปลายทาง", "เงินโอน"
    ]:
        print(f"\nKEYWORD: {keyword}")
        matches = list(re.finditer(
            re.escape(keyword), text, re.IGNORECASE
        ))

        print("Occurrences:", len(matches))

        for match in matches[:3]:
            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 150)
            print(repr(text[start:end]))

    print("\n=== SIZE BUTTONS ===")

    for button in soup.select(".text-select-button-2"):
        print({
            "text": button.get_text(" ", strip=True),
            "classes": button.get("class", []),
            "disabled": button.has_attr("disabled")
        })

    print("\n=== SCHEMA SKU ===")

    product = product_schema(soup)
    print("sku:", product.get("sku"))
    print("name:", product.get("name"))
    print("offers:", json.dumps(
        product.get("offers", {}),
        ensure_ascii=False
    )[:1500])

    print("\nDIAGNOSTIC COMPLETE")

if __name__ == "__main__":
    main()
