"""Read-only MXSB2 API test. Never prints credentials or writes catalog files."""
import re
from urllib.parse import parse_qs, unquote
import requests

BASE = 'https://www.luxe1927.com'
PRODUCT_URL = BASE + '/product/57005/mxsb2-sleeveless-metallic-maxi-dress-with-cape-draped-detail-ชมพู'
API_URL = BASE + '/_api/v4/product-web/v1/products/detailSku'

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/156.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Referer': BASE + '/',
})

# Warm up the public site so it can issue its own visitor session.
for url in (BASE + '/', PRODUCT_URL):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    if url == PRODUCT_URL:
        page = response.text

match = re.search(r'productAttributeSetID="(\d+)"', page)
if not match:
    raise RuntimeError('Attribute set ID not found in product HTML')
print('Attribute set:', match.group(1))

blocks = re.findall(
    r'product_attribute_name:"([^"]+)",product_attribute_order:[^,]+,product_attribute_member:\[(.*?)\]\}',
    page, re.S,
)
size_ids = None
for name, block in blocks:
    ids = re.findall(r'product_attribute_member_id:"(\d+)"', block)
    print('Attribute:', name, '| option IDs:', ', '.join(ids))
    if name.strip().upper() == 'SIZE':
        size_ids = ids
if not size_ids or len(size_ids) != 4:
    raise RuntimeError('Expected four MXSB2 sizes; inspect source format')

# svSession may contain a URL-encoded 'atk=<JWT>;rtk=<JWT>' pair.
# Only use a token freshly issued to this requests.Session; never hardcode it.
raw_cookie = session.cookies.get('svSession')
if raw_cookie:
    decoded = unquote(raw_cookie)
    token = parse_qs(decoded.replace(';', '&')).get('atk', [None])[0]
    if token:
        session.headers['Authorization'] = 'Bearer ' + token
        print('Visitor authorization: available (value hidden)')
    else:
        print('Visitor authorization: not found in session cookie')
else:
    print('Visitor authorization: no svSession cookie issued')

for size, size_id in zip(('S', 'M', 'L', 'XL'), size_ids):
    response = session.get(API_URL, params={
        'productID': '57005',
        'attributeSet': f'{size_id},5194',
        'language': 'th',
    }, timeout=25)
    print(size, 'HTTP:', response.status_code)
    if response.status_code == 401:
        raise RuntimeError('API requires authorization not obtained from public page; stop here')
    response.raise_for_status()
    product = response.json().get('product') or {}
    print(size, 'SKU:', product.get('productCode'),
          'variant:', product.get('productSkuID'), 'price:', product.get('price'))
    if not product.get('productSkuID') or not product.get('productCode'):
        raise RuntimeError(f'No validated variant for size {size}')
print('API verification completed successfully')
