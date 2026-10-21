# -*- coding: utf-8 -*-
"""首次部署引导：创建第一个管理员账号。

用法（在 backend 目录下执行）：
    python scripts/bootstrap_admin.py                    # 交互式输入
    python scripts/bootstrap_admin.py admin 密码123       # 直接指定
    python scripts/bootstrap_admin.py --list              # 仅查看现有账号

说明：
- 仅当数据库中还没有任何 admin 角色账号时才允许创建，避免误加管理员。
- 若已存在管理员而确实需要再加一个，用 --force 显式覆盖该保护。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import init_db, query           # noqa: E402
from models.account import Account      # noqa: E402


def list_accounts():
    rows = query("SELECT id, username, nickname, role, status FROM accounts ORDER BY id")
    if not rows:
        print('(当前没有任何账号)')
        return []
    print('%-4s %-16s %-16s %-12s %s' % ('ID', '用户名', '昵称', '角色', '状态'))
    for r in rows:
        print('%-4s %-16s %-16s %-12s %s' % (
            r['id'], r['username'], r['nickname'] or '', r['role'], r['status']))
    return rows


def main():
    init_db()

    if '--list' in sys.argv:
        list_accounts()
        return 0

    force = '--force' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('--')]

    existing_admins = query("SELECT id, username FROM accounts WHERE role = 'admin'")
    if existing_admins and not force:
        print('已存在管理员账号：%s' % ', '.join(a['username'] for a in existing_admins))
        print('如需强制新增，请加 --force 参数。')
        list_accounts()
        return 1

    if len(args) >= 2:
        username, password = args[0].strip(), args[1]
        nickname = args[2].strip() if len(args) >= 3 else username
    else:
        username = input('管理员用户名: ').strip()
        password = input('管理员密码（至少 6 位）: ')
        nickname = (input('显示昵称（留空同用户名）: ').strip() or username)

    if not username:
        print('错误：用户名不能为空')
        return 2
    if len(password) < 6:
        print('错误：密码至少 6 位')
        return 2
    if Account.find_by_username(username):
        print('错误：用户名 %s 已存在' % username)
        return 2

    Account.create(username=username, password=password, nickname=nickname, role='admin')
    print('已创建管理员账号：%s' % username)
    print('请登录后到「系统设置 -> 用户管理」创建业务员账号并绑定店铺。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
