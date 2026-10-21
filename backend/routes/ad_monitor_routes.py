# -*- coding: utf-8 -*-
"""
AI 广告监控 API 路由

【权限模型】与其它模块保持一致：
  - 管理员：可查看全部店铺的广告数据，可同步、可改阈值、可配凭证
  - 业务员：只能看到自己名下店铺（utils.auth.visible_store_pks 强制过滤），
            可查看与手动同步自己店铺，不能改阈值/凭证

【接口一览】
  GET    /api/ad-monitor/overview               看板（店铺列表 + 当日花费 + 三档告警）
  GET    /api/ad-monitor/alerts                 告警集合（登录弹窗用；含「当日花费超线」）
  GET    /api/ad-monitor/products               商品级告警（可按 type 过滤）
  GET    /api/ad-monitor/campaigns              广告活动列表
  GET    /api/ad-monitor/sync-logs              同步日志
  POST   /api/ad-monitor/sync                   触发同步（可指定 storePk / all）
  GET    /api/ad-monitor/settings               读取阈值
  PUT    /api/ad-monitor/settings               修改阈值（管理员）
  POST   /api/ad-monitor/stores/<pk>/credentials  保存 Performance 凭证（管理员）
  POST   /api/ad-monitor/stores/<pk>/test         凭证连通性自检（管理员）
"""
from flask import Blueprint, request

from db import query
from services import ad_monitor_service as svc
from services import ozon_performance_api as perf
from utils.auth import require_login, require_admin, get_current_user, visible_store_pks
from utils.response import success_response, error_response, handle_errors

ad_monitor_bp = Blueprint('ad_monitor', __name__)


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
# 看板 / 告警
# ============================================================

@ad_monitor_bp.route('/ad-monitor/overview', methods=['GET'])
@handle_errors
@require_login
def overview():
    """广告监控看板：店铺配置状态 + 当日花费 + 三档商品告警。"""
    user = get_current_user()
    pks = _visible_pks(user)
    data = svc.build_overview(pks)
    # 无凭证的店铺一并回报，前端提示去配置
    data['canManage'] = user.get('role') == 'admin'
    return success_response(data=data)


@ad_monitor_bp.route('/ad-monitor/alerts', methods=['GET'])
@handle_errors
@require_login
def alerts():
    """告警集合。前端登录后调用，若 hasDailyWarning 为真则弹窗。

    Query: date=YYYY-MM-DD（可选，默认今天 MSK）
    """
    user = get_current_user()
    pks = _visible_pks(user)
    stat_date = (request.args.get('date') or '').strip() or None
    return success_response(data=svc.build_alerts(pks, stat_date))


@ad_monitor_bp.route('/ad-monitor/products', methods=['GET'])
@handle_errors
@require_login
def products():
    """商品级告警明细。

    Query: type = test_complete | low_ctr | no_conversion（不传则全部）
    """
    user = get_current_user()
    pks = _visible_pks(user)
    result = svc.evaluate_products(pks)
    wanted = (request.args.get('type') or '').strip()
    bucket_map = {
        'test_complete': 'testComplete',
        'low_ctr': 'lowCtr',
        'no_conversion': 'noConversion',
    }
    if wanted in bucket_map:
        items = result[bucket_map[wanted]]
    else:
        items = result['all']
    return success_response(data={
        'list': items,
        'total': len(items),
        'counts': {
            'testComplete': len(result['testComplete']),
            'lowCtr': len(result['lowCtr']),
            'noConversion': len(result['noConversion']),
        },
        'settings': svc.get_settings(),
    })


# ============================================================
# 活动与同步
# ============================================================

@ad_monitor_bp.route('/ad-monitor/campaigns', methods=['GET'])
@handle_errors
@require_login
def campaigns():
    """已同步的广告活动列表（按店铺过滤）。"""
    user = get_current_user()
    pks = _visible_pks(user)
    if not pks:
        return success_response(data={'list': [], 'total': 0})
    ph = ','.join('?' * len(pks))
    rows = query(
        'SELECT * FROM ad_campaigns WHERE store_pk IN (%s) ORDER BY store_pk, id' % ph,
        pks,
    )
    names = {r['id']: (r['alias'] or r['store_id'])
             for r in query('SELECT id, store_id, alias FROM stores WHERE id IN (%s)' % ph, pks)}
    placement_text = {
        'PLACEMENT_SEARCH_AND_CATEGORY': '搜索与类目',
        'PLACEMENT_TOP_PROMOTION': '登上顶端',
        'PLACEMENT_OVERTOP': '置顶展示',
        'PLACEMENT_TAKEOVER': '买断展示',
        'PLACEMENT_TILE_MAIN': '首页橱窗',
        'PLACEMENT_PDP': '商品详情页',
    }
    payment_text = {'CPC': '按点击付费', 'CPO': '按订单付费', 'CPM': '按展示付费'}
    items = [{
        'campaignId': r['campaign_id'],
        'title': r['title'],
        'storePk': r['store_pk'],
        'storeName': names.get(r['store_pk'], ''),
        'paymentType': r['payment_type'],
        'paymentTypeText': payment_text.get(r['payment_type'], r['payment_type'] or '-'),
        'placement': r['placement'],
        'placementText': placement_text.get(r['placement'], r['placement'] or '-'),
        'state': r['state'],
        'advObjectType': r['adv_object_type'],
        'budget': r['budget'],
        'dailyBudget': r['daily_budget'],
        'fromDate': r['from_date'],
        'toDate': r['to_date'],
        'syncedAt': r['synced_at'],
    } for r in rows or []]
    return success_response(data={'list': items, 'total': len(items)})


@ad_monitor_bp.route('/ad-monitor/sync', methods=['POST'])
@handle_errors
@require_login
def sync():
    """触发广告数据同步。

    body: { "storePk": 1 }  或 { } （同步当前用户可见的全部店铺）
    """
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    pks, err = _resolve_target_pks(user, body)
    if err:
        return err
    if not pks:
        return error_response('没有可同步的店铺', 400)

    # 只同步配好凭证且开启监控的店铺，避免满屏报错
    monitorable = svc.monitorable_store_pks(pks)
    if not monitorable:
        return success_response(data={
            'synced': [], 'errors': [],
            'skipped': len(pks),
            'message': '所选店铺均未配置 Performance API 凭证，请先到「店铺管理」填写',
        }, msg='没有配置好广告凭证的店铺')

    result = svc.sync_stores(monitorable)
    result['skipped'] = len(pks) - len(monitorable)
    ok_count = len(result['synced'])
    fail_count = len(result['errors'])
    if ok_count == 0 and fail_count:
        return error_response(result['errors'][0]['message'], 400)
    return success_response(
        data=result,
        msg='同步完成：成功 %d 家，失败 %d 家' % (ok_count, fail_count),
    )


@ad_monitor_bp.route('/ad-monitor/sync-logs', methods=['GET'])
@handle_errors
@require_login
def sync_logs():
    """同步日志（管理员看全部，业务员看自己店铺）。"""
    user = get_current_user()
    pks = _visible_pks(user)
    if not pks:
        return success_response(data={'list': [], 'total': 0})
    limit = min(int(request.args.get('limit', 50) or 50), 200)
    ph = ','.join('?' * len(pks))
    rows = query(
        'SELECT * FROM ad_sync_log WHERE store_pk IN (%s) ORDER BY id DESC LIMIT ?' % ph,
        pks + [limit],
    )
    return success_response(data={'list': [dict(r) for r in rows or []], 'total': len(rows or [])})


# ============================================================
# 阈值配置（管理员）
# ============================================================

@ad_monitor_bp.route('/ad-monitor/settings', methods=['GET'])
@handle_errors
@require_login
def get_settings():
    return success_response(data=svc.get_settings())


@ad_monitor_bp.route('/ad-monitor/settings', methods=['PUT'])
@handle_errors
@require_admin
def put_settings():
    data = svc.update_settings(request.get_json(silent=True) or {}, get_current_user())
    return success_response(data=data, msg='监控阈值已更新')


# ============================================================
# 凭证管理（管理员）
# ============================================================

@ad_monitor_bp.route('/ad-monitor/stores/<int:store_pk>/credentials', methods=['POST'])
@handle_errors
@require_admin
def save_store_credentials(store_pk):
    """保存某店铺的 Performance API 凭证并立即验签。

    body: { "clientId": "xxx@advertising.performance.ozon.ru", "clientSecret": "..." }
    为空的字段表示不修改。
    """
    store = query('SELECT * FROM stores WHERE id = ?', (store_pk,), one=True)
    if not store:
        return error_response('店铺不存在', 404)
    body = request.get_json(silent=True) or {}
    client_id = (body.get('clientId') or '').strip()
    client_secret = (body.get('clientSecret') or '').strip()
    if not client_id and not client_secret:
        return error_response('请填写 client_id 与 client_secret', 400)
    if client_secret and not client_id and not (store.get('perf_client_id') or ''):
        return error_response('请同时填写 client_id', 400)

    perf.save_credentials(
        store_pk,
        client_id or store.get('perf_client_id') or '',
        client_secret,   # 为空则仅更新 client_id（不覆盖已存密钥）
    )

    # 立即验签，把问题在保存时就暴露出来
    try:
        info = perf.test_connection(store_pk)
        return success_response(data=info, msg='凭证已保存并验证通过，共发现 %d 个广告活动' % info['campaignCount'])
    except Exception as exc:  # noqa: BLE001
        return success_response(
            data={'ok': False, 'error': str(exc)},
            msg='凭证已保存，但验证失败：%s' % exc,
        )


@ad_monitor_bp.route('/ad-monitor/stores/<int:store_pk>/test', methods=['POST'])
@handle_errors
@require_admin
def test_store(store_pk):
    """凭证连通性自检（不改数据）。"""
    info = perf.test_connection(store_pk)
    return success_response(data=info, msg='连接正常，共发现 %d 个广告活动' % info['campaignCount'])
