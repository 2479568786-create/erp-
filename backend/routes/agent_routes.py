"""Agent 接口（R2 起）。

路由前缀 /api（与其它业务蓝图一致，路径里写 /agent/...）。
鉴权：utils.agent_auth.agent_request_guard 已在 app 级统一校验（Bearer 或 X-Agent-Key），
这里用 require_agent_scope 再做一层 scope 兜底；JWT 管理员同样可调（便于页面排障）。
审计：成功调用的 agent_ops 由 app 级 after_request 统一写一条（set_agent_audit 补字段）。
"""
from flask import Blueprint, request

from services import (agent_attributes_service, agent_card_service,
                      agent_images_service, agent_validate_service, suite_service)
from services.agent_card_service import AgentValidationError
from utils.agent_auth import current_agent, require_agent_scope, set_agent_audit
from utils.response import error_response, success_response

agent_bp = Blueprint('agent', __name__)


@agent_bp.route('/agent/cards/build', methods=['POST'])
@require_agent_scope('cards:write')
def agent_build_card():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response('请求体必须是 JSON 对象', 400)
    try:
        result = agent_card_service.build_card(payload)
    except AgentValidationError as exc:
        set_agent_audit(status='invalid', error=str(exc),
                        request_id=str(payload.get('external_id') or ''))
        return error_response(str(exc), 422)
    set_agent_audit(product_id=result.get('product_id', ''),
                    request_id=str(payload.get('external_id') or ''))
    return success_response(data=result, msg='建卡完成')


@agent_bp.route('/products/<product_id>/images/attach', methods=['POST'])
@require_agent_scope('images:write')
def agent_attach_images(product_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response('请求体必须是 JSON 对象', 400)
    set_agent_audit(product_id=str(product_id))
    try:
        from utils.auth import get_current_user
        result = agent_images_service.attach_images(
            product_id,
            payload.get('asset_ids'),
            mode=payload.get('mode', 'replace'),
            sku_index=payload.get('sku_index', 0),
            add_to_detail=bool(payload.get('add_to_detail')),
            user=get_current_user(),
        )
    except agent_images_service.AgentImageError as exc:
        set_agent_audit(status='invalid', error=str(exc))
        return error_response(str(exc), 422)
    except Exception as exc:  # 防御：不让绑定失败拖垮批次
        set_agent_audit(status='error', error=str(exc))
        return error_response(f'绑图失败：{exc}', 500)
    return success_response(data=result, msg=f'已绑定 {result["count"]} 张图')

@agent_bp.route('/agent/images/suite', methods=['POST'])
@require_agent_scope('images:write')
def agent_images_suite():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response('请求体必须是 JSON 对象', 400)
    agent = current_agent() or {}
    set_agent_audit(product_id=str(payload.get('product_id') or ''),
                    request_id=str(payload.get('request_id') or ''))
    try:
        result = suite_service.generate_suite(payload, agent.get('account_id'))
    except suite_service.AgentSuiteBillingError as exc:
        set_agent_audit(status='forbidden', error=str(exc))
        return error_response(str(exc), 403)
    except suite_service.AgentSuiteError as exc:
        set_agent_audit(status='invalid', error=str(exc))
        return error_response(str(exc), 422)
    if not result.get('ok'):
        set_agent_audit(status='partial', error='部分模块失败')
    return success_response(
        data=result,
        msg='套图完成 %d/%d' % (sum(1 for i in result['results'] if i['status'] == 'done'),
                                result['total']),
    )

@agent_bp.route('/products/<product_id>/attributes', methods=['PUT'])
@require_agent_scope('products:write')
def agent_product_attributes(product_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response('请求体必须是 JSON 对象', 400)
    set_agent_audit(product_id=str(product_id))
    try:
        result = agent_attributes_service.apply_attributes(product_id, payload)
    except agent_attributes_service.AgentAttributeError as exc:
        set_agent_audit(status='invalid', error=str(exc))
        return error_response(str(exc), 422)
    return success_response(data=result, msg='已写入 %d 条属性' % len(result['applied']))


@agent_bp.route('/products/<product_id>/validate', methods=['POST'])
@require_agent_scope('products:read')
def agent_validate_product(product_id):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return error_response('请求体必须是 JSON 对象', 400)
    set_agent_audit(product_id=str(product_id))
    try:
        result = agent_validate_service.validate_product(
            product_id,
            store_id=payload.get('store_id'),
            dry_run=payload.get('dry_run', True),
        )
    except agent_validate_service.AgentValidateError as exc:
        set_agent_audit(status='invalid', error=str(exc))
        return error_response(str(exc), 404)
    return success_response(data=result,
                            msg='校验通过' if result['ok'] else '发现 %d 个阻断项' % len(result['blocking']))
