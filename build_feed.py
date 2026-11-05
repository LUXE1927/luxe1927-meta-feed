
import json
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = (
    "https://www.luxe1927.com/product/54944/"
    "mxsx-draped-metallic-long-sleeve-maxi-dress-ชมพู"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LUXE1927Feed/1.0)"
}

def main():
    response = requests.get(URL, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()

    og_image = soup.find("meta", property="og:image")
    image = ""
    if og_image and og_image.get("content"):
        image = urljoin(URL, og_image["content"])

    # Read visible size buttons and their disabled state
    sizes = {}
    for element in soup.select(".text-select-button-2"):
        size = element.get_text(" ", strip=True).upper()
        if size in {"S", "M", "L", "XL", "XXL"}:
            sizes[size] = (
                "out of stock"
                if "disabled" in element.get("class", [])
                else "in stock"
            )

    # Collect structured product data, if available
    structured_products = []

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue

        entries = data if isinstance(data, list) else [data]

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            candidates = entry.get("@graph", [entry])
            if not isinstance(candidates, list):
                candidates = [entry]

            for item in candidates:
                if not isinstance(item, dict):
                    continue
                types = item.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if "Product" in types:
                    structured_products.append(item)

    result = {
        "url": URL,
        "http_status": response.status_code,
        "title": title,
        "image_url": image,
        "sizes": sizes,
        "structured_products": structured_products,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not title or not image or len(sizes) != 5:
        raise RuntimeError(
            "Product data incomplete: check title, image and sizes."
        )

if __name__ == "__main__":
    main()
