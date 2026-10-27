"""生图计费（余额/开关/扣点/外部接口）单元测试。

运行：cd backend && .venv/bin/python -m unittest tests.test_image_billing -v
"""
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db
from services import ai_config_service
from services import image_billing_service as billing

routes_package = types.ModuleType('routes')
routes_package.__path__ = [os.path.join(BACKEND_DIR, 'routes')]
sys.modules.setdefault('routes', routes_package)

from routes.image_billing_routes import image_billing_bp
from routes.auth_routes import auth_bp
from utils.auth import create_token

EXT_KEY = 'test-ext-key'
VALID_PNG_BYTES = __import__('base64').b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AL+2QAAAABJRU5ErkJggg==')


class ImageBillingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patcher = patch.object(db, 'DB_PATH', os.path.join(self.temp_dir.name, 'billing.db'))
        self.db_patcher.start()
        self.config_patcher = patch.object(
            ai_config_service, 'AI_CONFIG_FILE',
            os.path.join(self.temp_dir.name, 'ai_models.json'),
        )
        self.config_patcher.start()
        self.env_patcher = patch.dict(os.environ, {'OZON_EXT_API_KEYS': EXT_KEY, 'EXT_API_KEY': EXT_KEY})
        self.env_patcher.start()
        db.init_db()

        self.admin_id = self._account('admin', 'admin', role='admin', balance_cents=0)
        self.user_id = self._account('郭滨扬', 'GBY', role='salesperson', balance_cents=4000)
        self.poor_id = self._account('李双', '李双', role='salesperson', balance_cents=0)
        self.off_id = self._account('谭科芬', 'TKF', role='salesperson', balance_cents=10000, enabled=0)

        self.app = Flask(__name__)
        self.app.register_blueprint(image_billing_bp, url_prefix='/api')
        self.app.register_blueprint(auth_bp, url_prefix='/api')
        self.client = self.app.test_client()
        self.admin_token = create_token({'id': self.admin_id})
        self.user_token = create_token({'id': self.user_id})

    def tearDown(self):
        self.env_patcher.stop()
        self.config_patcher.stop()
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _account(username, nickname, role='salesperson', balance_cents=0, enabled=1):
        return db.execute(
            'INSERT INTO accounts (username, nickname, password_hash, role, image_enabled, '
            'image_balance_cents) VALUES (?, ?, ?, ?, ?, ?)',
            (username, nickname, 'x', role, enabled, balance_cents),
        )

    def _headers(self):
        return {'X-Ext-Key': EXT_KEY}

    def _balance_cents(self, account_id):
        row = db.query('SELECT image_balance_cents FROM accounts WHERE id = ?', (account_id,), one=True)
        return int(row['image_balance_cents'])

    # ---------------------------------------------------------------- 迁移
    def test_migration_seeds_initial_balances_by_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'legacy.db')
            raw = sqlite3.connect(path)
            raw.execute(
                """CREATE TABLE accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    nickname TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'operator'
                        CHECK(role IN ('admin', 'operator', 'salesperson')),
                    status TEXT NOT NULL DEFAULT 'active',
                    last_login_at TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )"""
            )
            raw.execute("INSERT INTO accounts (username, nickname, password_hash, role) "
                        "VALUES ('郭滨扬', 'GBY', 'x', 'salesperson')")
            raw.execute("INSERT INTO accounts (username, nickname, password_hash, role) "
                        "VALUES ('李双', '李双', 'x', 'salesperson')")
            raw.execute("INSERT INTO accounts (username, nickname, password_hash, role) "
                        "VALUES ('JWJ', 'JWJ', 'x', 'salesperson')")
            raw.commit()
            raw.close()

            with patch.object(db, 'DB_PATH', path):
                db.init_db()
                rows = {row['username']: row['image_balance_cents']
                        for row in db.query('SELECT username, image_balance_cents FROM accounts')}

        self.assertEqual(4000, rows['郭滨扬'])   # 40 点
        self.assertEqual(1300, rows['李双'])     # 13 点
        self.assertEqual(0, rows['JWJ'])

    def test_migration_is_idempotent_and_keeps_balance(self):
        db.execute('UPDATE accounts SET image_balance_cents = 123 WHERE id = ?', (self.user_id,))
        db.init_db()
        self.assertEqual(123, self._balance_cents(self.user_id))

    def test_migration_creates_usage_and_ops_tables(self):
        tables = {row['name'] for row in db.query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('image_usage', tables)
        self.assertIn('image_ops_log', tables)

    # ---------------------------------------------------------------- 闸门
    def test_gate_blocks_zero_balance(self):
        allowed, reason = billing.can_generate({'role': 'salesperson', 'image_enabled': 1,
                                                'image_balance_cents': 0})
        self.assertFalse(allowed)
        self.assertIn('余额不足', reason)

    def test_gate_blocks_disabled_account(self):
        allowed, reason = billing.can_generate({'role': 'salesperson', 'image_enabled': 0,
                                                'image_balance_cents': 100})
        self.assertFalse(allowed)
        self.assertIn('已停用', reason)

    def test_gate_allows_paid_account(self):
        allowed, _ = billing.can_generate({'role': 'salesperson', 'image_enabled': 1,
                                           'image_balance_cents': 1})
        self.assertTrue(allowed)

    def test_gate_never_blocks_admin(self):
        allowed, _ = billing.can_generate({'role': 'admin', 'image_enabled': 0,
                                           'image_balance_cents': 0})
        self.assertTrue(allowed)

    def test_gate_checks_points_for_this_request_count(self):
        billing.set_billing_config(price_points_per_image=0.37)   # 37 分/张
        account = {'role': 'salesperson', 'image_enabled': 1}

        # 余额 0.36 点：一张都不够
        allowed, reason = billing.can_generate(dict(account, image_balance_cents=36), images=1)
        self.assertFalse(allowed)
        self.assertIn('余额不足', reason)

        # 余额 0.37 点：正好够 1 张
        allowed, _ = billing.can_generate(dict(account, image_balance_cents=37), images=1)
        self.assertTrue(allowed)

        # 余额 1 点：4 张需要 1.48 点，必须拦下（原漏洞：能出 4 张并被扣成负数）
        allowed, reason = billing.can_generate(dict(account, image_balance_cents=100), images=4)
        self.assertFalse(allowed)
        self.assertIn('本次 4 张需 1.48 点', reason)

        # 余额 1.48 点：正好够 4 张
        allowed, _ = billing.can_generate(dict(account, image_balance_cents=148), images=4)
        self.assertTrue(allowed)

    def test_gate_and_charge_stay_consistent(self):
        billing.set_billing_config(price_points_per_image=0.37)
        db.execute('UPDATE accounts SET image_balance_cents = 100 WHERE id = ?', (self.user_id,))
        account = db.query('SELECT * FROM accounts WHERE id = ?', (self.user_id,), one=True)
        # 1 张：放行；扣完剩 63 分
        self.assertTrue(billing.can_generate(account, images=1)[0])
        billing.charge(self.user_id, 1)
        account = db.query('SELECT * FROM accounts WHERE id = ?', (self.user_id,), one=True)
        self.assertEqual(63, account['image_balance_cents'])
        # 1 张仍放行（63 ≥ 37），2 张不够（74 > 63）
        self.assertTrue(billing.can_generate(account, images=1)[0])
        self.assertFalse(billing.can_generate(account, images=2)[0])
        # 余额再也不会被扣成负数
        self.assertGreater(account['image_balance_cents'], 0)

    # ---------------------------------------------------------------- 扣点
    def test_charge_deducts_points_and_records_usage(self):
        billing.set_billing_config(price_points_per_image=0.5)   # 0.5 点/张
        result = billing.charge(self.user_id, 2, provider='lingkeai', model='banana-2.1', source='ai')

        self.assertEqual(100, result['cost_cents'])          # 2 张 × 0.5 点 = 1 点 = 100 分
        self.assertEqual(1.0, result['cost_points'])
        self.assertEqual(3900, self._balance_cents(self.user_id))   # 4000 - 100
        row = db.query('SELECT * FROM image_usage WHERE account_id = ? ORDER BY id DESC LIMIT 1',
                       (self.user_id,), one=True)
        self.assertEqual(2, row['images'])
        self.assertEqual(100, row['cost_cents'])
        self.assertEqual('charged', row['status'])
        self.assertEqual('lingkeai', row['provider'])
        self.assertEqual(3900, row['balance_after_cents'])

    def test_charge_waits_for_zero_price_means_no_deduction(self):
        billing.set_billing_config(price_points_per_image=0)
        result = billing.charge(self.user_id, 3)
        self.assertEqual(0, result['cost_cents'])
        self.assertEqual('no_price', result['status'])
        self.assertEqual(4000, self._balance_cents(self.user_id))

    def test_charge_admin_is_exempt_but_logged(self):
        billing.set_billing_config(price_points_per_image=1)
        result = billing.charge(self.admin_id, 2)
        self.assertEqual('admin_exempt', result['status'])
        self.assertEqual(0, self._balance_cents(self.admin_id))
        row = db.query('SELECT cost_points, status FROM image_usage WHERE account_id = ?',
                       (self.admin_id,), one=True)
        self.assertEqual(0, row['cost_points'])

    def test_charge_lets_balance_go_negative_after_successful_generation(self):
        billing.set_billing_config(price_points_per_image=1)
        billing.charge(self.user_id, 100)   # 100 点 > 40 点余额
        self.assertEqual(-6000, self._balance_cents(self.user_id))
        self.assertFalse(billing.can_generate(db.query(
            'SELECT * FROM accounts WHERE id = ?', (self.user_id,), one=True))[0])

    # ---------------------------------------------------------------- 外部接口
    def test_ext_api_requires_key(self):
        response = self.client.get('/api/ext/image-billing/accounts')
        self.assertEqual(401, response.status_code)

    def test_ext_api_lists_accounts_with_balance_in_points(self):
        response = self.client.get('/api/ext/image-billing/accounts', headers=self._headers())
        payload = response.get_json()
        self.assertEqual(200, response.status_code)
        by_name = {item['username']: item for item in payload['data']['list']}
        self.assertEqual(40.0, by_name['郭滨扬']['image_balance'])
        self.assertEqual(4000, by_name['郭滨扬']['image_balance_cents'])
        self.assertTrue(by_name['admin']['admin_exempt'])
        self.assertFalse(by_name['谭科芬']['image_enabled'])

    def test_ext_api_add_balance_and_audit(self):
        response = self.client.post(
            '/api/ext/image-billing/accounts/%d/balance' % self.user_id,
            json={'points': 2.5, 'mode': 'add', 'operator': 'billing-bot', 'reason': '充值'},
            headers=self._headers(),
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(42.5, response.get_json()['data']['image_balance'])
        self.assertEqual(4250, self._balance_cents(self.user_id))
        ops = db.query('SELECT * FROM image_ops_log WHERE account_id = ?', (self.user_id,), one=True)
        self.assertEqual('balance_add', ops['action'])
        self.assertEqual('billing-bot', ops['operator'])

    def test_ext_api_set_balance_mode(self):
        response = self.client.post(
            '/api/ext/image-billing/accounts/%d/balance' % self.user_id,
            json={'points': 10, 'mode': 'set'},
            headers=self._headers(),
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(1000, self._balance_cents(self.user_id))

    def test_ext_api_rejects_negative_balance(self):
        response = self.client.post(
            '/api/ext/image-billing/accounts/%d/balance' % self.user_id,
            json={'points': -100, 'mode': 'add'},
            headers=self._headers(),
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual(4000, self._balance_cents(self.user_id))

    def test_ext_api_toggle_enabled(self):
        response = self.client.post(
            '/api/ext/image-billing/accounts/%d/enabled' % self.user_id,
            json={'enabled': False, 'reason': '余额不足'},
            headers=self._headers(),
        )
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.get_json()['data']['image_enabled'])
        allowed, _ = billing.can_generate({'role': 'salesperson', 'image_enabled': 0,
                                           'image_balance_cents': 4000})
        self.assertFalse(allowed)

    def test_ext_api_usage_by_day(self):
        billing.set_billing_config(price_points_per_image=0.5)
        billing.charge(self.user_id, 2)
        billing.charge(self.user_id, 1)
        response = self.client.get('/api/ext/image-billing/usage?days=7', headers=self._headers())
        payload = response.get_json()
        self.assertEqual(200, response.status_code)
        self.assertEqual(3, payload['data']['totals']['images'])
        self.assertEqual(1.5, payload['data']['totals']['cost_points'])

    def test_ext_api_config_round_trip(self):
        response = self.client.put('/api/ext/image-billing/config',
                                   json={'price_points_per_image': 0.02}, headers=self._headers())
        self.assertEqual(200, response.status_code)
        self.assertEqual(0.02, response.get_json()['data']['price_points_per_image'])
        response = self.client.get('/api/ext/image-billing/config', headers=self._headers())
        self.assertEqual(0.02, response.get_json()['data']['price_points_per_image'])

    def test_ext_api_ping(self):
        response = self.client.get('/api/ext/image-billing/ping', headers=self._headers())
        self.assertEqual(200, response.status_code)
        self.assertEqual('pong', response.get_json()['msg'])

    # ---------------------------------------------------------------- 页面展示
    def test_user_info_exposes_own_balance(self):
        billing.set_billing_config(price_points_per_image=0.37)
        response = self.client.get('/api/user/info',
                                   headers={'Authorization': 'Bearer ' + self.user_token})
        user = response.get_json()['data']['user']
        self.assertEqual(40.0, user['imageBalance'])
        self.assertEqual(4000, user['imageBalanceCents'])
        self.assertFalse(user['imageAdminExempt'])
        self.assertTrue(user['imageEnabled'])
        self.assertEqual(0.37, user['imagePricePoints'])

    def test_user_info_marks_admin_as_exempt(self):
        response = self.client.get('/api/user/info',
                                   headers={'Authorization': 'Bearer ' + self.admin_token})
        user = response.get_json()['data']['user']
        self.assertTrue(user['imageAdminExempt'])

    def test_admin_user_list_shows_each_account_balance(self):
        response = self.client.get('/api/users',
                                   headers={'Authorization': 'Bearer ' + self.admin_token})
        self.assertEqual(200, response.status_code)
        by_name = {item['username']: item for item in response.get_json()['data']['list']}
        self.assertEqual(40.0, by_name['郭滨扬']['imageBalance'])
        self.assertEqual(0.0, by_name['李双']['imageBalance'])
        self.assertFalse(by_name['谭科芬']['imageEnabled'])

    def test_salesperson_cannot_read_the_account_list(self):
        response = self.client.get('/api/users',
                                   headers={'Authorization': 'Bearer ' + self.user_token})
        self.assertEqual(403, response.status_code)

    # ---------------------------------------------------------------- 管理员充值
    def test_admin_can_topup_and_it_is_logged(self):
        response = self.client.post(
            '/api/image-billing/accounts/%d/topup' % self.poor_id,
            json={'points': 12.5, 'reason': '微信转账 12.5 元'},
            headers={'Authorization': 'Bearer ' + self.admin_token},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(12.5, response.get_json()['data']['image_balance'])
        self.assertEqual(1250, self._balance_cents(self.poor_id))

        logs = self.client.get('/api/image-billing/topups',
                               headers={'Authorization': 'Bearer ' + self.admin_token})
        rows = logs.get_json()['data']['list']
        self.assertEqual(1, len(rows))
        self.assertEqual(12.5, rows[0]['points'])
        self.assertEqual('admin', rows[0]['operator'])
        self.assertEqual('微信转账 12.5 元', rows[0]['reason'])
        self.assertEqual(12.5, rows[0]['balance_after'])

    def test_topup_rejects_zero_and_negative_points(self):
        for points in (0, -5):
            response = self.client.post(
                '/api/image-billing/accounts/%d/topup' % self.poor_id,
                json={'points': points},
                headers={'Authorization': 'Bearer ' + self.admin_token},
            )
            self.assertEqual(400, response.status_code)
        self.assertEqual(0, self._balance_cents(self.poor_id))

    def test_topup_requires_admin(self):
        response = self.client.post(
            '/api/image-billing/accounts/%d/topup' % self.user_id,
            json={'points': 10},
            headers={'Authorization': 'Bearer ' + self.user_token},
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual(4000, self._balance_cents(self.user_id))

    def test_topup_also_visible_through_ext_api(self):
        billing.topup(self.user_id, 5, reason='测试充值', operator='admin')
        response = self.client.get('/api/ext/image-billing/topups', headers=self._headers())
        payload = response.get_json()['data']
        self.assertEqual(1, len(payload['list']))
        self.assertEqual(5.0, payload['list'][0]['points'])
        self.assertEqual(5.0, payload['total_points'])

    # ---------------------------------------------------------------- 生图入口（应用层计费守卫）
    def _gallery_app(self):
        from routes.gallery_routes import gallery_bp
        from services.image_billing_guard import register_image_billing_guard
        app = Flask(__name__)
        app.register_blueprint(gallery_bp, url_prefix='/api')
        register_image_billing_guard(app)
        return app

    def _balance_account(self, balance_cents=None):
        account = {'id': self.user_id, 'role': 'salesperson', 'image_enabled': 1,
                   'username': '郭滨扬'}
        if balance_cents is not None:
            db.execute('UPDATE accounts SET image_balance_cents = ? WHERE id = ?',
                       (balance_cents, self.user_id))
            account['image_balance_cents'] = balance_cents
        return account

    def test_generate_is_blocked_when_balance_is_empty(self):
        billing.set_billing_config(price_points_per_image=0.37)
        client = self._gallery_app().test_client()
        account = {'id': self.poor_id, 'role': 'salesperson', 'image_enabled': 1,
                   'image_balance_cents': 0, 'username': '李双'}
        with patch('services.image_billing_guard.get_current_user', return_value=account), \
             patch('routes.gallery_routes.jlc_media_service.generate_image') as generate:
            response = client.post('/api/ai/images/generate',
                                   json={'prompt': 'umbrella', 'count': 1})
        self.assertEqual(403, response.status_code)
        self.assertIn('余额不足', response.get_json()['msg'])
        generate.assert_not_called()          # 没花第三方钱

    def test_generate_blocks_four_images_when_balance_covers_one(self):
        billing.set_billing_config(price_points_per_image=0.37)
        account = self._balance_account(100)   # 1 点
        client = self._gallery_app().test_client()
        with patch('services.image_billing_guard.get_current_user', return_value=account), \
             patch('routes.gallery_routes.jlc_media_service.generate_image') as generate:
            response = client.post('/api/ai/images/generate',
                                   json={'prompt': 'umbrella', 'count': 4})
        self.assertEqual(403, response.status_code)
        self.assertIn('本次 4 张需 1.48 点', response.get_json()['msg'])
        generate.assert_not_called()
        self.assertEqual(100, self._balance_cents(self.user_id))

    def test_suite_endpoint_is_gated_too(self):
        billing.set_billing_config(price_points_per_image=0.37)
        client = self._gallery_app().test_client()
        account = {'id': self.poor_id, 'role': 'salesperson', 'image_enabled': 1,
                   'image_balance_cents': 0, 'username': '李双'}
        with patch('services.image_billing_guard.get_current_user', return_value=account), \
             patch('routes.gallery_routes.jlc_media_service.generate_image') as generate:
            response = client.post('/api/ai/suite/jlc',
                                   json={'prompt': 'umbrella', 'images': []})
        self.assertEqual(403, response.status_code)
        generate.assert_not_called()

    def test_generate_charges_by_actual_saved_images(self):
        billing.set_billing_config(price_points_per_image=0.37)
        account = self._balance_account(100)
        client = self._gallery_app().test_client()
        fake_response = MagicMock()
        fake_response.read.return_value = VALID_PNG_BYTES
        fake_context = MagicMock()
        fake_context.__enter__.return_value = fake_response
        fake_context.__exit__.return_value = False
        with patch('services.image_billing_guard.get_current_user', return_value=account), \
             patch('routes.gallery_routes.jlc_media_service.generate_image',
                   return_value={'urls': ['https://example.test/1.png'], 'model': 'banana-2.1'}), \
             patch('routes.gallery_routes.urllib.request.urlopen', return_value=fake_context), \
             patch('routes.gallery_routes._save_bytes',
                   return_value={'id': '1', 'url': '/api/gallery/files/1.png'}):
            response = client.post('/api/ai/images/generate',
                                   json={'prompt': 'umbrella', 'count': 1})
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(response.get_json()['data']['assets']))
        self.assertEqual(100 - 37, self._balance_cents(self.user_id))
        row = db.query('SELECT * FROM image_usage WHERE account_id = ? ORDER BY id DESC LIMIT 1',
                       (self.user_id,), one=True)
        self.assertEqual('charged', row['status'])
        self.assertEqual(37, row['cost_cents'])
        self.assertEqual('lingkeai', row['provider'])
        self.assertEqual('banana-2.1', row['model'])

    def test_generated_asset_is_owned_by_current_user(self):
        """2026-10-28 重构曾把 user_id 归属弄丢（生图后业务员看不到自己的图），这里守住。"""
        import tempfile
        import routes.gallery_routes as gallery_routes
        with tempfile.TemporaryDirectory() as gallery_dir:
            with patch.object(gallery_routes, 'GALLERY_DIR', gallery_dir), \
                 patch('routes.gallery_routes.get_current_user',
                       return_value={'id': self.user_id, 'role': 'salesperson'}):
                asset = gallery_routes._save_bytes(
                    VALID_PNG_BYTES, 'unit.png', 'ai', 'p', ['测试'])
        row = db.query('SELECT user_id, provider, model FROM gallery_assets WHERE id = ?',
                       (int(asset['id']),), one=True)
        self.assertEqual(self.user_id, row['user_id'])

    def test_gallery_folder_endpoints_are_registered(self):
        rules = {rule.rule for rule in self._gallery_app().url_map.iter_rules()}
        self.assertIn('/api/gallery/folders', rules)
        self.assertIn('/api/gallery/folders/<int:folder_id>', rules)
        self.assertIn('/api/ai/suite/jlc', rules)
        self.assertIn('/api/ai/images/generate', rules)

    def test_provider_parameter_is_ignored_and_always_calls_jlc(self):
        billing.set_billing_config(price_points_per_image=0.37)
        account = self._balance_account(1000)
        client = self._gallery_app().test_client()
        with patch('services.image_billing_guard.get_current_user', return_value=account), \
             patch('routes.gallery_routes.jlc_media_service.generate_image',
                   return_value={'urls': [], 'model': 'banana-2.1'}) as generate:
            response = client.post('/api/ai/images/generate',
                                   json={'prompt': 'umbrella', 'count': 1,
                                         'provider': 'apiyi', 'model': 'gpt-image-2.5-all'})
        # 已无 apiyi 分支：仍调用凌科，出不了图则是 502，且绝不调用别的 provider
        generate.assert_called_once()
        self.assertEqual('umbrella', generate.call_args[0][0])
        self.assertEqual(502, response.status_code)


if __name__ == '__main__':
    unittest.main()
