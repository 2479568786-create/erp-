import os
import unittest


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ProductCardsWorkspaceRemovedTests(unittest.TestCase):
    def test_standalone_workspace_is_not_loaded_or_linked(self):
        with open(os.path.join(ROOT_DIR, 'frontend', 'index.html'), encoding='utf-8') as file:
            index_html = file.read()

        self.assertNotIn('href="#/product-cards"', index_html)
        self.assertNotIn('js/pages/product-cards.js', index_html)
        self.assertFalse(os.path.exists(os.path.join(ROOT_DIR, 'frontend', 'js', 'pages', 'product-cards.js')))

    def test_embedded_product_editor_card_api_remains_available(self):
        with open(os.path.join(ROOT_DIR, 'frontend', 'js', 'pages', 'collect.js'), encoding='utf-8') as file:
            collect_page = file.read()

        self.assertIn("/api/product-cards/generate", collect_page)
        self.assertIn('openProductCardAI()', collect_page)


if __name__ == '__main__':
    unittest.main()
