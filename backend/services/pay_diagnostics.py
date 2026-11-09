# -*- coding: utf-8 -*-
"""支付失败只读诊断（2026-11-09）。

回答一句话：**最近 N 小时有没有入账失败、失败几次、哪几单**。

只读 `pay_events` / `pay_orders`，不碰收款路径、不写库。
把「探针自己打的假失败」单列（`alipay_notify_bad_sig` + `missing_sign` /
`missing_signature_headers`）：那是外部工具/我们自己的无签名探针，不该算进真实失败，
否则每次看报表都被自己的测试吓一次。
"""
from services import pay_service

DEFAULT_HOURS = 24

# 归到「真实失败」的事件 → 分类名
FAILURE_CLASSES = {
    'notify_verify_error': 'verify_error',
    'notify_bad_sig': 'bad_sig',
    'alipay_notify_bad_sig': 'bad_sig',
    'notify_bad_amount': 'amount_mismatch',
    'notify_bad_amount_format': 'amount_mismatch',
    'alipay_notify_bad_amount': 'amount_mismatch',
    'alipay_notify_bad_amount_format': 'amount_mismatch',
    'notify_unknown_order': 'unknown_order',
    'alipay_notify_unknown_order': 'unknown_order',
    'notify_channel_mismatch': 'channel_mismatch',
    'alipay_notify_channel_mismatch': 'channel_mismatch',
    'notify_state': 'trade_state',
    'close_fail': 'close_fail',
    'credit_fail': 'credit_fail',
    'recon_query_fail': 'recon_query_fail',
    'recon_closed_query_fail': 'recon_query_fail',
    'recon_bad_amount_format': 'recon_amount',
    'recon_amount_mismatch': 'recon_amount',
    'recon_credit_fail': 'credit_fail',
    'recon_closed_credit_fail': 'credit_fail',
    'recon_credited_without_ledger': 'ledger_missing',
    'refund_fail': 'refund_fail',
    'refund_transport': 'refund_fail',
    'refund_error': 'refund_fail',
}

# 探针/畸形请求：只免登录探针会打这些，不算真实失败
PROBE_REASONS = ('missing_sign', 'missing_signature_headers', 'missing_sign_type')


def failure_summary(hours=DEFAULT_HOURS, conn=None, now_ts=None):
    """返回最近 N 小时的失败汇总（只读）。

    结构：
      window      : {'hours': N, 'since': 'YYYY-MM-DD HH:MM:SS'(UTC)}
      total       : 真实失败条数（已排除探针）
      by_class    : {分类: 条数}
      orders      : [{out_trade_no, count, classes, last_at}]（按最近时间倒序）
      probes      : {'count': n, 'reasons': [...]}（探针假失败，单独列）
      samples     : [最近 5 条失败原文（事件/时间/单号/详情前缀）]
      verdict     : 'ok' / 'has_failure'
    """
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc) if now_ts is None else \
        datetime.datetime.fromtimestamp(now_ts, datetime.timezone.utc)
    since = (now - datetime.timedelta(hours=int(hours))).strftime('%Y-%m-%d %H:%M:%S')

    rows = pay_service._fetch(
        conn,
        "SELECT id, out_trade_no, event, detail, created_at FROM pay_events "
        "WHERE created_at >= ? ORDER BY id DESC",
        (since,), one=False) or []

    by_class = {}
    orders = {}
    probes = []
    samples = []
    total = 0
    for r in rows:
        cls = FAILURE_CLASSES.get(r['event'])
        if not cls:
            continue
        reason = (r['detail'] or '')
        if r['event'].endswith('bad_sig') and any(p in reason for p in PROBE_REASONS):
            probes.append({'at': r['created_at'], 'event': r['event'], 'reason': reason[:40]})
            continue
        total += 1
        by_class[cls] = by_class.get(cls, 0) + 1
        no = r['out_trade_no'] or '-'
        item = orders.setdefault(no, {'out_trade_no': no, 'count': 0, 'classes': set(),
                                      'last_at': r['created_at']})
        item['count'] += 1
        item['classes'].add(cls)
        if r['created_at'] > item['last_at']:
            item['last_at'] = r['created_at']
        if len(samples) < 5:
            samples.append({'id': r['id'], 'at': r['created_at'], 'event': r['event'],
                            'out_trade_no': no, 'detail': reason[:120]})

    order_list = sorted(orders.values(), key=lambda x: x['last_at'], reverse=True)
    for it in order_list:
        it['classes'] = sorted(it['classes'])

    return {
        'window': {'hours': int(hours), 'since': since},
        'total': total,
        'by_class': by_class,
        'orders': order_list,
        'probes': {'count': len(probes), 'reasons': sorted({p['reason'] for p in probes})},
        'samples': samples,
        'verdict': 'ok' if total == 0 else 'has_failure',
    }


def one_line(summary):
    """一行摘要，给日志/日报用。"""
    w = summary['window']
    if summary['total'] == 0:
        base = '最近 %dh 无入账失败' % w['hours']
    else:
        base = '最近 %dh 入账失败 %d 次（%s），涉及 %d 单' % (
            w['hours'], summary['total'],
            '、'.join('%s×%d' % (k, v) for k, v in sorted(summary['by_class'].items())),
            len(summary['orders']))
    if summary['probes']['count']:
        base += '；另有 %d 条探针/畸形请求已排除（%s）' % (
            summary['probes']['count'], '、'.join(summary['probes']['reasons']) or '-')
    return base
