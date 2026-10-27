"""图库套图文件夹表：确保全新库也能由代码创建（历史遗留表，2026-10-28 补齐）。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db


class GalleryFoldersMigrationTests(unittest.TestCase):
    @staticmethod
    def _table_names(db_path):
        with patch.object(db, 'DB_PATH', db_path):
            rows = db.query("SELECT name FROM sqlite_master WHERE type='table'")
        return {row['name'] for row in rows}

    def test_init_db_creates_gallery_folder_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'fresh.db')
            with patch.object(db, 'DB_PATH', db_path):
                db.init_db()
            tables = self._table_names(db_path)
        self.assertIn('gallery_folders', tables)
        self.assertIn('gallery_folder_assets', tables)

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'fresh.db')
            with patch.object(db, 'DB_PATH', db_path):
                db.init_db()
                db.init_db()
            tables = self._table_names(db_path)
        self.assertIn('gallery_folders', tables)
        self.assertIn('gallery_folder_assets', tables)


if __name__ == '__main__':
    unittest.main()
