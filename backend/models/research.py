"""
OZON ERP - 选品池数据模型
从老 ERP（workbench product_research_items）全量迁移，SQLite 存储
"""
import json
import sqlite3
from datetime import datetime
from db import query, execute


class ResearchItem:
    """选品池条目模型"""

    @staticmethod
    def create(data):
        """新增选品条目，自动计算蓝海评分

        Returns:
            dict: 新记录
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        row = {
            'kind': data.get('kind') or 'reference_product',
            'product_name': (data.get('product_name') or '').strip(),
            'source_url': data.get('source_url') or '',
            'benchmark_store_id': data.get('benchmark_store_id') or '',
            'suggested_price': data.get('suggested_price'),
            'owner_name': data.get('owner_name') or '未分配',
            'owner_ids': (
                data.get('owner_ids')
                if isinstance(data.get('owner_ids'), str)
                else json.dumps(data.get('owner_ids') or [], ensure_ascii=False)
            ),
            'notes': data.get('notes') or '',
            'created_by': data.get('created_by') or '',
            'source_type': data.get('source_type') or 'manual',
            'source_id': str(data.get('source_id') or ''),
            'platform': data.get('platform') or 'ozon',
            'source_payload_json': (
                data.get('source_payload_json')
                if isinstance(data.get('source_payload_json'), str)
                else json.dumps(data.get('source_payload_json') or {}, ensure_ascii=False)
            ),
            'source_synced_at': data.get('source_synced_at'),
            'created_at': now,
            'updated_at': now,
        }
        cols = list(row.keys())
        return execute(
            f"INSERT INTO product_research_items ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            tuple(row.values())
        )

    @staticmethod
    def find_by_source(source_type, source_id, platform='ozon'):
        return query(
            "SELECT * FROM product_research_items "
            "WHERE source_type = ? AND source_id = ? AND platform = ?",
            (source_type, str(source_id), platform), one=True,
        )

    @staticmethod
    def create_from_seerfar(data):
        """Create one Seerfar candidate, returning (row, created)."""
        source_id = str(data.get('source_id') or '').strip()
        if not source_id:
            raise ValueError('Seerfar 商品缺少 source_id')
        platform = data.get('platform') or 'ozon'
        existing = ResearchItem.find_by_source('seerfar', source_id, platform)
        if existing:
            return existing, False
        payload = dict(data)
        payload['source_type'] = 'seerfar'
        payload['source_id'] = source_id
        payload['platform'] = platform
        payload.setdefault(
            'source_synced_at',
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        )
        try:
            item_id = ResearchItem.create(payload)
        except sqlite3.IntegrityError:
            existing = ResearchItem.find_by_source('seerfar', source_id, platform)
            if existing:
                return existing, False
            raise
        return ResearchItem.find_by_id(item_id), True

    @staticmethod
    def update(item_id, data):
        """更新选品条目（仅更新传入字段），有指标变化时重算蓝海评分"""
        allowed = [
            'kind', 'product_name', 'source_url', 'benchmark_store_id',
            'suggested_price', 'owner_name', 'notes', 'created_by',
            'owner_ids',
        ]
        fields = {k: v for k, v in data.items() if k in allowed and v is not None}
        if 'owner_ids' in fields and not isinstance(fields['owner_ids'], str):
            fields['owner_ids'] = json.dumps(fields['owner_ids'] or [], ensure_ascii=False)
        if not fields:
            return False
        existing = ResearchItem.find_by_id(item_id)
        if not existing:
            return False
        fields['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        set_sql = ', '.join(f"{k} = ?" for k in fields)
        execute(f"UPDATE product_research_items SET {set_sql} WHERE id = ?",
                (*fields.values(), item_id))
        return True

    @staticmethod
    def find_by_id(item_id):
        return query("SELECT * FROM product_research_items WHERE id = ?",
                     (item_id,), one=True)

    @staticmethod
    def find_all(kind=None, keyword=None, owner_id=None, owner_name=None):
        conditions, params = [], []
        if kind:
            conditions.append("kind = ?")
            params.append(kind)
        if keyword:
            conditions.append("(product_name LIKE ? OR notes LIKE ? OR source_url LIKE ?)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])
        if owner_id is not None:
            # owner_ids is a JSON array of numeric account IDs, e.g. [2, 5].
            # Match comma-delimited IDs to avoid treating account 2 as account 20.
            owner_ids_csv = "replace(replace(replace(owner_ids, '[', ''), ']', ''), ' ', '')"
            owner_conditions = [f"(',' || {owner_ids_csv} || ',') LIKE ?"]
            params.append(f'%,{int(owner_id)},%')
            if owner_name:
                owner_conditions.append("('、' || owner_name || '、') LIKE ?")
                params.append(f'%、{owner_name}、%')
            conditions.append('(' + ' OR '.join(owner_conditions) + ')')
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return query(
            f"SELECT * FROM product_research_items{where} "
            "ORDER BY id DESC",
            params
        )

    @staticmethod
    def delete(item_id):
        execute("DELETE FROM product_research_items WHERE id = ?", (item_id,))
