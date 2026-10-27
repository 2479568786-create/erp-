import os
import sys
import unittest


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


class FakeClient:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or {
            'code': 200, 'msg': 'SUCCESS',
            'data': {'records': [], 'total': 0},
        }

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        return self.response


class SeerfarSelectionServiceTests(unittest.TestCase):
    def test_rankings_uses_ozon_report_endpoint_and_pagination(self):
        from services.seerfar_selection_service import SeerfarSelectionService

        client = FakeClient()
        result = SeerfarSelectionService(client).search_rankings({
            'monthlyRevenueMin': 10000,
            'pageNumber': 2,
            'pageSize': 20,
        })

        self.assertEqual('POST', client.calls[0][0])
        self.assertEqual('/open-api/productReport/search/ozon', client.calls[0][1])
        self.assertEqual(2, client.calls[0][2]['page']['pageNumber'])
        self.assertEqual([], result['records'])

    def test_record_mapping_keeps_sku_metrics_and_raw_payload(self):
        from services.seerfar_selection_service import SeerfarSelectionService

        item = SeerfarSelectionService(None).to_research_item({
            'sku': 175924376,
            'title': '测试商品',
            'imageUrl': 'https://img',
            'price': 299,
            'monthlyRevenue': 50000,
            'monthlySales': 120,
            'reviewRating': 4.8,
            'reviews': 80,
        })

        self.assertEqual('seerfar', item['source_type'])
        self.assertEqual('175924376', item['source_id'])
        self.assertEqual('测试商品', item['product_name'])
        self.assertEqual(299, item['suggested_price'])
        self.assertEqual(175924376, item['source_payload_json']['sku'])

    def test_missing_records_are_normalized_to_empty_list(self):
        from services.seerfar_selection_service import SeerfarSelectionService

        result = SeerfarSelectionService(FakeClient({
            'code': 200, 'msg': 'SUCCESS', 'data': {'total': 3},
        })).search_market_keywords({})

        self.assertEqual([], result['records'])
        self.assertEqual(3, result['total'])

    def test_market_keyword_aliases_fill_metric_ranges(self):
        from services.seerfar_selection_service import SeerfarSelectionService

        client = FakeClient()
        SeerfarSelectionService(client).search_market_keywords({
            'monthlyRevenueMin': 10000,
            'monthlyRevenueMax': 90000,
        })
        self.assertEqual(10000, client.calls[0][2]['monthlyRevenue']['min'])
        self.assertEqual(90000, client.calls[0][2]['monthlyRevenue']['max'])


if __name__ == '__main__':
    unittest.main()
