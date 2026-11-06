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
import os
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


def _topup_net_map():
    """一次算出各账号充值净额（balance_add 正负相抵），供 /accounts 逐账号自证用。"""
    rows = query('SELECT account_id, '
                 'COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END), 0) AS p, '
                 'COALESCE(SUM(CASE WHEN amount_cents < 0 THEN amount_cents ELSE 0 END), 0) AS r '
                 "FROM image_ops_log WHERE action = 'balance_add' AND amount_cents != 0 "
                 'GROUP BY account_id', one=False)
    return {int(x['account_id']): round(cents_to_points(int(x['p']) + int(x['r'])), 2)
            for x in rows}


def account_billing(account_id, net_map=None):
    """单账号计费快照：余额、开关、今日/近 7 天/近 30 天/累计消耗。

    另给账本自证四件套（只读，配合 `/topups` 顶层同名字段）：
    `initial_grant`（env 配置的历史初始额度，未配置为 None）、`total_points`（充值净额）、
    `usage_points`（累计扣点）、`balance_points`（当前余额，与 `image_balance` 同值，便于直接对等式）。
    自证等式：`initial_grant + total_points - usage_points == balance_points`。
    """
    row = _account_row(account_id)
    if not row:
        return None
    cents = _balance_cents(row)
    role = row.get('role') or ''
    account_id_int = int(row.get('id'))
    if net_map is None:
        net_map = _topup_net_map()
    grants = initial_grants()
    spent_total = _usage_sum(row.get('id'))
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
        'spent_total': spent_total,
        # —— 账本自证四件套（只加不删；initial_grant 未配置时为 None）——
        'initial_grant': (round(grants[account_id_int], 2) if account_id_int in grants else None),
        'total_points': float(net_map.get(account_id_int, 0.0)),
        'usage_points': round(float(spent_total.get('cost_points') or 0), 2),
        'balance_points': cents_to_points(cents),
    }


def list_accounts_billing():
    rows = query('SELECT %s FROM accounts ORDER BY id' % _ACCOUNT_COLUMNS)
    net_map = _topup_net_map()
    return [account_billing(row, net_map=net_map) for row in rows]


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


def _kind_of(action, cents):
    """流水行类别：topup（充值）/ reversal（冲销·退款）/ correction（修正）/ set（覆盖式调整）。"""
    if action == 'billing_correction':
        return 'correction'
    if action == 'balance_set':
        return 'set'
    return 'reversal' if cents < 0 else 'topup'


def topup_logs(account_id=None, limit=100, include_adjustments=False):
    """充值流水（默认只含 action=balance_add 且金额非 0 的记录）。

    - points > 0 是真实充值/加点；points < 0 是冲销/退款，`is_reversal=True`，`kind` 为 reversal；
    - `include_adjustments=True` 时额外带出 `billing_correction` / `balance_set`
      （余额调整、不是充值），这些行 `kind` 为 correction / set，不进充值净额；
    - 每行都带 `kind`，消费方按 `kind` / `is_reversal` 分类，不靠 reason 文字约定。
    """
    limit = max(1, min(int(limit or 100), 500))
    import json

    def _row_dict(row):
        try:
            after = json.loads(row.get('after_json') or '{}')
        except (TypeError, ValueError):
            after = {}
        cents = int(row.get('amount_cents') or 0)
        action = row.get('action') or 'balance_add'
        return {
            'id': row['id'],
            'account_id': row['account_id'],
            'points': cents_to_points(cents),
            'is_reversal': cents < 0,
            'kind': _kind_of(action, cents),
            'balance_after': after.get('balance_points'),
            'operator': row.get('operator') or '',
            'reason': row.get('reason') or '',
            'created_at': row.get('created_at') or '',
        }

    sel = ('SELECT id, account_id, action, amount_cents, after_json, operator, reason, created_at '
           'FROM image_ops_log WHERE ')
    params = []
    sql = sel + "action = 'balance_add' AND amount_cents != 0"
    if account_id is not None:
        sql += ' AND account_id = ?'
        params.append(int(account_id))
    sql += ' ORDER BY id DESC LIMIT ?'
    rows = query(sql, tuple(params) + (limit,))

    if include_adjustments:
        params2 = []
        sql2 = sel + "action IN ('billing_correction', 'balance_set') AND amount_cents != 0"
        if account_id is not None:
            sql2 += ' AND account_id = ?'
            params2.append(int(account_id))
        sql2 += ' ORDER BY id DESC LIMIT ?'
        rows = list(rows) + list(query(sql2, tuple(params2) + (limit,)))
        rows.sort(key=lambda r: r['id'], reverse=True)
        rows = rows[:limit]

    return [_row_dict(r) for r in rows]



def initial_grants():
    """初始额度映射（历史迁移直写进 accounts，没有任何流水行）。

    来源：env `IMAGE_BILLING_INITIAL_GRANTS`，格式 `8:40,7:13`（account_id:点数）。
    未配置返回空 dict，此时 `topup_summary()['initial_grant']` 为 None。
    """
    raw = (os.environ.get('IMAGE_BILLING_INITIAL_GRANTS') or '').strip()
    out = {}
    if not raw:
        return out
    for part in raw.split(','):
        part = part.strip()
        if not part or ':' not in part:
            continue
        key, value = part.split(':', 1)
        try:
            out[int(key.strip())] = float(value.strip())
        except (TypeError, ValueError):
            continue
    return out
def topup_summary(account_id=None):
    """全量汇总（SQL 聚合，**不受 list 的 limit 截断**）。

    - `total_points` 净额 / `total_positive_points` 正向累计 / `reversal_points` 冲销累计：
      仍只算 `balance_add`，语义不变；
    - `correction_points`：`billing_correction` 合计（如多扣退回、误覆盖补回）；
    - `adjustment_points`：`balance_set` 覆盖式调整合计（当前线上为 0）；
    - `initial_grant`：该账号初始额度（历史迁移直写，来源 env `IMAGE_BILLING_INITIAL_GRANTS`；
      未配置为 None），配合 `usage_points` / `balance_points` 自证
      `initial_grant + total_points − usage_points = balance_points`；
    - `net_balance_delta` = 三者相加（充值净额 + 修正 + 覆盖调整），是**账面净变化**，
      **不等于余额差**：`billing_correction` 是否真的改了 accounts 余额取决于写入方式。
      线上 11.11 那两笔只有台账记录、没落到余额（等式 `初始额度 + 充值 − image_usage 累计 = 余额`
      只有不含修正时才精确成立）。算「已消耗」请用 image_usage 累计，或
      `初始额度 + total_points − 余额`；不要用 `net_balance_delta − 余额`。
    """
    params = []
    where = "action = 'balance_add' AND amount_cents != 0"
    if account_id is not None:
        where += ' AND account_id = ?'
        params.append(int(account_id))
    row = query('SELECT '
                'COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END), 0) AS p, '
                'COALESCE(SUM(CASE WHEN amount_cents < 0 THEN amount_cents ELSE 0 END), 0) AS r '
                'FROM image_ops_log WHERE ' + where, tuple(params), one=True) or {}

    params2 = []
    where2 = "action IN ('billing_correction', 'balance_set') AND amount_cents != 0"
    if account_id is not None:
        where2 += ' AND account_id = ?'
        params2.append(int(account_id))
    adj = query("SELECT "
                "COALESCE(SUM(CASE WHEN action = 'billing_correction' THEN amount_cents ELSE 0 END), 0) AS c, "
                "COALESCE(SUM(CASE WHEN action = 'balance_set' THEN amount_cents ELSE 0 END), 0) AS s "
                "FROM image_ops_log WHERE " + where2, tuple(params2), one=True) or {}

    # 用量与余额快照（只读，用于账本自证：初始额度 + total_points − usage_points = balance_points）
    params3 = []
    where3 = '1 = 1'
    if account_id is not None:
        where3 += ' AND account_id = ?'
        params3.append(int(account_id))
    use = query('SELECT COALESCE(SUM(cost_points), 0) AS p, COUNT(*) AS n '
                'FROM image_usage WHERE ' + where3, tuple(params3), one=True) or {}
    if account_id is not None:
        bal = query('SELECT COALESCE(image_balance_cents, 0) AS b FROM accounts WHERE id = ?',
                    (int(account_id),), one=True) or {}
    else:
        bal = query('SELECT COALESCE(SUM(image_balance_cents), 0) AS b FROM accounts', one=True) or {}

    grants = initial_grants()
    if account_id is not None:
        grant = grants.get(int(account_id))
        grant = round(grant, 2) if grant is not None else None
    else:
        grant = round(sum(grants.values()), 2) if grants else None

    positive = int(row.get('p') or 0)
    reversal = int(row.get('r') or 0)
    correction = int(adj.get('c') or 0)
    adjustment = int(adj.get('s') or 0)
    net = positive + reversal
    return {
        'total_points': round(cents_to_points(net), 2),
        'total_positive_points': round(cents_to_points(positive), 2),
        'reversal_points': round(cents_to_points(reversal), 2),
        'correction_points': round(cents_to_points(correction), 2),
        'adjustment_points': round(cents_to_points(adjustment), 2),
        'net_balance_delta': round(cents_to_points(net + correction + adjustment), 2),
        'usage_points': round(float(use.get('p') or 0), 2),
        'usage_images': int(use.get('n') or 0),
        'balance_points': round(cents_to_points(int(bal.get('b') or 0)), 2),
        'initial_grant': grant,
    }


def topup_totals(rows):
    """充值流水汇总：`total_points` 为净额（正值+负值），另给正向累计与冲销累计。"""
    positive = 0.0
    reversal = 0.0
    for row in rows or []:
        points = float(row.get('points') or 0)
        if points > 0:
            positive += points
        elif points < 0:
            reversal += points
    return {
        'total_points': round(positive + reversal, 2),
        'total_positive_points': round(positive, 2),
        'reversal_points': round(reversal, 2),
    }


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
