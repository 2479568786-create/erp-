# -*- coding: utf-8 -*-
"""店铺自助绑定契约：业务员 5 家上限 / 归属强制自己 / 只能改删自己且无业务数据的店。"""
import json
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
from models.product import Product, PublishRecord, PublishTask  # noqa: E402
from utils.auth import create_token  # noqa: E402

LIMIT = 5


def _write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


class StoreSelfBindBase(unittest.TestCase):
    """临时库 + 临时 JSON 数据文件 + 只挂 store 蓝图。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='storeself_')
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self._patchers = [patch.object(db, 'DB_PATH', self.db_path)]
        self._json_paths = {}
        for name, cls in (('products.json', Product),
                          ('publish_records.json', PublishRecord),
                          ('publish_tasks.json', PublishTask)):
            path = os.path.join(self.tmpdir, name)
            _write_json(path, [])
            self._json_paths[name] = path
            self._patchers.append(patch.object(cls, 'FILE_PATH', path))
        for p in self._patchers:
            p.start()
        self._reset_json_caches()

        self._app_ctx = Flask(__name__).app_context()
        self._app_ctx.push()
        db.init_db()

        self.admin_id = self._seed_account('admin', '管理员', 'admin')
        self.gby_id = self._seed_account('gby', '郭滨扬', 'salesperson')
        self.ls_id = self._seed_account('ls', '李双', 'salesperson')
        self.ls_store_pk = self._seed_store('269729036', 'QING Store', self.ls_id)
        self.admin_token = create_token({'id': self.admin_id})
        self.gby_token = create_token({'id': self.gby_id})
        self.ls_token = create_token({'id': self.ls_id})

        from routes.store_routes import store_bp

        app = Flask(__name__)
        app.register_blueprint(store_bp, url_prefix='/api')
        app.config['TESTING'] = True
        self.client = app.test_client()

    def tearDown(self):
        try:
            self._app_ctx.pop()
        except Exception:
            pass
        for p in reversed(self._patchers):
            p.stop()
        self._reset_json_caches()

    # ---- 工具 ----
    def _reset_json_caches(self):
        for cls in (Product, PublishRecord, PublishTask):
            cls._cache = []
            cls._loaded = False

    def _seed_account(self, username, nickname, role):
        db.execute('INSERT INTO accounts (username, nickname, password_hash, role, '
                   'image_enabled, image_balance_cents) VALUES (?, ?, ?, ?, 1, 0)',
                   (username, nickname, 'x', role))
        return int(db.query('SELECT id FROM accounts WHERE username = ?', (username,), one=True)['id'])

    def _seed_store(self, store_id, alias, account_id=None):
        db.execute('INSERT INTO stores (store_id, alias, account_id) VALUES (?, ?, ?)',
                   (store_id, alias, account_id))
        return int(db.query('SELECT id FROM stores WHERE store_id = ?', (store_id,), one=True)['id'])

    def _write_products(self, items):
        _write_json(self._json_paths['products.json'], items)
        Product._cache = []
        Product._loaded = False

    def _add_watermark_ref(self, store_pk):
        db.execute('INSERT INTO shop_watermarks (store_id) VALUES (?)', (store_pk,))

    def _bind(self, token, store_id, alias, **extra):
        body = {'storeId': store_id, 'alias': alias, 'currency': 'RUB',
                'authType': 'api', 'clientId': 'cid', 'apiKey': 'akey',
                'authStatus': 'active'}
        body.update(extra)
        return self.client.post('/api/stores', json=body,
                                headers={'Authorization': 'Bearer ' + token})

    def _stores_of(self, account_id):
        return db.query('SELECT * FROM stores WHERE account_id = ?', (account_id,))

    def _store_row(self, store_pk):
        return db.query('SELECT * FROM stores WHERE id = ?', (store_pk,), one=True)


class StoreBindQuotaTests(StoreSelfBindBase):
    def test_salesperson_can_bind_own_store(self):
        res = self._bind(self.gby_token, '216800183', 'unistore')
        self.assertEqual(res.status_code, 200, res.get_json())
        body = res.get_json()
        self.assertEqual(int(body['data']['account_id']), self.gby_id)
        self.assertEqual(len(self._stores_of(self.gby_id)), 1)

    def test_salesperson_cannot_bind_for_other_account(self):
        res = self._bind(self.gby_token, '216800183', 'unistore', accountId=self.ls_id)
        self.assertEqual(res.status_code, 200)
        rows = self._stores_of(self.gby_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(self._stores_of(self.ls_id)), 1)   # 仍只有原来那家

    def test_sixth_store_rejected_with_quota(self):
        for i in range(LIMIT):
            res = self._bind(self.gby_token, '90000%d' % i, 'gby-%d' % i)
            self.assertEqual(res.status_code, 200, res.get_json())
        res = self._bind(self.gby_token, '999999', 'gby-over')
        self.assertEqual(res.status_code, 403)
        body = res.get_json()
        self.assertIn('上限', body['msg'])
        self.assertEqual(body['data']['quota']['used'], LIMIT)
        self.assertEqual(body['data']['quota']['limit'], LIMIT)
        self.assertFalse(body['data']['quota']['canBind'])
        self.assertEqual(len(self._stores_of(self.gby_id)), LIMIT)

    def test_duplicate_store_id_rejected_keeps_original_owner(self):
        res = self._bind(self.gby_token, '269729036', '抢店铺')
        self.assertEqual(res.status_code, 400)
        self.assertIn('已存在', res.get_json()['msg'])
        row = db.query('SELECT * FROM stores WHERE store_id = ?', ('269729036',), one=True)
        self.assertEqual(int(row['account_id']), self.ls_id)

    def test_admin_not_limited_and_can_assign_owner(self):
        before = len(self._stores_of(self.ls_id))
        for i in range(LIMIT + 1):
            res = self._bind(self.admin_token, '80000%d' % i, 'ls-%d' % i, accountId=self.ls_id)
            self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(len(self._stores_of(self.ls_id)), before + LIMIT + 1)

    def test_list_returns_quota_for_salesperson_only(self):
        self._bind(self.gby_token, '216800183', 'unistore')
        body = self.client.get('/api/stores', headers={'Authorization': 'Bearer ' + self.gby_token}).get_json()
        self.assertEqual(body['data']['quota']['used'], 1)
        self.assertEqual(body['data']['quota']['limit'], LIMIT)
        self.assertTrue(body['data']['quota']['canBind'])
        self.assertTrue(all(s['accountId'] == self.gby_id for s in body['data']['list']))
        # 管理员不返回配额（不限量）
        admin_body = self.client.get('/api/stores', headers={'Authorization': 'Bearer ' + self.admin_token}).get_json()
        self.assertIsNone(admin_body['data']['quota'])

    def test_list_marks_store_with_business_data_not_deletable(self):
        self._bind(self.gby_token, '216800183', 'unistore')
        store = self._stores_of(self.gby_id)[0]
        body = self.client.get('/api/stores', headers={'Authorization': 'Bearer ' + self.gby_token}).get_json()
        self.assertTrue(body['data']['list'][0]['deletable'])
        self._add_watermark_ref(store['id'])
        body = self.client.get('/api/stores', headers={'Authorization': 'Bearer ' + self.gby_token}).get_json()
        self.assertFalse(body['data']['list'][0]['deletable'])
        self.assertEqual(body['data']['list'][0]['dataRefs'], {'shop_watermarks': 1})


class StoreWriteScopeTests(StoreSelfBindBase):
    def setUp(self):
        super().setUp()
        self.gby_store_pk = self._seed_store('216800183', 'unistore', self.gby_id)

    def test_salesperson_can_update_own_store(self):
        res = self.client.put('/api/stores/%d' % self.gby_store_pk, json={'alias': 'unistore-new'},
                              headers={'Authorization': 'Bearer ' + self.gby_token})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(self._store_row(self.gby_store_pk)['alias'], 'unistore-new')

    def test_salesperson_cannot_update_other_store(self):
        res = self.client.put('/api/stores/%d' % self.ls_store_pk, json={'alias': 'hack'},
                              headers={'Authorization': 'Bearer ' + self.gby_token})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self._store_row(self.ls_store_pk)['alias'], 'QING Store')

    def test_salesperson_cannot_change_store_owner(self):
        res = self.client.put('/api/stores/%d' % self.gby_store_pk,
                              json={'alias': 'unistore', 'accountId': self.ls_id},
                              headers={'Authorization': 'Bearer ' + self.gby_token})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(int(self._store_row(self.gby_store_pk)['account_id']), self.gby_id)

    def test_salesperson_can_delete_own_store_without_data(self):
        res = self.client.delete('/api/stores/%d' % self.gby_store_pk,
                                 headers={'Authorization': 'Bearer ' + self.gby_token})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertIsNone(self._store_row(self.gby_store_pk))

    def test_salesperson_cannot_delete_own_store_with_db_data(self):
        self._add_watermark_ref(self.gby_store_pk)
        res = self.client.delete('/api/stores/%d' % self.gby_store_pk,
                                 headers={'Authorization': 'Bearer ' + self.gby_token})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json()['data']['usage'], {'shop_watermarks': 1})
        self.assertIsNotNone(self._store_row(self.gby_store_pk))

    def test_salesperson_cannot_delete_own_store_with_products(self):
        self._write_products([{'id': 'id_p1', 'storeId': str(self.gby_store_pk)},
                              {'id': 'id_p2', 'storeId': '999'}])
        res = self.client.delete('/api/stores/%d' % self.gby_store_pk,
                                 headers={'Authorization': 'Bearer ' + self.gby_token})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json()['data']['usage'], {'products': 1})

    def test_salesperson_cannot_delete_other_store(self):
        res = self.client.delete('/api/stores/%d' % self.ls_store_pk,
                                 headers={'Authorization': 'Bearer ' + self.gby_token})
        self.assertEqual(res.status_code, 403)
        self.assertIsNotNone(self._store_row(self.ls_store_pk))

    def test_admin_can_delete_store_with_data(self):
        self._add_watermark_ref(self.gby_store_pk)
        res = self.client.delete('/api/stores/%d' % self.gby_store_pk,
                                 headers={'Authorization': 'Bearer ' + self.admin_token})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertIsNone(self._store_row(self.gby_store_pk))

    def test_anonymous_cannot_write(self):
        self.assertEqual(self.client.post('/api/stores', json={}).status_code, 401)
        self.assertEqual(self.client.delete('/api/stores/%d' % self.gby_store_pk).status_code, 401)


if __name__ == '__main__':
    unittest.main()
