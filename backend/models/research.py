"""
OZON ERP - 选品池数据模型
从老 ERP（workbench product_research_items）全量迁移，SQLite 存储
"""
from datetime import datetime
from db import query, execute


class ResearchItem:
    """选品池条目模型"""

    STATUSES = ['待调研', '调研中', '已立项', '已放弃']

    @staticmethod
    def _num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def blue_ocean_score(monthly_revenue, growth_rate, top3_share,
                         seller_count, brand_count):
        """蓝海评分（0-100）：中规模 + 高增长 + 低垄断 = 高分。

        与老 ERP selection_service.blue_ocean_score 公式一致。
        top3_share 传 0-1 小数。
        """
        revenue_score = min(ResearchItem._num(monthly_revenue) / 5_000_000, 1.0) * 30
        growth_score = min(max(ResearchItem._num(growth_rate), 0) / 50, 1.0) * 35
        competition_score = (1 - ResearchItem._num(top3_share)) * 20
        mid_scale_score = 0.0
        sellers = ResearchItem._num(seller_count)
        if 50 <= sellers <= 500:
            mid_scale_score = 15
        elif 20 <= sellers <= 1000:
            mid_scale_score = 10
        diversity_score = min(ResearchItem._num(brand_count) / 100, 1.0) * 10
        return round(revenue_score + growth_score + competition_score
                     + mid_scale_score + diversity_score, 2)

    @staticmethod
    def compute_blue_ocean(row):
        """按字典行计算蓝海评分；缺关键指标返回 None。

        top3_brand_share 按百分数存储（如 35 表示 35%），计算时转 0-1。
        """
        monthly = ResearchItem._num(row.get('monthly_revenue'))
        sellers = ResearchItem._num(row.get('seller_count'))
        if monthly <= 0 or sellers <= 0:
            return None
        return ResearchItem.blue_ocean_score(
            monthly,
            row.get('revenue_growth_rate'),
            ResearchItem._num(row.get('top3_brand_share')) / 100.0,
            sellers,
            row.get('brand_count'),
        )

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
            'monthly_revenue': data.get('monthly_revenue'),
            'revenue_growth_rate': data.get('revenue_growth_rate'),
            'top3_brand_share': data.get('top3_brand_share'),
            'seller_count': data.get('seller_count'),
            'brand_count': data.get('brand_count'),
            'mpstats_source': data.get('mpstats_source') or 'manual',
            'owner_name': data.get('owner_name') or '未分配',
            'notes': data.get('notes') or '',
            'status': data.get('status') or '待调研',
            'created_by': data.get('created_by') or '',
            'created_at': now,
            'updated_at': now,
        }
        if data.get('blue_ocean_score') is not None:
            row['blue_ocean_score'] = data.get('blue_ocean_score')
        else:
            row['blue_ocean_score'] = ResearchItem.compute_blue_ocean(row)
        cols = list(row.keys())
        return execute(
            f"INSERT INTO product_research_items ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            tuple(row.values())
        )

    @staticmethod
    def update(item_id, data):
        """更新选品条目（仅更新传入字段），有指标变化时重算蓝海评分"""
        allowed = [
            'kind', 'product_name', 'source_url', 'benchmark_store_id',
            'suggested_price', 'monthly_revenue', 'revenue_growth_rate',
            'top3_brand_share', 'seller_count', 'brand_count',
            'mpstats_source', 'owner_name', 'notes', 'status', 'created_by',
        ]
        fields = {k: v for k, v in data.items() if k in allowed and v is not None}
        if not fields:
            return False
        existing = ResearchItem.find_by_id(item_id)
        if not existing:
            return False
        merged = {**existing, **fields}
        if any(k in fields for k in ('monthly_revenue', 'revenue_growth_rate',
                                     'top3_brand_share', 'seller_count', 'brand_count')):
            fields['blue_ocean_score'] = ResearchItem.compute_blue_ocean(merged)
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
    def find_all(kind=None, status=None, keyword=None):
        conditions, params = [], []
        if kind:
            conditions.append("kind = ?")
            params.append(kind)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if keyword:
            conditions.append("(product_name LIKE ? OR notes LIKE ? OR source_url LIKE ?)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return query(
            f"SELECT * FROM product_research_items{where} "
            f"ORDER BY COALESCE(blue_ocean_score, -1) DESC, id DESC",
            params
        )

    @staticmethod
    def delete(item_id):
        execute("DELETE FROM product_research_items WHERE id = ?", (item_id,))

    @staticmethod
    def status_counts():
        rows = query(
            "SELECT status, COUNT(*) AS cnt FROM product_research_items GROUP BY status"
        )
        return {r['status']: r['cnt'] for r in rows}
