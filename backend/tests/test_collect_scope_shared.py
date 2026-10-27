# -*- coding: utf-8 -*-
"""回归测试：采集箱 —— 业务员能进，但只看「自己的」商品，且上架失败同样可见。

背景（2026-10 用户两次确认）：
1. 员工反映看不到采集箱，只有管理员能看；
2. 修复后又要求：业务员只能看到**自己上架的产品**，**失败的也要看得到**。

线上数据事实（2026-10-27 实测）：193 个商品的 storeId / assignee 全为空，
316 条发布记录的 publisher 也全为空；只有发布记录的 storeId（Ozon 店铺 ID）
可以经 stores.store_id → account_id 映射到业务员。因此归属判据是：
assignee 是自己 ∪ storeId 是自己店铺（主键或 Ozon ID）∪ 该商品有自己店铺的发布记录。
不看发布状态 → failed / published_with_error 也在可见范围内。
"""
import os
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    path = os.path.join(ROOT_DIR, *parts)
    with open(path, encoding='utf-8') as file:
        return file.read()


class CollectScopeOwnershipTests(unittest.TestCase):
    def test_backend_reads_scope_param(self):
        source = _read('backend', 'routes', 'product_routes.py')
        self.assertIn("request.args.get('scope'", source)

    def test_collect_scope_branch_exists(self):
        source = _read('backend', 'routes', 'product_routes.py')
        self.assertIn("if scope == 'collect':", source)

    def test_ownership_uses_store_ids_and_publish_records(self):
        source = _read('backend', 'routes', 'product_routes.py')
        self.assertIn('visible_ozon_store_ids', source)
        self.assertIn('PublishRecord.find_all()', source)
        self.assertIn('owned_product_ids', source)

    def test_owner_by_assignee_supported(self):
        source = _read('backend', 'routes', 'product_routes.py')
        self.assertIn("(p.get('assignee') or '') in me", source)

    def test_no_status_filter_on_collect_scope(self):
        """失败的上架记录也要可见：采集箱分支不得按 publishStatus 过滤。"""
        source = _read('backend', 'routes', 'product_routes.py')
        start = source.index("if scope == 'collect':")
        block = source[start:start + 2200]
        self.assertNotIn("publishStatus ==", block)

    def test_collect_page_requests_shared_scope(self):
        source = _read('frontend', 'js', 'pages', 'collect.js')
        self.assertIn("scope: 'collect'", source)


if __name__ == '__main__':
    unittest.main()
