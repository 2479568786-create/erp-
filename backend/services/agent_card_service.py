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
from services import card_service
from db import query

ANNOTATION_KEYWORDS = ('简介', 'Аннотация', 'Annotation')
THEME_TAG_ATTR_ID = 23171
THEME_TAG_FALLBACK_NAME = '#主题标签'


class AgentValidationError(ValueError):
    """入参不合法（路由层转 422）。"""


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
    return data


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


def _rollback_created_product(product_id):
    """建卡链路失败时删掉本次创建的商品，避免留下孤儿商品（采集箱里看见、卡却没有）。"""
    try:
        from models.product import Product

        Product.delete(product_id)
        return True
    except Exception:
        return False


def build_card(payload, token_row=None):
    """R2 主流程。返回 dict（路由层直接塞进 data）。"""
    if not isinstance(payload, dict):
        raise AgentValidationError('请求体必须是 JSON 对象')
    external_id = _as_text(payload.get('external_id'))
    if not external_id:
        raise AgentValidationError('external_id 必填（幂等键）')
    title = _as_text(payload.get('title'))
    if not title:
        raise AgentValidationError('title 必填')

    existing = _find_by_external_id(external_id)
    if existing:
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
        try:
            card_row = card_service.generate_card(product_id, selling_points)
        except Exception as exc:
            # 建卡失败回滚：商品已落库但卡片没生成，不能留下孤儿商品（R2 修复）
            _rollback_created_product(product_id)
            raise AgentValidationError(f'商品卡生成失败：{exc}')
        product, applied, fallback = _apply_card_to_product(product, card_row)
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
    }
