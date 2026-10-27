import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db

_TEMP_DIR = tempfile.TemporaryDirectory()
db.DB_PATH = os.path.join(_TEMP_DIR.name, 'auth-guard.db')
db.init_db()

import utils.auth as auth_utils

require_api_auth = getattr(auth_utils, 'require_api_auth', lambda: None)


class ApiAuthGuardTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def check_request(self, path, method='GET', user=None):
        with self.app.test_request_context(path, method=method):
            with patch('utils.auth.get_current_user', return_value=user):
                return require_api_auth()

    def test_rejects_anonymous_business_api_requests(self):
        response = self.check_request('/api/gallery')

        status = response[1] if isinstance(response, tuple) else getattr(response, 'status_code', None)
        self.assertEqual(401, status)

    def test_allows_authenticated_business_api_requests(self):
        response = self.check_request('/api/gallery', user={'id': 1, 'role': 'admin'})

        self.assertIsNone(response)

    def test_keeps_only_required_public_endpoints_open(self):
        for path in (
            '/api/auth/login',
            '/api/user/info',
            '/api/health',
            '/api/config',
            '/api/gallery/files/example.png',
        ):
            with self.subTest(path=path):
                self.assertIsNone(self.check_request(path))

    def test_allows_cors_preflight_without_authentication(self):
        response = self.check_request('/api/gallery', method='OPTIONS')

        self.assertIsNone(response)

    def test_does_not_interfere_with_non_api_routes(self):
        self.assertIsNone(self.check_request('/frontend/index.html'))


if __name__ == '__main__':
    unittest.main()
