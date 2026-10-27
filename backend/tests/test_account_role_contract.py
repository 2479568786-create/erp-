import os
import sys
import unittest


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from routes import auth_routes


class AccountRoleContractTests(unittest.TestCase):
    def test_only_salesperson_is_a_creatable_managed_role(self):
        self.assertEqual(('salesperson',), auth_routes._CREATABLE_ROLES)

    def test_only_salesperson_is_an_assignable_store_owner(self):
        self.assertEqual(('salesperson',), auth_routes._ASSIGNABLE_STORE_OWNER_ROLES)


if __name__ == '__main__':
    unittest.main()
