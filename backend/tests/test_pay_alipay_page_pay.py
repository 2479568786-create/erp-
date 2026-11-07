# -*- coding: utf-8 -*-
"""A6 电脑网站支付（alipay.trade.page.pay）单测：参数、金额精度、超时、return_url、表单与签名。"""
import base64
import os
import sys
import unittest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import alipay_service  # noqa: E402


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


class PagePayTests(unittest.TestCase):
    def setUp(self):
        self.private, self.public = _keypair()
        self.cfg = {
            'app_id': '2021000000000001',
            'private_key_path': '/nonexistent/app_private_key.pem',
            'public_key_path': '/nonexistent/alipay_public_key.pem',
            'notify_url': 'https://example.test/api/pay/alipay/notify',
            'return_url': 'https://example.test/?alipay=return',
            'gateway': alipay_service.PROD_GATEWAY,
            'seller_id': '',
        }

    def _params(self, cents=1000, **kwargs):
        return alipay_service.build_page_pay_params(
            'RC20261107000001', 'Geekfast ERP 图片点数充值 10 点', cents,
            cfg=self.cfg, private_key=self.private, timestamp='2026-11-07 22:00:00', **kwargs)

    # ---------------- 参数 ----------------
    def test_core_params(self):
        p = self._params()
        self.assertEqual('alipay.trade.page.pay', p['method'])
        self.assertEqual('2021000000000001', p['app_id'])
        self.assertEqual('RSA2', p['sign_type'])
        self.assertEqual('utf-8', p['charset'])
        self.assertEqual('JSON', p['format'])
        self.assertEqual('1.0', p['version'])
        self.assertEqual('2026-11-07 22:00:00', p['timestamp'])
        self.assertEqual('https://example.test/api/pay/alipay/notify', p['notify_url'])
        self.assertEqual('https://example.test/?alipay=return', p['return_url'])
        self.assertIn('sign', p)

    def test_biz_content_fields(self):
        import json
        biz = json.loads(self._params()['biz_content'])
        self.assertEqual('RC20261107000001', biz['out_trade_no'])
        self.assertEqual('FAST_INSTANT_TRADE_PAY', biz['product_code'])
        self.assertEqual('15m', biz['timeout_express'])
        self.assertEqual('10.00', biz['total_amount'])
        self.assertIn('图片点数充值', biz['subject'])

    def test_amount_precision(self):
        for cents, expected in ((1000, '10.00'), (1, '0.01'), (1299, '12.99'), (20000, '200.00')):
            import json
            biz = json.loads(self._params(cents=cents)['biz_content'])
            self.assertEqual(expected, biz['total_amount'])

    def test_timeout_and_return_override(self):
        import json
        p = self._params(timeout_express='30m', return_url='https://example.test/x?alipay=return')
        self.assertEqual('30m', json.loads(p['biz_content'])['timeout_express'])
        self.assertEqual('https://example.test/x?alipay=return', p['return_url'])

    def test_default_return_url_when_cfg_empty(self):
        cfg = dict(self.cfg)
        cfg['return_url'] = ''
        p = alipay_service.build_page_pay_params(
            'RC1', 's', 1000, cfg=cfg, private_key=self.private, timestamp='2026-11-07 22:00:00')
        self.assertEqual(alipay_service.DEFAULT_RETURN_URL, p['return_url'])
        self.assertIn('?alipay=return', alipay_service.DEFAULT_RETURN_URL)

    # ---------------- 签名 ----------------
    def test_signature_verifies(self):
        p = dict(self._params())
        signature = p.pop('sign')
        text = alipay_service.sign_string(p)
        self.assertNotIn('sign_type=', text.split('&')[0] + '&')  # sign_type 不参与待签串
        self.public.verify(base64.b64decode(signature), text.encode('utf-8'),
                           padding.PKCS1v15(), hashes.SHA256())

    def test_tampered_amount_breaks_signature(self):
        import json
        p = dict(self._params())
        signature = p.pop('sign')
        biz = json.loads(p['biz_content'])
        biz['total_amount'] = '0.01'
        p['biz_content'] = json.dumps(biz, ensure_ascii=False, separators=(',', ':'))
        with self.assertRaises(Exception):
            self.public.verify(base64.b64decode(signature), alipay_service.sign_string(p).encode('utf-8'),
                               padding.PKCS1v15(), hashes.SHA256())

    # ---------------- 表单 ----------------
    def test_form_html(self):
        built = alipay_service.page_pay(
            'RC20261107000001', '充值 10 点', 1000, cfg=self.cfg, private_key=self.private,
            timestamp='2026-11-07 22:00:00')
        form = built['pay_form']
        self.assertIn('<form', form)
        self.assertIn('method="post"', form)
        self.assertIn(alipay_service.PROD_GATEWAY, form)
        for name in ('app_id', 'method', 'biz_content', 'sign', 'sign_type', 'return_url', 'notify_url'):
            self.assertIn('name="%s"' % name, form)
        # 表单字段值必须转义，避免 biz_content 里的引号把 HTML 弄坏
        self.assertNotIn('name="biz_content" value="{"', form)
        self.assertIn('&quot;', form)

    def test_page_pay_returns_params(self):
        built = alipay_service.page_pay('RC1', 's', 1000, cfg=self.cfg,
                                        private_key=self.private, timestamp='2026-11-07 22:00:00')
        self.assertEqual('alipay.trade.page.pay', built['params']['method'])
        self.assertIn('sign', built['params'])

    # ---------------- precreate 仍在（未启用） ----------------
    def test_precreate_still_available(self):
        self.assertTrue(hasattr(alipay_service, 'precreate'))


if __name__ == '__main__':
    unittest.main()
