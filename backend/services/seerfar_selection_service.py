"""Ozon-specific request bodies and field mapping for Seerfar selection data."""
from datetime import datetime

from services.seerfar_client import SeerfarClient


def _range(value_min=None, value_max=None):
    return {'min': value_min, 'max': value_max}


class SeerfarSelectionService:
    def __init__(self, client=None):
        self.client = client or SeerfarClient()

    def quota(self):
        return self.client.request('GET', '/open-api/quota').get('data') or {}

    def _page(self, filters, default_order='revenue'):
        filters = filters or {}
        return {
            'pageNumber': int(filters.get('pageNumber') or 1),
            'pageSize': min(int(filters.get('pageSize') or 20), 100),
            'orders': filters.get('orders') or [
                {'field': filters.get('orderField') or default_order,
                 'direction': filters.get('orderDirection') or 'DESC'}
            ],
        }

    def _metric_filters(self, filters):
        filters = filters or {}
        fields = (
            'reviewCount', 'drr', 'reviewRating', 'questionsAndAnswers',
            'price', 'monthlyRevenue', 'monthlySales', 'monthlySalesRate',
            'weight', 'volume', 'grossMargin', 'variants', 'convToCartPdp',
            'returnCancellationRate',
        )
        payload = {field: filters.get(field, _range()) for field in fields}
        aliases = {
            'monthlyRevenueMin': ('monthlyRevenue', 'min'),
            'monthlyRevenueMax': ('monthlyRevenue', 'max'),
            'monthlySalesMin': ('monthlySales', 'min'),
            'monthlySalesMax': ('monthlySales', 'max'),
            'priceMin': ('price', 'min'),
            'priceMax': ('price', 'max'),
            'monthlySalesRateMin': ('monthlySalesRate', 'min'),
            'monthlySalesRateMax': ('monthlySalesRate', 'max'),
        }
        for key, (field, bound) in aliases.items():
            if key in filters:
                payload[field][bound] = filters[key]
        return payload

    def _product_search_body(self, filters):
        filters = filters or {}
        body = self._metric_filters(filters)
        body.update({
            'creationDate': filters.get('creationDate'),
            'categoryIds': filters.get('categoryIds'),
            'fulfillment': filters.get('fulfillment') or [],
            'skus': filters.get('skus') or [],
            'sellerName': filters.get('sellerName') or [],
            'brand': filters.get('brand') or {'type': 0, 'brandName': []},
            'labels': filters.get('labels') or [],
            'keywords': filters.get('keywords') or [],
            'variationsMerge': filters.get('variationsMerge', 0),
            'searchDate': filters.get('searchDate') or '',
            'filterRemoveProduct': filters.get('filterRemoveProduct', True),
            'tag': filters.get('tag') or '',
            'page': self._page(filters),
        })
        return body

    def _search(self, path, body):
        response = self.client.request('POST', path, body)
        data = response.get('data') or {}
        records = data.get('records') if isinstance(data, dict) else []
        return {
            'records': records if isinstance(records, list) else [],
            'total': data.get('total', 0) if isinstance(data, dict) else 0,
            'raw': response,
        }

    def search_rankings(self, filters=None):
        return self._search(
            '/open-api/productReport/search/ozon',
            self._product_search_body(filters),
        )

    def overview_category_shares(self, filters=None):
        """Build dashboard category shares from the Ozon product report.

        Seerfar's product report is the stable Ozon market endpoint available
        to the current test key.  We aggregate the returned market sample by
        category so the dashboard remains useful while preserving the raw
        total for the UI to disclose.
        """
        filters = dict(filters or {})
        filters.setdefault('pageNumber', 1)
        filters.setdefault('pageSize', 100)
        result = self.search_rankings(filters)
        groups = {}
        for record in result['records']:
            category_info = record.get('categoryInfo') or {}
            name = (record.get('categoryName') or record.get('category') or
                    record.get('categoryPath') or category_info.get('cnTitlePath') or
                    category_info.get('category', {}).get('cnTitle') or
                    category_info.get('titlePath') or '未分类')
            if isinstance(name, list):
                name = ' > '.join(str(item) for item in name)
            name = str(name)
            group = groups.setdefault(name, {'name': name, 'monthlySales': 0, 'monthlyRevenue': 0, 'records': 0})
            group['monthlySales'] += self._number(record, 'monthlySales', 'sales')
            group['monthlyRevenue'] += self._number(record, 'monthlyRevenue', 'revenue', 'salesAmount')
            group['records'] += 1
        categories = sorted(groups.values(), key=lambda item: item['monthlySales'], reverse=True)
        return {
            'categories': categories,
            'sampleSize': len(result['records']),
            'upstreamTotal': result.get('total', 0),
        }

    @staticmethod
    def _number(record, *keys):
        for key in keys:
            value = record.get(key)
            if isinstance(value, (int, float)):
                return value
            try:
                if value not in (None, ''):
                    return float(str(value).replace(',', '').replace('₽', '').replace('%', ''))
            except (TypeError, ValueError):
                continue
        return 0

    def search_market_keywords(self, filters=None):
        filters = filters or {}
        body = {
            field: filters.get(field, _range()) for field in (
                'price', 'monthlySales', 'monthlyRevenue', 'weight', 'volume',
                'reviews', 'ratings', 'searchVolume', 'searchChange30',
                'sellers', 'products', 'productViews', 'conversionSharing',
                'marketSpace',
            )
        }
        for alias, field, bound in (
            ('monthlyRevenueMin', 'monthlyRevenue', 'min'),
            ('monthlyRevenueMax', 'monthlyRevenue', 'max'),
            ('monthlySalesMin', 'monthlySales', 'min'),
            ('monthlySalesMax', 'monthlySales', 'max'),
            ('priceMin', 'price', 'min'),
            ('priceMax', 'price', 'max'),
        ):
            if alias in filters:
                body[field][bound] = filters[alias]
        body.update({
            'categories': filters.get('categories'),
            'keywords': filters.get('keywords') or [],
            'matchType': filters.get('matchType', 0),
            'searchDate': filters.get('searchDate') or '',
            'page': self._page(filters, 'searchVolume'),
        })
        return self._search('/open-api/market/search/ozon', body)

    def search_shop_detail(self, shop_id, filters=None):
        filters = filters or {}
        body = self._metric_filters(filters)
        body.update({
            'id': int(shop_id),
            'creationDate': filters.get('creationDate'),
            'categoryIds': filters.get('categoryIds'),
            'fulfillment': filters.get('fulfillment') or [],
            'skus': filters.get('skus') or [],
            'brand': filters.get('brand') or {'type': 0, 'brandName': []},
            'labels': filters.get('labels') or [],
            'keywords': filters.get('keywords') or [],
            'filterRemoveProduct': filters.get('filterRemoveProduct', True),
            'tag': filters.get('tag') or '',
            'page': {
                'pageNumber': int(filters.get('pageNumber') or 1),
                'pageSize': min(int(filters.get('pageSize') or 20), 100),
                'orders': [{'field': 'upTime', 'direction': 'DESC'}],
            },
        })
        response = self.client.request('POST', '/open-api/shop/detail/search/ozon', body)
        data = response.get('data') or {}
        if int(filters.get('pageNumber') or 1) == 1:
            category_body = {
                'id': int(shop_id),
                'date': None,
                'categoryIds': [],
                'avgPrice': {'min': None, 'max': None},
                'products': {'min': None, 'max': None},
                'monthlySales': {'min': None, 'max': None},
                'monthlySalesRate': {'min': None, 'max': None},
                'monthlyRevenue': {'min': None, 'max': None},
                'monthlyRevenueRate': {'min': None, 'max': None},
                'page': {'pageNumber': 1, 'pageSize': 100, 'orders': [{'field': 'totalRevenues', 'direction': 'DESC'}]},
            }
            category_response = self.client.request('POST', '/open-api/shop/detail/category/search/ozon', category_body)
            category_data = category_response.get('data') or {}
            data['categorySummary'] = category_data.get('categories') or {}
        return data

    def search_category_products(self, category_id, filters=None):
        category_id = str(category_id).strip()
        filters = dict(filters or {})
        # Seerfar category-detail IDs are composite paths (L1_L2_leaf),
        # while ERP's live Ozon tree exposes numeric local IDs.  A numeric
        # ERP ID cannot be sent to Seerfar's detail endpoint (it returns 500),
        # so use the stable Ozon product report as a compatible fallback.
        if '_' not in category_id:
            return self.search_rankings(filters)

        body = self._product_search_body(filters)
        # The category-detail contract expects one fulfillment string; the
        # product-report contract accepts an array.
        body['fulfillment'] = filters.get('fulfillment') or 'FBO'
        body.update({'categoryId': category_id, 'date': filters.get('date')})
        response = self.client.request('POST', '/open-api/category/detail/search/ozon', body)
        data = response.get('data') or {}
        records = data.get('records') if isinstance(data, dict) else None
        if not isinstance(records, list):
            records = data.get('productList') if isinstance(data, dict) else []
        return {
            'records': records if isinstance(records, list) else [],
            'total': data.get('total', data.get('products', 0)) if isinstance(data, dict) else 0,
            'raw': response,
        }

    def product_detail(self, sku, date_range='past_30_days'):
        response = self.client.request('POST', '/open-api/product/detail/search/ozon', {
            'sku': int(sku),
            'dateRange': date_range,
        })
        return response.get('data') or {}

    def similar_products(self, sku):
        response = self.client.request('POST', '/open-api/product/detail/similar/ozon', {
            'sku': int(sku),
        })
        return response.get('data') or {}

    @staticmethod
    def to_research_item(record, source_type='seerfar'):
        sku = record.get('sku') or record.get('ozonId') or record.get('id')
        title = record.get('title') or record.get('name') or f'Ozon 商品 {sku or ""}'.strip()
        return {
            'kind': 'reference_product',
            'product_name': title,
            'source_url': record.get('url') or (
                f'https://www.ozon.ru/product/{sku}/' if sku else ''
            ),
            'suggested_price': record.get('price') or record.get('avgPrice'),
            'monthly_revenue': record.get('monthlyRevenue'),
            'revenue_growth_rate': record.get('monthlySalesRate'),
            'seller_count': record.get('sellers'),
            'brand_count': record.get('brands') or record.get('brandCount'),
            'source_type': source_type,
            'source_id': str(sku) if sku is not None else '',
            'platform': 'ozon',
            'source_payload_json': record,
            'source_synced_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'mpstats_source': 'seerfar',
        }
