# -*- coding: utf-8 -*-
"""广告写操作服务：CPC 活动预算 / 商品出价（建议 3）

范围（只做这两类写入）：
  1. 改 CPC 活动的预算/档期        PATCH /api/client/campaign/{campaignId}
  2. 改 CPC 活动内商品的出价        PUT   /api/client/campaign/{campaignId}/products
  3. 只读参考：按订单付费的固定费率  POST  /api/client/search_promo/get_cpo_min_bids

安全约束（硬编码在服务层，前端改不了）：
  · 先 preview 再 apply；apply 必须显式传 confirm=True
  · 预算：单次变动幅度 ≤ 100%，新日预算 100–100000 ₽
  · 出价：单次变动幅度 ≤ 100%，单次出价 1–5000 ₽
  · 活动状态为 FINISHED / ARCHIVED 时拒绝写入（Ozon 也不允许）
  · 每次写入都落 ad_ops_log（成功失败都记，含 before/after 与 Ozon 原始返回）

注意 PUT /campaign/{id}/products 是**覆盖**语义：只传目标商品会把其它商品踢出活动。
因此 apply_bids 会先拉全量商品，合并改动后再整体提交。
"""
from __future__ import annotations

import json

from db import execute, query
from services import ozon_performance_api as perf

# 硬性护栏（如需调整，改这里并补测试，不要从接口透传）
GUARD = {
    'max_change_ratio': 1.0,          # 单次变动幅度上限（100%）
    'min_daily_budget_rub': 100.0,
    'max_daily_budget_rub': 100000.0,
    'min_bid_rub': 1.0,
    'max_bid_rub': 5000.0,
    'blocked_states': ('CAMPAIGN_STATE_FINISHED', 'CAMPAIGN_STATE_ARCHIVED'),
}


class AdOpsError(Exception):
    """写操作被护栏拦住，或参数不合法（不触发 Ozon 调用）。"""


# ============================================================
# 工具
# ============================================================

def _round2(value) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _ratio_change(before: float, after: float) -> float:
    if before <= 0:
        return 0.0
    return abs(after - before) / before


def _campaign_snapshot(store_pk: int, campaign_id) -> dict:
    campaign = perf.find_campaign(store_pk, campaign_id)
    if not campaign:
        raise AdOpsError('活动不存在或不属于该店铺：%s' % campaign_id)
    return {
        'campaignId': str(campaign.get('id') or campaign_id),
        'title': str(campaign.get('title') or ''),
        'state': str(campaign.get('state') or ''),
        'paymentType': str(campaign.get('PaymentType') or campaign.get('paymentType') or ''),
        'budgetRub': perf.micros_to_rub(campaign.get('budget')),
        'dailyBudgetRub': perf.micros_to_rub(campaign.get('dailyBudget')),
        'weeklyBudgetRub': perf.micros_to_rub(campaign.get('weeklyBudget')),
        'fromDate': str(campaign.get('fromDate') or ''),
        'toDate': str(campaign.get('toDate') or ''),
    }


def _ensure_writable(snapshot: dict) -> None:
    state = str(snapshot.get('state') or '')
    if state in GUARD['blocked_states']:
        raise AdOpsError('活动状态 %s 不允许修改（Ozon 侧只能克隆或新建）' % state)


def _log_op(store_pk, store_id, campaign_id, action, before, after,
            status, message, operator) -> int:
    operator = operator or {}
    return execute(
        'INSERT INTO ad_ops_log (store_pk, store_id, campaign_id, action, before_json,'
        ' after_json, status, message, operator_id, operator_name)'
        ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            store_pk, str(store_id or ''), str(campaign_id or ''), str(action or ''),
            json.dumps(before or {}, ensure_ascii=False)[:8000],
            json.dumps(after or {}, ensure_ascii=False)[:8000],
            str(status or 'ok'), str(message or '')[:500],
            operator.get('id'), str(operator.get('name') or operator.get('username') or '')[:80],
        ),
    )


# ============================================================
# 预算
# ============================================================

def preview_budget(store_pk: int, campaign_id, daily_budget_rub=None,
                   weekly_budget_rub=None) -> dict:
    """计算预算变更的影响与护栏结果，不发写请求。"""
    snapshot = _campaign_snapshot(store_pk, campaign_id)
    changes, errors, warnings = {}, [], []

    if daily_budget_rub is None and weekly_budget_rub is None:
        raise AdOpsError('至少传一个预算字段（dailyBudgetRub / weeklyBudgetRub）')

    for field, new_value, current_key in (
        ('dailyBudget', daily_budget_rub, 'dailyBudgetRub'),
        ('weeklyBudget', weekly_budget_rub, 'weeklyBudgetRub'),
    ):
        if new_value is None:
            continue
        new_rub = _round2(new_value)
        current_rub = _round2(snapshot.get(current_key))
        if new_rub < GUARD['min_daily_budget_rub']:
            errors.append('%s 低于下限 %s ₽' % (field, GUARD['min_daily_budget_rub']))
        if new_rub > GUARD['max_daily_budget_rub']:
            errors.append('%s 超过上限 %s ₽' % (field, GUARD['max_daily_budget_rub']))
        ratio = _ratio_change(current_rub, new_rub)
        if ratio > GUARD['max_change_ratio']:
            errors.append('%s 变动 %.0f%% 超过单次上限 %.0f%%'
                          % (field, ratio * 100, GUARD['max_change_ratio'] * 100))
        if current_rub <= 0:
            warnings.append('%s 当前未限制，本次将首次设置' % field)
        changes[field + 'Micros'] = perf.rub_to_micros(new_rub)
        changes[field + 'Rub'] = new_rub
        changes['current' + field[0].upper() + field[1:] + 'Rub'] = current_rub

    try:
        _ensure_writable(snapshot)
    except AdOpsError as exc:
        errors.append(str(exc))

    return {
        'ok': not errors,
        'errors': errors,
        'warnings': warnings,
        'before': snapshot,
        'changes': changes,
        'ratioLimit': GUARD['max_change_ratio'],
        'requiresConfirm': True,
    }


def apply_budget(store_pk: int, campaign_id, daily_budget_rub=None,
                 weekly_budget_rub=None, confirm: bool = False,
                 operator: dict | None = None) -> dict:
    """执行预算变更。护栏不通过时不调用 Ozon。"""
    preview = preview_budget(store_pk, campaign_id, daily_budget_rub, weekly_budget_rub)
    store = query('SELECT id, alias, store_id FROM stores WHERE id = ?', (store_pk,), one=True)
    store_id = (store or {}).get('store_id', '')
    if not preview['ok']:
        _log_op(store_pk, store_id, campaign_id, 'budget', preview['before'],
                preview['changes'], 'blocked', '; '.join(preview['errors']), operator)
        raise AdOpsError('; '.join(preview['errors']))
    if not confirm:
        raise AdOpsError('需要确认：请在提交时带 confirm=true')

    # preview 里的是 dailyBudgetMicros / weeklyBudgetMicros，发给 Ozon 要还原成
    # 官方字段名 dailyBudget / weeklyBudget（值为 micros 字符串）
    body = {}
    for key, value in preview['changes'].items():
        if key.endswith('Micros') and value:
            body[key[:-len('Micros')]] = value
    try:
        result = perf.patch_campaign(store_pk, campaign_id, body)
    except Exception as exc:  # noqa: BLE001 - 失败也要留审计
        _log_op(store_pk, store_id, campaign_id, 'budget', preview['before'],
                preview['changes'], 'error', str(exc), operator)
        raise
    _log_op(store_pk, store_id, campaign_id, 'budget', preview['before'],
            preview['changes'], 'ok', '预算已更新', operator)
    return {
        'ok': True,
        'campaignId': preview['before']['campaignId'],
        'applied': preview['changes'],
        'ozon': result,
    }


# ============================================================
# 出价
# ============================================================

def _min_bid_map(store_pk: int, skus) -> dict:
    """查 Ozon 最低出价；查不到就返回空字典（不阻断，仅少一层校验）。"""
    try:
        raw = perf.get_min_sku_bid(store_pk, skus)
    except Exception:  # noqa: BLE001 - 最低价是参考，失败不阻断
        return {}
    mapping = {}
    for item in (raw or {}).get('bids') or (raw or {}).get('items') or []:
        if isinstance(item, dict) and item.get('sku'):
            mapping[str(item['sku'])] = perf.micros_to_rub(
                item.get('bid') or item.get('minBid') or item.get('price'))
    return mapping


def preview_bids(store_pk: int, campaign_id, bids) -> dict:
    """校验出价变更：返回将要提交的全量商品列表（PUT 是覆盖语义）。"""
    snapshot = _campaign_snapshot(store_pk, campaign_id)
    errors, warnings = [], []
    current_products = perf.list_campaign_products_all(store_pk, campaign_id)
    current_map = {str(p.get('sku')): p for p in current_products if p.get('sku')}

    targets = {}
    for item in bids or []:
        sku = str((item or {}).get('sku') or '').strip()
        if not sku:
            continue
        # 兼容前端 camelCase（bidRub）与内部 snake_case（bid_rub）
        raw_bid = (item or {}).get('bidRub')
        if raw_bid in (None, ''):
            raw_bid = (item or {}).get('bid_rub')
        new_rub = _round2(raw_bid)
        if new_rub < GUARD['min_bid_rub'] or new_rub > GUARD['max_bid_rub']:
            errors.append('SKU %s 出价 %s ₽ 超出 %s–%s ₽ 允许范围'
                          % (sku, new_rub, GUARD['min_bid_rub'], GUARD['max_bid_rub']))
            continue
        targets[sku] = new_rub
    if not targets:
        raise AdOpsError('没有可更新的出价（bids 为空或 SKU 缺失）')

    min_bids = _min_bid_map(store_pk, list(targets.keys()))
    changed = []
    for sku, new_rub in targets.items():
        current = current_map.get(sku)
        current_rub = perf.micros_to_rub(current.get('bid')) if current else 0.0
        if new_rub < min_bids.get(sku, 0):
            errors.append('SKU %s 出价 %s ₽ 低于 Ozon 最低出价 %s ₽'
                          % (sku, new_rub, min_bids[sku]))
        ratio = _ratio_change(current_rub, new_rub)
        if ratio > GUARD['max_change_ratio']:
            errors.append('SKU %s 出价变动 %.0f%% 超过上限 %.0f%%'
                          % (sku, ratio * 100, GUARD['max_change_ratio'] * 100))
        if not current:
            warnings.append('SKU %s 当前不在活动内，本次会被加入' % sku)
        changed.append({
            'sku': sku, 'currentBidRub': current_rub, 'newBidRub': new_rub,
            'deltaPercent': round(ratio * 100, 1),
        })

    try:
        _ensure_writable(snapshot)
    except AdOpsError as exc:
        errors.append(str(exc))

    return {
        'ok': not errors,
        'errors': errors,
        'warnings': warnings,
        'before': snapshot,
        'changed': changed,
        'totalProducts': len(current_products),
        'ratioLimit': GUARD['max_change_ratio'],
        'requiresConfirm': True,
    }


def apply_bids(store_pk: int, campaign_id, bids, confirm: bool = False,
               operator: dict | None = None) -> dict:
    """执行出价变更：拉全量 → 合并 → 整体 PUT（避免覆盖语义误删商品）。"""
    preview = preview_bids(store_pk, campaign_id, bids)
    store = query('SELECT id, alias, store_id FROM stores WHERE id = ?', (store_pk,), one=True)
    store_id = (store or {}).get('store_id', '')
    if not preview['ok']:
        _log_op(store_pk, store_id, campaign_id, 'bids', preview['before'],
                preview['changed'], 'blocked', '; '.join(preview['errors']), operator)
        raise AdOpsError('; '.join(preview['errors']))
    if not confirm:
        raise AdOpsError('需要确认：请在提交时带 confirm=true')

    # 覆盖语义：以「当前全量商品」为底，替换目标 SKU 的出价
    current_products = perf.list_campaign_products_all(store_pk, campaign_id)
    targets = {str(c['sku']): c['newBidRub'] for c in preview['changed']}
    merged = []
    for product in current_products:
        sku = str(product.get('sku') or '')
        if not sku:
            continue
        if sku in targets:
            merged.append({'sku': sku, 'bid_rub': targets[sku]})
        else:
            merged.append({'sku': sku, 'bid_rub': perf.micros_to_rub(product.get('bid'))})
    for sku, bid_rub in targets.items():
        if not any(item['sku'] == sku for item in merged):
            merged.append({'sku': sku, 'bid_rub': bid_rub})

    try:
        result = perf.update_campaign_product_bids(store_pk, campaign_id, merged)
    except Exception as exc:  # noqa: BLE001
        _log_op(store_pk, store_id, campaign_id, 'bids', preview['before'],
                preview['changed'], 'error', str(exc), operator)
        raise
    _log_op(store_pk, store_id, campaign_id, 'bids', preview['before'],
            preview['changed'], 'ok', '出价已更新（%d 个商品）' % len(merged), operator)
    return {
        'ok': True,
        'campaignId': preview['before']['campaignId'],
        'changed': preview['changed'],
        'submittedProducts': len(merged),
        'ozon': result,
    }


# ============================================================
# 只读参考 + 审计查询
# ============================================================

def cpa_min_bids(store_pk: int, skus) -> dict:
    """按订单付费的固定费率（只读；Ozon 已废弃 CPA 自定义出价）。"""
    raw = perf.get_cpo_min_bids(store_pk, skus)
    rows = []
    for item in (raw or {}).get('bids') or []:
        if isinstance(item, dict):
            rows.append({'sku': str(item.get('sku') or ''),
                         'bidRub': _round2(item.get('bid'))})
    return {'bids': rows}


def list_products(store_pk: int, campaign_id) -> dict:
    """活动内商品与当前出价（只读，供「调出价」界面渲染）。"""
    snapshot = _campaign_snapshot(store_pk, campaign_id)
    products = []
    for item in perf.list_campaign_products_all(store_pk, campaign_id):
        sku = str(item.get('sku') or '')
        if not sku:
            continue
        products.append({
            'sku': sku,
            'title': str(item.get('title') or ''),
            'bidRub': perf.micros_to_rub(item.get('bid')),
        })
    return {'campaign': snapshot, 'products': products, 'total': len(products)}


def list_ops(store_pk=None, limit: int = 100) -> list:
    limit = max(1, min(int(limit or 100), 500))
    if store_pk:
        rows = query(
            'SELECT * FROM ad_ops_log WHERE store_pk = ? ORDER BY id DESC LIMIT ?',
            (int(store_pk), limit),
        )
    else:
        rows = query('SELECT * FROM ad_ops_log ORDER BY id DESC LIMIT ?', (limit,))
    out = []
    for row in rows:
        item = dict(row)
        for key in ('before_json', 'after_json'):
            try:
                item[key.replace('_json', '')] = json.loads(item.pop(key) or '{}')
            except (TypeError, ValueError):
                item[key.replace('_json', '')] = {}
        out.append(item)
    return out
