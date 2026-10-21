# -*- coding: utf-8 -*-
"""
店群管理 API 路由 —— 业务员与店铺的绑定 / 解绑 / 总览 / 审计

【模块定位】
面向 5~7 人小团队的「店群管理模式」：由一名管理员统一持有全部 Ozon 店铺，
再把店铺按「一人多店」的方式分配给各业务员。业务员登录后只能看到自己
名下店铺的商品、订单、财务与店铺资料。

本模块是既有 `PUT /api/stores/owner`（单店绑定）的**批量增强版**，
两者共用同一份数据真相（stores.account_id），可混用。

【权限】
本模块全部接口仅管理员可用（require_admin）。

【接口一览】
  GET    /api/store-assignment/overview        店群总览（成员×店铺矩阵 + 未分配店铺）
  GET    /api/store-assignment/members         可分配成员（非管理员账号）
  GET    /api/users/<uid>/stores               某成员名下的店铺列表
  POST   /api/store-assignment/bind            批量绑定（支持按店铺 / 按分组 / 按店铺ID）
  POST   /api/store-assignment/unbind          批量解绑
  GET    /api/store-assignment/logs            绑定变更审计日志

【绑定语义】
- 店铺归属写在 stores.account_id：NULL = 公共店铺（仅管理员可见）。
- 把已归属他人的店铺绑给新业务员 = 转移（自动记录 transfer 审计）。
- 一个店铺同一时刻只能属于一个业务员（1:N，而非多对多）。
"""
from flask import Blueprint, request

from db import query, execute, execute_many
from models.account import Account, Store
from utils.auth import require_admin, get_current_user
from utils.response import success_response, error_response, handle_errors

store_assign_bp = Blueprint('store_assign', __name__)

# 绑定审计表：惰性建表，兼容任意加载顺序
try:
    execute("""CREATE TABLE IF NOT EXISTS store_binding_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        store_pk        INTEGER NOT NULL,
        store_id        TEXT    NOT NULL DEFAULT '',
        store_alias     TEXT    NOT NULL DEFAULT '',
        action          TEXT    NOT NULL,
        from_account_id INTEGER,
        to_account_id   INTEGER,
        operator_id     INTEGER,
        operator_name   TEXT    NOT NULL DEFAULT '',
        remark          TEXT    NOT NULL DEFAULT '',
        created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    )""")
    execute("CREATE INDEX IF NOT EXISTS idx_bindlog_store ON store_binding_log(store_pk)")
    execute("CREATE INDEX IF NOT EXISTS idx_bindlog_created ON store_binding_log(created_at)")
except Exception as _e:  # noqa: BLE001 - 建表失败不应阻断服务启动
    print(f'[店群管理] store_binding_log 建表异常: {_e}')


# ============================================================================
# 内部工具
# ============================================================================

def _store_brief(s):
    """店铺列表项的统一精简结构（供前端总览/绑定面板使用）"""
    return {
        'id': s['id'],
        'storeId': s.get('store_id', ''),
        'alias': s.get('alias', ''),
        'group': s.get('store_group', ''),
        'currency': s.get('currency', 'RUB'),
        'authStatus': s.get('auth_status', 'pending'),
        'accountId': s.get('account_id'),
        'todayLimit': s.get('today_limit', 0),
    }


def _account_map():
    """account_id -> 账号行"""
    return {r['id']: r for r in query("SELECT * FROM accounts")}


def _resolve_target_stores(payload):
    """按入参解析出待处理的店铺行列表。

    支持三种定位方式（可组合，结果去重）：
      storePks  : [主键, ...]
      storeIds  : ['Ozon 店铺ID', ...]
      group     : '分组名'（该分组下全部店铺）
      all       : true（全部店铺）

    Returns:
        (stores, error_msg)：stores 为 list[dict]，error_msg 非空表示参数有误
    """
    conditions = []
    params = []

    pks = payload.get('storePks') or []
    sids = payload.get('storeIds') or []
    group = (payload.get('group') or '').strip()
    take_all = bool(payload.get('all'))

    if not (pks or sids or group or take_all):
        return None, "请至少指定一个店铺、店铺分组或 storeIds"

    if pks:
        try:
            pks = [int(p) for p in pks]
        except (TypeError, ValueError):
            return None, "storePks 必须为整数主键列表"
        conditions.append("id IN (%s)" % ','.join('?' * len(pks)))
        params.extend(pks)
    if sids:
        conditions.append("store_id IN (%s)" % ','.join('?' * len(sids)))
        params.extend([str(x) for x in sids])
    if group:
        conditions.append("store_group = ?")
        params.append(group)

    if take_all and not conditions:
        return query("SELECT * FROM stores ORDER BY id"), None

    sql = "SELECT * FROM stores WHERE " + " OR ".join(conditions) + " ORDER BY id"
    return query(sql, params), None


def _write_log(rows, operator, remark='', default_action='bind'):
    """写绑定审计日志。

    Args:
        rows: [(store, from_account_id, to_account_id[, action]), ...]
              action 省略时使用 default_action。
        operator: 操作人账号行（来自 get_current_user）
        remark: 备注
        default_action: 'bind' | 'unbind' | 'transfer'
    """
    if not rows:
        return
    op_id = operator['id'] if operator else None
    op_name = (operator.get('nickname') or operator.get('username')) if operator else ''
    payload = []
    for row in rows:
        s, frm, to = row[0], row[1], row[2]
        action = row[3] if len(row) > 3 else default_action
        payload.append((s['id'], s.get('store_id', ''), s.get('alias', ''),
                        action, frm, to, op_id, op_name, remark))
    execute_many(
        """INSERT INTO store_binding_log
           (store_pk, store_id, store_alias, action, from_account_id,
            to_account_id, operator_id, operator_name, remark)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        payload,
    )


def _validate_assignee(account_id):
    """校验目标成员可被分配店铺。

    Returns:
        (account_row, error_msg)
    """
    if account_id in (None, '', 0):
        return None, None  # None 表示解绑，合法
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return None, "accountId 必须为整数"
    target = Account.find_by_id(account_id)
    if not target:
        return None, "业务员账号不存在"
    if target.get('role') == 'admin':
        return None, "不能把店铺绑定给管理员（管理员天然可见全部店铺）"
    if target.get('status') == 'disabled':
        return None, f"账号 {target['username']} 已禁用，请先启用再分配店铺"
    return target, None


# ============================================================================
# 1. 店群总览
# ============================================================================

@store_assign_bp.route('/store-assignment/overview', methods=['GET'])
@handle_errors
@require_admin
def assignment_overview():
    """店群总览：成员 × 店铺矩阵 + 未分配店铺 + 分组归属统计。

    前端「用户管理」页的看板数据源，一次请求拿全，避免多次往返。
    """
    accounts = query("SELECT * FROM accounts ORDER BY role, id")
    stores = query("SELECT * FROM stores ORDER BY id")

    by_owner = {}
    for s in stores:
        by_owner.setdefault(s.get('account_id'), []).append(s)

    members = []
    for a in accounts:
        if a.get('role') == 'admin':
            continue  # 管理员天然可见全部，不参与分配
        owned = by_owner.get(a['id'], [])
        members.append({
            'id': a['id'],
            'username': a['username'],
            'nickname': a.get('nickname', ''),
            'role': a.get('role', 'operator'),
            'status': a.get('status', 'active'),
            'lastLoginAt': a.get('last_login_at', ''),
            'storeCount': len(owned),
            'groups': sorted({s.get('store_group', '') for s in owned if s.get('store_group')}),
            'stores': [_store_brief(s) for s in owned],
        })

    # 未分配（公共）店铺
    unassigned = [_store_brief(s) for s in by_owner.get(None, [])]

    # 分组统计：该分组下店铺数 / 已绑定数 / 归属人
    group_stats = {}
    amap = _account_map()
    for s in stores:
        g = s.get('store_group') or '默认'
        item = group_stats.setdefault(g, {
            'name': g, 'storeCount': 0, 'boundCount': 0,
            'unboundCount': 0, 'ownerNames': set(),
        })
        item['storeCount'] += 1
        if s.get('account_id'):
            item['boundCount'] += 1
            owner = amap.get(s['account_id'])
            if owner:
                item['ownerNames'].add(owner.get('nickname') or owner['username'])
        else:
            item['unboundCount'] += 1

    groups = [
        {**v, 'ownerNames': sorted(v['ownerNames'])}
        for v in sorted(group_stats.values(), key=lambda x: x['name'])
    ]

    return success_response(data={
        'totals': {
            'stores': len(stores),
            'bound': sum(1 for s in stores if s.get('account_id')),
            'unassigned': len(unassigned),
            'members': len(members),
            'activeMembers': sum(1 for m in members if m['status'] == 'active'),
            'groups': len(groups),
        },
        'members': members,
        'unassigned': unassigned,
        'groups': groups,
    })


@store_assign_bp.route('/store-assignment/members', methods=['GET'])
@handle_errors
@require_admin
def assignment_members():
    """可分配成员列表（非管理员账号，含各自店铺数）—— 绑定下拉框数据源"""
    rows = query(
        """SELECT a.id, a.username, a.nickname, a.role, a.status,
                  (SELECT COUNT(*) FROM stores s WHERE s.account_id = a.id) AS store_count
           FROM accounts a
           WHERE a.role != 'admin'
           ORDER BY a.status, a.id"""
    )
    return success_response(data={
        'list': [{
            'id': r['id'],
            'username': r['username'],
            'nickname': r['nickname'] or '',
            'role': r['role'],
            'status': r['status'],
            'storeCount': r['store_count'],
        } for r in rows],
    })


@store_assign_bp.route('/users/<int:uid>/stores', methods=['GET'])
@handle_errors
@require_admin
def user_bound_stores(uid):
    """某成员名下的店铺列表（用于「查看已绑定店铺」抽屉）"""
    account = Account.find_by_id(uid)
    if not account:
        return error_response("账号不存在", 404)
    rows = query("SELECT * FROM stores WHERE account_id = ? ORDER BY id", (uid,))
    return success_response(data={
        'user': {
            'id': account['id'],
            'username': account['username'],
            'nickname': account.get('nickname', ''),
            'role': account.get('role'),
        },
        'list': [_store_brief(s) for s in rows],
        'total': len(rows),
    })


# ============================================================================
# 2. 批量绑定 / 解绑
# ============================================================================

@store_assign_bp.route('/store-assignment/bind', methods=['POST'])
@handle_errors
@require_admin
def batch_bind():
    """批量绑定店铺给业务员。

    body:
      accountId : 目标业务员 ID（必填，不能是 admin）
      storePks  : [店铺主键, ...]        ┐
      storeIds  : [Ozon 店铺ID, ...]     ├ 四选一或组合，结果自动去重
      group     : '分组名'               │
      all       : true（全部店铺）        ┘
      mode      : 'add'（默认，仅追加） | 'replace'（该业务员名下店铺整体覆盖为本次集合）
      remark    : 备注（写入审计日志）

    返回：{ bound, transferred, skipped, failed:[{store, reason}], account }
    """
    data = request.get_json() or {}
    target, err = _validate_assignee(data.get('accountId'))
    if err:
        return error_response(err, 400)
    if not target:
        return error_response("请指定 accountId（如需解绑请使用 /store-assignment/unbind）", 400)

    stores, err = _resolve_target_stores(data)
    if err:
        return error_response(err, 400)
    if not stores:
        return error_response("未匹配到任何店铺", 404)

    mode = (data.get('mode') or 'add').strip()
    if mode not in ('add', 'replace'):
        return error_response("mode 仅支持 add 或 replace", 400)

    operator = get_current_user()
    remark = (data.get('remark') or '').strip()
    _amap = _account_map()   # 账号映射查一次即可，避免逐店查询

    bound, transferred, skipped = [], [], []
    log_rows = []

    for s in stores:
        current = s.get('account_id')
        if current == target['id']:
            skipped.append(s)          # 已归属本人，幂等跳过
            continue
        if current:
            transferred.append(s)
            log_rows.append((s, current, target['id'], 'transfer'))
        else:
            bound.append(s)
            log_rows.append((s, current, target['id'], 'bind'))

    # 原子写入归属
    if log_rows:
        execute_many(
            "UPDATE stores SET account_id = ?, updated_at = datetime('now') WHERE id = ?",
            [(target['id'], row[0]['id']) for row in log_rows],
        )

    # replace 模式：把该业务员名下不在本次集合中的店铺解绑为公共店铺
    released = []
    if mode == 'replace':
        keep_ids = {s['id'] for s in stores}
        owned = query("SELECT * FROM stores WHERE account_id = ?", (target['id'],))
        released = [s for s in owned if s['id'] not in keep_ids]
        if released:
            execute_many(
                "UPDATE stores SET account_id = NULL, updated_at = datetime('now') WHERE id = ?",
                [(s['id'],) for s in released],
            )
            _write_log([(s, target['id'], None) for s in released],
                       operator, remark or 'replace 模式释放', default_action='unbind')

    _write_log(log_rows, operator, remark, default_action='bind')

    return success_response(data={
        'account': {'id': target['id'], 'username': target['username']},
        'bound': [_store_brief(s) for s in bound],
        'transferred': [{
            **_store_brief(s),
            'fromAccountId': s.get('account_id'),
            'fromName': (_amap.get(s.get('account_id')) or {}).get('username', ''),
        } for s in transferred],
        'skipped': [_store_brief(s) for s in skipped],
        'released': [_store_brief(s) for s in released],
        'counts': {
            'bound': len(bound),
            'transferred': len(transferred),
            'skipped': len(skipped),
            'released': len(released),
        },
    }, msg=(
        f"已绑定 {len(bound)} 家店铺给 {target['username']}"
        + (f"，转移 {len(transferred)} 家" if transferred else '')
        + (f"，释放 {len(released)} 家" if released else '')
        + (f"，{len(skipped)} 家无需变更" if skipped else '')
    ))


@store_assign_bp.route('/store-assignment/unbind', methods=['POST'])
@handle_errors
@require_admin
def batch_unbind():
    """批量解绑店铺（回归公共店铺，仅管理员可见）。

    body: storePks / storeIds / group / all（同 bind），
          accountId 可选：仅解绑「当前归属为该业务员」的店铺（防止误伤他人店铺）。
    """
    data = request.get_json() or {}
    stores, err = _resolve_target_stores(data)
    if err:
        return error_response(err, 400)
    if not stores:
        return error_response("未匹配到任何店铺", 404)

    only_owner = data.get('accountId')
    if only_owner not in (None, '', 0):
        try:
            only_owner = int(only_owner)
        except (TypeError, ValueError):
            return error_response("accountId 必须为整数", 400)
        stores = [s for s in stores if s.get('account_id') == only_owner]

    targets = [s for s in stores if s.get('account_id')]
    if not targets:
        return success_response(data={'counts': {'unbound': 0}},
                                msg="所选店铺本就是公共店铺，无需解绑")

    execute_many(
        "UPDATE stores SET account_id = NULL, updated_at = datetime('now') WHERE id = ?",
        [(s['id'],) for s in targets],
    )
    _write_log([(s, s.get('account_id'), None) for s in targets],
               get_current_user(), (data.get('remark') or '').strip(),
               default_action='unbind')

    return success_response(data={
        'unbound': [_store_brief(s) for s in targets],
        'counts': {'unbound': len(targets)},
    }, msg=f"已解绑 {len(targets)} 家店铺为公共店铺")


# ============================================================================
# 3. 审计日志
# ============================================================================

@store_assign_bp.route('/store-assignment/logs', methods=['GET'])
@handle_errors
@require_admin
def assignment_logs():
    """绑定变更审计日志（倒序分页）。

    Query: limit(默认 50, 上限 200)、storePk(可选，查某店铺历史)
    """
    limit = min(int(request.args.get('limit', 50)), 200)
    params = []
    where = ""
    if request.args.get('storePk'):
        where = " WHERE store_pk = ?"
        params.append(int(request.args['storePk']))

    rows = query(
        "SELECT * FROM store_binding_log" + where +
        " ORDER BY id DESC LIMIT ?", params + [limit]
    )
    amap = _account_map()
    action_text = {'bind': '绑定', 'unbind': '解绑', 'transfer': '转移'}

    items = []
    for r in rows:
        to_name = (amap.get(r['to_account_id']) or {}).get('username', '') if r['to_account_id'] else ''
        from_name = (amap.get(r['from_account_id']) or {}).get('username', '') if r['from_account_id'] else ''
        items.append({
            'id': r['id'],
            'storePk': r['store_pk'],
            'storeId': r['store_id'],
            'storeAlias': r['store_alias'],
            'action': r['action'],
            'actionText': action_text.get(r['action'], r['action']),
            'fromName': from_name,
            'toName': to_name,
            'operatorName': r['operator_name'],
            'remark': r['remark'],
            'createdAt': r['created_at'],
        })
    return success_response(data={'list': items, 'total': len(items)})
