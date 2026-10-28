"""R7 文案越界过滤：规则/清洗/路由/R2 集成（不连外部服务）。"""
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
from services import agent_card_service, copy_lint  # noqa: E402
from utils import agent_auth  # noqa: E402
from utils.auth import require_api_auth  # noqa: E402


class LintTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agentlint_')
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
        copy_lint._LEXICON_CACHE['ts'] = 0
        copy_lint._LEXICON_CACHE['brands'] = set()
        agent_auth._RATE_WINDOWS.clear()
        self._app = Flask(__name__)
        self._app_ctx = self._app.app_context()
        self._app_ctx.push()
        db.init_db()

    def tearDown(self):
        try:
            self._app_ctx.pop()
        except Exception:
            pass
        for p in reversed(self._patchers):
            p.stop()
        Product._cache = []
        Product._loaded = False

    def make_product(self, **overrides):
        data = {'title': 'Товар', 'brand': '无品牌', 'attributes': []}
        data.update(overrides)
        return Product.create(data)

    @staticmethod
    def codes(result):
        return [v['rule'] for v in result['violations']]


class LintRulesTests(LintTestBase):
    def test_clean_text_ok(self):
        result = copy_lint.lint_fields('Мотоциклетная гарнитура', 'Описание товара.', ['#гарнитура'])
        self.assertTrue(result['ok'], result['violations'])

    def test_promo(self):
        result = copy_lint.lint_fields('Скидка 20% на гарнитуру', '', [])
        self.assertIn('PROMO', self.codes(result))

    def test_original_claim(self):
        result = copy_lint.lint_fields('', 'Это 100% оригинал товара', [])
        self.assertIn('ORIGINAL_CLAIM', self.codes(result))

    def test_contact(self):
        result = copy_lint.lint_fields('', 'пишите https://t.me/seller', [])
        self.assertIn('CONTACT', self.codes(result))
        result2 = copy_lint.lint_fields('', 'contact @seller123', [])
        self.assertIn('CONTACT', self.codes(result2))

    def test_cjk(self):
        result = copy_lint.lint_fields('黑色蓝牙耳机', '', [])
        self.assertIn('CJK', self.codes(result))

    def test_title_and_tag_limits(self):
        result = copy_lint.lint_fields('A' * 220, '', ['t%d' % i for i in range(35)])
        codes = self.codes(result)
        self.assertIn('TITLE_LENGTH', codes)
        self.assertIn('TAG_COUNT', codes)
        result2 = copy_lint.lint_fields('', '', ['X' * 40])
        self.assertIn('TAG_LENGTH', self.codes(result2))

    def test_brand_non_own(self):
        target = self.make_product(title='Xiaomi гарнитура')
        result = copy_lint.lint_product(target, extra_brands=['Xiaomi'])
        self.assertIn('BRAND', self.codes(result))

    def test_own_brand_not_flagged(self):
        product = self.make_product(brand='Xiaomi', title='Xiaomi гарнитура')
        result = copy_lint.lint_product(product, extra_brands=['Xiaomi'])
        self.assertNotIn('BRAND', self.codes(result))

    def test_whitelist_no_brand(self):
        product = self.make_product(brand='无品牌', title='Гарнитура без бренда')
        result = copy_lint.lint_product(product, extra_brands=['无品牌'])
        self.assertNotIn('BRAND', self.codes(result))

    def test_sanitize_cleans(self):
        sanitized = copy_lint.sanitize_fields(
            title='Скидка 20% на гарнитуру',
            description='Оригинал! пишите https://t.me/x 黑色',
            tags=['#гарнитура', '#скидка', '#xiaomi'],
            product=self.make_product(brand='无品牌'),
            extra_brands=['Xiaomi'],
        )
        self.assertNotIn('скидк', sanitized['title_ru'].lower())
        self.assertNotIn('оригинал', sanitized['description_ru'].lower())
        self.assertNotIn('http', sanitized['description_ru'].lower())
        self.assertFalse(any('xiaomi' in t.lower() or 'скидк' in t.lower() for t in sanitized['tags']))
        again = copy_lint.lint_fields(sanitized['title_ru'], sanitized['description_ru'], sanitized['tags'],
                                      product=self.make_product(brand='无品牌'), extra_brands=['Xiaomi'])
        self.assertTrue(again['ok'], again['violations'])

    def test_lint_card_structure(self):
        card = {'title_ru': 'Скидка', 'description_ru': '', 'main_tags_ru': ['#акция', '#гарнитура']}
        result = copy_lint.lint_card(card, product=self.make_product())
        self.assertIn('PROMO', self.codes(result))
        self.assertEqual(result['source'] if 'source' in result else 'card', 'card' if 'source' not in result else result['source'])

    def test_lint_target_payload(self):
        result = copy_lint.lint_target('whatever', {'title_ru': 'Скидка', 'tags': ['#акция']})
        self.assertEqual(result['source'], 'payload')
        self.assertIn('PROMO', self.codes(result))
        self.assertIn('sanitized', result)

    def test_lint_target_product(self):
        product = self.make_product(title='Скидка на товар')
        result = copy_lint.lint_target(product['id'])
        self.assertEqual(result['source'], 'product')
        self.assertIn('PROMO', self.codes(result))

    def test_lint_target_missing(self):
        with self.assertRaises(ValueError):
            copy_lint.lint_target('id_missing')


class LintRouteTests(LintTestBase):
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
        token = 'agent_linttoken'
        db.execute(
            "INSERT INTO agent_tokens (name, token_hash, scopes, created_at) VALUES (?, ?, ?, datetime('now'))",
            ('t', agent_auth.hash_token(token), scopes),
        )
        return token

    def test_lint_product_route(self):
        product = self.make_product(title='Скидка на товар')
        token = self._token()
        resp = self.client.post(f'/api/agent/cards/{product["id"]}/lint',
                                headers={'X-Agent-Key': token}, json={})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertFalse(body['data']['ok'])
        self.assertIn('PROMO', [v['rule'] for v in body['data']['violations']])

    def test_lint_payload_ok(self):
        token = self._token()
        resp = self.client.post('/api/agent/cards/payload/lint', headers={'X-Agent-Key': token},
                                json={'title_ru': 'Чистый товар', 'tags': ['#товар']})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertTrue(body['data']['ok'])

    def test_lint_wrong_scope_403(self):
        token = self._token(scopes='images:write')
        resp = self.client.post('/api/agent/cards/id_x/lint', headers={'X-Agent-Key': token}, json={})
        self.assertEqual(resp.get_json()['code'], 403)

    def test_lint_no_token_401(self):
        resp = self.client.post('/api/agent/cards/id_x/lint', json={})
        self.assertEqual(resp.status_code, 401)

    def test_lint_missing_404(self):
        token = self._token()
        resp = self.client.post('/api/agent/cards/id_missing/lint', headers={'X-Agent-Key': token}, json={})
        self.assertEqual(resp.get_json()['code'], 404)


class CardLintIntegrationTests(LintTestBase):
    """R2 生成卡后自动 lint，把风险放进 warnings。"""

    def test_card_generation_adds_lint_warnings(self):
        fake_card = {
            'id': 99, 'product_id': 'x', 'version': 1, 'status': 'GENERATED',
            'title_ru': 'Скидка на гарнитуру', 'description_ru': 'Оригинал товар',
            'main_tags_ru': ['#акция'], 'long_tail_tags_ru': [], 'auxiliary_tags_ru': [],
        }
        with patch.object(agent_card_service.card_service, 'generate_card',
                          lambda product_id, points=None: dict(fake_card)), \
                patch.object(agent_card_service.card_service, 'activate_card', lambda card_id: dict(fake_card)), \
                patch.object(agent_card_service.card_service, 'get_card', lambda card_id: dict(fake_card)), \
                patch.object(agent_card_service.card_service, 'get_current_card', lambda pid: dict(fake_card)):
            result = agent_card_service.build_card({
                'external_id': 'lint-row-1', 'title': '蓝牙耳机', 'apply_card': True,
                'selling_points_zh': '降噪', 'images': [], 'price': 100,
            })
        self.assertTrue(any('文案风险' in w for w in result['warnings']), result['warnings'])


if __name__ == '__main__':
    unittest.main()
