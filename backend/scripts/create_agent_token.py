"""签发 / 撤销 Agent 服务令牌（R1）。

用法（在 backend 目录执行）：
    # 签发（token 只打印一次，库里只存 sha256(pepper+token)）
    .venv/bin/python scripts/create_agent_token.py --name agent-prod \
        --scopes products:write,cards:write,images:write,publish:write,gallery:read

    # 可选：指定有效期（天）
    .venv/bin/python scripts/create_agent_token.py --name agent-7d --scopes cards:write --expires-days 7

    # 列出（不含明文）
    .venv/bin/python scripts/create_agent_token.py --list

    # 撤销
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
    parser.add_argument('--revoke', type=int, default=0, help='撤销指定 id')
    parser.add_argument('--list', action='store_true', help='列出令牌（不含明文）')
    args = parser.parse_args()

    db.init_db()

    if args.list:
        rows = db.query(
            "SELECT id, name, scopes, created_at, expires_at, last_used_at, revoked "
            "FROM agent_tokens ORDER BY id"
        )
        if not rows:
            print('（暂无令牌）')
            return
        for row in rows:
            print(f"#{row['id']}  {row['name']}  scopes={row['scopes']}  "
                  f"created={row['created_at']}  expires={row['expires_at'] or '-'}  "
                  f"last_used={row['last_used_at'] or '-'}  revoked={row['revoked']}")
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

    token = 'agent_' + secrets.token_urlsafe(32)
    token_hash = hash_token(token)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    expires_at = None
    if args.expires_days > 0:
        expires_at = (datetime.now() + timedelta(days=args.expires_days)).strftime('%Y-%m-%d %H:%M:%S')

    db.execute(
        "INSERT INTO agent_tokens (name, token_hash, scopes, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (args.name, token_hash, ','.join(scopes), now, expires_at),
    )
    row = db.query("SELECT id FROM agent_tokens WHERE token_hash = ?", (token_hash,), one=True)
    print('令牌已创建（只显示一次，请立即保存到 ERP 服务器 env 或密钥管理）：')
    print(f'  id      = {row["id"]}')
    print(f'  name    = {args.name}')
    print(f'  scopes  = {",".join(scopes)}')
    print(f'  expires = {expires_at or "永不过期"}')
    print(f'  token   = {token}')


if __name__ == '__main__':
    main()
