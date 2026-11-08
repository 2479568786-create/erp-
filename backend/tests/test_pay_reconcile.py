# -*- coding: utf-8 -*-
"""A4：支付宝对账补偿（响应验签 + 查单 + 两类补偿）+ pay_refunds 退款占位表。

用本地一次性测试密钥模拟「支付宝平台」：响应节点由测试私钥签名，
服务端用对应公钥验签，完全不依赖真实支付宝/沙箱。
"""
import base64
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services import alipay_service, pay_reconcile, pay_service  # noqa: E402
from tests.test_pay_alipay import TEST_PUBLIC_B64, _priv  # noqa: E402

APP_ID = '2021000000000001'
QUERY_NODE = 'alipay_trade_query_response'


def signed_response(node, node_key=QUERY_NODE):
    """构造支付宝响应：签的必须是「嵌进外层后逐字节出现的那段 node 原文」。

    这里用同一个序列化器（相同 separators / ensure_ascii）生成两处，保证字节一致——
    这正是支付宝响应验签容易踩的坑：不能签一份、再 json.dumps 一份。
    """
    payload = {node_key: node}
    node_text = json.dumps(node, ensure_ascii=False)      # 与外层同一序列化器
    sig = _priv().sign(node_text.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256())
    payload['sign'] = base64.b64encode(sig).decode()
    return json.dumps(payload, ensure_ascii=False)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='alipay_recon_')
        self.pub_path = os.path.join(self.tmpdir, 'alipay_public_key.pem')
        with open(self.pub_path, 'wb') as f:
            f.write(base64.b64decode(TEST_PUBLIC_B64))
        self.env = patch.dict(os.environ, {
            'ALIPAY_ENABLED': '1',
            'ALIPAY_APPID': APP_ID,
            'ALIPAY_PUBLIC_KEY_PATH': self.pub_path,
            'ALIPAY_PRIVATE_KEY_PATH': os.path.join(self.tmpdir, 'app_private_key.pem'),
            'ALIPAY_NOTIFY_URL': 'https://example.test/api/pay/alipay/notify',
        }, clear=False)
        self.env.start()
        alipay_service._public_key_cache.clear()
        alipay_service._private_key_cache.clear()
        self.conn = sqlite3.connect(':memory:')
        pay_service.ensure_tables(conn=self.conn)
        self.credited = []

        def fake_adjust(account_id, points, mode, reason, operator):
            self.credited.append({'account_id': account_id, 'points': points,
                                  'operator': operator, 'reason': reason})
        self.adjust = fake_adjust

    def tearDown(self):
        self.env.stop()
        self.conn.close()
        alipay_service._public_key_cache.clear()
        alipay_service._private_key_cache.clear()

    def _seed(self, out_trade_no='RC20261106000000000001', amount_cents=1000,
              channel='alipay', points=10):
        pay_service.create_order(out_trade_no, 8, 8, points, amount_cents,
                                 conn=self.conn, channel=channel)
        return out_trade_no


class ResponseParseTests(Base):
    def test_signed_response_verifies_and_parses(self):
        node = {'code': '10000', 'msg': 'Success', 'trade_status': 'TRADE_SUCCESS',
                'total_amount': '10.00', 'trade_no': '2026110622001'}
        raw = signed_response(node)
        got = alipay_service.parse_response(raw, QUERY_NODE, public_key=_priv().public_key())
        self.assertEqual(got['trade_status'], 'TRADE_SUCCESS')
        self.assertEqual(got['code'], '10000')

    def test_tampered_node_fails_signature(self):
        raw = signed_response({'code': '10000', 'total_amount': '0.01', 'trade_no': 'T1'})
        raw = raw.replace('"0.01"', '"0.02"')
        with self.assertRaises(Exception):
            alipay_service.parse_response(raw, QUERY_NODE, public_key=_priv().public_key())

    def test_missing_sign_fails(self):
        raw = json.dumps({QUERY_NODE: {'code': '10000'}})
        with self.assertRaises(Exception):
            alipay_service.parse_response(raw, QUERY_NODE, public_key=_priv().public_key())

    def test_node_extraction_handles_nested_and_escaped(self):
        node = {'code': '10000', 'nested': {'a': [1, 2, {'b': '}'}]},
                'msg': '包含 } 和 "引号" 的值'}
        raw = signed_response(node)
        text = alipay_service.extract_response_node(raw, QUERY_NODE)
        self.assertEqual(json.loads(text), node)

    def test_query_trade_passes_method_and_biz_content(self):
        captured = {}

        def fake_post(params, cfg):
            captured.update(params)
            return signed_response({'code': '10000', 'trade_status': 'WAIT_BUYER_PAY',
                                    'total_amount': '10.00', 'trade_no': 'T1'})

        result = alipay_service.query_trade('RC1', private_key=_priv(),
                                            public_key=_priv().public_key(),
                                            http_post=fake_post)
        self.assertEqual(result['trade_status'], 'WAIT_BUYER_PAY')
        self.assertEqual(captured['method'], 'alipay.trade.query')
        self.assertEqual(json.loads(captured['biz_content']), {'out_trade_no': 'RC1'})
        self.assertEqual(captured['sign_type'], 'RSA2')
        self.assertTrue(captured.get('sign'))
        self.assertNotIn('notify_url', captured)     # 查单不需要回调地址


class PendingCreditTests(Base):
    def test_paid_order_gets_credited(self):
        no = self._seed()
        pay_service.mark_paid(no, 'T1', notify_id='n1', notify_digest='d1', conn=self.conn)
        summary = pay_reconcile.retry_pending_credits(conn=self.conn, adjust=self.adjust)
        self.assertEqual(summary['checked'], 1)
        self.assertEqual(summary['credited'], 1)
        self.assertEqual(pay_service.get_order(no, conn=self.conn)['status'], 'credited')
        self.assertEqual(len(self.credited), 1)

    def test_adjust_failure_is_counted_and_order_stays_paid(self):
        no = self._seed()
        pay_service.mark_paid(no, 'T1', notify_id='n1', notify_digest='d1', conn=self.conn)

        def boom(*a, **kw):
            raise RuntimeError('billing down')

        summary = pay_reconcile.retry_pending_credits(conn=self.conn, adjust=boom)
        self.assertEqual(summary['failed'], 1)
        self.assertEqual(pay_service.get_order(no, conn=self.conn)['status'], 'paid')


class StaleCreatedTests(Base):
    def _future(self):
        return time.time() + 3600     # 让 created 订单都算「超时」

    def test_paid_by_query_gets_marked_and_credited(self):
        no = self._seed()
        calls = []

        def fake_query(out_trade_no):
            calls.append(out_trade_no)
            return {'code': '10000', 'trade_status': 'TRADE_SUCCESS',
                    'total_amount': '10.00', 'trade_no': 'T-QUERY-1'}

        summary = pay_reconcile.reconcile_stale_created(
            conn=self.conn, query=fake_query, adjust=self.adjust, now_ts=self._future())
        self.assertEqual(calls, [no])
        self.assertEqual(summary['credited'], 1)
        order = pay_service.get_order(no, conn=self.conn)
        self.assertEqual(order['status'], 'credited')
        self.assertEqual(order['transaction_id'], 'T-QUERY-1')
        self.assertEqual(len(self.credited), 1)

    def test_trade_finished_also_reconciled(self):
        no = self._seed()
        summary = pay_reconcile.reconcile_stale_created(
            conn=self.conn,
            query=lambda n: {'code': '10000', 'trade_status': 'TRADE_FINISHED',
                             'total_amount': '10.00', 'trade_no': 'T2'},
            adjust=self.adjust, now_ts=self._future())
        self.assertEqual(summary['credited'], 1)
        self.assertEqual(pay_service.get_order(no, conn=self.conn)['status'], 'credited')

    def test_wait_buyer_pay_not_credited(self):
        no = self._seed()
        summary = pay_reconcile.reconcile_stale_created(
            conn=self.conn,
            query=lambda n: {'code': '10000', 'trade_status': 'WAIT_BUYER_PAY'},
            adjust=self.adjust, now_ts=self._future())
        self.assertEqual(summary['credited'], 0)
        self.assertEqual(summary['skipped'], 1)
        self.assertEqual(pay_service.get_order(no, conn=self.conn)['status'], 'created')
        self.assertEqual(self.credited, [])

    def test_amount_mismatch_not_credited(self):
        no = self._seed(amount_cents=1000)
        summary = pay_reconcile.reconcile_stale_created(
            conn=self.conn,
            query=lambda n: {'code': '10000', 'trade_status': 'TRADE_SUCCESS',
                             'total_amount': '0.01', 'trade_no': 'T3'},
            adjust=self.adjust, now_ts=self._future())
        self.assertEqual(summary['skipped'], 1)
        self.assertEqual(pay_service.get_order(no, conn=self.conn)['status'], 'created')
        self.assertEqual(self.credited, [])
        events = [r[0] for r in self.conn.execute(
            "SELECT event FROM pay_events WHERE out_trade_no = ?", (no,)).fetchall()]
        self.assertIn('recon_amount_mismatch', events)

    def test_query_exception_does_not_stop_batch(self):
        bad = self._seed('RC-BAD-0001')
        good = self._seed('RC-GOOD-0002')

        def fake_query(no):
            if no == bad:
                raise RuntimeError('network down')
            return {'code': '10000', 'trade_status': 'TRADE_SUCCESS',
                    'total_amount': '10.00', 'trade_no': 'T-GOOD'}

        summary = pay_reconcile.reconcile_stale_created(
            conn=self.conn, query=fake_query, adjust=self.adjust, now_ts=self._future())
        self.assertEqual(summary['checked'], 2)
        self.assertEqual(summary['failed'], 1)
        self.assertEqual(summary['credited'], 1)
        self.assertEqual(pay_service.get_order(good, conn=self.conn)['status'], 'credited')
        self.assertEqual(pay_service.get_order(bad, conn=self.conn)['status'], 'created')

    def test_biz_error_skipped(self):
        no = self._seed()
        summary = pay_reconcile.reconcile_stale_created(
            self.conn,
            query=lambda n: {'code': '40004', 'sub_code': 'ACQ.TRADE_NOT_EXIST',
                             'msg': 'Business Failed'},
            adjust=self.adjust, now_ts=self._future())
        self.assertEqual(summary['skipped'], 1)
        self.assertEqual(summary['credited'], 0)

    def test_fresh_orders_are_not_queried(self):
        self._seed()
        called = []
        summary = pay_reconcile.reconcile_stale_created(
            conn=self.conn, query=lambda n: called.append(n) or {},
            adjust=self.adjust, now_ts=time.time())
        self.assertEqual(called, [])
        self.assertEqual(summary['checked'], 0)

    def test_wxpay_orders_never_queried(self):
        self._seed(channel='wxpay')
        called = []
        pay_reconcile.reconcile_stale_created(
            conn=self.conn, query=lambda n: called.append(n) or {},
            adjust=self.adjust, now_ts=self._future())
        self.assertEqual(called, [])

    def test_full_reconcile_merges_summaries(self):
        no = self._seed()
        result = pay_reconcile.reconcile(
            conn=self.conn,
            query=lambda n: {'code': '10000', 'trade_status': 'TRADE_SUCCESS',
                             'total_amount': '10.00', 'trade_no': 'T9'},
            adjust=self.adjust, now_ts=self._future())
        self.assertEqual(result['credited'], 1)
        self.assertEqual(result['queried'], 1)
        self.assertEqual(pay_service.get_order(no, conn=self.conn)['status'], 'credited')


class RefundPlaceholderTests(Base):
    def _paid(self, no='RC-RF-0001', amount_cents=1000):
        self._seed(no, amount_cents=amount_cents)
        pay_service.mark_paid(no, 'TRADE-RF-1', notify_id='n', notify_digest='dg', conn=self.conn)
        return no

    def test_refund_table_created(self):
        cur = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pay_refunds'")
        self.assertIsNotNone(cur.fetchone())

    def test_create_refund_ok(self):
        no = self._paid()
        refund = pay_service.create_refund(no, 500, reason='多充了', conn=self.conn)
        self.assertEqual(refund['status'], 'created')
        self.assertEqual(refund['amount_cents'], 500)
        self.assertEqual(refund['channel'], 'alipay')
        self.assertTrue(refund['out_request_no'].startswith('RF'))
        self.assertEqual(len(pay_service.list_refunds(no, conn=self.conn)), 1)

    def test_duplicate_out_request_no_rejected(self):
        no = self._paid()
        pay_service.create_refund(no, 500, out_request_no='RF-FIXED-1', conn=self.conn)
        with self.assertRaises(Exception):
            pay_service.create_refund(no, 500, out_request_no='RF-FIXED-1', conn=self.conn)

    def test_amount_guards(self):
        no = self._paid(amount_cents=1000)
        with self.assertRaises(ValueError):
            pay_service.create_refund(no, 1001, conn=self.conn)   # 超过订单金额
        with self.assertRaises(ValueError):
            pay_service.create_refund(no, 0, conn=self.conn)      # 非正数

    def test_unpaid_order_cannot_refund(self):
        no = self._seed('RC-RF-UNPAID')
        with self.assertRaises(ValueError):
            pay_service.create_refund(no, 100, conn=self.conn)

    def test_mark_refund_status(self):
        no = self._paid()
        refund = pay_service.create_refund(no, 500, conn=self.conn)
        pay_service.mark_refund(refund['out_request_no'], 'succeeded',
                                refund_no='ALI-RF-1', trade_no='TRADE-RF-1', conn=self.conn)
        got = pay_service.get_refund(refund['out_request_no'], conn=self.conn)
        self.assertEqual(got['status'], 'succeeded')
        self.assertEqual(got['refund_no'], 'ALI-RF-1')
        with self.assertRaises(ValueError):
            pay_service.mark_refund(refund['out_request_no'], 'unknown', conn=self.conn)


if __name__ == '__main__':
    unittest.main()


class ClosedPaidCompensationTests(Base):
    """断线补偿：本地 closed 但远端已 TRADE_SUCCESS（2026-11-08 竞态形态）。"""

    def _close(self, no):
        pay_service.close_order(no, conn=self.conn)

    def test_closed_but_remote_paid_converges_and_credits(self):
        no = self._seed()
        self._close(no)
        q = lambda _no: {'code': '10000', 'trade_status': 'TRADE_SUCCESS',
                         'total_amount': '10.00', 'trade_no': 'TCLOSED1'}
        s = pay_reconcile.reconcile_closed_paid(conn=self.conn, query=q, adjust=self.adjust)
        self.assertEqual(1, s['credited'], s)
        self.assertEqual('credited', pay_service.get_order(no, conn=self.conn)['status'])
        self.assertEqual(1, len(self.credited))
        self.assertEqual('alipay:%s' % no, self.credited[0]['operator'])

    def test_closed_amount_mismatch_not_credited(self):
        no = self._seed()
        self._close(no)
        q = lambda _no: {'code': '10000', 'trade_status': 'TRADE_SUCCESS',
                         'total_amount': '9.99', 'trade_no': 'TCLOSED2'}
        s = pay_reconcile.reconcile_closed_paid(conn=self.conn, query=q, adjust=self.adjust)
        self.assertEqual(0, s['credited'], s)
        self.assertEqual([], self.credited)
        self.assertEqual('closed', pay_service.get_order(no, conn=self.conn)['status'])

    def test_closed_not_paid_stays_closed(self):
        no = self._seed()
        self._close(no)
        q = lambda _no: {'code': '40004', 'sub_code': 'ACQ.TRADE_NOT_EXIST'}
        s = pay_reconcile.reconcile_closed_paid(conn=self.conn, query=q, adjust=self.adjust)
        self.assertEqual(0, s['credited'])
        self.assertEqual('closed', pay_service.get_order(no, conn=self.conn)['status'])

    def test_reconcile_aggregate_includes_closed_convergence(self):
        no = self._seed()
        self._close(no)
        q = lambda _no: {'code': '10000', 'trade_status': 'TRADE_SUCCESS',
                         'total_amount': '10.00', 'trade_no': 'TCLOSED3'}
        res = pay_reconcile.reconcile(conn=self.conn, query=q, adjust=self.adjust)
        self.assertGreaterEqual(res.get('closed_converged', 0), 1, res)
        self.assertGreaterEqual(res.get('credited', 0), 1, res)

    def test_cron_script_runs_ledger_audit(self):
        """对账脚本必须每轮跑 audit_credited_without_ledger 并可报警（契约测试）。"""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, 'scripts', 'reconcile_pay.py'), encoding='utf-8').read()
        self.assertIn('audit_credited_without_ledger', src)
        self.assertIn('[reconcile][ERROR] credited without ledger', src)
        self.assertIn('reconcile_closed_paid', src)
