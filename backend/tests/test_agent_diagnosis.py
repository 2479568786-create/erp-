"""R8 发布警告归因：规则映射 + 路由契约。"""
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
from models.product import PublishRecord  # noqa: E402
from services import publish_diagnosis  # noqa: E402
from utils import agent_auth  # noqa: E402
from utils.auth import require_api_auth  # noqa: E402

SAMPLES = {
    'BRAND': '[#Хештеги] Хештег: удалите названия всех брендов, не связанных с товаром',
    'ORIGINAL_CLAIM': '[Название] В карточке товара есть указание на то, что продаётся оригинал товара',
    'PROMO_AD': '[#Хештеги] Хештег: уберите рекламные выражения или названия маркетинговых акций',
    'FORBIDDEN': '[Аннотация] Товар запрещен к продаже на площадке',
    'NO_IMAGE': 'Не удалось скачать изображение для главного фото',
    'MISSING_DICTIONARY': 'Payload校验失败: item[1] 字典属性 Бренд 缺少 dictionary_value_id',
    'OFFER_ID': 'invalid Request.OfferId: value length must be at most 50 characters',
    'PRICE': '[price] Зачёркнутая цена должна быть выше предельной цены.',
}


class DiagnosisTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agentdiag_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self.records_path = os.path.join(self.tmpdir, 'publish_records.json')
        open(self.records_path, 'w', encoding='utf-8').write('[]')
        self._patchers = [
            patch.object(db, 'DB_PATH', self.db_path),
            patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': 'test-pepper-123'}),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', 'test-pepper-123'),
            patch.object(Config, 'AGENT_MAX_RPM', 600),
            patch.object(PublishRecord, 'FILE_PATH', self.records_path),
        ]
        for p in self._patchers:
            p.start()
        PublishRecord._cache = []
        PublishRecord._loaded = False
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
        PublishRecord._cache = []
        PublishRecord._loaded = False

    def make_record(self, **overrides):
        data = {
            'productId': 'id_test',
            'title': 'Тестовый товар',
            'status': 'failed',
            'storeId': 'store-1',
            'error': '',
            'errors': [],
        }
        data.update(overrides)
        return PublishRecord.create(data)


class DiagnosisRulesTests(DiagnosisTestBase):
    def test_eight_categories(self):
        for expected, text in SAMPLES.items():
            result = publish_diagnosis.diagnose_record({'error': text})
            codes = [item['code'] for item in result['items']]
            self.assertIn(expected, codes, (expected, codes))
            self.assertEqual(result['unknown'], [], text)

    def test_multiple_segments(self):
        text = SAMPLES['BRAND'] + ' | ' + SAMPLES['PROMO_AD']
        result = publish_diagnosis.diagnose_record({'error': text})
        codes = [item['code'] for item in result['items']]
        self.assertIn('BRAND', codes)
        self.assertIn('PROMO_AD', codes)
        self.assertEqual(result['summary']['segments'], 2)

    def test_errors_list_dict(self):
        record = {'errors': [{'message': SAMPLES['PRICE']}, SAMPLES['OFFER_ID']]}
        result = publish_diagnosis.diagnose_record(record)
        codes = [item['code'] for item in result['items']]
        self.assertIn('PRICE', codes)
        self.assertIn('OFFER_ID', codes)

    def test_unknown_passthrough(self):
        result = publish_diagnosis.diagnose_record({'error': 'Some completely unknown upstream error'})
        self.assertEqual(result['items'], [])
        self.assertEqual(len(result['unknown']), 1)
        self.assertIn('unknown upstream', result['unknown'][0]['raw'])

    def test_field_from_bracket(self):
        result = publish_diagnosis.diagnose_record({'error': '[Аннотация] Товар запрещен к продаже'})
        self.assertEqual(result['items'][0]['field'], 'description')

    def test_diagnosis_fields_complete(self):
        result = publish_diagnosis.diagnose_record({'error': SAMPLES['BRAND']})
        item = result['items'][0]
        for key in ('code', 'severity', 'field', 'message_zh', 'fix_hint', 'raw'):
            self.assertIn(key, item)


class DiagnosisRouteTests(DiagnosisTestBase):
    def setUp(self):
        super().setUp()
        from routes.publish_record_routes import publish_record_bp

        app = Flask(__name__)
        app.before_request(agent_auth.agent_request_guard)
        app.before_request(require_api_auth)
        app.after_request(agent_auth.agent_audit_after_request)
        app.register_blueprint(publish_record_bp, url_prefix='/api')
        self.client = app.test_client()

    def _token(self, scopes='publish:read'):
        token = 'agent_diagtoken'
        db.execute(
            "INSERT INTO agent_tokens (name, token_hash, scopes, created_at) VALUES (?, ?, ?, datetime('now'))",
            ('t', agent_auth.hash_token(token), scopes),
        )
        return token

    def test_diagnosis_ok(self):
        record = self.make_record(error=SAMPLES['NO_IMAGE'])
        token = self._token()
        resp = self.client.get(f'/api/publish-records/{record["id"]}/diagnosis',
                               headers={'X-Agent-Key': token})
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertIn('NO_IMAGE', [i['code'] for i in body['data']['items']])
        self.assertEqual(body['data']['record_id'], record['id'])

    def test_diagnosis_not_found(self):
        token = self._token()
        resp = self.client.get('/api/publish-records/nope/diagnosis', headers={'X-Agent-Key': token})
        self.assertEqual(resp.get_json()['code'], 404)

    def test_diagnosis_wrong_scope_403(self):
        record = self.make_record()
        token = self._token(scopes='cards:write')
        resp = self.client.get(f'/api/publish-records/{record["id"]}/diagnosis',
                               headers={'X-Agent-Key': token})
        self.assertEqual(resp.get_json()['code'], 403)

    def test_diagnosis_no_token_401(self):
        resp = self.client.get('/api/publish-records/whatever/diagnosis')
        self.assertEqual(resp.status_code, 401)


if __name__ == '__main__':
    unittest.main()
