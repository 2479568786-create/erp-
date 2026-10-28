"""
GeekOzon ERP - Agent 属性写入服务（R5）

目标：Agent 能按 attr_id 或属性名精确写商品属性，服务端负责：
- 名称 → attribute_id 对齐（用 ozon_category_attributes，同 type_id 内匹配）
- 字典值解析（本地 ozon_attribute_dictionary_values；**唯一匹配才落 value_id**，
  多候选返回 422 + 候选，无匹配返回 422）
- 数值单位归一（kg→g、cm→mm、L→ml、mg→g）
- 密度自检：weight(g)×1000 ÷ (长×宽×高 mm³) ∈ [0.001, 13.55]
- merge/replace 两种模式；validate_required / strict 控制必填校验

注意：字典解析走**本地缓存表**而不是实时 Ozon API（写接口不能依赖第三方可用性）；
本地未命中时返回 unresolved，调用方可用 /api/categories/attribute-values/search 校准。
"""
import re

from db import query
from models.product import Product

DENSITY_MIN = 0.001
DENSITY_MAX = 13.55

_UNIT_RE = re.compile(
    r'^\s*(-?\d+(?:[.,]\d+)?)\s*(kg|кг|g|г|gr|mg|мг|mm|мм|cm|см|m|м|ml|мл|l|л)?\s*$',
    re.IGNORECASE,
)

# 单位 → (基准单位, 倍率)
_UNIT_FACTORS = {
    'g': ('g', 1.0), 'г': ('g', 1.0), 'gr': ('g', 1.0),
    'kg': ('g', 1000.0), 'кг': ('g', 1000.0),
    'mg': ('g', 0.001), 'мг': ('g', 0.001),
    'mm': ('mm', 1.0), 'мм': ('mm', 1.0),
    'cm': ('mm', 10.0), 'см': ('mm', 10.0),
    'm': ('mm', 1000.0), 'м': ('mm', 1000.0),
    'ml': ('ml', 1.0), 'мл': ('ml', 1.0),
    'l': ('ml', 1000.0), 'л': ('ml', 1000.0),
}


class AgentAttributeError(ValueError):
    """入参/属性不合法（路由层映射 422）。"""


def _normalize_name(text):
    text = str(text or '').strip().lower()
    text = re.sub(r'[（）()\[\]【】]', ' ', text)
    text = re.sub(r'[，,。/／、;；:：]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _category_attributes(product):
    """该商品的类目属性行（按 descriptionCategoryId + typeId）。"""
    category_id = product.get('descriptionCategoryId')
    type_id = product.get('typeId')
    if not category_id or not type_id:
        return []
    try:
        rows = query(
            """SELECT attribute_id, name, name_ru, name_zh, attr_type, is_required, dictionary_id
               FROM ozon_category_attributes
               WHERE description_category_id = ? AND type_id = ?
               ORDER BY is_required DESC, attribute_id""",
            (int(category_id), int(type_id)),
        )
    except Exception:
        return []
    return [dict(r) for r in rows]


def _find_attribute(entry, attributes):
    attr_id = entry.get('attr_id') or entry.get('id')
    if attr_id not in (None, ''):
        try:
            attr_id = int(attr_id)
        except (TypeError, ValueError):
            raise AgentAttributeError(f'attr_id 非法：{attr_id!r}')
        for row in attributes:
            if int(row['attribute_id']) == attr_id:
                return row
        return None

    raw_name = entry.get('name')
    if not raw_name:
        raise AgentAttributeError('每条属性都需要 attr_id 或 name')
    target = _normalize_name(raw_name)
    exact = []
    loose = []
    for row in attributes:
        names = {_normalize_name(row.get('name_ru')), _normalize_name(row.get('name_zh')),
                 _normalize_name(row.get('name'))}
        names.discard('')
        if target in names:
            exact.append(row)
        elif any(target in name or name in target for name in names):
            loose.append(row)
    chosen = exact or loose
    if not chosen:
        return None
    if len(exact) > 1:
        # 同名属性（极少）：优先按 attr_type 精确
        return exact[0]
    return chosen[0]


def _dictionary_candidates(attribute, value, category_id, type_id):
    """本地字典命中：返回 (value_id, row) 或 (None, candidates)。"""
    try:
        rows = query(
            """SELECT value_id, value, value_ru, value_zh
               FROM ozon_attribute_dictionary_values
               WHERE attribute_id = ?
                 AND (description_category_id = ? OR description_category_id = 0)
                 AND (type_id = ? OR type_id = 0)""",
            (int(attribute['attribute_id']), int(category_id), int(type_id)),
        )
    except Exception:
        rows = []
    target = _normalize_name(value)
    if not target:
        return None, []
    exact = []
    loose = []
    for row in rows:
        names = {_normalize_name(row.get('value')), _normalize_name(row.get('value_ru')),
                 _normalize_name(row.get('value_zh'))}
        names.discard('')
        if target in names:
            exact.append(dict(row))
        elif any(target in name or name in target for name in names):
            loose.append(dict(row))
    if len(exact) == 1:
        return exact[0].get('value_id'), exact[0]
    if len(exact) > 1:
        return None, exact[:10]
    if len(loose) == 1:
        return loose[0].get('value_id'), loose[0]
    if len(loose) > 1:
        return None, loose[:10]
    return None, []


def _attribute_unit_hint(name):
    low = str(name or '').lower()
    if any(token in low for token in ('вес', '重量', 'грамм')) or re.search(r'\bг\b', low):
        return 'g'
    if any(token in low for token in ('длина', 'ширина', 'высота', 'глубина', '尺寸', '长', '宽', '高')):
        return 'mm'
    if any(token in low for token in ('объ', 'объём', 'объем', '体积', '容量')):
        return 'ml'
    return None


def normalize_numeric(value, attribute_name=''):
    """数值单位归一：返回 (规范化后的值, 说明)。无法解析时原样返回。"""
    if isinstance(value, (int, float)):
        return value, ''
    raw = str(value or '').strip()
    match = _UNIT_RE.match(raw)
    if not match:
        return value, ''
    number = float(match.group(1).replace(',', '.'))
    unit = (match.group(2) or '').strip().lower()
    if not unit:
        hint = _attribute_unit_hint(attribute_name)
        return (int(number) if number.is_integer() else number), ''
    base, factor = _UNIT_FACTORS.get(unit, (None, 1.0))
    if not base:
        return value, ''
    converted = number * factor
    if converted.is_integer():
        converted = int(converted)
    note = '%s→%s' % (unit, base) if unit != base else ''
    hint = _attribute_unit_hint(attribute_name)
    if hint and hint != base:
        return converted, ('%s（与属性基准单位 %s 不一致，请核对）' % (note or unit, hint)).strip()
    return converted, note


def density_check(product):
    """密度自检：weight(g)*1000 / (L*W*H mm^3)。数据不全时返回 checked=False。"""
    try:
        from services.publish_service import _resolve_item_dimensions, _resolve_item_weight

        weight = _resolve_item_weight(product)
        length, width, height = _resolve_item_dimensions(product)
    except Exception:
        return {'checked': False}
    try:
        weight = float(weight or 0)
        volume = float(length or 0) * float(width or 0) * float(height or 0)
    except (TypeError, ValueError):
        return {'checked': False}
    if weight <= 0 or volume <= 0:
        return {'checked': False}
    value = round(weight * 1000.0 / volume, 4)
    return {
        'checked': True,
        'value': value,
        'ok': DENSITY_MIN <= value <= DENSITY_MAX,
        'weight_g': weight,
        'volume_mm3': volume,
    }


def _required_missing(product, attributes, final_attributes):
    present_ids = set()
    present_names = set()
    for item in final_attributes:
        if item.get('id') not in (None, ''):
            present_ids.add(str(item['id']))
        if item.get('name'):
            present_names.add(_normalize_name(item['name']))
    missing = []
    for row in attributes:
        if not row.get('is_required'):
            continue
        attr_id = str(row['attribute_id'])
        names = {_normalize_name(row.get('name_ru')), _normalize_name(row.get('name_zh')),
                 _normalize_name(row.get('name'))}
        names.discard('')
        if attr_id in present_ids or names & present_names:
            continue
        missing.append(row.get('name_ru') or row.get('name_zh') or attr_id)
    return missing


def apply_attributes(product_id, payload):
    if not isinstance(payload, dict):
        raise AgentAttributeError('请求体必须是 JSON 对象')
    entries = payload.get('attributes')
    if not isinstance(entries, list) or not entries:
        raise AgentAttributeError('attributes 必须是非空数组')

    mode = str(payload.get('mode') or 'merge').lower()
    if mode not in ('merge', 'replace'):
        raise AgentAttributeError("mode 只支持 merge 或 replace")
    resolve_dictionary = payload.get('resolve_dictionary', True)
    validate_required = payload.get('validate_required', True)
    strict = payload.get('strict', False)

    product = Product.find_by_id(str(product_id))
    if not product:
        raise AgentAttributeError('商品不存在或已删除')

    attributes = _category_attributes(product)
    category_id = product.get('descriptionCategoryId') or 0
    type_id = product.get('typeId') or 0

    applied = []
    unresolved = []
    warnings = []
    errors = []

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AgentAttributeError('attributes[%d] 必须是对象' % index)
        attribute = _find_attribute(entry, attributes)
        if not attribute:
            label = entry.get('name') or entry.get('attr_id') or entry.get('id')
            unresolved.append({'name': label, 'reason': 'attribute_not_found', 'candidates': []})
            continue

        raw_value = entry.get('value')
        dictionary_value_id = entry.get('dictionary_value_id')
        dictionary_id = int(attribute.get('dictionary_id') or 0)

        if dictionary_value_id in (None, '') and resolve_dictionary and dictionary_id > 0 and raw_value not in (None, ''):
            resolved_id, row = _dictionary_candidates(attribute, raw_value, category_id, type_id)
            if resolved_id is not None:
                dictionary_value_id = resolved_id
                raw_value = row.get('value') or row.get('value_ru') or raw_value
            else:
                candidates = [{'value_id': c.get('value_id'),
                               'value': c.get('value_ru') or c.get('value'),
                               'value_zh': c.get('value_zh')} for c in (row or [])]
                unresolved.append({
                    'name': attribute.get('name_ru') or attribute.get('name_zh'),
                    'attr_id': attribute['attribute_id'],
                    'reason': 'dictionary_ambiguous' if candidates else 'dictionary_not_found',
                    'candidates': candidates,
                })
                continue

        value = raw_value
        attr_type = str(attribute.get('attr_type') or '').lower()
        if attr_type in ('integer', 'decimal', 'number') and value not in (None, ''):
            value, note = normalize_numeric(value, attribute.get('name_ru') or attribute.get('name_zh'))
            if note:
                warnings.append('%s：%s' % (attribute.get('name_ru') or attribute.get('name_zh'), note))
            try:
                float(str(value).replace(',', '.'))
            except (TypeError, ValueError):
                unresolved.append({
                    'name': attribute.get('name_ru') or attribute.get('name_zh'),
                    'attr_id': attribute['attribute_id'],
                    'reason': 'value_not_numeric',
                    'candidates': [],
                })
                continue

        item = {
            'attr_id': int(attribute['attribute_id']),
            'name': attribute.get('name_ru') or attribute.get('name_zh') or str(attribute['attribute_id']),
            'value': value,
        }
        if dictionary_value_id not in (None, ''):
            item['dictionary_value_id'] = dictionary_value_id
        applied.append(item)

    if strict and unresolved:
        raise AgentAttributeError('存在无法解析的属性：%s' % ', '.join(
            str(item.get('name')) for item in unresolved))

    current = [] if mode == 'replace' else [dict(a) for a in (product.get('attributes') or []) if isinstance(a, dict)]
    by_id = {str(a.get('id')): idx for idx, a in enumerate(current) if a.get('id') not in (None, '')}
    for item in applied:
        key = str(item['attr_id'])
        if key in by_id:
            merged = dict(current[by_id[key]])
            merged['name'] = item['name']
            merged['value'] = item['value']
            if 'dictionary_value_id' in item:
                merged['dictionary_value_id'] = item['dictionary_value_id']
            current[by_id[key]] = merged
        else:
            by_id[key] = len(current)
            current.append({'id': item['attr_id'], 'name': item['name'], 'value': item['value'],
                            **({'dictionary_value_id': item['dictionary_value_id']}
                               if 'dictionary_value_id' in item else {})})

    missing_required = _required_missing(product, attributes, current) if validate_required else []

    density_product = dict(product)
    density_product['attributes'] = current
    density = density_check(density_product)
    if density.get('checked') and not density.get('ok'):
        warnings.append('密度异常：%.4f g/cm³（合理范围 %s–%s），发布可能被 Ozon 拒绝'
                        % (density['value'], DENSITY_MIN, DENSITY_MAX))
        density['code'] = 'DENSITY_OUT_OF_RANGE'

    Product.update(product['id'], {'attributes': current})

    return {
        'product_id': product['id'],
        'mode': mode,
        'applied': applied,
        'unresolved': unresolved,
        'missing_required': missing_required,
        'density': density,
        'warnings': warnings,
        'attributes': current,
    }
