# -*- coding: utf-8 -*-
"""回归测试：广告监控四条规则的判定逻辑（用造数据，不依赖真实 API）。

规则：
  R1 已完成测评  clicks >= 30
  R2 点击率偏低  views >= 100 且 ctr < 4%
  R3 高花费无转化 expense > 1000 且 orders == 0
  R4 当日花费告警 单店当日花费 >= 500

运行：cd backend && python tests/test_ad_monitor_rules.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='admon_')
db.DB_PATH = os.path.join(_TMP, 'test.db')
db.init_db()

from services import ad_monitor_service as svc  # noqa: E402

fails = []


def check(label, cond, detail=''):
    print('[%s] %s %s' % ('PASS' if cond else 'FAIL', label, detail))
    if not cond:
        fails.append(label)


# 两个测试店铺
for pk, sid, alias in ((101, 'S001', '甲店'), (102, 'S002', '乙店')):
    db.execute("INSERT INTO stores (id, store_id, alias, currency, store_group, auth_status,"
               " perf_client_id, perf_client_secret, ad_monitor_enabled)"
               " VALUES (?, ?, ?, 'RUB', '测试', 'active', '', '', 1)", (pk, sid, alias))

# 日期不能写死：service 的商品窗口是「近 30 天」（相对今天），
# 写死的历史日期会让数据落到窗口外，告警恒为空——这本测试曾因此变成"时间炸弹"
# （2026-09 前后写死 2026-09-13，到 10 月就必然全挂）。统一按莫斯科时间取今天/昨天。
_MSK = timezone(timedelta(hours=3))
_NOW = datetime.now(_MSK)
TODAY = _NOW.strftime('%Y-%m-%d')
YDAY = (_NOW - timedelta(days=1)).strftime('%Y-%m-%d')
DF = (_NOW - timedelta(days=29)).strftime('%Y-%m-%d')
DT = TODAY


def add_sku(store_pk, sku, views, clicks, expense, orders, title='', stat_date=None):
    """写入一条【按天】商品指标（表已改为 stat_date 粒度）。"""
    ctr = (clicks / float(views)) if views else 0.0
    db.execute(
        "INSERT OR REPLACE INTO ad_sku_stats (store_pk, store_id, stat_date, sku, campaign_id,"
        " title, impressions, clicks, ctr, expense, avg_cpc, orders, sales, to_cart, drr)"
        " VALUES (?, '', ?, ?, '', ?, ?, ?, ?, ?, 0, ?, 0, 0, 0)",
        (store_pk, stat_date or DT, sku, title or ('商品' + sku),
         views, clicks, ctr, expense, orders),
    )


def add_daily(store_pk, day, expense, campaign='c1', orders=0):
    db.execute(
        "INSERT OR REPLACE INTO ad_daily_stats (store_pk, store_id, stat_date, campaign_id,"
        " campaign_title, impressions, clicks, expense, avg_cpc, orders, orders_amount)"
        " VALUES (?, '', ?, ?, '', 0, 0, ?, 0, ?, 0)",
        (store_pk, day, campaign, expense, orders),
    )


print('=== 造数据 ===')
# R1 命中：35 次点击
add_sku(101, 'SKU_TEST_OK', views=1200, clicks=35, expense=300, orders=2, title='已达测评量商品')
# R1 未命中：29 次点击（差一点）
add_sku(101, 'SKU_TEST_NEAR', views=900, clicks=29, expense=250, orders=1, title='差一点商品')
# R2 命中：500 曝光 10 点击 = 2% < 4%
add_sku(101, 'SKU_LOWCTR', views=500, clicks=10, expense=120, orders=0, title='低点击率商品')
# R2 不应命中：曝光不足 100（防噪音门槛）
add_sku(101, 'SKU_LOWVIEWS', views=50, clicks=0, expense=0, orders=0, title='曝光不足商品')
# R2 不应命中：5% >= 4%
add_sku(101, 'SKU_CTR_OK', views=400, clicks=20, expense=200, orders=1, title='点击率合格商品')
# R3 命中：花费 1500₽ 零订单
add_sku(102, 'SKU_WASTE', views=800, clicks=40, expense=1500, orders=0, title='高花费零单商品')
# R3 不应命中：花费 1500₽ 但有 1 单
add_sku(102, 'SKU_HAS_ORDER', views=800, clicks=40, expense=1500, orders=1, title='有单商品')
# R3 边界：正好 1000₽ 不告警（规则是「超过 1000」）
add_sku(102, 'SKU_EDGE_1000', views=600, clicks=30, expense=1000, orders=0, title='刚好一千商品')
# 多活动合并：同一 SKU 两条活动，合计 35 次点击才应判「已完成测评」
add_sku(102, 'SKU_MULTI_A', views=600, clicks=18, expense=100, orders=0, title='多活动商品')
db.execute("UPDATE ad_sku_stats SET campaign_id='c2', id=id+1000 WHERE sku='SKU_MULTI_A'")
add_sku(102, 'SKU_MULTI_A', views=600, clicks=17, expense=100, orders=0, title='多活动商品')

# R4 日花费
add_daily(101, TODAY, 620.0)            # 超线
add_daily(102, TODAY, 480.5)            # 未超线
add_daily(101, YDAY, 900.0)             # 昨天超线，但今天不该报

print()
print('=== R1 已完成测评 ===')
res = svc.evaluate_products([101, 102])
tc = {x['sku'] for x in res['testComplete']}
check('35 次点击 → 已完成测评', 'SKU_TEST_OK' in tc, str(sorted(tc)))
check('29 次点击 → 不算完成', 'SKU_TEST_NEAR' not in tc)
check('多活动合计 35 次 → 已完成测评', 'SKU_MULTI_A' in tc, '合并后点击数应为 35')
multi = next((x for x in res['testComplete'] if x['sku'] == 'SKU_MULTI_A'), None)
check('多活动点击数正确合并为 35', multi and multi['clicks'] == 35, str(multi and multi['clicks']))

print()
print('=== R2 点击率低于 4% ===')
lc = {x['sku']: x for x in res['lowCtr']}
check('500 曝光 10 点击(2%) → 告警', 'SKU_LOWCTR' in lc,
      str([(k, round(v['ctr'] * 100, 2)) for k, v in lc.items()]))
check('曝光不足 100 → 不告警（防噪音）', 'SKU_LOWVIEWS' not in lc)
check('点击率 5% → 不告警', 'SKU_CTR_OK' not in lc)

print()
print('=== R3 高花费无转化 ===')
nc = {x['sku']: x for x in res['noConversion']}
check('1500₽ 零订单 → 告警', 'SKU_WASTE' in nc, str({k: v['expense'] for k, v in nc.items()}))
check('1500₽ 有 1 单 → 不告警', 'SKU_HAS_ORDER' not in nc)
check('刚好 1000₽ 零订单 → 不告警（规则为「超过」）', 'SKU_EDGE_1000' not in nc)

print()
print('=== R4 当日花费告警 ===')
spend = svc.daily_spend([101, 102], TODAY)
by = {s['storePk']: s for s in spend}
print('   ', [(s['storeName'], s['expense'], s['exceeded']) for s in spend])
check('甲店 620₽ ≥ 500 超线', by[101]['exceeded'] is True)
check('甲店超出额 = 120₽', abs(by[101]['overBy'] - 120.0) < 1e-6, str(by[101]['overBy']))
check('乙店 480.5₽ 未超线', by[102]['exceeded'] is False)
check('只统计指定日期（不含昨天 900₽）', by[101]['expense'] == 620.0, str(by[101]['expense']))

print()
print('=== 弹窗告警集合 ===')
alerts = svc.build_alerts([101, 102], TODAY)
check('hasDailyWarning 为真', alerts['hasDailyWarning'] is True)
check('只对超线店铺产生弹窗告警', len(alerts['dailySpendAlerts']) == 1,
      str([a['storeName'] for a in alerts['dailySpendAlerts']]))
check('弹窗文案含店名与金额',
      '甲店' in alerts['dailySpendAlerts'][0]['message']
      and '620' in alerts['dailySpendAlerts'][0]['message'],
      alerts['dailySpendAlerts'][0]['message'])
# 注意：三档判定**互相独立**，同一商品可以同时出现在多个档位。
# 例：某商品 35 次点击（已完成测评）但 CTR 只有 2.9%（点击率偏低）——
#     两件事都成立，都应各自提醒，不应互相抵消。
check('商品告警计数正确',
      alerts['productAlertCounts'] == {'testComplete': 5, 'lowCtr': 4, 'noConversion': 1},
      str(alerts['productAlertCounts']))
_overlap = svc.evaluate_products([101, 102])
_ok_skus = {x['sku'] for x in _overlap['testComplete']}
_lc_skus = {x['sku'] for x in _overlap['lowCtr']}
check('同一商品可同时命中「已完成测评」与「点击率偏低」',
      bool(_ok_skus & _lc_skus), '交集=' + str(sorted(_ok_skus & _lc_skus)))

print()
print('=== 阈值可调：把日花费阈值调到 1000 ===')
svc.update_settings({'dailySpendThreshold': 1000})
alerts2 = svc.build_alerts([101, 102], TODAY)
check('阈值提高后不再告警', alerts2['hasDailyWarning'] is False,
      str(alerts2['dailySpendAlerts']))
svc.update_settings({'dailySpendThreshold': 500})
check('阈值可还原', svc.get_settings()['dailySpendThreshold'] == 500)

print()
print('=== 只看自己店铺（隔离）===')
only_a = svc.evaluate_products([101])
skus_a = {x['sku'] for x in only_a['all']}
check('仅返回甲店商品', all(s.startswith('SKU_') for s in skus_a) and 'SKU_WASTE' not in skus_a,
      str(sorted(skus_a)))

print()
if fails:
    print('===== 失败项: %s =====' % ', '.join(fails))
    sys.exit(1)
print('===== 全部通过 =====')
