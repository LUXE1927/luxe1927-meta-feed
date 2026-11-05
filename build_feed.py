
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
MAX_PRODUCTS = 30  # ทดสอบ 30 รายการก่อน
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


def find_product_schema(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue

        def walk(value):
            if isinstance(value, list):
                for part in value:
                    yield from walk(part)
            elif isinstance(value, dict):
                types = value.get("@type", [])
                if isinstance(types, str):
                    types = [types]

                if "Product" in types:
                    yield value

                if "@graph" in value:
                    yield from walk(value["@graph"])

        for product in walk(data):
            return product

    return {}


def get_price(product):
    offers = product.get("offers", {})
    if isinstance(offers, list):
        offers = offers[0] if offers else {}

    if not isinstance(offers, dict):
        return None

    currency = offers.get("priceCurrency", "THB")
    if currency != "THB":
        return None

    # Use the highest displayed price in a price range.
    # Do not advertise a conditional bank-transfer discount
    # as the unconditional product price.
    price = offers.get("highPrice") or offers.get("price")

    try:
        price = float(str(price).replace(",", ""))
        return f"{price:.2f} THB" if price > 0 else None
    except (ValueError, TypeError):
        return None


def get_image(product, soup, url):
    image = product.get("image")

    if isinstance(image, list):
        image = image[0] if image else None

    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")

    if not image:
        meta = soup.find("meta", property="og:image")
        image = meta.get("content") if meta else None

    return urljoin(url, image) if isinstance(image, str) and image else ""


def get_sizes(soup):
    result = {}

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

        result[size] = (
            "out of stock" if disabled else "in stock"
        )

    return result


def read_product(url):
    response = session.get(url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    product = find_product_schema(soup)

    if not product:
        raise ValueError("Product schema not found")

    title = product.get("name", "").strip()
    image = get_image(product, soup, url)
    price = get_price(product)
    sizes = get_sizes(soup)

    description = BeautifulSoup(
        product.get("description") or title,
        "html.parser"
    ).get_text(" ", strip=True)

    match = re.search(r"/product/(\d+)", url)
    if not match:
        raise ValueError("Product ID missing")

    product_id = match.group(1)

    if not title or not image or not price or not sizes:
        raise ValueError("Incomplete product data")

    rows = []

    for size, availability in sizes.items():
        rows.append({
            "id": f"LUXE-{product_id}-{size}",
            "item_group_id": f"LUXE-{product_id}",
            "title": f"{title} - {size}",
            "description": description[:5000],
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

    rows = []
    errors = []

    for index, url in enumerate(urls[:MAX_PRODUCTS], 1):
        try:
            product_rows = read_product(url)
            rows.extend(product_rows)
            print(f"[{index}] OK: {url} ({len(product_rows)} sizes)")
        except Exception as exc:
            errors.append((url, str(exc)))
            print(f"[{index}] SKIPPED: {url} - {exc}")

        time.sleep(0.3)

    if not rows:
        raise RuntimeError("No valid products. Feed not created.")

    with open("products.csv", "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"CSV rows: {len(rows)}")
    print(f"Skipped products: {len(errors)}")
    print("Created products.csv")

    for url, error in errors[:10]:
        print(f"REVIEW: {url} - {error}")


if __name__ == "__main__":
    main()
