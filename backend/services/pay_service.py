"""充值订单业务层（状态机 + 幂等入账 + 回调去重）。

- 状态机单向：created → paid → credited（created → closed）
- 入账幂等：`UPDATE ... WHERE status='paid'` 抢一次；另加回调去重键
- 回调去重：`notify_digest` = sha256(回调原文) 唯一索引 + `notify_id` 记录微信回调 id
- 入账复用 image_billing_service.adjust_balance（operator='wxpay:<订单号>'，流水落 image_ops_log）
- 档位来自环境变量 WECHATPAY_TIERS（默认 10,30,50,100,200），不写死在代码里
- 所有函数支持传入 conn（单测用临时库），不传才用 app 的 db（懒加载，避免测试连生产库）
"""
import hashlib
import os
import random
import string
import time
from datetime import datetime, timedelta, timezone

DEFAULT_TIERS = (10, 30, 50, 100, 200)
ORDER_TTL_MINUTES = 15
ORDERS_PER_MINUTE = 3
# 入账流水里的渠道名（operator 前缀与文案都按 channel 区分，避免支付宝订单被记成微信）
# 渠道展示名（唯一来源；pay_routes 直接引用，避免两套定义漂移）
CHANNEL_LABELS = {'wxpay': '微信扫码', 'alipay': '支付宝（电脑网站支付）'}
# 台账/充值原因里用的短名（业务员对账时看的是这个）
CHANNEL_SHORT_LABELS = {'wxpay': '微信支付', 'alipay': '支付宝'}

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
        prepay_id TEXT,
        transaction_id TEXT,
        notify_id TEXT,
        notify_digest TEXT,
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
    """CREATE TABLE IF NOT EXISTS pay_refunds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        out_trade_no TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'wxpay',
        out_request_no TEXT NOT NULL UNIQUE,
        amount_cents INTEGER NOT NULL,
        reason TEXT,
        status TEXT NOT NULL DEFAULT 'created',
        trade_no TEXT,
        refund_no TEXT,
        notify_digest TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pay_events_no ON pay_events(out_trade_no)",
    "CREATE INDEX IF NOT EXISTS idx_pay_orders_status ON pay_orders(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_pay_refunds_order ON pay_refunds(out_trade_no)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_pay_orders_transaction ON pay_orders(transaction_id) "
    "WHERE transaction_id IS NOT NULL AND transaction_id != ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_pay_orders_notify_digest ON pay_orders(notify_digest) "
    "WHERE notify_digest IS NOT NULL AND notify_digest != ''",
)

# 建表语句 / 索引语句分开：索引必须等所有表都建完再建（pay_refunds 表在索引之前）
TABLES = SCHEMA[:3]
INDEXES = SCHEMA[3:]

ADD_COLUMNS = (
    ('prepay_id', "ALTER TABLE pay_orders ADD COLUMN prepay_id TEXT"),
    ('notify_id', "ALTER TABLE pay_orders ADD COLUMN notify_id TEXT"),
    ('notify_digest', "ALTER TABLE pay_orders ADD COLUMN notify_digest TEXT"),
)

_table_ready = False


def ensure_tables(conn=None):
    """幂等建表 + 补列 + 建索引。"""
    global _table_ready
    if conn is not None:
        for sql in TABLES:
            conn.execute(sql)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(pay_orders)")}
        for name, ddl in ADD_COLUMNS:
            if name not in cols:
                conn.execute(ddl)
        for sql in INDEXES:
            conn.execute(sql)
        conn.commit()
        return
    if _table_ready:
        return
    from db import execute, query
    for sql in TABLES:
        execute(sql)
    cols = {row['name'] for row in query("PRAGMA table_info(pay_orders)")}
    for name, ddl in ADD_COLUMNS:
        if name not in cols:
            execute(ddl)
    for sql in INDEXES:
        execute(sql)
    _table_ready = True


def _db_query(sql, params=(), one=True):
    from db import query
    return query(sql, params, one=one)


def _db_execute(sql, params=()):
    from db import execute
    return execute(sql, params)


def tiers():
    raw = (os.environ.get('WECHATPAY_TIERS') or '').strip()
    if not raw:
        return list(DEFAULT_TIERS)
    out = []
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if value > 0:
            out.append(value)
    return sorted(set(out)) or list(DEFAULT_TIERS)


def daily_cap_points():
    try:
        return int(os.environ.get('WECHATPAY_DAILY_CAP_POINTS', '2000'))
    except (TypeError, ValueError):
        return 2000


def _utc_str(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _local_today_start(day=None):
    if day:
        base = datetime.strptime(day, '%Y-%m-%d')
        return base.replace(hour=0, minute=0, second=0, microsecond=0).astimezone()
    now = datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


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
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        except Exception:
            conn.rollback()
            raise
    return _db_execute(sql, params)


def _write_affected(conn, sql, params=()):
    """执行 UPDATE/DELETE 并返回**真实影响行数**。

    为什么需要它：`db.execute()` 返回的是 `cursor.lastrowid`，而 UPDATE 的 lastrowid
    恒为 0。生产路径（conn=None）若拿 `_write` 的返回值判断「有没有抢到」，会永远判成
    没抢到——2026-11-08 事故：订单被标 credited、`claim_credit` 却返回 0，`credit_order`
    误判成 lost_race 并跳过 adjust_balance，钱到账、点数丢失。
    INSERT 仍走 `_write`（lastrowid 语义不变），避免动到取新 id 的调用点。
    """
    if conn is not None:
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        except Exception:
            conn.rollback()
            raise
    from db import get_connection
    c = get_connection()
    try:
        cur = c.execute(sql, params)
        c.commit()
        return cur.rowcount
    finally:
        c.close()


def new_out_trade_no(now=None):
    now = now or datetime.now()
    suffix = ''.join(random.choice(string.digits) for _ in range(6))
    return 'RC%s%s' % (now.strftime('%Y%m%d%H%M%S'), suffix)


def digest_of(raw):
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    return hashlib.sha256(raw or b'').hexdigest()


def validate_points(points):
    try:
        value = int(points)
    except (TypeError, ValueError):
        return False, None, '点数必须是整数'
    allowed = tiers()
    if value not in allowed:
        return False, None, '只支持这些充值档位：%s 点' % '/'.join(str(t) for t in allowed)
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


def create_order(out_trade_no, account_id, operator_id, points, amount_cents, conn=None,
                 channel='wxpay'):
    """建单。channel 决定后续用哪个渠道适配器（wxpay / alipay），默认 wxpay 保持兼容。"""
    channel = (channel or 'wxpay').strip() or 'wxpay'
    _write(conn,
           "INSERT INTO pay_orders (out_trade_no, channel, account_id, operator_id, amount_cents, points, status) "
           "VALUES (?, ?, ?, ?, ?, ?, 'created')",
           (out_trade_no, channel, account_id, operator_id, int(amount_cents), float(points)))
    return get_order(out_trade_no, conn=conn)



def list_orders(account_id=None, operator_id=None, owner_id=None, limit=20, conn=None):
    """最近订单（充值记录）。owner_id = 本人相关（account_id 或 operator_id）。"""
    try:
        limit = max(1, min(int(limit or 20), 100))
    except (TypeError, ValueError):
        limit = 20
    where = []
    params = []
    if owner_id is not None:
        where.append('(account_id = ? OR operator_id = ?)')
        params.extend([int(owner_id), int(owner_id)])
    else:
        if account_id is not None:
            where.append('account_id = ?')
            params.append(int(account_id))
        if operator_id is not None:
            where.append('operator_id = ?')
            params.append(int(operator_id))
    sql = ("SELECT out_trade_no, channel, account_id, operator_id, amount_cents, points, "
           "status, paid_at, credited_at, created_at FROM pay_orders")
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    rows = _fetch(conn, sql, tuple(params), one=False) or []
    return [dict(r) if hasattr(r, 'keys') else r for r in rows]

def get_order(out_trade_no, conn=None):
    return _fetch(conn, "SELECT * FROM pay_orders WHERE out_trade_no = ?", (out_trade_no,))


def set_code_url(out_trade_no, code_url, prepay_id=None, conn=None):
    return _write_affected(conn,
                  "UPDATE pay_orders SET code_url = ?, prepay_id = ?, updated_at = datetime('now') "
                  "WHERE out_trade_no = ?",
                  (code_url, prepay_id, out_trade_no))


def mark_paid(out_trade_no, transaction_id, notify_id=None, notify_digest=None,
              notify_raw=None, conn=None):
    return _write_affected(conn,
                  "UPDATE pay_orders SET status = 'paid', transaction_id = ?, notify_id = ?, "
                  "notify_digest = ?, notify_raw = ?, paid_at = datetime('now'), updated_at = datetime('now') "
                  "WHERE out_trade_no = ? AND status = 'created'",
                  (transaction_id, notify_id, notify_digest, notify_raw, out_trade_no))


def mark_paid_from_closed(out_trade_no, transaction_id, notify_id=None, notify_digest=None,
                          notify_raw=None, conn=None):
    """关单竞态收敛：把本地 `closed` 纠正为 `paid`（只为让入账链路继续）。

    只在支付宝明确 `TRADE_SUCCESS/TRADE_FINISHED` 且金额/交易号校验通过后由
    `alipay_notify` 调用；`WHERE status='closed'` 保证只迁一次（并发/重试安全）。
    """
    return _write_affected(conn,
                  "UPDATE pay_orders SET status = 'paid', transaction_id = ?, notify_id = ?, "
                  "notify_digest = ?, notify_raw = ?, paid_at = datetime('now'), "
                  "updated_at = datetime('now') WHERE out_trade_no = ? AND status = 'closed'",
                  (transaction_id, notify_id, notify_digest, notify_raw, out_trade_no))


def digest_seen(out_trade_no, notify_digest, conn=None):
    """同一条回调（原文相同）是否已处理过。"""
    row = _fetch(conn, "SELECT out_trade_no FROM pay_orders WHERE notify_digest = ?", (notify_digest,))
    return bool(row) and row['out_trade_no'] == out_trade_no


def close_order(out_trade_no, conn=None):
    return _write_affected(conn,
                  "UPDATE pay_orders SET status = 'closed', updated_at = datetime('now') "
                  "WHERE out_trade_no = ? AND status = 'created'",
                  (out_trade_no,))


def claim_credit(out_trade_no, conn=None):
    changed = _write_affected(conn,
                     "UPDATE pay_orders SET status = 'credited', credited_at = datetime('now'), "
                     "updated_at = datetime('now') WHERE out_trade_no = ? AND status = 'paid'",
                     (out_trade_no,))
    return int(changed or 0) == 1


def log_event(out_trade_no, event, detail='', conn=None):
    return _write(conn,
                  "INSERT INTO pay_events (out_trade_no, event, detail) VALUES (?, ?, ?)",
                  (out_trade_no, event, str(detail)[:2000]))


def credit_order(out_trade_no, conn=None, adjust=None):
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
    channel = (order.get('channel') or 'wxpay')
    label = CHANNEL_SHORT_LABELS.get(channel, channel)
    try:
        adjust(order['account_id'], points=order['points'], mode='add',
               reason='%s充值 %s 元' % (label, order['amount_cents'] / 100.0),
               operator='%s:%s' % (channel, out_trade_no))
    except Exception as exc:  # noqa: BLE001
        _write_affected(conn,
               "UPDATE pay_orders SET status = 'paid', credited_at = NULL, updated_at = datetime('now') "
               "WHERE out_trade_no = ? AND status = 'credited'",
               (out_trade_no,))
        log_event(out_trade_no, 'credit_fail', str(exc)[:300], conn=conn)
        return {'ok': False, 'reason': 'credit_failed', 'detail': str(exc)[:200]}
    log_event(out_trade_no, 'credit_ok', '%s points' % order['points'], conn=conn)
    return {'ok': True, 'reason': 'credited', 'points': order['points']}


def expire_created(conn=None, ttl_minutes=ORDER_TTL_MINUTES):
    cutoff = _utc_str((datetime.now() - timedelta(minutes=ttl_minutes)).astimezone())
    return _write_affected(conn,
                  "UPDATE pay_orders SET status = 'closed', updated_at = datetime('now') "
                  "WHERE status = 'created' AND created_at < ?",
                  (cutoff,))


def audit_credited_without_ledger(conn=None):
    """对账自检（只读）：已入账但**没有对应 ledger 流水**的订单。

    2026-11-08 事故的症状：`pay_orders.status='credited'`、`credited_at` 有值，
    但 `image_ops_log` 里没有 `alipay:<单号>` 那一行（`claim_credit` 因 rowcount
    误判成 lost_race，跳过了 `adjust_balance`）。这条自检用来把同类问题自动捞出来。
    """
    return _fetch(conn,
                  "SELECT o.out_trade_no, o.channel, o.points, o.amount_cents, o.credited_at "
                  "FROM pay_orders o LEFT JOIN image_ops_log l "
                  "ON l.operator = (o.channel || ':' || o.out_trade_no) "
                  "WHERE o.status = 'credited' AND l.id IS NULL ORDER BY o.id",
                  (), one=False) or []


def pending_credit(conn=None, limit=50):
    """已支付但未入账（含入账失败的），给对账脚本补。"""
    return _fetch(conn,
                  "SELECT out_trade_no, account_id, points, amount_cents, status FROM pay_orders "
                  "WHERE status = 'paid' ORDER BY paid_at ASC LIMIT ?",
                  (limit,), one=False) or []


def stale_created(channel='alipay', older_than_seconds=120, limit=50, conn=None, now_ts=None):
    """建单超过 N 秒仍是 created 的订单（回调可能丢），给对账脚本主动查单。"""
    now_ts = now_ts if now_ts is not None else time.time()
    cutoff = _utc_str(datetime.fromtimestamp(now_ts - older_than_seconds).astimezone())
    return _fetch(conn,
                  "SELECT * FROM pay_orders WHERE status = 'created' AND channel = ? "
                  "AND created_at < ? ORDER BY created_at ASC LIMIT ?",
                  (channel, cutoff, limit), one=False) or []


def stats(conn=None):
    rows = _fetch(conn, "SELECT status, COUNT(*) AS c FROM pay_orders GROUP BY status", (), one=False) or []
    return {row['status']: row['c'] for row in rows}


# ---------------- 退款（M3 占位：只落单与状态，不调渠道接口） ----------------

REFUND_STATUSES = ('created', 'succeeded', 'failed')


def new_out_request_no(now=None):
    """退款幂等键（我方生成，渠道用它去重）：RF + 时间戳 + 6 位随机。"""
    now = now or datetime.now()
    suffix = ''.join(random.choice(string.digits) for _ in range(6))
    return 'RF%s%s' % (now.strftime('%Y%m%d%H%M%S'), suffix)


def create_refund(out_trade_no, amount_cents, reason='', channel=None,
                  out_request_no=None, conn=None):
    """登记一笔退款（占位）。真实退款调用在 M3 接入渠道退款接口。

    校验：订单必须存在且已支付；退款金额 > 0 且不超过订单金额。
    幂等：out_request_no 唯一，重复登记抛异常（唯一索引）。
    """
    order = get_order(out_trade_no, conn=conn)
    if not order:
        raise ValueError('订单不存在')
    if order['status'] not in ('paid', 'credited'):
        raise ValueError('订单未支付，不能退款：%s' % order['status'])
    try:
        amount = int(amount_cents)
    except (TypeError, ValueError):
        raise ValueError('退款金额不合法')
    if amount <= 0:
        raise ValueError('退款金额必须大于 0')
    if amount > int(order['amount_cents']):
        raise ValueError('退款金额超过订单金额')
    out_request_no = (out_request_no or new_out_request_no()).strip()
    if not out_request_no:
        raise ValueError('退款幂等键不能为空')
    _write(conn,
           "INSERT INTO pay_refunds (out_trade_no, channel, out_request_no, amount_cents, reason, status) "
           "VALUES (?, ?, ?, ?, ?, 'created')",
           (out_trade_no, channel or order.get('channel') or 'wxpay',
            out_request_no, amount, str(reason or '')[:200]))
    log_event(out_trade_no, 'refund_created', out_request_no, conn=conn)
    return get_refund(out_request_no, conn=conn)


def get_refund(out_request_no, conn=None):
    return _fetch(conn, "SELECT * FROM pay_refunds WHERE out_request_no = ?", (out_request_no,))


def list_refunds(out_trade_no, conn=None):
    return _fetch(conn,
                  "SELECT * FROM pay_refunds WHERE out_trade_no = ? ORDER BY id DESC",
                  (out_trade_no,), one=False) or []


def mark_refund(out_request_no, status, refund_no=None, trade_no=None,
                notify_digest=None, conn=None):
    """更新退款状态（M3 由渠道退款回调/查单调用）。"""
    if status not in REFUND_STATUSES:
        raise ValueError('非法退款状态：%s' % status)
    return _write_affected(conn,
                  "UPDATE pay_refunds SET status = ?, refund_no = COALESCE(?, refund_no), "
                  "trade_no = COALESCE(?, trade_no), notify_digest = COALESCE(?, notify_digest), "
                  "updated_at = datetime('now') WHERE out_request_no = ?",
                  (status, refund_no, trade_no, notify_digest, out_request_no))
