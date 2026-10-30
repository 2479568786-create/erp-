"""OSS 上传卡死修复的回归测试（2026-10-30）。

覆盖：
1. OSS 上传走内网 endpoint、返回公网 URL、失败重试 3 次后返回 None（不挂起）
2. 大图发布前压 JPEG，小图不压
3. 图片线程池可重建（卡死线程不再影响后续任务）
4. 图片预处理：连续 2 次超时 → 重建线程池 + 整单快速失败（不再排队 8 张等 60s）
5. GET /api/health/publish：仅管理员、字段齐全、堆积判定
全部 mock（不连 OSS / 不下载图片）。
"""
import datetime
import os
import sys
import tempfile
import unittest
from concurrent.futures import Future
from io import BytesIO
from unittest.mock import MagicMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault('AGENT_TOKEN_PEPPER', 'test-pepper-123')

import db  # noqa: E402
from config import Config  # noqa: E402
from services import publish_service, storage_service  # noqa: E402


class OssUploadTests(unittest.TestCase):
    """改点 1：storage_service._upload_to_oss。"""

    def setUp(self):
        self.calls = []
        self.fail_times = 0
        self._patchers = [
            patch.object(Config, 'OSS_ACCESS_KEY_ID', 'fake-ak'),
            patch.object(Config, 'OSS_ACCESS_KEY_SECRET', 'fake-sk'),
            patch.object(Config, 'OSS_BUCKET', 'demo-bucket'),
            patch.object(Config, 'OSS_ENDPOINT', 'oss-cn-hangzhou.aliyuncs.com'),
            patch.object(Config, 'OSS_UPLOAD_ENDPOINT', 'oss-cn-hangzhou-internal.aliyuncs.com'),
            patch.object(Config, 'OSS_PUBLIC_ENDPOINT', 'oss-cn-hangzhou.aliyuncs.com'),
            patch.object(publish_service.time, 'sleep', lambda *a, **k: None),
            patch.object(storage_service.time, 'sleep', lambda *a, **k: None),
        ]
        for p in self._patchers:
            p.start()
        self._install_fake_oss2()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        sys.modules.pop('oss2', None)

    def _install_fake_oss2(self):
        outer = self

        class FakeSession:
            class _S:
                def close(self):
                    outer.calls.append(('session_close',))
            session = _S()

        class FakeBucket:
            def __init__(self, auth, endpoint, bucket, connect_timeout=None):
                outer.calls.append(('bucket', endpoint, bucket, connect_timeout))
                self.session = FakeSession()

            def put_object(self, key, data):
                outer.calls.append(('put', key, len(data)))
                if outer.fail_times > 0:
                    outer.fail_times -= 1
                    raise RuntimeError('oss write timeout(mock)')
                return None

        fake = MagicMock()
        fake.Auth = lambda ak, sk: ('auth', ak, sk)
        fake.Bucket = FakeBucket
        fake.defaults = MagicMock()
        sys.modules['oss2'] = fake

    def test_upload_uses_internal_endpoint_returns_public_url(self):
        url = storage_service._upload_to_oss(b'x' * 10, 'a.jpg')
        self.assertEqual(url, 'https://demo-bucket.oss-cn-hangzhou.aliyuncs.com/a.jpg')
        bucket_calls = [c for c in self.calls if c[0] == 'bucket']
        self.assertEqual(len(bucket_calls), 1)
        self.assertEqual(bucket_calls[0][1], 'https://oss-cn-hangzhou-internal.aliyuncs.com')
        self.assertEqual(bucket_calls[0][3], 5.0)  # connect_timeout

    def test_upload_retries_three_times_then_returns_none(self):
        self.fail_times = 5  # 永远失败
        url = storage_service._upload_to_oss(b'x' * 10, 'b.jpg')
        self.assertIsNone(url)
        puts = [c for c in self.calls if c[0] == 'put']
        self.assertEqual(len(puts), 3, '失败应重试到 3 次后放弃')
        closes = [c for c in self.calls if c[0] == 'session_close']
        self.assertEqual(len(closes), 3, '每次重试前应丢弃坏 keep-alive 连接')

    def test_upload_recovers_after_one_failure(self):
        self.fail_times = 1
        url = storage_service._upload_to_oss(b'x' * 10, 'c.jpg')
        self.assertEqual(url, 'https://demo-bucket.oss-cn-hangzhou.aliyuncs.com/c.jpg')

    def test_missing_credentials_returns_none(self):
        with patch.object(Config, 'OSS_ACCESS_KEY_ID', ''):
            self.assertIsNone(storage_service._upload_to_oss(b'x', 'd.jpg'))


class CompressTests(unittest.TestCase):
    """改点 2 的一环：>300KB 的图发布前压 JPEG。"""

    @staticmethod
    def _big_png(size=900):
        from PIL import Image
        import random
        random.seed(7)
        img = Image.new('RGB', (size, size))
        px = img.load()
        for y in range(size):
            for x in range(size):
                px[x, y] = (random.randrange(256), random.randrange(256), random.randrange(256))
        out = BytesIO()
        img.save(out, format='PNG')
        return out.getvalue()

    def test_big_png_compressed_to_jpeg(self):
        raw = self._big_png()
        self.assertGreater(len(raw), 300 * 1024, '测试图应大于阈值')
        data, ext = publish_service._maybe_compress_for_publish(raw, 'png')
        self.assertEqual(ext, 'jpg')
        self.assertLess(len(data), len(raw))
        self.assertTrue(data.startswith(b'\xff\xd8'), '应为 JPEG 头')

    def test_small_image_untouched(self):
        raw = b'\x89PNG\r\n\x1a\n' + b'0' * 1000
        data, ext = publish_service._maybe_compress_for_publish(raw, 'png')
        self.assertEqual((data, ext), (raw, 'png'))

    def test_broken_image_falls_back(self):
        raw = b'not-an-image' * 50000  # >300KB 但不是图片
        data, ext = publish_service._maybe_compress_for_publish(raw, 'png')
        self.assertEqual((data, ext), (raw, 'png'))


class ExecutorTests(unittest.TestCase):
    """改点 2：线程池可重建 + 连续超时快速失败。"""

    def test_replace_image_executor_creates_working_pool(self):
        old = publish_service._image_executor
        new = publish_service._replace_image_executor()
        try:
            self.assertIsNot(new, old)
            self.assertEqual(new.submit(lambda: 42).result(timeout=5), 42)
            stats = publish_service.image_executor_stats()
            self.assertEqual(stats['max_workers'], publish_service.IMAGE_WORKERS)
            self.assertIn('threads_alive', stats)
        finally:
            new.shutdown(wait=False)
            publish_service._image_executor = old

    def test_two_consecutive_timeouts_fail_fast(self):
        never = Future()  # 永不完成，模拟卡死 future

        class FakeExecutor:
            def submit(self, fn, *a, **k):
                return never

        product = {'images': ['https://example.com/1.jpg',
                              'https://example.com/2.jpg',
                              'https://example.com/3.jpg'],
                   'skus': [], 'detailImages': []}
        replaced = []
        with patch.object(publish_service, '_image_executor', FakeExecutor()), \
                patch.object(publish_service, 'IMAGE_PROCESS_TIMEOUT', 0.01), \
                patch.object(publish_service, '_replace_image_executor',
                             side_effect=lambda: replaced.append(1)):
            ok, errors = publish_service.preprocess_images_for_publish(product)
        self.assertFalse(ok)
        self.assertEqual(len(replaced), 1, '连续 2 次超时应重建线程池')
        self.assertTrue(any('图片上传超时' in e for e in errors), errors)
        self.assertTrue(any('中止本单预处理' in e for e in errors), errors)
        # 失败时保留原 URL（由调用方按 errors 中止发布），关键是快速失败而不是继续排队
        self.assertEqual(len(product['images']), 3)


class HealthEndpointTests(unittest.TestCase):
    """改点 3：GET /api/health/publish。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='pubhealth_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self._patchers = [
            patch.object(db, 'DB_PATH', self.db_path),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', 'test-pepper-123'),
        ]
        for p in self._patchers:
            p.start()
        db.init_db()
        from utils.auth import create_token
        db.execute("INSERT INTO accounts (username, nickname, password_hash, role)"
                   " VALUES ('admin1', '管理员', 'x', 'admin')")
        db.execute("INSERT INTO accounts (username, nickname, password_hash, role)"
                   " VALUES ('sale1', '业务员', 'x', 'salesperson')")
        admin_id = db.query("SELECT id FROM accounts WHERE username='admin1'", one=True)['id']
        sale_id = db.query("SELECT id FROM accounts WHERE username='sale1'", one=True)['id']
        self.admin_token = create_token({'id': admin_id})
        self.sale_token = create_token({'id': sale_id})

        from flask import Flask
        from routes.publish_health_routes import publish_health_bp
        app = Flask(__name__)
        app.register_blueprint(publish_health_bp, url_prefix='/api')
        self.client = app.test_client()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()

    def _get(self, token=None):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        return self.client.get('/api/health/publish', headers=headers)

    def test_admin_sees_healthy_queue(self):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        tasks = [
            {'id': 't1', 'status': 'published', 'createdAt': now},
            {'id': 't2', 'status': 'pending', 'createdAt': now},
        ]
        with patch.object(publish_service.PublishTask, 'find_all', return_value=tasks):
            body = self._get(self.admin_token).get_json()
        self.assertEqual(body['code'], 200)
        data = body['data']
        for key in ('pending', 'processing', 'oldest_pending_age_sec',
                    'image_executor_threads', 'ok'):
            self.assertIn(key, data)
        self.assertEqual(data['pending'], 1)
        self.assertEqual(data['processing'], 0)
        self.assertTrue(data['ok'])

    def test_stuck_pending_marked_not_ok(self):
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(seconds=400)).isoformat()
        tasks = [{'id': 't1', 'status': 'pending', 'createdAt': old}]
        with patch.object(publish_service.PublishTask, 'find_all', return_value=tasks):
            data = self._get(self.admin_token).get_json()['data']
        self.assertFalse(data['ok'])
        self.assertGreaterEqual(data['oldest_pending_age_sec'], 390)
        self.assertTrue(data['problems'])

    def test_no_token_401(self):
        self.assertEqual(self._get().status_code, 401)

    def test_salesperson_403(self):
        self.assertEqual(self._get(self.sale_token).get_json()['code'], 403)


if __name__ == '__main__':
    unittest.main()
