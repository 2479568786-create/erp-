# -*- coding: utf-8 -*-
"""R1 落地自检：表/列/守卫注册/令牌签发脚本可运行。"""
import os
import sys

sys.path.insert(0, os.getcwd())

os.environ.setdefault('AGENT_TOKEN_PEPPER', 'selfcheck-pepper')

import app as app_module  # noqa: E402
import db  # noqa: E402

application = app_module.create_app()
rules = len(list(application.url_map.iter_rules()))
print('ROUTES =', rules)

tables = {r['name'] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
print('agent_tokens =', 'agent_tokens' in tables, '| agent_ops =', 'agent_ops' in tables)
cols = [r['name'] for r in db.query('PRAGMA table_info(gallery_assets)')]
print('gallery 新列 =', [c for c in ('product_id', 'module_key', 'request_id', 'agent_source') if c in cols])

# 签发脚本可执行（不打印 token，只测 --list）
import subprocess  # noqa: E402
out = subprocess.run([sys.executable, 'scripts/create_agent_token.py', '--list'],
                     capture_output=True, text=True, env=dict(os.environ))
print('create_agent_token --list exit =', out.returncode)
print('list stdout head =', (out.stdout or '').strip()[:120])
if out.returncode != 0:
    print('stderr:', (out.stderr or '')[:300])
