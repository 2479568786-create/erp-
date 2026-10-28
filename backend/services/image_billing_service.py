"""生图计费服务：按账号扣点、余额校验、充值、生图开关。

口径（2026-10-27 用户确认）
--------------------------
- 1 点 = 1 元人民币。
- 余额以小单位「分」落库（``accounts.image_balance_cents``，100 分 = 1 点），
  因为单张图片的单价通常不足 1 点，用整数「点」记账会被抹零。
- 扣点发生在**生图成功之后**（图已经生成、第三方已经计费）；入口用
  ``can_generate(account, images)`` 按「本次张数 × 单价」预检，正常路径不会扣成负数
  （只有并发请求或中途改单价才可能出现微小负数）。
- admin 角色不受开关与余额限制（用户裁定：id1 涂懿榆永远可生图），
  但 success 后仍然写一行 usage，cost 记 0，方便审计对账。
- 历史已生图（改造上线前）不计入余额，余额从本功能上线后开始扣。
"""
import math
from datetime import date, datetime, timedelta

from db import execute, query
from services.ai_config_service import _load_config, _save_config

ADMIN_ROLE = 'admin'
DEFAULT_PRICE_POINTS = 0.0
MAX_PRICE_POINTS = 1000.0
MAX_SINGLE_ADJUST_POINTS = 100000.0
MAX_BALANCE_POINTS = 100000.0


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _today():
    return date.today().isoformat()


def _days_ago(days):
    return (date.today() - timedelta(days=int(days))).isoformat()


def points_to_cents(points):
    """点 → 分（0.01 点），四舍五入（half-up），0.005 点按 1 分计。"""
    return int(math.floor(float(points) * 100 + 0.5 + 1e-9))


def cents_to_points(cents):
    """分 → 点（保留 2 位小数）。"""
    return round(int(cents or 0) / 100.0, 2)


# ------------------------------------------------------------------ 单价配置

def _price_map():
    """按模型单价表（0.37 / 0.2 ...），只保留合法值。"""
    raw = (_load_config().get('image_billing') or {}).get('price_points_by_model') or {}
    out = {}
    for key, value in raw.items():
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= price <= MAX_PRICE_POINTS:
            out[str(key)] = round(price, 4)
    return out


def price_for_model(model=None):
    """按模型取单价；模型没有单独定价时回退全局单价。"""
    model = str(model or '').strip()
    prices = _price_map()
    if model and model in prices:
        return prices[model]
    return get_billing_config()['price_points_per_image']


def get_billing_config():
    """读取生图计费配置（存放在 backend/data/ai_models.json → image_billing）。"""
    raw = (_load_config().get('image_billing') or {})
    try:
        price = float(raw.get('price_points_per_image'))
    except (TypeError, ValueError):
        price = DEFAULT_PRICE_POINTS
    if price < 0 or price > MAX_PRICE_POINTS:
        price = DEFAULT_PRICE_POINTS
    return {
        'price_points_per_image': round(price, 4),
        'price_points_by_model': _price_map(),
        'currency': raw.get('currency') or 'CNY',
        'unit': '1点=1元',
        'updated_at': raw.get('updated_at') or '',
    }


def set_billing_config(price_points_per_image=None, currency=None):
    """修改单张图片单价（点）。单价为 0 时不扣点（配置就绪前的安全默认值）。"""
    current = get_billing_config()
    price = current['price_points_per_image'] if price_points_per_image is None else float(price_points_per_image)
    if price < 0 or price > MAX_PRICE_POINTS:
        raise ValueError('单张图片点数必须在 0 到 %g 之间' % MAX_PRICE_POINTS)
    config = _load_config()
    billing = config.setdefault('image_billing', {})
    billing['price_points_per_image'] = round(price, 4)
    billing['currency'] = currency or billing.get('currency') or 'CNY'
    billing['updated_at'] = _now()
    _save_config(config)
    return get_billing_config()


# ------------------------------------------------------------------ 账号余额

_ACCOUNT_COLUMNS = (
    'id, username, nickname, role, status, image_enabled, image_balance_cents'
)


def _account_row(account_id):
    if isinstance(account_id, dict):
        return account_id if 'image_balance_cents' in account_id else query(
            'SELECT %s FROM accounts WHERE id = ?' % _ACCOUNT_COLUMNS,
            (account_id.get('id'),), one=True,
        )
    return query('SELECT %s FROM accounts WHERE id = ?' % _ACCOUNT_COLUMNS, (account_id,), one=True)


def _enabled(account):
    value = account.get('image_enabled')
    if value is None:
        return True
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


def _balance_cents(account):
    try:
        return int(account.get('image_balance_cents') or 0)
    except (TypeError, ValueError):
        return 0


def _usage_sum(account_id=None, since=None):
    sql = ("SELECT COALESCE(SUM(images), 0) AS images, "
           "COALESCE(SUM(cost_points), 0) AS points, "
           "COALESCE(SUM(cost_cents), 0) AS cents, COUNT(*) AS requests "
           "FROM image_usage WHERE 1 = 1")
    params = []
    if account_id is not None:
        sql += ' AND account_id = ?'
        params.append(int(account_id))
    if since:
        sql += ' AND date(created_at) >= date(?)'
        params.append(since)
    row = query(sql, params, one=True) or {}
    return {
        'images': int(row.get('images') or 0),
        'cost_points': round(float(row.get('points') or 0), 2),
        'cost_cents': int(row.get('cents') or 0),
        'requests': int(row.get('requests') or 0),
    }


def account_billing(account_id):
    """单账号计费快照：余额、开关、今日/近 7 天/近 30 天/累计消耗。"""
    row = _account_row(account_id)
    if not row:
        return None
    cents = _balance_cents(row)
    role = row.get('role') or ''
    return {
        'account_id': row.get('id'),
        'username': row.get('username'),
        'nickname': row.get('nickname') or '',
        'role': role,
        'status': row.get('status') or '',
        'image_enabled': _enabled(row),
        'image_balance': cents_to_points(cents),
        'image_balance_cents': cents,
        'balance_unit': '点（1点=1元）',
        'admin_exempt': role == ADMIN_ROLE,
        'spent_today': _usage_sum(row.get('id'), _today()),
        'spent_7d': _usage_sum(row.get('id'), _days_ago(6)),
        'spent_30d': _usage_sum(row.get('id'), _days_ago(29)),
        'spent_total': _usage_sum(row.get('id')),
    }


def list_accounts_billing():
    rows = query('SELECT %s FROM accounts ORDER BY id' % _ACCOUNT_COLUMNS)
    return [account_billing(row) for row in rows]


# ------------------------------------------------------------------ 生图闸门

def can_generate(account, images=1, model=None):
    """生图前校验，返回 (allowed, reason)。

    ``images`` 是本次请求要生成的张数（ERP 单次最多 4 张）：必须按
    「张数 × 单价」预检余额，否则会出现「余额 0.1 点却一次出 4 张」被扣成负数。
    admin 永远放行。
    """
    if not account:
        return False, '请先登录'
    account = _account_row(account) or account
    if account.get('role') == ADMIN_ROLE:
        return True, ''
    if not _enabled(account):
        return False, '生图功能已停用：该账号当前不允许生成图片，请联系管理员'
    balance_cents = _balance_cents(account)
    if balance_cents <= 0:
        return False, '生图余额不足，请联系管理员充值后再试'
    try:
        images = max(int(images or 1), 1)
    except (TypeError, ValueError):
        images = 1
    price = price_for_model(model)
    required_cents = points_to_cents(price * images) if price > 0 else 0
    if required_cents > 0 and balance_cents < required_cents:
        return False, ('生图余额不足：本次 %d 张需 %s 点，当前余额 %s 点，'
                       '请联系管理员充值后再试'
                       % (images, cents_to_points(required_cents),
                          cents_to_points(balance_cents)))
    return True, ''


# ------------------------------------------------------------------ 记一笔

def charge(account_id, images, provider='', model='', source='ai'):
    """生图成功后扣点并记账。返回本次扣费明细（余额不足时余额可能变负）。"""
    try:
        images = int(images or 0)
    except (TypeError, ValueError):
        images = 0
    if images <= 0:
        return {'images': 0, 'cost_points': 0.0, 'cost_cents': 0,
                'status': 'skipped', 'balance_after': None}
    row = _account_row(account_id)
    if not row:
        return {'images': images, 'cost_points': 0.0, 'cost_cents': 0,
                'status': 'account_missing', 'balance_after': None}

    price = price_for_model(model)
    if row.get('role') == ADMIN_ROLE:
        cost_cents, status = 0, 'admin_exempt'
    else:
        cost_cents = points_to_cents(price * images)
        status = 'charged' if cost_cents > 0 else 'no_price'
    cost_points = cents_to_points(cost_cents)

    if cost_cents:
        execute(
            'UPDATE accounts SET image_balance_cents = image_balance_cents - ?, updated_at = ? WHERE id = ?',
            (cost_cents, _now(), row.get('id')),
        )
    after = _account_row(row.get('id')) or row
    balance_after = _balance_cents(after)
    execute(
        """INSERT INTO image_usage
           (account_id, images, cost_points, cost_cents, balance_after_cents,
            price_points, provider, model, source, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (row.get('id'), images, cost_points, cost_cents, balance_after,
         price, str(provider or ''), str(model or ''), str(source or ''),
         status, _now()),
    )
    return {
        'images': images,
        'cost_points': cost_points,
        'cost_cents': cost_cents,
        'price_points_per_image': price,
        'status': status,
        'balance_after': cents_to_points(balance_after),
        'balance_after_cents': balance_after,
    }


# ------------------------------------------------------------------ 充值与开关

def _write_ops_log(account_id, action, before, after, operator, reason, amount_cents=0):
    import json
    execute(
        """INSERT INTO image_ops_log
           (account_id, action, amount_cents, before_json, after_json, operator, reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (account_id, action, int(amount_cents or 0),
         json.dumps(before, ensure_ascii=False),
         json.dumps(after, ensure_ascii=False),
         str(operator or ''), str(reason or ''), _now()),
    )


def adjust_balance(account_id, points=None, mode='add', reason='', operator=''):
    """充值/设置余额。mode=add 在现有余额上加（可为负以纠错），mode=set 直接设为该值。"""
    row = _account_row(account_id)
    if not row:
        raise ValueError('账号不存在')
    if points is None:
        raise ValueError('缺少 points 参数')
    try:
        points = float(points)
    except (TypeError, ValueError):
        raise ValueError('points 必须是数字')
    if abs(points) > MAX_SINGLE_ADJUST_POINTS:
        raise ValueError('单次调整不能超过 %g 点' % MAX_SINGLE_ADJUST_POINTS)
    mode = (mode or 'add').strip().lower()
    if mode not in ('add', 'set'):
        raise ValueError('mode 只支持 add / set')
    before_cents = _balance_cents(row)
    delta_cents = points_to_cents(points)
    after_cents = before_cents + delta_cents if mode == 'add' else delta_cents
    if after_cents < 0:
        raise ValueError('调整后余额不能为负数')
    if cents_to_points(after_cents) > MAX_BALANCE_POINTS:
        raise ValueError('余额上限 %g 点' % MAX_BALANCE_POINTS)
    execute('UPDATE accounts SET image_balance_cents = ?, updated_at = ? WHERE id = ?',
            (after_cents, _now(), row.get('id')))
    _write_ops_log(
        row.get('id'), 'balance_' + mode,
        {'balance_points': cents_to_points(before_cents), 'balance_cents': before_cents},
        {'balance_points': cents_to_points(after_cents), 'balance_cents': after_cents,
         'delta_points': cents_to_points(delta_cents)},
        operator, reason,
        amount_cents=delta_cents,
    )
    return account_billing(row.get('id'))


def topup(account_id, points, reason='', operator=''):
    """管理员给账号充值点数：只接受正数，写一条充值流水。

    与 adjust_balance(mode='add') 的区别：这里拒绝 0/负数，专门用于「加点数」入口，
    流水里 amount_cents 恒为正，方便对账。
    """
    try:
        points = float(points)
    except (TypeError, ValueError):
        raise ValueError('点数必须是数字')
    if points <= 0:
        raise ValueError('充值点数必须大于 0')
    return adjust_balance(account_id, points=points, mode='add',
                          reason=reason or '管理员充值', operator=operator)


def topup_logs(account_id=None, limit=100):
    """充值流水（image_ops_log 里 action=balance_add 且加点为正的记录）。"""
    limit = max(1, min(int(limit or 100), 500))
    sql = ('SELECT id, account_id, amount_cents, after_json, operator, reason, created_at '
           "FROM image_ops_log WHERE action = 'balance_add' AND amount_cents > 0")
    params = []
    if account_id is not None:
        sql += ' AND account_id = ?'
        params.append(int(account_id))
    sql += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    import json
    rows = query(sql, params)
    result = []
    for row in rows:
        try:
            after = json.loads(row.get('after_json') or '{}')
        except (TypeError, ValueError):
            after = {}
        result.append({
            'id': row['id'],
            'account_id': row['account_id'],
            'points': cents_to_points(row.get('amount_cents')),
            'balance_after': after.get('balance_points'),
            'operator': row.get('operator') or '',
            'reason': row.get('reason') or '',
            'created_at': row.get('created_at') or '',
        })
    return result


def set_enabled(account_id, enabled, operator='', reason=''):
    """打开/关闭单个账号的生图能力。"""
    row = _account_row(account_id)
    if not row:
        raise ValueError('账号不存在')
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ('1', 'true', 'yes', 'on', 'enable', 'enabled')
    value = 1 if enabled else 0
    before = _enabled(row)
    execute('UPDATE accounts SET image_enabled = ?, updated_at = ? WHERE id = ?',
            (value, _now(), row.get('id')))
    _write_ops_log(row.get('id'), 'enabled' if value else 'disabled',
                   {'image_enabled': before}, {'image_enabled': bool(value)},
                   operator, reason)
    return account_billing(row.get('id'))


# ------------------------------------------------------------------ 用量查询

def usage_by_day(account_id=None, days=30, date_from=None, date_to=None):
    """按「天 × 账号」聚合用量。"""
    sql = ("SELECT account_id, date(created_at) AS day, SUM(images) AS images, "
           "ROUND(SUM(cost_points), 2) AS cost_points, COUNT(*) AS requests "
           "FROM image_usage WHERE 1 = 1")
    params = []
    if account_id is not None:
        sql += ' AND account_id = ?'
        params.append(int(account_id))
    if date_from:
        sql += ' AND date(created_at) >= date(?)'
        params.append(date_from)
    if date_to:
        sql += ' AND date(created_at) <= date(?)'
        params.append(date_to)
    elif not date_from:
        sql += ' AND date(created_at) >= date(?)'
        params.append(_days_ago(max(int(days or 30), 1) - 1))
    sql += ' GROUP BY account_id, day ORDER BY day DESC, account_id'
    rows = query(sql, params)
    return [{
        'account_id': row['account_id'],
        'day': row['day'],
        'images': int(row.get('images') or 0),
        'cost_points': round(float(row.get('cost_points') or 0), 2),
        'requests': int(row.get('requests') or 0),
    } for row in rows]


def usage_logs(account_id=None, limit=100, since=None):
    """原始扣点明细（每张图/每次请求一行）。"""
    limit = max(1, min(int(limit or 100), 500))
    sql = ('SELECT id, account_id, images, cost_points, cost_cents, balance_after_cents, '
           'price_points, provider, model, source, status, created_at '
           'FROM image_usage WHERE 1 = 1')
    params = []
    if account_id is not None:
        sql += ' AND account_id = ?'
        params.append(int(account_id))
    if since:
        sql += ' AND date(created_at) >= date(?)'
        params.append(since)
    sql += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    rows = query(sql, params)
    for row in rows:
        row['cost_points'] = round(float(row.get('cost_points') or 0), 2)
        row['balance_after'] = cents_to_points(row.get('balance_after_cents'))
    return rows


def ops_logs(account_id=None, limit=100):
    limit = max(1, min(int(limit or 100), 500))
    sql = ('SELECT id, account_id, action, amount_cents, before_json, after_json, operator, reason, created_at '
           'FROM image_ops_log WHERE 1 = 1')
    params = []
    if account_id is not None:
        sql += ' AND account_id = ?'
        params.append(int(account_id))
    sql += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    rows = query(sql, params)
    for row in rows:
        row['amount_points'] = cents_to_points(row.get('amount_cents'))
    return rows
