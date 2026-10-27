import json
import os
import sqlite3
import sys
import tempfile
import unittest


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db


class SeerfarResearchImportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.temp_dir.name, 'seerfar-import.db')
        db.init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_seerfar_import_is_idempotent_and_preserves_raw_payload(self):
        from models.research import ResearchItem

        payload = {
            'source_type': 'seerfar',
            'source_id': '175924376',
            'platform': 'ozon',
            'product_name': '测试商品',
            'source_payload_json': {'sku': 175924376},
        }

        first, first_created = ResearchItem.create_from_seerfar(payload)
        second, second_created = ResearchItem.create_from_seerfar(payload)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(
            175924376,
            json.loads(second['source_payload_json'])['sku'],
        )
        self.assertTrue(second['source_synced_at'])

    def test_concurrent_import_recovers_existing_unique_record(self):
        from models.research import ResearchItem
        from unittest.mock import patch

        existing = {
            'id': 42,
            'source_type': 'seerfar',
            'source_id': 'race-1',
            'platform': 'ozon',
        }
        with patch.object(ResearchItem, 'find_by_source', side_effect=[None, existing]), \
                patch.object(ResearchItem, 'create', side_effect=sqlite3.IntegrityError()):
            row, created = ResearchItem.create_from_seerfar({
                'source_id': 'race-1',
                'product_name': '并发商品',
            })

        self.assertFalse(created)
        self.assertEqual(42, row['id'])

    def test_create_keeps_existing_json_string_when_copying_record(self):
        from models.research import ResearchItem

        item_id = ResearchItem.create({
            'product_name': '复制商品',
            'source_payload_json': '{"sku": 175924376}',
        })

        row = ResearchItem.find_by_id(item_id)
        self.assertEqual('{"sku": 175924376}', row['source_payload_json'])

    def test_existing_research_schema_migrates_before_source_index_creation(self):
        self.temp_dir.cleanup()
        self.temp_dir = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.temp_dir.name, 'legacy.db')
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("""CREATE TABLE product_research_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL DEFAULT 'reference_product',
            product_name TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            benchmark_store_id TEXT NOT NULL DEFAULT '',
            suggested_price REAL,
            monthly_revenue REAL,
            revenue_growth_rate REAL,
            top3_brand_share REAL,
            seller_count INTEGER,
            brand_count INTEGER,
            blue_ocean_score REAL,
            mpstats_source TEXT NOT NULL DEFAULT 'manual',
            owner_name TEXT NOT NULL DEFAULT '未分配',
            notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '待调研',
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.commit()
        conn.close()

        db.init_db()
        check_conn = sqlite3.connect(db.DB_PATH)
        try:
            columns = {row[1] for row in check_conn.execute(
                'PRAGMA table_info(product_research_items)'
            )}
        finally:
            check_conn.close()
        self.assertIn('source_type', columns)


if __name__ == '__main__':
    unittest.main()
