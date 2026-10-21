# -*- coding: utf-8 -*-
"""
AI 广告监控服务

【业务目标】
业务员自行在 Ozon 后台开通广告（登上顶端 / 按点击付费 / 按订单付费等），
ERP 不代投，只负责**把效果盯住**，及时把问题商品和超支风险推给人。

【四条监控规则】（阈值均可在「AI广告监控」页调整，默认值见 schema 的 ad_monitor_settings）

  R1 已完成测评  clicks >= 30
     商品已累积足够点击量，可以据此判断广告效果，标记为「已完成测评」。

  R2 点击率偏低  views >= 100 且 ctr < 4%
     曝光够了但没人点，通常是主图/标题/定价与需求不匹配。
     注：加了最低曝光门槛，避免「2 次曝光 0 次点击 → CTR 0%」这类噪音刷屏。

  R3 高花费无转化  expense > 1000₽ 且 orders == 0
     钱花出去了但一单没出，属于明确的投放浪费。

  R4 当日花费告警 单店当日（MSK）广告总花费 >= 500₽
     接近预算失控前提醒业务员，通过弹窗强制触达。

【数据口径】
- 一律按莫斯科时间（MSK）自然日统计，见 ozon_performance_api.today_msk()。
- 商品指标按「店铺 + SKU」聚合各广告活动（一个商品可能同时投多个活动）。
- CTR 不跨活动累加，而是用 总点击 / 总曝光 重算，避免加权错误。

【重要语义：三条规则互相独立，不做互斥】
同一商品可能同时出现在多个档位，例如某商品已有 35 次点击（R1 达标）
但 CTR 仅 2.9%（R2 偏低）——两件事都成立，都应各自提醒。
不要因为「已达标」就压掉「点击率低」的告警，二者指向不同的动作。
"""
from __future__ import annotations

import re

from db import query, execute, execute_many
from services import ozon_performance_api as perf

# 同步窗口：商品级指标统计近 N 天；日花费统计近 N 天
DEFAULT_SKU_WINDOW_DAYS = 30
DEFAULT_DAILY_WINDOW_DAYS = 14


# ============================================================
# 表结构自愈
# ============================================================
# 迁移本来在 db.init_db() 里执行，但模块被「直接导入」时（脚本/测试/
# 未走 create_app 的场景）迁移不会跑，于是这里做一次幂等自检并自愈，
# 避免出现「代码已更新、表还是旧结构 → no such column: stat_date」。


def _ensure_daily_table() -> None:
    """确保 ad_sku_stats 是「按天」结构（旧结构且表为空时重建）。"""
    try:
        cols = {r['name'] for r in query('PRAGMA table_info(ad_sku_stats)')}
    except Exception:  # noqa: BLE001
        return
    if not cols:
        return                       # 表不存在，交给 schema.sql 建
    if 'stat_date' in cols:
        return                       # 已是新结构
    try:
        cnt = (query('SELECT COUNT(*) AS c FROM ad_sku_stats', one=True) or {}).get('c', 0)
    except Exception:  # noqa: BLE001
        cnt = 0
    if cnt:
        print('[广告监控] ad_sku_stats 有 %d 行历史数据，跳过结构重建' % cnt)
        return
    execute('DROP TABLE ad_sku_stats')
    execute(
        "CREATE TABLE ad_sku_stats ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "store_pk INTEGER NOT NULL,"
        "store_id TEXT NOT NULL DEFAULT '',"
        "stat_date TEXT NOT NULL,"
        "sku TEXT NOT NULL,"
        "campaign_id TEXT NOT NULL DEFAULT '',"
        "title TEXT NOT NULL DEFAULT '',"
        "impressions INTEGER NOT NULL DEFAULT 0,"
        "clicks INTEGER NOT NULL DEFAULT 0,"
        "ctr REAL NOT NULL DEFAULT 0,"
        "expense REAL NOT NULL DEFAULT 0,"
        "avg_cpc REAL NOT NULL DEFAULT 0,"
        "orders INTEGER NOT NULL DEFAULT 0,"
        "sales REAL NOT NULL DEFAULT 0,"
        "to_cart INTEGER NOT NULL DEFAULT 0,"
        "drr REAL NOT NULL DEFAULT 0,"
        "synced_at TEXT NOT NULL DEFAULT (datetime('now')),"
        "UNIQUE(store_pk, sku, campaign_id, stat_date))"
    )
    execute("CREATE INDEX IF NOT EXISTS idx_ad_sku_store ON ad_sku_stats(store_pk, stat_date)")
    execute("CREATE INDEX IF NOT EXISTS idx_ad_sku_sku ON ad_sku_stats(sku)")
    print('[广告监控] ad_sku_stats 已自愈重建为「按天」结构')


_ensure_daily_table()

# ============================================================
# 工具
# ============================================================

def _f(value) -> float:
    """宽松转 float（Ozon 的数值字段常以字符串返回，如 expense='123.45'）"""
    if value is None or value == '':
        return 0.0
    try:
        return float(str(value).replace(',', '.').replace(' ', '').replace('₽', ''))
    except (TypeError, ValueError):
        return 0.0


def _i(value) -> int:
    return int(_f(value))


def _pick(row: dict, *keys):
    """从 dict 里按多个候选键取值（兼容不同语言/写法的报表字段）"""
    for k in keys:
        if k in row and row[k] not in (None, ''):
            return row[k]
    # 退化到大小写不敏感匹配
    lowered = {str(k).lower(): v for k, v in row.items()}
    for k in keys:
        v = lowered.get(str(k).lower())
        if v not in (None, ''):
            return v
    return None


# ============================================================
# 阈值配置
# ============================================================

_SETTING_FIELDS = (
    'test_clicks_threshold', 'low_ctr_threshold', 'low_ctr_min_views',
    'high_spend_threshold', 'daily_spend_threshold',
)


def get_settings() -> dict:
    """读取监控阈值（表不存在或为空时返回内置默认值）"""
    try:
        row = query('SELECT * FROM ad_monitor_settings WHERE id = 1', one=True)
    except Exception:  # noqa: BLE001 - 老库未迁移时兜底
        row = None
    if not row:
        row = {
            'test_clicks_threshold': 30, 'low_ctr_threshold': 0.04,
            'low_ctr_min_views': 100, 'high_spend_threshold': 1000,
            'daily_spend_threshold': 500, 'updated_at': '',
        }
    return {
        'testClicksThreshold': _i(row.get('test_clicks_threshold')),
        'lowCtrThreshold': _f(row.get('low_ctr_threshold')),
        'lowCtrMinViews': _i(row.get('low_ctr_min_views')),
        'highSpendThreshold': _f(row.get('high_spend_threshold')),
        'dailySpendThreshold': _f(row.get('daily_spend_threshold')),
        'updatedAt': row.get('updated_at', ''),
        'updatedByName': row.get('updated_by_name', '') or '',
        # 同步窗口（只读展示）
        'skuWindowDays': DEFAULT_SKU_WINDOW_DAYS,
        'dailyWindowDays': DEFAULT_DAILY_WINDOW_DAYS,
    }


def update_settings(data: dict, operator: dict | None = None) -> dict:
    """更新阈值。**仅管理员可调用**（路由层用 @require_admin 把关）。

    只接受白名单字段，非法值忽略；同时记录「谁在什么时候改的」以备追溯。

    Args:
        data: 阈值字段（API 驼峰命名）
        operator: 操作人账号行（来自 get_current_user），写入留痕
    """
    mapping = {
        'testClicksThreshold': ('test_clicks_threshold', _i),
        'lowCtrThreshold': ('low_ctr_threshold', _f),
        'lowCtrMinViews': ('low_ctr_min_views', _i),
        'highSpendThreshold': ('high_spend_threshold', _f),
        'dailySpendThreshold': ('daily_spend_threshold', _f),
    }
    sets, params = [], []
    for api_key, (col, caster) in mapping.items():
        if api_key in data and data[api_key] not in (None, ''):
            val = caster(data[api_key])
            if val < 0:
                continue
            sets.append(col + ' = ?')
            params.append(val)
    # lowCtrThreshold 允许输入百分数（如 4 → 0.04）
    if 'lowCtrThreshold' in data and _f(data.get('lowCtrThreshold')) > 1:
        idx = [i for i, s in enumerate(sets) if s.startswith('low_ctr_threshold')]
        if idx:
            params[idx[0]] = _f(data['lowCtrThreshold']) / 100.0

    if sets:
        sets.append("updated_at = datetime('now','localtime')")
        if operator:
            sets.append('updated_by_id = ?')
            params.append(operator.get('id'))
            sets.append('updated_by_name = ?')
            params.append((operator.get('nickname') or operator.get('username') or '')[:50])
        execute('UPDATE ad_monitor_settings SET %s WHERE id = 1' % ', '.join(sets), params)
    return get_settings()


# ============================================================
# 同步
# ============================================================

def sync_store(store_pk: int, sku_days: int = DEFAULT_SKU_WINDOW_DAYS,
               daily_days: int = DEFAULT_DAILY_WINDOW_DAYS) -> dict:
    """同步单店广告数据：活动列表 + 商品级指标 + 每日花费。

    任一步失败都会写 ad_sync_log 并抛出 OzonPerformanceError，
    由路由层返回可读错误，不影响其它店铺。
    """
    store = query('SELECT * FROM stores WHERE id = ?', (store_pk,), one=True)
    if not store:
        raise perf.OzonPerformanceError('店铺不存在')
    store_id = store.get('store_id', '')
    store_name = store.get('alias') or store_id

    def _log(status, campaigns=0, sku_rows=0, daily_rows=0, message=''):
        execute(
            'INSERT INTO ad_sync_log (store_pk, store_id, status, campaigns, sku_rows, daily_rows, message)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?)',
            (store_pk, store_id, status, campaigns, sku_rows, daily_rows, message[:500]),
        )

    if not perf.has_credentials(store):
        msg = '未配置 Performance API 凭证'
        _log('error', message=msg)
        raise perf.OzonPerformanceError(
            '店铺「%s」%s，请到「店铺管理」填写广告 API 的 client_id / client_secret' % (store_name, msg)
        )

    try:
        # ---- 1. 活动列表 ----
        campaigns = _persist_campaigns(store_pk, store_id)
        campaign_ids = [str(c.get('id', '')) for c in campaigns if c.get('id')]

        # ---- 2. 商品级指标（R1/R2/R3 数据源）----
        # 走异步报表：官方快速接口 /statistics/products/sku 只接受今天/昨天，
        # 给不了 30 天区间，因此改用支持 62 天且逐日逐 SKU 的报表接口。
        date_from, date_to = perf.date_msk(sku_days - 1), perf.today_msk()
        report_rows = []
        if campaign_ids:
            report_rows = perf.campaign_report_rows(store_pk, campaign_ids, date_from, date_to)
        sku_rows = _persist_sku_stats(store_pk, store_id, report_rows)

        # ---- 3. 每日花费（R4 数据源）：与商品明细同源汇总，保证口径一致 ----
        daily_rows = _rebuild_daily_stats(store_pk, store_id, date_from, date_to)

        # 区分「同步成功但确实没有投放数据」与「同步失败」——
        # 前者是正常状态（该店没开广告），不该报错吓人，但必须讲清楚。
        if sku_rows:
            summary = '同步成功：%d 个活动，%d 条投放明细' % (len(campaigns), sku_rows)
        else:
            summary = ('同步成功，但 %s ~ %s 期间没有任何广告投放数据'
                       '（该店在 Ozon 侧无投放或未开广告）' % (date_from, date_to))
        _log('ok', len(campaigns), sku_rows, daily_rows, summary)
        return {
            'storePk': store_pk, 'storeId': store_id, 'storeName': store_name,
            'campaigns': len(campaigns), 'skuRows': sku_rows, 'dailyRows': daily_rows,
            'range': {'from': date_from, 'to': date_to},
            'hasActivity': sku_rows > 0,
            'message': summary,
        }
    except perf.OzonPerformanceError as exc:
        _log('error', message=str(exc))
        raise


def _persist_sku_stats(store_pk, store_id, rows) -> int:
    """写入逐日逐 SKU 的报表行（INSERT OR REPLACE，重复同步幂等）。

    数据来自异步报表 CSV，字段已由 ozon_performance_api.parse_report_csv 归一化：
      stat_date / sku / title / campaignId / impressions / clicks /
      ctr_percent / expense / avg_cpc / orders / sales / to_cart / drr_percent
    """
    payload = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sku = str(r.get('sku') or '').strip()
        stat_date = _norm_date(r.get('stat_date'))
        if not sku or not stat_date:
            continue
        impressions = _i(r.get('impressions'))
        clicks = _i(r.get('clicks'))
        ctr_pct = _f(r.get('ctr_percent'))
        # 报表给的是百分数；缺失时用 点击/曝光 自行计算
        ctr = (ctr_pct / 100.0) if ctr_pct > 0 else (
            (clicks / float(impressions)) if impressions else 0.0)
        drr_pct = _f(r.get('drr_percent'))
        payload.append((
            store_pk, store_id, stat_date, sku,
            str(r.get('campaignId') or ''),
            str(r.get('title') or '')[:200],
            impressions, clicks, ctr,
            _f(r.get('expense')), _f(r.get('avg_cpc')),
            _i(r.get('orders')), _f(r.get('sales')),
            _i(r.get('to_cart')), (drr_pct / 100.0) if drr_pct > 1 else drr_pct,
        ))
    if not payload:
        return 0
    execute_many(
        'INSERT OR REPLACE INTO ad_sku_stats'
        ' (store_pk, store_id, stat_date, sku, campaign_id, title, impressions, clicks, ctr,'
        '  expense, avg_cpc, orders, sales, to_cart, drr, synced_at)'
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))",
        payload,
    )
    return len(payload)


def _norm_date(value) -> str:
    """报表日期归一化为 YYYY-MM-DD（兼容 2026-09-13 / 13.09.2026 / RFC3339）。"""
    s = str(value or '').strip()
    if not s:
        return ''
    m = re.match(r'^(\\d{4})-(\\d{2})-(\\d{2})', s)
    if m:
        return '%s-%s-%s' % m.groups()
    m = re.match(r'^(\\d{2})\\.(\\d{2})\\.(\\d{4})', s)
    if m:
        d, mo, y = m.groups()
        return '%s-%s-%s' % (y, mo, d)
    return s[:10]


def _persist_campaigns(store_pk: int, store_id: str) -> list:
    """同步活动列表。

    ⚠️ 实测返回与官方 spec 有出入，必须做兼容（否则会直接 500）：

      1) placement 返回的是**数组**，例如 ['PLACEMENT_SEARCH_AND_CATEGORY']。
         原样绑定进 SQLite 会抛：
           InterfaceError: Error binding parameter 5 - probably unsupported type.
         这里用 '/' 拼成字符串存储。

      2) 付费方式的键是 **PaymentType**（首字母大写），不是 spec 写的 paymentType。
         按小写取值会永远拿到空串，前端「付费方式」列会一片空白。
    """
    campaigns = perf.list_all_campaigns(store_pk)
    if not campaigns:
        return []
    now = perf.now_msk().strftime('%Y-%m-%d %H:%M:%S')
    payload = []
    for c in campaigns:
        placement = c.get('placement')
        if isinstance(placement, (list, tuple)):
            placement = '/'.join(str(p) for p in placement if p)
        payment = c.get('PaymentType') or c.get('paymentType') or ''
        payload.append((
            store_pk, store_id, str(c.get('id', '')), str(c.get('title') or ''),
            str(payment or ''), str(placement or ''), str(c.get('state') or ''),
            str(c.get('advObjectType') or ''), str(c.get('budget') or ''),
            str(c.get('dailyBudget') or ''), str(c.get('fromDate') or ''),
            str(c.get('toDate') or ''), now,
        ))
    execute_many(
        "INSERT INTO ad_campaigns (store_pk, store_id, campaign_id, title, payment_type,"
        " placement, state, adv_object_type, budget, daily_budget, from_date, to_date, synced_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(store_pk, campaign_id) DO UPDATE SET"
        " title=excluded.title, payment_type=excluded.payment_type, placement=excluded.placement,"
        " state=excluded.state, adv_object_type=excluded.adv_object_type, budget=excluded.budget,"
        " daily_budget=excluded.daily_budget, from_date=excluded.from_date, to_date=excluded.to_date,"
        " synced_at=excluded.synced_at",
        payload,
    )
    return campaigns


def _rebuild_daily_stats(store_pk, store_id, date_from, date_to) -> int:
    """由商品级明细汇总出「活动 × 日期」的花费（R4 数据源）。

    为什么从 ad_sku_stats 反推、而不是再调一次 daily 接口：
      · 与商品明细同源同口径，不会出现「明细有花费、日表却没有」的对不上；
      · daily 接口的行格式在不同账号间不稳定，少一个未知格式的风险点。
    """
    execute('DELETE FROM ad_daily_stats WHERE store_pk = ? AND stat_date BETWEEN ? AND ?',
            (store_pk, date_from, date_to))
    rows = query(
        """SELECT stat_date, campaign_id,
                  SUM(impressions) AS impressions, SUM(clicks) AS clicks,
                  SUM(expense) AS expense, SUM(orders) AS orders, SUM(sales) AS sales
           FROM ad_sku_stats
           WHERE store_pk = ? AND stat_date BETWEEN ? AND ?
           GROUP BY stat_date, campaign_id""",
        (store_pk, date_from, date_to),
    )
    if not rows:
        return 0
    titles = {
        r['campaign_id']: r['title']
        for r in query('SELECT campaign_id, title FROM ad_campaigns WHERE store_pk = ?',
                       (store_pk,))
    }
    payload = []
    for r in rows:
        clicks = int(r['clicks'] or 0)
        expense = float(r['expense'] or 0)
        payload.append((
            store_pk, store_id, r['stat_date'], r['campaign_id'],
            titles.get(r['campaign_id'], ''),
            int(r['impressions'] or 0), clicks, expense,
            (expense / clicks) if clicks else 0.0,
            int(r['orders'] or 0), float(r['sales'] or 0),
        ))
    execute_many(
        'INSERT OR REPLACE INTO ad_daily_stats'
        ' (store_pk, store_id, stat_date, campaign_id, campaign_title, impressions, clicks,'
        '  expense, avg_cpc, orders, orders_amount, synced_at)'
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))",
        payload,
    )
    return len(payload)


def sync_stores(store_pks, **kwargs) -> dict:
    """批量同步，单店失败不影响其它店铺。"""
    results, errors = [], []
    for pk in store_pks:
        try:
            results.append(sync_store(pk, **kwargs))
        except Exception as exc:  # noqa: BLE001 - 汇总错误继续
            errors.append({'storePk': pk, 'message': str(exc)})
    return {'synced': results, 'errors': errors}


# ============================================================
# 规则评估
# ============================================================

def _in_clause(values):
    return ','.join('?' * len(values))


def _store_name_map(store_pks) -> dict:
    if not store_pks:
        return {}
    rows = query(
        'SELECT id, store_id, alias FROM stores WHERE id IN (%s)' % _in_clause(store_pks),
        list(store_pks),
    )
    return {r['id']: (r['alias'] or r['store_id']) for r in rows}


def aggregate_sku_metrics(store_pks) -> list[dict]:
    """按「店铺 + SKU」聚合最近一次同步窗口内的广告指标。

    一个商品可能同时投多个活动，这里合并计算：
      - 点击/曝光/花费/订单直接求和
      - CTR 用 总点击/总曝光 重算（不能把各活动的 CTR 相加）
    """
    if not store_pks:
        return []
    # 按天累计：取最近 SKU_WINDOW 天的所有日行求和
    # （快速接口只给今天/昨天，30 天口径必须自己 SUM 出来）
    window_start = perf.date_msk(DEFAULT_SKU_WINDOW_DAYS - 1)
    rows = query(
        """SELECT s.store_pk, s.store_id, s.sku, MAX(s.title) AS title,
                  MIN(s.stat_date) AS date_from, MAX(s.stat_date) AS date_to,
                  SUM(s.impressions) AS views,
                  SUM(s.clicks)      AS clicks,
                  SUM(s.expense)     AS expense,
                  SUM(s.orders)      AS orders,
                  SUM(s.sales)       AS sales,
                  SUM(s.to_cart)     AS to_cart
           FROM ad_sku_stats s
           WHERE s.store_pk IN (%s) AND s.stat_date >= ?
           GROUP BY s.store_pk, s.sku""" % _in_clause(store_pks),
        list(store_pks) + [window_start],
    )
    names = _store_name_map(store_pks)
    out = []
    for r in rows or []:
        views, clicks = int(r['views'] or 0), int(r['clicks'] or 0)
        out.append({
            'storePk': r['store_pk'],
            'storeId': r['store_id'] or '',
            'storeName': names.get(r['store_pk'], ''),
            'sku': r['sku'],
            'title': r['title'] or r['sku'],
            'views': views,
            'clicks': clicks,
            'ctr': (clicks / float(views)) if views else 0.0,
            'expense': round(float(r['expense'] or 0), 2),
            'orders': int(r['orders'] or 0),
            'sales': round(float(r['sales'] or 0), 2),
            'toCart': int(r['to_cart'] or 0),
            'dateFrom': r['date_from'] or '',
            'dateTo': r['date_to'] or '',
        })
    return out


def evaluate_products(store_pks, settings: dict | None = None) -> dict:
    """执行 R1 / R2 / R3 三条商品级规则。

    Returns:
        {'all': [...], 'testComplete': [...], 'lowCtr': [...], 'noConversion': [...]}
        每个元素是带 message 的告警对象。
    """
    cfg = settings or get_settings()
    metrics = aggregate_sku_metrics(store_pks)

    test_complete, low_ctr, no_conversion = [], [], []
    for m in metrics:
        base = dict(m)
        # R1 已完成测评
        if m['clicks'] >= cfg['testClicksThreshold']:
            test_complete.append({
                **base, 'type': 'test_complete', 'severity': 'info',
                'message': '已累积 %d 次点击（≥%d），可直接判断投放效果'
                           % (m['clicks'], cfg['testClicksThreshold']),
            })
        # R2 点击率偏低
        if m['views'] >= cfg['lowCtrMinViews'] and m['ctr'] < cfg['lowCtrThreshold']:
            low_ctr.append({
                **base, 'type': 'low_ctr', 'severity': 'warning',
                'message': '点击率 %.2f%%（低于 %.2f%%），曝光 %d 次仅点击 %d 次，建议优化主图/标题'
                           % (m['ctr'] * 100, cfg['lowCtrThreshold'] * 100, m['views'], m['clicks']),
            })
        # R3 高花费无转化
        if m['expense'] > cfg['highSpendThreshold'] and m['orders'] == 0:
            no_conversion.append({
                **base, 'type': 'no_conversion', 'severity': 'danger',
                'message': '已花费 %.0f₽（超过 %.0f₽）但零订单，属于无效投放，建议暂停或降价'
                           % (m['expense'], cfg['highSpendThreshold']),
            })

    test_complete.sort(key=lambda x: -x['clicks'])
    low_ctr.sort(key=lambda x: x['ctr'])
    no_conversion.sort(key=lambda x: -x['expense'])
    return {
        'all': no_conversion + low_ctr + test_complete,
        'testComplete': test_complete,
        'lowCtr': low_ctr,
        'noConversion': no_conversion,
        'metricsCount': len(metrics),
    }


def daily_spend(store_pks, stat_date: str | None = None) -> list[dict]:
    """按店铺汇总某天（MSK）的广告总花费，并标注是否触及告警线。"""
    if not store_pks:
        return []
    cfg = get_settings()
    day = stat_date or perf.today_msk()
    rows = query(
        """SELECT store_pk, store_id,
                  SUM(expense) AS expense, SUM(clicks) AS clicks,
                  SUM(impressions) AS impressions, SUM(orders) AS orders,
                  SUM(orders_amount) AS orders_amount
           FROM ad_daily_stats
           WHERE stat_date = ? AND store_pk IN (%s)
           GROUP BY store_pk""" % _in_clause(store_pks),
        [day] + list(store_pks),
    )
    names = _store_name_map(store_pks)
    out = []
    for r in rows or []:
        expense = round(float(r['expense'] or 0), 2)
        threshold = cfg['dailySpendThreshold']
        out.append({
            'storePk': r['store_pk'],
            'storeId': r['store_id'] or '',
            'storeName': names.get(r['store_pk'], ''),
            'statDate': day,
            'expense': expense,
            'clicks': int(r['clicks'] or 0),
            'impressions': int(r['impressions'] or 0),
            'orders': int(r['orders'] or 0),
            'ordersAmount': round(float(r['orders_amount'] or 0), 2),
            'threshold': threshold,
            'exceeded': expense >= threshold,
            'overBy': round(max(0.0, expense - threshold), 2),
        })
    out.sort(key=lambda x: -x['expense'])
    return out


def build_alerts(store_pks, stat_date: str | None = None) -> dict:
    """组装告警集合 —— 前端登录后弹窗与页面提醒共用同一份数据。

    dailySpendAlerts 为「必须弹窗」的严重告警（当日花费 ≥ 阈值）。
    """
    day = stat_date or perf.today_msk()
    spend_rows = daily_spend(store_pks, day)
    daily_alerts = [{
        'type': 'daily_spend',
        'severity': 'danger',
        'storePk': s['storePk'],
        'storeId': s['storeId'],
        'storeName': s['storeName'],
        'title': s['storeName'],
        'statDate': s['statDate'],
        'expense': s['expense'],
        'threshold': s['threshold'],
        'overBy': s['overBy'],
        'orders': s['orders'],
        'message': '店铺「%s」今日（%s）广告已花费 %.0f₽，达到 %.0f₽ 告警线，超出 %.0f₽'
                   % (s['storeName'], s['statDate'], s['expense'], s['threshold'], s['overBy']),
    } for s in spend_rows if s['exceeded']]

    products = evaluate_products(store_pks)
    return {
        'statDate': day,
        'dailySpendAlerts': daily_alerts,
        'hasDailyWarning': bool(daily_alerts),
        'productAlertCounts': {
            'testComplete': len(products['testComplete']),
            'lowCtr': len(products['lowCtr']),
            'noConversion': len(products['noConversion']),
        },
        'totalDailySpend': round(sum(s['expense'] for s in spend_rows), 2),
    }


def build_overview(store_pks) -> dict:
    """「AI广告监控」页面看板数据（一次取全）。"""
    cfg = get_settings()
    today = perf.today_msk()

    if not store_pks:
        stores = []
    else:
        stores = query(
            'SELECT id, store_id, alias, perf_client_id, perf_client_secret,'
            ' ad_monitor_enabled, auth_status FROM stores WHERE id IN (%s) ORDER BY id'
            % _in_clause(store_pks),
            list(store_pks),
        )

    store_rows, configured = [], 0
    for s in stores or []:
        ok = bool((s.get('perf_client_id') or '').strip() and (s.get('perf_client_secret') or ''))
        if ok:
            configured += 1
        last_sync = query(
            'SELECT status, message, created_at FROM ad_sync_log WHERE store_pk = ?'
            ' ORDER BY id DESC LIMIT 1', (s['id'],), one=True)
        campaign_count = query(
            'SELECT COUNT(*) AS c FROM ad_campaigns WHERE store_pk = ?', (s['id'],), one=True)
        store_rows.append({
            'storePk': s['id'],
            'storeId': s['store_id'],
            'storeName': s.get('alias') or s['store_id'],
            'credentialsReady': ok,
            'perfClientId': s.get('perf_client_id') or '',
            'monitorEnabled': bool(s.get('ad_monitor_enabled', 1)),
            'campaignCount': (campaign_count or {}).get('c', 0),
            'lastSync': (last_sync or {}).get('created_at', ''),
            'lastSyncStatus': (last_sync or {}).get('status', ''),
            'lastSyncMessage': (last_sync or {}).get('message', ''),
        })

    spend = daily_spend(store_pks, today)
    products = evaluate_products(store_pks, cfg)

    return {
        'statDate': today,
        'settings': cfg,
        'totals': {
            'stores': len(store_rows),
            'storesConfigured': configured,
            'storesPending': len(store_rows) - configured,
            'todaySpend': round(sum(s['expense'] for s in spend), 2),
            'todayOrders': sum(s['orders'] for s in spend),
            'todayClicks': sum(s['clicks'] for s in spend),
            'warningStores': sum(1 for s in spend if s['exceeded']),
            'metricsCount': products['metricsCount'],
        },
        'stores': store_rows,
        'dailySpend': spend,
        'products': products['all'],
        'buckets': {
            'testComplete': products['testComplete'],
            'lowCtr': products['lowCtr'],
            'noConversion': products['noConversion'],
        },
    }


def monitorable_store_pks(store_pks) -> list:
    """过滤出真正开启监控、且配了凭证的店铺。"""
    if not store_pks:
        return []
    rows = query(
        'SELECT id, perf_client_id, perf_client_secret, ad_monitor_enabled'
        ' FROM stores WHERE id IN (%s)' % _in_clause(store_pks),
        list(store_pks),
    )
    out = []
    for r in rows or []:
        if not r.get('ad_monitor_enabled', 1):
            continue
        if not (r.get('perf_client_id') or '').strip():
            continue
        if not r.get('perf_client_secret'):
            continue
        out.append(r['id'])
    return out
