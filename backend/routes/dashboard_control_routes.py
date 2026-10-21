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
    """同步当前账号可见店铺的财务明细。"""
    from services import finance_service
    body = request.get_json(silent=True) or {}
    store_id = body.get('store_id')
    user = get_current_user()
    visible_ids = visible_ozon_store_ids(user)
    try:
        days = max(1, min(int(body.get('days', 90)), 365))
    except (TypeError, ValueError):
        days = 90
    if store_id:
        if visible_ids is not None and str(store_id) not in visible_ids:
            return error_response('无权同步该店铺', 403)
        result = finance_service.sync_store_transactions(str(store_id), initial_days=days)
        if result.get('error'):
            return error_response(f"店铺 {result['store_id']} 同步失败: {result['error']}", 500)
        return success_response(result, msg='同步完成')
    summary = finance_service.sync_all_stores(days=days, store_ids=visible_ids)
    return success_response(summary, msg=f"同步完成：成功 {summary['ok']} 店，失败 {summary['failed']} 店")
