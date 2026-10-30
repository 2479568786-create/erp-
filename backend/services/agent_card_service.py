"""
GeekOzon ERP - Agent 一键建卡服务（R2）

职责：
1. 按 external_id 幂等创建商品（Product JSON，字段默认值沿用 models.product.Product.create）；
2. 可选 apply_card：调现有 card_service 生成俄语商品卡 → activate → **服务端回填**（复刻前端
   pages_collect.js:1951 applyGeneratedCardToForm 的行为）：
   - product.title        ← card.title_ru
   - product.description  ← card.description_ru
   - 简介属性（简介/Аннотация/Annotation）← card.description_ru
   - 主题标签属性（attr id 23171，全类目统一）← main_tags_ru + long_tail_tags_ru + auxiliary_tags_ru 空格拼接
   - 属性不存在时的降级：写 product.pendingCardDescription / product.pendingCardTags，
     并把主题标签作为 {id:23171,name:'#主题标签',value:...} 追加进 product.attributes（与前端保存时一致）
3. 返回 applied / missing_attributes / warnings，供 Agent 判断是否还需 R5 补属性。

注意：R2 入参是结构化数据，不复用 /api/products/collect 的「市场页数据清洗/合并」逻辑
（那部分只处理插件抓下来的脏数据），但商品仍走同一个 Product 模型与默认字段。
"""
import json

from services import card_service
from db import query

ANNOTATION_KEYWORDS = ('简介', 'Аннотация', 'Annotation')
THEME_TAG_ATTR_ID = 23171
THEME_TAG_FALLBACK_NAME = '#主题标签'


class AgentValidationError(ValueError):
    """入参不合法（路由层转 422）。"""


from utils.agent_scope import AgentPermissionError  # noqa: E402  (R-A2 店铺越权，路由层转 403)


def _as_text(value):
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        return ' '.join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip()


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [value]


def _find_by_external_id(external_id):
    from models.product import Product

    external_id = _as_text(external_id)
    if not external_id:
        return None
    for item in Product.find_all():
        if _as_text(item.get('externalId')) == external_id:
            return item
        note = _as_text(item.get('note'))
        if note.startswith('agent:') and note[6:].strip() == external_id:
            return item
    return None


def _normalize_attributes(raw):
    out = []
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get('name'))
        if 'value' not in item and 'dictionary_value_id' not in item and not name:
            continue
        entry = {'value': item.get('value', '')}
        if item.get('id') not in (None, ''):
            try:
                entry['id'] = int(item['id'])
            except (TypeError, ValueError):
                entry['id'] = item['id']
        if name:
            entry['name'] = name
        if item.get('dictionary_value_id') not in (None, ''):
            entry['dictionary_value_id'] = item['dictionary_value_id']
        out.append(entry)
    return out


def _build_product_data(payload, external_id):
    """把 R2 请求映射成 Product 字段（字段名以现有 Product JSON 为准）。"""
    category = payload.get('category') or {}
    if not isinstance(category, dict):
        category = {}
    images = [u for u in _as_list(payload.get('images')) if isinstance(u, str) and u.strip()]
    data = {
        'title': _as_text(payload.get('title')),
        'platform': _as_text(payload.get('platform')) or 'manual',
        'originalUrl': _as_text(payload.get('original_url') or payload.get('originalUrl')),
        'images': images,
        'attributes': _normalize_attributes(payload.get('attributes')),
        'externalId': external_id,
        'note': f'agent:{external_id}',
        'source': 'agent',
    }
    if payload.get('description'):
        data['description'] = _as_text(payload.get('description'))
    if payload.get('category_name'):
        data['category'] = _as_text(payload.get('category_name'))
    if category.get('description_category_id') not in (None, ''):
        data['descriptionCategoryId'] = category.get('description_category_id')
    if category.get('type_id') not in (None, ''):
        data['typeId'] = category.get('type_id')
    if category.get('name'):
        data['category'] = _as_text(category.get('name'))
    if category.get('parent_id') not in (None, ''):
        data['parentCategoryId'] = category.get('parent_id')

    for src_key, dst_key in (('price', 'price'), ('old_price', 'oldPrice'),
                             ('source_price', 'sourcePrice'), ('stock', 'stock'),
                             ('weight_g', 'weight'), ('barcode', 'barcode'),
                             ('part_number', 'partNumber'), ('brand', 'brand')):
        if payload.get(src_key) not in (None, ''):
            data[dst_key] = payload.get(src_key)

    # 包装尺寸：写进 dimensions（编辑器/发布读的是 dimensions）
    dims = {}
    for key, field in (('length_mm', 'length'), ('width_mm', 'width'), ('height_mm', 'height')):
        if payload.get(key) not in (None, ''):
            dims[field] = payload.get(key)
    if dims:
        data['dimensions'] = dims
    if payload.get('package_mode'):
        data['packageMode'] = _as_text(payload.get('package_mode'))
    if payload.get('store_id') not in (None, ''):
        data['storeId'] = payload.get('store_id')
    skus = _build_skus(payload.get('skus'), category.get('description_category_id'),
                       category.get('type_id'))
    if skus:
        data['skus'] = skus
    return data


# ============================================================
# R2-Fix2：SKU（至少单 SKU 颜色）支持
# ============================================================
COLOR_ATTR_ID = 10096
COLOR_STANDARD_KEY = '商品颜色（Цвет товара）'
_COLOR_KEY_HINTS = ('цвет товара', 'цвет', '颜色', 'color', 'colour')


def _is_color_key(key):
    text = str(key or '').strip().lower()
    return any(hint in text for hint in _COLOR_KEY_HINTS)


def _dictionary_color(text, description_category_id=None, type_id=None):
    """按 Ozon 颜色字典（attribute 10096）解析颜色。

    返回 (value_id, canonical_value, candidates)：
    - 唯一命中：value_id/canonical_value 有值，candidates 为空；
    - 未命中或多候选：value_id=None，candidates 返回候选行（供 422 报文）。
    只认唯一匹配，不做模糊兜底（与 R5 字典口径一致）。
    """
    target = _as_text(text)
    if not target:
        return None, '', []
    sql = ("SELECT value_id, value, value_zh, value_ru FROM ozon_attribute_dictionary_values "
           "WHERE attribute_id = ?")
    params = [COLOR_ATTR_ID]
    rows = []
    try:
        if description_category_id not in (None, '') and type_id not in (None, ''):
            rows = query(sql + " AND description_category_id = ? AND type_id = ?",
                         (COLOR_ATTR_ID, int(description_category_id), int(type_id)))
        if not rows:
            rows = query(sql, tuple(params))
    except Exception:
        rows = []
    needle = target.casefold()
    matched = []
    for row in rows:
        names = [str(row.get(key) or '').casefold() for key in ('value', 'value_zh', 'value_ru')]
        if needle in names:
            matched.append(row)
    if len(matched) == 1:
        row = matched[0]
        return row.get('value_id'), str(row.get('value') or row.get('value_ru') or ''), []
    candidates = [{'value_id': (r or {}).get('value_id'), 'value': (r or {}).get('value'),
                   'value_zh': (r or {}).get('value_zh')} for r in (matched or rows)[:20]]
    return None, '', candidates


def _build_skus(raw_skus, description_category_id=None, type_id=None):
    """把请求里的 skus[] 映射成 Product.skus[]（combo 键与编辑器一致）。

    每个 SKU 支持：combo（颜色等）、price/old_price/source_price/stock、
    package_weight_g/length_mm/width_mm/height_mm、image、sku_code（显式货号）。
    颜色值必须能在 Ozon 字典里唯一命中，否则 422 并把候选带回。
    """
    items = _as_list(raw_skus)
    if not items:
        return []
    out = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise AgentValidationError('skus[%d] 必须是 JSON 对象' % index)
        combo_raw = item.get('combo')
        if combo_raw is None:
            combo_raw = item.get('attributes') or {}
        if not isinstance(combo_raw, dict):
            raise AgentValidationError('skus[%d].combo 必须是对象' % index)
        combo = {}
        color_value_id = item.get('color_value_id')
        color_value = _as_text(item.get('color'))
        for key, value in combo_raw.items():
            text = _as_text(value)
            if not text:
                continue
            if _is_color_key(key):
                if color_value_id not in (None, ''):
                    combo[COLOR_STANDARD_KEY] = text
                    continue
                value_id, canonical, candidates = _dictionary_color(
                    text, description_category_id, type_id)
                if value_id is None:
                    raise AgentValidationError(
                        'skus[%d] 颜色「%s」无法在 Ozon 颜色字典唯一命中，候选：%s'
                        % (index, text, json.dumps(candidates, ensure_ascii=False)))
                color_value_id = value_id
                color_value = canonical or text
                combo[COLOR_STANDARD_KEY] = color_value
            else:
                combo[str(key)] = text
        if color_value and COLOR_STANDARD_KEY not in combo:
            value_id, canonical, candidates = _dictionary_color(
                color_value, description_category_id, type_id)
            if value_id is None:
                raise AgentValidationError(
                    'skus[%d] 颜色「%s」无法在 Ozon 颜色字典唯一命中，候选：%s'
                    % (index, color_value, json.dumps(candidates, ensure_ascii=False)))
            color_value_id = value_id
            combo[COLOR_STANDARD_KEY] = canonical or color_value

        sku = {'combo': combo}
        if color_value_id not in (None, ''):
            sku['colorValueId'] = color_value_id
        for src_key, dst_key in (('price', 'price'), ('old_price', 'oldPrice'),
                                 ('source_price', 'sourcePrice'), ('stock', 'stock')):
            if item.get(src_key) not in (None, ''):
                sku[dst_key] = item.get(src_key)
        weight = item.get('package_weight_g', item.get('weight_g', item.get('weight')))
        if weight not in (None, ''):
            sku['weight'] = weight
        dims = {}
        for src_key, field in (('length_mm', 'length'), ('width_mm', 'width'),
                               ('height_mm', 'height')):
            if item.get(src_key) not in (None, ''):
                dims[field] = item.get(src_key)
        if dims:
            sku['dimensions'] = dims
        image = _as_text(item.get('image'))
        if image:
            sku['image'] = image
            sku['images'] = [image]
        sku_code = _as_text(item.get('sku_code') or item.get('skuCode'))
        if sku_code:
            sku['skuCode'] = sku_code
            sku['offerId'] = sku_code
        out.append(sku)
    return out


def _required_attributes(description_category_id, type_id):
    """该三级类目的必填属性（只读 DB，不触发同步）。"""
    if description_category_id in (None, '') or type_id in (None, ''):
        return []
    try:
        rows = query(
            """SELECT attribute_id, name_ru, name_zh, dictionary_id
               FROM ozon_category_attributes
               WHERE description_category_id = ? AND type_id = ? AND is_required = 1
               ORDER BY attribute_id""",
            (int(description_category_id), int(type_id)),
        )
    except Exception:
        return []
    return [dict(r) for r in rows]


def _attribute_name(attr):
    return (attr.get('name_ru') or attr.get('name_zh') or '').strip()


def _missing_required(product, required):
    provided = {_as_text(a.get('name')) for a in (product.get('attributes') or []) if isinstance(a, dict)}
    provided_ids = {str(a.get('id')) for a in (product.get('attributes') or []) if isinstance(a, dict) and a.get('id') is not None}
    missing = []
    for row in required:
        attr_id = str(row.get('attribute_id'))
        name = _attribute_name(row)
        key_hit = any(name and (name in provided_name or provided_name in name) for provided_name in provided if provided_name)
        if attr_id not in provided_ids and not key_hit:
            missing.append(name or attr_id)
    return missing


def _theme_tags_text(card):
    parts = []
    for key in ('main_tags_ru', 'long_tail_tags_ru', 'auxiliary_tags_ru'):
        value = card.get(key)
        if isinstance(value, list):
            parts.extend(str(v).strip() for v in value if str(v).strip())
    return ' '.join(parts).strip()


def _apply_to_attributes(product, card):
    """回填简介属性与主题标签属性，返回 (applied_fields, fallback_flags)。"""
    applied = []
    fallback = []
    attributes = [dict(a) for a in (product.get('attributes') or []) if isinstance(a, dict)]

    description = _as_text(card.get('description_ru'))
    ann_index = None
    for idx, attr in enumerate(attributes):
        name = _as_text(attr.get('name'))
        if any(keyword in name for keyword in ANNOTATION_KEYWORDS):
            ann_index = idx
            break
    if description:
        if ann_index is not None:
            attributes[ann_index]['value'] = description
            applied.append('annotation')
        else:
            fallback.append('pendingCardDescription')

    tags_text = _theme_tags_text(card)
    if tags_text:
        tag_index = None
        for idx, attr in enumerate(attributes):
            if str(attr.get('id')) == str(THEME_TAG_ATTR_ID):
                tag_index = idx
                break
            if '主题标签' in _as_text(attr.get('name')):
                tag_index = idx
                break
        if tag_index is not None:
            attributes[tag_index]['value'] = tags_text
            applied.append('theme_tags')
        else:
            attributes.append({'id': THEME_TAG_ATTR_ID, 'name': THEME_TAG_FALLBACK_NAME, 'value': tags_text})
            applied.append('theme_tags')
            fallback.append('pendingCardTags')

    return attributes, applied, fallback


def _apply_card_to_product(product, card):
    from models.product import Product

    update = {}
    applied = []

    title = _as_text(card.get('title_ru'))
    if title:
        update['title'] = title
        applied.append('title')

    description = _as_text(card.get('description_ru'))
    if description:
        update['description'] = description
        applied.append('description')

    attributes, attr_applied, fallback = _apply_to_attributes(product, card)
    applied.extend(attr_applied)
    update['attributes'] = attributes
    if 'pendingCardDescription' in fallback and description:
        update['pendingCardDescription'] = description
    if 'pendingCardTags' in fallback:
        update['pendingCardTags'] = _theme_tags_text(card)

    saved = Product.update(product['id'], update)
    # 卡片改为 EDITED（采纳）
    try:
        card_service.activate_card(card['id'])
    except Exception as exc:
        raise AgentValidationError(f'卡片已生成但采纳失败：{exc}')
    return saved or product, applied, fallback


def build_card(payload, token_row=None, user=None):
    """R2 主流程。返回 dict（路由层直接塞进 data）。

    user：当前 JWT 用户（R-A1 业务员放行后传入）。业务员只能在自己的店铺建卡：
    store_id 必填且必须属于自己，校验在落库之前完成（不通过抛 AgentPermissionError）。
    """
    if not isinstance(payload, dict):
        raise AgentValidationError('请求体必须是 JSON 对象')
    external_id = _as_text(payload.get('external_id'))
    if not external_id:
        raise AgentValidationError('external_id 必填（幂等键）')
    title = _as_text(payload.get('title'))
    if not title:
        raise AgentValidationError('title 必填')

    from utils.agent_scope import assert_store_for_user, product_store, store_account_id

    assert_store_for_user(user, payload.get('store_id'))  # R-A2

    existing = _find_by_external_id(external_id)
    if existing:
        # R-A2：幂等命中也要管住店铺边界，避免拿别人的 external_id 探到商品
        if user and user.get('role') != 'admin':
            store = product_store(existing)
            if not store or store_account_id(store) != int(user.get('id') or 0):
                raise AgentPermissionError('无权访问该商品：external_id 已被其它店铺占用')
        card = None
        try:
            card = card_service.get_current_card(existing['id'])
        except Exception:
            card = None
        return {
            'product_id': existing['id'],
            'created': False,
            'card_id': (card or {}).get('id'),
            'card_version': (card or {}).get('version'),
            'applied': [],
            'missing_attributes': [],
            'warnings': ['external_id 已存在，返回已有商品，未重复建卡'],
        }

    from models.product import Product

    data = _build_product_data(payload, external_id)
    product = Product.create(data)
    product_id = product['id']
    warnings = []
    card_row = None
    applied = []

    if payload.get('apply_card', True):
        selling_points = payload.get('selling_points_zh')
        if not _as_text(selling_points):
            # 与前端一致：没有卖点时用商品信息兜底（card_service 内部会做提取）
            selling_points = '\n'.join(filter(None, [
                title,
                _as_text(payload.get('description')),
                _as_text(payload.get('category_name')),
            ])) or title
            warnings.append('selling_points_zh 为空，已用标题/描述兜底')
        fallback = []
        min_desc_words = payload.get('min_desc_words')
        allow_short = bool(payload.get('allow_short_description'))
        try:
            card_row = card_service.generate_card(
                product_id, selling_points,
                min_desc_words=min_desc_words,
                allow_short_description=allow_short,
            )
            # R2-Fix1：词数不足但降级放行时，把卡片侧的警告透传给调用方
            warnings.extend(card_row.pop('warnings', []) or [])
            product, applied, fallback = _apply_card_to_product(product, card_row)
        except Exception as exc:
            # 补偿回滚：建卡/采纳失败时，本次新建的商品不能留成孤儿
            # （2026-10-29 R7 线上冒烟暴露：文案质量门槛 422 后商品残留）
            rolled_back = False
            try:
                Product.delete(product_id)
                rolled_back = True
            except Exception:
                pass
            if rolled_back:
                raise AgentValidationError(f'商品卡生成失败：{exc}（已回滚本次新建商品 {product_id}）')
            raise AgentValidationError(
                f'商品卡生成失败：{exc}（商品已创建但回滚失败，需按 id 清理：{product_id}）')
        if fallback:
            warnings.append('部分卡片字段未找到对应属性，已写入 pendingCardDescription/pendingCardTags 兜底')
        card_row = card_service.get_card(card_row['id']) or card_row
        # R7：生成卡后自动跑文案越界检查，把风险放进 warnings
        try:
            from services import copy_lint

            lint_result = copy_lint.lint_card(card_row, product=product)
            for violation in lint_result.get('violations') or []:
                warnings.append('文案风险[%s] %s：%s' % (
                    violation.get('rule'), violation.get('field'), violation.get('message')))
        except Exception:
            pass

    required = _required_attributes(
        (payload.get('category') or {}).get('description_category_id'),
        (payload.get('category') or {}).get('type_id'),
    )
    missing = _missing_required(product, required)

    return {
        'product_id': product_id,
        'created': True,
        'card_id': (card_row or {}).get('id'),
        'card_version': (card_row or {}).get('version'),
        'applied': applied,
        'missing_attributes': missing,
        'warnings': warnings,
        'sku_count': len(product.get('skus') or []),
        'sku_ids': [str(s.get('skuCode') or '') for s in (product.get('skus') or [])
                    if isinstance(s, dict) and s.get('skuCode')],
    }
