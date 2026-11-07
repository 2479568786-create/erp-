# -*- coding: utf-8 -*-
"""A2：支付宝异步通知处理（验签 / 三层幂等 / 金额校验 / 状态分支）+ 回调路由应答格式。

用本地一次性测试密钥（与 A1 共用同一对）签名构造回调，不依赖沙箱或真实商户。
数据库用 sqlite `:memory:` + `conn` 注入，不碰任何真实库。
"""
import base64
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services import alipay_notify, alipay_service, pay_service  # noqa: E402
from tests.test_pay_alipay import TEST_PUBLIC_B64, _priv  # noqa: E402

APP_ID = '2021000000000001'
SELLER_ID = '2088000000000001'
OUT_NO = 'RC20261106120000000001'
TRADE_NO = '2026110622001400000000000001'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='alipay_notify_')
        self.pub_path = os.path.join(self.tmpdir, 'alipay_public_key.pem')
        with open(self.pub_path, 'wb') as f:
            f.write(base64.b64decode(TEST_PUBLIC_B64))
        self._env = patch.dict(os.environ, {
            'ALIPAY_ENABLED': '1',
            'ALIPAY_APPID': APP_ID,
            'ALIPAY_SELLER_ID': SELLER_ID,
            'ALIPAY_PUBLIC_KEY_PATH': self.pub_path,
            'ALIPAY_NOTIFY_URL': 'https://example.test/api/pay/alipay/notify',
        }, clear=False)
        self._env.start()
        alipay_service._public_key_cache.clear()

        self.conn = sqlite3.connect(':memory:')
        pay_service.ensure_tables(conn=self.conn)
        self.credited = []

        def fake_adjust(account_id, points, mode, reason, operator):
            self.credited.append({'account_id': account_id, 'points': points,
                                  'mode': mode, 'reason': reason, 'operator': operator})
        self.adjust = fake_adjust
        self.calls = 0

    def tearDown(self):
        self._env.stop()
        self.conn.close()
        alipay_service._public_key_cache.clear()

    # ---- 工具 ----
    def _seed(self, out_trade_no=OUT_NO, amount_cents=1000, channel='alipay', points=10):
        pay_service.create_order(out_trade_no, 8, 8, points, amount_cents,
                                 conn=self.conn, channel=channel)

    def _form(self, **over):
        form = {
            'app_id': APP_ID,
            'seller_id': SELLER_ID,
            'out_trade_no': OUT_NO,
            'trade_no': TRADE_NO,
            'trade_status': 'TRADE_SUCCESS',
            'total_amount': '10.00',
            'notify_id': '2026110600222000000001',
            'notify_time': '2026-11-06 12:00:05',
            'charset': 'utf-8',
            'version': '1.0',
            'sign_type': 'RSA2',
        }
        form.update(over)
        form['sign'] = alipay_service.sign(form, private_key=_priv(), mode='notify')
        return form

    def _digest(self, form):
        # 模拟「回调原文」的指纹：内容变则指纹变，够用于去重测试
        return pay_service.digest_of(json.dumps(form, sort_keys=True, ensure_ascii=False))

    def _handle(self, form, digest=None):
        self.calls += 1
        return alipay_notify.handle_notify(form, digest or self._digest(form),
                                          conn=self.conn, adjust=self.adjust)

    def _order(self, out_trade_no=OUT_NO):
        return pay_service.get_order(out_trade_no, conn=self.conn)

    def _events(self, out_trade_no=None):
        if out_trade_no is None:
            cur = self.conn.execute("SELECT event FROM pay_events ORDER BY id")
        else:
            cur = self.conn.execute("SELECT event FROM pay_events WHERE out_trade_no = ? "
                                    "ORDER BY id", (out_trade_no,))
        return [row[0] for row in cur.fetchall()]


class HappyPathTests(Base):
    def test_valid_notify_credits_once_with_alipay_labels(self):
        self._seed()
        status, body, info = self._handle(self._form())
        self.assertEqual((status, body), (200, 'success'), info)
        self.assertTrue(info['credited'])
        order = self._order()
        self.assertEqual(order['status'], 'credited')
        self.assertEqual(order['transaction_id'], TRADE_NO)
        self.assertEqual(order['notify_id'], '2026110600222000000001')
        self.assertEqual(len(self.credited), 1)
        entry = self.credited[0]
        self.assertEqual((entry['account_id'], entry['points'], entry['mode']), (8, 10.0, 'add'))
        self.assertEqual(entry['operator'], 'alipay:' + OUT_NO)
        self.assertIn('支付宝充值', entry['reason'])
        self.assertIn('alipay_notify_ok', self._events())

    def test_trade_finished_also_credits(self):
        self._seed()
        status, body, _ = self._handle(self._form(trade_status='TRADE_FINISHED'))
        self.assertEqual((status, body), (200, 'success'))
        self.assertEqual(self._order()['status'], 'credited')
        self.assertEqual(len(self.credited), 1)


class IdempotencyTests(Base):
    def test_same_notify_five_times_credits_once(self):
        self._seed()
        form = self._form()
        digest = self._digest(form)
        results = [self._handle(form, digest) for _ in range(5)]
        self.assertTrue(all(r[0] == 200 and r[1] == 'success' for r in results))
        self.assertEqual(len(self.credited), 1)
        self.assertEqual(self._order()['status'], 'credited')
        self.assertEqual(self._events().count('alipay_notify_duplicate'), 4)

    def test_new_notify_id_same_trade_no_no_double_credit(self):
        self._seed()
        self._handle(self._form())
        second = self._form(notify_id='2026110600222000000002')
        status, body, info = self._handle(second)
        self.assertEqual((status, body), (200, 'success'))
        self.assertEqual(len(self.credited), 1)
        self.assertEqual(self._order()['status'], 'credited')

    def test_credit_not_repeated_after_flip_to_credited(self):
        """第二层/第三层幂等：状态已 credited 时再走一遍也不会加点。"""
        self._seed()
        self._handle(self._form())
        order = self._order()
        self.assertEqual(order['status'], 'credited')
        result = pay_service.credit_order(OUT_NO, conn=self.conn, adjust=self.adjust)
        self.assertEqual(result['reason'], 'already_credited')
        self.assertEqual(len(self.credited), 1)


class SecurityTests(Base):
    def test_bad_signature_returns_fail_401(self):
        self._seed()
        form = self._form()
        form['sign'] = base64.b64encode(b'x' * 256).decode()
        status, body, info = self._handle(form)
        self.assertEqual((status, body), (401, 'fail'), info)
        self.assertEqual(self._order()['status'], 'created')
        self.assertEqual(self.credited, [])
        self.assertIn('alipay_notify_bad_sig', self._events())

    def test_missing_sign_returns_fail(self):
        self._seed()
        form = self._form()
        form.pop('sign')
        status, body, _ = self._handle(form)
        self.assertEqual((status, body), (401, 'fail'))

    def test_wrong_app_id_returns_fail_403(self):
        self._seed()
        status, body, info = self._handle(self._form(app_id='2021000000009999'))
        self.assertEqual((status, body), (403, 'fail'), info)
        self.assertEqual(self._order()['status'], 'created')
        self.assertEqual(self.credited, [])

    def test_seller_mismatch_returns_fail_403(self):
        self._seed()
        status, body, info = self._handle(self._form(seller_id='2088000000009999'))
        self.assertEqual((status, body), (403, 'fail'), info)
        self.assertEqual(self.credited, [])

    def test_verify_error_returns_503_when_key_missing(self):
        self._seed()
        bad_path = os.path.join(self.tmpdir, 'missing.pem')
        with patch.dict(os.environ, {'ALIPAY_PUBLIC_KEY_PATH': bad_path}):
            alipay_service._public_key_cache.clear()
            status, body, info = self._handle(self._form())
        self.assertEqual((status, body), (503, 'fail'), info)
        self.assertEqual(self._order()['status'], 'created')


class AmountTests(Base):
    def test_mismatched_amount_acked_but_not_credited(self):
        self._seed(amount_cents=1000)
        status, body, info = self._handle(self._form(total_amount='9.99'))
        self.assertEqual((status, body), (200, 'success'), info)
        self.assertEqual(info['event'], 'bad_amount')
        self.assertEqual(self._order()['status'], 'created')
        self.assertEqual(self.credited, [])
        self.assertIn('alipay_notify_bad_amount', self._events())

    def test_bad_amount_format_acked_but_not_credited(self):
        self._seed()
        status, body, info = self._handle(self._form(total_amount='10.001'))
        self.assertEqual((status, body), (200, 'success'), info)
        self.assertEqual(info['event'], 'bad_amount_format')
        self.assertEqual(self.credited, [])

    def test_amount_equal_after_normalization(self):
        self._seed(amount_cents=1000)
        status, body, info = self._handle(self._form(total_amount='10'))
        self.assertEqual((status, body), (200, 'success'), info)
        self.assertEqual(len(self.credited), 1)


class StatusBranchTests(Base):
    def test_wait_buyer_pay_acked_without_credit(self):
        self._seed()
        status, body, info = self._handle(self._form(trade_status='WAIT_BUYER_PAY'))
        self.assertEqual((status, body), (200, 'success'), info)
        self.assertEqual(self._order()['status'], 'created')
        self.assertEqual(self.credited, [])

    def test_trade_closed_closes_created_order(self):
        self._seed()
        status, body, info = self._handle(self._form(trade_status='TRADE_CLOSED'))
        self.assertEqual((status, body), (200, 'success'), info)
        self.assertEqual(self._order()['status'], 'closed')
        self.assertEqual(self.credited, [])

    def test_unknown_order_acked(self):
        form = self._form(out_trade_no='RC_NOT_EXIST')
        status, body, info = self._handle(form)
        self.assertEqual((status, body), (200, 'success'), info)
        self.assertEqual(info['event'], 'unknown_order')

    def test_wxpay_order_ignored_by_alipay_notify(self):
        self._seed(channel='wxpay')
        status, body, info = self._handle(self._form())
        self.assertEqual((status, body), (200, 'success'), info)
        self.assertEqual(info['event'], 'channel_mismatch')
        self.assertEqual(self._order()['channel'], 'wxpay')
        self.assertEqual(self._order()['status'], 'created')

    def test_trade_no_mismatch_does_not_credit_twice(self):
        self._seed()
        self._handle(self._form())
        self.assertEqual(len(self.credited), 1)
        # 同一订单号又来一条「另一个支付宝交易号」的通知
        second = self._form(trade_no='2026110622001400000000000099',
                            notify_id='2026110600222000000003')
        status, body, info = self._handle(second)
        self.assertEqual((status, body), (200, 'success'))
        self.assertEqual(info['event'], 'trade_no_mismatch')
        self.assertEqual(len(self.credited), 1)
        self.assertEqual(self._order()['transaction_id'], TRADE_NO)


class RouteTests(unittest.TestCase):
    """路由契约：应答必须是纯文本 success/fail（不能用 JSON），超大报文 413。"""

    @classmethod
    def setUpClass(cls):
        from routes.pay_routes import pay_bp
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(pay_bp, url_prefix='/api')
        app.config['TESTING'] = True
        cls.client = app.test_client()

    def setUp(self):
        # 413 分支会写 pay_events 审计；全量跑时别的模块可能把 db.DB_PATH 指到没有建表的库，
        # 这里显式给本类一个独立库并建表，避免「单独跑绿、全量跑红」的假失败。
        import db as _db
        self._tmpdir = tempfile.mkdtemp(prefix='alipay_notify_route_')
        self._db_patch = patch.object(_db, 'DB_PATH',
                                      os.path.join(self._tmpdir, 'test.db'))
        self._db_patch.start()
        self._prev_ready = pay_service._table_ready
        pay_service._table_ready = False
        pay_service.ensure_tables()
        self.addCleanup(self._restore_db)

    def _restore_db(self):
        pay_service._table_ready = self._prev_ready
        self._db_patch.stop()

    def test_route_returns_plain_text_success(self):
        with patch.object(alipay_notify, 'handle_notify',
                          return_value=(200, 'success', {'event': 'notify_ok'})) as m:
            resp = self.client.post('/api/pay/alipay/notify',
                                    data={'out_trade_no': OUT_NO, 'sign': 'x'},
                                    content_type='application/x-www-form-urlencoded')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b'success')
        self.assertTrue(resp.mimetype.startswith('text/plain'), resp.mimetype)
        self.assertTrue(m.called)
        self.assertEqual(m.call_args[0][0].get('out_trade_no'), OUT_NO)

    def test_route_passes_digest_of_raw_body(self):
        with patch.object(alipay_notify, 'handle_notify',
                          return_value=(200, 'success', {})) as m:
            self.client.post('/api/pay/alipay/notify', data={'a': '1'},
                             content_type='application/x-www-form-urlencoded')
        digest = m.call_args[0][1]
        self.assertEqual(digest, pay_service.digest_of(b'a=1'))

    def test_route_returns_plain_text_fail_on_bad_signature(self):
        with patch.object(alipay_notify, 'handle_notify', return_value=(401, 'fail', {})):
            resp = self.client.post('/api/pay/alipay/notify', data={'a': '1'})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.data, b'fail')

    def test_route_rejects_oversized_body(self):
        big = 'x' * (64 * 1024 + 10)
        resp = self.client.post('/api/pay/alipay/notify', data={'a': big})
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(resp.data, b'fail')

    def test_route_rejects_get(self):
        self.assertEqual(self.client.get('/api/pay/alipay/notify').status_code, 405)


if __name__ == '__main__':
    unittest.main()
