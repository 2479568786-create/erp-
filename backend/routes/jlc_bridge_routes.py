"""JLC 黑盒 sidecar 适配器：ERP 1688 商品 → JLC 工作台流水线。

职责：
- 把 ERP 采集箱里的 1688 商品（Product JSON）翻译成 JLC collector-capture payload；
- 服务端直连 127.0.0.1:8765 获取类目 rules_snapshot 并创建 JLC 商品；
- 前端后续通过 /jlc-workspace/api/* 同源直调 JLC 工作台（nginx 已代理 + cookie 鉴权）。
"""
import html
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from flask import Blueprint, request

from models.product import Product
from utils.auth import require_login
from utils.response import error_response, handle_errors, success_response

jlc_bridge_bp = Blueprint('jlc_bridge', __name__)

JLC_BASE = 'http://127.0.0.1:8765'
JLC_TIMEOUT = 30
MAX_SKUS = 10
GENERATED_SKU_PREFIX = re.compile(
    r'^(script-sku|dom-sku|dom-combo|combo-sku|local-spec-variant-offer-key)-', re.IGNORECASE
)


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _jlc_request(method, path, payload=None):
    """服务端直连 JLC sidecar（loopback 永远放行 local_network_only）。"""
    url = f'{JLC_BASE}{path}'
    data = None
    headers = {'Accept': 'application/json'}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=JLC_TIMEOUT) as resp:
            body = resp.read().decode('utf-8')
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(body)
        except Exception:
            detail = body[:500]
        return exc.code, detail


def _jlc_error_message(detail):
    if isinstance(detail, dict):
        for key in ('message', 'msg', 'detail', 'error'):
            value = detail.get(key)
            if value:
                if isinstance(value, dict):
                    return str(value.get('message') or value)[:300]
                return str(value)[:300]
        return json.dumps(detail, ensure_ascii=False)[:300]
    return str(detail)[:300]


def _clean_text(value):
    if value is None:
        return ''
    return html.unescape(str(value).strip())


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _http_urls(value):
    if isinstance(value, str):
        value = [value]
    out, seen = [], set()
    for item in value or []:
        if isinstance(item, dict):
            item = item.get('url') or item.get('src') or ''
        url = _clean_text(item)
        if url.startswith(('http://', 'https://')) and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _jlc_image_ok(url):
    """JLC 只允许下载 1688.com / alicdn.com 原始图（image_source_preflight）。"""
    host = ''
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or '').lower()
    except Exception:
        return False
    return host == '1688.com' or host.endswith('.1688.com') or host == 'alicdn.com' or host.endswith('.alicdn.com')


def _combo_pairs(sku):
    combo = sku.get('combo') if isinstance(sku.get('combo'), dict) else (sku.get('attributes') if isinstance(sku.get('attributes'), dict) else {})
    pairs = []
    for key, value in (combo or {}).items():
        name = _clean_text(key)
        text = _clean_text(value)
        if name and text:
            pairs.append((name, text))
    return pairs


def _real_1688_sku_id(sku):
    """只把数字形态（>=6 位）的 sku 标识当真实 1688 规格 id；ERP 生成的
    ``1688-MMDDHHMM-颜色`` 平台码不能冒充真实 id。"""
    source_data = sku.get('source_data') if isinstance(sku.get('source_data'), dict) else {}
    for source in (sku, source_data):
        for key in ('sku_id', 'skuId', 'skuID', 'sku_id_str', 'skuIdStr',
                    'specId', 'specID', 'spec_id', 'spec_id_str', 'specIdStr',
                    'skuCode', 'offerId', 'offer_id'):
            value = _clean_text(source.get(key))
            if value and value.lower() != 'unknown' and not GENERATED_SKU_PREFIX.match(value):
                if re.fullmatch(r'\d{6,}', value):
                    return value
    return ''


def _build_skus(product, source_url, main_images, sku_indexes=None):
    raw_skus = product.get('skus') or product.get('skuList') or []
    raw_skus = [sku for sku in raw_skus if isinstance(sku, dict)]
    if not raw_skus:
        raise ValueError('商品没有 SKU 数据')
    if sku_indexes is not None:
        picked = []
        for idx in sku_indexes:
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(raw_skus):
                picked.append((idx, raw_skus[idx]))
        if not picked:
            raise ValueError('所选 SKU 索引无效')
        raw_skus = [sku for _, sku in picked]

    offer_match = re.search(r'/offer/(\d{6,})', source_url or '')
    base_n = offer_match.group(1) if offer_match else '100000'
    fallback_price = _as_float(product.get('sourcePrice') or product.get('price'))

    out = []
    for index, sku in enumerate(raw_skus):
        real_id = _real_1688_sku_id(sku)
        combo = _combo_pairs(sku)
        sku_images = [url for url in _http_urls(sku.get('images') or sku.get('image')) if _jlc_image_ok(url)]
        image_url = sku_images[0] if sku_images else ''
        price = _as_float(sku.get('sourcePrice') or sku.get('price'), fallback_price)
        stock = _as_int(sku.get('stock'))
        availability = 'in_stock' if stock > 0 else 'unknown'
        option_values = [{
            'name_cn': name, 'value_cn': text,
            'source': 'erp_sku', 'source_text': f'{name}:{text}',
        } for name, text in combo]
        sku_name = _clean_text(sku.get('title') or sku.get('name') or '')

        if real_id:
            out.append({
                'sku_id': real_id,
                'sku_identity_type': '1688_sku',
                'sku_name': sku_name,
                'option_values': option_values,
                'price': price,
                'purchase_price': price,
                'price_source': 'erp_1688',
                'image_url': image_url,
                'availability': availability,
                'selection_order': index + 1,
                'source_data': {'sku_id': real_id, 'identity_source': 'erp_1688'},
            })
        elif len(raw_skus) == 1 and not combo:
            out.append({
                'sku_id': f'single-spec-{base_n}',
                'sku_identity_type': 'single_specification',
                'sku_name': '单规格',
                'option_values': [],
                'price': price,
                'purchase_price': price,
                'price_source': 'erp_1688',
                'image_url': image_url or (main_images[0] if main_images else ''),
                'availability': availability,
                'selection_order': index + 1,
                'source_data': {'identity_source': 'erp_single_spec'},
            })
        elif combo and image_url:
            out.append({
                'sku_id': f'local-spec-variant-offer-key-{base_n}-{index}',
                'sku_identity_type': 'visible_variant',
                'sku_name': sku_name,
                'option_values': option_values,
                'price': price,
                'purchase_price': price,
                'price_source': 'erp_1688',
                'image_url': image_url,
                'availability': availability,
                'selection_order': index + 1,
                'source_data': {'identity_source': 'visible_sku_option'},
            })
        # 多规格但缺规格图/无规格名 → 丢弃，避免 JLC 422

    if not out:
        raise ValueError('SKU 缺少真实 1688 规格 id，且没有可验证的规格图，无法导入 JLC 流水线')
    if len(out) > MAX_SKUS:
        raise ValueError(f'SKU 数量超过 {MAX_SKUS} 个上限，请先在采集箱中缩减')
    return out


def _build_category_selection(category_id, type_id):
    status, rules = _jlc_request('POST', '/api/collector/categories/rules', {
        'category_id': category_id,
        'type_id': type_id,
        'allow_readonly_fetch': True,
    })
    if status != 200 or not isinstance(rules, dict):
        raise ValueError(f'JLC 类目规则获取失败：{_jlc_error_message(rules)}')
    return {
        'category_id': str(category_id),
        'type_id': str(type_id),
        'selected_at': _now_iso(),
        'rules_snapshot': rules,
    }


def build_collector_payload(product, category_id, type_id, sku_indexes=None):
    source_url = _clean_text(product.get('originalUrl') or product.get('sourceLink') or '')
    if '1688.' not in source_url:
        raise ValueError('商品缺少 1688 原始链接（originalUrl），无法导入 JLC')

    main_images = [url for url in _http_urls(product.get('images')) if _jlc_image_ok(url)][:10]
    detail_images = [url for url in _http_urls(product.get('detailImages')) if _jlc_image_ok(url)][:20]
    if not main_images:
        raise ValueError('商品没有 1688/alicdn 原始主图，无法导入 JLC（ERP 图片代理地址会被 JLC 拒绝）')

    skus = _build_skus(product, source_url, main_images, sku_indexes)

    attributes = []
    for attr in product.get('attributes') or []:
        if isinstance(attr, dict):
            name = _clean_text(attr.get('name') or attr.get('id') or '')
            value = _clean_text(attr.get('value') or '')
        else:
            parts = str(attr).split(':', 1)
            name = _clean_text(parts[0])
            value = _clean_text(parts[1] if len(parts) > 1 else '')
        if name and value:
            attributes.append({
                'name_cn': name, 'value_cn': value,
                'source': 'erp_1688', 'source_text': f'{name}:{value}',
            })

    raw_skus = product.get('skus') or product.get('skuList') or []
    price_lines = []
    for i, sku in enumerate(raw_skus, 1):
        if isinstance(sku, dict):
            price_lines.append(f"SKU {i}: {_as_float(sku.get('sourcePrice') or sku.get('price'))} CNY")
    base_price = min(
        [price for price in (_as_float(sku.get('sourcePrice') or sku.get('price')) for sku in raw_skus if isinstance(sku, dict)) if price > 0],
        default=_as_float(product.get('sourcePrice') or product.get('price')),
    )
    moq = _as_int(product.get('minimumOrderQuantity') or product.get('moq'), 1)

    title = _clean_text(product.get('title') or '')
    return {
        'source_platform': '1688',
        'source_url': source_url,
        'captured_at': _now_iso(),
        'page_title': title,
        'title_cn': title,
        'supplier_name': _clean_text(
            product.get('sourceName')
            or (product.get('seller') or {}).get('name')
            or '1688供应商'
        ),
        'product_attributes': attributes,
        'price_information': {
            'currency': 'CNY',
            'price_ranges': [{'min_quantity': 1, 'price_cny': base_price, 'raw_text': f'ERP 成本 {base_price} CNY'}],
            'raw_text': '；'.join(price_lines),
        },
        'minimum_order_quantity': {'value': moq, 'unit': '件', 'raw_text': ''},
        'main_images': [{'url': url} for url in main_images],
        'detail_images': [{'url': url} for url in detail_images],
        'skus': skus,
        'selected_sku_ids': [sku['sku_id'] for sku in skus],
        'field_diagnostics': [
            {'field': 'title', 'selector': 'erp_product', 'method': 'erp_product'},
            {'field': 'main_images', 'selector': 'erp_product', 'method': 'erp_product'},
            {'field': 'sku', 'selector': 'erp_product', 'method': 'erp_product'},
        ],
        'capture_warnings': [],
        'raw_snapshot': {'all_raw_skus': skus, 'source': 'geekozon_erp_import'},
        'plugin_version': 'geekozon-erp-import/1.0',
        'ozon_category_selection': _build_category_selection(category_id, type_id),
        'allow_new_version': True,
    }


@jlc_bridge_bp.get('/api/jlc-bridge/products')
@require_login
@handle_errors
def list_importable_products():
    keyword = _clean_text(request.args.get('keyword') or '')
    items = Product.find_all()
    rows = []
    for item in items:
        platform = str(item.get('platform') or '').lower()
        if platform not in ('1688', 'alibaba', 'alibaba1688'):
            continue
        blob = json.dumps(item, ensure_ascii=False)
        if keyword and keyword.lower() not in blob.lower():
            continue
        rows.append({
            'id': item.get('id'),
            'title': item.get('title') or '',
            'platform': item.get('platform'),
            'originalUrl': item.get('originalUrl') or item.get('sourceLink') or '',
            'sourceName': item.get('sourceName') or '',
            'sourcePrice': item.get('sourcePrice'),
            'images': (item.get('images') or [])[:3],
            'skuCount': len(item.get('skus') or item.get('skuList') or []),
            'categoryPath': item.get('categoryPath') or item.get('category') or '',
            'jlcProductId': item.get('jlcProductId'),
            'jlcImportStatus': item.get('jlcImportStatus'),
            'updatedAt': item.get('updatedAt'),
        })
    rows.sort(key=lambda row: row.get('updatedAt') or '', reverse=True)
    return success_response(data=rows)


@jlc_bridge_bp.post('/api/jlc-bridge/import')
@require_login
@handle_errors
def import_product_to_jlc():
    body = request.get_json(silent=True) or {}
    product_id = _clean_text(body.get('productId') or '')
    category_id = body.get('categoryId')
    type_id = body.get('typeId')
    sku_indexes = body.get('skuIndexes')
    if not product_id:
        return error_response('缺少 productId')
    if not category_id or not type_id:
        return error_response('缺少 categoryId / typeId')
    if sku_indexes is not None:
        if not isinstance(sku_indexes, list) or not (1 <= len(sku_indexes) <= MAX_SKUS):
            return error_response(f'skuIndexes 必须是 1-{MAX_SKUS} 个的数组')

    product = Product.find_by_id(product_id)
    if not product:
        return error_response('商品不存在', 404)
    platform = str(product.get('platform') or '').lower()
    if platform not in ('1688', 'alibaba', 'alibaba1688'):
        return error_response('仅支持 1688 商品导入 JLC 流水线')

    payload = build_collector_payload(product, category_id, type_id, sku_indexes)
    status, data = _jlc_request('POST', '/api/collector/products', payload)
    if status not in (200, 201):
        code = status if 400 <= status < 600 else 502
        return error_response(f'JLC 导入失败：{_jlc_error_message(data)}', code)

    jlc_product_id = (data or {}).get('product_id') or (data or {}).get('productId') or ''
    if not jlc_product_id:
        return error_response('JLC 返回异常：缺少 product_id', 502)

    Product.update(product_id, {
        'jlcProductId': jlc_product_id,
        'jlcImportStatus': (data or {}).get('status') or 'COLLECTED',
        'jlcImportedAt': _now_iso(),
        'jlcCategory': {'categoryId': str(category_id), 'typeId': str(type_id)},
    })
    return success_response(data={
        'productId': product_id,
        'jlcProductId': jlc_product_id,
        'status': (data or {}).get('status'),
        'warnings': (data or {}).get('warnings') or [],
    })