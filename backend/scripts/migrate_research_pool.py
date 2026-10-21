"""
选品池存量数据一次性迁移脚本
老 ERP workbench-data.db → 新 ERP geekozon.db 的 product_research_items 表

用法（在 ozon-erp/backend 目录下）：
    python scripts/migrate_research_pool.py [--src <老库路径>] [--dry-run]

幂等：按 (product_name, source_url) 去重，已存在的条目跳过。
"""
import argparse
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(HERE)
sys.path.insert(0, BACKEND_DIR)

DEFAULT_SRC = os.path.join(
    BACKEND_DIR, '..', '..', 'ozon-listing-assistant', 'workbench-data.db')

COLUMNS = [
    'kind', 'product_name', 'source_url', 'benchmark_store_id',
    'suggested_price', 'monthly_revenue', 'revenue_growth_rate',
    'top3_brand_share', 'seller_count', 'brand_count', 'blue_ocean_score',
    'mpstats_source', 'owner_name', 'notes', 'status', 'created_by',
    'created_at', 'updated_at',
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--src', default=os.path.abspath(DEFAULT_SRC))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    from db import DB_PATH, init_db, query, execute

    if not os.path.exists(args.src):
        print(f'[错误] 老库不存在: {args.src}')
        return 1

    init_db()  # 确保新表已建
    src = sqlite3.connect(args.src)
    src.row_factory = sqlite3.Row

    rows = src.execute("SELECT * FROM product_research_items").fetchall()
    print(f'源库: {args.src}')
    print(f'待迁移条目: {len(rows)} 条 → 目标 {DB_PATH}')

    inserted = skipped = 0
    for row in rows:
        d = dict(row)
        existing = query(
            "SELECT id FROM product_research_items WHERE product_name = ? "
            "AND source_url = ?",
            (d.get('product_name') or '', d.get('source_url') or ''), one=True)
        if existing:
            skipped += 1
            continue
        if args.dry_run:
            print(f"  [dry-run] 将迁移: {d.get('product_name')}")
            inserted += 1
            continue
        cols = [c for c in COLUMNS if c in d]
        execute(
            f"INSERT INTO product_research_items ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            tuple(d[c] for c in cols))
        inserted += 1

    print(f'完成: 迁移 {inserted} 条, 跳过已存在 {skipped} 条')
    return 0


if __name__ == '__main__':
    sys.exit(main())
