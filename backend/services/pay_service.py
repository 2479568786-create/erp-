"""充值订单业务层（状态机 + 幂等入账）。

设计要点
- 状态机单向：created → paid → credited（关闭：created → closed）
- 入账幂等：`UPDATE ... WHERE status='paid'` 抢一次，rowcount=0 就不再点数
- 入账复用 image_billing_service.adjust_balance，流水落 image_ops_log（operator='wxpay:<订单号>'）
- 所有函数支持传入 conn（单测用临时库），不传才用 app 的 db 助手（懒加载，避免测试时连到生产库）
"""
import os
import random
import string
import time
from datetime import datetime, timedelta, timezone

TIERS_POINTS = (10, 30, 50, 100, 200, 500)
ORDER_TTL_MINUTES = 15
ORDERS_PER_MINUTE = 3

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS pay_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        out_trade_no TEXT NOT NULL UNIQUE,
        channel TEXT NOT NULL DEFAULT 'wxpay',
        account_id INTEGER NOT NULL,
        operator_id INTEGER NOT NULL,
        amount_cents INTEGER NOT NULL,
        points REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'created',
        code_url TEXT,
        transaction_id TEXT,
        paid_at TEXT,
        credited_at TEXT,
        notify_raw TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS pay_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        out_trade_no TEXT NOT NULL,
        event TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pay_events_no ON pay_events(out_trade_no)",
    "CREATE INDEX IF NOT EXISTS idx_pay_orders_status ON pay_orders(status, created_at)",
)

_table_ready = False


def ensure_tables(conn=None):
    """幂等建表；conn 为空时走 app 的 db.execute（只在首次调用时做一次）。"""
    global _table_ready
    if conn is not None:
        for sql in SCHEMA:
            conn.execute(sql)
        conn.commit()
        return
    if _table_ready:
        return
    from db import execute
    for sql in SCHEMA:
        execute(sql)
    _table_ready = True


def _db_query(sql, params=(), one=True):
    from db import query
    return query(sql, params, one=one)


def _db_execute(sql, params=()):
    from db import execute
    return execute(sql, params)


def _utc_str(dt):
    """本地 datetime -> SQLite datetime('now') 同口径的 UTC 字符串。"""
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _local_today_start(day=None):
    """本地某天 00:00；day 形如 2026-11-05，缺省为本地今天。"""
    if day:
        base = datetime.strptime(day, '%Y-%m-%d')
        return base.replace(hour=0, minute=0, second=0, microsecond=0).astimezone()
    now = datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def daily_cap_points():
    try:
        return int(os.environ.get('WXPAY_DAILY_CAP_POINTS', '2000'))
    except (TypeError, ValueError):
        return 2000


def _row_to_dict(row):
    if row is None:
        return None
    return dict(row) if hasattr(row, 'keys') else row


def _fetch(conn, sql, params=(), one=True):
    if conn is not None:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            return None
        cols = [d[0] for d in cur.description]
        value = [dict(zip(cols, r)) for r in rows]
        return (value[0] if value else None) if one else value
    return _row_to_dict(_db_query(sql, params, one=one))


def _write(conn, sql, params=()):
    if conn is not None:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    return _db_execute(sql, params)


def new_out_trade_no(now=None):
    now = now or datetime.now()
    suffix = ''.join(random.choice(string.digits) for _ in range(6))
    return 'RC%s%s' % (now.strftime('%Y%m%d%H%M%S'), suffix)


def validate_points(points):
    """只接受白名单档位；返回 (ok, points_int_or_none, reason)。"""
    try:
        value = int(points)
    except (TypeError, ValueError):
        return False, None, '点数必须是整数'
    if value not in TIERS_POINTS:
        return False, None, '只支持这些充值档位：%s 点' % '/'.join(str(t) for t in TIERS_POINTS)
    return True, value, ''


def orders_last_minute(account_id, conn=None, now_ts=None):
    now_ts = now_ts if now_ts is not None else time.time()
    since = _utc_str(datetime.fromtimestamp(now_ts - 60).astimezone())
    sql = "SELECT COUNT(*) AS c FROM pay_orders WHERE account_id = ? AND created_at >= ?"
    if conn is not None:
        cur = conn.execute(sql, (account_id, since))
        return int(cur.fetchone()[0])
    row = _db_query(sql, (account_id, since), one=True)
    return int(row['c'] if row else 0)


def daily_points(account_id, conn=None, day=None):
    """当天该账号「已支付/已入账」的点数合计（不含 created/closed）。"""
    start = _local_today_start(day)
    end = start + timedelta(days=1)
    sql = ("SELECT COALESCE(SUM(points), 0) AS p FROM pay_orders "
           "WHERE account_id = ? AND status IN ('paid', 'credited') "
           "AND created_at >= ? AND created_at < ?")
    params = (account_id, _utc_str(start), _utc_str(end))
    if conn is not None:
        cur = conn.execute(sql, params)
        return float(cur.fetchone()[0] or 0)
    row = _db_query(sql, params, one=True)
    return float(row['p'] if row else 0)


def create_order(out_trade_no, account_id, operator_id, points, amount_cents, conn=None):
    _write(conn,
           "INSERT INTO pay_orders (out_trade_no, channel, account_id, operator_id, amount_cents, points, status) "
           "VALUES (?, 'wxpay', ?, ?, ?, ?, 'created')",
           (out_trade_no, account_id, operator_id, int(amount_cents), float(points)))
    return get_order(out_trade_no, conn=conn)


def get_order(out_trade_no, conn=None):
    return _fetch(conn, "SELECT * FROM pay_orders WHERE out_trade_no = ?", (out_trade_no,))


def set_code_url(out_trade_no, code_url, conn=None):
    return _write(conn,
                  "UPDATE pay_orders SET code_url = ?, updated_at = datetime('now') WHERE out_trade_no = ?",
                  (code_url, out_trade_no))


def mark_paid(out_trade_no, transaction_id, notify_raw=None, conn=None):
    return _write(conn,
                  "UPDATE pay_orders SET status = 'paid', transaction_id = ?, notify_raw = ?, "
                  "paid_at = datetime('now'), updated_at = datetime('now') "
                  "WHERE out_trade_no = ? AND status = 'created'",
                  (transaction_id, notify_raw, out_trade_no))


def close_order(out_trade_no, conn=None):
    return _write(conn,
                  "UPDATE pay_orders SET status = 'closed', updated_at = datetime('now') "
                  "WHERE out_trade_no = ? AND status = 'created'",
                  (out_trade_no,))


def claim_credit(out_trade_no, conn=None):
    """把 paid 原子地改成 credited；抢到返回 True。"""
    changed = _write(conn,
                     "UPDATE pay_orders SET status = 'credited', credited_at = datetime('now'), "
                     "updated_at = datetime('now') WHERE out_trade_no = ? AND status = 'paid'",
                     (out_trade_no,))
    return int(changed or 0) == 1


def log_event(out_trade_no, event, detail='', conn=None):
    return _write(conn,
                  "INSERT INTO pay_events (out_trade_no, event, detail) VALUES (?, ?, ?)",
                  (out_trade_no, event, str(detail)[:2000]))


def credit_order(out_trade_no, conn=None, adjust=None):
    """幂等入账：只有成功把 paid→credited 的那一次才加点数。"""
    order = get_order(out_trade_no, conn=conn)
    if not order:
        return {'ok': False, 'reason': 'order_not_found'}
    if order['status'] == 'credited':
        return {'ok': True, 'reason': 'already_credited', 'points': order['points']}
    if order['status'] != 'paid':
        return {'ok': False, 'reason': 'not_paid', 'status': order['status']}
    if not claim_credit(out_trade_no, conn=conn):
        return {'ok': True, 'reason': 'lost_race', 'points': order['points']}
    if adjust is None:
        from services import image_billing_service as billing
        adjust = billing.adjust_balance
    try:
        adjust(order['account_id'], points=order['points'], mode='add',
               reason='微信支付充值 %s 元' % (order['amount_cents'] / 100.0),
               operator='wxpay:%s' % out_trade_no)
    except Exception as exc:  # noqa: BLE001
        _write(conn,
               "UPDATE pay_orders SET status = 'paid', credited_at = NULL, updated_at = datetime('now') "
               "WHERE out_trade_no = ? AND status = 'credited'",
               (out_trade_no,))
        log_event(out_trade_no, 'credit_fail', str(exc)[:300], conn=conn)
        return {'ok': False, 'reason': 'credit_failed', 'detail': str(exc)[:200]}
    log_event(out_trade_no, 'credit_ok', '%s points' % order['points'], conn=conn)
    return {'ok': True, 'reason': 'credited', 'points': order['points']}


def expire_created(conn=None, ttl_minutes=ORDER_TTL_MINUTES):
    """把超时未支付的单关掉（不动已支付未入账的）。"""
    cutoff = _utc_str((datetime.now() - timedelta(minutes=ttl_minutes)).astimezone())
    return _write(conn,
                  "UPDATE pay_orders SET status = 'closed', updated_at = datetime('now') "
                  "WHERE status = 'created' AND created_at < ?",
                  (cutoff,))
