
import csv
import json
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.luxe1927.com"
SITEMAP = BASE + "/sitemap.xml"
MAX_PRODUCTS = 30
SIZES = {"S", "M", "L", "XL", "XXL"}

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; LUXE1927Feed/1.0)"
})

FIELDS = [
    "id", "item_group_id", "title", "description",
    "availability", "condition", "price", "link",
    "image_link", "brand", "size"
]


def get_sitemap_urls():
    response = session.get(SITEMAP, timeout=40)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    urls = []

    for element in root.iter():
        if element.tag.split("}")[-1] != "loc":
            continue

        url = (element.text or "").strip()
        parsed = urlparse(url)

        if (
            parsed.netloc in {"luxe1927.com", "www.luxe1927.com"}
            and parsed.path.startswith("/product/")
        ):
            urls.append(url)

    return list(dict.fromkeys(urls))


def get_schema(soup):
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
            for product in walk(data):
                return product
        except (ValueError, TypeError):
            continue

    return {}


def get_meta(soup, key, attribute="property"):
    element = soup.find("meta", attrs={attribute: key})
    return element.get("content", "").strip() if element else ""


def get_price(product):
    offers = product.get("offers", {})

    if isinstance(offers, list):
        offers = offers[0] if offers else {}

    if not isinstance(offers, dict):
        return None

    if offers.get("priceCurrency", "THB") != "THB":
        return None

    price = offers.get("highPrice") or offers.get("price")

    try:
        value = float(str(price).replace(",", ""))
        return f"{value:.2f} THB" if value > 0 else None
    except (ValueError, TypeError):
        return None


def get_description(soup, product, title):
    candidates = [
        get_meta(soup, "description", "name"),
        get_meta(soup, "og:description"),
        product.get("description", "")
    ]

    for candidate in candidates:
        if not isinstance(candidate, str):
            continue

        description = BeautifulSoup(
            candidate, "html.parser"
        ).get_text(" ", strip=True)

        description = re.sub(r"\s+", " ", description)

        if not description:
            continue

        if re.fullmatch(
            r"(โอน|เก็บเงินปลายทาง|ลด|ส่วนลด|COD|[\d\s.,฿-])+",
            description,
            flags=re.IGNORECASE
        ):
            continue

        if len(description) >= 25:
            return description[:5000]

    return title


def get_sizes(soup):
    sizes = {}

    for element in soup.select(".text-select-button-2"):
        size = element.get_text(" ", strip=True).upper()

        if size not in SIZES:
            continue

        classes = element.get("class", [])
        disabled = (
            "disabled" in classes
            or element.has_attr("disabled")
            or element.get("aria-disabled") == "true"
        )

        sizes[size] = (
            "out of stock" if disabled else "in stock"
        )

    return sizes


def read_product(url):
    response = session.get(url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    product = get_schema(soup)

    if not product:
        raise ValueError("Product schema missing")

    title = str(product.get("name") or "").strip()
    if not title:
        title = get_meta(soup, "og:title")

    image = product.get("image")

    if isinstance(image, list):
        image = image[0] if image else ""

    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")

    if not isinstance(image, str) or not image:
        image = get_meta(soup, "og:image")

    image = urljoin(url, image) if image else ""

    price = get_price(product)
    sizes = get_sizes(soup)

    # Keep both numeric components of MakeWebEasy IDs.
    match = re.search(r"/product/(\d+(?:-\d+)?)", url)

    if not match:
        raise ValueError("Product ID missing")

    product_id = match.group(1)

    if not title or not image or not price or not sizes:
        raise ValueError("Incomplete product data")

    description = get_description(soup, product, title)

    rows = []

    for size, availability in sizes.items():
        rows.append({
            "id": f"LUXE-{product_id}-{size}",
            "item_group_id": f"LUXE-{product_id}",
            "title": f"{title} - {size}",
            "description": description,
            "availability": availability,
            "condition": "new",
            "price": price,
            "link": url,
            "image_link": image,
            "brand": "LUXE",
            "size": size
        })

    return rows


def main():
    urls = get_sitemap_urls()
    print(f"Product URLs in sitemap: {len(urls)}")

    rows_by_id = {}
    skipped = []

    for index, url in enumerate(urls[:MAX_PRODUCTS], 1):
        try:
            product_rows = read_product(url)

            for row in product_rows:
                # Never output duplicate IDs.
                if row["id"] in rows_by_id:
                    previous = rows_by_id[row["id"]]

                    if previous != row:
                        raise ValueError(
                            f"Conflicting duplicate ID: {row['id']}"
                        )
                else:
                    rows_by_id[row["id"]] = row

            print(
                f"[{index}] OK: {url} "
                f"({len(product_rows)} sizes)"
            )

        except Exception as exc:
            skipped.append((url, str(exc)))
            print(f"[{index}] SKIPPED: {url} - {exc}")

        time.sleep(0.3)

    rows = list(rows_by_id.values())

    if not rows:
        raise RuntimeError("No valid products found")

    with open(
        "products.csv", "w",
        newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Unique CSV rows: {len(rows)}")
    print(f"Unique groups: {len(set(r['item_group_id'] for r in rows))}")
    print(f"Skipped URLs: {len(skipped)}")
    print("Created products.csv")

    for url, error in skipped[:15]:
        print(f"REVIEW: {url} - {error}")


if __name__ == "__main__":
    main()
