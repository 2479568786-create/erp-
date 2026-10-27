"""上得快插件（geekozon-plugin v2+）→ JLC 流水线直通入口。

用途：浏览器插件在 1688/淘宝 页面采集到商品后，把 payload 发到本路由；
本路由把「上得快采集载荷」翻译成 JLC 的 collector-capture payload 并直连
JLC 内网 worker（:8765），使商品直接进入 JLC 采集箱，供后续 15 步自动上架流水线。

鉴权：兼容插件现有约定 —— 优先 X-Ext-Key（插件 API Key），其次 ERP 登录态
（Authorization Bearer）。二者皆无则 401。

⚠️ 全新旁路：不写 ERP 采集箱（Product JSON），不触碰既有商品/发布链路。
"""
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Blueprint, request

from utils.auth import get_current_user
from utils.response import error_response, success_response

jlc_ext_bp = Blueprint('jlc_ext', __name__)

JLC_BASE = 'http://127.0.0.1:8765'
JLC_TIMEOUT = 30


def _plugin_api_key():
    """插件 API Key：只读环境变量 EXT_API_KEY，未配置则返回空串（按未授权处理）。"""
    import os
    return os.environ.get('EXT_API_KEY') or ''


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _jlc_request(method, path, payload=None, timeout=JLC_TIMEOUT):
    url = JLC_BASE + path
    data = None
    headers = {'Accept': 'application/json'}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', 'replace')
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(body)
        except Exception:
            detail = body[:800]
        return exc.code, detail
    except urllib.error.URLError as exc:
        return 502, {'message': f'JLC 内部服务不可达：{exc}'}


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
    import html
    return html.unescape(str(value).strip())


def _extract_1688_offer_id(source_url):
    m = re.search(r'/offer/(\d{6,})', source_url or '')
    if m:
        return m.group(1)
    m = re.search(r'(\d{8,})', source_url or '')
    return m.group(1) if m else ''


def _authorized():
    """鉴权：插件 API Key 或 ERP 登录态。"""
    key = request.headers.get('X-Ext-Key', '')
    if key and key == _plugin_api_key():
        return True
    user = get_current_user()
    if user:
        return True
    return False


def _plugin_skus_to_jlc(payload):
    """上得快 skus[] → JLC skus[]。兼容两种形态：
    - geekozon-plugin v2：skus[i].combo = {规格名:值}, source_price, image
    - 老扩展：selected_skus[i].spec_name/spec_value/purchase_price
    """
    out = []
    raw_skus = payload.get('skus') or payload.get('selected_skus') or []
    source_url = payload.get('source_url') or ''
    offer_id = _extract_1688_offer_id(source_url) or 'plugin'
    fallback_price = payload.get('price')
    for index, s in enumerate(raw_skus, 1):
        if not isinstance(s, dict):
            continue
        combo = {}
        if isinstance(s.get('combo'), dict):
            for k, v in s['combo'].items():
                kk = _clean_text(k)
                vv = _clean_text(str(v))
                # 空规格值（如 combo={"规格": ""}）视为无规格，不产生选项
                if kk and vv:
                    combo[kk] = vv
        else:
            spec_name = _clean_text(s.get('spec_name') or s.get('specName') or '规格')
            spec_value = _clean_text(s.get('spec_value') or s.get('specValue') or '')
            if spec_name and spec_value:
                combo[spec_name] = spec_value
        price = s.get('source_price') or s.get('purchase_price') or s.get('price') or fallback_price
        sku_id_raw = str(s.get('sku_id') or s.get('skuId') or s.get('sku') or '')
        image = s.get('image') or ''
        has_real_id = bool(re.fullmatch(r'\d{6,}', sku_id_raw))
        # 单规格商品：仅一个 SKU 且无有效规格选项（含"规格为空"的情况），
        # 即使带 1688 skuId 也应按单规格处理，否则 JLC 会误判为多规格并强制要求 SKU 参考图。
        single_spec = len(raw_skus) == 1 and not combo
        if single_spec:
            sku_id = sku_id_raw if has_real_id else f'single-spec-{offer_id}'
            identity = 'single_specification'
            identity_source = 'erp_single_spec'
        elif has_real_id:
            sku_id = sku_id_raw
            identity = '1688_sku'
            identity_source = 'erp_1688'
        elif combo and image:
            sku_id = f'local-spec-variant-offer-key-{offer_id}-{index}'
            identity = 'visible_variant'
            identity_source = 'visible_sku_option'
        else:
            # 无真 id、无规格图、无单规格 → 不可进入 JLC，跳过
            continue
        try:
            price_f = float(price) if price not in (None, '') else None
        except (TypeError, ValueError):
            price_f = None
        out.append({
            'sku_id': sku_id,
            'sku_identity_type': identity,
            'sku_name': _clean_text(s.get('name') or (combo.get('规格') if '规格' in combo else
                       (next(iter(combo.values()), '') if combo else ''))),
            'option_values': [{'name_cn': k, 'value_cn': v,
                               'source': 'plugin', 'source_text': f'{k}:{v}'}
                              for k, v in combo.items()],
            'price': price_f,
            'purchase_price': price_f,
            'price_source': 'plugin_1688',
            'image_url': image,
            'availability': 'in_stock',
            'selection_order': index,
            'source_data': {'identity_source': identity_source},
        })
    return out


def _build_jlc_payload(payload, category_id, type_id, rules_snapshot):
    """上得快采集载荷 → JLC collector-capture payload。"""
    source_url = _clean_text(payload.get('source_url') or '')
    title = _clean_text(payload.get('title') or payload.get('page_title') or '')
    skus = _plugin_skus_to_jlc(payload)
    if not skus:
        raise ValueError('采集数据没有任何可选 SKU，无法进入 JLC 流水线')
    if not source_url:
        raise ValueError('采集数据缺少 source_url')
    if not title:
        raise ValueError('采集数据缺少商品标题')

    main_images = []
    for u in (payload.get('main_images') or [])[:10]:
        if isinstance(u, str) and u.startswith(('http://', 'https://')):
            main_images.append({'url': u})
        elif isinstance(u, dict) and u.get('url'):
            main_images.append({'url': u['url']})
    detail_images = []
    for u in (payload.get('detail_images') or [])[:20]:
        if isinstance(u, str) and u.startswith(('http://', 'https://')):
            detail_images.append({'url': u})
        elif isinstance(u, dict) and u.get('url'):
            detail_images.append({'url': u['url']})

    attributes = []
    raw_attrs = payload.get('attributes') or {}
    if isinstance(raw_attrs, dict):
        for k, v in raw_attrs.items():
            if isinstance(v, (str, int, float)):
                attributes.append({
                    'name_cn': _clean_text(k), 'value_cn': _clean_text(str(v)),
                    'source': 'plugin', 'source_text': f'{k}:{v}',
                })

    # 采集面板填写的产品重量(g) / 尺寸(mm) → 转成 JLC 识别的 SKU尺寸/SKU重量 属性，
    # 让 measurements/定价步骤能拿到真实计量数据。
    weight_g = payload.get('weight_g') or payload.get('productWeight')
    length_mm = payload.get('length_mm')
    width_mm = payload.get('width_mm')
    height_mm = payload.get('height_mm')
    if weight_g is not None and str(weight_g) != '':
        attributes.append({
            'name_cn': 'SKU重量', 'value_cn': f'{weight_g}g',
            'source': 'plugin', 'source_text': f'SKU重量:{weight_g}g',
        })
    if any(v is not None and str(v) != '' for v in (length_mm, width_mm, height_mm)):
        dim_text = f'{length_mm or 0}x{width_mm or 0}x{height_mm or 0}mm'
        attributes.append({
            'name_cn': 'SKU尺寸', 'value_cn': dim_text,
            'source': 'plugin', 'source_text': f'SKU尺寸:{dim_text}',
        })

    # 采集面板填写的 Ozon 售价（CNY）→ 同时存进 product_attributes（JLC 采集环不丢字段）
    ozon_selling_price = payload.get('ozon_selling_price') or payload.get('selling_price')
    if ozon_selling_price is not None and str(ozon_selling_price) != '':
        attributes.append({
            'name_cn': 'Ozon售价', 'value_cn': f'{ozon_selling_price}CNY',
            'source': 'plugin', 'source_text': f'Ozon售价:{ozon_selling_price}CNY',
        })

    price = payload.get('price')
    price_lines = [f"SKU {i+1}: {s.get('purchase_price')} CNY" for i, s in enumerate(skus)
                   if s.get('purchase_price')]
    base_price = min([s.get('purchase_price') for s in skus if s.get('purchase_price')],
                     default=(float(price) if price else None))

    return {
        'source_platform': payload.get('source_platform') or '1688',
        'source_url': source_url,
        'captured_at': payload.get('collected_at') or _now_iso(),
        'page_title': title,
        'title_cn': title,
        'supplier_name': _clean_text(payload.get('supplier_name') or '1688供应商'),
        'product_attributes': attributes,
        'price_information': {
            'currency': 'CNY',
            'price_ranges': [{'min_quantity': 1, 'price_cny': base_price,
                              'raw_text': f'插件成本 {base_price} CNY'}] if base_price else [],
            'raw_text': '；'.join(price_lines),
        },
        'minimum_order_quantity': {'value': payload.get('minimum_order_quantity') or 1,
                                   'unit': '件', 'raw_text': ''},
        'main_images': main_images,
        'detail_images': detail_images,
        'skus': skus,
        'field_diagnostics': [
            {'field': 'title', 'selector': 'plugin', 'method': 'geekozon_plugin'},
            {'field': 'main_images', 'selector': 'plugin', 'method': 'geekozon_plugin'},
            {'field': 'sku', 'selector': 'plugin', 'method': 'geekozon_plugin'},
        ],
        'capture_warnings': list(payload.get('warnings') or (payload.get('extraction') or {}).get('warnings') or []),
        'raw_snapshot': {
            'all_raw_skus': skus,
            'source': 'geekozon_plugin',
            'ozon_selling_price_cny': ozon_selling_price,
            'weight_g': weight_g,
            'dimensions_mm': {'length': length_mm, 'width': width_mm, 'height': height_mm},
        },
        'plugin_version': 'geekozon-plugin/2.0',
        'ozon_category_selection': {
            'schema_version': '1.0.0',
            'selection_source': 'ai_matcher',
            'selected_at': _now_iso(),
            'category_id': str(category_id),
            'type_id': str(type_id),
            'category_name_ru': '',
            'category_path': '',
            'rules_snapshot': rules_snapshot or {},
            'rules_snapshot_hash': len(json.dumps(rules_snapshot or {}, ensure_ascii=False)),
            'locked_for_batch': True,
            'allow_runtime_rematch': True,
        },
        'allow_new_version': True,
    }


def _fetch_jlc_category_rules(category_id, type_id):
    status, rules = _jlc_request('POST', '/api/collector/categories/rules', {
        'category_id': category_id, 'type_id': type_id, 'allow_readonly_fetch': True,
    })
    if status != 200 or not isinstance(rules, dict):
        return {}
    return rules


@jlc_ext_bp.post('/api/ext/collect')
def ext_collect():
    """插件采集入口：优先转进 JLC 采集箱；类目匹配失败时降级存 ERP 采集箱。"""
    if not _authorized():
        return error_response('未授权：插件 API Key 或登录态缺失', 401)

    payload = request.get_json(silent=True) or {}
    # 字段归一化：兼容 geekozon-plugin v2.0.5（驼峰字段）与 v2.0.1（下划线字段）两套字段名，
    # 采集链路只认下划线字段，避免插件新旧版本切换导致商品进不了 JLC 采集箱。
    _alias_map = {
        'source_url': ['source_url', 'sourceUrl', 'sourceLink', 'originalUrl'],
        'category_path': ['category_path', 'category'],
        'main_images': ['main_images', 'images'],
        'detail_images': ['detail_images', 'detailImages'],
        'source_platform': ['source_platform', 'platform'],
        'raw_description': ['raw_description', 'description'],
    }
    for canonical, aliases in _alias_map.items():
        if payload.get(canonical) is None:
            for a in aliases:
                if payload.get(a) not in (None, ''):
                    payload[canonical] = payload.get(a)
                    break
    # SKU 字段：v2.0.5 用 sourcePrice（驼峰），后端只认 source_price
    for _s in (payload.get('skus') or []):
        if isinstance(_s, dict):
            if _s.get('source_price') is None and _s.get('sourcePrice') is not None:
                _s['source_price'] = _s['sourcePrice']
            if _s.get('purchase_price') is None and _s.get('purchasePrice') is not None:
                _s['purchase_price'] = _s['purchasePrice']
    # attributes：v2.0.5 是 [{name,value}]，后端按 dict 读 → 转回 dict
    _attrs = payload.get('attributes')
    if isinstance(_attrs, list):
        _dict = {}
        for _a in _attrs:
            if isinstance(_a, dict) and _a.get('name'):
                _dict[str(_a['name'])] = _a.get('value', '')
        if _dict:
            payload['attributes'] = _dict

    if not payload.get('title') and not payload.get('page_title'):
        return error_response('采集数据缺少商品标题')

    # 0. 价格前置校验：JLC validate_source 要求每个 SKU 都有采购价，
    #    价格没采到时先给明确提示（插件面板可手动补采购价），避免进 JLC 后卡在第 1 步。
    raw_skus = payload.get('skus') or payload.get('selected_skus') or []
    any_price = any(
        (isinstance(s, dict) and (s.get('source_price') or s.get('purchase_price') or s.get('price')))
        for s in raw_skus
    )
    if not any_price and not payload.get('price'):
        return error_response('未采集到商品价格：请在采集面板为每个 SKU 填写采购价后再采集', 422)

    # 1. 类目匹配：显式 category_id/type_id 优先；否则走「关键词 + AI」分层匹配
    #    AI 匹配是同步 LLM 调用（最长 30s），采集回传不能等它：加 8s 硬超时，
    #    超时即降级，保证商品标题/图片/上架信息稳定回传，不被类目匹配拖垮。
    category_id = payload.get('category_id') or payload.get('description_category_id')
    type_id = payload.get('type_id')
    matched_category = None
    match_reason = ''
    if not (category_id and type_id):
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
            from services.category_matcher import match_category
            source_category = _clean_text(payload.get('category_path') or payload.get('category') or '')
            with ThreadPoolExecutor(max_workers=1) as _pool:
                _future = _pool.submit(
                    match_category,
                    source_category,
                    '1688',
                    title=_clean_text(payload.get('title') or ''),
                    description=_clean_text(payload.get('raw_description') or ''),
                )
                try:
                    match = _future.result(timeout=10)
                except FutureTimeout:
                    match = None
                    match_reason = '类目匹配超时（AI 慢），已降级'
        except Exception as exc:  # 匹配异常不阻断，转入降级路径
            match = None
            match_reason = f'类目匹配异常：{exc}'

        if match:
            if match.get('matched'):
                category_id = match.get('description_category_id') or match.get('category_id')
                type_id = match.get('type_id')
                matched_category = dict(match)
            elif match.get('candidates'):
                first = match['candidates'][0] if isinstance(match['candidates'][0], dict) else {}
                category_id = first.get('description_category_id') or first.get('category_id')
                type_id = first.get('type_id')
                matched_category = first
                match_reason = match.get('reason') or '候选类目'
            else:
                match_reason = match.get('reason') or '无候选'

    if category_id and type_id:
        # 2. 取 JLC 类目规则快照
        rules_snapshot = _fetch_jlc_category_rules(int(category_id), int(type_id))
        # 3. 转 JLC 采集载荷
        try:
            jlc_payload = _build_jlc_payload(payload, category_id, type_id, rules_snapshot)
        except ValueError as exc:
            return error_response(str(exc), 422)
        # 4. 直连 JLC 采集箱
        status, data = _jlc_request('POST', '/api/collector/products', jlc_payload)
        if status in (200, 201):
            jlc_product_id = (data or {}).get('product_id') or (data or {}).get('productId') or ''
            if jlc_product_id:
                return success_response(data={
                    'jlcProductId': jlc_product_id,
                    'status': (data or {}).get('status'),
                    'category': {'categoryId': str(category_id), 'typeId': str(type_id),
                                 'label': (matched_category or {}).get('label', '')},
                    'matchedBy': 'ai' if matched_category else 'explicit',
                    'warnings': (data or {}).get('warnings') or [],
                }, msg='已进入 JLC 采集箱，可在上得快页面运行流水线')
        else:
            match_reason = f'JLC 采集失败：{_jlc_error_message(data)}'

    # 5. 降级：存 ERP 采集箱（复用 compat 的映射约定），用户到「上得快」页选类目后导入 JLC
    try:
        from routes.compat_routes import _map_collection_to_product
        product_data = _map_collection_to_product(_payload_to_legacy_shape(payload))
        from models.product import Product
        existing = Product.find_by_original_url(product_data.get('originalUrl'))
        if existing:
            changed = {k: v for k, v in product_data.items()
                       if k not in ('id', 'createdAt', 'status')
                       and existing.get(k) in (None, '', [], {})
                       and v not in (None, '', [], {})}
            if changed:
                Product.update(existing['id'], changed)
            record_id = existing['id']
            created = False
        else:
            record = Product.create(product_data)
            record_id = record['id']
            created = True
        return success_response(data={
            'productId': record_id,
            'created': created,
            'degraded': True,
            'reason': match_reason or '类目未能自动确认',
            'next': '请在「上得快」页面为该商品选择 Ozon 类目后导入 JLC 流水线',
        }, msg='已存入 ERP 采集箱（类目需手动确认）')
    except Exception as exc:
        return error_response(f'采集失败且降级保存也失败：{exc}', 500)


def _payload_to_legacy_shape(payload):
    """上得快 v2 payload(combo 格式) → 老插件 selected_skus 格式，复用 _map_collection_to_product。"""
    out = dict(payload)
    raw_skus = payload.get('skus') or []
    selected = []
    for s in raw_skus:
        if not isinstance(s, dict):
            continue
        combo = s.get('combo') if isinstance(s.get('combo'), dict) else {}
        spec_name = (next(iter(combo), None)) or '规格'
        spec_value = combo.get(spec_name, '') if combo else ''
        selected.append({
            'spec_name': spec_name,
            'spec_value': str(spec_value),
            'purchase_price': s.get('source_price') or s.get('price'),
            'image': s.get('image') or '',
            'net_weight': s.get('net_weight'),
            'package_weight': s.get('package_weight'),
        })
    out['selected_skus'] = selected
    return out


@jlc_ext_bp.post('/api/ext/product/export-to-jlc')
def ext_export_product_to_jlc():
    """把 ERP 采集箱商品送进 JLC 工作区（生成 P 号商品），实现采集箱互通。

    ERP 采集箱商品（id_xxx）与 JLC 商品（P 号）的桥接：
      - 从 ERP products.json 读商品数据；
      - 重新匹配 Ozon 类目（DNS 已恢复，AI 匹配可用）；
      - 用 _build_jlc_payload 转成 JLC collector-capture 载荷；
      - POST /api/collector/products 写入 JLC；
      - 成功后在 ERP 商品上回写 jlcProductId。
    """
    if not _authorized():
        return error_response('未授权：插件 API Key 或登录态缺失', 401)

    body = request.get_json(silent=True) or {}
    product_id = str(body.get('productId') or body.get('product_id') or '').strip()
    if not product_id:
        return error_response('缺少 productId', 400)

    from models.product import Product
    product = Product.find_by_id(product_id)
    if not product:
        return error_response(f'ERP 采集箱不存在该商品：{product_id}', 404)
    if product.get('jlcProductId'):
        return success_response(data={
            'jlcProductId': product['jlcProductId'],
            'alreadyExported': True,
        }, msg='该商品已在 JLC 工作区')

    # 组装成 ext_collect 认识的 payload（下划线字段）
    payload = {
        'source_platform': product.get('platform') or '1688',
        'source_url': product.get('sourceUrl') or product.get('sourceLink') or product.get('originalUrl') or '',
        'title': product.get('title') or product.get('name') or '',
        'price': product.get('sourcePrice') or product.get('price') or None,
        'category_path': product.get('category') or '',
        'main_images': product.get('images') or [],
        'detail_images': product.get('detailImages') or [],
        'skus': product.get('skus') or product.get('skuList') or [],
        'attributes': product.get('attributes') or {},
        'weight_g': product.get('weight_g') or product.get('weightValue'),
        'length_mm': product.get('length_mm') or (product.get('dimensions') or {}).get('length'),
        'width_mm': product.get('width_mm') or (product.get('dimensions') or {}).get('width'),
        'height_mm': product.get('height_mm') or (product.get('dimensions') or {}).get('height'),
        'ozon_selling_price': product.get('ozonSellingPrice') or product.get('ozon_selling_price'),
    }
    if not payload.get('title'):
        return error_response('该商品缺少标题，无法进入 JLC 流水线', 422)
    if not payload.get('source_url'):
        return error_response('该商品缺少 1688 货源链接，无法进入 JLC 流水线', 422)

    # SKU 补全：ERP 采集箱的 skus[] 可能缺 sku_id/skuId，但 skuList[] 里有真实 1688 skuId。
    # 按顺序/规格名回填，确保 _plugin_skus_to_jlc 能识别真实 SKU 并进入 JLC。
    _sku_list = product.get('skuList') or []
    _raw_skus = payload.get('skus') or []
    if _sku_list and _raw_skus:
        _merged = []
        for _i, _s in enumerate(_raw_skus):
            if not isinstance(_s, dict):
                continue
            _s = dict(_s)
            if not _s.get('sku_id') and not _s.get('skuId'):
                _ref = _sku_list[_i] if _i < len(_sku_list) else {}
                _sid = (_ref or {}).get('skuId') or (_ref or {}).get('sku_id')
                if _sid:
                    _s['sku_id'] = str(_sid)
            _merged.append(_s)
        payload['skus'] = _merged

    # 字段归一化：ERP 采集箱 skus[] 用驼峰 sourcePrice/price，
    # 而 _plugin_skus_to_jlc 只读下划线 source_price，导致价格丢失。
    for _s in payload.get('skus') or []:
        if isinstance(_s, dict):
            if _s.get('source_price') in (None, '') and _s.get('sourcePrice') not in (None, ''):
                _s['source_price'] = _s['sourcePrice']
            if _s.get('purchase_price') in (None, '') and _s.get('purchasePrice') not in (None, ''):
                _s['purchase_price'] = _s['purchasePrice']
            if _s.get('price') in (None, '') and _s.get('source_price') not in (None, ''):
                _s['price'] = _s['source_price']

    # 价格前置校验（与 ext_collect 一致）
    raw_skus = payload.get('skus') or []
    any_price = any(
        isinstance(s, dict) and (s.get('source_price') or s.get('sourcePrice') or s.get('purchase_price') or s.get('price'))
        for s in raw_skus
    ) or payload.get('price')
    if not any_price:
        return error_response('该商品没有采购价：请先在 ERP 采集箱编辑补采购价，再送 JLC', 422)

    # 类目匹配：复用 ext_collect 的分层逻辑（本地 4 层 + AI 兜底，10s 超时降级）
    category_id = payload.get('category_id') or product.get('descriptionCategoryId')
    type_id = payload.get('type_id') or product.get('typeId')
    matched_category = None
    match_reason = ''
    if not (category_id and type_id):
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
            from services.category_matcher import match_category
            source_category = _clean_text(payload.get('category_path') or '')
            with ThreadPoolExecutor(max_workers=1) as _pool:
                _future = _pool.submit(
                    match_category,
                    source_category,
                    '1688',
                    title=_clean_text(payload.get('title') or ''),
                    description=_clean_text(product.get('description') or ''),
                )
                try:
                    match = _future.result(timeout=10)
                except FutureTimeout:
                    match = None
                    match_reason = '类目匹配超时（AI 慢）'
        except Exception as exc:
            match = None
            match_reason = f'类目匹配异常：{exc}'

        if match:
            if match.get('matched'):
                category_id = match.get('description_category_id') or match.get('category_id')
                type_id = match.get('type_id')
                matched_category = dict(match)
            elif match.get('candidates'):
                first = match['candidates'][0] if isinstance(match['candidates'][0], dict) else {}
                category_id = first.get('description_category_id') or first.get('category_id')
                type_id = first.get('type_id')
                matched_category = first
                match_reason = match.get('reason') or '候选类目'
            else:
                match_reason = match.get('reason') or '无候选'

    if not (category_id and type_id):
        return error_response(f'无法匹配 Ozon 类目，请在 ERP 采集箱编辑里手动选择类目后再送 JLC（{match_reason}）', 422)

    rules_snapshot = _fetch_jlc_category_rules(int(category_id), int(type_id))
    try:
        jlc_payload = _build_jlc_payload(payload, category_id, type_id, rules_snapshot)
    except ValueError as exc:
        return error_response(str(exc), 422)

    status, data = _jlc_request('POST', '/api/collector/products', jlc_payload)
    if status not in (200, 201):
        return error_response(f'送 JLC 失败：{_jlc_error_message(data)}', 502)

    jlc_product_id = (data or {}).get('product_id') or (data or {}).get('productId') or ''
    if not jlc_product_id:
        return error_response('JLC 返回成功但缺少 product_id，请到 JLC 工作区确认', 502)

    # 回写 ERP 商品的 jlcProductId，实现互通
    Product.update(product_id, {'jlcProductId': jlc_product_id})
    return success_response(data={
        'jlcProductId': jlc_product_id,
        'status': (data or {}).get('status'),
        'category': {'categoryId': str(category_id), 'typeId': str(type_id),
                     'label': (matched_category or {}).get('label', '')},
    }, msg='已送进 JLC 工作区，可运行 15 步流水线生产')


@jlc_ext_bp.get('/api/ext/collect/health')
def ext_collect_health():
    status, data = _jlc_request('GET', '/health', timeout=5)
    return success_response(data={'reachable': status == 200, 'service': (data or {}).get('service')})@jlc_ext_bp.post('/api/ext/handshake')
def ext_handshake():
    """插件握手：校验 API Key，返回操作员与店铺列表（插件诊断与采集前调用）。"""
    if not _authorized():
        return error_response('API Key 无效', 401)
    from db import query as _query
    stores = _query("SELECT id, alias, store_id FROM stores WHERE auth_status != 'disabled' ORDER BY id")
    return success_response(data={
        'operator': {'id': 1, 'name': '管理员'},
        'stores': [{'id': r['id'], 'name': r['alias'] or f"店铺 {r['store_id']}",
                    'store_id': r['store_id']} for r in stores],
        'image_filter': {'min_width': 600, 'min_height': 600, 'max_candidates': 5},
        'version': '2.0.0',
    })


@jlc_ext_bp.get('/api/ext/rules')
def ext_rules():
    if not _authorized():
        return error_response('API Key 无效', 401)
    return success_response(data={'list': []})


@jlc_ext_bp.get('/api/ext/product')
def ext_lookup_product():
    if not _authorized():
        return error_response('API Key 无效', 401)
    from models.product import Product
    url = request.args.get('url') or ''
    if not url:
        return success_response(data=None)
    existing = Product.find_by_original_url(url)
    if not existing:
        return success_response(data=None)
    return success_response(data={
        'title': existing.get('title') or '',
        'skus': existing.get('skus') or [],
        'imageCount': len(existing.get('images') or []),
    })


@jlc_ext_bp.post('/api/ext/images')
def ext_collect_images():
    if not _authorized():
        return error_response('API Key 无效', 401)
    # 图片采集转 ERP 图库走既有 /api/collect/images（compat），这里透传保持兼容。
    from routes.compat_routes import collect_images
    return collect_images()


@jlc_ext_bp.post('/api/ext/image-upload')
def ext_image_upload():
    """JLC 上架图片托管：接收 base64 图片，存到 ERP 图库，返回公网 URL。

    替代 cloudflared 快速隧道（生产环境网络无法下载 cloudflared），
    JLC 把 staging 图片 base64 上传到这里，获得 {IMAGE_BASE_URL}/uploads/xxx 公网 URL。
    """
    if not _authorized():
        return error_response('API Key 无效', 401)
    import base64 as _b64
    body = request.get_json(silent=True) or {}
    raw = body.get('image') or body.get('image_data') or ''
    if not raw:
        # 兼容文件流上传
        if 'file' in request.files:
            file = request.files['file']
            image_data = file.read()
            ext = (file.filename or '').rsplit('.', 1)[-1].lower() or 'png'
        else:
            return error_response('缺少 image 参数', 400)
    else:
        header = ''
        if raw.startswith('data:'):
            header, _, raw = raw.partition(',')
        ext_map = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif'}
        ext = ext_map.get(header.split(';', 1)[0], 'png')
        try:
            image_data = _b64.b64decode(raw)
        except Exception:
            return error_response('图片 base64 解码失败', 400)
    if not image_data:
        return error_response('图片数据为空', 400)
    try:
        from services.storage_service import save_image
        url = save_image(image_data, ext)
    except Exception as exc:
        return error_response(f'保存图片失败: {exc}', 500)
    return success_response(data={'url': url, 'hosting_mode': 'erp_public_hosting'})


@jlc_ext_bp.post('/api/ext/favorite')
def ext_favorite():
    if not _authorized():
        return error_response('API Key 无效', 401)
    return success_response(data={})


@jlc_ext_bp.post('/api/ext/market/snapshot')
def ext_market_snapshot():
    if not _authorized():
        return error_response('API Key 无效', 401)
    return success_response(data={})


@jlc_ext_bp.get('/api/ext/pricing')
def ext_pricing():
    if not _authorized():
        return error_response('API Key 无效', 401)
    return success_response(data={})
