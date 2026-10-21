import os
import unittest


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class OnlineProductsStoreFilterContractTests(unittest.TestCase):
    def test_store_selector_uses_local_store_primary_key(self):
        path = os.path.join(ROOT_DIR, 'frontend', 'js', 'pages', 'online-products.js')
        with open(path, encoding='utf-8') as file:
            source = file.read()

        self.assertIn('const sid = s.id || s.store_id || s.storeId;', source)

    def test_sync_uses_selected_store_and_all_stores(self):
        path = os.path.join(ROOT_DIR, 'frontend', 'js', 'pages', 'online-products.js')
        with open(path, encoding='utf-8') as file:
            source = file.read()

        self.assertIn('storesToSync = [currentOnlineStoreId]', source)
        self.assertIn('await Api.syncOnlineProducts({ storeId })', source)
        self.assertIn('for (const storeId of storesToSync)', source)

    def test_status_counts_follow_selected_store(self):
        page_path = os.path.join(ROOT_DIR, 'frontend', 'js', 'pages', 'online-products.js')
        api_path = os.path.join(ROOT_DIR, 'frontend', 'js', 'api.js')
        route_path = os.path.join(ROOT_DIR, 'backend', 'routes', 'online_product_routes.py')
        with open(page_path, encoding='utf-8') as file:
            page_source = file.read()
        with open(api_path, encoding='utf-8') as file:
            api_source = file.read()
        with open(route_path, encoding='utf-8') as file:
            route_source = file.read()

        self.assertIn('Api.getOnlineProductStats(currentOnlineStoreId)', page_source)
        self.assertIn("request.args.get('storeId', '').strip()", route_source)
        self.assertIn('OnlineProduct.get_stats(store_ids=[store_id])', route_source)

    def test_image_proxy_failure_falls_back_to_original_image(self):
        path = os.path.join(ROOT_DIR, 'frontend', 'js', 'pages', 'online-products.js')
        with open(path, encoding='utf-8') as file:
            source = file.read()

        self.assertIn('data-original-image=', source)
        self.assertIn('this.dataset.originalImage', source)


if __name__ == '__main__':
    unittest.main()
