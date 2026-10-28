"""签发 / 撤销 Agent 服务令牌（R1/R3）。

用法（在 backend 目录执行）：
    # 签发（token 只打印一次，库里只存 sha256(pepper+token)）
    .venv/bin/python scripts/create_agent_token.py --name agent-prod \
        --scopes products:write,cards:write,images:write,publish:write,gallery:read \
        --account-id 8

    # 可选：有效期（天）/ 绑定已有令牌到计费账号（R3 生图扣点用）
    .venv/bin/python scripts/create_agent_token.py --name agent-7d --scopes cards:write --expires-days 7
    .venv/bin/python scripts/create_agent_token.py --bind-account 1 --account-id 8

    # 列出（不含明文）/ 撤销
    .venv/bin/python scripts/create_agent_token.py --list
    .venv/bin/python scripts/create_agent_token.py --revoke 3

环境变量：AGENT_TOKEN_PEPPER 必须已配置（/etc/geekozon-erp.env）。
"""
import argparse
import os
import secrets
import sys
from datetime import datetime, timedelta

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db  # noqa: E402
from utils.agent_auth import hash_token, pepper_configured  # noqa: E402

ALL_SCOPES = [
    'products:read', 'products:write',
    'cards:write',
    'images:write',
    'gallery:read',
    'publish:read', 'publish:write',
    'categories:read',
    'agent:read',
]


def main():
    parser = argparse.ArgumentParser(description='签发/撤销 ERP Agent 服务令牌')
    parser.add_argument('--name', help='令牌名称（如 agent-prod）')
    parser.add_argument('--scopes', default='', help='逗号分隔，如 products:write,cards:write；* 表示全部')
    parser.add_argument('--expires-days', type=int, default=0, help='有效期（天），0/缺省=不过期')
    parser.add_argument('--account-id', type=int, default=0,
                        help='绑定计费账号（accounts.id）；生图套图扣点用，0=不绑定')
    parser.add_argument('--bind-account', type=int, default=0,
                        help='把已有令牌 id 绑定到 --account-id（R3 生图需要）')
    parser.add_argument('--revoke', type=int, default=0, help='撤销指定 id')
    parser.add_argument('--list', action='store_true', help='列出令牌（不含明文）')
    args = parser.parse_args()

    db.init_db()

    if args.list:
        rows = db.query(
            "SELECT id, name, scopes, account_id, created_at, expires_at, last_used_at, revoked "
            "FROM agent_tokens ORDER BY id"
        )
        if not rows:
            print('（暂无令牌）')
            return
        for row in rows:
            print(f"#{row['id']}  {row['name']}  scopes={row['scopes']}  "
                  f"account_id={row['account_id'] or '-'}  created={row['created_at']}  "
                  f"expires={row['expires_at'] or '-'}  last_used={row['last_used_at'] or '-'}  "
                  f"revoked={row['revoked']}")
        return

    if args.bind_account:
        if not args.account_id:
            print('错误：--bind-account 需要同时提供 --account-id')
            sys.exit(1)
        row = db.query("SELECT id, name FROM agent_tokens WHERE id = ?", (args.bind_account,), one=True)
        if not row:
            print(f'令牌 #{args.bind_account} 不存在')
            sys.exit(1)
        account = db.query("SELECT id, username, nickname FROM accounts WHERE id = ?",
                           (args.account_id,), one=True)
        if not account:
            print(f'账号 #{args.account_id} 不存在')
            sys.exit(1)
        db.execute("UPDATE agent_tokens SET account_id = ? WHERE id = ?",
                   (args.account_id, args.bind_account))
        print(f'已把令牌 #{row["id"]}（{row["name"]}）绑定到账号 #{account["id"]} '
              f'（{account.get("nickname") or account.get("username")}）')
        return

    if args.revoke:
        row = db.query("SELECT id, name FROM agent_tokens WHERE id = ?", (args.revoke,), one=True)
        if not row:
            print(f'令牌 #{args.revoke} 不存在')
            sys.exit(1)
        db.execute("UPDATE agent_tokens SET revoked = 1 WHERE id = ?", (args.revoke,))
        print(f'已撤销令牌 #{row["id"]}（{row["name"]}）')
        return

    if not args.name:
        parser.print_help()
        sys.exit(1)
    if not pepper_configured():
        print('错误：AGENT_TOKEN_PEPPER 未配置，无法签发令牌。请先在 /etc/geekozon-erp.env 配置。')
        sys.exit(1)

    scopes = [s.strip() for s in (args.scopes or '').split(',') if s.strip()]
    if not scopes:
        print('错误：--scopes 不能为空。可用：' + ','.join(ALL_SCOPES) + '（或 *）')
        sys.exit(1)
    invalid = [s for s in scopes if s not in ALL_SCOPES and s != '*']
    if invalid:
        print(f'错误：未知 scope：{invalid}。可用：{",".join(ALL_SCOPES)}（或 *）')
        sys.exit(1)

    account_id = args.account_id or None
    if account_id:
        account = db.query("SELECT id, username, nickname FROM accounts WHERE id = ?", (account_id,), one=True)
        if not account:
            print(f'错误：账号 #{account_id} 不存在')
            sys.exit(1)

    token = 'agent_' + secrets.token_urlsafe(32)
    token_hash = hash_token(token)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    expires_at = None
    if args.expires_days > 0:
        expires_at = (datetime.now() + timedelta(days=args.expires_days)).strftime('%Y-%m-%d %H:%M:%S')

    db.execute(
        "INSERT INTO agent_tokens (name, token_hash, scopes, created_at, expires_at, account_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (args.name, token_hash, ','.join(scopes), now, expires_at, account_id),
    )
    row = db.query("SELECT id FROM agent_tokens WHERE token_hash = ?", (token_hash,), one=True)
    print('令牌已创建（只显示一次，请立即保存到 ERP 服务器 env 或密钥管理）：')
    print(f'  id         = {row["id"]}')
    print(f'  name       = {args.name}')
    print(f'  scopes     = {",".join(scopes)}')
    print(f'  account_id = {account_id or "-（未绑定：R3 生图会拒绝，需 --bind-account 绑定）"}')
    print(f'  expires    = {expires_at or "永不过期"}')
    print(f'  token      = {token}')


if __name__ == '__main__':
    main()
