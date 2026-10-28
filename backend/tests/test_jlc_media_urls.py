"""JLC 生图客户端：结果 URL 提取的回归测试（2026-10-28 参考图被当结果的事故）。"""
import os
import sys
import types
import unittest
import urllib.request
from unittest.mock import MagicMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services import jlc_media_service as jlc

RESULT = 'https://tos-hk.lingkeai.vip/uploads/2026.10/28/out.jpg'
REFERENCE = 'https://tos.lingkeai.vip/yonghu/2026.10/28/ref.webp'


class JlcOutputUrlTests(unittest.TestCase):
    def test_status_with_input_files_returns_only_generated_result(self):
        info = {'task_id': 1, 'state': 'success', 'is_final': True, 'cost': 0.1782,
                'input_files': [REFERENCE], 'result_url': RESULT, 'result_urls': [RESULT]}
        self.assertEqual([RESULT], jlc._output_urls(info))

    def test_single_result_url_is_used(self):
        info = {'result_url': RESULT, 'input_files': [REFERENCE]}
        self.assertEqual([RESULT], jlc._output_urls(info))

    def test_fallback_prunes_input_fields_before_scanning(self):
        info = {'input_files': [REFERENCE],
                'data': {'image_url': 'https://tos-hk.lingkeai.vip/uploads/fallback.jpg'}}
        self.assertEqual(['https://tos-hk.lingkeai.vip/uploads/fallback.jpg'],
                         jlc._output_urls(info))

    def test_generate_image_never_returns_the_reference_image(self):
        submitted = {'code': 0, 'data': {'task_id': 42}}
        status = {'data': {'is_final': True, 'state': 'success', 'cost': 0.1,
                           'input_files': [REFERENCE], 'result_urls': [RESULT]}}

        def fake_request(method, path, body=None, timeout=90, **kwargs):
            return submitted if method == 'POST' else status

        with patch.object(jlc, '_request', side_effect=fake_request), \
             patch.object(jlc.time, 'sleep', lambda *_: None):
            result = jlc.generate_image('probe prompt', images=[REFERENCE])

        self.assertEqual([RESULT], result['urls'])
        self.assertNotIn(REFERENCE, result['urls'])


class SuiteRouteAssetCountTests(unittest.TestCase):
    """套图端点：一次任务只落 1 张图（参考图不再入库、也不再多扣点）。"""

    def setUp(self):
        import db
        import tempfile
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patcher = patch.object(db, 'DB_PATH', os.path.join(self.temp_dir.name, 'x.db'))
        self.db_patcher.start()
        db.init_db()
        self.db = db
        from flask import Flask
        from routes.gallery_routes import gallery_bp
        self.app = Flask(__name__)
        self.app.register_blueprint(gallery_bp, url_prefix='/api')
        self.client = self.app.test_client()

    def tearDown(self):
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def test_suite_generation_saves_exactly_one_asset(self):
        import routes.gallery_routes as gallery_routes
        fake_response = MagicMock()
        fake_response.read.return_value = b'\x89PNG\r\n\x1a\nfake'
        fake_context = MagicMock()
        fake_context.__enter__.return_value = fake_response
        fake_context.__exit__.return_value = False
        saved = []

        def fake_save(data, original_name, source='upload', prompt='', tags=None, **kwargs):
            saved.append(original_name)
            return {'id': str(len(saved)), 'url': '/api/gallery/files/x.png'}

        with patch.object(gallery_routes.jlc_media_service, 'generate_image',
                          return_value={'urls': ['https://example.test/one.jpg'],
                                        'task_id': '1', 'cost': 0.1, 'model': 'banana-2.1'}), \
             patch.object(gallery_routes.urllib.request, 'urlopen', return_value=fake_context), \
             patch.object(gallery_routes, 'image_extension', return_value='jpg'), \
             patch.object(gallery_routes, '_save_bytes', side_effect=fake_save), \
             patch.object(gallery_routes, 'get_current_user', return_value={'id': 1}):
            response = self.client.post('/api/ai/suite/jlc',
                                        json={'prompt': 'probe', 'images': [REFERENCE]})

        self.assertEqual(200, response.status_code)
        assets = response.get_json()['data']['assets']
        self.assertEqual(1, len(assets))
        self.assertEqual(1, len(saved))


if __name__ == '__main__':
    unittest.main()
