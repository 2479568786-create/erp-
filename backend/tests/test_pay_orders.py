"""充值模块单测：验签/解密（微信支付公钥模式）+ 订单状态机幂等 + 风控口径。

不依赖真实商户号：用本地生成的 RSA 密钥对模拟「微信支付公钥」，用随机 32 位 APIv3 密钥模拟解密。
"""
import base64
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from services import pay_service, wxpay_service


def _make_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, priv.decode(), pub.decode()


class WxpayCryptoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key, cls.priv_pem, cls.pub_pem = _make_keypair()
        cls.tmpdir = tempfile.mkdtemp()
        with open(os.path.join(cls.tmpdir, 'wechatpay_pub_key.pem'), 'w') as f:
            f.write(cls.pub_pem)
        cls.apiv3 = 'A' * 32

    def setUp(self):
        os.environ.update({
            'WXPAY_MCHID': '1900000001',
            'WXPAY_APPID': 'wxtestappid',
            'WXPAY_SERIAL_NO': 'SERIALTEST',
            'WXPAY_APIV3_KEY': self.apiv3,
            'WXPAY_NOTIFY_URL': 'https://example.test/api/pay/wx/notify',
            'WXPAY_PUB_KEY_ID': 'PUB_KEY_ID_TEST',
            'WXPAY_CERT_DIR': self.tmpdir,
            'WXPAY_PUB_KEY_FILE': os.path.join(self.tmpdir, 'wechatpay_pub_key.pem'),
            'WXPAY_ENABLED': '0',
        })
        wxpay_service._public_key_cache.clear()

    def _sign(self, ts, nonce, body):
        message = '%s\n%s\n%s\n' % (ts, nonce, body)
        sig = self.key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode()

    def test_verify_ok(self):
        body = json.dumps({'id': 'evt1'})
        ts = str(int(time.time()))
        headers = {'Wechatpay-Timestamp': ts, 'Wechatpay-Nonce': 'NONCE1',
                   'Wechatpay-Serial': 'PUB_KEY_ID_TEST',
                   'Wechatpay-Signature': self._sign(ts, 'NONCE1', body)}
        ok, reason = wxpay_service.verify_notify(headers, body.encode())
        self.assertTrue(ok, reason)

    def test_verify_tampered_body(self):
        body = json.dumps({'id': 'evt1'})
        ts = str(int(time.time()))
        headers = {'Wechatpay-Timestamp': ts, 'Wechatpay-Nonce': 'NONCE1',
                   'Wechatpay-Serial': 'PUB_KEY_ID_TEST',
                   'Wechatpay-Signature': self._sign(ts, 'NONCE1', body)}
        ok, reason = wxpay_service.verify_notify(headers, body.replace('evt1', 'evt2').encode())
        self.assertFalse(ok)
        self.assertIn('bad_signature', reason)

    def test_verify_skew_rejected(self):
        body = '{}'
        ts = str(int(time.time()) - 3600)
        headers = {'Wechatpay-Timestamp': ts, 'Wechatpay-Nonce': 'N1',
                   'Wechatpay-Serial': 'PUB_KEY_ID_TEST',
                   'Wechatpay-Signature': self._sign(ts, 'N1', body)}
        ok, reason = wxpay_service.verify_notify(headers, body.encode())
        self.assertFalse(ok)
        self.assertEqual(reason, 'timestamp_skew')

    def test_verify_unknown_serial(self):
        body = '{}'
        ts = str(int(time.time()))
        headers = {'Wechatpay-Timestamp': ts, 'Wechatpay-Nonce': 'N1',
                   'Wechatpay-Serial': 'OTHER_ID',
                   'Wechatpay-Signature': self._sign(ts, 'N1', body)}
        ok, reason = wxpay_service.verify_notify(headers, body.encode())
        self.assertFalse(ok)
        self.assertEqual(reason, 'unknown_serial')

    def test_decrypt_roundtrip(self):
        payload = {'out_trade_no': 'RC1', 'trade_state': 'SUCCESS', 'amount': {'total': 1000}}
        nonce = 'abcdefghijkl'
        ad = 'transaction'
        raw = json.dumps(payload).encode()
        ct = AESGCM(self.apiv3.encode()).encrypt(nonce.encode(), raw, ad.encode())
        resource = {'algorithm': 'AEAD_AES_256_GCM', 'nonce': nonce, 'associated_data': ad,
                    'ciphertext': base64.b64encode(ct).decode()}
        got = wxpay_service.decrypt_resource(resource)
        self.assertEqual(got['out_trade_no'], 'RC1')
        self.assertEqual(got['amount']['total'], 1000)

    def test_decrypt_bad_key(self):
        nonce = 'abcdefghijkl'
        ct = AESGCM(('B' * 32).encode()).encrypt(nonce.encode(), b'{}', None)
        resource = {'nonce': nonce, 'associated_data': '', 'ciphertext': base64.b64encode(ct).decode()}
        with self.assertRaises(Exception):
            wxpay_service.decrypt_resource(resource)

    def test_missing_config_lists_names(self):
        os.environ['WXPAY_APPID'] = ''
        missing = wxpay_service.missing_config()
        self.assertIn('WXPAY_APPID', missing)


class PayOrderStateTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        pay_service.ensure_tables(conn=self.conn)

    def tearDown(self):
        self.conn.close()

    def test_validate_points_tiers(self):
        ok, pts, _ = pay_service.validate_points(10)
        self.assertTrue(ok)
        self.assertEqual(pts, 10)
        ok, _, reason = pay_service.validate_points(11)
        self.assertFalse(ok)
        self.assertIn('档位', reason)
        ok, _, _ = pay_service.validate_points('abc')
        self.assertFalse(ok)

    def test_create_and_get(self):
        pay_service.create_order('RC001', 8, 8, 10, 1000, conn=self.conn)
        order = pay_service.get_order('RC001', conn=self.conn)
        self.assertEqual(order['status'], 'created')
        self.assertEqual(order['amount_cents'], 1000)
        self.assertEqual(float(order['points']), 10.0)

    def test_credit_is_idempotent(self):
        calls = []

        def fake_adjust(account_id, points, mode, reason, operator):
            calls.append((account_id, points, mode, operator))

        pay_service.create_order('RC002', 8, 8, 30, 3000, conn=self.conn)
        pay_service.mark_paid('RC002', 'TX2', conn=self.conn)
        first = pay_service.credit_order('RC002', conn=self.conn, adjust=fake_adjust)
        second = pay_service.credit_order('RC002', conn=self.conn, adjust=fake_adjust)
        self.assertEqual(first['reason'], 'credited')
        self.assertEqual(second['reason'], 'already_credited')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 8)
        self.assertEqual(float(calls[0][1]), 30.0)
        self.assertEqual(calls[0][3], 'wxpay:RC002')

    def test_credit_requires_paid(self):
        pay_service.create_order('RC003', 8, 8, 10, 1000, conn=self.conn)
        result = pay_service.credit_order('RC003', conn=self.conn, adjust=lambda *a, **k: None)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'not_paid')

    def test_claim_credit_only_once(self):
        pay_service.create_order('RC004', 8, 8, 10, 1000, conn=self.conn)
        pay_service.mark_paid('RC004', 'TX4', conn=self.conn)
        self.assertTrue(pay_service.claim_credit('RC004', conn=self.conn))
        self.assertFalse(pay_service.claim_credit('RC004', conn=self.conn))

    def test_mark_paid_is_guarded(self):
        pay_service.create_order('RC005', 8, 8, 10, 1000, conn=self.conn)
        self.assertEqual(pay_service.mark_paid('RC005', 'TX5', conn=self.conn), 1)
        self.assertEqual(pay_service.mark_paid('RC005', 'TX5', conn=self.conn), 0)
        pay_service.close_order('RC005', conn=self.conn)
        self.assertEqual(pay_service.get_order('RC005', conn=self.conn)['status'], 'paid')

    def test_daily_points_counts_paid_and_credited(self):
        pay_service.create_order('RC006', 8, 8, 50, 5000, conn=self.conn)
        pay_service.create_order('RC007', 8, 8, 30, 3000, conn=self.conn)
        pay_service.mark_paid('RC006', 'TX6', conn=self.conn)
        pay_service.close_order('RC007', conn=self.conn)
        self.assertEqual(pay_service.daily_points(8, conn=self.conn), 50.0)

    def test_expire_created(self):
        pay_service.create_order('RC008', 8, 8, 10, 1000, conn=self.conn)
        self.conn.execute("UPDATE pay_orders SET created_at = datetime('now', '-30 minutes') "
                          "WHERE out_trade_no = 'RC008'")
        self.conn.commit()
        pay_service.expire_created(conn=self.conn)
        self.assertEqual(pay_service.get_order('RC008', conn=self.conn)['status'], 'closed')

    def test_log_event(self):
        pay_service.log_event('RC009', 'notify_ok', 'credit=credited', conn=self.conn)
        rows = self.conn.execute("SELECT event FROM pay_events WHERE out_trade_no='RC009'").fetchall()
        self.assertEqual(rows[0][0], 'notify_ok')

    def test_daily_points_uses_local_day_boundary(self):
        """库里 created_at 是 UTC；「今天」按本地日算，跨零点不能错一天。"""
        from datetime import datetime, timedelta, timezone
        local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        today_utc = (local_midnight + timedelta(minutes=30)).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        yesterday_utc = (local_midnight - timedelta(minutes=30)).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        pay_service.create_order('RC010', 8, 8, 20, 2000, conn=self.conn)
        pay_service.mark_paid('RC010', 'TX10', conn=self.conn)
        self.conn.execute("UPDATE pay_orders SET created_at=? WHERE out_trade_no='RC010'", (today_utc,))
        pay_service.create_order('RC011', 8, 8, 40, 4000, conn=self.conn)
        pay_service.mark_paid('RC011', 'TX11', conn=self.conn)
        self.conn.execute("UPDATE pay_orders SET created_at=? WHERE out_trade_no='RC011'", (yesterday_utc,))
        self.conn.commit()
        self.assertEqual(pay_service.daily_points(8, conn=self.conn), 20.0)

    def test_orders_last_minute_uses_utc(self):
        from datetime import datetime, timezone
        recent_utc = datetime.now().astimezone().astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        pay_service.create_order('RC012', 8, 8, 10, 1000, conn=self.conn)
        self.conn.execute("UPDATE pay_orders SET created_at=? WHERE out_trade_no='RC012'", (recent_utc,))
        self.conn.commit()
        self.assertEqual(pay_service.orders_last_minute(8, conn=self.conn), 1)

if __name__ == '__main__':
    unittest.main()
