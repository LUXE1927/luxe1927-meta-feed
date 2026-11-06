"""Fast, read-only API verification for MXSB2. No catalog writes."""
import re
import requests
from bs4 import BeautifulSoup

BASE = 'https://www.luxe1927.com'
URL = BASE + '/product/57005/mxsb2-sleeveless-metallic-maxi-dress-with-cape-draped-detail-ชมพู'
s = requests.Session()
s.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json, text/plain, */*'})
html = s.get(URL, timeout=30)
html.raise_for_status()
page = html.text
set_id = re.search(r'productAttributeSetID="(\d+)"', page)
if not set_id:
    raise RuntimeError('Attribute set ID not found in product HTML')
print('Attribute set:', set_id.group(1))
blocks = re.findall(r'product_attribute_name:"([^"]+)",product_attribute_order:[^,]+,product_attribute_member:\[(.*?)\]\}', page, re.S)
for name, block in blocks:
    ids = re.findall(r'product_attribute_member_id:"(\d+)"', block)
    print('Attribute:', name, '| option IDs:', ', '.join(ids))
    if name.strip().upper() == 'SIZE':
        size_ids = ids
if 'size_ids' not in locals() or len(size_ids) != 4:
    raise RuntimeError('Expected four MXSB2 sizes; inspect source format')
# The payment member ID is established from the user's actual Network response.
for size, size_id in zip(('S','M','L','XL'), size_ids):
    r = s.get(BASE + '/_api/v4/product-web/v1/products/detailSku', params={
        'productID':'57005', 'attributeSet':f'{size_id},5194', 'language':'th'
    }, timeout=25)
    r.raise_for_status()
    obj = r.json().get('product') or {}
    print(size, 'SKU:', obj.get('productCode'), 'variant:', obj.get('productSkuID'), 'price:', obj.get('price'))
    if not obj.get('productSkuID') or not obj.get('productCode'):
        raise RuntimeError(f'No validated variant for size {size}')
print('API verification completed successfully')
