"""
OZON ERP - 财务服务
1. 从 Ozon /v1/finance/accrual/by-day 逐笔同步财务明细（按店增量，幂等落库）
2. 中控大屏聚合：营业额 / 各店营收条 / 今日取消退货 / 财务大类明细 / 上架人员业绩榜
"""
from datetime import datetime, timedelta

from db import query, execute
from models.order import Order
from models.account import Store
from models.product import PublishRecord
from services.ozon_api import (
    _get_store_credentials, get_finance_accruals_by_day,
    get_finance_accrual_types,
)

# 已付款订单状态（营业额口径，与 AI 询盘助手保持一致）
PAID_STATUSES = (
    'awaiting_packaging', 'awaiting_deliver', 'awaiting_passport_data',
    'delivering', 'delivered', 'driver_pickup',
)

# 退货/取消相关状态
CANCELLED_STATUSES = (
    'cancelled', 'cancelled_by_seller', 'cancelled_by_client',
)
RETURN_STATUSES = (
    'arbitration', 'client_arbitration', 'in_arbitration',
    'client_returned_delivering', 'client_returned_delivered',
    'client_returned_unknown', 'not_accepted',
)

# Ozon 财务大类（新版 accrual 分类及旧接口值兼容） → 中文标签
PARENT_TYPE_LABELS = {
    'POSTING': '订单入账',
    'ITEM': '商品费用',
    'NON_ITEM': '服务费',
    'UNSPECIFIED': '其他',
    # v3 type 值（当前接口）
    'orders': '订单入账',
    'returns': '退货/取消',
    'services': '服务费',
    'compensation': '补贴/赔付',
    'transferDelivery': '运费',
    'other': '其他',
    'all': '全部',
    # v2 旧字段兼容
    'Marketplace': '平台结算',
    'Payout': '打款',
    'Refund': '退款',
    'Chargeback': '拒付',
    'Service': '服务费',
    'Compensation': '赔付',
    'OtherCoef': '其他系数',
    'CustomerOperations': '客户操作',
    'AdvanceTicket': '预打款',
    'LogisticServices': '物流服务',
    'SaleFinishedMarketplace': '交易完成结算',
    'Promo': '推广',
}


def _store_in_cond(store_ids, alias=''):
    """店铺隔离过滤片段：store_ids=None 返回 ('', [])；否则返回 (' AND {alias}store_id IN (...)', params)"""
    if not store_ids:
        return '', []
    alias_prefix = f"{alias}." if alias else ""
    ph = ','.join('?' * len(store_ids))
    return f" AND {alias_prefix}store_id IN ({ph})", list(store_ids)



def _labels():
    """Ozon 可能返回未收录的大类，兜底显示原始代码"""
    return PARENT_TYPE_LABELS


def _month_slices(date_from, date_to):
    """把 [date_from, date_to] 按自然月切片（Ozon v3 限制单次查询区间 ≤ 1 个月）"""
    start = datetime.strptime(date_from, '%Y-%m-%d')
    end = datetime.strptime(date_to, '%Y-%m-%d')
    slices = []
    cur = start
    while cur <= end:
        nxt = datetime(cur.year + 1, 1, 1) if cur.month == 12 \
            else datetime(cur.year, cur.month + 1, 1)
        seg_end = min(nxt - timedelta(days=1), end)
        slices.append((cur.strftime('%Y-%m-%d'), seg_end.strftime('%Y-%m-%d')))
        cur = nxt
    return slices


def sync_store_transactions(store_id, date_from=None, date_to=None, initial_days=90):
    """同步单个店铺的财务交易明细（增量，按 finance_sync_state 水位）

    Args:
        store_id: Ozon 店铺 ID（字符串）
        date_from/date_to: 'YYYY-MM-DD'；不传则从水位续拉，首次拉最近 90 天

    Returns:
        dict: {store_id, saved, total, from, to, error?}
    """
    today = datetime.now().strftime('%Y-%m-%d')
    state = query(
        "SELECT * FROM finance_sync_state WHERE store_id = ?", (store_id,), one=True
    )

    if not date_from:
        if state and state['last_to']:
            # 从上次水位次日续拉（重叠 1 天靠唯一索引去重）
            last = datetime.strptime(state['last_to'], '%Y-%m-%d')
            date_from = (last - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            date_from = (datetime.now() - timedelta(days=initial_days)).strftime('%Y-%m-%d')
    if not date_to:
        date_to = today

    client_id, api_key = _get_store_credentials(store_id)
    if not client_id or not api_key:
        return {'store_id': store_id, 'saved': 0, 'total': 0,
                'from': date_from, 'to': date_to, 'error': '店铺凭证不存在或未配置'}
    saved = 0
    total = 0
    try:
        types = get_finance_accrual_types(client_id, api_key).get('accrual_types') or []
        type_names = {str(t.get('id')): t.get('name', '') for t in types}
        cur = datetime.strptime(date_from, '%Y-%m-%d')
        end = datetime.strptime(date_to, '%Y-%m-%d')
        while cur <= end:
            day = cur.strftime('%Y-%m-%d')
            last_id = ''
            for _ in range(200):
                resp = get_finance_accruals_by_day(day, client_id, api_key, last_id)
                accruals = (resp or {}).get('accruals') or []
                if not accruals:
                    break
                for op in accruals:
                    nested = op.get('non_item_fee') or {}
                    type_id = nested.get('type_id')
                    if type_id is None:
                        type_id = op.get('type_id')
                    amount = (op.get('total_amount') or {}).get('amount') or 0
                    execute(
                        """INSERT OR IGNORE INTO finance_transactions
                           (store_id, operation_id, operation_type, operation_type_name,
                            operation_parent_type, operation_date, posting_number,
                            amount, payout, items_json, raw_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            store_id, str(op.get('accrual_id', '')),
                            str(type_id or ''), type_names.get(str(type_id), ''),
                            op.get('accrued_category') or 'UNSPECIFIED',
                            op.get('date') or day, op.get('unit_number') or '',
                            float(amount), None, _dumps(op), _dumps(op),
                        ),
                    )
                    saved += 1
                total += len(accruals)
                next_id = resp.get('last_id') or ''
                if not next_id or next_id == last_id:
                    break
                last_id = next_id
            else:
                raise RuntimeError(f'{day} 财务分页超过 200 页，已停止以避免漏数据')
            cur += timedelta(days=1)
    except Exception as exc:  # noqa: BLE001 —— 单店失败不影响其他店
        return {'store_id': store_id, 'saved': saved, 'total': total,
                'from': date_from, 'to': date_to, 'error': str(exc)}

    count_row = query(
        "SELECT COUNT(*) AS c FROM finance_transactions WHERE store_id = ?",
        (store_id,), one=True,
    )
    execute(
        """INSERT INTO finance_sync_state (store_id, last_to, synced_at, transaction_count)
           VALUES (?, ?, datetime('now'), ?)
           ON CONFLICT(store_id) DO UPDATE SET
             last_to = excluded.last_to,
             synced_at = excluded.synced_at,
             transaction_count = excluded.transaction_count""",
        (store_id, date_to, count_row['c'] if count_row else 0),
    )
    return {'store_id': store_id, 'saved': saved, 'total': total,
            'from': date_from, 'to': date_to}


def _dumps(obj):
    import json
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return None


def sync_all_stores(days=90, store_ids=None):
    """遍历所有凭证有效的店铺同步财务明细"""
    stores = Store.find_all(auth_status='active') or []
    if store_ids is not None:
        allowed = {str(store_id) for store_id in store_ids}
        stores = [store for store in stores if str(store.get('store_id')) in allowed]
    results = []
    for s in stores:
        sid = str(s['store_id'])
        r = sync_store_transactions(sid, initial_days=days)
        r['alias'] = s.get('alias') or sid
        results.append(r)
    return {'stores': results,
            'ok': sum(1 for r in results if not r.get('error')),
            'failed': sum(1 for r in results if r.get('error'))}


# ========== 中控大屏聚合 ==========

def control_dashboard(days=30, store_ids=None):
    """中控大屏数据聚合

    Returns dict:
      total_revenue / store_revenue / today(cancelled,returned) /
      finance_summary(按大类聚合+逐笔最近) / publisher_rank(上架人员业绩榜) /
      finance_sync(各店同步水位)
    """
    placeholders = ','.join('?' * len(PAID_STATUSES))
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # --- 营业额（已付款订单，近 N 天） ---
    st_cond, st_params = _store_in_cond(store_ids, 'o') if False else _store_in_cond(store_ids)
    total_row = query(
        f"""SELECT COALESCE(SUM(price_amount),0) AS rev, COUNT(*) AS cnt
            FROM orders WHERE status IN ({placeholders})
              AND COALESCE(in_process_at,'') >= ?{st_cond}""",
        (*PAID_STATUSES, since, *st_params), one=True,
    )
    total_revenue = round(float(total_row['rev'] or 0), 2)
    total_orders = int(total_row['cnt'] or 0)

    # --- 各店铺营收条（带店铺别名） ---
    st_cond2, st_params2 = _store_in_cond(store_ids, 'o')
    store_rows = query(
        f"""SELECT o.store_id,
                   COALESCE(s.alias, o.store_id) AS alias,
                   COALESCE(SUM(o.price_amount),0) AS rev,
                   COUNT(*) AS cnt
            FROM orders o
            LEFT JOIN stores s ON s.store_id = o.store_id
            WHERE o.status IN ({placeholders})
              AND COALESCE(o.in_process_at,'') >= ?{st_cond2}
            GROUP BY o.store_id, alias
            ORDER BY rev DESC""",
        (*PAID_STATUSES, since, *st_params2),
    )
    store_revenue = [
        {'store_id': r['store_id'], 'alias': r['alias'],
         'revenue': round(float(r['rev']), 2), 'orders': int(r['cnt'])}
        for r in store_rows
    ]

    # --- 今日取消 / 退货 ---
    today_str = datetime.now().strftime('%Y-%m-%d')
    cancel_ph = ','.join('?' * len(CANCELLED_STATUSES))
    return_ph = ','.join('?' * len(RETURN_STATUSES))
    st_cond3, st_params3 = _store_in_cond(store_ids)
    today_cancel = query(
        f"""SELECT COUNT(*) AS c FROM orders
            WHERE status IN ({cancel_ph})
              AND substr(COALESCE(updated_at,''),1,10) = ?{st_cond3}""",
        (*CANCELLED_STATUSES, today_str, *st_params3), one=True,
    )
    st_cond4, st_params4 = _store_in_cond(store_ids)
    today_return = query(
        f"""SELECT COUNT(*) AS c FROM orders
            WHERE status IN ({return_ph})
              AND substr(COALESCE(updated_at,''),1,10) = ?{st_cond4}""",
        (*RETURN_STATUSES, today_str, *st_params4), one=True,
    )
    # 全期取消/退货兜底（今日为 0 时前端也能看到总量）
    st_cond5, st_params5 = _store_in_cond(store_ids)
    total_cancel = query(
        f"SELECT COUNT(*) AS c FROM orders WHERE status IN ({cancel_ph}){st_cond5}",
        (*CANCELLED_STATUSES, *st_params5), one=True,
    )
    total_return = query(
        f"SELECT COUNT(*) AS c FROM orders WHERE status IN ({return_ph}){st_cond5}",
        (*RETURN_STATUSES, *st_params5), one=True,
    )

    # --- 财务大类聚合（近 N 天交易明细） ---
    st_cond6, st_params6 = _store_in_cond(store_ids, 't')
    fin_rows = query(
        f"""SELECT t.operation_parent_type, COUNT(*) AS cnt,
                  COALESCE(SUM(t.amount),0) AS amt
           FROM finance_transactions t
           WHERE substr(t.operation_date,1,10) >= ?{st_cond6}
           GROUP BY t.operation_parent_type ORDER BY amt DESC""",
        (since, *st_params6),
    )
    labels = _labels()
    finance_summary = [
        {'parent_type': r['operation_parent_type'] or 'Unknown',
         'label': labels.get(r['operation_parent_type'], r['operation_parent_type'] or '未知'),
         'count': int(r['cnt']), 'amount': round(float(r['amt']), 2)}
        for r in fin_rows
    ]
    st_cond7, st_params7 = _store_in_cond(store_ids, 't')
    recent_rows = query(
        f"""SELECT t.store_id, COALESCE(s.alias,t.store_id) AS alias,
                  t.operation_parent_type, t.operation_type, t.operation_type_name,
                  t.operation_date, t.posting_number, t.amount
           FROM finance_transactions t
           LEFT JOIN stores s ON s.store_id = t.store_id
           WHERE 1=1{st_cond7}
           ORDER BY t.operation_date DESC LIMIT 30""",
        (*st_params7,),
    )
    recent_transactions = [
        {'store_id': r['store_id'], 'alias': r['alias'],
         'parent_type': r['operation_parent_type'],
         'parent_label': labels.get(r['operation_parent_type'], r['operation_parent_type'] or '未知'),
         'operation_type': r['operation_type'],
         'operation_type_name': r['operation_type_name'] or '',
         'date': r['operation_date'], 'posting_number': r['posting_number'],
         'amount': round(float(r['amount']), 2)}
        for r in recent_rows
    ]
    net_amount = round(sum(item['amount'] for item in finance_summary), 2)

    # --- 上架人员业绩榜（替代老 ERP 业务员榜，按 PublishRecord.publisher） ---
    publisher_rank = _publisher_rank(days)

    # --- 各店财务同步水位 ---
    st_cond8, st_params8 = _store_in_cond(store_ids, 'f')
    sync_rows = query(
        f"""SELECT f.store_id, COALESCE(s.alias,f.store_id) AS alias,
                  f.last_to, f.synced_at, f.transaction_count
           FROM finance_sync_state f
           LEFT JOIN stores s ON s.store_id = f.store_id
           WHERE 1=1{st_cond8}
           ORDER BY f.synced_at DESC""",
        (*st_params8,),
    )

    return {
        'period_days': days,
        'total_revenue': total_revenue,
        'total_orders': total_orders,
        'currency': 'RUB',
        'store_revenue': store_revenue,
        'today': {
            'cancelled': int(today_cancel['c'] or 0),
            'returned': int(today_return['c'] or 0),
            'total_cancelled_all': int(total_cancel['c'] or 0),
            'total_returned_all': int(total_return['c'] or 0),
        },
        'finance': {
            'net_amount': net_amount,
            'summary': finance_summary,
            'recent': recent_transactions,
        },
        'publisher_rank': publisher_rank,
        'sync_state': [dict(r) for r in sync_rows],
    }


def _publisher_rank(days=30):
    """上架人员业绩榜：预估利润 − 退货损失 = 净业绩

    - 预估利润（estProfit）：来自上架记录 PublishRecord（业务员填写，₽/件）
    - 退货损失（loss_amount）：来自订单 orders（业务员在退货/取消单登记，₽）
    - 净业绩 = Σ预估利润 − Σ退货损失
    """
    records = PublishRecord.find_all() or []
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S')
    buckets = {}
    for r in records:
        created = (r.get('createdAt') or r.get('created_at') or '')
        if created and created < since:
            continue
        name = (r.get('publisher') or '').strip() or '未署名'
        b = buckets.setdefault(name, {
            'publisher': name, 'total': 0, 'published': 0, 'failed': 0,
            'est_profit': 0.0, 'loss_total': 0.0,
            'stores': set(),
        })
        b['total'] += 1
        status = r.get('status') or ''
        if status in ('published', 'published_with_errors'):
            b['published'] += 1
        elif status == 'failed':
            b['failed'] += 1
        try:
            b['est_profit'] += float(r.get('estProfit') or 0)
        except (TypeError, ValueError):
            pass
        store_name = r.get('storeName') or ''
        if store_name:
            b['stores'].add(store_name)

    # 退货损失：按 loss_publisher 归属到业务员（近 N 天填写的损失）
    since_day = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    loss_rows = query(
        """SELECT loss_publisher AS lp, COALESCE(SUM(loss_amount),0) AS amt, COUNT(*) AS cnt
           FROM orders
           WHERE loss_publisher != '' AND loss_amount > 0
             AND updated_at >= ?
           GROUP BY loss_publisher""",
        (since_day,),
    )
    for row in loss_rows:
        name = (row['lp'] or '').strip() or '未署名'
        buckets.setdefault(name, {
            'publisher': name, 'total': 0, 'published': 0, 'failed': 0,
            'est_profit': 0.0, 'loss_total': 0.0, 'stores': set(),
        })
        buckets[name]['loss_total'] = round(float(row['amt'] or 0), 2)

    rank = []
    for b in buckets.values():
        est = round(b['est_profit'], 2)
        loss = round(b['loss_total'], 2)
        rank.append({
            'publisher': b['publisher'],
            'total': b['total'],
            'published': b['published'],
            'failed': b['failed'],
            'success_rate': round(b['published'] / b['total'] * 100, 1) if b['total'] else 0,
            'est_profit': est,
            'loss_total': loss,
            'net_profit': round(est - loss, 2),
            'stores': sorted(b['stores']),
        })
    rank.sort(key=lambda x: (-x['net_profit'], -x['published'], -x['total']))
    return rank


def finance_center(days=30, store_id=None, store_ids=None):
    """财务中心简版页数据：合计 + 按大类 + 按店铺 + 明细分页（store_ids 为店铺隔离集合）"""
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    where = "substr(operation_date,1,10) >= ?"
    params = [since]
    if store_id:
        where += " AND t.store_id = ?"
        params.append(str(store_id))
    # 店铺隔离
    st_cond, st_params = _store_in_cond(store_ids, 't')
    if st_cond:
        where += st_cond
        params.extend(st_params)

    totals = query(
        f"""SELECT COUNT(*) AS cnt, COALESCE(SUM(t.amount),0) AS amt,
                   COALESCE(SUM(CASE WHEN t.amount > 0 THEN t.amount ELSE 0 END),0) AS inflow,
                   COALESCE(SUM(CASE WHEN t.amount < 0 THEN t.amount ELSE 0 END),0) AS outflow
            FROM finance_transactions t WHERE {where}""",
        params, one=True,
    )
    by_type = query(
        f"""SELECT t.operation_parent_type AS pt, COUNT(*) AS cnt,
                   COALESCE(SUM(t.amount),0) AS amt
            FROM finance_transactions t WHERE {where}
            GROUP BY pt ORDER BY amt DESC""",
        params,
    )
    by_store = query(
        f"""SELECT t.store_id, COALESCE(s.alias,t.store_id) AS alias,
                   COUNT(*) AS cnt, COALESCE(SUM(t.amount),0) AS amt
            FROM finance_transactions t
            LEFT JOIN stores s ON s.store_id = t.store_id
            WHERE {where}
            GROUP BY t.store_id, alias ORDER BY amt DESC""",
        params,
    )
    labels = _labels()
    fc_sync_rows = _fc_sync_state_rows(store_ids)
    return {
        'period_days': days,
        'total_count': int(totals['cnt'] or 0),
        'net_amount': round(float(totals['amt'] or 0), 2),
        'inflow': round(float(totals['inflow'] or 0), 2),
        'outflow': round(float(totals['outflow'] or 0), 2),
        'by_type': [
            {'parent_type': r['pt'] or 'Unknown',
             'label': labels.get(r['pt'], r['pt'] or '未知'),
             'count': int(r['cnt']), 'amount': round(float(r['amt']), 2)}
            for r in by_type
        ],
        'by_store': [
            {'store_id': r['store_id'], 'alias': r['alias'],
             'count': int(r['cnt']), 'amount': round(float(r['amt']), 2)}
            for r in by_store
        ],
        'sync_state': [dict(r) for r in fc_sync_rows],
    }


def _fc_sync_state_rows(store_ids):
    """财务同步水位（店铺隔离过滤）"""
    cond, params = _store_in_cond(store_ids, 'f')
    return query(
        f"""SELECT f.store_id, COALESCE(s.alias,f.store_id) AS alias,
                  f.last_to, f.synced_at, f.transaction_count
           FROM finance_sync_state f
           LEFT JOIN stores s ON s.store_id = f.store_id
           WHERE 1=1{cond}
           ORDER BY f.synced_at DESC""",
        params,
    )


def transaction_list(days=30, store_id=None, parent_type=None,
                     page=1, page_size=50, store_ids=None):
    """财务明细分页查询（财务中心表格）"""
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    where = ["substr(t.operation_date,1,10) >= ?"]
    params = [since]
    if store_id:
        where.append("t.store_id = ?")
        params.append(str(store_id))
    # 店铺隔离
    if store_ids:
        where.append("t.store_id IN ({})".format(','.join('?' * len(store_ids))))
        params.extend(store_ids)
    if parent_type:
        where.append("t.operation_parent_type = ?")
        params.append(parent_type)
    where_sql = ' AND '.join(where)

    total = query(
        f"SELECT COUNT(*) AS c FROM finance_transactions t WHERE {where_sql}",
        params, one=True,
    )
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    rows = query(
        f"""SELECT t.id, t.store_id, COALESCE(s.alias,t.store_id) AS alias,
                   t.operation_id, t.operation_type, t.operation_type_name,
                   t.operation_parent_type,
                   t.operation_date, t.posting_number, t.amount, t.payout,
                   t.items_json
            FROM finance_transactions t
            LEFT JOIN stores s ON s.store_id = t.store_id
            WHERE {where_sql}
            ORDER BY t.operation_date DESC, t.id DESC
            LIMIT ? OFFSET ?""",
        (*params, page_size, (page - 1) * page_size),
    )
    labels = _labels()
    items = []
    for r in rows:
        items.append({
            'id': r['id'], 'store_id': r['store_id'], 'alias': r['alias'],
            'operation_id': r['operation_id'],
            'operation_type': r['operation_type'],
            'operation_type_name': r['operation_type_name'] or '',
            'parent_type': r['operation_parent_type'],
            'parent_label': labels.get(r['operation_parent_type'], r['operation_parent_type'] or '未知'),
            'date': r['operation_date'],
            'posting_number': r['posting_number'],
            'amount': round(float(r['amount']), 2),
            'payout': r['payout'],
        })
    return {'items': items, 'total': int(total['c'] or 0),
            'page': page, 'page_size': page_size}
