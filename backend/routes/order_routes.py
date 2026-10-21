"""
OZON ERP - 订单 API 路由
管理 FBS/FBO 发货单：列表、详情、同步、状态刷新、FBS 发货出库

接口:
- GET    /api/orders                    订单列表（分页/店铺/类型/状态/关键词/日期）
- GET    /api/orders/stats              状态统计（Tab 角标）
- GET    /api/orders/<posting_number>   订单详情
- POST   /api/orders/sync               同步订单（按店铺，FBS/FBO）
- POST   /api/orders/<posting_number>/refresh  刷新单条详情
- POST   /api/orders/<posting_number>/ship     FBS 发货出库
- PUT    /api/orders/<posting_number>/loss     填写退货/取消损失金额与归属业务员
- POST   /api/orders/batch/delete       批量删除本地记录
"""
from flask import Blueprint, request
from db import execute
from models.order import Order
from models.account import Store
from services import order_service
from services.ozon_api import ship_fbs_postings, OzonAPIError
from utils.response import success_response, error_response, paginate_response, handle_errors
from utils.auth import require_login, require_admin, get_current_user, visible_ozon_store_ids

order_bp = Blueprint('orders', __name__)


def _visible_ozon_ids(user):
    """业务员可见 Ozon 店铺 ID 列表；管理员 None（不过滤）"""
    if user.get('role') == 'admin':
        return None
    return visible_ozon_store_ids(user) or []


def _assert_order_visible(user, order):
    """非管理员：订单必须属于本人店铺"""
    if user.get('role') == 'admin':
        return
    visible = set(visible_ozon_store_ids(user) or [])
    if not order or str(order.get('store_id')) not in visible:
        raise PermissionError('无权访问该订单（不属于你绑定的店铺）')


@order_bp.route('/orders', methods=['GET'])
@handle_errors
@require_login
def list_orders():
    """订单列表（分页 + 筛选）

    权限：管理员全量；业务员仅本人绑定店铺，越权 store_id 参数被忽略
    """
    user = get_current_user()
    store_id = request.args.get('store_id') or None
    # 角色强制过滤
    visible = _visible_ozon_ids(user)
    if visible is not None:
        store_id = store_id if (store_id and store_id in visible) else None
        store_ids = visible
    else:
        store_ids = None
    ftype = request.args.get('fulfillment', '').upper() or None
    status = request.args.get('status') or None
    keyword = request.args.get('keyword') or None
    date_from = request.args.get('date_from') or None
    date_to = request.args.get('date_to') or None

    try:
        page = max(1, int(request.args.get('page', 1)))
        page_size = min(200, max(1, int(request.args.get('pageSize', 20))))
    except ValueError:
        page, page_size = 1, 20

    rows, total = Order.find_all(
        store_id=store_id, store_ids=store_ids, fulfillment_type=ftype, status=status,
        keyword=keyword, date_from=date_from, date_to=date_to,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return paginate_response(rows, total, page, page_size)


@order_bp.route('/orders/stats', methods=['GET'])
@handle_errors
@require_login
def order_stats():
    """订单状态统计（用于 Tab 角标）"""
    user = get_current_user()
    store_id = request.args.get('store_id') or None
    visible = _visible_ozon_ids(user)
    if visible is not None:
        store_id = store_id if (store_id and store_id in visible) else None
        store_ids = visible
    else:
        store_ids = None
    ftype = request.args.get('fulfillment', '').upper() or None
    counts = Order.status_counts(store_id=store_id, store_ids=store_ids, fulfillment_type=ftype)
    return success_response(data={
        'counts': counts,
        'total': sum(counts.values()),
    })


@order_bp.route('/orders/<posting_number>', methods=['GET'])
@handle_errors
@require_login
def get_order(posting_number):
    """订单详情（业务员仅本人店铺）"""
    order = Order.find_by_posting(posting_number)
    if not order:
        return error_response('订单不存在', 404)
    _assert_order_visible(get_current_user(), order)
    return success_response(data=order)


@order_bp.route('/orders/sync', methods=['POST'])
@handle_errors
@require_admin
def sync_orders():
    """同步订单（拉取 Ozon 发货单入库）

    Body: { "store_id": "...", "fulfillment": "FBS|FBO|", "days": 30 }
    store_id 为空则同步全部 active 店铺
    """
    body = request.get_json(silent=True) or {}
    store_id = body.get('store_id') or None
    ftype = (body.get('fulfillment') or '').upper() or None
    if ftype not in ('FBS', 'FBO'):
        ftype = None
    days = int(body.get('days', 30))

    payload = []
    if store_id:
        # 单店铺同步
        store = Store.find_by_store_id(store_id)
        if not store:
            return error_response(f'店铺 {store_id} 不存在', 404)
        # 单店同步（注意：fulfillment 已是 'FBS'/'FBO' 时不能把字符串当列表迭代）
        ftypes = [ftype] if ftype in ('FBS', 'FBO') else ['FBS', 'FBO']
        for f in ftypes:
            r = order_service.sync_orders_for_store(store, f, days=days)
            r['store_id'] = store.get('store_id')
            r['store_alias'] = store.get('alias')
            r['fulfillment_type'] = f
            payload.append(r)
    else:
        result = order_service.sync_orders(fulfillment_type=ftype)
        payload = result['results']

    total_saved = sum(r.get('saved', 0) for r in payload)
    total_fetched = sum(r.get('fetched', 0) for r in payload)
    errors = [r for r in payload if r.get('error')]
    return success_response(data={
        'results': payload,
        'totalSaved': total_saved,
        'totalFetched': total_fetched,
        'hasError': len(errors) > 0,
    }, msg=f'同步完成：拉取 {total_fetched} 条，写入 {total_saved} 条')


@order_bp.route('/orders/<posting_number>/refresh', methods=['POST'])
@handle_errors
@require_login
def refresh_order(posting_number):
    """刷新单条订单详情"""
    body = request.get_json(silent=True) or {}
    user = get_current_user()
    visible = _visible_ozon_ids(user)
    if visible is not None:
        sid = body.get('store_id')
        if sid and sid not in visible:
            raise PermissionError('无权刷新该店铺的订单')
        if not sid:
            # 从订单本身反查归属
            order = Order.find_by_posting(posting_number)
            _assert_order_visible(user, order)
    result = order_service.refresh_detail(posting_number, store_id=body.get('store_id'))
    if result.get('refresh'):
        return success_response(data=result, msg='订单已刷新')
    return error_response(result.get('error', '刷新失败'), 400)


@order_bp.route('/orders/<posting_number>/ship', methods=['POST'])
@handle_errors
@require_login
def ship_order(posting_number):
    """FBS 发货出库

    Body: { "products": [{"product_id": 123456, "quantity": 2}], "store_id": "..." }
    """
    body = request.get_json(silent=True) or {}
    products = body.get('products')
    if not products or not isinstance(products, list) or len(products) == 0:
        return error_response('缺少出库商品列表 products', 400)

    order = Order.find_by_posting(posting_number)
    if not order:
        return error_response('订单不存在', 404)
    _assert_order_visible(get_current_user(), order)
    if order['fulfillment_type'] == 'FBO':
        return error_response('FBO 订单由 Ozon 仓发货，无需卖家出库', 400)

    # 取店铺凭证
    store_id = body.get('store_id') or order.get('store_id')
    if store_id:
        store = Store.find_by_store_id(store_id)
    else:
        stores = Store.find_all(auth_status='active')
        store = stores[0] if stores else None
    if not store:
        return error_response('未找到店铺凭证，请先在店铺管理中配置 API Key', 401)

    from utils.security import decrypt_secret
    client_id = store.get('client_id') or store.get('store_id')
    api_key = decrypt_secret(store.get('api_key') or '')
    if not client_id or not api_key:
        return error_response('店铺凭证不完整', 401)

    try:
        resp = ship_fbs_postings(posting_number, products, client_id=str(client_id), api_key=api_key)
    except OzonAPIError as e:
        return error_response(f'发货失败：{e.message}', e.status_code or 400)

    # 发货成功后刷新本地状态
    try:
        order_service.refresh_detail(posting_number, store_id=store_id)
    except Exception:
        pass
    return success_response(data={'posting_number': posting_number, 'result': resp}, msg='发货成功')


@order_bp.route('/orders/<posting_number>/loss', methods=['PUT'])
@handle_errors
@require_login
def fill_order_loss(posting_number):
    """填写订单退货/取消损失金额（业务员登记，用于业绩扣减）

    Body: { "loss_amount": 123.45, "loss_publisher": "张三" }
    """
    body = request.get_json(silent=True) or {}
    order = Order.find_by_posting(posting_number)
    if not order:
        return error_response('订单不存在', 404)
    _assert_order_visible(get_current_user(), order)

    try:
        loss_amount = float(body.get('loss_amount') or 0)
    except (TypeError, ValueError):
        return error_response('损失金额格式错误', 400)
    if loss_amount < 0:
        return error_response('损失金额不能为负数', 400)
    loss_publisher = (body.get('loss_publisher') or '').strip()

    execute(
        "UPDATE orders SET loss_amount = ?, loss_publisher = ?, updated_at = datetime('now') "
        "WHERE posting_number = ?",
        (loss_amount, loss_publisher, posting_number),
    )
    return success_response(
        data={'posting_number': posting_number, 'loss_amount': loss_amount,
              'loss_publisher': loss_publisher},
        msg='损失金额已登记',
    )


@order_bp.route('/orders/batch/delete', methods=['POST'])
@handle_errors
@require_admin
def batch_delete_orders():
    """批量删除本地订单记录"""
    body = request.get_json(silent=True) or {}
    ids = body.get('ids') or []
    if not ids:
        return error_response('缺少 ids', 400)
    Order.batch_delete(ids)
    return success_response(data={'deleted': len(ids)}, msg='已删除')