# -*- coding: utf-8 -*-
"""R2-Fix3：Agent 长请求的上游超时/中断统一返回 JSON（502 + retryable），500 带 retryable=false。"""
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
from routes.agent_routes import agent_bp  # noqa: E402
from services import agent_card_service  # noqa: E402
from utils import agent_auth  # noqa: E402
from utils.auth import require_api_auth  # noqa: E402

TEST_PEPPER = 'test-pepper-123'


class AgentErrorTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agenterr_')
        self._patchers = [
            patch.object(db, 'DB_PATH', os.path.join(self.tmpdir, 'test.db')),
            patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': TEST_PEPPER}),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', TEST_PEPPER),
            patch.object(Config, 'AGENT_MAX_RPM', 600),
        ]
        for p in self._patchers:
            p.start()
        agent_auth._RATE_WINDOWS.clear()
        db.init_db()
        app = Flask(__name__)
        app.before_request(agent_auth.agent_request_guard)
        app.before_request(require_api_auth)
        app.after_request(agent_auth.agent_audit_after_request)
        app.register_blueprint(agent_bp, url_prefix='/api')
        self.client = app.test_client()
        app_ctx = app.app_context()
        app_ctx.push()
        self._app_ctx = app_ctx

    def tearDown(self):
        try:
            self._app_ctx.pop()
        except Exception:
            pass
        for p in reversed(self._patchers):
            p.stop()

    def _token(self, scopes='cards:write,images:write'):
        token = 't-' + os.urandom(4).hex()
        db.execute(
            "INSERT INTO agent_tokens (name, token_hash, scopes, created_at) VALUES (?, ?, ?, datetime('now'))",
            ('t', agent_auth.hash_token(token), scopes),
        )
        return token

    def _post_build(self, token):
        return self.client.post('/api/agent/cards/build', headers={'X-Agent-Key': token},
                                json={'external_id': 'err-1', 'title': 'x'})

    def test_upstream_timeout_returns_502_retryable(self):
        token = self._token()
        with patch.object(agent_card_service, 'build_card',
                          side_effect=TimeoutError('upstream read timed out')):
            resp = self._post_build(token)
        body = resp.get_json()
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(body['code'], 502)
        self.assertTrue(body['retryable'])
        self.assertIn('重试', body['msg'])

    def test_connection_reset_returns_502_retryable(self):
        token = self._token()
        with patch.object(agent_card_service, 'build_card',
                          side_effect=ConnectionError('Connection reset by peer')):
            resp = self._post_build(token)
        self.assertEqual(resp.status_code, 502)
        self.assertTrue(resp.get_json()['retryable'])

    def test_unexpected_error_returns_500_not_retryable(self):
        token = self._token()
        with patch.object(agent_card_service, 'build_card',
                          side_effect=RuntimeError('boom')):
            resp = self._post_build(token)
        body = resp.get_json()
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(body['code'], 500)
        self.assertFalse(body['retryable'])

    def test_validation_error_stays_422(self):
        token = self._token()
        with patch.object(agent_card_service, 'build_card',
                          side_effect=agent_card_service.AgentValidationError('参数错')):
            resp = self._post_build(token)
        self.assertEqual(resp.status_code, 422)
        self.assertFalse(resp.get_json().get('retryable', False))

    def test_suite_timeout_returns_502_retryable(self):
        token = self._token()
        from services import suite_service
        with patch.object(suite_service, 'generate_suite',
                          side_effect=TimeoutError('picture upstream timeout')):
            resp = self.client.post('/api/agent/images/suite', headers={'X-Agent-Key': token},
                                    json={'product_id': 'id_x', 'modules': ['hero']})
        self.assertEqual(resp.status_code, 502)
        self.assertTrue(resp.get_json()['retryable'])


if __name__ == '__main__':
    unittest.main()
