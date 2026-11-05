"""充值（微信 Native 支付）接口。

- GET  /api/pay/status                        支付开关 / 缺失配置 / 档位（管理员）
- POST /api/pay/orders                        建单：{points, account_id?} → code_url
- GET  /api/pay/orders/<out_trade_no>          查单（本人/管理员）
- POST /api/pay/orders/<out_trade_no>/close    关单
- POST /api/pay/orders/<out_trade_no>/refund   退款（占位，501）
- POST /api/payment/wechat/callback           微信回调（唯一入口，无鉴权靠验签）

口径：1 点 = 1 元 = 100 分；档位来自 WECHATPAY_TIERS；业务员只能给自己充，管理员可代充。
"""
import json
import os

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
MAX_BODY = 64 * 1024


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
    """支付开关 / 档位。管理员看到完整配置，业务员只看到「能否充值」。"""
    user, err = _current_user_or_401()
    if err:
        return err
    cfg = wxpay_service.config()
    missing = wxpay_service.missing_config()
    payload = {
        'enabled': cfg['enabled'],
        'ready': bool(cfg['enabled']) and not missing,
        'tiers': pay_service.tiers(),
        'order_ttl_minutes': pay_service.ORDER_TTL_MINUTES,
        'channel': 'wechat_native',
    }
    if not _is_admin(user):
        # 业务员：不暴露回调地址/缺失项细节，只告诉「能不能充」和「缺几项」
        payload['missing_count'] = len(missing)
        return success_response(payload)
    payload.update({
        'missing': missing,
        'daily_cap_points': pay_service.daily_cap_points(),
        'orders_per_minute': pay_service.ORDERS_PER_MINUTE,
        'callback_ip_mode': cfg['callback_ip_mode'],
        'callback_ips_configured': len(cfg['callback_ips']),
        'notify_url': cfg['notify_url'],
    })
    return success_response(payload)


@pay_bp.route('/pay/orders', methods=['POST'])
@handle_errors
def create_order():
    user, err = _current_user_or_401()
    if err:
        return err

    cfg = wxpay_service.config()
    if not cfg['enabled']:
        return error_response('支付未启用（WECHATPAY_ENABLED=0）', 503)
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
        account_id = int(user['id'])
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
    prepay_id = resp.get('prepay_id') or None
    if not code_url:
        pay_service.close_order(out_trade_no)
        return error_response('微信未返回二维码', 502)
    pay_service.set_code_url(out_trade_no, code_url, prepay_id)
    pay_service.log_event(out_trade_no, 'create', 'points=%s account=%s' % (points, account_id))
    return success_response({
        'out_trade_no': out_trade_no,
        'code_url': code_url,
        'points': points,
        'amount_cents': points * 100,
        'account_id': account_id,
        'expires_in': pay_service.ORDER_TTL_MINUTES * 60,
    }, msg='订单已创建，请扫码支付')


@pay_bp.route('/pay/orders', methods=['GET'])
@handle_errors
def list_orders_route():
    """充值记录列表。业务员只看自己（account_id/operator_id 命中），管理员可 ?account_id= 过滤。"""
    user, err = _current_user_or_401()
    if err:
        return err
    try:
        limit = int(request.args.get('limit') or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))  # 硬上限：单页最多 100 条（服务层同样 clamp，双保险）
    raw_account = (request.args.get('account_id') or request.args.get('account') or '').strip()
    account_id = int(raw_account) if raw_account.isdigit() else None
    if _is_admin(user):
        rows = pay_service.list_orders(account_id=account_id, limit=limit)
    else:
        rows = pay_service.list_orders(owner_id=int(user['id']), limit=limit)
    return success_response({'list': rows, 'total': len(rows)})


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
    except Exception:  # noqa: BLE001
        pass
    pay_service.log_event(out_trade_no, 'close')
    return success_response({'out_trade_no': out_trade_no, 'status': 'closed'}, msg='订单已关闭')


@pay_bp.route('/pay/orders/<out_trade_no>/refund', methods=['POST'])
@handle_errors
def refund_order(out_trade_no):
    """退款占位：接口位保留，M3 再做（需要商户平台退款权限 + 回调）。"""
    user, err = _current_user_or_401()
    if err:
        return err
    if not _is_admin(user):
        return error_response('仅管理员可发起退款', 403)
    order = pay_service.get_order(out_trade_no)
    if not order:
        return error_response('订单不存在', 404)
    pay_service.log_event(out_trade_no, 'refund_requested')
    return error_response('退款功能尚未实现（M3）', 501,
                          data={'out_trade_no': out_trade_no, 'status': order['status']})


@pay_bp.route('/payment/wechat/callback', methods=['POST'])
def wxpay_callback():
    """微信支付回调（唯一入口）。验签失败 → 401；业务异常 → 200（避免无限重试）。"""
    raw = request.get_data() or b''
    if len(raw) > MAX_BODY:
        pay_service.log_event('-', 'notify_too_large', 'bytes=%d' % len(raw))
        return json.dumps({'code': 'FAIL', 'message': 'payload_too_large'}), 413, {'Content-Type': 'application/json'}

    cfg = wxpay_service.config()
    client_ip = (request.headers.get('X-Real-IP') or request.remote_addr or '').strip()
    if cfg['callback_ips'] and client_ip not in cfg['callback_ips']:
        if cfg['callback_ip_mode'] == 'strict':
            pay_service.log_event('-', 'notify_bad_ip', client_ip)
            return json.dumps({'code': 'FAIL', 'message': 'ip_not_allowed'}), 403, {'Content-Type': 'application/json'}
        pay_service.log_event('-', 'notify_unexpected_ip', client_ip)  # 先只记日志

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

    notify_id = str(payload.get('id') or '')
    digest = pay_service.digest_of(raw)
    out_trade_no = data.get('out_trade_no') or ''
    order = pay_service.get_order(out_trade_no)
    if not order:
        pay_service.log_event(out_trade_no or '-', 'notify_unknown_order')
        return json.dumps(SUCCESS), 200, {'Content-Type': 'application/json'}

    if pay_service.digest_seen(out_trade_no, digest):
        pay_service.log_event(out_trade_no, 'notify_duplicate', notify_id)
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
        try:
            pay_service.mark_paid(out_trade_no, data.get('transaction_id') or '',
                                  notify_id=notify_id, notify_digest=digest,
                                  notify_raw=raw.decode('utf-8', 'replace')[:2000])
        except Exception as exc:  # noqa: BLE001 - 唯一索引冲突 = 重复回调
            pay_service.log_event(out_trade_no, 'notify_mark_paid_dup', str(exc)[:200])
    result = pay_service.credit_order(out_trade_no)
    pay_service.log_event(out_trade_no, 'notify_ok', 'credit=%s' % result.get('reason'))
    return json.dumps(SUCCESS), 200, {'Content-Type': 'application/json'}
