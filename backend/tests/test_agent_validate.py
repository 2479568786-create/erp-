"""R6 发布前校验：服务层 + 路由契约（本地 schema，不调 Ozon）。"""
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
from services import agent_validate_service as validator  # noqa: E402
from utils import agent_auth  # noqa: E402
from utils.auth import require_api_auth  # noqa: E402

CATEGORY_ID = 17028929
TYPE_ID = 970959015


class ValidateTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agentval_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self.products_path = os.path.join(self.tmpdir, 'products.json')
        self.gallery_dir = os.path.join(self.tmpdir, 'gallery')
        self.upload_dir = os.path.join(self.tmpdir, 'uploads')
        os.makedirs(self.gallery_dir, exist_ok=True)
        os.makedirs(self.upload_dir, exist_ok=True)
        open(self.products_path, 'w', encoding='utf-8').write('[]')
        self._patchers = [
            patch.object(db, 'DB_PATH', self.db_path),
            patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': 'test-pepper-123'}),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', 'test-pepper-123'),
            patch.object(Config, 'AGENT_MAX_RPM', 600),
            patch.object(Config, 'UPLOAD_DIR', self.upload_dir),
            patch.object(Product, 'FILE_PATH', self.products_path),
            patch('routes.gallery_routes.GALLERY_DIR', self.gallery_dir),
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
        self.seed_schema()
        self.seed_gallery_files()

    def tearDown(self):
        try:
            self._app_ctx.pop()
        except Exception:
            pass
        for p in reversed(self._patchers):
            p.stop()
        Product._cache = []
        Product._loaded = False

    def seed_schema(self):
        rows = [
            (9048, 'Название модели', '型号名称', 'String', 1, 0),
            (8229, 'Тип', '类型', 'String', 1, 1960),
            (10096, 'Цвет товара', '颜色', 'String', 0, 10096),
        ]
        for attr_id, name_ru, name_zh, attr_type, required, dict_id in rows:
            db.execute(
                """INSERT INTO ozon_category_attributes
                   (description_category_id, type_id, attribute_id, name, name_zh, name_ru,
                    attr_type, is_required, dictionary_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (CATEGORY_ID, TYPE_ID, attr_id, name_ru, name_zh, name_ru, attr_type, required, dict_id),
            )
        db.execute(
            """INSERT INTO ozon_attribute_dictionary_values
               (dictionary_id, attribute_id, description_category_id, type_id, value_id, value, value_ru, value_zh)
               VALUES (1960, 8229, ?, ?, 111, 'Гарнитура', 'Гарнитура', '耳机')""",
            (CATEGORY_ID, TYPE_ID),
        )

    def seed_gallery_files(self):
        for name in ('img1.png', 'img2.png'):
            path = os.path.join(self.gallery_dir, name)
            with open(path, 'wb') as f:
                f.write(b'fake-png-bytes')

    def make_product(self, **overrides):
        base_attrs = [
            {'id': 9048, 'name': 'Название модели', 'value': 'MZ-2601'},
            {'id': 8229, 'name': 'Тип', 'value': 'Гарнитура', 'dictionary_value_id': 111},
        ]
        data = {
            'title': 'Мотоциклетная гарнитура',
            'descriptionCategoryId': CATEGORY_ID,
            'typeId': TYPE_ID,
            'price': 320,
            'oldPrice': 399,
            'weight': 300,
            'dimensions': {'length': 120, 'width': 80, 'height': 40},
            'images': ['/api/gallery/files/img1.png', '/api/gallery/files/img2.png'],
            'attributes': base_attrs,
        }
        data.update(overrides)
        return Product.create(data)


class ValidateServiceTests(ValidateTestBase):
    def test_ok_case_with_preview(self):
        product = self.make_product()
        result = validator.validate_product(product['id'])
        self.assertTrue(result['ok'], result['blocking'])
        self.assertEqual(result['images']['count'], 2)
        self.assertTrue(result['images']['primary'].endswith('img1.png'))
        self.assertIsNotNone(result['payload_preview'])
        self.assertEqual(result['payload_preview']['_items_total'], result['items_count'])
        self.assertEqual(result['schema_source'], 'local-cache')
        self.assertTrue(result['dry_run'])

    def test_no_image(self):
        product = self.make_product(images=[])
        result = validator.validate_product(product['id'])
        self.assertFalse(result['ok'])
        codes = [b['code'] for b in result['blocking']]
        self.assertIn('NO_IMAGE', codes)

    def test_bad_image_missing_local_file(self):
        product = self.make_product(images=['/uploads/not-exists.png'])
        result = validator.validate_product(product['id'])
        codes = [b['code'] for b in result['blocking']]
        self.assertIn('BAD_IMAGE', codes)
        self.assertTrue(result['images']['bad'])

    def test_missing_required_attributes(self):
        product = self.make_product(attributes=[])
        result = validator.validate_product(product['id'])
        codes = [b['code'] for b in result['blocking']]
        self.assertIn('MISSING_REQUIRED', codes)

    def test_missing_dictionary_value(self):
        product = self.make_product(attributes=[{'id': 8229, 'name': 'Тип', 'value': 'Гарнитура'}])
        result = validator.validate_product(product['id'])
        codes = [b['code'] for b in result['blocking']]
        self.assertIn('MISSING_DICTIONARY', codes)

    def test_price_invalid(self):
        product = self.make_product(price=0)
        result = validator.validate_product(product['id'])
        codes = [b['code'] for b in result['blocking']]
        self.assertIn('PRICE_INVALID', codes)

    def test_offer_id_too_long(self):
        product = self.make_product(skus=[{'combo': {}, 'price': 320, 'offerId': 'X' * 60, 'images': []}])
        result = validator.validate_product(product['id'])
        codes = [b['code'] for b in result['blocking']]
        self.assertIn('OFFER_ID_TOO_LONG', codes)

    def test_offer_id_non_ascii(self):
        # 历史失败真实原因：offer_id 里带中文（如 10281439-哑光黑色（черный матовый）），Ozon 直接拒
        from unittest.mock import patch as _patch
        product = self.make_product()
        fake_item = {
            'offer_id': '10281439-哑光黑色（черный матовый）',
            'price': 320,
            'images': ['/api/gallery/files/img1.png'],
            'weight': 300, 'width': 80, 'height': 40, 'depth': 120,
            'attributes': [
                {'id': 9048, 'values': [{'value': 'MZ-2601'}]},
                {'id': 8229, 'values': [{'value': 'Гарнитура', 'dictionary_value_id': 111}]},
            ],
        }
        with _patch('services.agent_validate_service.publish_service.build_ozon_product_items',
                    return_value=[fake_item]):
            result = validator.validate_product(product['id'])
        entry = [b for b in result['blocking'] if b['code'] == 'OFFER_ID_TOO_LONG']
        self.assertTrue(entry, result['blocking'])
        self.assertIn('非 ASCII', entry[0]['message'])

    def test_density_out_of_range(self):
        product = self.make_product(weight=5000, dimensions={'length': 10, 'width': 10, 'height': 10})
        result = validator.validate_product(product['id'])
        codes = [b['code'] for b in result['blocking']]
        self.assertIn('DENSITY_OUT_OF_RANGE', codes)

    def test_unknown_product(self):
        with self.assertRaises(validator.AgentValidateError):
            validator.validate_product('id_nope')

    def test_no_ozon_network_calls(self):
        product = self.make_product()
        called = {'n': 0}

        def boom(*args, **kwargs):
            called['n'] += 1
            raise RuntimeError('不应调用 Ozon API')

        with patch('services.ozon_api.get_category_attributes', boom), \
                patch('services.ozon_api.validate_category_pair', boom):
            result = validator.validate_product(product['id'])
        self.assertEqual(called['n'], 0)
        self.assertIn('ok', result)


class ValidateRouteTests(ValidateTestBase):
    def setUp(self):
        super().setUp()
        from routes.agent_routes import agent_bp

        app = Flask(__name__)
        app.before_request(agent_auth.agent_request_guard)
        app.before_request(require_api_auth)
        app.after_request(agent_auth.agent_audit_after_request)
        app.register_blueprint(agent_bp, url_prefix='/api')
        self.client = app.test_client()

    def _token(self, scopes='products:read'):
        token = 'agent_valtoken'
        db.execute(
            "INSERT INTO agent_tokens (name, token_hash, scopes, created_at) VALUES (?, ?, ?, datetime('now'))",
            ('t', agent_auth.hash_token(token), scopes),
        )
        return token

    def test_route_ok(self):
        product = self.make_product()
        token = self._token()
        resp = self.client.post(f'/api/products/{product["id"]}/validate',
                                headers={'X-Agent-Key': token}, json={'dry_run': True})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertTrue(body['data']['ok'])

    def test_route_wrong_scope_403(self):
        product = self.make_product()
        token = self._token(scopes='images:write')
        resp = self.client.post(f'/api/products/{product["id"]}/validate',
                                headers={'X-Agent-Key': token}, json={})
        self.assertEqual(resp.get_json()['code'], 403)

    def test_route_no_token_401(self):
        resp = self.client.post('/api/products/id_x/validate', json={})
        self.assertEqual(resp.status_code, 401)

    def test_route_unknown_product_404(self):
        token = self._token()
        resp = self.client.post('/api/products/id_missing/validate',
                                headers={'X-Agent-Key': token}, json={})
        self.assertEqual(resp.get_json()['code'], 404)


if __name__ == '__main__':
    unittest.main()
