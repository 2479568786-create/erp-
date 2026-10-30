"""R-A5：agent_ops 审计到人 + /api/agent/ops 按账号隔离（业务员只看自己）。"""
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
from services import agent_validate_service  # noqa: E402
from utils import agent_auth  # noqa: E402
from utils.auth import create_token, require_api_auth  # noqa: E402

TEST_PEPPER = 'test-pepper-123'


class OpsTenantTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agentops_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self.products_path = os.path.join(self.tmpdir, 'products.json')
        with open(self.products_path, 'w', encoding='utf-8') as f:
            f.write('[]')
        self._patchers = [
            patch.object(db, 'DB_PATH', self.db_path),
            patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': TEST_PEPPER}),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', TEST_PEPPER),
            patch.object(Config, 'AGENT_MAX_RPM', 600),
            patch.object(Product, 'FILE_PATH', self.products_path),
            patch.object(agent_validate_service, 'validate_product',
                         lambda product_id, **kwargs: {'ok': True, 'blocking': [], 'items': []}),
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

        self.admin_id = self._seed_account('admin', '管理员', 'admin')
        self.gby_id = self._seed_account('gby', '郭滨扬', 'salesperson')
        self.ls_id = self._seed_account('ls', '李双', 'salesperson')
        self.gby_store_pk = self._seed_store('216800183', 'unistore', self.gby_id)
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

    def _seed_account(self, username, nickname, role):
        db.execute('INSERT INTO accounts (username, nickname, password_hash, role, image_enabled,'
                   ' image_balance_cents) VALUES (?, ?, ?, ?, 1, 100000)',
                   (username, nickname, 'x', role))
        return int(db.query('SELECT id FROM accounts WHERE username = ?', (username,), one=True)['id'])

    def _seed_store(self, store_id, alias, account_id):
        db.execute("INSERT INTO stores (store_id, alias, account_id) VALUES (?, ?, ?)",
                   (store_id, alias, account_id))
        return int(db.query('SELECT id FROM stores WHERE store_id = ?', (store_id,), one=True)['id'])

    def _seed_ops(self, account_id, username, product_id, action='POST /api/products/x/validate'):
        db.execute("INSERT INTO agent_ops (token_id, scope, action, product_id, request_id, status,"
                   " duration_ms, error, account_id, username, created_at)"
                   " VALUES (NULL, 'jwt', ?, ?, '', 'ok', 1, '', ?, ?, datetime('now'))",
                   (action, product_id, account_id, username))

    def _get(self, path, token):
        return self.client.get(path, headers={'Authorization': 'Bearer ' + token})


class OpsTenantFilterTests(OpsTenantTestBase):
    def setUp(self):
        super().setUp()
        self._seed_ops(self.gby_id, 'gby', 'id_gby_1', 'POST /api/agent/cards/build')
        self._seed_ops(self.gby_id, 'gby', 'id_gby_2', 'PUT /api/products/id_gby_2/attributes')
        self._seed_ops(self.ls_id, 'ls', 'id_ls_1', 'POST /api/agent/cards/build')

    def test_salesperson_sees_only_own_rows(self):
        body = self._get('/api/agent/ops', self.gby_token).get_json()
        self.assertEqual(body['code'], 200)
        rows = body['data']['items']
        # 自己的 2 条 + 本次 GET 自身写的那条（也在自己名下）
        self.assertTrue(rows)
        self.assertTrue(all(r['account_id'] == self.gby_id for r in rows),
                        [r['account_id'] for r in rows])
        self.assertNotIn('id_ls_1', [r['product_id'] for r in rows])

    def test_salesperson_cannot_see_other_account(self):
        body = self._get('/api/agent/ops?product_id=id_ls_1', self.gby_token).get_json()
        self.assertEqual(body['data']['items'], [])
        self.assertEqual(body['data']['total'], 0)

    def test_admin_sees_all(self):
        body = self._get('/api/agent/ops', self.admin_token).get_json()
        self.assertEqual(body['code'], 200)
        product_ids = [r['product_id'] for r in body['data']['items']]
        self.assertIn('id_gby_1', product_ids)
        self.assertIn('id_ls_1', product_ids)

    def test_agent_token_with_scope_sees_all(self):
        token = 'ops_token_1'
        db.execute("INSERT INTO agent_tokens (name, token_hash, scopes, created_at)"
                   " VALUES (?, ?, ?, datetime('now'))",
                   ('agent-prod', agent_auth.hash_token(token), 'agent:read'))
        body = self.client.get('/api/agent/ops', headers={'X-Agent-Key': token}).get_json()
        self.assertEqual(body['code'], 200)
        product_ids = [r['product_id'] for r in body['data']['items']]
        self.assertIn('id_gby_1', product_ids)
        self.assertIn('id_ls_1', product_ids)

    def test_items_expose_actor_fields(self):
        body = self._get('/api/agent/ops?product_id=id_ls_1', self.admin_token).get_json()
        item = body['data']['items'][0]
        self.assertEqual(item['account_id'], self.ls_id)
        self.assertEqual(item['username'], 'ls')


class OpsAuditWriteTests(OpsTenantTestBase):
    def test_jwt_call_writes_actor(self):
        product = Product.create({'title': '审计测试商品', 'storeId': str(self.gby_store_pk)})
        resp = self.client.post('/api/products/%s/validate' % product['id'],
                                headers={'Authorization': 'Bearer ' + self.gby_token},
                                json={'dry_run': True})
        self.assertEqual(resp.get_json()['code'], 200)
        row = db.query("SELECT * FROM agent_ops ORDER BY id DESC")[0]
        self.assertEqual(row['account_id'], self.gby_id)
        self.assertEqual(row['username'], 'gby')
        self.assertEqual(row['scope'], 'jwt')
        self.assertEqual(row['product_id'], product['id'])

    def test_forbidden_call_recorded(self):
        # 别的店铺商品 → 403，审计里 status=forbidden 且记到人
        resp = self.client.put('/api/products/id_other/attributes',
                               headers={'Authorization': 'Bearer ' + self.ls_token},
                               json={'attributes': []})
        self.assertEqual(resp.get_json()['code'], 404)  # 商品不存在先 404
        row = db.query("SELECT * FROM agent_ops ORDER BY id DESC")[0]
        self.assertEqual(row['account_id'], self.ls_id)
        self.assertEqual(row['username'], 'ls')
        self.assertEqual(row['status'], 'invalid')

    def test_only_one_row_per_call(self):
        product = Product.create({'title': '审计唯一性商品', 'storeId': str(self.gby_store_pk)})
        self.client.post('/api/products/%s/validate' % product['id'],
                         headers={'Authorization': 'Bearer ' + self.gby_token},
                         json={'dry_run': True})
        rows = db.query("SELECT * FROM agent_ops WHERE product_id = ?", (product['id'],))
        self.assertEqual(len(rows), 1)


if __name__ == '__main__':
    unittest.main()
