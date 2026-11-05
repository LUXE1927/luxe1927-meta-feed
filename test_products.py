
import requests
from bs4 import BeautifulSoup

URL = (
    "https://www.luxe1927.com/product/54944/"
    "mxsx-draped-metallic-long-sleeve-maxi-dress-ชมพู"
)

SIZES = {"S", "M", "L", "XL", "XXL"}

response = requests.get(
    URL,
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=30,
)
response.raise_for_status()

soup = BeautifulSoup(response.text, "lxml")

print("LUXE1927 - Product Stock Test")
print("HTTP status:", response.status_code)
print("Page title:", soup.title.get_text(strip=True) if soup.title else "N/A")

buttons = soup.select(".text-select-button-2")
found = {}

for button in buttons:
    size = button.get_text(" ", strip=True).upper()

    if size not in SIZES:
        continue

    sold_out = "disabled" in button.get("class", [])

    found[size] = "out of stock" if sold_out else "in stock"

for size in ["S", "M", "L", "XL", "XXL"]:
    print(f"{size}: {found.get(size, 'NOT FOUND')}")

if not found:
    print("RESULT: Size buttons not found in HTML.")
    print("A browser-rendering method may be required.")
elif len(found) != len(SIZES):
    print("RESULT: Some sizes were not found. Needs investigation.")
else:
    print("RESULT: All five sizes found in HTML.")
