# -*- coding: utf-8 -*-
"""广告写操作（预算/出价）单元测试。

原则：**不连接真实 Ozon**，perf 的所有网络函数都被替换成假实现，
数据库的 query/execute 也被替换成内存记录，验证：
  · 金额单位换算（卢布 ↔ micros）
  · 护栏（预算/出价上下限、单次变动幅度、活动状态）
  · 覆盖语义：PUT products 必须提交「全量商品 + 改动」，不能只交目标商品
  · 审计：成功/拦截/失败都写 ad_ops_log
"""
import unittest
from unittest.mock import patch

from services import ad_ops_service as ops
from services import ozon_performance_api as perf


CAMPAIGN = {
    'id': '111', 'title': 'Campaign A', 'state': 'CAMPAIGN_STATE_RUNNING',
    'PaymentType': 'CPC', 'budget': '0', 'dailyBudget': str(500 * 1_000_000),
    'weeklyBudget': '0', 'fromDate': '2026-10-01', 'toDate': '2026-11-01',
}


class _Recorder:
    """记录 db 写入调用，供断言审计日志。"""

    def __init__(self):
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return len(self.executed)


class TestMoney(unittest.TestCase):
    def test_rub_to_micros(self):
        self.assertEqual(perf.rub_to_micros(1), '1000000')
        self.assertEqual(perf.rub_to_micros(12.5), '12500000')
        self.assertEqual(perf.rub_to_micros('0.01'), '10000')

    def test_micros_to_rub(self):
        self.assertEqual(perf.micros_to_rub('12500000'), 12.5)
        self.assertEqual(perf.micros_to_rub(0), 0.0)
        self.assertEqual(perf.micros_to_rub(None), 0.0)


class TestBudgetPreview(unittest.TestCase):
    def setUp(self):
        self.rec = _Recorder()
        self.patches = [
            patch.object(ops, 'execute', self.rec.execute),
            patch.object(ops, 'query', lambda *a, **k: {'id': 4, 'alias': 'unistore', 'store_id': '216800183'}),
            patch.object(ops.perf, 'find_campaign', lambda *a, **k: dict(CAMPAIGN)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_ok_change(self):
        result = ops.preview_budget(4, '111', daily_budget_rub=800)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['changes']['dailyBudgetMicros'], str(800 * 1_000_000))
        self.assertEqual(result['changes']['currentDailyBudgetRub'], 500.0)

    def test_below_minimum_blocked(self):
        result = ops.preview_budget(4, '111', daily_budget_rub=50)
        self.assertFalse(result['ok'])
        self.assertTrue(any('下限' in e for e in result['errors']))

    def test_ratio_over_limit_blocked(self):
        result = ops.preview_budget(4, '111', daily_budget_rub=1200)  # +140%
        self.assertFalse(result['ok'])
        self.assertTrue(any('变动' in e for e in result['errors']))

    def test_finished_state_blocked(self):
        finished = dict(CAMPAIGN, state='CAMPAIGN_STATE_FINISHED')
        with patch.object(ops.perf, 'find_campaign', lambda *a, **k: finished):
            result = ops.preview_budget(4, '111', daily_budget_rub=600)
        self.assertFalse(result['ok'])
        self.assertTrue(any('不允许修改' in e for e in result['errors']))


class TestBudgetApply(unittest.TestCase):
    def setUp(self):
        self.rec = _Recorder()
        self.patch_calls = []
        self.patches = [
            patch.object(ops, 'execute', self.rec.execute),
            patch.object(ops, 'query', lambda *a, **k: {'id': 4, 'alias': 'unistore', 'store_id': '216800183'}),
            patch.object(ops.perf, 'find_campaign', lambda *a, **k: dict(CAMPAIGN)),
            patch.object(ops.perf, 'patch_campaign',
                         lambda store, cid, body: self.patch_calls.append((store, cid, body)) or {'campaignId': cid}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_requires_confirm(self):
        with self.assertRaises(ops.AdOpsError):
            ops.apply_budget(4, '111', daily_budget_rub=700, confirm=False)
        self.assertEqual(self.patch_calls, [], '未确认时不得调用 Ozon')

    def test_applies_and_audits(self):
        result = ops.apply_budget(4, '111', daily_budget_rub=700, confirm=True,
                                  operator={'id': 1, 'name': '管理员'})
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.patch_calls), 1)
        body = self.patch_calls[0][2]
        self.assertEqual(body['dailyBudget'], str(700 * 1_000_000))
        self.assertTrue(any('ad_ops_log' in sql for sql, _ in self.rec.executed))
        params = [p for sql, p in self.rec.executed if 'ad_ops_log' in sql][0]
        self.assertEqual(params[0], 4)          # store_pk
        self.assertEqual(params[3], 'budget')   # action

    def test_blocked_is_audited_without_ozon_call(self):
        with self.assertRaises(ops.AdOpsError):
            ops.apply_budget(4, '111', daily_budget_rub=50, confirm=True,
                             operator={'id': 1, 'name': '管理员'})
        self.assertEqual(self.patch_calls, [])
        statuses = [p[6] for sql, p in self.rec.executed if 'ad_ops_log' in sql]
        self.assertIn('blocked', statuses)


class TestBids(unittest.TestCase):
    def setUp(self):
        self.rec = _Recorder()
        self.put_calls = []
        self.products = [
            {'sku': '1001', 'bid': str(10 * 1_000_000), 'title': 'A'},
            {'sku': '1002', 'bid': str(20 * 1_000_000), 'title': 'B'},
        ]
        self.patches = [
            patch.object(ops, 'execute', self.rec.execute),
            patch.object(ops, 'query', lambda *a, **k: {'id': 4, 'alias': 'unistore', 'store_id': '216800183'}),
            patch.object(ops.perf, 'find_campaign', lambda *a, **k: dict(CAMPAIGN)),
            patch.object(ops.perf, 'list_campaign_products_all', lambda *a, **k: list(self.products)),
            patch.object(ops.perf, 'get_min_sku_bid', lambda *a, **k: {'bids': [{'sku': '1001', 'bid': '5000000'}]}),
            patch.object(ops.perf, 'update_campaign_product_bids',
                         lambda store, cid, bids: self.put_calls.append(list(bids)) or {}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_preview_ratio_guard(self):
        result = ops.preview_bids(4, '111', [{'sku': '1001', 'bid_rub': 30}])  # +200%
        self.assertFalse(result['ok'])
        self.assertTrue(any('变动' in e for e in result['errors']))

    def test_preview_below_ozon_min(self):
        result = ops.preview_bids(4, '111', [{'sku': '1001', 'bid_rub': 3}])   # 最低 5 ₽
        self.assertFalse(result['ok'])
        self.assertTrue(any('最低出价' in e for e in result['errors']))

    def test_apply_keeps_untouched_products(self):
        """PUT 是覆盖语义：必须把未改动的商品也一起提交，否则会被踢出活动。"""
        ops.apply_bids(4, '111', [{'sku': '1001', 'bid_rub': 12}], confirm=True,
                       operator={'id': 1, 'name': '管理员'})
        self.assertEqual(len(self.put_calls), 1)
        submitted = {item['sku']: item['bid_rub'] for item in self.put_calls[0]}
        self.assertEqual(submitted['1001'], 12)
        self.assertEqual(submitted['1002'], 20, '未改动的商品必须原样保留')
        self.assertEqual(len(submitted), 2)

    def test_apply_requires_confirm(self):
        with self.assertRaises(ops.AdOpsError):
            ops.apply_bids(4, '111', [{'sku': '1001', 'bid_rub': 12}], confirm=False)
        self.assertEqual(self.put_calls, [])


if __name__ == '__main__':
    unittest.main()
