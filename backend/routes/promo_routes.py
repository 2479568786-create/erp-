# -*- coding: utf-8 -*-
"""
促销活动自动移除 API 路由

【权限模型】与其它模块保持一致：
  - 管理员：可查看/扫描/移除全部店铺
  - 业务员：只能看到自己名下店铺（utils.auth.visible_store_pks 强制过滤），
            可查看可扫描；「移除」也可用（针对自己店铺的商品）

【接口一览】
  GET    /api/promotions/overview              看板（店铺列表 + 统计 + 命中清单）
  GET    /api/promotions/alerts                告警集合（登录弹窗用；含未处理的命中）
  GET    /api/promotions/scan                  扫描（?storePk= 可选，不传则扫全部可见店铺）
  POST   /api/promotions/remove                手动移除命中商品（body: {hits:[...]})
  GET    /api/promotions/logs                  移除留痕（可按 result 过滤）
  GET    /api/promotions/products              商品促销状态快照
  GET    /api/promotions/actions               活动清单（含自动添加时间）
"""
from flask import Blueprint, request

from db import query
from services import promo_service as svc
from utils.auth import require_login, require_admin, get_current_user, visible_store_pks
from utils.response import success_response, error_response, handle_errors

promo_bp = Blueprint('promotions', __name__)


def _visible_pks(user) -> list:
    """当前用户可见的店铺主键列表（管理员=全部）。"""
    if user and user.get('role') == 'admin':
        return [r['id'] for r in query('SELECT id FROM stores ORDER BY id')]
    return list(visible_store_pks(user) or [])


def _resolve_target_pks(user, body=None):
    """解析本次操作的目标店铺：指定 storePk 时校验越权。"""
    allowed = _visible_pks(user)
    store_pk = (body or {}).get('storePk') or request.args.get('storePk')
    if store_pk:
        try:
            store_pk = int(store_pk)
        except (TypeError, ValueError):
            return None, error_response('storePk 必须为整数', 400)
        if store_pk not in allowed:
            return None, error_response('无权访问该店铺', 403)
        return [store_pk], None
    return allowed, None


# ============================================================
# 扫描 / 看板 / 告警
# ============================================================

@promo_bp.route('/promotions/scan', methods=['GET'])
@handle_errors
@require_login
def scan():
    """扫描促销活动，返回命中清单（活动价低于限制价的商品）。

    Query: storePk=1（可选，不传则扫描当前用户可见的全部店铺）
    """
    user = get_current_user()
    pks, err = _resolve_target_pks(user)
    if err:
        return err
    scanned = svc.active_store_pks(pks)
    if not scanned:
        return success_response(data={
            'stores': [], 'hits': [], 'autoProducts': [], 'reviewPromos': [],
            'errors': [], 'message': '所选店铺均未配置 Seller API 凭证，请先到「店铺管理」填写',
        }, msg='没有配置好凭证的店铺')
    result = svc.scan_stores(scanned)
    result['skipped'] = len(pks) - len(scanned)
    return success_response(
        data=result,
        msg='扫描完成：%d 家店铺，命中 %d 个商品（其中平台自动拉入 %d 个）'
            % (len(result['stores']), len(result['hits']),
               sum(1 for h in result['hits'] if h.get('isAuto'))),
    )


@promo_bp.route('/promotions/overview', methods=['GET'])
@handle_errors
@require_login
def overview():
    """促销移除看板：统计摘要 + 最近留痕 + 商品状态快照。"""
    user = get_current_user()
    pks = _visible_pks(user)

    stores = []
    if pks:
        rows = query(
            'SELECT id, store_id, alias, client_id, auth_status FROM stores'
            ' WHERE id IN (%s) ORDER BY id' % ','.join('?' * len(pks)), pks)
        for s in rows or []:
            stores.append({
                'storePk': s['id'],
                'storeId': s['store_id'],
                'storeName': s.get('alias') or s['store_id'],
                'credentialsReady': bool((s.get('client_id') or '').strip()),
                'authStatus': s.get('auth_status') or '',
            })

    return success_response(data={
        'stats': svc.get_stats(pks),
        'stores': stores,
        'logs': svc.get_logs(pks, limit=50),
        'products': svc.get_state(pks),
        'canManage': user.get('role') == 'admin',
    })


@promo_bp.route('/promotions/alerts', methods=['GET'])
@handle_errors
@require_login
def alerts():
    """告警集合。前端登录后调用，若 hasWarning 为真则弹窗提示业务员。

    这里只读本地快照（promo_product_state），不实时调 Ozon 接口，
    避免每次登录都打 Ozon API。实时扫描走 /promotions/scan。
    """
    user = get_current_user()
    pks = _visible_pks(user)
    products = svc.get_state(pks)
    stats = svc.get_stats(pks)

    auto_products = [p for p in products if int(p.get('auto_action_count') or 0) > 0]
    review_products = [p for p in products if p.get('reviewsPromoEnabled')]
    failed_logs = svc.get_logs(pks, limit=50, result='failed')

    return success_response(data={
        'stats': stats,
        'hasWarning': bool(auto_products or review_products or failed_logs),
        'autoProducts': auto_products[:50],
        'reviewPromos': review_products[:50],
        'failedRemovals': failed_logs[:20],
        'counts': {
            'autoProducts': len(auto_products),
            'reviewPromos': len(review_products),
            'failedRemovals': len(failed_logs),
        },
    })


# ============================================================
# 移除
# ============================================================

@promo_bp.route('/promotions/remove', methods=['POST'])
@handle_errors
@require_login
def remove():
    """手动移除命中商品出促销活动。

    body: {
        "hits": [{actionId, productId, storePk, addMode, autoAddDate, ...}],
        "storePk": 1        # 可选；hits 里没有 storePk 时用
    }
    也支持 {"all": true, "storePk": 1}：先实时扫描再把所有命中项移除。
    """
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    allowed = _visible_pks(user)
    if not allowed:
        return error_response('没有可操作的店铺', 400)

    hits = body.get('hits') or []
    if body.get('all'):
        pks, err = _resolve_target_pks(user, body)
        if err:
            return err
        scanned = svc.active_store_pks(pks)
        if not scanned:
            return error_response('所选店铺均未配置 Seller API 凭证', 400)
        hits = svc.scan_stores(scanned).get('hits') or []
        if not hits:
            return success_response(
                data={'removed': [], 'failed': [], 'removedCount': 0, 'failedCount': 0},
                msg='扫描完成，没有需要移除的商品')

    if not hits:
        return error_response('请提供要移除的商品（hits）', 400)

    # 越权校验：hits 里的 storePk 必须在可见范围内
    default_pk = body.get('storePk')
    cleaned = []
    for h in hits:
        pk = h.get('storePk') or default_pk
        try:
            pk = int(pk)
        except (TypeError, ValueError):
            return error_response('命中项缺少合法的 storePk', 400)
        if pk not in allowed:
            return error_response('无权操作店铺 %s 的商品' % pk, 403)
        cleaned.append({**h, 'storePk': pk})

    if body.get('dryRun'):
        return success_response(data=svc.remove_products(cleaned, operator=user,
                                                         trigger_type='manual',
                                                         dry_run=True),
                                msg='预演完成（未实际执行）')

    result = svc.remove_products(cleaned, operator=user, trigger_type='manual')
    if result['removedCount'] == 0 and result['failedCount']:
        return error_response('移除失败：%s' % result['failed'][0].get('message', '未知错误'), 400)
    return success_response(
        data=result,
        msg='移除完成：成功 %d 个，失败 %d 个'
            % (result['removedCount'], result['failedCount']))


# ============================================================
# 留痕与快照
# ============================================================

@promo_bp.route('/promotions/logs', methods=['GET'])
@handle_errors
@require_login
def logs():
    """移除留痕。

    Query: result = removed | failed（可选）、limit（默认 100，上限 500）
    """
    user = get_current_user()
    pks = _visible_pks(user)
    items = svc.get_logs(pks,
                         limit=request.args.get('limit', 100),
                         result=(request.args.get('result') or '').strip() or None)
    # 同时给出 list / logs 两个键名，前端两种写法都能取到
    return success_response(data={'list': items, 'logs': items, 'total': len(items)})


@promo_bp.route('/promotions/products', methods=['GET'])
@handle_errors
@require_login
def products():
    """商品促销状态快照（最近一次扫描结果）。"""
    user = get_current_user()
    pks = _visible_pks(user)
    items = svc.get_state(pks)
    return success_response(data={'list': items, 'products': items, 'total': len(items)})


@promo_bp.route('/promotions/actions', methods=['GET'])
@handle_errors
@require_login
def actions():
    """实时拉取活动清单（含自动添加时间）。"""
    user = get_current_user()
    pks, err = _resolve_target_pks(user)
    if err:
        return err
    scanned = svc.active_store_pks(pks)
    if not scanned:
        return success_response(data={'list': [], 'actions': [], 'total': 0},
                                msg='没有配置好凭证的店铺')
    out = []
    for pk in scanned:
        r = svc.scan_store(pk, collect_only=True)
        for a in r.get('actions') or []:
            out.append({**a, 'storePk': pk, 'storeName': r.get('storeName')})
    return success_response(data={'list': out, 'actions': out, 'total': len(out)})
