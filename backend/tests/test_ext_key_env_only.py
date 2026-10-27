"""插件 X-Ext-Key 只从环境变量读取（不再内置默认值）的回归测试。"""
import os
import sys
import types
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

routes_package = types.ModuleType('routes')
routes_package.__path__ = [os.path.join(BACKEND_DIR, 'routes')]
sys.modules.setdefault('routes', routes_package)

from flask import Flask

from utils import ext_auth
from routes import jlc_ext_routes, ozon_collect_routes

LEGACY_DEFAULT = 'legacy-default-key-not-configured'


class ExtKeyEnvOnlyTests(unittest.TestCase):
    def test_no_env_means_no_valid_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('OZON_EXT_API_KEYS', None)
            os.environ.pop('EXT_API_KEY', None)
            self.assertEqual([], ext_auth.plugin_api_keys())
            self.assertEqual([], ozon_collect_routes._plugin_api_keys())
            self.assertEqual('', ozon_collect_routes._plugin_api_key())
            self.assertEqual('', jlc_ext_routes._plugin_api_key())

    def test_legacy_default_key_rejected_when_env_missing(self):
        app = Flask(__name__)

        @app.route('/probe')
        def probe():
            return 'valid' if ext_auth.is_ext_key_valid() else 'invalid'

        client = app.test_client()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('OZON_EXT_API_KEYS', None)
            os.environ.pop('EXT_API_KEY', None)
            response = client.get('/probe', headers={'X-Ext-Key': LEGACY_DEFAULT})
        self.assertEqual('invalid', response.get_data(as_text=True))

    def test_env_keys_are_parsed_in_order(self):
        with patch.dict(os.environ, {'OZON_EXT_API_KEYS': 'new-key, old-key'}, clear=False):
            self.assertEqual(['new-key', 'old-key'], ext_auth.plugin_api_keys())
            self.assertEqual(['new-key', 'old-key'], ozon_collect_routes._plugin_api_keys())

    def test_single_env_key_still_works(self):
        with patch.dict(os.environ, {'EXT_API_KEY': 'only-key'}, clear=False):
            os.environ.pop('OZON_EXT_API_KEYS', None)
            self.assertEqual(['only-key'], ext_auth.plugin_api_keys())
            self.assertEqual('only-key', jlc_ext_routes._plugin_api_key())


if __name__ == '__main__':
    unittest.main()
