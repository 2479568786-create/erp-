"""R2 一键建卡：服务层 + 路由契约测试（卡片 AI 全部 mock，不连外部 API）。"""
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
from services import agent_card_service, card_service  # noqa: E402
from utils import agent_auth  # noqa: E402
from utils.auth import require_api_auth  # noqa: E402

TEST_PEPPER = 'test-pepper-123'

FAKE_CARD = {
    'id': 77,
    'product_id': 'PLACEHOLDER',
    'version': 1,
    'status': 'GENERATED',
    'title_ru': 'Русский заголовок товара',
    'description_ru': 'Описание товара для Ozon.',
    'main_tags_ru': ['#тег1', '#тег2'],
    'long_tail_tags_ru': ['#длинный'],
    'auxiliary_tags_ru': ['#доп'],
}


class CardTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agentcard_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self.products_path = os.path.join(self.tmpdir, 'products.json')
        open(self.products_path, 'w', encoding='utf-8').write('[]')
        self._patchers = [
            patch.object(db, 'DB_PATH', self.db_path),
            patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': TEST_PEPPER}),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', TEST_PEPPER),
            patch.object(Config, 'AGENT_MAX_RPM', 600),
            patch.object(Product, 'FILE_PATH', self.products_path),
            patch.object(agent_card_service.card_service, 'generate_card', self._fake_generate),
            patch.object(agent_card_service.card_service, 'activate_card', self._fake_activate),
            patch.object(agent_card_service.card_service, 'get_card', self._fake_get_card),
            patch.object(agent_card_service.card_service, 'get_current_card', self._fake_current_card),
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

        self.calls = {'generate': [], 'activate': []}
        self.get_card_result = dict(FAKE_CARD)
        self.current_card_result = dict(FAKE_CARD)

    def tearDown(self):
        try:
            self._app_ctx.pop()
        except Exception:
            pass
        for p in reversed(self._patchers):
            p.stop()
        Product._cache = []
        Product._loaded = False

    # ---- mocks ----
    def _fake_generate(self, product_id, raw_selling_points=None, base_card_id=None):
        self.calls['generate'].append((product_id, raw_selling_points))
        card = dict(FAKE_CARD)
        card['product_id'] = product_id
        return card

    def _fake_activate(self, card_id):
        self.calls['activate'].append(card_id)
        card = dict(self.get_card_result)
        card['status'] = 'EDITED'
        return card

    def _fake_get_card(self, card_id):
        card = dict(self.get_card_result)
        card['id'] = card_id
        return card

    def _fake_current_card(self, product_id):
        card = dict(self.current_card_result)
        card['product_id'] = product_id
        return card

    def payload(self, **overrides):
        data = {
            'external_id': 'excel-row-0001',
            'title': '摩托车蓝牙耳机 500m 对讲',
            'platform': 'manual',
            'original_url': 'https://detail.1688.com/offer/xxx.html',
            'category': {'description_category_id': 17028929, 'type_id': 970959015},
            'selling_points_zh': '1. 500m 对讲\n2. 降噪',
            'apply_card': True,
            'attributes': [{'name': '品牌（Бренд）', 'value': '无品牌'}],
            'price': 320, 'old_price': 399, 'source_price': 55, 'stock': 10,
            'weight_g': 300, 'length_mm': 120, 'width_mm': 80, 'height_mm': 40,
            'package_mode': 'spu',
            'images': ['https://example.com/1.jpg'],
            'store_id': 6,
        }
        data.update(overrides)
        return data

    def insert_required_attrs(self):
        for attr_id, name in ((85, 'Бренд'), (8229, 'Тип'), (9048, 'Название модели')):
            db.execute(
                """INSERT INTO ozon_category_attributes
                   (description_category_id, type_id, attribute_id, name, name_zh, name_ru, is_required)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (17028929, 970959015, attr_id, name, name, name),
            )


class BuildCardTests(CardTestBase):
    def test_create_without_card(self):
        result = agent_card_service.build_card(self.payload(apply_card=False))
        self.assertTrue(result['created'])
        self.assertIsNone(result['card_id'])
        self.assertEqual(self.calls['generate'], [])
        product = Product.find_by_id(result['product_id'])
        self.assertEqual(product['title'], '摩托车蓝牙耳机 500m 对讲')
        self.assertEqual(product['externalId'], 'excel-row-0001')
        self.assertEqual(product['note'], 'agent:excel-row-0001')
        self.assertEqual(product['price'], 320)
        self.assertEqual(product['descriptionCategoryId'], 17028929)
        self.assertEqual(product['typeId'], 970959015)
        self.assertEqual(product['images'], ['https://example.com/1.jpg'])
        self.assertEqual(product['attributes'][0]['name'], '品牌（Бренд）')
        self.assertEqual(product['dimensions'], {'length': 120, 'width': 80, 'height': 40})

    def test_apply_card_backfills(self):
        result = agent_card_service.build_card(self.payload())
        self.assertTrue(result['created'])
        self.assertEqual(result['card_id'], 77)
        self.assertEqual(len(self.calls['generate']), 1)
        self.assertEqual(self.calls['activate'], [77])
        product = Product.find_by_id(result['product_id'])
        self.assertEqual(product['title'], 'Русский заголовок товара')
        self.assertEqual(product['description'], 'Описание товара для Ozon.')
        # 主题标签（属性不存在 → 追加 23171）
        theme = [a for a in product['attributes'] if str(a.get('id')) == '23171']
        self.assertEqual(len(theme), 1)
        self.assertEqual(theme[0]['value'], '#тег1 #тег2 #длинный #доп')
        # 无简介属性 → pendingCardDescription 兜底
        self.assertEqual(product.get('pendingCardDescription'), 'Описание товара для Ozon.')
        self.assertIn('title', result['applied'])
        self.assertIn('theme_tags', result['applied'])

    def test_annotation_attribute_replaced(self):
        base = self.payload()
        base['attributes'] = [{'name': '简介（Аннотация）', 'value': '旧简介'}]
        result = agent_card_service.build_card(base)
        product = Product.find_by_id(result['product_id'])
        ann = [a for a in product['attributes'] if 'Аннотация' in (a.get('name') or '')]
        self.assertEqual(len(ann), 1)
        self.assertEqual(ann[0]['value'], 'Описание товара для Ozon.')
        self.assertNotIn('pendingCardDescription', product)

    def test_idempotent_by_external_id(self):
        first = agent_card_service.build_card(self.payload())
        second = agent_card_service.build_card(self.payload(title='改了标题也不应新建'))
        self.assertEqual(first['product_id'], second['product_id'])
        self.assertFalse(second['created'])
        self.assertEqual(len(self.calls['generate']), 1)
        self.assertEqual(len(Product.find_all()), 1)

    def test_missing_required_attributes(self):
        self.insert_required_attrs()
        base = self.payload()
        base['attributes'] = [{'id': 85, 'name': 'Бренд', 'value': '无品牌'}]
        result = agent_card_service.build_card(base)
        self.assertIn('Тип', result['missing_attributes'])
        self.assertIn('Название модели', result['missing_attributes'])
        self.assertNotIn('Бренд', result['missing_attributes'])

    def test_validation_errors(self):
        with self.assertRaises(agent_card_service.AgentValidationError):
            agent_card_service.build_card({'title': 'x'})
        with self.assertRaises(agent_card_service.AgentValidationError):
            agent_card_service.build_card({'external_id': 'x'})
        with self.assertRaises(agent_card_service.AgentValidationError):
            agent_card_service.build_card('not-a-dict')

    def test_empty_selling_points_fallback_warning(self):
        result = agent_card_service.build_card(self.payload(selling_points_zh=''))
        self.assertTrue(any('selling_points_zh' in w for w in result['warnings']))
        self.assertEqual(len(self.calls['generate']), 1)

    def test_card_generation_failure_rolls_back_product(self):
        """建卡失败要回滚本次新建的商品，不留孤儿（2026-10-29 线上冒烟暴露）。"""
        def boom(*args, **kwargs):
            raise RuntimeError('俄文描述仅 134 词，未达 150 词')
        with patch.object(agent_card_service.card_service, 'generate_card', boom):
            with self.assertRaises(agent_card_service.AgentValidationError) as ctx:
                agent_card_service.build_card(self.payload())
        self.assertIn('已回滚', str(ctx.exception))
        self.assertEqual(Product.find_all(), [], '建卡失败后不应残留商品')
        # 回滚后同 external_id 可重试（不命中幂等分支）
        result = agent_card_service.build_card(self.payload())
        self.assertTrue(result['created'])
        self.assertEqual(len(Product.find_all()), 1)


class RouteContractTests(CardTestBase):
    def setUp(self):
        super().setUp()
        from routes.agent_routes import agent_bp

        app = Flask(__name__)
        app.before_request(agent_auth.agent_request_guard)
        app.before_request(require_api_auth)
        app.after_request(agent_auth.agent_audit_after_request)
        app.register_blueprint(agent_bp, url_prefix='/api')
        self.client = app.test_client()

    def _token(self, scopes='cards:write'):
        token = 'agent_cardtoken'
        db.execute(
            "INSERT INTO agent_tokens (name, token_hash, scopes, created_at) VALUES (?, ?, ?, datetime('now'))",
            ('t', agent_auth.hash_token(token), scopes),
        )
        return token

    def test_build_ok_with_agent_token(self):
        token = self._token()
        resp = self.client.post('/api/agent/cards/build', headers={'X-Agent-Key': token}, json=self.payload())
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertTrue(body['data']['created'])
        self.assertEqual(body['data']['product_id'], Product.find_all()[0]['id'])

    def test_missing_scope_403(self):
        token = self._token(scopes='products:read')
        resp = self.client.post('/api/agent/cards/build', headers={'X-Agent-Key': token}, json=self.payload())
        self.assertEqual(resp.get_json()['code'], 403)

    def test_no_token_401(self):
        resp = self.client.post('/api/agent/cards/build', json=self.payload())
        self.assertEqual(resp.status_code, 401)

    def test_validation_422(self):
        token = self._token()
        resp = self.client.post('/api/agent/cards/build', headers={'X-Agent-Key': token}, json={'title': 'x'})
        self.assertEqual(resp.get_json()['code'], 422)

    def test_audit_written(self):
        token = self._token()
        self.client.post('/api/agent/cards/build', headers={'X-Agent-Key': token}, json=self.payload())
        rows = db.query("SELECT * FROM agent_ops ORDER BY id DESC")
        self.assertEqual(len(rows), 1, '每次调用只应写一条审计')
        self.assertEqual(rows[0]['status'], 'ok')
        self.assertTrue(rows[0]['product_id'])
        self.assertEqual(rows[0]['request_id'], 'excel-row-0001')

    def test_generate_failure_422_and_no_orphan_product(self):
        token = self._token()

        def boom(*args, **kwargs):
            raise RuntimeError('文案质量门槛未通过')

        with patch.object(agent_card_service.card_service, 'generate_card', boom):
            resp = self.client.post('/api/agent/cards/build',
                                    headers={'X-Agent-Key': token}, json=self.payload())
        self.assertEqual(resp.get_json()['code'], 422)
        self.assertEqual(Product.find_all(), [], '422 后不应残留孤儿商品')
        row = db.query("SELECT * FROM agent_ops ORDER BY id DESC")[0]
        self.assertEqual(row['status'], 'invalid')
        self.assertEqual(row['request_id'], 'excel-row-0001')


if __name__ == '__main__':
    unittest.main()
