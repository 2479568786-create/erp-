#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信支付充值对账脚本（每小时跑一次）。

做三件事：
1. 把「已支付但未入账」（paid）的订单补入账——只处理回调已验签确认支付的单，安全；
2. 关掉超时未支付的 created 单（本地关单，不重复调支付接口）；
3. 打印统计，异常退出码 1 供 cron 告警。

用法：
    /srv/geekozon-erp/backend/.venv/bin/python /srv/geekozon-erp/backend/scripts/reconcile_pay.py [--dry-run]
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
    """先读专用支付配置（geekozon 可读），再读主 env（root 600 时读不到就跳过）。"""
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

    print('[reconcile] stats =', pay_service.stats())
    if failed:
        print('[reconcile] FAILED=%d' % failed)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
