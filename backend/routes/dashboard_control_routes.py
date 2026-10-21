"""
OZON ERP - 中控大屏 & 财务中心路由
GET  /api/dashboard/control      中控大屏聚合（营业额/各店/今日取消退货/财务chip/上架人员榜）
GET  /api/finance/center         财务中心简版页（合计/大类/店铺/水位）
GET  /api/finance/transactions   财务明细分页
POST /api/finance/sync           触发财务交易明细同步（全部店铺或单店）
"""
from flask import Blueprint, request

from utils.response import success_response, error_response, handle_errors
from utils.auth import require_login, require_admin, get_current_user, visible_ozon_store_ids

dashboard_control_bp = Blueprint('dashboard_control', __name__)


def _days_arg(default=30):
    try:
        days = int(request.args.get('days', default))
    except ValueError:
        days = default
    return max(1, min(days, 365))


def _visible_ids():
    """当前用户可见 Ozon 店铺 ID 集合；管理员返回 None（全量）"""
    user = get_current_user()
    if user.get('role') == 'admin':
        return None
    return visible_ozon_store_ids(user) or []


@dashboard_control_bp.route('/dashboard/control', methods=['GET'])
@handle_errors
@require_login
def dashboard_control():
    """中控大屏聚合数据（业务员仅自己店铺）"""
    from services import finance_service
    data = finance_service.control_dashboard(days=_days_arg(), store_ids=_visible_ids())
    return success_response(data)


@dashboard_control_bp.route('/finance/center', methods=['GET'])
@handle_errors
@require_login
def finance_center():
    """财务中心简版页（业务员仅自己店铺）"""
    from services import finance_service
    store_id = request.args.get('store_id') or None
    visible = _visible_ids()
    # 业务员：越权 store_id 参数被忽略
    if visible is not None and store_id and store_id not in visible:
        store_id = None
    return success_response(finance_service.finance_center(
        days=_days_arg(), store_id=store_id, store_ids=visible))


@dashboard_control_bp.route('/finance/transactions', methods=['GET'])
@handle_errors
@require_login
def finance_transactions():
    """财务明细分页（业务员仅自己店铺）"""
    from services import finance_service
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 50))
    except ValueError:
        return error_response('参数格式错误')
    store_id = request.args.get('store_id') or None
    visible = _visible_ids()
    if visible is not None and store_id and store_id not in visible:
        store_id = None
    return success_response(finance_service.transaction_list(
        days=_days_arg(),
        store_id=store_id,
        parent_type=request.args.get('parent_type') or None,
        page=page, page_size=page_size,
        store_ids=visible,
    ))


@dashboard_control_bp.route('/finance/sync', methods=['POST'])
@handle_errors
@require_login
def finance_sync():
    """触发财务交易明细同步。body: {store_id?: str}，不传则同步当前用户可见店铺（管理员=全部）"""
    from services import finance_service
    body = request.get_json(silent=True) or {}
    store_id = body.get('store_id')
    visible = _visible_ids()
    if store_id:
        if visible is not None and str(store_id) not in [str(x) for x in visible]:
            return error_response('无权限同步该店铺', 403)
        result = finance_service.sync_store_transactions(str(store_id))
        if result.get('error'):
            # 单店失败就是失败，不能返回 200 让前端弹「成功」
            return error_response(f"店铺 {result['store_id']} 同步失败: {result['error']}", 500)
        return success_response(
            result,
            msg='同步完成：新增 %d 条明细（回溯 %d 天）' % (result.get('saved', 0),
                                                          result.get('days', 0)))

    summary = finance_service.sync_all_stores(store_ids=visible)
    stores = summary.get('stores') or []
    ok, failed = int(summary.get('ok') or 0), int(summary.get('failed') or 0)

    if not stores:
        return error_response('没有可同步的店铺（请先添加店铺并确认授权状态为「已授权」）', 400)

    if ok == 0:
        # 全部失败：这是失败，绝不能再返回 200 让前端弹绿勾
        reasons = '；'.join(
            '%s：%s' % (s.get('alias') or s.get('store_id'), s.get('error') or '未知原因')
            for s in stores if s.get('error')) or '未知原因'
        return error_response('财务同步失败（%d 家店铺全部同步失败）— %s' % (failed, reasons[:400]), 500)

    if failed:
        # 部分成功：用 warning 语义提示，让人注意到有店没同步上
        return success_response(
            summary,
            msg='同步完成：成功 %d 店，失败 %d 店（部分店铺未同步成功，请查看详情）' % (ok, failed))

    return success_response(summary, msg='同步完成：%d 家店铺全部成功' % ok)
