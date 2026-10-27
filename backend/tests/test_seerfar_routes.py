import os
import sys
import unittest
from unittest.mock import patch

from flask import Flask


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


class SeerfarRouteTests(unittest.TestCase):
    def setUp(self):
        from routes.seerfar_routes import seerfar_bp
        from routes.research_routes import research_bp

        self.app = Flask(__name__)
        self.app.register_blueprint(seerfar_bp, url_prefix='/api')
        self.app.register_blueprint(research_bp, url_prefix='/api')
        self.client = self.app.test_client()

    def test_seerfar_rankings_requires_login(self):
        response = self.client.post('/api/seerfar/rankings', json={})
        self.assertIn(response.status_code, (401, 403))

    def test_seerfar_import_requires_admin(self):
        with patch('utils.auth.get_current_user', return_value={
            'id': 2, 'role': 'operator',
        }):
            response = self.client.post('/api/seerfar/import', json={
                'source_id': '175924376', 'product_name': '测试商品',
            })
        self.assertEqual(403, response.status_code)

    def test_seerfar_import_is_ozon_only_and_requires_real_source_id(self):
        with patch('utils.auth.get_current_user', return_value={
            'id': 1, 'role': 'admin',
        }):
            wildberries = self.client.post('/api/seerfar/import', json={
                'platform': 'wildberries', 'source_id': '123',
                'product_name': '测试商品',
            })
            missing_id = self.client.post('/api/seerfar/import', json={
                'platform': 'ozon', 'source_id': '-',
                'product_name': '测试商品',
            })
        self.assertEqual(400, wildberries.status_code)
        self.assertEqual(400, missing_id.status_code)

    def test_upstream_unauthorized_does_not_log_out_erp_user_or_echo_secret(self):
        from services.seerfar_client import SeerfarApiError

        with patch('utils.auth.get_current_user', return_value={
            'id': 1, 'role': 'admin',
        }), patch(
            'routes.seerfar_routes.SeerfarSelectionService.quota',
            side_effect=SeerfarApiError('secret-api-key', status_code=401, code=401),
        ):
            response = self.client.get('/api/seerfar/quota')
        self.assertEqual(502, response.status_code)
        self.assertNotIn('secret-api-key', response.get_json()['msg'])

    def test_copying_seerfar_item_clears_source_identity(self):
        from routes.research_routes import ResearchItem

        source = {
            'id': 7,
            'product_name': '来源商品',
            'source_type': 'seerfar',
            'source_id': '175924376',
            'platform': 'ozon',
            'source_payload_json': '{"sku": 175924376}',
            'source_synced_at': '2026-09-24 12:00:00',
        }
        copied = dict(source, id=8, product_name='来源商品（副本）',
                      source_type='manual', source_id='',
                      source_payload_json='{}', source_synced_at=None)
        with patch('utils.auth.get_current_user', return_value={
            'id': 1, 'role': 'admin',
        }), patch.object(ResearchItem, 'find_by_id', side_effect=[source, copied]), \
                patch.object(ResearchItem, 'create', return_value=8) as create:
            response = self.client.post('/api/product-research/7/copy')

        self.assertEqual(200, response.status_code)
        created_data = create.call_args.args[0]
        self.assertEqual('manual', created_data['source_type'])
        self.assertEqual('', created_data['source_id'])


if __name__ == '__main__':
    unittest.main()
