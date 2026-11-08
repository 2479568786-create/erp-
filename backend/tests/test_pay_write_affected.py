# -*- coding: utf-8 -*-
"""`_write_affected` 修复的生产路径用例（2026-11-08 事故回归）。

事故：`db.execute()` 返回 `lastrowid`，UPDATE 恒为 0 → `claim_credit` 判成没抢到 →
`credit_order` 返回 lost_race 并跳过 adjust_balance → 订单 credited、点数丢失。
本文件**全程不传 conn**（走生产路径），覆盖 Chief 要求的四类：
  ① claim_credit 在 conn=None 下返回真实行数（True/False）
  ② INSERT 取 id 的调用点不受影响
  ③ 「credited 但无 ledger 流水」自检能命中、补记后清空
  ④ 对账 cron 的 credit_order 在 conn=None 下真的会调加点
"""
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db  # noqa: E402
from services import pay_service  # noqa: E402

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS image_ops_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    operator TEXT,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    amount_cents INTEGER NOT NULL DEFAULT 0
)
"""


class ProdPathBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = os.path.join(tempfile.gettempdir(), 'pay_wa_%s' % uuid.uuid4().hex)
        os.makedirs(self.tmpdir, exist_ok=True)
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        self._db_patch = patch.object(db, 'DB_PATH', self.db_path)
        self._db_patch.start()
        self._prev_ready = pay_service._table_ready
        pay_service._table_ready = False
        pay_service.ensure_tables()
        c = db.get_connection()
        try:
            c.executescript(LEDGER_DDL)
            c.commit()
        finally:
            c.close()

    def tearDown(self):
        pay_service._table_ready = self._prev_ready
        self._db_patch.stop()

    # 生产路径：不传 conn
    def _order(self, no='RC20261109000001', amount_cents=1000):
        pay_service.create_order(no, 8, 8, 10, amount_cents, channel='alipay')
        return no

    def _row(self, no):
        return pay_service._fetch(None, "SELECT * FROM pay_orders WHERE out_trade_no = ?", (no,))

    def _ledger(self, operator):
        return pay_service._fetch(None, "SELECT * FROM image_ops_log WHERE operator = ?", (operator,))


class ClaimCreditProdPathTests(ProdPathBase):
    def test_claim_credit_returns_true_then_false_without_conn(self):
        no = self._order()
        self.assertEqual(1, pay_service._write_affected(
            None, "UPDATE pay_orders SET status='paid' WHERE out_trade_no=? AND status='created'", (no,)))
        self.assertTrue(pay_service.claim_credit(no), '生产路径必须返回 True（修复前恒 False）')
        self.assertEqual('credited', self._row(no)['status'])
        self.assertFalse(pay_service.claim_credit(no), '第二次必须抢不到')


class InsertIdUnchangedTests(ProdPathBase):
    def test_create_order_log_event_create_refund_still_work(self):
        no = self._order()
        # create_order 后能读回（INSERT 语义未动）
        self.assertEqual('created', self._row(no)['status'])
        pay_service.log_event(no, 'unit_test', 'insert intact')
        ev = pay_service._fetch(None, "SELECT * FROM pay_events WHERE out_trade_no=? AND event='unit_test'", (no,))
        self.assertIsNotNone(ev, 'log_event 必须仍能插入')
        pay_service._write_affected(None, "UPDATE pay_orders SET status='paid' WHERE out_trade_no=?", (no,))
        refund = pay_service.create_refund(no, 1000, reason='unit', out_request_no='RUT%s' % no)
        self.assertIsNotNone(refund, 'create_refund 必须仍能插入并读回')


class LedgerAuditTests(ProdPathBase):
    def test_audit_detects_missing_ledger_then_clears(self):
        no = self._order()
        # 复刻事故形态：标 credited 但不写 ledger
        pay_service._write_affected(None, "UPDATE pay_orders SET status='credited', "
                                          "credited_at=datetime('now') WHERE out_trade_no=?", (no,))
        hits = pay_service.audit_credited_without_ledger()
        self.assertEqual(1, len(hits), '必须能捞到「credited 但无流水」')
        self.assertEqual(no, hits[0]['out_trade_no'])
        # 补一条 ledger 后应清空
        pay_service._write_affected(None, "INSERT INTO image_ops_log (account_id, action, operator, "
                                          "reason, amount_cents) VALUES (8,'balance_add',?,'补点',1000)",
                                    ('alipay:%s' % no,))
        self.assertEqual([], pay_service.audit_credited_without_ledger())


class ReconcileCronPathTests(ProdPathBase):
    def test_credit_order_calls_adjust_without_conn(self):
        """对账 cron 补入账走的也是 credit_order → 生产路径必须真的调加点。"""
        no = self._order()
        pay_service._write_affected(None, "UPDATE pay_orders SET status='paid' WHERE out_trade_no=?", (no,))
        calls = []

        def fake_adjust(account_id, points=None, mode=None, reason=None, operator=None):
            calls.append({'account_id': account_id, 'points': points, 'operator': operator,
                          'reason': reason})
            pay_service._write_affected(
                None, "INSERT INTO image_ops_log (account_id, action, operator, reason, amount_cents) "
                      "VALUES (?, 'balance_add', ?, ?, ?)",
                (account_id, operator, reason, 1000))
            return {'ok': True}

        from services import image_billing_service
        with patch.object(image_billing_service, 'adjust_balance', fake_adjust):
            result = pay_service.credit_order(no)
        self.assertIn(result.get('reason'), ('credited',), result)
        self.assertEqual(1, len(calls), '必须调用一次加点')
        self.assertEqual('alipay:%s' % no, calls[0]['operator'])
        self.assertIsNotNone(self._ledger('alipay:%s' % no))
        self.assertEqual([], pay_service.audit_credited_without_ledger())


if __name__ == '__main__':
    unittest.main()
