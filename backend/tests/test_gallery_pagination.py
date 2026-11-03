# -*- coding: utf-8 -*-
"""图库列表分页 + 列表瘦身（需求 C1 后端）。

覆盖：
- 带 page/pageSize → 分页模式：条数正确、total 正确、prompt 只回 120 字预览
- 不带 page/pageSize → 兼容旧行为（全量），但响应标记 paginated=false
- pageSize 上限 100、非法参数 400、筛选条件在分页模式下仍生效
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from flask import Flask  # noqa: E402

import db  # noqa: E402
from routes import gallery_routes  # noqa: E402

LONG_PROMPT = 'P' * 300


class GalleryPaginationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='gallery_page_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self._patcher_db = patch.object(db, 'DB_PATH', self.db_path)
        self._patcher_user = patch.object(gallery_routes, 'get_current_user',
                                          lambda: {'id': 1})
        self._patcher_db.start()
        self._patcher_user.start()
        db.init_db()
        app = Flask(__name__)
        app.register_blueprint(gallery_routes.gallery_bp, url_prefix='/api')
        self.client = app.test_client()
        self._seed(30)

    def tearDown(self):
        self._patcher_user.stop()
        self._patcher_db.stop()

    def _seed(self, count):
        for index in range(count):
            db.execute(
                """INSERT INTO gallery_assets
                   (user_id, filename, original_name, title, mime_type, file_size,
                    source, prompt, tags, created_at)
                   VALUES (?, ?, ?, ?, 'image/png', 1024, ?, ?, '[]', ?)""",
                (1, 'f%03d.png' % index, 'orig%03d.png' % index, '标题%d' % index,
                 'ai' if index % 2 else 'upload', LONG_PROMPT,
                 '2026-11-03 10:%02d:00' % index),
            )

    def test_paginated_mode(self):
        resp = self.client.get('/api/gallery?page=1&pageSize=7')
        body = resp.get_json()
        data = body['data']
        self.assertTrue(data['paginated'])
        self.assertEqual(len(data['list']), 7)
        self.assertEqual(data['total'], 30)
        self.assertEqual((data['page'], data['pageSize']), (1, 7))
        item = data['list'][0]
        # 列表瘦身：无 prompt 全文、有 120 字预览与 thumb_url
        self.assertNotIn('prompt', item)
        self.assertEqual(len(item['prompt_preview']), 120)
        self.assertTrue(item['prompt_truncated'])
        self.assertIn('thumb_url', item)
        self.assertEqual(item['url'], '/api/gallery/files/%s' % item['filename'])
        self.assertNotIn('endpoint', item)
        self.assertNotIn('provider', item)

    def test_second_page_does_not_overlap(self):
        first = self.client.get('/api/gallery?page=1&pageSize=10').get_json()['data']['list']
        second = self.client.get('/api/gallery?page=2&pageSize=10').get_json()['data']['list']
        first_ids = {row['id'] for row in first}
        second_ids = {row['id'] for row in second}
        self.assertEqual(len(second), 10)
        self.assertFalse(first_ids & second_ids)

    def test_last_page_size(self):
        data = self.client.get('/api/gallery?page=3&pageSize=10').get_json()['data']
        self.assertEqual(len(data['list']), 10)
        data = self.client.get('/api/gallery?page=4&pageSize=10').get_json()['data']
        self.assertEqual(len(data['list']), 0)
        self.assertEqual(data['total'], 30)

    def test_legacy_mode_returns_full_list(self):
        data = self.client.get('/api/gallery').get_json()['data']
        self.assertFalse(data['paginated'])
        self.assertEqual(len(data['list']), 30)
        self.assertEqual(data['total'], 30)
        # 兼容模式保留原字段（含 prompt 全文）
        self.assertIn('prompt', data['list'][0])

    def test_page_size_capped_at_100(self):
        data = self.client.get('/api/gallery?page=1&pageSize=500').get_json()['data']
        self.assertEqual(data['pageSize'], 100)

    def test_invalid_pagination_returns_400(self):
        resp = self.client.get('/api/gallery?page=abc')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()['code'], 400)

    def test_filters_still_apply_in_paginated_mode(self):
        data = self.client.get('/api/gallery?page=1&pageSize=5&source=ai').get_json()['data']
        self.assertEqual(data['total'], 15)
        self.assertEqual(len(data['list']), 5)
        self.assertTrue(all(row['source'] == 'ai' for row in data['list']))

    def test_keyword_filter_in_paginated_mode(self):
        data = self.client.get('/api/gallery?page=1&pageSize=5&keyword=orig00').get_json()['data']
        self.assertEqual(data['total'], 10)  # orig000..orig009
        self.assertEqual(len(data['list']), 5)


if __name__ == '__main__':
    unittest.main()
