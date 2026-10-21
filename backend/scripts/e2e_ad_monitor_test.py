# -*- coding: utf-8 -*-
"""端到端验证：AI 广告监控模块（接口 + 迁移 + 规则引擎）。"""
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
adm = res.get('data', {}).get('token')
check('管理员登录', res.get('code') == 200)
if not adm:
    sys.exit(1)

print('\n=== 1. 迁移：广告相关表与凭证列 ===')
st, res = call('GET', '/api/stores', token=adm)
stores = res.get('data', {}).get('list') or []
check('店铺列表可读', res.get('code') == 200, '%d 家' % len(stores))
if stores:
    s0 = stores[0]
    check('响应含 hasPerfSecret 字段（凭证未泄露）', 'hasPerfSecret' in s0, json.dumps(
        {k: s0.get(k) for k in ('storeId', 'hasApiKey', 'hasPerfSecret', 'perf_client_id')}, ensure_ascii=False))
    check('响应不含 perf_client_secret（密钥不外泄）', 'perf_client_secret' not in s0)
    check('响应不含 perf_token', 'perf_token' not in s0)

print('\n=== 2. 阈值配置 ===')
st, res = call('GET', '/api/ad-monitor/settings', token=adm)
cfg = res.get('data') or {}
check('读取阈值成功', res.get('code') == 200, json.dumps(cfg, ensure_ascii=False))
check('默认 30 次点击', cfg.get('testClicksThreshold') == 30)
check('默认 CTR 4%', abs((cfg.get('lowCtrThreshold') or 0) - 0.04) < 1e-9)
check('默认花费 1000₽', cfg.get('highSpendThreshold') == 1000)
check('默认日花费 500₽', cfg.get('dailySpendThreshold') == 500)

st, res = call('PUT', '/api/ad-monitor/settings', {'dailySpendThreshold': 600, 'lowCtrThreshold': 5}, adm)
check('修改阈值成功', res.get('code') == 200, str(res.get('msg')))
d = res.get('data') or {}
check('日花费阈值已改 600', d.get('dailySpendThreshold') == 600)
check('百分数 5 自动转 0.05', abs((d.get('lowCtrThreshold') or 0) - 0.05) < 1e-9,
      str(d.get('lowCtrThreshold')))
# 还原
call('PUT', '/api/ad-monitor/settings', {'dailySpendThreshold': 500, 'lowCtrThreshold': 0.04}, adm)

print('\n=== 3. 看板 ===')
st, res = call('GET', '/api/ad-monitor/overview', token=adm)
ov = res.get('data') or {}
check('看板可读', res.get('code') == 200)
check('看板结构完整',
      all(k in ov for k in ('statDate', 'settings', 'totals', 'stores', 'dailySpend', 'products', 'buckets')))
print('   statDate:', ov.get('statDate'))
print('   totals  :', json.dumps(ov.get('totals'), ensure_ascii=False))
print('   店铺    :', json.dumps(
    [{k: s.get(k) for k in ('storeName', 'credentialsReady', 'campaignCount')} for s in (ov.get('stores') or [])],
    ensure_ascii=False))
check('未配凭证的店铺被标记', all('credentialsReady' in s for s in (ov.get('stores') or [])))

print('\n=== 4. 告警接口（弹窗数据源）===')
st, res = call('GET', '/api/ad-monitor/alerts', token=adm)
al = res.get('data') or {}
check('告警接口可读', res.get('code') == 200)
check('告警结构完整', all(k in al for k in ('statDate', 'dailySpendAlerts', 'hasDailyWarning', 'productAlertCounts')))
print('   ', json.dumps({k: al.get(k) for k in ('statDate', 'hasDailyWarning', 'productAlertCounts', 'totalDailySpend')},
                         ensure_ascii=False))

print('\n=== 5. 商品告警 ===')
st, res = call('GET', '/api/ad-monitor/products', token=adm)
pd = res.get('data') or {}
check('商品告警可读', res.get('code') == 200)
check('含三档计数', 'counts' in pd, json.dumps(pd.get('counts'), ensure_ascii=False))

print('\n=== 6. 同步（无凭证时应给出可读提示而非 500）===')
st, res = call('POST', '/api/ad-monitor/sync', {}, adm)
check('同步接口返回可读结果', res.get('code') in (200, 400), 'code=%s msg=%s' % (res.get('code'), res.get('msg')))
print('   ', res.get('msg'))

print('\n=== 7. 活动列表 ===')
st, res = call('GET', '/api/ad-monitor/campaigns', token=adm)
check('活动列表可读', res.get('code') == 200, '共 %s 个' % (res.get('data') or {}).get('total'))

print('\n=== 8. 权限：业务员只能看自己店铺、不能改阈值 ===')
st, res = call('POST', '/api/auth/login', {'username': 'sales_a', 'password': 'Sales@123'})
sp = res.get('data', {}).get('token')
st, res = call('GET', '/api/ad-monitor/overview', token=sp)
check('业务员可读看板', res.get('code') == 200)
check('业务员 canManage=false', (res.get('data') or {}).get('canManage') is False)
st, res = call('PUT', '/api/ad-monitor/settings', {'dailySpendThreshold': 1}, sp)
check('业务员改阈值被拒(403)', res.get('code') == 403, str(res.get('msg')))

st, res = call('PUT', '/api/ad-monitor/settings', {'dailySpendThreshold': 1})
check('未登录改阈值被拒(401)', res.get('code') == 401, str(res.get('msg')))

print('\n=== 8b. 阈值改动留痕 ===')
st, res = call('PUT', '/api/ad-monitor/settings', {'testClicksThreshold': 30}, adm)
d = res.get('data') or {}
check('管理员改阈值成功', res.get('code') == 200, str(res.get('msg')))
check('记录了最后修改人', bool(d.get('updatedByName')), 'updatedByName=%s' % d.get('updatedByName'))
check('记录了修改时间', bool(d.get('updatedAt')), 'updatedAt=%s' % d.get('updatedAt'))

# 被拒的修改不应产生任何留痕变化
st, before = call('GET', '/api/ad-monitor/settings', token=adm)
call('PUT', '/api/ad-monitor/settings', {'testClicksThreshold': 999}, sp)
st, after = call('GET', '/api/ad-monitor/settings', token=adm)
check('被拒的修改未生效（阈值不变）',
      (before.get('data') or {}).get('testClicksThreshold') == (after.get('data') or {}).get('testClicksThreshold'),
      '%s -> %s' % ((before.get('data') or {}).get('testClicksThreshold'),
                    (after.get('data') or {}).get('testClicksThreshold')))
st, res = call('POST', '/api/ad-monitor/stores/1/test', {}, sp)
check('业务员测试凭证被拒(403)', res.get('code') == 403, str(res.get('msg')))
st, res = call('GET', '/api/ad-monitor/overview')
check('未登录被拒(401)', res.get('code') == 401)

print()
if ok:
    print('===== 全部通过 =====')
else:
    print('===== 存在失败项 =====')
sys.exit(0 if ok else 1)
