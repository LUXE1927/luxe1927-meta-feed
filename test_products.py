
import requests
from bs4 import BeautifulSoup

url = "https://www.luxe1927.com/product/54944/mxsx-draped-metallic-long-sleeve-maxi-dress-%E0%B8%8A%E0%B8%A1%E0%B8%9E%E0%B8%B9"

response = requests.get(url, timeout=30)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

print("=== MXSX PINK STOCK TEST ===")
print("HTTP:", response.status_code)

sizes = {"S", "M", "L", "XL", "XXL"}

for button in soup.select(".text-select-button-2"):
    size = button.get_text(" ", strip=True).upper()

    if size not in sizes:
        continue

    classes = button.get("class", [])
    disabled = (
        "disabled" in classes
        or button.has_attr("disabled")
    )

    print(
        f"SIZE {size}: "
        f"{'OUT OF STOCK' if disabled else 'IN STOCK'} "
        f"| classes={classes}"
    )

print("=== TEST COMPLETE ===")
