"""生图计费接口（外部机器人与管理端专用）。

鉴权：``X-Ext-Key``（与采集插件同一把 key，环境变量 OZON_EXT_API_KEYS/EXT_API_KEY），
或 ERP 管理员登录态。所有写操作（充值/开关/改单价）都记 ``image_ops_log``。

接口一览
--------
- GET  /api/ext/image-billing/ping                       连通性自检
- GET  /api/ext/image-billing/accounts                   全部账号余额/开关/消耗
- GET  /api/ext/image-billing/accounts/<id>              单账号快照
- POST /api/ext/image-billing/accounts/<id>/balance      充值/设置余额（mode=add|set）
- POST /api/ext/image-billing/accounts/<id>/enabled      打开/关闭生图
- GET  /api/ext/image-billing/usage?account_id=&days=    按天聚合用量
- GET  /api/ext/image-billing/usage/logs?account_id=     原始扣点明细
- GET  /api/ext/image-billing/ops-logs?account_id=       充值与开关审计
- GET  /api/ext/image-billing/topups?account_id=         充值流水
- GET  /api/ext/image-billing/config                     单价配置
- PUT  /api/ext/image-billing/config                     改单价（points_per_image）

管理端（ERP 登录态，仅管理员）
------------------------------
- POST /api/image-billing/accounts/<id>/topup            给账号加点数（充值）
- GET  /api/image-billing/topups                         充值流水（对账）
"""
from datetime import datetime

from flask import Blueprint, request

from services import image_billing_service as billing
from utils.auth import get_current_user, require_admin
from utils.ext_auth import require_ext_or_admin, require_ext_or_login
from utils.response import error_response, handle_errors, success_response

image_billing_bp = Blueprint('image_billing', __name__)

SERVICE_VERSION = '1.0.0'


def _operator_label():
    user = get_current_user() or {}
    return user.get('username') or user.get('nickname') or 'ext-key'


def _int_arg(name, default=None):
    raw = request.args.get(name)
    if raw is None or str(raw).strip() == '':
        return default
    return int(raw)


@image_billing_bp.route('/ext/image-billing/ping', methods=['GET'])
@require_ext_or_login
def ping():
    return success_response({
        'service': 'image-billing',
        'version': SERVICE_VERSION,
        'server_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'config': billing.get_billing_config(),
    }, msg='pong')


@image_billing_bp.route('/ext/image-billing/accounts', methods=['GET'])
@require_ext_or_login
@handle_errors
def list_accounts():
    accounts = billing.list_accounts_billing()
    return success_response({
        'list': accounts,
        'total': len(accounts),
        'config': billing.get_billing_config(),
    })


@image_billing_bp.route('/ext/image-billing/accounts/<int:account_id>', methods=['GET'])
@require_ext_or_login
@handle_errors
def get_account(account_id):
    row = billing.account_billing(account_id)
    if not row:
        return error_response('账号不存在', 404)
    return success_response(row)


@image_billing_bp.route('/ext/image-billing/accounts/<int:account_id>/balance', methods=['POST'])
@require_ext_or_admin
@handle_errors
def change_balance(account_id):
    body = request.get_json(silent=True) or {}
    points = body.get('points')
    if points is None:
        points = body.get('amount')
    if points is None:
        return error_response('缺少 points 参数（单位：点，1点=1元）', 400)
    mode = (body.get('mode') or 'add').strip().lower()
    operator = (body.get('operator') or '').strip() or _operator_label()
    reason = (body.get('reason') or '').strip()
    if not reason:
        reason = '充值' if mode == 'add' else '设置余额'
    result = billing.adjust_balance(account_id, points=points, mode=mode,
                                    reason=reason, operator=operator)
    return success_response(result, msg=('已充值 %s 点' % points) if mode == 'add'
                            else ('余额已设为 %s 点' % points))


@image_billing_bp.route('/ext/image-billing/accounts/<int:account_id>/enabled', methods=['POST'])
@require_ext_or_admin
@handle_errors
def change_enabled(account_id):
    body = request.get_json(silent=True) or {}
    if 'enabled' not in body:
        return error_response('缺少 enabled 参数（true/false）', 400)
    operator = (body.get('operator') or '').strip() or _operator_label()
    reason = (body.get('reason') or '').strip()
    result = billing.set_enabled(account_id, body.get('enabled'), operator=operator, reason=reason)
    return success_response(result, msg='生图已开启' if result['image_enabled'] else '生图已关闭')


@image_billing_bp.route('/ext/image-billing/usage', methods=['GET'])
@require_ext_or_login
@handle_errors
def usage():
    account_id = _int_arg('account_id')
    days = _int_arg('days', 30) or 30
    days = max(1, min(days, 366))
    date_from = (request.args.get('from') or '').strip() or None
    date_to = (request.args.get('to') or '').strip() or None
    rows = billing.usage_by_day(account_id=account_id, days=days,
                                date_from=date_from, date_to=date_to)
    totals = {
        'images': sum(item['images'] for item in rows),
        'cost_points': round(sum(item['cost_points'] for item in rows), 2),
        'requests': sum(item['requests'] for item in rows),
    }
    return success_response({'list': rows, 'totals': totals,
                             'days': days, 'from': date_from, 'to': date_to})


@image_billing_bp.route('/ext/image-billing/usage/logs', methods=['GET'])
@require_ext_or_login
@handle_errors
def usage_logs():
    account_id = _int_arg('account_id')
    limit = _int_arg('limit', 100) or 100
    since = (request.args.get('from') or '').strip() or None
    return success_response({'list': billing.usage_logs(account_id=account_id, limit=limit, since=since)})


@image_billing_bp.route('/ext/image-billing/ops-logs', methods=['GET'])
@require_ext_or_login
@handle_errors
def ops_logs():
    account_id = _int_arg('account_id')
    limit = _int_arg('limit', 100) or 100
    return success_response({'list': billing.ops_logs(account_id=account_id, limit=limit)})


@image_billing_bp.route('/ext/image-billing/config', methods=['GET'])
@require_ext_or_login
@handle_errors
def get_config():
    return success_response(billing.get_billing_config())


@image_billing_bp.route('/ext/image-billing/topups', methods=['GET'])
@require_ext_or_login
@handle_errors
def ext_topups():
    account_id = _int_arg('account_id')
    limit = _int_arg('limit', 100) or 100
    rows = billing.topup_logs(account_id=account_id, limit=limit)
    return success_response({'list': rows, 'total_points': round(sum(r['points'] for r in rows), 2)})


# ---------------------------------------------------------------- 管理端（登录态）

@image_billing_bp.route('/image-billing/topups', methods=['GET'])
@require_admin
@handle_errors
def admin_topups():
    """管理员：充值流水（对账用）。"""
    account_id = _int_arg('account_id')
    limit = _int_arg('limit', 100) or 100
    rows = billing.topup_logs(account_id=account_id, limit=limit)
    return success_response({'list': rows, 'total_points': round(sum(r['points'] for r in rows), 2)})


@image_billing_bp.route('/image-billing/accounts/<int:account_id>/topup', methods=['POST'])
@require_admin
@handle_errors
def admin_topup(account_id):
    """管理员：给账号加点数（充值）。只接受正数，写一条充值流水。"""
    body = request.get_json(silent=True) or {}
    points = body.get('points')
    if points is None:
        return error_response('缺少 points 参数（单位：点，1点=1元）', 400)
    operator = _operator_label()
    reason = (body.get('reason') or '').strip() or '管理员充值'
    result = billing.topup(account_id, points=points, reason=reason, operator=operator)
    return success_response(result, msg='已充值 %g 点' % float(points))


@image_billing_bp.route('/ext/image-billing/config', methods=['PUT', 'POST'])
@require_ext_or_admin
@handle_errors
def update_config():
    body = request.get_json(silent=True) or {}
    price = body.get('price_points_per_image')
    if price is None:
        price = body.get('points_per_image')
    if price is None:
        return error_response('缺少 price_points_per_image 参数（单位：点/张）', 400)
    result = billing.set_billing_config(price_points_per_image=price,
                                        currency=body.get('currency'))
    return success_response(result, msg='单价已更新为 %s 点/张' % result['price_points_per_image'])
