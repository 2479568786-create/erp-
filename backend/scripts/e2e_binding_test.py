# -*- coding: utf-8 -*-
"""端到端验证：业务员与店铺绑定 + 数据隔离。

前置：后端已在 http://127.0.0.1:5000 运行，且存在管理员 admin/Admin@123456。
"""
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
        with urllib.request.urlopen(req, timeout=20) as resp:
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
    mark = 'PASS' if cond else 'FAIL'
    if not cond:
        ok = False
    print('[%s] %s %s' % (mark, label, detail))


print('=== 1. 管理员登录 ===')
st, res = call('POST', '/api/auth/login', {'username': 'admin', 'password': 'Admin@123456'})
check('管理员登录', res.get('code') == 200, str(res.get('msg')))
adm = res.get('data', {}).get('token')
if not adm:
    print('无法取得管理员 token，终止'); sys.exit(1)

print('\n=== 1b. 重置店铺归属（保证用例可重复执行）===')
st, res = call('GET', '/api/stores', token=adm)
for _s in (res.get('data', {}).get('list') or []):
    if _s.get('accountId'):
        call('PUT', '/api/stores/owner', {'storePk': _s['id'], 'accountId': None}, adm)
print('   已把全部店铺重置为公共店铺')

print('\n=== 2. 创建业务员账号 ===')
def ensure_user(username, nickname):
    """幂等创建业务员：已存在视为成功（便于反复执行本用例）"""
    st, res = call('POST', '/api/users', {
        'username': username, 'password': 'Sales@123',
        'nickname': nickname, 'role': 'salesperson'}, adm)
    ok_create = res.get('code') == 200
    ok_exists = '已存在' in str(res.get('msg', ''))
    check('创建 %s' % username, ok_create or ok_exists, str(res.get('msg')))
    return ok_create or ok_exists

ensure_user('sales_a', '业务员甲')
ensure_user('sales_b', '业务员乙')

st, res = call('GET', '/api/users', token=adm)
users = res.get('data', {}).get('list', [])
print('   用户列表:', [(u['id'], u['username'], u['role'], u['storeCount']) for u in users])
ua = next((u for u in users if u['username'] == 'sales_a'), None)
ub = next((u for u in users if u['username'] == 'sales_b'), None)
check('GET /api/users 返回 storeCount', ua is not None and 'storeCount' in ua)

print('\n=== 3. 创建店铺 ===')
store_ids = []
for i, sid in enumerate(('900000001', '900000002', '900000003'), 1):
    st, res = call('POST', '/api/stores', {
        'storeId': sid, 'alias': '测试店铺%d' % i, 'currency': 'RUB',
        'storeGroup': '测试组', 'authType': 'api',
        'clientId': 'test-client-%d' % i, 'apiKey': 'test-key-%d' % i,
    }, adm)
    print('   创建店铺', sid, '->', res.get('code'), res.get('msg'))
    if res.get('code') == 200:
        store_ids.append(res.get('data', {}).get('id') or res.get('data', {}).get('store', {}).get('id'))

st, res = call('GET', '/api/stores', token=adm)
stores = res.get('data', {}).get('list', res.get('data', {}).get('stores', [])) if isinstance(res.get('data'), dict) else []
print('   店铺列表条数:', len(stores))
for s in stores[:6]:
    print('     ', {k: s.get(k) for k in ('id', 'store_id', 'alias', 'accountId', 'ownerName')})

print('\n=== 4. 绑定店铺 -> 业务员甲 ===')
pks = [s['id'] for s in stores if str(s.get('store_id', '')).startswith('9000000')]
print('   待绑定 store pk:', pks)
for pk in pks[:2]:
    st, res = call('PUT', '/api/stores/owner', {'storePk': pk, 'accountId': ua['id']}, adm)
    check('绑定 storePk=%s -> sales_a' % pk, res.get('code') == 200, str(res.get('msg')))

print('\n=== 5. 业务员甲可见性 ===')
st, res = call('POST', '/api/auth/login', {'username': 'sales_a', 'password': 'Sales@123'})
ta = res.get('data', {}).get('token')
check('业务员甲登录', res.get('code') == 200, str(res.get('msg')))
st, res = call('GET', '/api/stores', token=ta)
sa = res.get('data', {}).get('list', res.get('data', {}).get('stores', [])) if isinstance(res.get('data'), dict) else []
check('业务员甲仅见已绑定店铺(2 家)', len(sa) == 2, '实际 %d 家' % len(sa))

print('\n=== 6. 业务员乙可见性（应 0 家）===')
st, res = call('POST', '/api/auth/login', {'username': 'sales_b', 'password': 'Sales@123'})
tb = res.get('data', {}).get('token')
st, res = call('GET', '/api/stores', token=tb)
sb = res.get('data', {}).get('list', res.get('data', {}).get('stores', [])) if isinstance(res.get('data'), dict) else []
check('业务员乙仅见已绑定店铺(0 家)', len(sb) == 0, '实际 %d 家' % len(sb))

print('\n=== 7. 越权防护：业务员不得访问用户管理 ===')
st, res = call('GET', '/api/users', token=ta)
check('业务员 GET /api/users 被拒', res.get('code') == 403, str(res.get('msg')))
st, res = call('PUT', '/api/stores/owner', {'storePk': pks[0], 'accountId': ub['id']}, ta)
check('业务员改绑定被拒', res.get('code') == 403, str(res.get('msg')))

print('\n=== 8. 解绑 ===')
if len(pks) >= 2:
    st, res = call('PUT', '/api/stores/owner', {'storePk': pks[1], 'accountId': None}, adm)
    check('解绑店铺', res.get('code') == 200, str(res.get('msg')))
    st, res = call('GET', '/api/stores', token=ta)
    sa2 = res.get('data', {}).get('list', res.get('data', {}).get('stores', [])) if isinstance(res.get('data'), dict) else []
    check('解绑后业务员甲仅见 1 家', len(sa2) == 1, '实际 %d 家' % len(sa2))

print('\n===== 结果: %s =====' % ('全部通过' if ok else '存在失败项'))
sys.exit(0 if ok else 1)
