# -*- coding: utf-8 -*-
"""端到端验证：店群管理（批量绑定/转移/解绑/总览/审计）。"""
import json
import sys
import urllib.request
import urllib.error

BASE = 'http://127.0.0.1:5000'


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {'raw': raw}


ok = True


def check(label, cond, detail=''):
    global ok
    if not cond:
        ok = False
    print('[%s] %s %s' % ('PASS' if cond else 'FAIL', label, detail))


st, res = call('POST', '/api/auth/login', {'username': 'admin', 'password': 'Admin@123456'})
adm = res['data']['token']
print('管理员登录 OK')

# 前置重置：全部解绑，保证用例可重复执行
call('POST', '/api/store-assignment/unbind', {'all': True}, adm)

st, res = call('GET', '/api/store-assignment/members', token=adm)
members = res['data']['list']
print('\n=== 成员列表 ===')
for m in members:
    print('   ', m['id'], m['username'], m['role'], 'storeCount=', m['storeCount'])
check('members 接口可用', res['code'] == 200 and len(members) >= 2)
ua = next(m for m in members if m['username'] == 'sales_a')
ub = next(m for m in members if m['username'] == 'sales_b')

print('\n=== 初始总览 ===')
st, res = call('GET', '/api/store-assignment/overview', token=adm)
ov = res['data']
print('   totals:', ov['totals'])
for m in ov['members']:
    print('   member:', m['username'], 'stores=', m['storeCount'], 'groups=', m['groups'])
print('   unassigned:', [(s['id'], s['alias']) for s in ov['unassigned']])
print('   groups:', ov['groups'])
check('overview 结构完整', all(k in ov for k in ('totals', 'members', 'unassigned', 'groups')))

GRP = ov['groups'][0]['name'] if ov['groups'] else '默认'
print('\n=== 批量绑定：按分组「%s」绑定全部店铺 -> sales_a ===' % GRP)
st, res = call('POST', '/api/store-assignment/bind',
               {'accountId': ua['id'], 'group': GRP, 'mode': 'add'}, adm)
print('   ->', res.get('code'), res.get('msg'))
check('按分组批量绑定成功', res['code'] == 200, res.get('msg'))
if res['code'] != 200:
    print('无法继续，终止'); sys.exit(1)
cnt = res['data']['counts']
print('   counts:', cnt)

st, res = call('GET', '/api/users/%d/stores' % ua['id'], token=adm)
owned_a = res['data']['list']
check('sales_a 名下店铺数 == 分组店铺数', len(owned_a) == cnt['bound'] + cnt['transferred'] + cnt.get('skipped', 0),
      'owned=%d' % len(owned_a))

print('\n=== 幂等：重复绑定同一批 ===')
st, res = call('POST', '/api/store-assignment/bind',
               {'accountId': ua['id'], 'group': GRP, 'mode': 'add'}, adm)
check('重复绑定全部幂等跳过', res['data']['counts']['bound'] == 0 and res['data']['counts']['transferred'] == 0,
      str(res['data']['counts']))

print('\n=== 转移：把分组店铺转给 sales_b ===')
st, res = call('POST', '/api/store-assignment/bind',
               {'accountId': ub['id'], 'group': GRP, 'mode': 'add', 'remark': '交接给乙'}, adm)
print('   ->', res.get('code'), res.get('msg'))
check('转移成功且记录 fromName', res['code'] == 200 and res['data']['counts']['transferred'] >= 1,
      str(res['data']['counts']))
print('   transferred:', [(t['alias'], t.get('fromName')) for t in res['data']['transferred']])

st, res = call('GET', '/api/users/%d/stores' % ua['id'], token=adm)
check('转移后 sales_a 名下为 0', len(res['data']['list']) == 0, 'owned=%d' % len(res['data']['list']))

print('\n=== replace 模式：仅保留指定店铺 ===')
allpks = [s['id'] for s in ov['unassigned']] + [s['id'] for m in ov['members'] for s in m['stores']]
keep = allpks[:1]
st, res = call('POST', '/api/store-assignment/bind',
               {'accountId': ub['id'], 'storePks': keep, 'mode': 'replace'}, adm)
print('   ->', res.get('code'), res.get('msg'), res['data']['counts'])
check('replace 释放其余店铺', res['data']['counts']['released'] >= 1, str(res['data']['counts']))
st, res = call('GET', '/api/users/%d/stores' % ub['id'], token=adm)
check('replace 后 sales_b 仅剩 1 家', len(res['data']['list']) == 1, 'owned=%d' % len(res['data']['list']))

print('\n=== 错误处理 ===')
st, res = call('POST', '/api/store-assignment/bind', {'accountId': 1, 'all': True}, adm)
check('禁止绑定给管理员', res['code'] == 400, res.get('msg'))
st, res = call('POST', '/api/store-assignment/bind', {'all': True}, adm)
check('缺少 accountId 被拒', res['code'] == 400, res.get('msg'))
st, res = call('POST', '/api/store-assignment/bind', {'accountId': ub['id']}, adm)
check('未指定店铺被拒', res['code'] == 400, res.get('msg'))
st, res = call('POST', '/api/store-assignment/bind', {'accountId': 99999, 'all': True}, adm)
check('不存在的账号被拒', res['code'] == 400, res.get('msg'))

print('\n=== 解绑 ===')
st, res = call('POST', '/api/store-assignment/unbind', {'all': True}, adm)
print('   ->', res.get('code'), res.get('msg'))
st, res = call('GET', '/api/store-assignment/overview', token=adm)
check('全部解绑后 unassigned == 总店铺数',
      res['data']['totals']['unassigned'] == res['data']['totals']['stores'],
      str(res['data']['totals']))

print('\n=== 审计日志 ===')
st, res = call('GET', '/api/store-assignment/logs?limit=20', token=adm)
logs = res['data']['list']
check('审计日志有记录', len(logs) > 0, '%d 条' % len(logs))
for lg in logs[:8]:
    print('   %s %s %s -> %s by %s (%s)' % (lg['createdAt'], lg['actionText'], lg['storeAlias'],
                                            lg['toName'] or '公共', lg['operatorName'], lg['remark']))

print('\n=== 权限：业务员不可访问店群接口 ===')
st, res = call('POST', '/api/auth/login', {'username': 'sales_a', 'password': 'Sales@123'})
ta = res['data']['token']
for path, method, body in (
    ('/api/store-assignment/overview', 'GET', None),
    ('/api/store-assignment/members', 'GET', None),
    ('/api/store-assignment/logs', 'GET', None),
    ('/api/users/3/stores', 'GET', None),
    ('/api/store-assignment/bind', 'POST', {'accountId': ub['id'], 'all': True}),
    ('/api/store-assignment/unbind', 'POST', {'all': True}),
):
    st, res = call(method, path, body, ta)
    check('业务员 %s %s 被拒' % (method, path), res.get('code') == 403, res.get('msg'))

print('\n===== 结果: %s =====' % ('全部通过' if ok else '存在失败项'))
sys.exit(0 if ok else 1)
