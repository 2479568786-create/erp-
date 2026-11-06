# -*- coding: utf-8 -*-
"""A5：支付宝当面付预下单（precreate）服务层 + POST /api/pay/orders 渠道分发。

服务层用注入的 http_post 模拟网关（本地测试密钥签响应），路由层用临时库 + patch 渠道配置。
"""
import base64
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from flask import Flask

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db  # noqa: E402
from routes import pay_routes  # noqa: E402
from services import alipay_service, pay_service, wxpay_service  # noqa: E402
from tests.test_pay_alipay import TEST_PUBLIC_B64, _priv  # noqa: E402

APP_ID = '2021000000000001'
PRECREATE_NODE = 'alipay_trade_precreate_response'


def signed_node(node, node_key=PRECREATE_NODE):
    payload = {node_key: node}
    node_text = json.dumps(node, ensure_ascii=False)
    sig = _priv().sign(node_text.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256())
    payload['sign'] = base64.b64encode(sig).decode()
    return json.dumps(payload, ensure_ascii=False)


class PrecreateServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='alipay_precreate_')
        self.pub_path = os.path.join(self.tmpdir, 'alipay_public_key.pem')
        with open(self.pub_path, 'wb') as f:
            f.write(base64.b64decode(TEST_PUBLIC_B64))
        self.env = patch.dict(os.environ, {
            'ALIPAY_ENABLED': '1',
            'ALIPAY_APPID': APP_ID,
            'ALIPAY_PUBLIC_KEY_PATH': self.pub_path,
            'ALIPAY_PRIVATE_KEY_PATH': os.path.join(self.tmpdir, 'missing_private.pem'),
            'ALIPAY_NOTIFY_URL': 'https://example.test/api/pay/alipay/notify',
        }, clear=False)
        self.env.start()
        alipay_service._public_key_cache.clear()
        alipay_service._private_key_cache.clear()

    def tearDown(self):
        self.env.stop()

    def _call(self, amount_cents=1000, subject='Geekfast ERP 充值 10 点', **over):
        captured = {}
        node_over = dict(over.pop('node_over', {}))

        def fake_post(params, cfg):
            captured.update(params)
            node = {'code': '10000', 'msg': 'Success',
                    'out_trade_no': 'RC1', 'qr_code': 'https://qr.alipay.com/abc123'}
            node.update(node_over)
            return signed_node(node)

        result = alipay_service.precreate(
            'RC1', amount_cents, subject=subject,
            private_key=_priv(), public_key=_priv().public_key(),
            http_post=fake_post, **over)
        return result, captured

    def test_precreate_returns_qr_code(self):
        result, _ = self._call()
        self.assertEqual(result['qr_code'], 'https://qr.alipay.com/abc123')

    def test_request_params_shape(self):
        _, captured = self._call(amount_cents=1250)
        self.assertEqual(captured['method'], 'alipay.trade.precreate')
        self.assertEqual(captured['sign_type'], 'RSA2')
        self.assertEqual(captured['notify_url'], 'https://example.test/api/pay/alipay/notify')
        biz = json.loads(captured['biz_content'])
        self.assertEqual(biz['out_trade_no'], 'RC1')
        self.assertEqual(biz['total_amount'], '12.50')      # 分 → 元，两位小数
        self.assertEqual(biz['timeout_express'], '15m')
        self.assertTrue(biz['subject'])
        self.assertTrue(captured.get('sign'))

    def test_amount_conversion_from_cents(self):
        for cents, expected in ((1, '0.01'), (1000, '10.00'), (1250, '12.50'), (20000, '200.00')):
            _, captured = self._call(amount_cents=cents)
            self.assertEqual(json.loads(captured['biz_content'])['total_amount'], expected)

    def test_subject_truncated_to_256(self):
        _, captured = self._call(subject='充值' * 500)
        self.assertEqual(len(json.loads(captured['biz_content'])['subject']), 256)

    def test_biz_error_raises_with_codes(self):
        with self.assertRaises(alipay_service.AlipayBizError) as ctx:
            self._call(node_over={'code': '40004', 'sub_code': 'ACQ.INVALID_PARAMETER',
                                  'msg': 'Business Failed'})
        self.assertEqual(ctx.exception.code, '40004')
        self.assertEqual(ctx.exception.sub_code, 'ACQ.INVALID_PARAMETER')

    def test_missing_qr_code_raises(self):
        with self.assertRaises(alipay_service.AlipayError):
            self._call(node_over={'qr_code': ''})

    def test_bad_response_signature_raises(self):
        def fake_post(params, cfg):
            raw = signed_node({'code': '10000', 'qr_code': 'https://qr.alipay.com/x'})
            return raw.replace('https://qr.alipay.com/x', 'https://qr.alipay.com/y')

        with self.assertRaises(Exception):
            alipay_service.precreate('RC1', 1000, private_key=_priv(),
                                     public_key=_priv().public_key(), http_post=fake_post)

    def test_missing_private_key_is_config_error(self):
        def fake_post(params, cfg):
            return signed_node({'code': '10000', 'qr_code': 'x'})

        with self.assertRaises(alipay_service.AlipayConfigError):
            alipay_service.precreate('RC1', 1000, http_post=fake_post)   # 不传私钥，走路径（不存在）

    def test_transport_error_type(self):
        def boom(params, cfg):
            raise alipay_service.AlipayTransportError('连接超时')

        with self.assertRaises(alipay_service.AlipayTransportError):
            alipay_service.precreate('RC1', 1000, private_key=_priv(), http_post=boom)

    def test_no_notify_url_for_query_but_yes_for_precreate(self):
        # precreate 需要 notify_url；query 不需要（避免给查单带回调地址）
        captured = {}
        alipay_service.query_trade(
            'RC1', private_key=_priv(), public_key=_priv().public_key(),
            http_post=lambda p, c: (captured.update(p) or signed_node(
                {'code': '10000', 'trade_status': 'WAIT_BUYER_PAY'},
                node_key='alipay_trade_query_response')))
        self.assertNotIn('notify_url', captured)


class CreateOrderRouteTests(unittest.TestCase):
    """POST /api/pay/orders 的渠道分发（临时库，不碰真实数据）。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='pay_route_')
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

        self._ali_cfg = {'enabled': True, 'app_id': APP_ID,
                         'notify_url': 'https://example.test/api/pay/alipay/notify',
                         'refund_notify_url': '', 'sandbox': False,
                         'private_key_path': '/x/app.pem', 'public_key_path': '/x/pub.pem',
                         'gateway': alipay_service.PROD_GATEWAY, 'seller_id': ''}
        self._wx_cfg = {'enabled': False, 'notify_url': '', 'callback_ip_mode': 'log',
                        'callback_ips': []}

    def tearDown(self):
        pay_service._table_ready = self._prev_ready
        self._db_patch.stop()

    def _post(self, body, user=None, wx_cfg=None, wx_missing=None):
        user = user or {'id': 8, 'role': 'salesperson'}
        wx_cfg = wx_cfg if wx_cfg is not None else self._wx_cfg
        wx_missing = wx_missing if wx_missing is not None else ['WECHATPAY_APPID']
        with patch.object(pay_routes, 'get_current_user', return_value=user), \
             patch.object(alipay_service, 'config', return_value=self._ali_cfg), \
             patch.object(alipay_service, 'missing_config', return_value=[]), \
             patch.object(wxpay_service, 'config', return_value=wx_cfg), \
             patch.object(wxpay_service, 'missing_config', return_value=wx_missing):
            return self.client.post('/api/pay/orders', json=body,
                                    headers={'Authorization': 'Bearer x'})

    def _order(self, out_trade_no):
        return db.query('SELECT * FROM pay_orders WHERE out_trade_no = ?',
                        (out_trade_no,), one=True)

    def test_alipay_order_created_with_qr(self):
        with patch.object(alipay_service, 'precreate',
                          return_value={'qr_code': 'https://qr.alipay.com/abc'}):
            res = self._post({'points': 10, 'channel': 'alipay'})
        self.assertEqual(res.status_code, 200, res.get_json())
        data = res.get_json()['data']
        self.assertEqual(data['channel'], 'alipay')
        self.assertEqual(data['code_url'], 'https://qr.alipay.com/abc')
        order = self._order(data['out_trade_no'])
        self.assertEqual(order['channel'], 'alipay')
        self.assertEqual(order['status'], 'created')
        self.assertEqual(order['code_url'], 'https://qr.alipay.com/abc')
        self.assertEqual(order['amount_cents'], 1000)
        events = [r['event'] for r in db.query(
            "SELECT event FROM pay_events WHERE out_trade_no = ?", (data['out_trade_no'],))]
        self.assertIn('create', events)

    def test_alipay_biz_error_closes_order(self):
        with patch.object(alipay_service, 'precreate',
                          side_effect=alipay_service.AlipayBizError('40004', 'ACQ.X', 'bad')):
            res = self._post({'points': 10, 'channel': 'alipay'})
        self.assertEqual(res.status_code, 502)
        rows = db.query("SELECT out_trade_no FROM pay_orders ORDER BY id DESC LIMIT 1")
        order = self._order(rows[0]['out_trade_no'])
        self.assertEqual(order['status'], 'closed')
        events = [r['event'] for r in db.query(
            "SELECT event FROM pay_events WHERE out_trade_no = ?", (order['out_trade_no'],))]
        self.assertIn('create_fail', events)

    def test_alipay_config_error_returns_503(self):
        with patch.object(alipay_service, 'precreate',
                          side_effect=alipay_service.AlipayConfigError('missing key')):
            res = self._post({'points': 10, 'channel': 'alipay'})
        self.assertEqual(res.status_code, 503)

    def test_alipay_missing_qr_returns_502(self):
        with patch.object(alipay_service, 'precreate', return_value={}):
            res = self._post({'points': 10, 'channel': 'alipay'})
        self.assertEqual(res.status_code, 502)

    def test_alipay_disabled_returns_503(self):
        cfg = dict(self._ali_cfg, enabled=False)
        with patch.object(alipay_service, 'config', return_value=cfg):
            with patch.object(pay_routes, '_channel_state',
                              return_value={'label': '支付宝扫码', 'enabled': False,
                                            'ready': False, 'missing': ['ALIPAY_APPID'],
                                            'notify_url': '', 'sandbox': False}):
                res = self._post({'points': 10, 'channel': 'alipay'})
        self.assertEqual(res.status_code, 503)

    def test_unknown_channel_rejected(self):
        res = self._post({'points': 10, 'channel': 'unionpay'})
        self.assertEqual(res.status_code, 400)
        self.assertIn('不支持的支付渠道', res.get_json()['msg'])

    def test_wxpay_path_still_works(self):
        fake_resp = {'code_url': 'weixin://wxpay/abc', 'prepay_id': 'PREPAY-1'}
        with patch.object(wxpay_service, 'create_native_order', return_value=fake_resp):
            res = self._post({'points': 10, 'channel': 'wxpay'},
                             wx_cfg=dict(self._wx_cfg, enabled=True), wx_missing=[])
        self.assertEqual(res.status_code, 200, res.get_json())
        data = res.get_json()['data']
        self.assertEqual(data['channel'], 'wxpay')
        order = self._order(data['out_trade_no'])
        self.assertEqual(order['channel'], 'wxpay')
        self.assertEqual(order['prepay_id'], 'PREPAY-1')

    def test_channel_defaults_to_primary(self):
        with patch.object(alipay_service, 'precreate',
                          return_value={'qr_code': 'https://qr.alipay.com/def'}), \
             patch.dict(os.environ, {'PAY_PRIMARY_CHANNEL': 'alipay'}):
            res = self._post({'points': 10})     # 不传 channel
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json()['data']['channel'], 'alipay')


if __name__ == '__main__':
    unittest.main()
