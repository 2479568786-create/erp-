"""充值（微信 Native 支付）接口。

- GET  /api/pay/status                        支付开关 / 缺失配置 / 档位（管理员）
- POST /api/pay/orders                        建单：{points, account_id?} → code_url
- GET  /api/pay/orders/<out_trade_no>          查单（本人/管理员）
- POST /api/pay/orders/<out_trade_no>/close    关单
- POST /api/pay/orders/<out_trade_no>/refund   退款（占位，501）
- POST /api/payment/wechat/callback           微信回调（唯一入口，无鉴权靠验签）
- POST /api/pay/alipay/notify                  支付宝异步通知（唯一入口，无鉴权靠验签，应答纯文本 success/fail）

口径：1 点 = 1 元 = 100 分；档位来自 WECHATPAY_TIERS；业务员只能给自己充，管理员可代充。
"""
import json
import os

from flask import Blueprint, request

from services import alipay_notify, alipay_service, pay_service, wxpay_service
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


# 渠道注册表：新增渠道只加一行 + 一个 config/missing_config 适配器
CHANNEL_LABELS = {'wxpay': '微信扫码', 'alipay': '支付宝扫码'}
CHANNEL_ORDER = ('wxpay', 'alipay')
DEFAULT_PRIMARY_CHANNEL = 'alipay'      # 2026-11 起主通道为支付宝，微信保留可切回
_LEGACY_CHANNEL_NAME = {'wxpay': 'wechat_native', 'alipay': 'alipay_qr'}


def _channel_state(name):
    """单个渠道的开关/就绪状态。missing 只给变量名，不含值。"""
    if name == 'wxpay':
        cfg = wxpay_service.config()
        missing = wxpay_service.missing_config()
        sandbox = False
    else:
        cfg = alipay_service.config()
        missing = alipay_service.missing_config()
        sandbox = bool(cfg.get('sandbox'))
    return {
        'label': CHANNEL_LABELS.get(name, name),
        'enabled': bool(cfg.get('enabled')),
        'ready': bool(cfg.get('enabled')) and not missing,
        'missing': missing,
        'notify_url': cfg.get('notify_url') or '',
        'sandbox': sandbox,
    }


def _primary_channel(channels, env=None):
    """配置的主通道；值非法或未配置时回落到默认主通道。"""
    env = os.environ if env is None else env
    raw = (env.get('PAY_PRIMARY_CHANNEL') or '').strip().lower()
    if raw in channels:
        return raw
    if raw:
        return DEFAULT_PRIMARY_CHANNEL      # 配了但不认识 → 回默认，并在 status 里体现
    return DEFAULT_PRIMARY_CHANNEL


def _active_channel(primary, channels):
    """真正能用的渠道：主通道优先，否则取第一个就绪的；都不行返回 None。"""
    if primary in channels and channels[primary]['ready']:
        return primary
    for name in CHANNEL_ORDER:
        if name in channels and channels[name]['ready']:
            return name
    return None


@pay_bp.route('/pay/status', methods=['GET'])
@handle_errors
def pay_status():
    """支付开关 / 档位 / 各渠道状态。管理员看完整配置，业务员只看到「能否充值」。"""
    user, err = _current_user_or_401()
    if err:
        return err

    channels = {name: _channel_state(name) for name in CHANNEL_ORDER}
    primary = _primary_channel(channels)
    active = _active_channel(primary, channels)
    is_admin = _is_admin(user)

    # 对外只暴露「能不能用」，缺失项细节只给管理员
    public_channels = {}
    for name, state in channels.items():
        item = {
            'label': state['label'],
            'enabled': state['enabled'],
            'ready': state['ready'],
            'missing_count': len(state['missing']),
            'sandbox': state['sandbox'],
        }
        if is_admin:
            item['missing'] = state['missing']
            item['notify_url'] = state['notify_url']
        public_channels[name] = item

    payload = {
        'primary_channel': primary,
        'active_channel': active,           # 真正可用的渠道（主通道没开时自动落到备选）
        'can_pay': active is not None,
        'channels': public_channels,
        'ready_channels': [n for n in CHANNEL_ORDER if channels[n]['ready']],
        'tiers': pay_service.tiers(),
        'order_ttl_minutes': pay_service.ORDER_TTL_MINUTES,
        # ---- 兼容旧前端/旧脚本：仍是「当前主通道」的开关与就绪态 ----
        'enabled': channels[primary]['enabled'],
        'ready': channels[primary]['ready'],
        'channel': _LEGACY_CHANNEL_NAME.get(primary, primary),
        'missing_count': len(channels[primary]['missing']),
    }
    if not is_admin:
        # 业务员：不暴露回调地址/缺失项明细，只告诉「能不能充」和「缺几项」
        return success_response(payload)

    payload.update({
        'missing': channels[primary]['missing'],
        'daily_cap_points': pay_service.daily_cap_points(),
        'orders_per_minute': pay_service.ORDERS_PER_MINUTE,
        'callback_ip_mode': wxpay_service.config()['callback_ip_mode'],
        'callback_ips_configured': len(wxpay_service.config()['callback_ips']),
        'notify_url': channels[primary]['notify_url'],
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


@pay_bp.route('/pay/alipay/notify', methods=['POST'])
def alipay_notify_callback():
    """支付宝异步通知（唯一入口）。

    应答必须是**纯文本** `success` / `fail`（返回 JSON 会被支付宝判定失败并重试 25 小时）。
    验签失败 → 401 fail；业务异常 → 200 success（避免无限重试，异常落 pay_events 供对账）。
    """
    raw = request.get_data() or b''
    if len(raw) > MAX_BODY:
        pay_service.log_event('-', 'alipay_notify_too_large', 'bytes=%d' % len(raw))
        return alipay_notify.FAIL_TEXT, 413, {'Content-Type': 'text/plain; charset=utf-8'}

    form = request.form.to_dict(flat=True)
    digest = pay_service.digest_of(raw)
    status, body, info = alipay_notify.handle_notify(form, digest)
    return body, status, {'Content-Type': 'text/plain; charset=utf-8'}
