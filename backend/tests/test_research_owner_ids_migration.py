"""选品池 owner_ids 列：全新库也要有（历史手工列，2026-10-28 补迁移）。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db


class ResearchOwnerIdsMigrationTests(unittest.TestCase):
    def test_fresh_db_has_owner_ids_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'fresh.db')
            with patch.object(db, 'DB_PATH', db_path):
                db.init_db()
                columns = {row['name'] for row in db.query(
                    'PRAGMA table_info(product_research_items)')}
        self.assertIn('owner_ids', columns)

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'fresh.db')
            with patch.object(db, 'DB_PATH', db_path):
                db.init_db()
                db.init_db()
                columns = {row['name'] for row in db.query(
                    'PRAGMA table_info(product_research_items)')}
        self.assertIn('owner_ids', columns)


if __name__ == '__main__':
    unittest.main()
