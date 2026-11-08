# -*- coding: utf-8 -*-
"""支付宝异步通知：白名单放行 + 分支口径 + 关单竞态收敛 + 请求体快照（2026-11-08）。

背景（生产事故）：用户真付了 10 元，`/api/pay/alipay/notify` 被全局登录守卫 401 丢掉；
钱在账上、点没有。本用例锁定修复后的行为契约。
"""
import base64
import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from flask import Flask

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db  # noqa: E402
from routes import pay_routes  # noqa: E402
from services import alipay_notify, alipay_service, pay_service  # noqa: E402
from tests.test_pay_alipay import TEST_PUBLIC_B64, _priv  # noqa: E402
from utils import auth as auth_utils  # noqa: E402

APP_ID = '2021007118626312'
NOTIFY_PATH = '/api/pay/alipay/notify'


class NotifyBase(unittest.TestCase):
    def setUp(self):
        # 显式唯一目录：不依赖 tempfile.mkdtemp（沙箱下会被改成共享目录池）
        self.tmpdir = os.path.join(tempfile.gettempdir(),
                                   'alipay_notify_public_%s' % uuid.uuid4().hex)
        os.makedirs(self.tmpdir, exist_ok=True)
        self.pub_path = os.path.join(self.tmpdir, 'alipay_public_key.pem')
        with open(self.pub_path, 'wb') as f:
            f.write(base64.b64decode(TEST_PUBLIC_B64))
        self._env = patch.dict(os.environ, {
            'ALIPAY_ENABLED': '0',                 # 全程 0：通知仍必须被处理
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
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

        app = Flask(__name__)
        app.before_request(auth_utils.require_api_auth)   # 真守卫：这条是事故根因
        app.register_blueprint(pay_routes.pay_bp, url_prefix='/api')
        app.config['TESTING'] = True
        self.client = app.test_client()

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass
        pay_service._table_ready = self._prev_ready
        self._db_patch.stop()
        self._env.stop()
        alipay_service._public_key_cache.clear()

    # --- helpers ---
    def _seed(self, out_trade_no='RC20261108000001', channel='alipay', status=None):
        pay_service.create_order(out_trade_no, 8, 8, 10, 1000,
                                 conn=self.conn, channel=channel)
        if status == 'closed':
            pay_service.close_order(out_trade_no, conn=self.conn)
        return out_trade_no

    def _params(self, out_trade_no, **over):
        params = {
            'app_id': APP_ID,
            'out_trade_no': out_trade_no,
            'trade_no': '2026110822001400000000000009',
            'total_amount': '10.00',
            'trade_status': 'TRADE_SUCCESS',
            'charset': 'utf-8',
            'notify_id': 'notify-0001',
            'notify_time': '2026-11-08 21:08:21',
            'notify_type': 'trade_status_sync',
            'seller_id': '2088000000000001',
            'buyer_logon_id': '16612345670',
            'buyer_open_id': '0610Xivbg-ChH-QgaD1t3_O9FxJbh81eUAE5gmdBM0Lyco4',
            'sign_type': 'RSA2',
        }
        params.update(over)
        params['sign'] = alipay_service.sign(params, private_key=_priv(), mode='notify')
        return params

    def _digest(self, params):
        raw = '&'.join('%s=%s' % (k, v) for k, v in sorted(params.items()))
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def _handle(self, params, adjust_recorder=None, verify=None):
        def _adjust(account_id, points=None, mode=None, reason=None, operator=None):
            if adjust_recorder is not None:
                adjust_recorder.append({'account_id': account_id, 'points': points,
                                        'operator': operator, 'reason': reason})
            return {'ok': True}

        return alipay_notify.handle_notify(params, self._digest(params), conn=self.conn,
                                           cfg=alipay_service.config(), verify=verify,
                                           adjust=_adjust)

    def _order(self, out_trade_no):
        return pay_service.get_order(out_trade_no, conn=self.conn)

    def _events(self, out_trade_no):
        return pay_service._fetch(
            self.conn, "SELECT event, detail FROM pay_events WHERE out_trade_no = ? ORDER BY id",
            (out_trade_no,), one=False) or []


class WhitelistTests(NotifyBase):
    def test_notify_path_is_public_but_still_verifies(self):
        """白名单只免登录：路径在公开表里，且 handler 仍走验签。"""
        self.assertIn(NOTIFY_PATH, auth_utils._PUBLIC_API_PATHS)

    def test_post_without_token_reaches_verification_not_login_401(self):
        """不带 token 的 POST 必须走到验签逻辑（回 fail），而不是「请先登录」。"""
        res = self.client.post(NOTIFY_PATH, data={'out_trade_no': 'RC1'})
        body = res.get_data(as_text=True)
        self.assertNotIn('请先登录', body, body[:200])
        self.assertEqual('fail', body.strip(), (res.status_code, body[:200]))

    def test_post_without_token_valid_signature_hits_business_logic(self):
        """有效签名 + 未知单号 → 业务层回 fail（证明守卫放行、验签通过）。"""
        res = self.client.post(NOTIFY_PATH, data=self._params('RC_NOT_EXIST'))
        self.assertEqual('fail', res.get_data(as_text=True).strip())


class BranchPolicyTests(NotifyBase):
    def test_created_paid_credits(self):
        no = self._seed()
        rec = []
        status, body, info = self._handle(self._params(no), rec)
        self.assertEqual((200, 'success'), (status, body))
        self.assertTrue(info['credited'])
        self.assertEqual('credited', self._order(no)['status'])
        self.assertEqual(1, len(rec))
        self.assertEqual('alipay:%s' % no, rec[0]['operator'])

    def test_closed_paid_converges_and_autocredits(self):
        """关单竞态：closed + 已付 + 金额符 → 收敛入账，且留显眼事件。"""
        no = self._seed(status='closed')
        rec = []
        status, body, info = self._handle(self._params(no), rec)
        self.assertEqual((200, 'success'), (status, body))
        self.assertTrue(info['credited'])
        self.assertEqual('credited', self._order(no)['status'])
        self.assertEqual(1, len(rec))
        events = [e['event'] for e in self._events(no)]
        self.assertIn('paid_on_closed_order_autocredited', events)

    def test_closed_paid_amount_mismatch_refuses(self):
        no = self._seed(status='closed')
        rec = []
        status, body, info = self._handle(self._params(no, total_amount='0.01'), rec)
        self.assertEqual('fail', body)
        self.assertFalse(info['credited'])
        self.assertEqual('closed', self._order(no)['status'])
        self.assertEqual([], rec)

    def test_credited_replay_is_noop_success(self):
        """第二、三次通知：no-op success，绝不二次加点。"""
        no = self._seed()
        rec = []
        self._handle(self._params(no), rec)
        status, body, info = self._handle(self._params(no, notify_id='notify-0002'), rec)
        self.assertEqual((200, 'success'), (status, body))
        self.assertTrue(info['credited'])
        self.assertEqual(1, len(rec), '重放不能再调一次 adjust')

    def test_unknown_order_asks_alipay_to_retry(self):
        status, body, _info = self._handle(self._params('RC_NOT_EXIST'))
        self.assertEqual('fail', body)

    def test_body_snapshot_keeps_sign_and_masks_buyer(self):
        no = self._seed()
        params = self._params(no)
        self._handle(params, [])
        snap = [e for e in self._events(no) if e['event'] == 'alipay_notify_body']
        self.assertEqual(1, len(snap))
        detail = snap[0]['detail']
        self.assertIn('sign=', detail)                    # 签名保留，供重放
        self.assertIn(params['sign'][:16], detail)
        self.assertNotIn('16612345670', detail)           # 买家号打码
        self.assertNotIn('0610Xivbg', detail)
        self.assertLessEqual(len(detail), 2000)


if __name__ == '__main__':
    unittest.main()
