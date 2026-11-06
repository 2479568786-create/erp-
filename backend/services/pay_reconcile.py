"""支付宝对账补偿（A4）：回调丢了也能把已付的订单补进账。

两类补偿：
  A) 订单 `paid` 但未入账（入账时崩了/余额服务抖动）→ 直接重试入账，不用查渠道；
  B) 订单 `created` 且超过 N 秒（回调可能丢）→ `alipay.trade.query` 主动查单：
     已支付且金额一致 → mark_paid + 入账；未支付 → 留到下次；金额不一致 → 只记事件不入账。

设计约束：
- 只处理 `channel='alipay'` 的订单（微信有自己的补偿/回调，不混）；
- 查单异常不影响其它订单（逐笔 try，汇总计数）；
- 补偿置 paid 时用 `recon:<trade_no>` 当 notify_digest，靠唯一索引防并发重复；
- 不写任何密钥相关内容到日志。
"""
from services import alipay_service, pay_service

PAID_STATUSES = ('TRADE_SUCCESS', 'TRADE_FINISHED')
DEFAULT_MIN_AGE_SECONDS = 120


def _log(out_trade_no, event, detail, conn):
    try:
        pay_service.log_event(out_trade_no or '-', event, detail, conn=conn)
    except Exception:  # noqa: BLE001
        pass


def retry_pending_credits(conn=None, adjust=None, limit=50):
    """A 类：paid 但未入账 → 重试入账。"""
    summary = {'checked': 0, 'credited': 0, 'skipped': 0, 'failed': 0, 'details': []}
    for row in pay_service.pending_credit(conn=conn, limit=limit):
        out_trade_no = row['out_trade_no']
        summary['checked'] += 1
        result = pay_service.credit_order(out_trade_no, conn=conn, adjust=adjust)
        if result.get('ok') and result.get('reason') == 'credited':
            summary['credited'] += 1
            summary['details'].append({'out_trade_no': out_trade_no, 'action': 'credit_retried'})
        elif result.get('ok'):
            summary['skipped'] += 1
            summary['details'].append({'out_trade_no': out_trade_no,
                                       'action': 'skip:%s' % result.get('reason')})
        else:
            summary['failed'] += 1
            summary['details'].append({'out_trade_no': out_trade_no,
                                       'action': 'fail:%s' % result.get('reason')})
            _log(out_trade_no, 'recon_credit_fail', str(result.get('reason')), conn)
    return summary


def reconcile_stale_created(conn=None, query=None, adjust=None, limit=50,
                            min_age_seconds=DEFAULT_MIN_AGE_SECONDS, now_ts=None,
                            channel='alipay'):
    """B 类：created 且超时 → 查单，已支付则补 paid + 入账。"""
    query = query or alipay_service.query_trade
    summary = {'checked': 0, 'credited': 0, 'skipped': 0, 'failed': 0, 'details': []}
    orders = pay_service.stale_created(channel=channel, older_than_seconds=min_age_seconds,
                                       limit=limit, conn=conn, now_ts=now_ts)
    for order in orders:
        out_trade_no = order['out_trade_no']
        summary['checked'] += 1
        try:
            payload = query(out_trade_no)
        except Exception as exc:  # noqa: BLE001 - 网络/验签失败不该中断整批
            summary['failed'] += 1
            summary['details'].append({'out_trade_no': out_trade_no, 'action': 'query_fail'})
            _log(out_trade_no, 'recon_query_fail', str(exc)[:200], conn)
            continue

        code = str(payload.get('code') or '')
        if code and code != '10000':
            summary['skipped'] += 1
            summary['details'].append({'out_trade_no': out_trade_no,
                                       'action': 'biz_error:%s' % (payload.get('sub_code') or code)})
            _log(out_trade_no, 'recon_query_biz_error',
                 '%s/%s' % (code, payload.get('sub_code') or ''), conn)
            continue

        trade_status = str(payload.get('trade_status') or '').upper()
        if trade_status not in PAID_STATUSES:
            summary['skipped'] += 1
            summary['details'].append({'out_trade_no': out_trade_no,
                                       'action': 'not_paid:%s' % (trade_status or '-')})
            continue

        # 金额必须严格一致才入账（单位：元 → 分）
        try:
            paid_cents = alipay_service.amount_to_cents(payload.get('total_amount'))
        except ValueError as exc:
            summary['failed'] += 1
            summary['details'].append({'out_trade_no': out_trade_no, 'action': 'bad_amount_format'})
            _log(out_trade_no, 'recon_bad_amount_format', str(exc)[:120], conn)
            continue
        if int(paid_cents) != int(order['amount_cents']):
            summary['skipped'] += 1
            summary['details'].append({'out_trade_no': out_trade_no, 'action': 'amount_mismatch'})
            _log(out_trade_no, 'recon_amount_mismatch',
                 'paid=%s order=%s' % (paid_cents, order['amount_cents']), conn)
            continue

        trade_no = str(payload.get('trade_no') or '')
        digest = 'recon:%s' % (trade_no or out_trade_no)
        if order.get('status') == 'created':
            try:
                pay_service.mark_paid(out_trade_no, trade_no, notify_id='',
                                      notify_digest=digest,
                                      notify_raw='reconciled', conn=conn)
            except Exception as exc:  # noqa: BLE001 - 唯一索引冲突=已被并发补过
                _log(out_trade_no, 'recon_mark_paid_dup', str(exc)[:200], conn)
        result = pay_service.credit_order(out_trade_no, conn=conn, adjust=adjust)
        if result.get('ok'):
            summary['credited'] += 1
            summary['details'].append({'out_trade_no': out_trade_no,
                                       'action': 'reconciled:%s' % result.get('reason')})
            _log(out_trade_no, 'recon_reconciled', result.get('reason') or '', conn)
        else:
            summary['failed'] += 1
            summary['details'].append({'out_trade_no': out_trade_no,
                                       'action': 'credit_fail:%s' % result.get('reason')})
            _log(out_trade_no, 'recon_credit_fail', str(result.get('reason')), conn)
    return summary


def reconcile(conn=None, query=None, adjust=None, limit=50,
              min_age_seconds=DEFAULT_MIN_AGE_SECONDS, now_ts=None):
    """一次完整对账：先补入账、再查单补偿。返回合并后的汇总。"""
    a = retry_pending_credits(conn=conn, adjust=adjust, limit=limit)
    b = reconcile_stale_created(conn=conn, query=query, adjust=adjust, limit=limit,
                                min_age_seconds=min_age_seconds, now_ts=now_ts)
    return {
        'checked': a['checked'] + b['checked'],
        'credited': a['credited'] + b['credited'],
        'skipped': a['skipped'] + b['skipped'],
        'failed': a['failed'] + b['failed'],
        'credit_retried': a['credited'],
        'queried': b['checked'],
        'details': a['details'] + b['details'],
    }


def main():  # pragma: no cover - 手工/定时任务入口
    """python -m services.pay_reconcile —— 在应用上下文里跑一次对账。"""
    import json
    import app as app_module

    application = app_module.create_app()
    with application.app_context():
        pay_service.ensure_tables()
        print(json.dumps(reconcile(), ensure_ascii=False, indent=2))


if __name__ == '__main__':  # pragma: no cover
    main()
