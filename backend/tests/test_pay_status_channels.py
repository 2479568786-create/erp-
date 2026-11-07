# -*- coding: utf-8 -*-
"""A3：/api/pay/status 的渠道级状态（channels / primary_channel / active_channel / can_pay）。

思路：route 层用 Flask test_client + 注入 get_current_user，
渠道配置用 patch 替换 config()/missing_config()，不碰真实 env 与文件。
"""
import os
import sys
import unittest
from unittest.mock import patch

from flask import Flask

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from routes import pay_routes  # noqa: E402
from services import alipay_service, pay_service, wxpay_service  # noqa: E402

ADMIN = {'id': 1, 'role': 'admin'}
SALES = {'id': 8, 'role': 'salesperson'}


def _wx_cfg(enabled=True, notify_url='https://example.test/api/payment/wechat/callback'):
    return {'enabled': enabled, 'notify_url': notify_url,
            'callback_ip_mode': 'log', 'callback_ips': ['1.2.3.4']}


def _ali_cfg(enabled=True, sandbox=False, notify_url='https://example.test/api/pay/alipay/notify'):
    return {'enabled': enabled, 'app_id': '2021000000000001', 'notify_url': notify_url,
            'refund_notify_url': '', 'sandbox': sandbox,
            'private_key_path': '/nonexistent/app.pem', 'public_key_path': '/nonexistent/pub.pem',
            'gateway': alipay_service.SANDBOX_GATEWAY if sandbox else alipay_service.PROD_GATEWAY,
            'seller_id': ''}


class StatusBase(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(pay_routes.pay_bp, url_prefix='/api')
        app.config['TESTING'] = True
        self.client = app.test_client()

    def _status(self, user=ADMIN, wx=None, ali=None, primary=None, wx_missing=None, ali_missing=None):
        wx = wx or _wx_cfg(enabled=False)
        ali = ali or _ali_cfg(enabled=False)
        env = {}
        if primary is not None:
            env['PAY_PRIMARY_CHANNEL'] = primary
        with patch.object(pay_routes, 'get_current_user', return_value=user), \
             patch.object(wxpay_service, 'config', return_value=wx), \
             patch.object(wxpay_service, 'missing_config',
                          return_value=(wx_missing if wx_missing is not None else ['WECHATPAY_APPID'])), \
             patch.object(alipay_service, 'config', return_value=ali), \
             patch.object(alipay_service, 'missing_config',
                          return_value=(ali_missing if ali_missing is not None else ['ALIPAY_APPID'])), \
             patch.dict(os.environ, env, clear=False):
            if primary is None:
                os.environ.pop('PAY_PRIMARY_CHANNEL', None)
            resp = self.client.get('/api/pay/status',
                                   headers={'Authorization': 'Bearer x'})
        self.assertEqual(resp.status_code, 200, resp.data[:200])
        return resp.get_json()['data']


class ChannelStateTests(StatusBase):
    def test_both_disabled_cannot_pay(self):
        d = self._status()
        self.assertEqual(set(d['channels']), {'wxpay', 'alipay'})
        self.assertFalse(d['can_pay'])
        self.assertIsNone(d['active_channel'])
        self.assertEqual(d['ready_channels'], [])
        self.assertEqual(d['primary_channel'], 'alipay')     # 默认主通道
        for name in ('wxpay', 'alipay'):
            self.assertFalse(d['channels'][name]['ready'])
            self.assertEqual(d['channels'][name]['missing_count'], 1)
            self.assertIn('label', d['channels'][name])

    def test_alipay_ready_when_enabled_and_no_missing(self):
        d = self._status(ali=_ali_cfg(enabled=True, sandbox=True), ali_missing=[])
        self.assertTrue(d['can_pay'])
        self.assertEqual(d['active_channel'], 'alipay')
        self.assertEqual(d['ready_channels'], ['alipay'])
        self.assertEqual(d['channels']['alipay']['label'], '支付宝（电脑网站支付）')
        self.assertTrue(d['channels']['alipay']['sandbox'])
        self.assertTrue(d['channels']['alipay']['ready'])
        self.assertFalse(d['channels']['wxpay']['ready'])

    def test_enabled_but_missing_config_is_not_ready(self):
        d = self._status(ali=_ali_cfg(enabled=True), ali_missing=['ALIPAY_APPID', 'ALIPAY_PRIVATE_KEY_FILE'])
        self.assertFalse(d['channels']['alipay']['ready'])
        self.assertFalse(d['can_pay'])
        self.assertEqual(d['channels']['alipay']['missing_count'], 2)

    def test_primary_from_env(self):
        d = self._status(wx=_wx_cfg(enabled=True), wx_missing=[], primary='wxpay')
        self.assertEqual(d['primary_channel'], 'wxpay')
        self.assertEqual(d['active_channel'], 'wxpay')
        self.assertTrue(d['can_pay'])

    def test_unknown_primary_falls_back_to_default(self):
        d = self._status(primary='unionpay')
        self.assertEqual(d['primary_channel'], 'alipay')

    def test_falls_back_to_ready_channel_when_primary_not_ready(self):
        # 主通道支付宝没开，但微信配好了 → 仍然可以充
        d = self._status(wx=_wx_cfg(enabled=True), wx_missing=[],
                         ali=_ali_cfg(enabled=False), ali_missing=['ALIPAY_APPID'])
        self.assertEqual(d['primary_channel'], 'alipay')
        self.assertEqual(d['active_channel'], 'wxpay')
        self.assertTrue(d['can_pay'])
        self.assertEqual(d['ready_channels'], ['wxpay'])

    def test_primary_preferred_when_both_ready(self):
        d = self._status(wx=_wx_cfg(enabled=True), wx_missing=[],
                         ali=_ali_cfg(enabled=True), ali_missing=[], primary='alipay')
        self.assertEqual(d['active_channel'], 'alipay')
        self.assertEqual(d['ready_channels'], ['wxpay', 'alipay'])


class PayloadVisibilityTests(StatusBase):
    def test_admin_sees_missing_names_and_notify_url(self):
        d = self._status(ali=_ali_cfg(enabled=True), ali_missing=['ALIPAY_APPID'])
        self.assertIn('missing', d['channels']['alipay'])
        self.assertEqual(d['missing'], ['ALIPAY_APPID'])          # 兼容旧字段
        self.assertIn('notify_url', d)
        self.assertIn('daily_cap_points', d)
        self.assertIn('callback_ip_mode', d)

    def test_salesperson_does_not_see_details(self):
        d = self._status(user=SALES, ali=_ali_cfg(enabled=True), ali_missing=['ALIPAY_APPID'])
        for name in ('wxpay', 'alipay'):
            self.assertNotIn('missing', d['channels'][name])
            self.assertNotIn('notify_url', d['channels'][name])
        self.assertNotIn('missing', d)
        self.assertNotIn('notify_url', d)
        self.assertNotIn('daily_cap_points', d)
        self.assertTrue(d['channels']['alipay']['missing_count'] == 1)

    def test_legacy_fields_track_primary_channel(self):
        d = self._status(ali=_ali_cfg(enabled=True, sandbox=True), ali_missing=[])
        self.assertTrue(d['enabled'])
        self.assertTrue(d['ready'])
        self.assertEqual(d['channel'], 'alipay_qr')
        d2 = self._status(wx=_wx_cfg(enabled=True), wx_missing=[], primary='wxpay')
        self.assertEqual(d2['channel'], 'wechat_native')

    def test_tiers_present(self):
        d = self._status()
        self.assertEqual(d['tiers'], pay_service.tiers())
        self.assertIn('order_ttl_minutes', d)


if __name__ == '__main__':
    unittest.main()
