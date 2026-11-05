"""充值模块单测：验签/解密（微信支付公钥模式）+ 订单状态机幂等 + 回调去重 + 档位配置。

不依赖真实商户号：本地生成 RSA 密钥对模拟「微信支付公钥」，随机 32 位 APIv3 密钥模拟解密。
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
        cls.pub_path = os.path.join(cls.tmpdir, 'pub_key.pem')
        cls.priv_path = os.path.join(cls.tmpdir, 'apiclient_key.pem')
        with open(cls.pub_path, 'w') as f:
            f.write(cls.pub_pem)
        with open(cls.priv_path, 'w') as f:
            f.write(cls.priv_pem)
        cls.apiv3 = 'A' * 32

    def setUp(self):
        os.environ.update({
            'WECHATPAY_ENABLED': '0',
            'WECHATPAY_MCHID': '1900000001',
            'WECHATPAY_APPID': 'wxtestappid',
            'WECHATPAY_API_V3_KEY': self.apiv3,
            'WECHATPAY_CERT_SERIAL': 'SERIALTEST',
            'WECHATPAY_CERT_DIR': self.tmpdir,
            'WECHATPAY_PRIVATE_KEY_PATH': self.priv_path,
            'WECHATPAY_PUBKEY_ID': 'PUB_KEY_ID_TEST',
            'WECHATPAY_PUBKEY_PATH': self.pub_path,
            'WECHATPAY_NOTIFY_URL': 'https://example.test/api/payment/wechat/callback',
        })
        wxpay_service._public_key_cache.clear()
        wxpay_service._private_key_cache.clear()

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

    def test_decrypt_bad_key(self):
        nonce = 'abcdefghijkl'
        ct = AESGCM(('B' * 32).encode()).encrypt(nonce.encode(), b'{}', None)
        resource = {'nonce': nonce, 'associated_data': '', 'ciphertext': base64.b64encode(ct).decode()}
        with self.assertRaises(Exception):
            wxpay_service.decrypt_resource(resource)

    def test_missing_config_lists_names(self):
        os.environ['WECHATPAY_APPID'] = ''
        missing = wxpay_service.missing_config()
        self.assertIn('WECHATPAY_APPID', missing)

    def test_missing_pubkey_file_is_reported(self):
        os.environ['WECHATPAY_PUBKEY_PATH'] = os.path.join(self.tmpdir, 'nope.pem')
        wxpay_service._public_key_cache.clear()
        missing = wxpay_service.missing_config()
        self.assertIn('WECHATPAY_PUBKEY_FILE', missing)

    def test_create_order_requires_appid(self):
        os.environ['WECHATPAY_APPID'] = ''
        with self.assertRaises(RuntimeError) as ctx:
            wxpay_service.create_native_order('RC0', 'x', 100)
        self.assertIn('WECHATPAY_APPID', str(ctx.exception))

    def test_authorization_header_shape(self):
        header = wxpay_service.build_authorization('POST', '/v3/pay/transactions/native', '{"a":1}')
        self.assertTrue(header.startswith('WECHATPAY2-SHA256-RSA2048 '))
        self.assertIn('mchid="1900000001"', header)
        self.assertIn('serial_no="SERIALTEST"', header)


class PayOrderStateTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop('WECHATPAY_TIERS', None)
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

    def test_tiers_default_without_500(self):
        self.assertEqual(pay_service.tiers(), [10, 30, 50, 100, 200])
        ok, _, reason = pay_service.validate_points(500)
        self.assertFalse(ok, reason)

    def test_tiers_from_env(self):
        os.environ['WECHATPAY_TIERS'] = '10, 20 ,30'
        try:
            self.assertEqual(pay_service.tiers(), [10, 20, 30])
            ok, pts, _ = pay_service.validate_points(20)
            self.assertTrue(ok)
            self.assertEqual(pts, 20)
        finally:
            os.environ.pop('WECHATPAY_TIERS', None)

    def test_create_and_get(self):
        pay_service.create_order('RC001', 8, 8, 10, 1000, conn=self.conn)
        order = pay_service.get_order('RC001', conn=self.conn)
        self.assertEqual(order['status'], 'created')
        self.assertEqual(order['amount_cents'], 1000)

    def test_prepay_id_stored(self):
        pay_service.create_order('RC001b', 8, 8, 10, 1000, conn=self.conn)
        pay_service.set_code_url('RC001b', 'weixin://x', 'PREPAY123', conn=self.conn)
        order = pay_service.get_order('RC001b', conn=self.conn)
        self.assertEqual(order['prepay_id'], 'PREPAY123')
        self.assertEqual(order['code_url'], 'weixin://x')

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

    def test_credit_requires_paid(self):
        pay_service.create_order('RC003', 8, 8, 10, 1000, conn=self.conn)
        result = pay_service.credit_order('RC003', conn=self.conn, adjust=lambda *a, **k: None)
        self.assertFalse(result['ok'])

    def test_claim_credit_only_once(self):
        pay_service.create_order('RC004', 8, 8, 10, 1000, conn=self.conn)
        pay_service.mark_paid('RC004', 'TX4', conn=self.conn)
        self.assertTrue(pay_service.claim_credit('RC004', conn=self.conn))
        self.assertFalse(pay_service.claim_credit('RC004', conn=self.conn))

    def test_notify_digest_dedupe(self):
        digest = pay_service.digest_of(b'{"id":"evt-1"}')
        pay_service.create_order('RC005', 8, 8, 10, 1000, conn=self.conn)
        self.assertFalse(pay_service.digest_seen('RC005', digest, conn=self.conn))
        pay_service.mark_paid('RC005', 'TX5', notify_id='evt-1', notify_digest=digest, conn=self.conn)
        self.assertTrue(pay_service.digest_seen('RC005', digest, conn=self.conn))

    def test_transaction_id_unique(self):
        pay_service.create_order('RC006', 8, 8, 10, 1000, conn=self.conn)
        pay_service.create_order('RC007', 8, 8, 20, 2000, conn=self.conn)
        pay_service.mark_paid('RC006', 'TXSAME', conn=self.conn)
        with self.assertRaises(Exception):
            pay_service.mark_paid('RC007', 'TXSAME', conn=self.conn)

    def test_daily_points_counts_paid_and_credited(self):
        pay_service.create_order('RC008', 8, 8, 50, 5000, conn=self.conn)
        pay_service.create_order('RC009', 8, 8, 30, 3000, conn=self.conn)
        pay_service.mark_paid('RC008', 'TX8', conn=self.conn)
        pay_service.close_order('RC009', conn=self.conn)
        self.assertEqual(pay_service.daily_points(8, conn=self.conn), 50.0)

    def test_daily_points_uses_local_day_boundary(self):
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

    def test_expire_created(self):
        pay_service.create_order('RC012', 8, 8, 10, 1000, conn=self.conn)
        self.conn.execute("UPDATE pay_orders SET created_at = datetime('now', '-30 minutes') "
                          "WHERE out_trade_no = 'RC012'")
        self.conn.commit()
        pay_service.expire_created(conn=self.conn)
        self.assertEqual(pay_service.get_order('RC012', conn=self.conn)['status'], 'closed')

    def test_pending_credit_lists_paid_only(self):
        pay_service.create_order('RC013', 8, 8, 10, 1000, conn=self.conn)
        pay_service.mark_paid('RC013', 'TX13', conn=self.conn)
        pay_service.create_order('RC014', 8, 8, 10, 1000, conn=self.conn)
        rows = pay_service.pending_credit(conn=self.conn)
        self.assertEqual([r['out_trade_no'] for r in rows], ['RC013'])

    def test_log_event(self):
        pay_service.log_event('RC015', 'notify_ok', 'credit=credited', conn=self.conn)
        rows = self.conn.execute("SELECT event FROM pay_events WHERE out_trade_no='RC015'").fetchall()
        self.assertEqual(rows[0][0], 'notify_ok')


if __name__ == '__main__':
    unittest.main()
