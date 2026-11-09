# -*- coding: utf-8 -*-
"""支付失败诊断 + closed 单对账时间窗（2026-11-09）。

覆盖 Chief 两条要求：
  ① 「最近 24h 有没有入账失败、几次、哪几单」能一句话回答；失败**分类计数**；
     探针自己打的假失败（无签名 bad_sig）单列、不计入失败。
  ② `stale_closed` 只查最近 48h：更早的 closed 单不再参与对账，不再被永久反复查远端。
"""
import os
import sqlite3
import sys
import time
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services import pay_diagnostics, pay_reconcile, pay_service  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        pay_service.ensure_tables(conn=self.conn)

    def tearDown(self):
        self.conn.close()

    def _seed(self, no, status='closed', amount_cents=1000, channel='alipay', age_hours=1):
        pay_service.create_order(no, 8, 8, 10, amount_cents, conn=self.conn, channel=channel)
        if status != 'created':
            self.conn.execute("UPDATE pay_orders SET status=? WHERE out_trade_no=?", (status, no))
        # 显式把 updated_at 拉到 N 小时前
        self.conn.execute(
            "UPDATE pay_orders SET updated_at = datetime('now', ?), created_at = datetime('now', ?) "
            "WHERE out_trade_no = ?", ('-%d hours' % age_hours, '-%d hours' % age_hours, no))
        self.conn.commit()
        return no

    def _event(self, no, event, detail='', hours_ago=0):
        self.conn.execute(
            "INSERT INTO pay_events (out_trade_no, event, detail, created_at) "
            "VALUES (?, ?, ?, datetime('now', ?))", (no, event, detail, '-%d hours' % hours_ago))
        self.conn.commit()


class FailureSummaryTests(_Base):
    def test_zero_failure_verdict_ok(self):
        s = pay_diagnostics.failure_summary(hours=24, conn=self.conn)
        self.assertEqual('ok', s['verdict'])
        self.assertEqual(0, s['total'])
        self.assertIn('无入账失败', pay_diagnostics.one_line(s))

    def test_classified_counts_and_orders(self):
        self._event('RC1', 'alipay_notify_bad_amount', 'paid=999 order=1000')
        self._event('RC1', 'alipay_notify_bad_amount_format', 'bad')
        self._event('RC2', 'alipay_notify_unknown_order', 'TRADE_SUCCESS')
        self._event('RC3', 'close_fail', 'remote timeout')
        s = pay_diagnostics.failure_summary(hours=24, conn=self.conn)
        self.assertEqual('has_failure', s['verdict'])
        self.assertEqual(4, s['total'])
        self.assertEqual({'amount_mismatch': 2, 'unknown_order': 1, 'close_fail': 1},
                         s['by_class'])
        orders = {o['out_trade_no']: o for o in s['orders']}
        self.assertEqual(2, orders['RC1']['count'])
        self.assertEqual(['amount_mismatch'], orders['RC1']['classes'])
        self.assertEqual(3, len(s['orders']))
        self.assertTrue(s['samples'])

    def test_probe_fake_failure_excluded_but_reported(self):
        self._event('-', 'alipay_notify_bad_sig', 'missing_sign')
        self._event('-', 'alipay_notify_bad_sig', 'missing_signature_headers')
        self._event('RC9', 'alipay_notify_bad_sig', 'RSA2 verify failed')  # 真失败
        s = pay_diagnostics.failure_summary(hours=24, conn=self.conn)
        self.assertEqual(1, s['total'], '探针不能被算成真实失败')
        self.assertEqual({'bad_sig': 1}, s['by_class'])
        self.assertEqual(2, s['probes']['count'])
        line = pay_diagnostics.one_line(s)
        self.assertIn('探针', line)
        self.assertIn('排除', line)

    def test_window_excludes_old_events(self):
        self._event('RC5', 'close_fail', 'old', hours_ago=30)
        s = pay_diagnostics.failure_summary(hours=24, conn=self.conn)
        self.assertEqual(0, s['total'])
        s2 = pay_diagnostics.failure_summary(hours=48, conn=self.conn)
        self.assertEqual(1, s2['total'])


class StaleClosedWindowTests(_Base):
    def test_recent_closed_included_old_excluded(self):
        recent = self._seed('RC_RECENT', age_hours=1)
        old = self._seed('RC_OLD', age_hours=72)
        rows = pay_service.stale_closed(conn=self.conn)
        nos = [r['out_trade_no'] for r in rows]
        self.assertIn(recent, nos)
        self.assertNotIn(old, nos, '超过 48h 的 closed 单不应再参与对账')

    def test_custom_window(self):
        no = self._seed('RC_60H', age_hours=60)
        self.assertEqual([], [r['out_trade_no'] for r in pay_service.stale_closed(conn=self.conn)])
        self.assertIn(no, [r['out_trade_no'] for r in
                           pay_service.stale_closed(conn=self.conn, hours=72)])

    def test_reconcile_does_not_query_old_closed(self):
        old = self._seed('RC_OLD2', age_hours=96)
        recent = self._seed('RC_NEW2', age_hours=2)
        queried = []

        def fake_query(no):
            queried.append(no)
            return {'code': '40004', 'sub_code': 'ACQ.TRADE_NOT_EXIST'}

        pay_reconcile.reconcile_closed_paid(conn=self.conn, query=fake_query, adjust=None)
        self.assertIn(recent, queried)
        self.assertNotIn(old, queried, '老 closed 单不应再查远端')


if __name__ == '__main__':
    unittest.main()
