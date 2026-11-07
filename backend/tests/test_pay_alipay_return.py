# -*- coding: utf-8 -*-
"""电脑网站支付同步回跳路由：验签 + 302 回前端 + **绝不入账**（入账只认异步通知）。"""
import base64
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db  # noqa: E402
from routes import pay_routes  # noqa: E402
from services import alipay_service, pay_service  # noqa: E402
from tests.test_pay_alipay import TEST_PUBLIC_B64, _priv  # noqa: E402

APP_ID = '2021007118626312'


class ReturnRouteBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='alipay_return_')
        self.pub_path = os.path.join(self.tmpdir, 'alipay_public_key.pem')
        with open(self.pub_path, 'wb') as f:
            f.write(base64.b64decode(TEST_PUBLIC_B64))
        self._env = patch.dict(os.environ, {
            'ALIPAY_ENABLED': '0',
            'ALIPAY_APPID': APP_ID,
            'ALIPAY_PUBLIC_KEY_PATH': self.pub_path,
        }, clear=False)
        self._env.start()
        alipay_service._public_key_cache.clear()

        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self._db_patch = patch.object(db, 'DB_PATH', self.db_path)
        self._db_patch.start()
        self._prev_ready = pay_service._table_ready
        pay_service._table_ready = False
        pay_service.ensure_tables()

        app = Flask(__name__)
        app.register_blueprint(pay_routes.pay_bp, url_prefix='/api')
        app.config['TESTING'] = True
        self.client = app.test_client()

    def tearDown(self):
        pay_service._table_ready = self._prev_ready
        self._db_patch.stop()
        self._env.stop()
        alipay_service._public_key_cache.clear()

    def _seed(self, out_trade_no='RC20261107000001', amount_cents=1000, channel='alipay'):
        pay_service.create_order(out_trade_no, 8, 8, 10, amount_cents,
                                 conn=sqlite3.connect(self.db_path), channel=channel)
        return out_trade_no

    def _params(self, **over):
        params = {
            'app_id': APP_ID,
            'out_trade_no': 'RC20261107000001',
            'trade_no': '2026110722001400000000000001',
            'total_amount': '10.00',
            'charset': 'utf-8',
            'method': 'alipay.trade.page.pay.return',
            'timestamp': '2026-11-07 22:00:00',
            'version': '1.0',
            'sign_type': 'RSA2',
        }
        params.update(over)
        params['sign'] = alipay_service.sign(params, private_key=_priv(), mode='notify')
        return params

    def _get(self, params):
        return self.client.get('/api/pay/alipay/return', query_string=params)

    def _order(self, out_trade_no):
        return db.query('SELECT * FROM pay_orders WHERE out_trade_no = ?',
                        (out_trade_no,), one=True)


class ReturnHappyPathTests(ReturnRouteBase):
    def test_valid_signature_redirects_with_order(self):
        self._seed()
        res = self._get(self._params())
        self.assertEqual(res.status_code, 302, res.data[:200])
        loc = res.headers.get('Location', '')
        self.assertTrue(loc.startswith('/?alipay=return'), loc)
        self.assertIn('out_trade_no=RC20261107000001', loc)
        self.assertIn('state=created', loc)

    def test_unknown_order_still_redirects(self):
        res = self._get(self._params(out_trade_no='RC_NOT_EXIST_1'))
        self.assertEqual(res.status_code, 302)
        loc = res.headers.get('Location', '')
        self.assertIn('alipay=return', loc)
        self.assertNotIn('state=', loc)

    def test_return_never_credits(self):
        out_no = self._seed()
        res = self._get(self._params(out_trade_no=out_no))
        self.assertEqual(res.status_code, 302)
        order = self._order(out_no)
        self.assertEqual(order['status'], 'created')      # 回跳不改变订单状态
        events = [r['event'] for r in db.query(
            "SELECT event FROM pay_events WHERE out_trade_no = ?", (out_no,))]
        self.assertIn('alipay_return_ok', events)
        self.assertNotIn('credit_ok', events)

    def test_events_recorded_with_state(self):
        out_no = self._seed()
        self._get(self._params(out_trade_no=out_no))
        row = db.query("SELECT detail FROM pay_events WHERE out_trade_no = ? "
                       "AND event = 'alipay_return_ok'", (out_no,), one=True)
        self.assertIsNotNone(row)
        self.assertIn('state=created', row['detail'])


class ReturnSecurityTests(ReturnRouteBase):
    def test_tampered_out_trade_no_rejected(self):
        params = self._params()
        params['out_trade_no'] = 'RC_ATTACKER_1'
        res = self._get(params)
        self.assertEqual(res.status_code, 401)
        self.assertNotIn('Location', res.headers)
        self.assertIn('校验未通过', res.data.decode('utf-8', 'replace'))

    def test_missing_sign_rejected(self):
        params = self._params()
        params.pop('sign')
        res = self._get(params)
        self.assertEqual(res.status_code, 401)
        self.assertIn('缺少签名', res.data.decode('utf-8', 'replace'))

    def test_non_rsa2_rejected(self):
        params = self._params()
        params['sign_type'] = 'RSA'
        res = self._get(params)
        self.assertEqual(res.status_code, 401)

    def test_bad_signature_logged(self):
        params = self._params(out_trade_no='RC_BADSIG_1')
        params['sign'] = 'A' * 256
        res = self._get(params)
        self.assertEqual(res.status_code, 401)
        events = [r['event'] for r in db.query(
            "SELECT event FROM pay_events WHERE out_trade_no = ?", ('RC_BADSIG_1',))]
        self.assertIn('alipay_return_bad_sig', events)


class RouteRegistrationTests(unittest.TestCase):
    def test_return_route_registered(self):
        app = Flask(__name__)
        app.register_blueprint(pay_routes.pay_bp, url_prefix='/api')
        rules = [str(r) for r in app.url_map.iter_rules()]
        self.assertIn('/api/pay/alipay/return', rules)
        self.assertIn('/api/pay/alipay/notify', rules)


if __name__ == '__main__':
    unittest.main()
