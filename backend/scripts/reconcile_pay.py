#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信支付充值对账脚本（每小时跑一次）。

做三件事：
1. 把「已支付但未入账」（paid）的订单补入账——只处理回调已验签确认支付的单，安全；
2. 关掉超时未支付的 created 单（本地关单，不重复调支付接口）；
3. 打印统计，异常退出码 1 供 cron 告警。

用法：
    /srv/<app>/backend/.venv/bin/python /srv/<app>/backend/scripts/reconcile_pay.py [--dry-run]
"""
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)


def _load_env_file(path, override=False):
    if not os.path.exists(path):
        return 0
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except PermissionError:
        return 0
    loaded = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if override or key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def _load_env():
    """按「cron 用户可读」优先加载：
    1. 支付宝专用配置 `/etc/geekozon-erp-pay/alipay/pay.env`（600 geekozon，cron 读得到）
    2. 微信专用配置 `/etc/geekozon-erp/wechatpay/pay.env`
    3. 主 env `/etc/geekozon-erp.env`（600 root，cron 用户读不到就跳过）

    2026-11-09 修：此前 cron 以 geekozon 跑，读不到 root-only 主 env，支付宝查单缺 app_id，
    网关回「响应缺少 sign」，对账的支付宝路径实际从未工作过。
    """
    _load_env_file('/etc/geekozon-erp-pay/alipay/pay.env', override=True)
    _load_env_file('/etc/geekozon-erp/wechatpay/pay.env', override=True)
    _load_env_file('/etc/geekozon-erp.env')


def main():
    _load_env()
    dry = '--dry-run' in sys.argv
    from services import pay_service, wxpay_service  # noqa: E402

    pay_service.ensure_tables()
    cfg = wxpay_service.config()
    print('[reconcile] enabled=%s missing=%s dry_run=%s' % (
        cfg['enabled'], ','.join(wxpay_service.missing_config()) or 'none', dry))

    expired = 0 if dry else pay_service.expire_created()
    print('[reconcile] expired created orders = %s' % expired)

    pending = pay_service.pending_credit(limit=200)
    print('[reconcile] pending credit = %d' % len(pending))
    failed = 0
    for row in pending:
        no = row['out_trade_no']
        if dry:
            print('  would credit %s (%.2f points, account %s)' % (no, row['points'], row['account_id']))
            continue
        result = pay_service.credit_order(no)
        ok = result.get('ok')
        if not ok:
            failed += 1
        print('  credit %s -> %s' % (no, result.get('reason')))
        pay_service.log_event(no, 'reconcile', str(result.get('reason')))

    # 断线补偿：本地 closed 但远端已 TRADE_SUCCESS（先关单后付款 / 通知全丢）
    try:
        from services import pay_reconcile
        conv = {'checked': 0, 'credited': 0, 'skipped': 0, 'failed': 0}
        if not dry:
            conv = pay_reconcile.reconcile_closed_paid(conn=None, adjust=None)
        print('[reconcile] closed_paid checked=%s credited=%s skipped=%s failed=%s'
              % (conv.get('checked'), conv.get('credited'), conv.get('skipped'), conv.get('failed')))
        failed += int(conv.get('failed') or 0)
    except Exception as exc:  # noqa: BLE001 - 补偿失败不能中断其它对账
        print('[reconcile][ERROR] closed_paid failed: %s: %s' % (type(exc).__name__, str(exc)[:150]))
        failed += 1

    # 自检：标了已入账但没有 ledger 流水（2026-11-08 事故形态）→ ERROR 日志 + 落事件
    hits = pay_service.audit_credited_without_ledger()
    print('[reconcile] credited_without_ledger = %d' % len(hits))
    for h in hits:
        print('[reconcile][ERROR] credited without ledger: %s channel=%s points=%s credited_at=%s'
              % (h['out_trade_no'], h['channel'], h['points'], h['credited_at']))
        if not dry:
            pay_service.log_event(h['out_trade_no'], 'recon_credited_without_ledger',
                                  'channel=%s points=%s credited_at=%s'
                                  % (h['channel'], h['points'], h['credited_at']))

    print('[reconcile] stats =', pay_service.stats())
    if failed:
        print('[reconcile] FAILED=%d' % failed)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
