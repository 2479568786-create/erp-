"""R1 Agent 服务令牌鉴权：单测（不连真实 Ozon/AI，全部本地临时库）。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault('AGENT_TOKEN_PEPPER', 'test-pepper-123')

from flask import Flask, jsonify  # noqa: E402

import db  # noqa: E402
from config import Config  # noqa: E402
from utils import agent_auth  # noqa: E402
from utils.auth import require_api_auth  # noqa: E402

TEST_PEPPER = 'test-pepper-123'


class AgentAuthTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agent_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self._patchers = [
            patch.object(db, 'DB_PATH', self.db_path),
            patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': TEST_PEPPER}),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', TEST_PEPPER),
            patch.object(Config, 'AGENT_MAX_RPM', 60),
        ]
        for p in self._patchers:
            p.start()
        agent_auth._RATE_WINDOWS.clear()
        # error_response 内部用 jsonify，需要在应用上下文里运行
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
        agent_auth._RATE_WINDOWS.clear()

    def insert_token(self, token='agent_testtoken', scopes='products:read', revoked=0, expires_at=None):
        db.execute(
            "INSERT INTO agent_tokens (name, token_hash, scopes, created_at, expires_at, revoked) "
            "VALUES (?, ?, ?, datetime('now'), ?, ?)",
            ('test', agent_auth.hash_token(token), scopes, expires_at, revoked),
        )
        return token


class SchemaTests(AgentAuthTestBase):
    def test_agent_tables_exist(self):
        tables = {r['name'] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('agent_tokens', tables)
        self.assertIn('agent_ops', tables)

    def test_gallery_agent_columns(self):
        cols = {r['name'] for r in db.query("PRAGMA table_info(gallery_assets)")}
        for name in ('product_id', 'module_key', 'request_id', 'agent_source'):
            self.assertIn(name, cols)

    def test_migration_idempotent(self):
        db.init_db()
        db.init_db()
        cols = {r['name'] for r in db.query("PRAGMA table_info(gallery_assets)")}
        self.assertIn('module_key', cols)


class HashTests(AgentAuthTestBase):
    def test_hash_is_stable_and_salted(self):
        h1 = agent_auth.hash_token('abc')
        h2 = agent_auth.hash_token('abc')
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)
        with patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': 'other-pepper'}):
            self.assertNotEqual(agent_auth.hash_token('abc'), h1)

    def test_hash_requires_pepper(self):
        with patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': ''}):
            with patch.object(Config, 'AGENT_TOKEN_PEPPER', ''):
                with self.assertRaises(RuntimeError):
                    agent_auth.hash_token('abc')
                self.assertFalse(agent_auth.pepper_configured())


class ScopeMapTests(unittest.TestCase):
    def test_required_scope(self):
        cases = [
            ('POST', '/api/products/collect', 'products:write'),
            ('GET', '/api/products/id_abc', 'products:read'),
            ('PUT', '/api/products/id_abc', 'products:write'),
            ('POST', '/api/products/id_abc/images/attach', 'images:write'),
            ('PUT', '/api/products/id_abc/attributes', 'products:write'),
            ('POST', '/api/products/id_abc/validate', 'products:read'),
            ('POST', '/api/product-cards/generate', 'cards:write'),
            ('POST', '/api/product-cards/12/activate', 'cards:write'),
            ('POST', '/api/agent/cards/build', 'cards:write'),
            ('POST', '/api/agent/images/suite', 'images:write'),
            ('GET', '/api/gallery', 'gallery:read'),
            ('POST', '/api/publish', 'publish:write'),
            ('GET', '/api/publish/task1/status', 'publish:write'),
            ('GET', '/api/categories/attributes', 'categories:read'),
        ]
        for method, path, expected in cases:
            self.assertEqual(agent_auth.required_scope(method, path), expected, (method, path))
        self.assertIsNone(agent_auth.required_scope('POST', '/api/publish-records'))
        self.assertIsNone(agent_auth.required_scope('GET', '/api/products'))
        self.assertIsNone(agent_auth.required_scope('DELETE', '/api/products/x'))


class LookupTests(AgentAuthTestBase):
    def test_valid_token(self):
        token = self.insert_token(scopes='products:read,publish:write')
        row, err = agent_auth.lookup_token(token)
        self.assertIsNone(err)
        self.assertEqual(row['name'], 'test')
        self.assertTrue(agent_auth.has_scope(row, 'publish:write'))
        self.assertFalse(agent_auth.has_scope(row, 'cards:write'))
        # last_used_at 已写
        fresh = db.query("SELECT last_used_at FROM agent_tokens WHERE id = ?", (row['id'],), one=True)
        self.assertTrue(fresh['last_used_at'])

    def test_plaintext_not_stored(self):
        token = self.insert_token()
        rows = db.query("SELECT token_hash FROM agent_tokens")
        self.assertEqual(len(rows), 1)
        self.assertNotIn(token, rows[0]['token_hash'])
        self.assertEqual(rows[0]['token_hash'], agent_auth.hash_token(token))
        self.assertEqual(len(rows[0]['token_hash']), 64)

    def test_wrong_token(self):
        self.insert_token()
        row, err = agent_auth.lookup_token('agent_wrong')
        self.assertIsNone(row)
        self.assertEqual(err[0].get_json()['code'], 401)

    def test_revoked(self):
        token = self.insert_token(revoked=1)
        row, err = agent_auth.lookup_token(token)
        self.assertIsNone(row)
        self.assertEqual(err[0].get_json()['code'], 401)

    def test_expired(self):
        token = self.insert_token(expires_at='2000-01-01 00:00:00')
        row, err = agent_auth.lookup_token(token)
        self.assertIsNone(row)
        self.assertEqual(err[0].get_json()['code'], 401)

    def test_future_expiry_ok(self):
        token = self.insert_token(expires_at='2999-01-01 00:00:00')
        row, err = agent_auth.lookup_token(token)
        self.assertIsNone(err)
        self.assertIsNotNone(row)

    def test_rate_limit(self):
        token = self.insert_token()
        with patch.object(Config, 'AGENT_MAX_RPM', 2):
            agent_auth._RATE_WINDOWS.clear()
            self.assertIsNone(agent_auth.lookup_token(token)[1])
            self.assertIsNone(agent_auth.lookup_token(token)[1])
            _, err = agent_auth.lookup_token(token)
            self.assertIsNotNone(err)
            self.assertEqual(err[0].get_json()['code'], 429)

    def test_no_pepper_is_fail_closed(self):
        token = self.insert_token()
        with patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': ''}):
            with patch.object(Config, 'AGENT_TOKEN_PEPPER', ''):
                row, err = agent_auth.lookup_token(token)
                self.assertIsNone(row)
                self.assertEqual(err[0].get_json()['code'], 503)


class EndpointGuardTests(AgentAuthTestBase):
    """用最小 Flask 应用验证 before_request 协同：JWT 不变、Agent 令牌可用、scope 生效。"""

    def setUp(self):
        super().setUp()
        app = Flask(__name__)
        app.before_request(agent_auth.agent_request_guard)
        app.before_request(require_api_auth)
        app.after_request(agent_auth.agent_audit_after_request)

        @app.route('/api/products/<pid>', methods=['GET'])
        def get_product(pid):
            return jsonify({'code': 200, 'data': {'id': pid, 'by': 'agent' if agent_auth.current_agent() else 'jwt'}})

        @app.route('/api/products/collect', methods=['POST'])
        def collect():
            return jsonify({'code': 200, 'data': {'ok': True}})

        self.client = app.test_client()

    def _seed_user(self, role='admin'):
        db.execute(
            "INSERT INTO accounts (username, nickname, password_hash, role) VALUES (?, ?, ?, ?)",
            ('u1', 'U1', 'x', role),
        )
        user = db.query("SELECT * FROM accounts WHERE username = 'u1'", one=True)
        db.execute(
            "INSERT INTO auth_tokens (token, account_id, expires_at) VALUES (?, ?, ?)",
            ('jwt-token-1', user['id'], 4102444800),
        )

    def test_no_token_401(self):
        resp = self.client.get('/api/products/id_x')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.get_json()['code'], 401)

    def test_agent_token_ok(self):
        token = self.insert_token(scopes='products:read')
        resp = self.client.get('/api/products/id_x', headers={'X-Agent-Key': token})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertEqual(body['data']['by'], 'agent')

    def test_bearer_agent_token_ok(self):
        token = self.insert_token(scopes='products:write')
        resp = self.client.post('/api/products/collect',
                                headers={'Authorization': f'Bearer {token}'},
                                json={'title': 'x'})
        self.assertEqual(resp.get_json()['code'], 200)

    def test_missing_scope_403(self):
        token = self.insert_token(scopes='products:read')
        resp = self.client.post('/api/products/collect', headers={'X-Agent-Key': token}, json={})
        self.assertEqual(resp.get_json()['code'], 403)

    def test_bad_token_401(self):
        self.insert_token()
        resp = self.client.get('/api/products/id_x', headers={'X-Agent-Key': 'agent_nope'})
        self.assertEqual(resp.get_json()['code'], 401)

    def test_jwt_still_works(self):
        self._seed_user()
        resp = self.client.get('/api/products/id_x', headers={'Authorization': 'Bearer jwt-token-1'})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertEqual(body['data']['by'], 'jwt')

    def test_non_whitelisted_path_unaffected(self):
        # 未列入白名单的路径：Agent 令牌不生效，仍要求 JWT
        resp = self.client.get('/api/products', headers={'X-Agent-Key': self.insert_token()})
        self.assertEqual(resp.get_json()['code'], 401)

    def test_audit_written(self):
        token = self.insert_token(scopes='products:read')
        self.client.get('/api/products/id_x', headers={'X-Agent-Key': token})
        rows = db.query("SELECT * FROM agent_ops")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'ok')
        self.assertIn('/api/products/id_x', rows[0]['action'])
        self.assertEqual(rows[0]['scope'], 'products:read')


if __name__ == '__main__':
    unittest.main()
