# -*- coding: utf-8 -*-
"""上架记录提交：submit 必须把 record.storeId 透传给 PublishService.create_task。

背景：2026-10-29 真推验收发现 `/api/publish-records/<id>/submit` 只传
(product_id, platform)，create_task 内部 `if not store_id: raise ValueError`，
导致 UI「重新提交」对真实发布必失败。这里锁住修复。
"""
import os
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from flask import Flask  # noqa: E402

from routes import publish_record_routes as prr  # noqa: E402

RECORD = {
    'id': 'id_rec_1',
    'productId': 'id_prod_1',
    'storeId': '5930627',
    'platform': 'ozon',
}


class SubmitPassesStoreIdTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(prr.publish_record_bp, url_prefix='/api')

    def _submit(self):
        view = prr.submit_publish_record
        while hasattr(view, '__wrapped__'):
            view = view.__wrapped__
        with self.app.test_request_context('/api/publish-records/id_rec_1/submit', method='POST'):
            with patch.object(prr, 'PublishRecord') as record_model, \
                    patch.object(prr, 'PublishService') as publish_service, \
                    patch.object(prr, 'get_current_user', return_value={'id': 1, 'role': 'admin'}):
                record_model.find_by_id.return_value = dict(RECORD)
                publish_service.create_task.return_value = {'id': 'id_task_1', 'status': 'pending'}
                resp = view('id_rec_1')
        return resp, publish_service

    def test_submit_passes_store_id(self):
        resp, publish_service = self._submit()
        self.assertEqual(resp[1], 200)
        self.assertEqual(publish_service.create_task.call_args.args[0], 'id_prod_1')
        self.assertEqual(publish_service.create_task.call_args.kwargs.get('store_id'), '5930627')

    def test_submit_reports_task_and_record_status(self):
        resp, _ = self._submit()
        body = resp[0].get_json()
        self.assertEqual(body['data']['id'], 'id_task_1')
        self.assertEqual(body['msg'], '已提交发布')


if __name__ == '__main__':
    unittest.main()
