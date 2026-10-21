"""只读商品数据分析：直接调用 Ozon Seller API analytics/data。"""
from datetime import datetime
import json
import urllib.request

import requests
from flask import Blueprint, request

from db import get_connection
from models.account import Store
from utils.auth import get_current_user, require_login, visible_store_pks
from utils.response import error_response, handle_errors, success_response
from utils.security import decrypt_secret

seller_insights_bp = Blueprint('seller_insights', __name__)
OZON_ANALYTICS_URL = 'https://api-seller.ozon.ru/v1/analytics/data'
METRIC_KEYS = {
    'session_view_pdp', 'hits_view_pdp', 'hits_tocart_pdp', 'conv_tocart_pdp', 'ordered_units', 'revenue',
}


def _translate_product_titles(store_id, products):
    """批量翻译商品标题，并按店铺、SKU 和原文缓存。翻译失败不影响分析数据。"""
    pending = {}
    conn = get_connection()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS seller_insight_title_translations (
            store_id TEXT NOT NULL,
            sku TEXT NOT NULL,
            source_title TEXT NOT NULL,
            title_zh TEXT NOT NULL,
            PRIMARY KEY (store_id, sku, source_title)
        )""")
        for sku, product in products.items():
            title = str(product.get('title') or '').strip()
            if not title or any('\u4e00' <= char <= '\u9fff' for char in title):
                product['titleZh'] = title
                continue
            cached = conn.execute(
                "SELECT title_zh FROM seller_insight_title_translations WHERE store_id = ? AND sku = ? AND source_title = ?",
                (str(store_id), str(sku), title),
            ).fetchone()
            if cached:
                product['titleZh'] = cached['title_zh']
            else:
                pending[str(sku)] = title

        if not pending:
            return
        from services.ai_config_service import get_ai_config
        config = get_ai_config()
        if not config.get('api_key') or not config.get('base_url') or not config.get('model'):
            return

        for offset in range(0, len(pending), 40):
            batch = dict(list(pending.items())[offset:offset + 40])
            body = json.dumps({
                'model': config['model'],
                'messages': [
                    {'role': 'system', 'content': '将俄罗斯电商商品标题准确翻译成简体中文，保留品牌、型号、数字和单位。只返回 JSON：{"translations":{"SKU":"中文标题"}}。'},
                    {'role': 'user', 'content': json.dumps(
                        [{'sku': sku, 'title': title} for sku, title in batch.items()], ensure_ascii=False)},
                ],
                'response_format': {'type': 'json_object'},
                'temperature': 0.1,
            }).encode('utf-8')
            req = urllib.request.Request(
                config['base_url'].rstrip('/') + '/chat/completions', data=body,
                headers={'Content-Type': 'application/json', 'Authorization': f"Bearer {config['api_key']}"},
            )
            try:
                with urllib.request.urlopen(req, timeout=35) as response:
                    result = json.loads(response.read().decode('utf-8'))
                content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                translations = json.loads(content).get('translations', {})
                if isinstance(translations, list):
                    translations = {str(item.get('sku')): item.get('titleZh') or item.get('title_zh') or item.get('translation') for item in translations if isinstance(item, dict)}
                if not isinstance(translations, dict):
                    continue
                for sku, title in batch.items():
                    translated = str(translations.get(sku) or '').strip()
                    if translated:
                        products[sku]['titleZh'] = translated
                        conn.execute(
                            "INSERT OR REPLACE INTO seller_insight_title_translations (store_id, sku, source_title, title_zh) VALUES (?, ?, ?, ?)",
                            (str(store_id), sku, title, translated),
                        )
                conn.commit()
            except Exception:
                continue
    finally:
        conn.close()


@seller_insights_bp.route('/seller-insights', methods=['POST'])
@handle_errors
@require_login
def get_seller_insights():
    body = request.get_json(silent=True) or {}
    store_pk = body.get('storeId')
    if not str(store_pk or '').isdigit():
        return error_response('请选择店铺', 400)

    user = get_current_user()
    allowed = visible_store_pks(user)
    if allowed is not None and int(store_pk) not in allowed:
        return error_response('无权访问该店铺', 403)
    store = Store.find_by_id(int(store_pk))
    if not store:
        return error_response('店铺不存在', 404)

    client_id = store.get('client_id') or store.get('store_id')
    api_key = decrypt_secret(store.get('api_key'))
    if not client_id or not api_key:
        return error_response('该店铺未配置 Seller API 凭证', 400)

    date_from = str(body.get('dateFrom') or '')
    date_to = str(body.get('dateTo') or '')
    try:
        start = datetime.strptime(date_from, '%Y-%m-%d').date()
        end = datetime.strptime(date_to, '%Y-%m-%d').date()
    except ValueError:
        return error_response('请选择有效的开始和结束日期', 400)
    if start > end:
        return error_response('开始日期不能晚于结束日期', 400)
    if (end - start).days > 90:
        return error_response('单次查询最多选择 91 天', 400)

    metrics = body.get('metrics') or list(METRIC_KEYS)
    metrics = list(dict.fromkeys(key for key in metrics if key in METRIC_KEYS))
    if not metrics:
        return error_response('至少选择一个指标', 400)
    dimension = body.get('dimension')
    if dimension not in ('sku', 'sku_day'):
        dimension = 'sku'
    limit = body.get('limit', 500)
    try:
        limit = max(1, min(1000, int(limit)))
    except (TypeError, ValueError):
        limit = 500

    payload = {
        'date_from': date_from,
        'date_to': date_to,
        'metrics': metrics,
        'dimension': ['sku', 'day'] if dimension == 'sku_day' else ['sku'],
        'filters': [],
        'sort': [{'key': metrics[0], 'order': 'DESC'}],
        'limit': limit,
        'offset': 0,
    }
    try:
        response = requests.post(
            OZON_ANALYTICS_URL,
            json=payload,
            headers={
                'Client-Id': str(client_id),
                'Api-Key': api_key,
                'Content-Type': 'application/json',
            },
            timeout=45,
        )
    except requests.RequestException as exc:
        return error_response(f'Ozon 请求失败：{exc}', 502)

    try:
        result = response.json()
    except ValueError:
        result = {}
    if not response.ok:
        message = result.get('message') or result.get('error') or f'HTTP {response.status_code}'
        return error_response(f'Ozon 分析接口返回错误：{message}', 502)

    report = result.get('result') or {}
    rows = report.get('data') or []
    sku_ids = {
        str(dimension.get('id'))
        for row in rows
        for dimension in (row.get('dimensions') or [])
        if str(dimension.get('id') or '').isdigit()
    }
    products = {}
    for row in rows:
        for item in row.get('dimensions') or []:
            sku = str(item.get('id') or '')
            title = str(item.get('name') or '').strip()
            if sku.isdigit() and title:
                products[sku] = {'title': title, 'image': ''}
    if sku_ids:
        placeholders = ','.join('?' for _ in sku_ids)
        conn = get_connection()
        try:
            product_rows = conn.execute(
                f"SELECT sku, sku_id, title, image FROM online_products WHERE store_id = ? AND (sku_id IN ({placeholders}) OR sku IN ({placeholders}))",
                [store['id'], *sku_ids, *sku_ids],
            ).fetchall()
            for product in product_rows:
                for key in (product['sku_id'], product['sku']):
                    if key:
                        products[str(key)] = {
                            'title': product['title'],
                            'image': product['image'],
                        }
        finally:
            conn.close()
        _translate_product_titles(store['id'], products)
    return success_response(data={
        'store': store.get('alias') or store.get('store_id'),
        'dateFrom': date_from,
        'dateTo': date_to,
        'metrics': metrics,
        'totals': report.get('totals') or [],
        'rows': rows,
        'products': products,
        'timestamp': result.get('timestamp'),
    })
