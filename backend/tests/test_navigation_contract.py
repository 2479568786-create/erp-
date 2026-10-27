import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND = os.path.join(ROOT, 'frontend')


class NavigationContractTests(unittest.TestCase):
    def test_seerfar_selection_is_an_independent_workspace(self):
        with open(os.path.join(FRONTEND, 'index.html'), encoding='utf-8') as file:
            index = file.read()
        with open(os.path.join(FRONTEND, 'js', 'pages', 'seerfar-selection.js'), encoding='utf-8') as file:
            page = file.read()

        self.assertIn('href="#/seerfar-selection"', index)
        self.assertIn('js/pages/seerfar-selection.js', index)
        self.assertIn("Router.register('/seerfar-selection'", page)

    def test_promotion_menu_routes_are_registered(self):
        with open(os.path.join(FRONTEND, 'index.html'), encoding='utf-8') as file:
            index = file.read()
        with open(os.path.join(FRONTEND, 'js', 'pages', 'promotion.js'), encoding='utf-8') as file:
            promo_page = file.read()

        self.assertIn('href="#/ad-monitor"', index)
        self.assertIn('href="#/promo-remove"', index)
        self.assertIn("Router.register('/ad-monitor'", promo_page)
        self.assertIn("Router.register('/promo-remove'", promo_page)
