# -*- coding: utf-8 -*-
"""
Ozon 促销活动监控与自动移除服务

【业务目标】
Ozon 平台会把商品**自动拉进各类促销活动**（Максимальный бустинг / Эластичный
бустинг 等），拉进去之后商品会以「活动价」成交。如果活动价低于业务员在 Ozon
后台为该商品设定的**促销活动限制价（min_price，价格地板）**，就等于在亏本卖，
必须尽快把商品移出活动。

ERP 的职责是：**主动发现 + 主动移除**（涂总决策：检测到就自动移除，同时留记录），
而不是等业务员自己去后台翻。

【两条检测规则】

  R1 促销价低于限制价（核心规则）
     Ozon `/v1/actions/products` 返回活动内每个商品的 `action_price`（活动价，即
     当前实际成交价）与 `price`（商品原价）。
     Ozon `/v3/product/info/list` 返回该商品的 `min_price`——这就是业务员在
     Ozon 后台「价格」里设置的**最低价/价格地板**，涂总口中的「促销活动限制价格」。
     命中条件：`action_price < min_price`。
     含义：活动把价格压到了业务员设定的地板以下，需要立即移出活动。

  R2 平台自动拉入（辅助信号）
     `/v1/actions/products` 返回的 `add_mode` 字段：
       - `"AUTO"`   = 平台自动把商品拉进活动（本次治理的主要对象）
       - `"MANUAL"` = 业务员自己手动加入（业务员知情，不告警）
     用于区分"平台偷偷拉进去的"和"业务员自己报名的"。

  R3 积分评价 / 快速收集评价（独立检测，不涉及移除）
     这是一个功能的两个名字，统一以「积分评价」为准。
     Ozon `/v3/product/info/list` 返回 `promotions: [{"is_enabled": bool,
     "type": "REVIEWS_PROMO"}]`，`type == "REVIEWS_PROMO"` 即该商品的积分评价开关。
     全店商品都会带这个字段，是否开启看 `is_enabled`。
     命中即提示业务员（该项**不做自动操作**）。

【执行动作】
命中 R1 的商品 → 自动移出活动：
  1. 主手段 `deactivate_action_products(action_id, [product_id])`
     —— `POST /v1/actions/products/deactivate`，把商品拉出活动。
  2. 辅助手段 `delete_auto_add_products(action_id, auto_add_date, [product_id])`
     —— `POST /v1/actions/auto-add/products/delete`，把商品从「自动添加名单」里摘掉，
        防止 Ozon 在下一个 `auto_add_date` 又把它自动拉回去。这一步只在活动有
        `auto_add_dates` 时执行。
  3. 无论成败都写 `promo_removal_logs` 留痕（谁、什么时候、哪个商品、哪个活动、
     活动价、限制价、结果、失败原因）。

【重要约束】
- Ozon **没有删除活动本身的接口**，只能把商品移出活动（`POST /v1/actions` 返回 405）。
- 活动开始后可能不允许退出，接口会报错——此时记录失败并保持告警，由业务员人工处理。
- 移除操作有配额，且滥用可能影响店铺评分，故必须带「限制价」这层业务判断，不做无差别移除。
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from db import query, execute
from services import ozon_api

# 单次扫描最多处理的活动数（防御：异常情况下活动列表爆炸）
MAX_ACTIONS_PER_SCAN = 50
# 移除失败的退避重试次数
REMOVE_RETRIES = 2
REMOVE_BACKOFF_SECONDS = 3


# ============================================================
# 表结构自愈
# ============================================================
# 迁移本来在 db.init_db() 里执行，但模块被「直接导入」时（脚本/测试/
# 未走 create_app 的场景）迁移不会跑，于是这里做一次幂等自检并自愈，
# 避免出现「代码已更新、表还是旧结构 → no such column」。
# 与 ad_monitor_service._ensure_daily_table() 同一套路。

_LOGS_DDL = (
    "CREATE TABLE IF NOT EXISTS promo_removal_logs ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "store_pk INTEGER NOT NULL,"
    "store_id TEXT NOT NULL DEFAULT '',"
    "action_id TEXT NOT NULL DEFAULT '',"
    "action_title TEXT NOT NULL DEFAULT '',"
    "product_id TEXT NOT NULL DEFAULT '',"
    "offer_id TEXT NOT NULL DEFAULT '',"
    "sku TEXT NOT NULL DEFAULT '',"
    "title TEXT NOT NULL DEFAULT '',"
    "action_price REAL NOT NULL DEFAULT 0,"
    "base_price REAL NOT NULL DEFAULT 0,"
    "min_price REAL NOT NULL DEFAULT 0,"
    "discount_percent REAL NOT NULL DEFAULT 0,"
    "add_mode TEXT NOT NULL DEFAULT '',"
    "auto_add_date TEXT NOT NULL DEFAULT '',"
    "result TEXT NOT NULL DEFAULT '',"
    "message TEXT NOT NULL DEFAULT '',"
    "trigger_type TEXT NOT NULL DEFAULT 'auto',"
    "operator_id INTEGER,"
    "operator_name TEXT NOT NULL DEFAULT '',"
    "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
)

_STATE_DDL = (
    "CREATE TABLE IF NOT EXISTS promo_product_state ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "store_pk INTEGER NOT NULL,"
    "store_id TEXT NOT NULL DEFAULT '',"
    "product_id TEXT NOT NULL DEFAULT '',"
    "offer_id TEXT NOT NULL DEFAULT '',"
    "sku TEXT NOT NULL DEFAULT '',"
    "title TEXT NOT NULL DEFAULT '',"
    "base_price REAL NOT NULL DEFAULT 0,"
    "min_price REAL NOT NULL DEFAULT 0,"
    "auto_action_count INTEGER NOT NULL DEFAULT 0,"
    "reviews_promo_enabled INTEGER NOT NULL DEFAULT 0,"
    "reviews_promo_type TEXT NOT NULL DEFAULT '',"
    "last_scan_at TEXT NOT NULL DEFAULT '',"
    "updated_at TEXT NOT NULL DEFAULT (datetime('now')),"
    "UNIQUE(store_pk, product_id))"
)


def _ensure_tables() -> None:
    """确保促销相关表存在（幂等）。"""
    try:
        execute(_LOGS_DDL)
        execute("CREATE INDEX IF NOT EXISTS idx_promo_logs_store "
                "ON promo_removal_logs(store_pk, id)")
        execute("CREATE INDEX IF NOT EXISTS idx_promo_logs_product "
                "ON promo_removal_logs(product_id)")
        execute(_STATE_DDL)
        execute("CREATE INDEX IF NOT EXISTS idx_promo_state_store "
                "ON promo_product_state(store_pk)")
    except Exception as exc:  # noqa: BLE001
        print('[促销监控] 建表失败：%s' % exc)


_ensure_tables()


# ============================================================
# 工具
# ============================================================

def _f(value) -> float:
    """宽松转 float（Ozon 数值字段常以字符串返回）"""
    if value is None or value == '':
        return 0.0
    try:
        return float(str(value).replace(',', '.').replace(' ', '').replace('₽', ''))
    except (TypeError, ValueError):
        return 0.0


def _s(value) -> str:
    return '' if value is None else str(value)


def _in_clause(values):
    return ','.join('?' * len(values))


def _store_name_map(store_pks) -> dict:
    if not store_pks:
        return {}
    rows = query(
        'SELECT id, store_id, alias FROM stores WHERE id IN (%s)' % _in_clause(store_pks),
        list(store_pks),
    )
    return {r['id']: (r.get('alias') or r.get('store_id') or '') for r in rows or []}


def _store_credentials(store) -> tuple:
    """从 stores 行取出 (client_id, api_key_plain)。

    【重要】api_key 在库中是加密存储的（形如 `enc$v1$<nonce>$<cipher>`）。
    直接把它当明文发给 Ozon 会被判定为 `Invalid Api-Key`，因此这里
    统一走 decrypt_secret 解密；对历史明文数据 decrypt_secret 会原样返回。
    """
    if not store:
        return None, None
    client_id = _s(store.get('client_id')) or _s(store.get('store_id'))
    raw_key = store.get('api_key')
    api_key = None
    if raw_key:
        try:
            from utils.security import decrypt_secret
            api_key = decrypt_secret(raw_key)
        except Exception:  # noqa: BLE001 — 解密异常时退回原值，避免整体失败
            api_key = _s(raw_key)
    return (client_id or None), (api_key or None)


def active_store_pks(candidate_pks) -> list:
    """过滤出配好 Seller API 凭证的店铺（促销接口走 Seller API，非 Performance API）。"""
    if not candidate_pks:
        return []
    rows = query(
        'SELECT id, client_id, api_key, auth_status FROM stores WHERE id IN (%s)'
        % _in_clause(candidate_pks),
        list(candidate_pks),
    )
    out = []
    for r in rows or []:
        client_id, api_key = _store_credentials(r)
        if not client_id or not api_key:
            continue
        if (r.get('auth_status') or '') == 'disabled':
            continue
        out.append(r['id'])
    return out


# ============================================================
# 数据采集
# ============================================================

def _fetch_action_products_grouped(action_id, client_id, api_key) -> tuple:
    """拉取活动内全部商品，返回 (product_id -> 行, 原始列表)。"""
    rows = ozon_api.get_all_action_products(action_id, client_id=client_id, api_key=api_key)
    return {int(r.get('id') or 0): r for r in rows}, rows


def _fetch_min_prices(product_ids, client_id, api_key) -> dict:
    """批量取商品信息，抽出 min_price（业务员设的价格地板）与促销/积分评价状态。

    Returns:
        dict: product_id(int) -> {
            'min_price': float, 'price': float, 'old_price': float,
            'marketing_price': float, 'reviews_promo_enabled': bool,
            'reviews_promo_type': str, 'offer_id': str, 'name': str
        }
    """
    out = {}
    ids = [int(x) for x in product_ids if x]
    if not ids:
        return out
    # get_product_info_list 内部已按 100 分批
    try:
        items = ozon_api.get_product_info_list(ids, client_id=client_id, api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        print('[促销监控] 拉取商品详情失败：%s' % exc)
        return out

    for it in items or []:
        pid = int(it.get('id') or it.get('product_id') or 0)
        if not pid:
            continue
        promos = it.get('promotions') or []
        review_enabled, review_type = False, ''
        for p in promos:
            if isinstance(p, dict) and _s(p.get('type')).upper() == 'REVIEWS_PROMO':
                review_type = 'REVIEWS_PROMO'
                review_enabled = bool(p.get('is_enabled'))
                break
        out[pid] = {
            'min_price': _f(it.get('min_price')),
            'price': _f(it.get('price')),
            'old_price': _f(it.get('old_price')),
            'marketing_price': _f(it.get('marketing_price')),
            'reviews_promo_enabled': review_enabled,
            'reviews_promo_type': review_type,
            'offer_id': _s(it.get('offer_id')),
            'name': _s(it.get('name')),
            'currency_code': _s(it.get('currency_code')),
        }
    return out


# ============================================================
# 扫描：一次拉全量，产出命中清单
# ============================================================

def scan_store(store_pk: int, *, collect_only: bool = False) -> dict:
    """扫描单个店铺的全部促销活动，找出「活动价低于限制价」的自动拉入商品。

    Args:
        store_pk: stores.id
        collect_only: True 时只采集不落库（用于预演/测试）

    Returns:
        dict: {
            'storePk', 'storeId', 'storeName',
            'actions': [...],      # 活动概览
            'hits': [...],         # 命中清单（活动价 < 限制价）
            'autoProducts': [...], # 全部被平台自动拉入的商品（含未超线的）
            'reviewPromos': [...], # 开启了积分评价的商品
            'scanned': int, 'error': str|None
        }
    """
    store = query('SELECT * FROM stores WHERE id = ?', (store_pk,), one=True)
    if not store:
        return {'storePk': store_pk, 'error': '店铺不存在', 'hits': [], 'actions': []}

    client_id, api_key = _store_credentials(store)
    store_id = _s(store.get('store_id'))
    store_name = _s(store.get('alias')) or store_id
    base = {
        'storePk': store_pk, 'storeId': store_id, 'storeName': store_name,
        'actions': [], 'hits': [], 'autoProducts': [], 'reviewPromos': [],
        'scanned': 0, 'error': None,
    }
    if not client_id or not api_key:
        base['error'] = '未配置 Seller API 凭证'
        return base

    # 1) 活动清单
    try:
        actions = ozon_api.get_actions(client_id=client_id, api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        base['error'] = '拉取活动列表失败：%s' % exc
        return base

    actions = list(actions or [])[:MAX_ACTIONS_PER_SCAN]
    all_auto, all_hits, action_rows = [], [], []
    scanned_pids = set()

    for act in actions:
        action_id = act.get('id')
        if not action_id:
            continue
        action_title = _s(act.get('title'))
        auto_dates = act.get('auto_add_dates') or []
        auto_add_date = _s(auto_dates[0]) if auto_dates else ''
        participating = bool(act.get('is_participating'))
        action_rows.append({
            'actionId': _s(action_id),
            'title': action_title,
            'actionType': _s(act.get('action_type')),
            'isParticipating': participating,
            'productsCount': int(act.get('participating_products_count') or 0),
            'potentialCount': int(act.get('potential_products_count') or 0),
            'dateStart': _s(act.get('date_start')),
            'dateEnd': _s(act.get('date_end')),
            'autoAddDates': [_s(x) for x in auto_dates],
            'discountType': _s(act.get('discount_type')),
            'discountValue': _f(act.get('discount_value')),
        })

        # 只处理参与中的活动（未参与=没商品在里面，拉也拉不到）
        if not participating:
            continue
        try:
            by_pid, raw = _fetch_action_products_grouped(action_id, client_id, api_key)
        except Exception as exc:  # noqa: BLE001
            print('[促销监控] 活动 %s 商品拉取失败：%s' % (action_id, exc))
            continue
        if not raw:
            continue

        # 2) 取这批商品的 min_price / 积分评价状态
        info_map = _fetch_min_prices(list(by_pid.keys()), client_id, api_key)
        if not collect_only:
            scanned_pids.update(info_map.keys())

        for pid, prow in by_pid.items():
            info = info_map.get(pid) or {}
            add_mode = _s(prow.get('add_mode')).upper()
            action_price = _f(prow.get('action_price'))
            base_price = _f(prow.get('price'))
            min_price = _f(info.get('min_price'))
            offer_id = _s(info.get('offer_id')) or _s(prow.get('offer_id'))
            title = _s(info.get('name'))
            discount_pct = 0.0
            if base_price > 0:
                discount_pct = round((base_price - action_price) / base_price * 100, 2)

            if add_mode == 'AUTO':
                all_auto.append({
                    'storePk': store_pk, 'storeId': store_id, 'storeName': store_name,
                    'actionId': _s(action_id), 'actionTitle': action_title,
                    'productId': _s(pid), 'offerId': offer_id, 'title': title,
                    'actionPrice': action_price, 'basePrice': base_price,
                    'minPrice': min_price, 'discountPercent': discount_pct,
                    'addMode': add_mode, 'autoAddDate': auto_add_date,
                })

            # R1 核心命中：活动价 < 业务员设的限制价
            if min_price > 0 and action_price > 0 and action_price < min_price:
                all_hits.append({
                    'storePk': store_pk, 'storeId': store_id, 'storeName': store_name,
                    'actionId': _s(action_id), 'actionTitle': action_title,
                    'productId': _s(pid), 'offerId': offer_id, 'title': title,
                    'actionPrice': action_price, 'basePrice': base_price,
                    'minPrice': min_price, 'discountPercent': discount_pct,
                    'diff': round(action_price - min_price, 2),
                    'gapPercent': round((min_price - action_price) / min_price * 100, 2)
                    if min_price else 0.0,
                    'addMode': add_mode or 'UNKNOWN',
                    'isAuto': add_mode == 'AUTO',
                    'autoAddDate': auto_add_date,
                    'severity': 'danger' if add_mode == 'AUTO' else 'warning',
                    'reason': (
                        '平台自动拉入' if add_mode == 'AUTO' else '业务员手动加入'
                    ) + '；活动价 %.2f 低于限制价 %.2f' % (action_price, min_price),
                })

    # 3) 全店积分评价状态（按商品维度，与活动无关）
    review_promos = []
    if scanned_pids:
        for pid, info in _fetch_min_prices(list(scanned_pids), client_id, api_key).items():
            if info.get('reviews_promo_enabled'):
                review_promos.append({
                    'storePk': store_pk, 'storeId': store_id, 'storeName': store_name,
                    'productId': _s(pid), 'offerId': info.get('offer_id'),
                    'title': info.get('name'),
                    'basePrice': info.get('price'), 'minPrice': info.get('min_price'),
                    'promoType': info.get('reviews_promo_type') or 'REVIEWS_PROMO',
                })

    base.update({
        'actions': action_rows,
        'hits': all_hits,
        'autoProducts': all_auto,
        'reviewPromos': review_promos,
        'scanned': len(scanned_pids),
    })

    if not collect_only:
        _persist_state(store_pk, store_id, all_auto, review_promos)
    return base


def scan_stores(store_pks) -> dict:
    """批量扫描多个店铺，汇总结果。"""
    stores, hits, autos, reviews, errors = [], [], [], [], []
    for pk in store_pks or []:
        r = scan_store(pk)
        stores.append({
            'storePk': pk, 'storeId': r.get('storeId'), 'storeName': r.get('storeName'),
            'actionCount': len(r.get('actions') or []),
            'hitCount': len(r.get('hits') or []),
            'autoCount': len(r.get('autoProducts') or []),
            'reviewPromoCount': len(r.get('reviewPromos') or []),
            'scanned': r.get('scanned', 0),
            'error': r.get('error'),
        })
        hits.extend(r.get('hits') or [])
        autos.extend(r.get('autoProducts') or [])
        reviews.extend(r.get('reviewPromos') or [])
        if r.get('error'):
            errors.append({'storePk': pk, 'message': r['error']})
    # 命中的排前面（自动拉入优先、差价大的优先）
    hits.sort(key=lambda h: (0 if h.get('isAuto') else 1, -abs(h.get('diff') or 0)))
    return {
        'stores': stores,
        'hits': hits,
        'autoProducts': autos,
        'reviewPromos': reviews,
        'errors': errors,
        'scannedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def _persist_state(store_pk, store_id, auto_products, review_promos) -> None:
    """把扫描结果落到 promo_product_state（每个商品一行，UPSERT）。"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    auto_cnt = {}
    meta = {}
    for a in auto_products:
        pid = a['productId']
        auto_cnt[pid] = auto_cnt.get(pid, 0) + 1
        meta[pid] = a
    for r in review_promos:
        pid = r['productId']
        meta.setdefault(pid, {
            'productId': pid, 'offerId': r.get('offerId'), 'title': r.get('title'),
            'basePrice': r.get('basePrice'), 'minPrice': r.get('minPrice'),
        })
        meta[pid]['_review'] = True
        meta[pid]['_reviewType'] = r.get('promoType') or 'REVIEWS_PROMO'

    for pid, m in meta.items():
        try:
            execute(
                """INSERT INTO promo_product_state
                   (store_pk, store_id, product_id, offer_id, title, base_price,
                    min_price, auto_action_count, reviews_promo_enabled,
                    reviews_promo_type, last_scan_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(store_pk, product_id) DO UPDATE SET
                     store_id=excluded.store_id, offer_id=excluded.offer_id,
                     title=excluded.title, base_price=excluded.base_price,
                     min_price=excluded.min_price,
                     auto_action_count=excluded.auto_action_count,
                     reviews_promo_enabled=excluded.reviews_promo_enabled,
                     reviews_promo_type=excluded.reviews_promo_type,
                     last_scan_at=excluded.last_scan_at, updated_at=excluded.updated_at""",
                (store_pk, store_id, pid, _s(m.get('offerId')), _s(m.get('title')),
                 _f(m.get('basePrice')), _f(m.get('minPrice')), auto_cnt.get(pid, 0),
                 1 if m.get('_review') else 0, _s(m.get('_reviewType')),
                 now, now),
            )
        except Exception as exc:  # noqa: BLE001
            print('[促销监控] 状态落库失败 product=%s: %s' % (pid, exc))


# ============================================================
# 执行：移除出活动
# ============================================================

def remove_products(hits, *, operator=None, trigger_type='auto',
                    dry_run=False) -> dict:
    """把命中商品移出促销活动（自动移除）。

    对每个命中项执行双保险：
      1. deactivate（主）——把商品拉出活动
      2. delete auto-add（辅）——从自动添加名单摘掉，防止下次又被拉回

    Args:
        hits: scan_store() 产出的命中清单（或前端传入的精简版，需含
              actionId / productId / offerId / minPrice / actionPrice 等）
        operator: 操作人（手动触发时传入，用于留痕）
        trigger_type: 'auto'（定时/检测即移除）| 'manual'（业务员点按钮）
        dry_run: True 时只返回将要做什么，不真的调接口

    Returns:
        dict: {'removed': [...], 'failed': [...], 'skipped': [...], 'total': int}
    """
    removed, failed, skipped = [], [], []
    op_id = operator.get('id') if operator else None
    op_name = (operator.get('nickname') or operator.get('username')) if operator else ''

    for h in hits or []:
        action_id = h.get('actionId')
        product_id = h.get('productId')
        store_pk = h.get('storePk')
        if not action_id or not product_id or not store_pk:
            skipped.append({**h, 'message': '缺少 actionId/productId/storePk'})
            continue

        store = query('SELECT * FROM stores WHERE id = ?', (store_pk,), one=True)
        if not store:
            failed.append({**h, 'message': '店铺不存在', 'result': 'failed'})
            _log_removal(h, 'failed', '店铺不存在', trigger_type, op_id, op_name)
            continue
        client_id, api_key = _store_credentials(store)
        if not client_id or not api_key:
            failed.append({**h, 'message': '未配置 Seller API 凭证', 'result': 'failed'})
            _log_removal(h, 'failed', '未配置 Seller API 凭证', trigger_type, op_id, op_name)
            continue

        if dry_run:
            skipped.append({**h, 'message': 'dry-run 不执行'})
            continue

        ok, msg = _do_remove(h, client_id, api_key)
        if ok:
            removed.append({**h, 'message': msg, 'result': 'removed'})
            _log_removal(h, 'removed', msg, trigger_type, op_id, op_name)
        else:
            failed.append({**h, 'message': msg, 'result': 'failed'})
            _log_removal(h, 'failed', msg, trigger_type, op_id, op_name)

    return {
        'removed': removed, 'failed': failed, 'skipped': skipped,
        'total': len(removed) + len(failed),
        'removedCount': len(removed), 'failedCount': len(failed),
        'skippedCount': len(skipped),
    }


def _do_remove(hit, client_id, api_key) -> tuple:
    """对单个命中执行移除（带退避重试）。返回 (是否成功, 说明)。"""
    action_id = int(hit['actionId'])
    pid = int(hit['productId'])
    auto_add_date = _s(hit.get('autoAddDate'))
    messages = []

    # 1) 主手段：移出活动
    ok_main, msg_main = _retry_call(
        lambda: ozon_api.deactivate_action_products(
            action_id, [pid], client_id=client_id, api_key=api_key)
    )
    messages.append('deactivate=%s' % ('ok' if ok_main else msg_main))
    if not ok_main:
        return False, '；'.join(messages)

    # 2) 辅助手段：从自动添加名单摘掉（有 auto_add_date 才做；失败不影响主结果）
    if auto_add_date and _s(hit.get('addMode')).upper() == 'AUTO':
        ok_aux, msg_aux = _retry_call(
            lambda: ozon_api.delete_auto_add_products(
                action_id, auto_add_date, [pid], client_id=client_id, api_key=api_key)
        )
        messages.append('auto-add-delete=%s' % ('ok' if ok_aux else msg_aux))
        if not ok_aux:
            messages.append('（已移出活动，但未从自动添加名单摘掉，下个周期可能被重新拉入）')

    return True, '；'.join(messages)


def _retry_call(fn) -> tuple:
    """带指数退避的重试调用。返回 (是否成功, 错误说明)。"""
    delay = REMOVE_BACKOFF_SECONDS
    last = ''
    for attempt in range(REMOVE_RETRIES + 1):
        try:
            fn()
            return True, ''
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            if attempt < REMOVE_RETRIES:
                time.sleep(delay)
                delay *= 2
    return False, last


def _log_removal(hit, result, message, trigger_type, op_id, op_name) -> None:
    """写移除留痕。"""
    try:
        execute(
            """INSERT INTO promo_removal_logs
               (store_pk, store_id, action_id, action_title, product_id, offer_id,
                sku, title, action_price, base_price, min_price, discount_percent,
                add_mode, auto_add_date, result, message, trigger_type,
                operator_id, operator_name)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hit.get('storePk'), _s(hit.get('storeId')), _s(hit.get('actionId')),
             _s(hit.get('actionTitle')), _s(hit.get('productId')), _s(hit.get('offerId')),
             _s(hit.get('sku')), _s(hit.get('title')), _f(hit.get('actionPrice')),
             _f(hit.get('basePrice')), _f(hit.get('minPrice')),
             _f(hit.get('discountPercent')), _s(hit.get('addMode')),
             _s(hit.get('autoAddDate')), result, message, trigger_type,
             op_id, op_name),
        )
    except Exception as exc:  # noqa: BLE001
        print('[促销监控] 留痕写入失败：%s' % exc)


# ============================================================
# 对外查询接口
# ============================================================

def get_logs(store_pks, limit=100, result=None) -> list:
    """移除留痕列表（按店铺过滤）。"""
    if not store_pks:
        return []
    sql = ('SELECT * FROM promo_removal_logs WHERE store_pk IN (%s)'
           % _in_clause(store_pks))
    params = list(store_pks)
    if result:
        sql += ' AND result = ?'
        params.append(result)
    sql += ' ORDER BY id DESC LIMIT ?'
    params.append(min(int(limit or 100), 500))
    rows = query(sql, params)
    names = _store_name_map(store_pks)
    out = []
    for r in rows or []:
        d = dict(r)
        d['storeName'] = names.get(r['store_pk'], '')
        d['resultText'] = {'removed': '已移除', 'failed': '移除失败',
                           'kept': '保留'}.get(r['result'], r['result'])
        out.append(d)
    return out


def get_state(store_pks) -> list:
    """商品促销状态快照（最近一次扫描）。"""
    if not store_pks:
        return []
    rows = query(
        'SELECT * FROM promo_product_state WHERE store_pk IN (%s)'
        ' ORDER BY auto_action_count DESC, reviews_promo_enabled DESC, id DESC'
        % _in_clause(store_pks),
        list(store_pks),
    )
    names = _store_name_map(store_pks)
    out = []
    for r in rows or []:
        d = dict(r)
        d['storeName'] = names.get(r['store_pk'], '')
        d['reviewsPromoEnabled'] = bool(r.get('reviews_promo_enabled'))
        out.append(d)
    return out


def get_stats(store_pks) -> dict:
    """统计摘要（供看板/弹窗使用）。"""
    if not store_pks:
        return {'hitProducts': 0, 'removed': 0, 'failed': 0, 'reviewPromos': 0,
                'autoProducts': 0, 'lastRemovalAt': ''}
    ph = _in_clause(store_pks)
    hit = query(
        'SELECT COUNT(DISTINCT product_id) AS c FROM promo_product_state'
        ' WHERE store_pk IN (%s) AND auto_action_count > 0' % ph, list(store_pks),
        one=True) or {}
    review = query(
        'SELECT COUNT(*) AS c FROM promo_product_state'
        ' WHERE store_pk IN (%s) AND reviews_promo_enabled = 1' % ph, list(store_pks),
        one=True) or {}
    removed = query(
        "SELECT COUNT(*) AS c FROM promo_removal_logs WHERE store_pk IN (%s)"
        " AND result = 'removed'" % ph, list(store_pks), one=True) or {}
    failed = query(
        "SELECT COUNT(*) AS c FROM promo_removal_logs WHERE store_pk IN (%s)"
        " AND result = 'failed'" % ph, list(store_pks), one=True) or {}
    last = query(
        'SELECT created_at FROM promo_removal_logs WHERE store_pk IN (%s)'
        ' ORDER BY id DESC LIMIT 1' % ph, list(store_pks), one=True) or {}
    return {
        'hitProducts': int(hit.get('c') or 0),
        'reviewPromos': int(review.get('c') or 0),
        'removed': int(removed.get('c') or 0),
        'failed': int(failed.get('c') or 0),
        'autoProducts': int(hit.get('c') or 0),
        'lastRemovalAt': _s(last.get('created_at')),
    }
