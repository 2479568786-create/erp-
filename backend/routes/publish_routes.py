"""
发布任务相关 API 路由
提供发布任务的提交、状态查询等接口
"""
from flask import Blueprint, request
from models.product import Product, PublishTask
from services.publish_service import PublishService, build_ozon_product_item, _get_store_currency
from utils.response import success_response, error_response, handle_errors
from utils.auth import require_login, get_current_user, visible_ozon_store_ids

publish_bp = Blueprint('publish', __name__)


def _resolve_authorized_store_id(user, requested_store_id):
    """校验并确定本次发布的目标店铺。

    - 管理员：可选任意店铺；未指定时由 create_task 提示必选。
    - 业务员：只能用自己绑定的店铺；指定了非己方店铺则 403；
      未指定且恰好绑定一家店铺时自动取该店，绑定多店时必须显式指定。
    返回 (store_id, error_response)；error_response 非空即失败。
    """
    if not user:
        return None, error_response("请先登录", 401)
    allowed = visible_ozon_store_ids(user)  # admin -> None（全量）；业务员 -> [store_id...]
    requested = (str(requested_store_id) or "").strip() if requested_store_id else ""

    if user.get('role') == 'admin':
        return requested, None

    allowed = allowed or []
    if requested:
        if requested not in allowed:
            return None, error_response("只能上架到自己有权限的店铺", 403)
        return requested, None
    # 未指定店铺：业务员只能在自己的唯一店铺，多店必须显式选择
    if len(allowed) == 1:
        return allowed[0], None
    return None, error_response("请选择要发布到的 Ozon 店铺", 422)


@publish_bp.route('/publish', methods=['POST'])
@handle_errors
@require_login
def submit_publish():
    """提交发布任务

    请求体: { "productIds": ["id1", ...], "platform"?: "ozon", "storeId"?: "店铺ID" }
    storeId 用于多店铺发布，透传到 Ozon API 凭证和币种选择。
    权限：业务员只能发布到自己绑定的店铺（后台强校验，不依赖前端）。
    """
    data = request.get_json()
    if not data:
        return error_response("请求体不能为空")

    user = get_current_user()
    requested_store_id = data.get('storeId')  # 多店铺发布：目标店铺 ID
    store_id, auth_error = _resolve_authorized_store_id(user, requested_store_id)
    if auth_error is not None:
        return auth_error
    data['storeId'] = store_id

    product_ids = data.get('productIds', [])
    platform = data.get('platform', 'ozon')
    publish_mode = data.get('publishMode') or data.get('publish_mode')
    price_offset = data.get('price_offset', data.get('priceOffset', 0))

    if not product_ids:
        return error_response("请选择要发布的商品")

    tasks = []
    invalid_ids = []
    for pid in product_ids:
        # 验证商品存在
        product = Product.find_by_id(pid)
        if not product:
            invalid_ids.append(pid)
            continue

        task = PublishService.create_task(
            pid,
            platform,
            store_id=store_id,
            publish_mode=publish_mode,
            price_offset=price_offset,
        )
        tasks.append(task)

    if not tasks:
        hint = ''
        if invalid_ids:
            hint = f'（无效商品ID: {invalid_ids}；请确认传入的是后端返回的 data.id 而非源平台 productId）'
        return error_response(f"未找到有效的商品{hint}")

    # 返回任务详情（含状态和错误信息），便于前端识别立即失败的任务
    task_details = [
        {
            "id": t['id'],
            "productId": t.get('productId'),
            "status": t.get('status', 'pending'),
            "error": t.get('error'),
        }
        for t in tasks
    ]
    failed_count = sum(1 for t in task_details if t['status'] == 'failed')

    # 若所有任务都立即失败，返回错误响应让前端明确感知
    if failed_count == len(task_details):
        first_error = task_details[0].get('error') or '发布失败'
        return error_response(
            f"发布失败: {first_error}",
            data={"tasks": task_details, "taskIds": [t['id'] for t in tasks], "count": len(tasks)}
        )

    msg = f"已提交 {len(tasks)} 个发布任务"
    if failed_count > 0:
        msg += f"（其中 {failed_count} 个立即失败）"

    return success_response(
        data={
            "taskIds": [t['id'] for t in tasks],
            "count": len(tasks),
            "tasks": task_details,
            "failedCount": failed_count,
        },
        msg=msg
    )


@publish_bp.route('/publish/<task_id>/status', methods=['GET'])
@handle_errors
def get_publish_status(task_id):
    """查询发布任务状态"""
    task = PublishTask.find_by_id(task_id)
    if not task:
        return error_response("任务不存在", 404)

    # 查询 Ozon 真实发布状态并同步到本地任务
    status_info = PublishService.check_status(task)

    return success_response(data=status_info)


@publish_bp.route('/publish/<task_id>/retry', methods=['POST'])
@handle_errors
@require_login
def retry_publish_task(task_id):
    """重试失败的发布任务（支持断点续传）

    将 failed 状态的任务重新提交到后台线程池。
    若任务在失败前已完成图片预处理和数据组装，且商品未被修改，
    则跳过前两步直接调用 Ozon API。
    """
    user = get_current_user()
    if user.get('role') != 'admin':
        task = PublishTask.find_by_id(task_id)
        if not task:
            return error_response("任务不存在", 404)
        store_id = str(task.get('storeId') or '').strip()
        allowed = visible_ozon_store_ids(user) or []
        if store_id and store_id not in allowed:
            return error_response("只能重试自己有权限店铺的发布任务", 403)
    try:
        task = PublishService.retry_task(task_id)
    except ValueError as e:
        return error_response(str(e), 400)

    return success_response(data=task, msg="任务已重新提交，支持断点续传")
