"""方案 A：业务员 JWT 直接调 Agent 上架接口（R-A1~R-A4 店铺级隔离）。

覆盖：
- R-A1 业务员/操作员 JWT 放行，其它角色仍 403、未登录 401
- R-A2 建卡必须指定自己店铺；跨店/缺 store_id/店铺不存在 → 403 且不落商品
- R-A3 商品级接口（attributes/attach/validate/lint）跨店 403、自己店 200
- R-A4 套图强制计费账号 = 商品所属店铺；显式 billing_account_id 覆盖 → 403
- R-A6 admin JWT 与 Agent 令牌行为不变

全部外部依赖 mock（不连 Ozon / 凌科 / 真实文件存储）。
"""
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
from services import (agent_attributes_service, agent_card_service,  # noqa: E402
                      agent_images_service, agent_validate_service, copy_lint,
                      suite_service)
from utils import agent_auth  # noqa: E402
from utils.auth import create_token, require_api_auth  # noqa: E402

TEST_PEPPER = 'test-pepper-123'

FAKE_CARD = {
    'id': 501,
    'product_id': 'PLACEHOLDER',
    'version': 1,
    'status': 'GENERATED',
    'title_ru': 'Русский заголовок товара',
    'description_ru': 'Описание товара для Ozon.',
    'main_tags_ru': ['#тег1'],
    'long_tail_tags_ru': [],
    'auxiliary_tags_ru': [],
}


class ScopeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agentscope_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self.products_path = os.path.join(self.tmpdir, 'products.json')
        with open(self.products_path, 'w', encoding='utf-8') as f:
            f.write('[]')
        self.suite_calls = []
        self._patchers = [
            patch.object(db, 'DB_PATH', self.db_path),
            patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': TEST_PEPPER}),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', TEST_PEPPER),
            patch.object(Config, 'AGENT_MAX_RPM', 600),
            patch.object(Product, 'FILE_PATH', self.products_path),
            patch.object(agent_card_service.card_service, 'generate_card', self._fake_generate),
            patch.object(agent_card_service.card_service, 'activate_card', self._fake_activate),
            patch.object(agent_card_service.card_service, 'get_card', self._fake_get_card),
            patch.object(agent_card_service.card_service, 'get_current_card', self._fake_current),
            patch.object(agent_images_service, 'attach_images', self._fake_attach),
            patch.object(agent_attributes_service, 'apply_attributes', self._fake_attributes),
            patch.object(agent_validate_service, 'validate_product', self._fake_validate),
            patch.object(copy_lint, 'lint_target', self._fake_lint),
            patch.object(suite_service, 'generate_suite', self._fake_suite),
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

        # 账号：管理员 1、郭滨扬 8（unistore/好物推荐官）、李双 7（QING Store）
        self.admin_id = self._seed_account('admin', '管理员', 'admin')
        self.gby_id = self._seed_account('gby', '郭滨扬', 'salesperson')
        self.ls_id = self._seed_account('ls', '李双', 'salesperson')
        self.unistore_pk = self._seed_store('216800183', 'unistore', self.gby_id)
        self.haowu_pk = self._seed_store('216800184', '好物推荐官', self.gby_id)
        self.qing_pk = self._seed_store('216800185', 'QING Store', self.ls_id)

        self.admin_token = create_token({'id': self.admin_id})
        self.gby_token = create_token({'id': self.gby_id})
        self.ls_token = create_token({'id': self.ls_id})

        from routes.agent_routes import agent_bp

        app = Flask(__name__)
        app.before_request(agent_auth.agent_request_guard)
        app.before_request(require_api_auth)
        app.after_request(agent_auth.agent_audit_after_request)
        app.register_blueprint(agent_bp, url_prefix='/api')
        self.client = app.test_client()

    def tearDown(self):
        try:
            self._app_ctx.pop()
        except Exception:
            pass
        for p in reversed(self._patchers):
            p.stop()
        Product._cache = []
        Product._loaded = False

    # ---- 数据构造 ----
    def _seed_account(self, username, nickname, role):
        db.execute(
            'INSERT INTO accounts (username, nickname, password_hash, role, image_enabled,'
            ' image_balance_cents) VALUES (?, ?, ?, ?, 1, 100000)',
            (username, nickname, 'x', role),
        )
        row = db.query('SELECT id FROM accounts WHERE username = ?', (username,), one=True)
        return int(row['id'])

    def _seed_store(self, store_id, alias, account_id):
        db.execute("INSERT INTO stores (store_id, alias, account_id) VALUES (?, ?, ?)",
                   (store_id, alias, account_id))
        row = db.query('SELECT id FROM stores WHERE store_id = ?', (store_id,), one=True)
        return int(row['id'])

    def _seed_product(self, store_ref):
        product = Product.create({'title': '测试商品', 'storeId': str(store_ref)})
        return product['id']

    # ---- mocks ----
    def _fake_generate(self, product_id, raw_selling_points=None, base_card_id=None, **kwargs):
        card = dict(FAKE_CARD)
        card['product_id'] = product_id
        return card

    def _fake_activate(self, card_id):
        card = dict(FAKE_CARD)
        card['id'] = card_id
        card['status'] = 'EDITED'
        return card

    def _fake_get_card(self, card_id):
        card = dict(FAKE_CARD)
        card['id'] = card_id
        return card

    def _fake_current(self, product_id):
        card = dict(FAKE_CARD)
        card['product_id'] = product_id
        return card

    def _fake_attach(self, *args, **kwargs):
        return {'count': 1, 'images': ['https://example.com/a.jpg']}

    def _fake_attributes(self, *args, **kwargs):
        return {'applied': [{'attr_id': 4389}], 'missing_required': []}

    def _fake_validate(self, product_id, **kwargs):
        return {'ok': True, 'blocking': [], 'items': []}

    def _fake_lint(self, target, payload=None):
        text = str(target or '')
        return {'ok': True, 'violations': [], 'sanitized': {}, 'source': 'product',
                'product_id': text if text.startswith('id_') else ''}

    def _fake_suite(self, payload, account_id):
        self.suite_calls.append({'payload': dict(payload), 'account_id': account_id})
        return {'request_id': payload.get('request_id') or 'rid', 'ok': True, 'total': 1,
                'results': [{'module': 'hero', 'status': 'done'}], 'points': 0.2,
                'billing_account_id': account_id, 'warnings': []}

    # ---- 请求封装 ----
    def _headers(self, token):
        return {'Authorization': 'Bearer ' + token}

    def _build_payload(self, **overrides):
        data = {
            'external_id': 'scope-row-0001',
            'title': '摩托车蓝牙耳机 500m 对讲',
            'apply_card': False,
        }
        data.update(overrides)
        return data

    def _post(self, path, token=None, json=None):
        headers = self._headers(token) if token else {}
        return self.client.post(path, headers=headers, json=json or {})

    def _put(self, path, token=None, json=None):
        headers = self._headers(token) if token else {}
        return self.client.put(path, headers=headers, json=json or {})

    def _get(self, path, token=None):
        headers = self._headers(token) if token else {}
        return self.client.get(path, headers=headers)


class BuildCardScopeTests(ScopeTestBase):
    """R-A1 + R-A2：建卡店铺归属。"""

    def test_salesperson_build_own_store_ok(self):
        resp = self._post('/api/agent/cards/build', self.gby_token,
                          self._build_payload(store_id=self.unistore_pk))
        body = resp.get_json()
        self.assertEqual(body['code'], 200, body)
        product_id = body['data']['product_id']
        product = Product.find_by_id(product_id)
        self.assertEqual(str(product['storeId']), str(self.unistore_pk))

    def test_salesperson_build_accepts_ozon_store_id(self):
        resp = self._post('/api/agent/cards/build', self.gby_token,
                          self._build_payload(external_id='scope-row-ozon', store_id='216800183'))
        self.assertEqual(resp.get_json()['code'], 200)

    def test_salesperson_build_other_store_403(self):
        resp = self._post('/api/agent/cards/build', self.gby_token,
                          self._build_payload(store_id=self.qing_pk))
        self.assertEqual(resp.get_json()['code'], 403)
        self.assertEqual(Product.find_all(), [], '越权建卡不应留下商品')

    def test_salesperson_build_missing_store_403(self):
        resp = self._post('/api/agent/cards/build', self.gby_token, self._build_payload())
        self.assertEqual(resp.get_json()['code'], 403)
        self.assertIn('store_id', resp.get_json()['msg'])
        self.assertEqual(Product.find_all(), [])

    def test_salesperson_build_unknown_store_403(self):
        resp = self._post('/api/agent/cards/build', self.gby_token,
                          self._build_payload(store_id=999999))
        self.assertEqual(resp.get_json()['code'], 403)

    def test_salesperson_idempotent_on_other_store_external_id_403(self):
        # 先用李双身份建卡，再让郭滨扬用同一 external_id → 403，且不泄漏商品
        first = self._post('/api/agent/cards/build', self.ls_token,
                           self._build_payload(external_id='shared-row', store_id=self.qing_pk))
        self.assertEqual(first.get_json()['code'], 200)
        second = self._post('/api/agent/cards/build', self.gby_token,
                            self._build_payload(external_id='shared-row', store_id=self.unistore_pk))
        self.assertEqual(second.get_json()['code'], 403)

    def test_admin_build_without_store_unchanged(self):
        resp = self._post('/api/agent/cards/build', self.admin_token,
                          self._build_payload(external_id='admin-row'))
        self.assertEqual(resp.get_json()['code'], 200)

    def test_agent_token_build_unaffected(self):
        token = 'scope_token_1'
        db.execute("INSERT INTO agent_tokens (name, token_hash, scopes, created_at)"
                   " VALUES (?, ?, ?, datetime('now'))",
                   ('t', agent_auth.hash_token(token), 'cards:write'))
        resp = self.client.post('/api/agent/cards/build', headers={'X-Agent-Key': token},
                                json=self._build_payload(external_id='token-row'))
        self.assertEqual(resp.get_json()['code'], 200)

    def test_unauthenticated_401(self):
        resp = self.client.post('/api/agent/cards/build', json=self._build_payload())
        self.assertEqual(resp.status_code, 401)


class ProductScopeTests(ScopeTestBase):
    """R-A3：商品级接口跨店 403 / 自己店 200。"""

    def setUp(self):
        super().setUp()
        self.own_pid = self._seed_product(self.unistore_pk)
        self.other_pid = self._seed_product(self.qing_pk)

    def test_attributes_own_ok(self):
        resp = self._put('/api/products/%s/attributes' % self.own_pid, self.gby_token,
                         {'attributes': []})
        self.assertEqual(resp.get_json()['code'], 200)

    def test_attributes_cross_store_403(self):
        resp = self._put('/api/products/%s/attributes' % self.other_pid, self.gby_token,
                         {'attributes': []})
        self.assertEqual(resp.get_json()['code'], 403)

    def test_attributes_missing_product_404(self):
        resp = self._put('/api/products/id_doesnotexist/attributes', self.gby_token,
                         {'attributes': []})
        self.assertEqual(resp.get_json()['code'], 404)

    def test_attach_own_ok(self):
        resp = self._post('/api/products/%s/images/attach' % self.own_pid, self.gby_token,
                          {'asset_ids': [1]})
        self.assertEqual(resp.get_json()['code'], 200)

    def test_attach_cross_store_403(self):
        resp = self._post('/api/products/%s/images/attach' % self.other_pid, self.gby_token,
                          {'asset_ids': [1]})
        self.assertEqual(resp.get_json()['code'], 403)

    def test_validate_own_ok(self):
        resp = self._post('/api/products/%s/validate' % self.own_pid, self.gby_token,
                          {'dry_run': True})
        self.assertEqual(resp.get_json()['code'], 200)

    def test_validate_cross_store_403(self):
        resp = self._post('/api/products/%s/validate' % self.other_pid, self.gby_token,
                          {'dry_run': True})
        self.assertEqual(resp.get_json()['code'], 403)

    def test_lint_own_product_ok(self):
        resp = self._post('/api/agent/cards/%s/lint' % self.own_pid, self.gby_token, {})
        self.assertEqual(resp.get_json()['code'], 200)

    def test_lint_cross_store_403(self):
        resp = self._post('/api/agent/cards/%s/lint' % self.other_pid, self.gby_token, {})
        self.assertEqual(resp.get_json()['code'], 403)

    def test_lint_payload_text_allowed(self):
        resp = self._post('/api/agent/cards/_/lint', self.gby_token,
                          {'title_ru': 'Чистый товар', 'tags': ['#товар']})
        self.assertEqual(resp.get_json()['code'], 200)

    def test_admin_cross_store_unchanged(self):
        resp = self._post('/api/products/%s/validate' % self.other_pid, self.admin_token,
                          {'dry_run': True})
        self.assertEqual(resp.get_json()['code'], 200)


class SuiteBillingScopeTests(ScopeTestBase):
    """R-A4：套图计费账号强制为商品所属店铺。"""

    def setUp(self):
        super().setUp()
        self.own_pid = self._seed_product(self.unistore_pk)
        self.other_pid = self._seed_product(self.qing_pk)

    def _suite_payload(self, **overrides):
        data = {
            'request_id': 'scope-suite-1',
            'product_id': self.own_pid,
            'prompt': '黑色蓝牙耳机',
            'modules': ['hero'],
        }
        data.update(overrides)
        return data

    def test_salesperson_suite_forces_store_account(self):
        resp = self._post('/api/agent/images/suite', self.gby_token, self._suite_payload())
        self.assertEqual(resp.get_json()['code'], 200)
        self.assertEqual(len(self.suite_calls), 1)
        self.assertEqual(self.suite_calls[0]['account_id'], self.gby_id)
        self.assertEqual(self.suite_calls[0]['payload'].get('billing_account_id'), self.gby_id)

    def test_salesperson_suite_billing_override_403(self):
        resp = self._post('/api/agent/images/suite', self.gby_token,
                          self._suite_payload(billing_account_id=self.admin_id))
        self.assertEqual(resp.get_json()['code'], 403)
        self.assertEqual(self.suite_calls, [])

    def test_salesperson_suite_same_billing_allowed(self):
        resp = self._post('/api/agent/images/suite', self.gby_token,
                          self._suite_payload(billing_account_id=self.gby_id))
        self.assertEqual(resp.get_json()['code'], 200)

    def test_salesperson_suite_other_store_product_403(self):
        resp = self._post('/api/agent/images/suite', self.gby_token,
                          self._suite_payload(product_id=self.other_pid))
        self.assertEqual(resp.get_json()['code'], 403)
        self.assertEqual(self.suite_calls, [])

    def test_salesperson_suite_requires_product_id(self):
        payload = self._suite_payload()
        payload.pop('product_id')
        resp = self._post('/api/agent/images/suite', self.gby_token, payload)
        self.assertEqual(resp.get_json()['code'], 403)

    def test_admin_suite_explicit_billing_unchanged(self):
        # admin 路径：路由不解析 billing，原样把 payload 交给 generate_suite（服务层解析），行为不变
        resp = self._post('/api/agent/images/suite', self.admin_token,
                          self._suite_payload(billing_account_id=self.ls_id))
        self.assertEqual(resp.get_json()['code'], 200)
        self.assertEqual(self.suite_calls[0]['payload'].get('billing_account_id'), self.ls_id)
        self.assertIsNone(self.suite_calls[0]['account_id'])


class AuditActorTests(ScopeTestBase):
    """R-A5 的前半：JWT 调用写 account_id/username；Agent 令牌写 agent:<name>。"""

    def test_jwt_audit_records_actor(self):
        self._post('/api/agent/cards/build', self.gby_token,
                   self._build_payload(store_id=self.unistore_pk))
        rows = db.query("SELECT * FROM agent_ops ORDER BY id DESC")
        self.assertEqual(len(rows), 1, '每次调用只应写一条审计')
        row = rows[0]
        self.assertEqual(row['account_id'], self.gby_id)
        self.assertEqual(row['username'], 'gby')
        self.assertEqual(row['scope'], 'jwt')
        self.assertEqual(row['action'], 'POST /api/agent/cards/build')

    def test_agent_token_audit_records_token_name(self):
        token = 'scope_token_2'
        db.execute("INSERT INTO agent_tokens (name, token_hash, scopes, created_at)"
                   " VALUES (?, ?, ?, datetime('now'))",
                   ('agent-prod', agent_auth.hash_token(token), 'cards:write'))
        self.client.post('/api/agent/cards/build', headers={'X-Agent-Key': token},
                         json=self._build_payload(external_id='token-audit-row'))
        row = db.query("SELECT * FROM agent_ops ORDER BY id DESC")[0]
        self.assertEqual(row['username'], 'agent:agent-prod')
        self.assertIsNone(row['account_id'])


if __name__ == '__main__':
    unittest.main()
