"""R4 图片绑定：服务层 + 路由契约（不连外部服务）。"""
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
from models.product import Product  # noqa: E402
from services import agent_images_service  # noqa: E402
from utils import agent_auth  # noqa: E402
from utils.auth import require_api_auth  # noqa: E402

TEST_PEPPER = 'test-pepper-123'


class ImageTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='agentimg_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self.products_path = os.path.join(self.tmpdir, 'products.json')
        open(self.products_path, 'w', encoding='utf-8').write('[]')
        self._patchers = [
            patch.object(db, 'DB_PATH', self.db_path),
            patch.dict(os.environ, {'AGENT_TOKEN_PEPPER': TEST_PEPPER}),
            patch.object(Config, 'AGENT_TOKEN_PEPPER', TEST_PEPPER),
            patch.object(Config, 'AGENT_MAX_RPM', 600),
            patch.object(Product, 'FILE_PATH', self.products_path),
        ]
        for p in self._patchers:
            p.start()
        Product._cache = []
        Product._loaded = False
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
        Product._cache = []
        Product._loaded = False

    def seed_product(self, skus=None, images=None, detail_images=None):
        product = Product.create({
            'title': '测试商品',
            'skus': skus if skus is not None else [
                {'combo': {'颜色': '黑'}, 'images': ['/api/gallery/files/old1.png']},
                {'combo': {'颜色': '白'}, 'images': []},
            ],
            'images': images or ['/api/gallery/files/old1.png'],
            'detailImages': detail_images or [],
        })
        return product

    def seed_assets(self, count=3, user_id=1, prefix='a'):
        ids = []
        for i in range(count):
            db.execute(
                """INSERT INTO gallery_assets (user_id, filename, original_name, title, mime_type, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'image/png', 'ai', datetime('now'), datetime('now'))""",
                (user_id, f'{prefix}{i}.png', f'{prefix}{i}.png', f'{prefix}{i}'),
            )
            row = db.query("SELECT id FROM gallery_assets WHERE filename = ?", (f'{prefix}{i}.png',), one=True)
            ids.append(int(row['id']))
        return ids


class AttachServiceTests(ImageTestBase):
    def test_replace_sets_order_and_cover(self):
        product = self.seed_product()
        ids = self.seed_assets(3)
        result = agent_images_service.attach_images(product['id'], ids, mode='replace', sku_index=0)
        self.assertEqual(result['count'], 3)
        urls = [f'/api/gallery/files/a{i}.png' for i in range(3)]
        self.assertEqual(result['sku_images'], urls)
        saved = Product.find_by_id(product['id'])
        self.assertEqual(saved['skus'][0]['images'], urls)
        self.assertEqual(saved['skus'][0]['image'], urls[0])
        self.assertEqual(saved['images'], urls)
        # 旧主图被替换（replace 清遗留）
        self.assertNotIn('/api/gallery/files/old1.png', saved['images'])

    def test_append_is_idempotent(self):
        product = self.seed_product(skus=[{'combo': {'颜色': '黑'}, 'images': []}], images=[])
        ids = self.seed_assets(2)
        agent_images_service.attach_images(product['id'], ids, mode='append', sku_index=0)
        first = Product.find_by_id(product['id'])
        agent_images_service.attach_images(product['id'], ids, mode='append', sku_index=0)
        second = Product.find_by_id(product['id'])
        self.assertEqual(first['skus'][0]['images'], second['skus'][0]['images'])
        self.assertEqual(len(second['skus'][0]['images']), 2)

    def test_union_across_skus(self):
        product = self.seed_product()
        ids1 = self.seed_assets(2, prefix='x')
        ids2 = self.seed_assets(1, prefix='y')
        agent_images_service.attach_images(product['id'], ids1, mode='replace', sku_index=0)
        result = agent_images_service.attach_images(product['id'], ids2, mode='replace', sku_index=1)
        self.assertEqual(result['images'], [
            '/api/gallery/files/x0.png', '/api/gallery/files/x1.png', '/api/gallery/files/y0.png',
        ])
        self.assertEqual(result['sku_images'], ['/api/gallery/files/y0.png'])

    def test_add_to_detail(self):
        product = self.seed_product(detail_images=['/api/gallery/files/detail.png'])
        ids = self.seed_assets(2)
        agent_images_service.attach_images(product['id'], ids, mode='replace', sku_index=0, add_to_detail=True)
        saved = Product.find_by_id(product['id'])
        self.assertEqual(saved['detailImages'], [
            '/api/gallery/files/detail.png',
            '/api/gallery/files/a0.png',
            '/api/gallery/files/a1.png',
        ])

    def test_create_sku_when_missing(self):
        product = self.seed_product(skus=[])
        ids = self.seed_assets(1)
        result = agent_images_service.attach_images(product['id'], ids, sku_index=0)
        self.assertEqual(result['sku_index'], 0)
        saved = Product.find_by_id(product['id'])
        self.assertEqual(saved['skus'][0]['images'], ['/api/gallery/files/a0.png'])

    def test_errors(self):
        product = self.seed_product()
        ids = self.seed_assets(1)
        with self.assertRaises(agent_images_service.AgentImageError):
            agent_images_service.attach_images(product['id'], [], mode='replace')
        with self.assertRaises(agent_images_service.AgentImageError):
            agent_images_service.attach_images(product['id'], ids, mode='bad')
        with self.assertRaises(agent_images_service.AgentImageError):
            agent_images_service.attach_images(product['id'], list(range(1, 40)))
        with self.assertRaises(agent_images_service.AgentImageError):
            agent_images_service.attach_images(product['id'], [999999])
        with self.assertRaises(agent_images_service.AgentImageError):
            agent_images_service.attach_images('id_missing', ids)

    def test_ownership_for_non_admin_user(self):
        product = self.seed_product()
        ids = self.seed_assets(1, user_id=8)
        with self.assertRaises(agent_images_service.AgentImageError):
            agent_images_service.attach_images(product['id'], ids, user={'id': 7, 'role': 'salesperson'})
        # 管理员可跨账号绑定
        agent_images_service.attach_images(product['id'], ids, user={'id': 1, 'role': 'admin'})


class AttachRouteTests(ImageTestBase):
    def setUp(self):
        super().setUp()
        from routes.agent_routes import agent_bp

        app = Flask(__name__)
        app.before_request(agent_auth.agent_request_guard)
        app.before_request(require_api_auth)
        app.after_request(agent_auth.agent_audit_after_request)
        app.register_blueprint(agent_bp, url_prefix='/api')
        self.client = app.test_client()

    def _token(self, scopes='images:write'):
        token = 'agent_imgtoken'
        db.execute(
            "INSERT INTO agent_tokens (name, token_hash, scopes, created_at) VALUES (?, ?, ?, datetime('now'))",
            ('t', agent_auth.hash_token(token), scopes),
        )
        return token

    def test_attach_ok(self):
        product = self.seed_product()
        ids = self.seed_assets(2)
        token = self._token()
        resp = self.client.post(
            f'/api/products/{product["id"]}/images/attach',
            headers={'X-Agent-Key': token},
            json={'asset_ids': ids, 'mode': 'replace', 'sku_index': 0, 'add_to_detail': True},
        )
        body = resp.get_json()
        self.assertEqual(body['code'], 200)
        self.assertEqual(body['data']['count'], 2)

    def test_attach_wrong_scope_403(self):
        product = self.seed_product()
        ids = self.seed_assets(1)
        token = self._token(scopes='products:read')
        resp = self.client.post(f'/api/products/{product["id"]}/images/attach',
                                headers={'X-Agent-Key': token}, json={'asset_ids': ids})
        self.assertEqual(resp.get_json()['code'], 403)

    def test_attach_no_token_401(self):
        resp = self.client.post('/api/products/id_x/images/attach', json={'asset_ids': [1]})
        self.assertEqual(resp.status_code, 401)

    def test_attach_validation_422(self):
        product = self.seed_product()
        token = self._token()
        resp = self.client.post(f'/api/products/{product["id"]}/images/attach',
                                headers={'X-Agent-Key': token}, json={'asset_ids': []})
        self.assertEqual(resp.get_json()['code'], 422)

    def test_audit_single_row(self):
        product = self.seed_product()
        ids = self.seed_assets(1)
        token = self._token()
        self.client.post(f'/api/products/{product["id"]}/images/attach',
                         headers={'X-Agent-Key': token}, json={'asset_ids': ids})
        rows = db.query("SELECT * FROM agent_ops ORDER BY id DESC")
        self.assertEqual(len(rows), 1, '每次调用只应写一条审计')
        self.assertEqual(rows[0]['product_id'], product['id'])
        self.assertEqual(rows[0]['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
