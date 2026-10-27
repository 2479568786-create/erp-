# -*- coding: utf-8 -*-
"""回归测试：采集箱对所有账号可见（2026-10 员工反馈「只有管理员能看到采集箱」）。

背景：`GET /api/products` 对非管理员按「自己归属店铺」过滤，且未绑定店铺
（无 storeId）的采集商品对业务员一律不可见。采集箱里的商品绝大多数是
「待归属」采集品，于是业务员看到一个空箱子。

约定：采集箱是**共享区**——前端请求带 `scope=collect`，后端对该 scope
跳过店铺归属过滤；其它页面（在线商品等）仍保持原过滤不变。
"""
import os
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    path = os.path.join(ROOT_DIR, *parts)
    with open(path, encoding='utf-8') as file:
        return file.read()


class CollectScopeSharedTests(unittest.TestCase):
    def test_backend_reads_scope_param(self):
        source = _read('backend', 'routes', 'product_routes.py')
        self.assertIn("request.args.get('scope'", source)

    def test_backend_skips_store_filter_for_collect_scope(self):
        source = _read('backend', 'routes', 'product_routes.py')
        self.assertIn("scope != 'collect'", source)

    def test_store_filter_still_applies_without_scope(self):
        """默认请求（不带 scope）仍然按店铺过滤，避免把其它页面的隔离一起放开。"""
        source = _read('backend', 'routes', 'product_routes.py')
        self.assertIn("visible_pks = set(visible_store_pks(user) or [])", source)

    def test_collect_page_requests_shared_scope(self):
        source = _read('frontend', 'js', 'pages', 'collect.js')
        self.assertIn("scope: 'collect'", source)


if __name__ == '__main__':
    unittest.main()
