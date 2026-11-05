"""充值（微信 Native 支付）接口。

- GET  /api/pay/status                 支付开关与缺失配置（管理员可见）
- POST /api/pay/orders                 建单：{points, account_id?} → code_url
- GET  /api/pay/orders/<out_trade_no>   查单（本人/管理员）
- POST /api/pay/orders/<out_trade_no>/close  关单
- POST /api/pay/wx/notify              微信回调（无鉴权，靠验签）

计费口径：1 点 = 1 元；只有白名单档位可下单；业务员只能给自己充，管理员可代充。
"""
import json

from flask import Blueprint, request

from services import pay_service, wxpay_service
from utils.auth import get_current_user
from utils.response import error_response, handle_errors, success_response

pay_bp = Blueprint('pay', __name__)

try:  # 幂等建表；失败不阻断应用启动（/pay/status 会暴露问题）
    pay_service.ensure_tables()
except Exception:  # noqa: BLE001
    pass

SUCCESS = {'code': 'SUCCESS', 'message': '成功'}


def _current_user_or_401():
    user = get_current_user() or {}
    if not user.get('id'):
        return None, error_response('请先登录', 401)
    return user, None


def _is_admin(user):
    return (user.get('role') or '') == 'admin'


@pay_bp.route('/pay/status', methods=['GET'])
@handle_errors
def pay_status():
    user, err = _current_user_or_401()
    if err:
        return err
    if not _is_admin(user):
        return error_response('仅管理员可见', 403)
    return success_response({
        'enabled': wxpay_service.config()['enabled'],
        'ready': wxpay_service.config()['enabled'] and not wxpay_service.missing_config(),
        'missing': wxpay_service.missing_config(),
        'tiers': list(pay_service.TIERS_POINTS),
        'daily_cap_points': pay_service.daily_cap_points(),
        'orders_per_minute': pay_service.ORDERS_PER_MINUTE,
        'order_ttl_minutes': pay_service.ORDER_TTL_MINUTES,
    })


@pay_bp.route('/pay/orders', methods=['POST'])
@handle_errors
def create_order():
    user, err = _current_user_or_401()
    if err:
        return err

    cfg = wxpay_service.config()
    if not cfg['enabled']:
        return error_response('支付未启用（WXPAY_ENABLED=0）', 503)
    missing = wxpay_service.missing_config()
    if missing:
        return error_response('支付配置不完整：缺少 %s' % '、'.join(missing), 503)

    body = request.get_json(silent=True) or {}
    ok, points, reason = pay_service.validate_points(body.get('points'))
    if not ok:
        return error_response(reason, 400)

    account_id = body.get('account_id')
    try:
        account_id = int(account_id) if account_id not in (None, '') else None
    except (TypeError, ValueError):
        return error_response('account_id 不合法', 400)
    if not _is_admin(user):
        account_id = int(user['id'])          # 业务员强制自己，忽略传入
    elif account_id is None:
        account_id = int(user['id'])

    used = pay_service.daily_points(account_id)
    if used + points > pay_service.daily_cap_points():
        return error_response('超过单账号单日充值上限 %s 点（今日已充 %s 点）'
                              % (pay_service.daily_cap_points(), used), 429)
    if pay_service.orders_last_minute(account_id) >= pay_service.ORDERS_PER_MINUTE:
        return error_response('下单太频繁，请稍后再试', 429)

    out_trade_no = pay_service.new_out_trade_no()
    pay_service.create_order(out_trade_no, account_id, int(user['id']), points, points * 100)
    try:
        resp = wxpay_service.create_native_order(
            out_trade_no, 'Geekfast ERP 图片点数充值 %s 点' % points, points * 100)
    except Exception as exc:  # noqa: BLE001
        pay_service.close_order(out_trade_no)
        pay_service.log_event(out_trade_no, 'create_fail', str(exc)[:300])
        return error_response('微信下单失败：%s' % str(exc)[:160], 502)

    code_url = resp.get('code_url') or ''
    if not code_url:
        pay_service.close_order(out_trade_no)
        return error_response('微信未返回二维码', 502)
    pay_service.set_code_url(out_trade_no, code_url)
    pay_service.log_event(out_trade_no, 'create', 'points=%s account=%s' % (points, account_id))
    return success_response({
        'out_trade_no': out_trade_no,
        'code_url': code_url,
        'points': points,
        'amount_cents': points * 100,
        'account_id': account_id,
        'expires_in': pay_service.ORDER_TTL_MINUTES * 60,
    }, msg='订单已创建，请扫码支付')


@pay_bp.route('/pay/orders/<out_trade_no>', methods=['GET'])
@handle_errors
def get_order(out_trade_no):
    user, err = _current_user_or_401()
    if err:
        return err
    order = pay_service.get_order(out_trade_no)
    if not order:
        return error_response('订单不存在', 404)
    if not _is_admin(user) and int(order['operator_id']) != int(user['id']) \
            and int(order['account_id']) != int(user['id']):
        return error_response('无权查看该订单', 403)
    return success_response({
        'out_trade_no': order['out_trade_no'],
        'status': order['status'],
        'points': order['points'],
        'amount_cents': order['amount_cents'],
        'account_id': order['account_id'],
        'paid_at': order['paid_at'],
        'credited_at': order['credited_at'],
        'created_at': order['created_at'],
    })


@pay_bp.route('/pay/orders/<out_trade_no>/close', methods=['POST'])
@handle_errors
def close_order(out_trade_no):
    user, err = _current_user_or_401()
    if err:
        return err
    order = pay_service.get_order(out_trade_no)
    if not order:
        return error_response('订单不存在', 404)
    if not _is_admin(user) and int(order['operator_id']) != int(user['id']):
        return error_response('无权关闭该订单', 403)
    if order['status'] != 'created':
        return error_response('当前状态不可关闭：%s' % order['status'], 409)
    pay_service.close_order(out_trade_no)
    try:
        wxpay_service.close_order(out_trade_no)
    except Exception:  # noqa: BLE001 - 本地已关，远端失败不影响
        pass
    pay_service.log_event(out_trade_no, 'close')
    return success_response({'out_trade_no': out_trade_no, 'status': 'closed'}, msg='订单已关闭')


@pay_bp.route('/pay/wx/notify', methods=['POST'])
def wxpay_notify():
    """微信支付回调。验签失败 → 401（让微信重试）；业务异常 → 200（避免无限重试）。"""
    raw = request.get_data() or b''
    ok, reason = wxpay_service.verify_notify(request.headers, raw)
    if not ok:
        pay_service.log_event('-', 'notify_bad_sig', reason)
        return json.dumps({'code': 'FAIL', 'message': reason}), 401, {'Content-Type': 'application/json'}

    try:
        payload = json.loads(raw.decode('utf-8'))
        data = wxpay_service.decrypt_resource(payload.get('resource') or {})
    except Exception as exc:  # noqa: BLE001
        pay_service.log_event('-', 'notify_decrypt_fail', str(exc)[:200])
        return json.dumps({'code': 'FAIL', 'message': 'decrypt_failed'}), 400, {'Content-Type': 'application/json'}

    out_trade_no = data.get('out_trade_no') or ''
    order = pay_service.get_order(out_trade_no)
    if not order:
        pay_service.log_event(out_trade_no or '-', 'notify_unknown_order')
        return json.dumps(SUCCESS), 200, {'Content-Type': 'application/json'}

    if (data.get('trade_state') or '').upper() != 'SUCCESS':
        pay_service.log_event(out_trade_no, 'notify_state', data.get('trade_state'))
        return json.dumps(SUCCESS), 200, {'Content-Type': 'application/json'}

    paid_total = int(((data.get('amount') or {}).get('total')) or 0)
    if paid_total != int(order['amount_cents']):
        pay_service.log_event(out_trade_no, 'notify_bad_amount',
                              'paid=%s order=%s' % (paid_total, order['amount_cents']))
        return json.dumps(SUCCESS), 200, {'Content-Type': 'application/json'}

    if order['status'] == 'created':
        pay_service.mark_paid(out_trade_no, data.get('transaction_id') or '', raw.decode('utf-8', 'replace')[:2000])
    result = pay_service.credit_order(out_trade_no)
    pay_service.log_event(out_trade_no, 'notify_ok',
                          'credit=%s' % result.get('reason'))
    return json.dumps(SUCCESS), 200, {'Content-Type': 'application/json'}
