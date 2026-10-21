"""
OZON ERP - 我的选品数据模型
每个账号独立维护自己的选品台账（账号隔离）；管理员可查看/操作所有账号的选品
字段：产品名称、采购链接、贴单费、购买成本；日常上品自动带入产品名称，其余人工补充
"""
from datetime import datetime
from db import query, execute


def _ensure_table():
    execute("""CREATE TABLE IF NOT EXISTS my_selections (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id           INTEGER NOT NULL,
        product_name      TEXT    NOT NULL DEFAULT '',
        purchase_url      TEXT    NOT NULL DEFAULT '',
        sticker_fee       REAL    NOT NULL DEFAULT 0,
        purchase_cost     REAL    NOT NULL DEFAULT 0,
        source_product_id TEXT    NOT NULL DEFAULT '',
        auto_filled       INTEGER NOT NULL DEFAULT 0,
        created_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    )""")
    execute("CREATE INDEX IF NOT EXISTS idx_my_selections_user ON my_selections(user_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_my_selections_source ON my_selections(user_id, source_product_id)")
    # 迁移：给已有表补齐 purchase_cost 列（幂等）
    try:
        execute("ALTER TABLE my_selections ADD COLUMN purchase_cost REAL NOT NULL DEFAULT 0")
    except Exception:
        pass


class MySelection:
    """我的选品条目（按 user_id 隔离；管理员传 user_id=None 表示不过滤）"""

    @staticmethod
    def create(user_id, data):
        _ensure_table()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        fee = data.get('sticker_fee')
        cost = data.get('purchase_cost')
        row = {
            'user_id': int(user_id),
            'product_name': (data.get('product_name') or '').strip(),
            'purchase_url': (data.get('purchase_url') or '').strip(),
            'sticker_fee': float(fee) if fee not in (None, '') else 0.0,
            'purchase_cost': float(cost) if cost not in (None, '') else 0.0,
            'source_product_id': data.get('source_product_id') or '',
            'auto_filled': 1 if data.get('auto_filled') else 0,
            'created_at': now,
            'updated_at': now,
        }
        cols = list(row.keys())
        return execute(
            f"INSERT INTO my_selections ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            tuple(row.values()),
        )

    @staticmethod
    def update(item_id, user_id, data):
        allowed = ['product_name', 'purchase_url', 'sticker_fee', 'purchase_cost', 'source_product_id']
        fields = {k: v for k, v in data.items() if k in allowed and v is not None}
        if not fields:
            return False
        if 'product_name' in fields:
            fields['product_name'] = (fields['product_name'] or '').strip()
        if 'purchase_url' in fields:
            fields['purchase_url'] = (fields['purchase_url'] or '').strip()
        fields['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        set_sql = ', '.join(f"{k} = ?" for k in fields)
        if user_id is not None:
            execute(f"UPDATE my_selections SET {set_sql} WHERE id = ? AND user_id = ?",
                    (*fields.values(), item_id, user_id))
        else:
            execute(f"UPDATE my_selections SET {set_sql} WHERE id = ?",
                    (*fields.values(), item_id))
        return True

    @staticmethod
    def delete(item_id, user_id=None):
        if user_id is not None:
            execute("DELETE FROM my_selections WHERE id = ? AND user_id = ?", (item_id, user_id))
        else:
            execute("DELETE FROM my_selections WHERE id = ?", (item_id,))

    @staticmethod
    def find_by_id(item_id, user_id=None):
        _ensure_table()
        if user_id is not None:
            return query("SELECT * FROM my_selections WHERE id = ? AND user_id = ?",
                         (item_id, user_id), one=True)
        return query("SELECT * FROM my_selections WHERE id = ?", (item_id,), one=True)

    @staticmethod
    def find_all(user_id=None, keyword=None):
        _ensure_table()
        conditions, params = [], []
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(int(user_id))
        if keyword:
            conditions.append("(product_name LIKE ? OR purchase_url LIKE ?)")
            kw = f"%{keyword}%"
            params.extend([kw, kw])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return query(f"SELECT * FROM my_selections{where} ORDER BY id DESC", params)

    @staticmethod
    def find_all_admin(keyword=None):
        """管理员视图：返回所有账号的选品，带 owner_name/owner_username"""
        _ensure_table()
        conditions, params = [], []
        if keyword:
            conditions.append("(ms.product_name LIKE ? OR ms.purchase_url LIKE ? OR COALESCE(a.nickname,'') LIKE ? OR COALESCE(a.username,'') LIKE ?)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw, kw])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return query(
            f"""SELECT ms.*, COALESCE(a.nickname, '') AS owner_name, COALESCE(a.username, '') AS owner_username
                FROM my_selections ms
                LEFT JOIN accounts a ON a.id = ms.user_id
                {where} ORDER BY ms.id DESC""",
            params,
        )

    @staticmethod
    def find_by_source_product(user_id, source_product_id):
        _ensure_table()
        if not source_product_id:
            return None
        return query(
            "SELECT * FROM my_selections WHERE user_id = ? AND source_product_id = ?",
            (int(user_id), source_product_id), one=True,
        )
