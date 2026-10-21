# -*- coding: utf-8 -*-
"""检查店群/绑定相关数据表当前状态。"""
import os, sqlite3, sys, json

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'geekozon.db')
print('DB:', DB, 'exists=', os.path.exists(DB))
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r['name'] for r in cur.fetchall()]
print('TABLES(%d):' % len(tables), ', '.join(tables))

for t in ('accounts', 'stores'):
    if t in tables:
        cur.execute('SELECT COUNT(*) c FROM ' + t)
        print('%s count = %d' % (t, cur.fetchone()['c']))
    else:
        print('%s MISSING' % t)

if 'accounts' in tables:
    cur.execute('SELECT id, username, nickname, role, status FROM accounts ORDER BY id')
    rows = cur.fetchall()
    print('--- accounts ---')
    for r in rows:
        print(dict(r))
    if not rows:
        print('(no accounts -> 需要引导创建管理员)')

if 'stores' in tables:
    cur.execute('SELECT id, store_id, alias, store_group, account_id FROM stores ORDER BY id')
    for r in cur.fetchall():
        print('store:', dict(r))

conn.close()
