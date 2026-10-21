import os
import unittest


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAGES_DIR = os.path.join(ROOT_DIR, 'frontend', 'js', 'pages')


class OnlineRelistFrontendContractTests(unittest.TestCase):
    def test_online_product_has_direct_relist_action_and_new_identity(self):
        with open(os.path.join(PAGES_DIR, 'online-products.js'), encoding='utf-8') as file:
            online_page = file.read()

        self.assertIn('startRelistOnlineProduct', online_page)
        self.assertIn('Api.getOnlineProductEditData(id)', online_page)
        self.assertIn('chooseRelistTargetStore', online_page)
        self.assertIn("productId: ''", online_page)
        self.assertIn('delete copy.skuId', online_page)
        self.assertIn('delete copy.sku', online_page)
        self.assertIn('targetStore.id', online_page)
        self.assertIn('targetStore.name', online_page)

    def test_shared_publish_store_selector_remains_unchanged(self):
        with open(os.path.join(PAGES_DIR, 'collect.js'), encoding='utf-8') as file:
            collect_page = file.read()

        self.assertIn('function showStoreSelectModal() {', collect_page)
        self.assertNotIn('preferredStoreId', collect_page)
        self.assertNotIn('showStoreSelectModal(product.storeId)', collect_page)


if __name__ == '__main__':
    unittest.main()
