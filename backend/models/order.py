"""
OZON ERP - 订单数据模型（FBS / FBO 发货单）
基于 SQLite，以 posting_number 为业务主键做幂等写入
"""
import json
from datetime import datetime
from db import query, execute, execute_many


class Order:
    """订单模型 - 管理从 Ozon 拉取的 FBS/FBO 发货单"""

    # Ozon posting 状态 → 中文标签
    STATUS_LABELS = {
        'awaiting_packaging': '待打包',
        'awaiting_deliver': '待发货',
        'awaiting_passport_data': '等待护照数据',
        'arbitration': '仲裁中',
        'client_arbitration': '客户仲裁',
        'client_returned_delivering': '客户退回配送中',
        'client_returned_delivered': '客户已退回',
        'client_returned_unknown': '客户退回待确认',
        'delivering': '配送中',
        'delivered': '已送达',
        'driver_pickup': '司机取货',
        'not_accepted': '未接受',
        'cancelled': '已取消',
        'cancelled_by_seller': '卖家取消',
        'cancelled_by_client': '客户取消',
        'in_arbitration': '仲裁中',
    }

    @staticmethod
    def status_label(status):
        """Ozon 原始状态 → 中文标签"""
        return Order.STATUS_LABELS.get(status or '', status or '-')

    @staticmethod
    def upsert(posting_number, store_id, fulfillment_type, status,
               in_process_at=None, ship_date=None, delivery_date=None,
               cancel_reason=None, customer=None, price=None,
               products=None, financial=None, raw=None):
        """按 posting_number 幂等写入/更新订单

        Returns:
            int: 订单主键 id（新增或已有）
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        existing = Order.find_by_posting(posting_number)
        customer = customer or {}
        price = price or {}
        products_json = json.dumps(products, ensure_ascii=False) if products is not None else '[]'
        financial_json = json.dumps(financial, ensure_ascii=False) if financial is not None else None
        raw_json = json.dumps(raw, ensure_ascii=False) if raw is not None else None

        fields = {
            'store_id': store_id,
            'fulfillment_type': fulfillment_type,
            'status': status or '',
            'status_label': Order.status_label(status),
            'in_process_at': in_process_at,
            'ship_date': ship_date,
            'delivery_date': delivery_date,
            'cancel_reason': cancel_reason,
            'customer_name': customer.get('name'),
            'customer_country': customer.get('country'),
            'customer_city': customer.get('city'),
            'customer_address': customer.get('address'),
            'price_amount': price.get('amount', 0),
            'price_currency': price.get('currency', 'RUB'),
            'products_json': products_json,
            'financial_json': financial_json,
            'raw_json': raw_json,
        }
        if existing:
            fields['updated_at'] = now
            set_sql = ', '.join(f"{k} = ?" for k in fields)
            execute(f"UPDATE orders SET {set_sql} WHERE posting_number = ?",
                    (*fields.values(), posting_number))
            return existing['id']
        fields['posting_number'] = posting_number
        fields['created_at'] = now
        fields['updated_at'] = now
        cols = list(fields.keys())
        return execute(
            f"INSERT INTO orders ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            tuple(fields.values())
        )

    @staticmethod
    def find_by_posting(posting_number):
        """按发货单号查找"""
        return query("SELECT * FROM orders WHERE posting_number = ?", (posting_number,), one=True)

    @staticmethod
    def find_by_id(order_id):
        """按主键查找"""
        return query("SELECT * FROM orders WHERE id = ?", (order_id,), one=True)

    @staticmethod
    def find_all(store_id=None, store_ids=None, fulfillment_type=None, status=None, keyword=None,
                 date_from=None, date_to=None, limit=100, offset=0):
        """订单列表查询，支持多条件筛选；store_ids 为可见店铺 ID 列表（业务员隔离）"""
        conditions = []
        params = []
        if store_ids:
            conditions.append("store_id IN ({})".format(','.join('?' * len(store_ids))))
            params.extend(store_ids)
        if store_id:
            conditions.append("store_id = ?")
            params.append(store_id)
        if fulfillment_type:
            conditions.append("fulfillment_type = ?")
            params.append(fulfillment_type)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if keyword:
            conditions.append("(posting_number LIKE ? OR customer_name LIKE ? OR customer_city LIKE ?)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])
        if date_from:
            conditions.append("in_process_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("in_process_at <= ?")
            params.append(date_to)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = query(
            f"SELECT * FROM orders{where} ORDER BY COALESCE(in_process_at, created_at) DESC LIMIT ? OFFSET ?",
            (*params, limit, offset)
        )
        total = query(
            f"SELECT COUNT(*) as cnt FROM orders{where}", params, one=True
        )
        return rows, (total['cnt'] if total else 0)

    @staticmethod
    def count(store_id=None, fulfillment_type=None):
        """订单数量统计"""
        conditions = []
        params = []
        if store_id:
            conditions.append("store_id = ?")
            params.append(store_id)
        if fulfillment_type:
            conditions.append("fulfillment_type = ?")
            params.append(fulfillment_type)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        result = query(f"SELECT COUNT(*) as cnt FROM orders{where}", params, one=True)
        return result['cnt'] if result else 0

    @staticmethod
    def count_by_status(store_id=None, store_ids=None, fulfillment_type=None):
        """按状态分组统计订单数（用于看板 Tab 角标）"""
        conditions = []
        params = []
        if store_ids:
            conditions.append("store_id IN ({})".format(','.join('?' * len(store_ids))))
            params.extend(store_ids)
        if store_id:
            conditions.append("store_id = ?")
            params.append(store_id)
        if fulfillment_type:
            conditions.append("fulfillment_type = ?")
            params.append(fulfillment_type)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = query(
            f"SELECT status, status_label, COUNT(*) as cnt FROM orders{where} GROUP BY status ORDER BY cnt DESC",
            params
        )
        return rows

    @staticmethod
    def status_counts(store_id=None, store_ids=None, fulfillment_type=None):
        """返回 {status: count} 便于前端 Tab 展示"""
        rows = Order.count_by_status(store_id, store_ids, fulfillment_type)
        return {r['status']: r['cnt'] for r in rows}

    @staticmethod
    def batch_delete(ids):
        """批量删除本地订单记录（不同步 Ozon 侧）"""
        from db import execute_many
        execute_many("DELETE FROM orders WHERE id = ?", [(i,) for i in ids])

    @staticmethod
    def delete_all(store_id=None):
        """清空订单（可指定店铺）"""
        if store_id:
            execute("DELETE FROM orders WHERE store_id = ?", (store_id,))
        else:
            execute("DELETE FROM orders")