# -*- coding: utf-8 -*-
"""R2-Fix2：build 支持 SKU（颜色字典唯一命中 + skus[] 映射），不连外部 AI/DB。"""
import os
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services import agent_card_service  # noqa: E402


def _dict_rows(*values):
    rows = []
    for idx, (value_id, value, value_zh) in enumerate(values, start=1):
        rows.append({'value_id': value_id, 'value': value, 'value_zh': value_zh,
                     'value_ru': value})
    return rows


class BuildSkusTests(unittest.TestCase):
    def test_unique_color_match(self):
        rows = _dict_rows((955, 'черный', '黑色'))
        with patch.object(agent_card_service, 'query', lambda *a, **k: rows):
            skus = agent_card_service._build_skus(
                [{'combo': {'商品颜色（Цвет товара）': 'черный'},
                  'price': 320, 'old_price': 400, 'source_price': 60, 'stock': 10,
                  'package_weight_g': 300, 'length_mm': 120, 'width_mm': 80, 'height_mm': 50,
                  'image': '/uploads/a.png', 'sku_code': 'qa-1'}],
                17028929, 970959015)
        self.assertEqual(len(skus), 1)
        sku = skus[0]
        self.assertEqual(sku['combo'][agent_card_service.COLOR_STANDARD_KEY], 'черный')
        self.assertEqual(sku['colorValueId'], 955)
        self.assertEqual(sku['skuCode'], 'qa-1')
        self.assertEqual(sku['offerId'], 'qa-1')
        self.assertEqual(sku['price'], 320)
        self.assertEqual(sku['oldPrice'], 400)
        self.assertEqual(sku['stock'], 10)
        self.assertEqual(sku['weight'], 300)
        self.assertEqual(sku['dimensions'], {'length': 120, 'width': 80, 'height': 50})
        self.assertEqual(sku['images'], ['/uploads/a.png'])

    def test_chinese_color_matches_value_zh(self):
        rows = _dict_rows((955, 'черный', '黑色'))
        with patch.object(agent_card_service, 'query', lambda *a, **k: rows):
            skus = agent_card_service._build_skus([{'color': '黑色'}], 17028929, 970959015)
        self.assertEqual(skus[0]['colorValueId'], 955)
        self.assertEqual(skus[0]['combo'][agent_card_service.COLOR_STANDARD_KEY], 'черный')

    def test_ambiguous_color_raises_with_candidates(self):
        rows = _dict_rows((955, 'черный', '黑色'), (956, 'черный', '黑色'))
        with patch.object(agent_card_service, 'query', lambda *a, **k: rows):
            with self.assertRaises(agent_card_service.AgentValidationError) as ctx:
                agent_card_service._build_skus([{'color': 'черный'}], 17028929, 970959015)
        message = str(ctx.exception)
        self.assertIn('候选', message)
        self.assertIn('955', message)

    def test_unknown_color_raises(self):
        rows = _dict_rows((955, 'черный', '黑色'))
        with patch.object(agent_card_service, 'query', lambda *a, **k: rows):
            with self.assertRaises(agent_card_service.AgentValidationError) as ctx:
                agent_card_service._build_skus([{'color': 'фиолетовый'}], 17028929, 970959015)
        self.assertIn('无法在 Ozon 颜色字典唯一命中', str(ctx.exception))

    def test_explicit_color_value_id_bypasses_lookup(self):
        def boom(*args, **kwargs):
            raise AssertionError('不应查字典')

        with patch.object(agent_card_service, '_dictionary_color', boom):
            skus = agent_card_service._build_skus(
                [{'combo': {'商品颜色': 'черный'}, 'color_value_id': 955}], 1, 2)
        self.assertEqual(skus[0]['colorValueId'], 955)
        self.assertEqual(skus[0]['combo'][agent_card_service.COLOR_STANDARD_KEY], 'черный')

    def test_multi_skus_and_no_color(self):
        rows = _dict_rows((955, 'черный', '黑色'))
        with patch.object(agent_card_service, 'query', lambda *a, **k: rows):
            skus = agent_card_service._build_skus([
                {'combo': {'商品颜色': 'черный'}, 'price': 100},
                {'price': 200, 'stock': 3},
            ], 17028929, 970959015)
        self.assertEqual(len(skus), 2)
        self.assertEqual(skus[1]['price'], 200)
        self.assertNotIn('colorValueId', skus[1])

    def test_empty_skus(self):
        self.assertEqual(agent_card_service._build_skus(None), [])
        self.assertEqual(agent_card_service._build_skus([]), [])

    def test_bad_sku_shape(self):
        with self.assertRaises(agent_card_service.AgentValidationError):
            agent_card_service._build_skus(['not-a-dict'])
        with self.assertRaises(agent_card_service.AgentValidationError):
            agent_card_service._build_skus([{'combo': 'not-a-dict'}])

    def test_build_product_data_includes_skus(self):
        rows = _dict_rows((955, 'черный', '黑色'))
        with patch.object(agent_card_service, 'query', lambda *a, **k: rows):
            data = agent_card_service._build_product_data({
                'title': '测试商品',
                'category': {'description_category_id': 17028929, 'type_id': 970959015},
                'skus': [{'combo': {'商品颜色': 'черный'}, 'price': 320, 'sku_code': 'qa-9'}],
            }, 'ex-1')
        self.assertEqual(data['descriptionCategoryId'], 17028929)
        self.assertEqual(len(data['skus']), 1)
        self.assertEqual(data['skus'][0]['skuCode'], 'qa-9')


if __name__ == '__main__':
    unittest.main()
