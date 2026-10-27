import unittest

from services.jlc_pricing_service import calculate_jlc_price
from services.product_analysis_service import analyze_product


class ProductAnalysisAndJlcPricingTests(unittest.TestCase):
    def test_analysis_keeps_unknowns_and_extracts_skus(self):
        result = analyze_product({
            'id': 'p1',
            'title': '不锈钢保温杯',
            'category': '厨房用品',
            'attributes': [{'name': '尺寸', 'value': '12*12*25cm'}, {'name': '重量', 'value': '0.5kg'}],
            'skus': [{'skuId': 'red', 'name': '红色', 'sourcePrice': 20, 'images': ['a.jpg']}],
            'images': ['cover.jpg'],
        })
        self.assertEqual(result['facts']['dimensions']['height'], 25.0)
        self.assertEqual(result['facts']['weight']['value'], 500.0)
        self.assertEqual(result['facts']['skus'][0]['purchase_price_cny'], 20.0)
        self.assertIn('certifications', {item['field'] for item in result['missing_information']})

    def test_jlc_profile_uses_volumetric_weight_and_eligible_route(self):
        result = calculate_jlc_price(cost_cny=26, weight_g=575, length_cm=13, width_cm=13, height_cm=26.25)
        self.assertEqual(result['profile'], 'jlc_global_rets')
        self.assertGreater(result['shipping']['volumetric_weight_g'], 0)
        self.assertEqual(result['shipping']['route_name'], 'RETS Small Economy')
        self.assertGreater(result['suggested_price_rub'], 0)

    def test_jlc_profile_rejects_impossible_measurements(self):
        with self.assertRaisesRegex(ValueError, '没有符合'):
            calculate_jlc_price(cost_cny=20, weight_g=40000, length_cm=200, width_cm=200, height_cm=200)


if __name__ == '__main__':
    unittest.main()
