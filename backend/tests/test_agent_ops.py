"""R9 审计查询：/api/agent/ops + 限流收尾验证。"""
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
from utils import agent_auth  # noqa: E402
from utils.auth import require_api_auth  # noqa: E402


class OpsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agentops_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self._patchers = [
            patch.object(db, 'DB_PATH', self.db_path),
            patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': 'test-pepper-123'}),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', 'test-pepper-123'),
            patch.object(Config, 'AGENT_MAX_RPM', 600),
        ]
        for p in self._patchers:
            p.start()
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

    def seed_ops(self, product_id='id_p1', scope='cards:write', request_id='req-1', status='ok', count=3):
        for index in range(count):
            db.execute(
                """INSERT INTO agent_ops
                   (token_id, scope, action, product_id, request_id, status, duration_ms, error, created_at)
                   VALUES (1, ?, 'POST /x', ?, ?, ?, 5, '', datetime('now'))""",
                (scope, product_id, request_id if index == 0 else '', status),
            )


class OpsRouteTests(OpsTestBase):
    def setUp(self):
        super().setUp()
        from routes.agent_routes import agent_bp

        app = Flask(__name__)
        app.before_request(agent_auth.agent_request_guard)
        app.before_request(require_api_auth)
        app.after_request(agent_auth.agent_audit_after_request)
        app.register_blueprint(agent_bp, url_prefix='/api')
        self.client = app.test_client()

    def _token(self, scopes='agent:read'):
        token = 'agent_opstoken'
        db.execute(
            "INSERT INTO agent_tokens (name, token_hash, scopes, created_at) VALUES (?, ?, ?, datetime('now'))",
            ('t', agent_auth.hash_token(token), scopes),
        )
        return token

    def test_list_all(self):
        self.seed_ops(count=3)
        token = self._token()
        resp = self.client.get('/api/agent/ops', headers={'X-Agent-Key': token})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        # 3 条种子 + 本次调用的审计（after_request 会在响应后写，这里只断言 >=3）
        self.assertGreaterEqual(body['data']['total'], 3)

    def test_filter_product_id(self):
        self.seed_ops(product_id='id_p1', count=2)
        self.seed_ops(product_id='id_p2', count=1)
        token = self._token()
        resp = self.client.get('/api/agent/ops?product_id=id_p1', headers={'X-Agent-Key': token})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertTrue(all(item['product_id'] == 'id_p1' for item in body['data']['items']))

    def test_filter_status_and_limit(self):
        self.seed_ops(status='ok', count=2)
        self.seed_ops(status='invalid', count=1)
        token = self._token()
        resp = self.client.get('/api/agent/ops?status=invalid&limit=1', headers={'X-Agent-Key': token})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertEqual(len(body['data']['items']), 1)
        self.assertEqual(body['data']['items'][0]['status'], 'invalid')

    def test_wrong_scope_403(self):
        token = self._token(scopes='cards:write')
        resp = self.client.get('/api/agent/ops', headers={'X-Agent-Key': token})
        self.assertEqual(resp.get_json()['code'], 403)

    def test_no_token_401(self):
        resp = self.client.get('/api/agent/ops')
        self.assertEqual(resp.status_code, 401)

    def test_limit_capped(self):
        token = self._token()
        resp = self.client.get('/api/agent/ops?limit=9999', headers={'X-Agent-Key': token})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertEqual(body['data']['filters']['limit'], 500)


class RateLimitTests(OpsTestBase):
    def test_rate_limit_returns_429(self):
        token = self._token = 'agent_ratetoken'
        db.execute(
            "INSERT INTO agent_tokens (name, token_hash, scopes, created_at) VALUES (?, ?, ?, datetime('now'))",
            ('t', agent_auth.hash_token(token), 'agent:read'),
        )
        with patch.object(Config, 'AGENT_MAX_RPM', 2):
            agent_auth._RATE_WINDOWS.clear()
            self.assertIsNone(agent_auth.lookup_token(token)[1])
            self.assertIsNone(agent_auth.lookup_token(token)[1])
            _, err = agent_auth.lookup_token(token)
            self.assertEqual(err[0].get_json()['code'], 429)


if __name__ == '__main__':
    unittest.main()
