"""R3 套图批量：服务层 + 路由契约（凌科生图/下载全部 mock，不连外部 API）。"""
import io
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
from PIL import Image  # noqa: E402

import db  # noqa: E402
from config import Config  # noqa: E402
from services import image_billing_service as billing  # noqa: E402
from services import suite_service  # noqa: E402
from utils import agent_auth  # noqa: E402
from utils.auth import require_api_auth  # noqa: E402

TEST_PEPPER = 'test-pepper-123'
ALL_MODULES = [m['key'] for m in suite_service.AI_SUITE_MODULES]


def tiny_png():
    buf = io.BytesIO()
    Image.new('RGB', (16, 16), (200, 60, 60)).save(buf, format='PNG')
    return buf.getvalue()


class SuiteTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agentsuite_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self.gallery_dir = os.path.join(self.tmpdir, 'gallery')
        os.makedirs(self.gallery_dir, exist_ok=True)
        self._patchers = [
            patch.object(db, 'DB_PATH', self.db_path),
            patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': TEST_PEPPER}),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', TEST_PEPPER),
            patch.object(Config, 'AGENT_MAX_RPM', 600),
            patch.object(Config, 'SUITE_CONCURRENCY', 2),
            patch('routes.gallery_routes.GALLERY_DIR', self.gallery_dir),
            patch('routes.gallery_routes._download_generated_image', lambda url: tiny_png()),
            patch.object(suite_service.jlc_media_service, 'generate_image',
                         self._fake_generate),
            patch.object(billing, 'price_for_model', lambda model=None: 0.37),
        ]
        for p in self._patchers:
            p.start()
        agent_auth._RATE_WINDOWS.clear()
        self._app = Flask(__name__)
        self._app_ctx = self._app.app_context()
        self._app_ctx.push()
        db.init_db()
        self.calls = []
        self.fail_modules = set()

    def tearDown(self):
        try:
            self._app_ctx.pop()
        except Exception:
            pass
        for p in reversed(self._patchers):
            p.stop()

    def _fake_generate(self, prompt, aspect_ratio='3:4', image_size='2K', images=None, model=None):
        self.calls.append({'prompt': prompt, 'ratio': aspect_ratio, 'images': len(images or []),
                           'model': model})
        for module_key in self.fail_modules:
            module = suite_service.MODULE_MAP[module_key]
            if module['name'] in prompt:
                raise RuntimeError('mock upstream 失败: %s' % module_key)
        return {'urls': ['http://mock/img-%d.png' % len(self.calls)],
                'model': model or 'banana-2.1', 'tasks': len(self.calls)}

    @staticmethod
    def _module_name_in(prompt, module_key):
        return module_key in prompt

    def seed_account(self, role='salesperson', balance_cents=100000):
        db.execute(
            """INSERT INTO accounts (username, nickname, password_hash, role, image_enabled, image_balance_cents)
               VALUES (?, ?, ?, ?, 1, ?)""",
            ('agentbot', 'AgentBot', 'x', role, balance_cents),
        )
        return int(db.query("SELECT id FROM accounts WHERE username='agentbot'", one=True)['id'])

    def payload(self, **overrides):
        data = {
            'request_id': 'row-0001-suite',
            'product_id': 'id_testproduct',
            'prompt': '黑色摩托车蓝牙耳机，500m 对讲，降噪，保留商品真实外观',
            'modules': ALL_MODULES,
            'reference_images': ['data:image/png;base64,AAAA', 'https://example.com/ref.jpg'],
            'model': 'banana-2.1',
            'aspect_ratio': '3:4',
            'image_size': '2K',
        }
        data.update(overrides)
        return data


class SuiteServiceTests(SuiteTestBase):
    def test_all_modules_success_and_billing(self):
        account_id = self.seed_account()
        result = suite_service.generate_suite(self.payload(), account_id)
        self.assertTrue(result['ok'])
        self.assertEqual(result['total'], 8)
        self.assertEqual(len([r for r in result['results'] if r['status'] == 'done']), 8)
        self.assertEqual(len(self.calls), 8)
        assets = db.query("SELECT * FROM gallery_assets")
        self.assertEqual(len(assets), 8)
        self.assertEqual({a['module_key'] for a in assets}, set(ALL_MODULES))
        for asset in assets:
            self.assertEqual(asset['request_id'], 'row-0001-suite')
            self.assertEqual(asset['product_id'], 'id_testproduct')
            self.assertEqual(asset['agent_source'], 'agent-suite')
            self.assertEqual(asset['template_version'], 'suite-v1')
            self.assertEqual(asset['user_id'], account_id)
        usage = db.query("SELECT * FROM image_usage")
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]['images'], 8)
        self.assertAlmostEqual(usage[0]['cost_points'], 8 * 0.37, places=4)
        self.assertAlmostEqual(result['points'], 8 * 0.37, places=4)
        balance = db.query("SELECT image_balance_cents FROM accounts WHERE id=?", (account_id,), one=True)
        self.assertEqual(balance['image_balance_cents'], 100000 - int(round(8 * 37)))

    def test_partial_failure_isolated(self):
        account_id = self.seed_account()
        payload = self.payload()
        payload['modules'] = ['hero', 'detail', 'size']
        self.fail_modules = {'detail'}
        result = suite_service.generate_suite(payload, account_id)
        self.assertFalse(result['ok'])
        self.assertEqual(result['total'], 3)
        by_module = {r['module']: r for r in result['results']}
        self.assertEqual(by_module['hero']['status'], 'done')
        self.assertEqual(by_module['size']['status'], 'done')
        self.assertEqual(by_module['detail']['status'], 'failed')
        self.assertIn('mock upstream', by_module['detail']['error'])
        self.assertEqual(len(db.query("SELECT * FROM gallery_assets")), 2)
        usage = db.query("SELECT * FROM image_usage")
        self.assertEqual(usage[0]['images'], 2)

    def test_idempotent_by_request_id(self):
        account_id = self.seed_account()
        first = suite_service.generate_suite(self.payload(), account_id)
        calls_after_first = len(self.calls)
        second = suite_service.generate_suite(self.payload(), account_id)
        self.assertTrue(second['ok'])
        self.assertEqual(len(self.calls), calls_after_first, '不应重复调用模型')
        self.assertEqual(len(db.query("SELECT * FROM gallery_assets")), 8)
        self.assertEqual(len(db.query("SELECT * FROM image_usage")), 1, '不应重复扣点')
        self.assertEqual(second['points'], 0.0)
        self.assertTrue(any('复用' in w for w in second['warnings']))
        self.assertEqual(first['request_id'], second['request_id'])

    def test_partial_retry_only_regenerates_missing(self):
        account_id = self.seed_account()
        payload = self.payload()
        payload['modules'] = ['hero', 'detail']
        self.fail_modules = {'detail'}
        suite_service.generate_suite(payload, account_id)
        self.fail_modules = set()
        calls_before = len(self.calls)
        result = suite_service.generate_suite(payload, account_id)
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.calls) - calls_before, 1, '只重生成失败的模块')
        self.assertEqual(len(db.query("SELECT * FROM gallery_assets")), 2)

    def test_unbound_account_rejected(self):
        with self.assertRaises(suite_service.AgentSuiteBillingError):
            suite_service.generate_suite(self.payload(), None)

    def test_billing_denied(self):
        account_id = self.seed_account(balance_cents=0)
        with self.assertRaises(suite_service.AgentSuiteBillingError):
            suite_service.generate_suite(self.payload(), account_id)
        self.assertEqual(len(self.calls), 0, '余额不足不应调用模型')

    def test_validation(self):
        cases = [
            {'request_id': ''},
            {'prompt': ''},
            {'modules': []},
            {'modules': ['bad_module']},
            {'reference_images': []},
            {'reference_images': ['x'] * 7},
            {'reference_images': ['notaurl']},
        ]
        account_id = self.seed_account()
        for overrides in cases:
            payload = self.payload(**overrides)
            with self.assertRaises(suite_service.AgentSuiteError, msg=str(overrides)):
                suite_service.generate_suite(payload, account_id)

    def test_prompt_template_matches_frontend(self):
        hero = suite_service.MODULE_MAP['hero']
        prompt = suite_service.build_suite_prompt(
            hero, '商品事实', '3:4', suite_service.fallback_plan(hero, '商品事实'))
        self.assertIn('当前只生成一个模块：首屏主视觉（Главное изображение）', prompt)
        self.assertIn('当前模块目标：清晰展示商品与核心价值', prompt)
        self.assertIn('当前模块俄语文案：Главное изображение\n可无文字或仅保留短俄语标题', prompt)
        self.assertIn('采用 Ozon 主图构造', prompt)
        self.assertIn('画布必须使用 3:4 比例', prompt)
        detail = suite_service.MODULE_MAP['detail']
        detail_prompt = suite_service.build_suite_prompt(
            detail, '商品事实', '1:1', suite_service.fallback_plan(detail, '商品事实'))
        self.assertIn('采用专业电商详情图构图', detail_prompt)


class SuiteRouteTests(SuiteTestBase):
    def setUp(self):
        super().setUp()
        from routes.agent_routes import agent_bp
        from routes.gallery_routes import gallery_bp

        app = Flask(__name__)
        app.before_request(agent_auth.agent_request_guard)
        app.before_request(require_api_auth)
        app.after_request(agent_auth.agent_audit_after_request)
        app.register_blueprint(agent_bp, url_prefix='/api')
        app.register_blueprint(gallery_bp, url_prefix='/api')
        self.client = app.test_client()
        self.account_id = self.seed_account()

    def _token(self, scopes='images:write,gallery:read', account_id=None):
        token = 'agent_suitetoken'
        db.execute(
            "INSERT INTO agent_tokens (name, token_hash, scopes, account_id, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            ('t', agent_auth.hash_token(token), scopes,
             self.account_id if account_id is None else account_id),
        )
        return token

    def test_suite_ok(self):
        token = self._token()
        payload = self.payload()
        payload['modules'] = ['hero', 'detail']
        resp = self.client.post('/api/agent/images/suite', headers={'X-Agent-Key': token}, json=payload)
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertTrue(body['data']['ok'])
        self.assertEqual(len(body['data']['results']), 2)

    def test_suite_wrong_scope_403(self):
        token = self._token(scopes='cards:write')
        resp = self.client.post('/api/agent/images/suite', headers={'X-Agent-Key': token},
                                json=self.payload())
        self.assertEqual(resp.get_json()['code'], 403)

    def test_suite_no_token_401(self):
        resp = self.client.post('/api/agent/images/suite', json=self.payload())
        self.assertEqual(resp.status_code, 401)

    def test_suite_unbound_account_403(self):
        token = self._token(account_id=0)
        resp = self.client.post('/api/agent/images/suite', headers={'X-Agent-Key': token},
                                json=self.payload())
        self.assertEqual(resp.get_json()['code'], 403)
        self.assertIn('计费账号', resp.get_json()['msg'])

    def test_gallery_filter_by_request_id(self):
        token = self._token()
        payload = self.payload()
        payload['modules'] = ['hero']
        self.client.post('/api/agent/images/suite', headers={'X-Agent-Key': token}, json=payload)
        resp = self.client.get('/api/gallery?request_id=row-0001-suite', headers={'X-Agent-Key': token})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertEqual(len(body['data']['list']), 1)
        self.assertEqual(body['data']['list'][0]['module_key'], 'hero')


class SuiteBillingOwnerTests(SuiteTestBase):
    """用户口径：测试阶段记管理员；正式按「哪个账号上的品就记哪个账号」。"""

    def _seed_account_named(self, username, nickname='', role='salesperson', balance_cents=100000):
        db.execute(
            """INSERT INTO accounts (username, nickname, password_hash, role, image_enabled, image_balance_cents)
               VALUES (?, ?, 'x', ?, 1, ?)""",
            (username, nickname, role, balance_cents),
        )
        return int(db.query("SELECT id FROM accounts WHERE username=?", (username,), one=True)['id'])

    def _seed_product(self, **fields):
        from models.product import Product
        data = {'title': '测试商品'}
        data.update(fields)
        return Product.create(data)

    def _payload_for(self, product_id, **overrides):
        payload = self.payload()
        payload['product_id'] = product_id
        payload['modules'] = ['hero']
        payload.update(overrides)
        return payload

    def setUp(self):
        super().setUp()
        self.products_path = os.path.join(self.tmpdir, 'products.json')
        open(self.products_path, 'w', encoding='utf-8').write('[]')
        from models.product import Product
        self._product_patcher = patch.object(Product, 'FILE_PATH', self.products_path)
        self._product_patcher.start()
        Product._cache = []
        Product._loaded = False

    def tearDown(self):
        self._product_patcher.stop()
        from models.product import Product
        Product._cache = []
        Product._loaded = False
        super().tearDown()

    def test_assignee_account_used(self):
        admin_id = self.seed_account(role='admin', balance_cents=0)
        seller_id = self._seed_account_named('lishuang', '李双')
        product = self._seed_product(assignee='李双')
        result = suite_service.generate_suite(self._payload_for(product['id']), admin_id)
        self.assertEqual(result['billing_account_id'], seller_id)
        usage = db.query('SELECT * FROM image_usage ORDER BY id DESC')
        self.assertEqual(int(usage[0]['account_id']), seller_id)
        assets = db.query('SELECT user_id FROM gallery_assets')
        self.assertEqual(int(assets[0]['user_id']), seller_id)

    def test_store_owner_account_used(self):
        admin_id = self.seed_account(role='admin', balance_cents=0)
        seller_id = self._seed_account_named('gby', '郭滨扬')
        db.execute("INSERT INTO stores (store_id, alias, account_id) VALUES ('999001', '测试店', ?)",
                   (seller_id,))
        store_pk = int(db.query("SELECT id FROM stores WHERE store_id='999001'", one=True)['id'])
        product = self._seed_product(storeId=store_pk)
        result = suite_service.generate_suite(self._payload_for(product['id']), admin_id)
        self.assertEqual(result['billing_account_id'], seller_id)

    def test_explicit_billing_account_wins(self):
        admin_id = self.seed_account(role='admin', balance_cents=0)
        other_id = self._seed_account_named('other', '别人')
        product = self._seed_product(assignee='不存在的业务员')
        result = suite_service.generate_suite(
            self._payload_for(product['id'], billing_account_id=other_id), admin_id)
        self.assertEqual(result['billing_account_id'], other_id)

    def test_fallback_to_token_account_when_unowned(self):
        admin_id = self.seed_account(role='admin', balance_cents=0)
        result = suite_service.generate_suite(self._payload_for('id_no_such_product'), admin_id)
        self.assertEqual(result['billing_account_id'], admin_id)
        usage = db.query('SELECT * FROM image_usage ORDER BY id DESC')
        self.assertEqual(int(usage[0]['account_id']), admin_id)


if __name__ == '__main__':
    unittest.main()
