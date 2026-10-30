"""Agent 接口（R2 起）。

路由前缀 /api（与其它业务蓝图一致，路径里写 /agent/...）。
鉴权：utils.agent_auth.agent_request_guard 已在 app 级统一校验（Bearer 或 X-Agent-Key），
这里用 require_agent_scope 再做一层 scope 兜底；JWT 管理员同样可调（便于页面排障）。
审计：成功调用的 agent_ops 由 app 级 after_request 统一写一条（set_agent_audit 补字段）。
"""
from flask import Blueprint, jsonify, request

from services import (agent_attributes_service, agent_card_service,
                      agent_images_service, agent_validate_service, copy_lint,
                      suite_service)
from services.agent_card_service import AgentValidationError
from utils.agent_auth import current_agent, require_agent_scope, set_agent_audit
from utils.agent_scope import (AgentNotFoundError, AgentPermissionError,
                               assert_product_owned, current_user,
                               forced_billing_account, requires_store_scope)
from utils.response import error_response, success_response

agent_bp = Blueprint('agent', __name__)

# R2-Fix3：上游超时/连接中断是可重试的，网关/后端都应回 JSON 并带 retryable，
# 避免 Agent 拿到 nginx 默认 HTML 页（部署重启期间会出现 502 HTML）。
_RETRYABLE_MARKERS = (
    'timed out', 'timeout', 'temporarily', 'connection reset', 'connection aborted',
    'connection refused', 'remote end closed', 'bad gateway', 'service unavailable',
    'too many requests', 'rate limit', 'broken pipe',
)


def _is_retryable_upstream(exc):
    """判断异常是否属于「可重试的上游问题」（超时 / 连接中断 / 502-504 / 429）。"""
    if isinstance(exc, (TimeoutError, ConnectionError, BrokenPipeError)):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _RETRYABLE_MARKERS)


def _upstream_error_response(exc, scope_label='请求'):
    """R2-Fix3：上游超时/中断统一返回 502 JSON；其它异常 500 且 retryable=false。"""
    if _is_retryable_upstream(exc):
        return jsonify({
            'code': 502,
            'msg': '%s超时或上游中断，请重试：%s' % (scope_label, exc),
            'data': None,
            'retryable': True,
        }), 502
    return jsonify({
        'code': 500,
        'msg': '%s失败：%s' % (scope_label, exc),
        'data': None,
        'retryable': False,
    }), 500


# ---------------------------------------------------------------------------
# 店铺级权限（R-A3）：业务员 JWT 只能操作自己店铺的商品。
# 未登录（Agent 令牌）与管理员不受限；对象不存在 404、越权 403。
# ---------------------------------------------------------------------------

def _owned_product(product_id):
    """返回 (product, None) 或 (None, error_response)。"""
    try:
        product, _store = assert_product_owned(product_id, current_user())
        return product, None
    except AgentNotFoundError as exc:
        return None, error_response(str(exc), 404)
    except AgentPermissionError as exc:
        return None, error_response(str(exc), 403)


def _deny(product_id, resp):
    """越权/不存在时补审计字段并返回响应。"""
    try:
        body = resp.get_json() or {}
        msg = body.get('msg') or ''
        code = int(body.get('code') or 0)
    except Exception:
        msg, code = '', 0
    set_agent_audit(product_id=str(product_id),
                    status='forbidden' if code == 403 else 'invalid',
                    error=msg)
    return resp


_LINT_TEXT_KEYS = ('title_ru', 'description_ru', 'tags')


def _lint_target_product_id(card_id, payload):
    """lint 的 target 解析成商品 id（用于归属校验）；直接传文案时返回 ''。"""
    payload = payload if isinstance(payload, dict) else {}
    if any(payload.get(key) for key in _LINT_TEXT_KEYS):
        return ''
    text = str(card_id or '').strip()
    if not text:
        return ''
    if text.isdigit():
        try:
            from services import card_service

            card = card_service.get_card(int(text))
        except Exception:
            card = None
        return str((card or {}).get('product_id') or '')
    if text.startswith('id_'):
        return text
    return ''


@agent_bp.route('/agent/cards/build', methods=['POST'])
@require_agent_scope('cards:write')
def agent_build_card():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response('请求体必须是 JSON 对象', 400)
    user = current_user()
    try:
        result = agent_card_service.build_card(payload, user=user)
    except AgentPermissionError as exc:  # R-A2：跨店 / 未指定店铺
        set_agent_audit(status='forbidden', error=str(exc),
                        request_id=str(payload.get('external_id') or ''))
        return error_response(str(exc), 403)
    except AgentValidationError as exc:
        set_agent_audit(status='invalid', error=str(exc),
                        request_id=str(payload.get('external_id') or ''))
        return error_response(str(exc), 422)
    except Exception as exc:  # R2-Fix3：上游超时/中断 → 502 JSON(retryable)，其它 → 500 JSON
        set_agent_audit(status='error', error=str(exc),
                        request_id=str(payload.get('external_id') or ''))
        return _upstream_error_response(exc, '建卡')
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
    _product, deny = _owned_product(product_id)  # R-A3
    if deny is not None:
        return _deny(product_id, deny)
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
    user = current_user()
    set_agent_audit(product_id=str(payload.get('product_id') or ''),
                    request_id=str(payload.get('request_id') or ''))
    token_account_id = agent.get('account_id')
    if requires_store_scope(user):
        # R-A4：业务员套图只能记在「商品所属店铺」的账号上，禁止显式覆盖
        product_id = str(payload.get('product_id') or '').strip()
        if not product_id:
            set_agent_audit(status='forbidden', error='业务员套图必须指定 product_id')
            return error_response('业务员套图必须指定 product_id（按商品所属店铺计费）', 403)
        _product, deny = _owned_product(product_id)
        if deny is not None:
            return _deny(product_id, deny)
        forced = forced_billing_account(_product)
        if not forced:
            set_agent_audit(status='forbidden', error='商品未绑定店铺，无法确定计费账号')
            return error_response('商品未绑定店铺，无法确定计费账号', 403)
        explicit = payload.get('billing_account_id')
        if explicit not in (None, ''):
            try:
                explicit_id = int(explicit)
            except (TypeError, ValueError):
                set_agent_audit(status='invalid', error='billing_account_id 必须是整数')
                return error_response('billing_account_id 必须是整数', 422)
            if explicit_id != forced:
                set_agent_audit(status='forbidden', error='禁止指定其它账号计费')
                return error_response('禁止指定计费账号：业务员套图只能记在商品所属店铺账号', 403)
        payload = dict(payload)
        payload['billing_account_id'] = forced
        token_account_id = forced
    try:
        result = suite_service.generate_suite(payload, token_account_id)
    except suite_service.AgentSuiteBillingError as exc:
        set_agent_audit(status='forbidden', error=str(exc))
        return error_response(str(exc), 403)
    except suite_service.AgentSuiteError as exc:
        set_agent_audit(status='invalid', error=str(exc))
        return error_response(str(exc), 422)
    except Exception as exc:  # R2-Fix3：套图上游超时/中断 → 502 JSON(retryable)
        set_agent_audit(status='error', error=str(exc))
        return _upstream_error_response(exc, '套图')
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
    _product, deny = _owned_product(product_id)  # R-A3
    if deny is not None:
        return _deny(product_id, deny)
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
    _product, deny = _owned_product(product_id)  # R-A3
    if deny is not None:
        return _deny(product_id, deny)
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


@agent_bp.route('/agent/cards/<card_id>/lint', methods=['POST'])
@require_agent_scope('cards:write')
def agent_card_lint(card_id):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return error_response('请求体必须是 JSON 对象', 400)
    set_agent_audit()
    target_product_id = _lint_target_product_id(card_id, payload)  # R-A3
    if target_product_id:
        _product, deny = _owned_product(target_product_id)
        if deny is not None:
            return _deny(target_product_id, deny)
    try:
        result = copy_lint.lint_target(card_id, payload)
    except ValueError as exc:
        set_agent_audit(status='invalid', error=str(exc))
        return error_response(str(exc), 404)
    set_agent_audit(status='ok' if result.get('ok') else 'warning',
                    product_id=str(result.get('product_id') or ''),
                    error='' if result.get('ok') else '%d 条文案风险' % len(result.get('violations') or []))
    return success_response(data=result,
                            msg='文案检查通过' if result.get('ok')
                            else '发现 %d 条文案风险' % len(result.get('violations') or []))


@agent_bp.route('/agent/ops', methods=['GET'])
@require_agent_scope('agent:read')
def agent_ops_list():
    """R9：Agent 调用审计查询（Agent 令牌 agent:read 或 JWT 管理员）。"""
    from db import query as _query

    product_id = (request.args.get('product_id') or '').strip()
    token_id = (request.args.get('token_id') or '').strip()
    scope = (request.args.get('scope') or '').strip()
    status = (request.args.get('status') or '').strip()
    request_id = (request.args.get('request_id') or '').strip()
    since = (request.args.get('since') or '').strip()
    try:
        limit = min(max(int(request.args.get('limit') or 50), 1), 500)
    except (TypeError, ValueError):
        limit = 50

    conditions = []
    params = []
    if product_id:
        conditions.append('product_id = ?')
        params.append(product_id)
    if request_id:
        conditions.append('request_id = ?')
        params.append(request_id)
    if token_id.isdigit():
        conditions.append('token_id = ?')
        params.append(int(token_id))
    if scope:
        conditions.append('scope = ?')
        params.append(scope)
    if status:
        conditions.append('status = ?')
        params.append(status)
    if since:
        conditions.append('created_at >= ?')
        params.append(since)
    # R-A5：业务员/操作员只看自己账号的审计记录
    scoped_user = current_user()
    if requires_store_scope(scoped_user):
        conditions.append('account_id = ?')
        params.append(int(scoped_user.get('id') or 0))
    where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
    rows = _query(
        'SELECT id, token_id, account_id, username, scope, action, product_id, request_id,'
        ' status, duration_ms, error, created_at'
        ' FROM agent_ops' + where + ' ORDER BY id DESC LIMIT ?',
        tuple(params + [limit]),
    )
    total = _query('SELECT COUNT(*) AS c FROM agent_ops' + where, tuple(params), one=True)
    set_agent_audit(status='ok')
    return success_response(data={
        'items': [dict(row) for row in rows],
        'total': int((total or {}).get('c') or 0),
        'filters': {'product_id': product_id, 'request_id': request_id, 'token_id': token_id,
                    'scope': scope, 'status': status, 'since': since, 'limit': limit},
    }, msg='共 %d 条审计' % len(rows))
