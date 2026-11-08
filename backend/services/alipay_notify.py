"""支付宝异步通知业务处理（A2）。

职责边界：**只做业务判定与落库**，不碰 Flask（路由在 routes/pay_routes.py），
便于用临时库 + 本地密钥做单测。

流程（与微信回调同口径，但验签/字段不同）：
  验签 → app_id / seller_id → trade_status 分支 → 订单定位 → channel 校验
  → 原文 digest 去重 → 金额校验（元→分必须严格相等）→ trade_no 一致性
  → mark_paid（status='created' 抢一次）→ credit_order（claim_credit 抢一次）

三层幂等（复用 pay_service 现有机制）：
  ① pay_orders.notify_digest 唯一索引（同一条报文只处理一次）
  ② mark_paid 的 WHERE status='created'（并发/重试只落一次 paid）
  ③ claim_credit 的 WHERE status='paid'（同一订单只加一次点）

应答口径（2026-11-08 重定，按 Chief 决策）：
  - 验签失败/缺参数、app_id/seller_id 不符 → 回 `fail`（让支付宝继续重试）
  - 订单不存在、金额/交易号不匹配 → 记事件 → 回 `fail`（不静默吞掉）
  - 已入账（credited_at 非空）→ 幂等 no-op，回 `success`（绝不重复加点）
  - `created` + 已付 → mark_paid → credit_order → 回 `success`
  - `closed` + 已付（关单竞态）→ **允许收敛**：closed→paid→credit，记
    `paid_on_closed_order_autocredited`（必须显眼，根因是关单时机太早）→ 回 `success`
理由：钱已经进了商家账户，判「给点」比判「不给点」更安全；但每次收敛都必须留痕。
"""
from services import alipay_service, pay_service

SUCCESS_TEXT = 'success'
FAIL_TEXT = 'fail'

PAID_STATUSES = ('TRADE_SUCCESS', 'TRADE_FINISHED')
CLOSED_STATUSES = ('TRADE_CLOSED',)


def _log(out_trade_no, event, detail, conn):
    try:
        pay_service.log_event(out_trade_no or '-', event, detail, conn=conn)
    except Exception:  # noqa: BLE001 - 审计写失败不能影响主流程
        pass


_SENSITIVE_KEYS = ('buyer_logon_id', 'buyer_open_id')


def _mask(value):
    s = str(value or '')
    if '@' in s:
        name, domain = s.split('@', 1)
        return '%s***@%s' % (name[:3], domain)
    return s[:3] + '***' + s[-2:] if len(s) > 5 else '***'


def _snapshot(form, digest, conn):
    """验签前落一条完整字段快照（`sign` 保留原值，供重放/复盘；仅打码买家标识）。

    只进 pay_events，不进 journal / git。限长 2000 字符（列约束），超长标注 truncated。
    """
    parts = []
    for key in sorted(form):
        value = str(form.get(key) or '')
        if key in _SENSITIVE_KEYS:
            value = _mask(value)
        parts.append('%s=%s' % (key, value))
    text = '&'.join(parts)
    if len(text) > 1880:
        text = text[:1880] + '...truncated'
    _log(form.get('out_trade_no') or '-', 'alipay_notify_body',
         'digest=%s|%s' % (str(digest or '')[:12], text), conn)


def handle_notify(form, digest, conn=None, cfg=None, verify=None, adjust=None):
    """处理一条支付宝异步通知。

    参数：
      form   : 回调表单（dict，含 sign/sign_type）
      digest : 回调原文的 sha256（pay_service.digest_of(raw)）
      conn   : sqlite 连接（单测传临时库；None 走应用 db）
      cfg    : alipay_service.config() 的结果（可注入）
      verify : 验签函数（可注入，默认 alipay_service.verify_notify）
      adjust : 入账函数（可注入，默认走 image_billing_service.adjust_balance）

    返回：(http_status, body_text, info)
    """
    verify = verify or alipay_service.verify_notify
    cfg = cfg or alipay_service.config()
    info = {'event': '', 'out_trade_no': '', 'credited': False}

    # 0) 请求体快照（验签之前，字段全留；sign 保留原值，买家标识打码）
    _snapshot(form, digest, conn)

    # 1) 验签（唯一信任入口）
    try:
        ok, reason = verify(form)
    except Exception as exc:  # noqa: BLE001 - 缺密钥/文件损坏等
        _log('-', 'alipay_notify_verify_error', str(exc)[:200], conn)
        info['event'] = 'verify_error'
        return 503, FAIL_TEXT, info
    if not ok:
        _log('-', 'alipay_notify_bad_sig', reason, conn)
        info['event'] = 'bad_sig'
        return 401, FAIL_TEXT, info

    # 2) 身份校验：app_id / seller_id（防串应用、串商户）
    app_id = str(form.get('app_id') or '')
    if cfg.get('app_id') and app_id != cfg['app_id']:
        _log(form.get('out_trade_no'), 'alipay_notify_bad_appid',
             'got_len=%d' % len(app_id), conn)
        info['event'] = 'bad_appid'
        return 403, FAIL_TEXT, info
    seller_id = str(form.get('seller_id') or '')
    if cfg.get('seller_id') and seller_id and seller_id != cfg['seller_id']:
        _log(form.get('out_trade_no'), 'alipay_notify_bad_seller', '', conn)
        info['event'] = 'bad_seller'
        return 403, FAIL_TEXT, info

    out_trade_no = str(form.get('out_trade_no') or '').strip()
    info['out_trade_no'] = out_trade_no
    trade_status = (str(form.get('trade_status') or '')).upper()

    order = pay_service.get_order(out_trade_no, conn=conn) if out_trade_no else None
    if not order:
        _log(out_trade_no, 'alipay_notify_unknown_order', trade_status, conn)
        info['event'] = 'unknown_order'
        return 200, FAIL_TEXT, info      # 可能是建单竞态：回 fail 让支付宝重试

    if (order.get('channel') or '') != 'alipay':
        _log(out_trade_no, 'alipay_notify_channel_mismatch',
             'channel=%s' % order.get('channel'), conn)
        info['event'] = 'channel_mismatch'
        return 200, SUCCESS_TEXT, info

    # 幂等第一优先：已入账 → no-op success，绝不重复加点（第二、三次通知走这里）
    if order.get('credited_at'):
        _log(out_trade_no, 'alipay_notify_already_credited',
             'credited_at=%s trade_status=%s' % (order['credited_at'], trade_status), conn)
        info['event'] = 'already_credited'
        info['credited'] = True
        return 200, SUCCESS_TEXT, info

    # 3) 状态分支
    if trade_status in CLOSED_STATUSES:
        closed = pay_service.close_order(out_trade_no, conn=conn)
        _log(out_trade_no, 'alipay_notify_closed', 'closed_rows=%s' % closed, conn)
        info['event'] = 'closed'
        return 200, SUCCESS_TEXT, info
    if trade_status not in PAID_STATUSES:
        _log(out_trade_no, 'alipay_notify_state', trade_status, conn)
        info['event'] = 'state:%s' % trade_status
        return 200, SUCCESS_TEXT, info

    # 4) 原文去重（第一层幂等）
    if pay_service.digest_seen(out_trade_no, digest, conn=conn):
        _log(out_trade_no, 'alipay_notify_duplicate', str(form.get('notify_id') or ''), conn)
        info['event'] = 'duplicate'
        info['credited'] = order.get('status') == 'credited'
        return 200, SUCCESS_TEXT, info

    # 5) 金额校验（元 → 分，严格相等）
    try:
        paid_cents = alipay_service.amount_to_cents(form.get('total_amount'))
    except ValueError as exc:
        _log(out_trade_no, 'alipay_notify_bad_amount_format', str(exc)[:120], conn)
        info['event'] = 'bad_amount_format'
        return 200, FAIL_TEXT, info
    if int(paid_cents) != int(order.get('amount_cents') or 0):
        _log(out_trade_no, 'alipay_notify_bad_amount',
             'paid=%s order=%s' % (paid_cents, order.get('amount_cents')), conn)
        info['event'] = 'bad_amount'
        return 200, FAIL_TEXT, info

    # 6) trade_no 一致性：同一订单已被别的支付宝交易号付过，不覆盖、不重复入账
    trade_no = str(form.get('trade_no') or '')
    if order.get('transaction_id') and trade_no and trade_no != order['transaction_id']:
        _log(out_trade_no, 'alipay_notify_trade_no_mismatch',
             'order_trade_no_len=%d' % len(str(order['transaction_id'])), conn)
        info['event'] = 'trade_no_mismatch'
        return 200, FAIL_TEXT, info

    # 7) 置为已支付
    #    created：正常迁移（第二层幂等，只从 created 迁一次）
    #    closed ：**关单竞态收敛**——钱已经付了，必须入账，但要响亮留痕（Chief 2026-11-08 定）
    if order.get('status') == 'created':
        try:
            pay_service.mark_paid(out_trade_no, trade_no,
                                  notify_id=str(form.get('notify_id') or ''),
                                  notify_digest=digest,
                                  notify_raw='&'.join('%s=%s' % (k, v) for k, v in form.items())[:2000],
                                  conn=conn)
        except Exception as exc:  # noqa: BLE001 - 唯一索引冲突=重复/并发
            _log(out_trade_no, 'alipay_notify_mark_paid_dup', str(exc)[:200], conn)
    elif order.get('status') == 'closed':
        rows = pay_service.mark_paid_from_closed(
            out_trade_no, trade_no, notify_id=str(form.get('notify_id') or ''),
            notify_digest=digest, notify_raw='closed_converge', conn=conn)
        _log(out_trade_no, 'paid_on_closed_order_autocredited',
             'reason=local_closed_before_notify(关单竞态窗口) rows=%s trade_no=%s amount=%s pay_at=%s'
             % (rows, trade_no, form.get('total_amount'),
                form.get('gmt_payment') or form.get('send_pay_date')), conn)

    # 8) 入账（第三层幂等：claim_credit 只成功一次）
    result = pay_service.credit_order(out_trade_no, conn=conn, adjust=adjust)
    info['credited'] = bool(result.get('ok'))
    info['event'] = 'notify_ok'
    _log(out_trade_no, 'alipay_notify_ok',
         'credit=%s points=%s' % (result.get('reason'), result.get('points')), conn)
    return 200, SUCCESS_TEXT, info
