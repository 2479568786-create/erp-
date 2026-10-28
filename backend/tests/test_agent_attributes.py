"""R5 属性写入：服务层 + 路由契约（本地字典表，不调 Ozon API）。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault('AGENT_TOKEN_PEPPER', 'test-pepper-123')

from flask import Flask  # noqa: E402

import db  # noqa: E402
from config import Config  # noqa: E402
from models.product import Product  # noqa: E402
from services import agent_attributes_service as attrs  # noqa: E402
from utils import agent_auth  # noqa: E402
from utils.auth import require_api_auth  # noqa: E402

CATEGORY_ID = 17028929
TYPE_ID = 970959015


class AttrTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agentattr_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self.products_path = os.path.join(self.tmpdir, 'products.json')
        open(self.products_path, 'w', encoding='utf-8').write('[]')
        self._patchers = [
            patch.object(db, 'DB_PATH', self.db_path),
            patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': 'test-pepper-123'}),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', 'test-pepper-123'),
            patch.object(Config, 'AGENT_MAX_RPM', 600),
            patch.object(Product, 'FILE_PATH', self.products_path),
        ]
        for p in self._patchers:
            p.start()
        Product._cache = []
        Product._loaded = False
        agent_auth._RATE_WINDOWS.clear()
        self._app = Flask(__name__)
        self._app_ctx = self._app.app_context()
        self._app_ctx.push()
        db.init_db()
        self.seed_category()

    def tearDown(self):
        try:
            self._app_ctx.pop()
        except Exception:
            pass
        for p in reversed(self._patchers):
            p.stop()
        Product._cache = []
        Product._loaded = False

    # ---------- fixtures ----------
    def seed_category(self):
        rows = [
            (85, 'Бренд', '品牌', 'String', 1, 28732849),
            (8229, 'Тип', '类型', 'String', 1, 1960),
            (9048, 'Название модели', '型号名称', 'String', 1, 0),
            (10096, 'Цвет товара', '颜色', 'String', 0, 10096),
            (5000, 'Вес товара, г', '商品重量（克）', 'Decimal', 0, 0),
            (6000, 'Длина, мм', '长度（毫米）', 'Decimal', 0, 0),
        ]
        for attr_id, name_ru, name_zh, attr_type, required, dict_id in rows:
            db.execute(
                """INSERT INTO ozon_category_attributes
                   (description_category_id, type_id, attribute_id, name, name_zh, name_ru,
                    attr_type, is_required, dictionary_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (CATEGORY_ID, TYPE_ID, attr_id, name_ru, name_zh, name_ru, attr_type, required, dict_id),
            )
        dict_rows = [
            (85, 28732849, 90296, 'Нет бренда', '无品牌'),
            (10096, 10096, 39140, 'Чёрный', '黑色'),
            (10096, 10096, 39141, 'Чёрный матовый', '哑光黑'),
            (10096, 10096, 39142, 'Чёрный глянцевый', '亮黑'),
        ]
        for attr_id, dict_id, value_id, value_ru, value_zh in dict_rows:
            db.execute(
                """INSERT INTO ozon_attribute_dictionary_values
                   (dictionary_id, attribute_id, description_category_id, type_id,
                    value_id, value, value_ru, value_zh)
                   VALUES (?, ?, 0, 0, ?, ?, ?, ?)""",
                (dict_id, attr_id, value_id, value_ru, value_ru, value_zh),
            )

    def make_product(self, **overrides):
        data = {
            'title': '摩托车蓝牙耳机',
            'descriptionCategoryId': CATEGORY_ID,
            'typeId': TYPE_ID,
            'attributes': [],
            'weight': 300,
            'dimensions': {'length': 120, 'width': 80, 'height': 40},
        }
        data.update(overrides)
        return Product.create(data)


class ApplyAttributeTests(AttrTestBase):
    def test_brand_by_id_resolves_dictionary(self):
        product = self.make_product()
        result = attrs.apply_attributes(product['id'], {
            'attributes': [{'attr_id': 85, 'value': '无品牌'}],
        })
        self.assertEqual(len(result['applied']), 1)
        applied = result['applied'][0]
        self.assertEqual(applied['attr_id'], 85)
        self.assertEqual(applied['dictionary_value_id'], 90296)
        saved = Product.find_by_id(product['id'])
        brand = [a for a in saved['attributes'] if a['id'] == 85][0]
        self.assertEqual(brand['dictionary_value_id'], 90296)

    def test_model_by_name_no_dictionary(self):
        product = self.make_product()
        result = attrs.apply_attributes(product['id'], {
            'attributes': [{'name': '型号名称', 'value': 'MZ-2601'}],
        })
        applied = result['applied'][0]
        self.assertEqual(applied['attr_id'], 9048)
        self.assertEqual(applied['value'], 'MZ-2601')
        self.assertNotIn('dictionary_value_id', applied)

    def test_color_unique_hit(self):
        product = self.make_product()
        result = attrs.apply_attributes(product['id'], {
            'attributes': [{'attr_id': 10096, 'value': 'Чёрный'}],
        })
        self.assertEqual(result['applied'][0]['dictionary_value_id'], 39140)
        self.assertEqual(result['density']['checked'], True)

    def test_dictionary_ambiguous_422_data(self):
        product = self.make_product()
        result = attrs.apply_attributes(product['id'], {
            'attributes': [{'attr_id': 10096, 'value': 'Чёрный'}],
            'strict': False,
        })
        # 精确命中唯一值，不应歧义；再用一个前缀触发多候选
        result2 = attrs.apply_attributes(product['id'], {
            'attributes': [{'attr_id': 10096, 'value': 'Чёрный '}],
        })
        self.assertTrue(result['applied'])
        self.assertEqual(len(result2['applied']), 1)
        # 构造真正歧义：查询 "Ч"
        result3 = attrs.apply_attributes(product['id'], {
            'attributes': [{'attr_id': 10096, 'value': 'Чё'}],
        })
        self.assertEqual(result3['applied'], [])
        self.assertEqual(result3['unresolved'][0]['reason'], 'dictionary_ambiguous')
        self.assertGreaterEqual(len(result3['unresolved'][0]['candidates']), 2)

        with self.assertRaises(attrs.AgentAttributeError):
            attrs.apply_attributes(product['id'], {
                'attributes': [{'attr_id': 10096, 'value': 'Чё'}],
                'strict': True,
            })

    def test_dictionary_not_found(self):
        product = self.make_product()
        result = attrs.apply_attributes(product['id'], {
            'attributes': [{'attr_id': 85, 'value': '不存在的品牌'}],
        })
        self.assertEqual(result['applied'], [])
        self.assertEqual(result['unresolved'][0]['reason'], 'dictionary_not_found')

    def test_merge_keeps_other_attributes(self):
        product = self.make_product(attributes=[
            {'id': 9048, 'name': 'Название модели', 'value': 'OLD'}
        ])
        attrs.apply_attributes(product['id'], {
            'attributes': [{'attr_id': 85, 'value': '无品牌'}],
            'mode': 'merge',
        })
        saved = Product.find_by_id(product['id'])
        ids = {a['id'] for a in saved['attributes']}
        self.assertEqual(ids, {85, 9048})

    def test_replace_clears(self):
        product = self.make_product(attributes=[
            {'id': 9048, 'name': 'Название модели', 'value': 'OLD'}
        ])
        attrs.apply_attributes(product['id'], {
            'attributes': [{'attr_id': 85, 'value': '无品牌'}],
            'mode': 'replace',
        })
        saved = Product.find_by_id(product['id'])
        self.assertEqual([a['id'] for a in saved['attributes']], [85])

    def test_unit_normalization(self):
        product = self.make_product()
        result = attrs.apply_attributes(product['id'], {
            'attributes': [
                {'attr_id': 5000, 'value': '1.2kg'},
                {'attr_id': 6000, 'value': '12cm'},
            ],
        })
        values = {item['attr_id']: item['value'] for item in result['applied']}
        self.assertEqual(values[5000], 1200)
        self.assertEqual(values[6000], 120)

    def test_density_out_of_range_warning(self):
        product = self.make_product(weight=5000, dimensions={'length': 10, 'width': 10, 'height': 10})
        result = attrs.apply_attributes(product['id'], {
            'attributes': [{'attr_id': 9048, 'name': '型号名称', 'value': 'M'}],
        })
        self.assertFalse(result['density']['ok'])
        self.assertTrue(any('密度异常' in w for w in result['warnings']))

    def test_missing_required(self):
        product = self.make_product()
        result = attrs.apply_attributes(product['id'], {
            'attributes': [{'attr_id': 85, 'value': '无品牌'}],
            'validate_required': True,
        })
        self.assertIn('Тип', result['missing_required'])
        self.assertIn('Название модели', result['missing_required'])
        self.assertNotIn('Бренд', result['missing_required'])

    def test_unknown_attr_id(self):
        product = self.make_product()
        result = attrs.apply_attributes(product['id'], {
            'attributes': [{'attr_id': 999999, 'value': 'x'}],
        })
        self.assertEqual(result['unresolved'][0]['reason'], 'attribute_not_found')

    def test_validation_errors(self):
        product = self.make_product()
        for payload in ({'attributes': []}, {'attributes': 'x'}, {'attributes': [{'value': 'x'}]},
                        {'attributes': [{'attr_id': 85, 'value': 'x'}], 'mode': 'bad'}):
            with self.assertRaises(attrs.AgentAttributeError):
                attrs.apply_attributes(product['id'], payload)
        with self.assertRaises(attrs.AgentAttributeError):
            attrs.apply_attributes('id_missing', {'attributes': [{'attr_id': 85, 'value': 'x'}]})


class AttrRouteTests(AttrTestBase):
    def setUp(self):
        super().setUp()
        from routes.agent_routes import agent_bp

        app = Flask(__name__)
        app.before_request(agent_auth.agent_request_guard)
        app.before_request(require_api_auth)
        app.after_request(agent_auth.agent_audit_after_request)
        app.register_blueprint(agent_bp, url_prefix='/api')
        self.client = app.test_client()

    def _token(self, scopes='products:write'):
        token = 'agent_attrtoken'
        db.execute(
            "INSERT INTO agent_tokens (name, token_hash, scopes, created_at) VALUES (?, ?, ?, datetime('now'))",
            ('t', agent_auth.hash_token(token), scopes),
        )
        return token

    def test_route_ok(self):
        product = self.make_product()
        token = self._token()
        resp = self.client.put(f'/api/products/{product["id"]}/attributes',
                               headers={'X-Agent-Key': token},
                               json={'attributes': [{'attr_id': 85, 'value': '无品牌'}]})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertEqual(len(body['data']['applied']), 1)

    def test_route_wrong_scope_403(self):
        product = self.make_product()
        token = self._token(scopes='images:write')
        resp = self.client.put(f'/api/products/{product["id"]}/attributes',
                               headers={'X-Agent-Key': token},
                               json={'attributes': [{'attr_id': 85, 'value': '无品牌'}]})
        self.assertEqual(resp.get_json()['code'], 403)

    def test_route_no_token_401(self):
        resp = self.client.put('/api/products/id_x/attributes', json={'attributes': []})
        self.assertEqual(resp.status_code, 401)

    def test_route_validation_422(self):
        product = self.make_product()
        token = self._token()
        resp = self.client.put(f'/api/products/{product["id"]}/attributes',
                               headers={'X-Agent-Key': token}, json={'attributes': []})
        self.assertEqual(resp.get_json()['code'], 422)


if __name__ == '__main__':
    unittest.main()
