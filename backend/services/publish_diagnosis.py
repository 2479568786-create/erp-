"""
GeekOzon ERP - 发布警告/失败归因（R8）

把 Ozon 返回的俄语错误/警告文本（含我们自己的中文报错）映射成结构化诊断：
{code, severity, field, message_zh, fix_hint, raw}

覆盖历史已出现的 8 类：品牌词、正品宣称、广告词、禁售品、无主图、字典缺失、OfferId、价格
（另补单位/密度/政策类）。无法匹配的原文进 unknown，保证「全部有归因或明确 unknown」。
"""
import re

# (code, severity, field, message_zh, fix_hint, patterns)
RULES = [
    ('BRAND', 'warning', 'hashtags', '标签含无关品牌名', '删除标签中的品牌词后重新提交', [
        r'названия всех брендов', r'названия брендов', r'бренд',
    ]),
    ('ORIGINAL_CLAIM', 'warning', 'title', '文案宣称「原装正品/оригинал」', '删除 оригинал/原装正品 类表述后重新提交', [
        r'оригинал', r'оригинальн',
    ]),
    ('PROMO_AD', 'warning', 'hashtags', '标签含广告/促销表达', '删除 скидка/акция/распродажа 等促销词', [
        r'рекламные выражения', r'маркетинговых акций', r'рекламн', r'скидк', r'акци', r'распродаж',
    ]),
    ('FORBIDDEN', 'error', 'title', '商品被判定禁售/违规', '按 Ozon 禁售规则修改或归档商品（等值不可售）', [
        r'запрещен', r'запрещён', r'запрещенных веществ', r'военных действий', r'политик',
    ]),
    ('NO_IMAGE', 'error', 'images', '主图缺失/图片无法下载', '更换可公网访问的主图（jpg/png，≥900px）后重提', [
        r'не удалось скачать изображение', r'не смогли скачать картин', r'изображени',
        r'主图', r'图片处理异常', r'图片预处理失败',
    ]),
    ('MISSING_DICTIONARY', 'error', 'attributes', '字典属性缺少 dictionary_value_id', '调用 PUT /api/products/{id}/attributes 解析字典值后重提', [
        r'dictionary_value_id', r'字典属性', r'缺少 dictionary',
    ]),
    ('OFFER_ID', 'error', 'offer_id', 'offer_id 非法（超长/含非 ASCII/重复）', '按平台 SKU 生成纯 ASCII offer_id（≤50）后重提', [
        r'offerid', r'offer_id', r'offer id',
    ]),
    ('PRICE', 'error', 'price', '价格不合规（划线价/最低价/区间）', '核对 price/old_price 与 Ozon 价格规则后重提', [
        r'зачёркнутая цена', r'зачеркнутая цена', r'предельной цены', r'цена', r'price',
    ]),
    ('UNIT_VALUE', 'error', 'attributes', '数值/单位不合法（如数量小于最小值）', '核对计量单位与数值（g/mm/ml）后重提', [
        r'значение меньше допустимого', r'единицу измерения', r'количество товара',
    ]),
    ('DENSITY', 'warning', 'dimensions', '重量/尺寸不合理（密度越界）', '核对重量(g)与长宽高(mm)单位后重提', [
        r'габариты или вес', r'density', r'плотност',
    ]),
    ('AUTH_CREDENTIAL', 'error', 'credentials', '店铺 API 凭证无效/过期', '在店铺管理重新填写 Ozon Client-Id/Api-Key 后重提', [
        r'invalid\s+api-key', r'api[- ]key', r'unauthorized', r'401',
    ]),
    ('DIMENSION_ZERO', 'error', 'dimensions', '长/宽/高或重量为 0', '补齐重量(g)与长宽高(mm)后重提', [
        r'width\s*(必须|required)', r'height\s*(必须|required)', r'depth\s*(必须|required)',
        r'габарит', r'измерен',
    ]),
    ('UPSTREAM_TIMEOUT', 'error', 'upstream', 'Ozon 接口超时', '稍后重试；多次超时先检查网络/店铺网关', [
        r'请求超时', r'timeout', r'timed\s*out', r'超时',
    ]),
    ('MISSING_STORE', 'error', 'store', '未选择要发布的店铺', '确认商品归属店铺后重提', [
        r'请选择要发布到的\s*ozon\s*店铺', r'选择要发布', r'店铺',
    ]),
    ('DUPLICATE_SPU', 'warning', 'spu', '商品与他人的商品卡重复（SPU 冲突）', '合并到同类商品卡或更换货源/型号后重提', [
        r'\[spu\]', r'дублируется в других', r'скопировать',
    ]),
    ('IMAGE_LINK', 'error', 'images', '图片链接不可访问（无法下载）', '换可公网访问的图片地址后重提', [
        r'скачать фото', r'скачать картин', r'загрузить фото', r'не удалось скачать',
    ]),
    ('ATTRIBUTE_VALUE', 'warning', 'attributes', '属性值不合规（含搜索关键词/格式错误）', '按 Ozon 属性要求修正后重提', [
        r'ключевые слова', r'значение атрибута', r'исправили значение',
    ]),
]

_COMPILED = [(code, severity, field, msg, fix, [re.compile(p, re.I) for p in patterns])
             for code, severity, field, msg, fix, patterns in RULES]

_FIELD_HINTS = {
    'название': 'title',
    'аннотация': 'description',
    '#хештеги': 'hashtags',
    'хештеги': 'hashtags',
    'pictures': 'images',
    'price': 'price',
    'цена': 'price',
    'density': 'dimensions',
    'offer_id': 'offer_id',
    'offerid': 'offer_id',
}


def _split_segments(record):
    """把 record.error / record.errors 拆成独立告警片段。"""
    segments = []
    error = record.get('error')
    if isinstance(error, str) and error.strip():
        segments.extend(part for part in re.split(r'\s\|\s', error.strip()) if part.strip())
    errors = record.get('errors')
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, str) and item.strip():
                segments.extend(part for part in re.split(r'\s\|\s', item.strip()) if part.strip())
            elif isinstance(item, dict):
                text = str(item.get('message') or item.get('msg') or item.get('raw') or '').strip()
                if text:
                    segments.append(text)
    return segments


def _field_of(segment, default):
    match = re.match(r'^\[([^\]]+)\]', segment.strip())
    if not match:
        return default
    key = match.group(1).strip().lower()
    return _FIELD_HINTS.get(key, default)


def diagnose_text(segment, field_default=''):
    """单条告警文本 → 诊断项列表（可能命中多条规则）。"""
    items = []
    used = set()
    for code, severity, field, message_zh, fix_hint, patterns in _COMPILED:
        for pattern in patterns:
            match = pattern.search(segment)
            if not match:
                continue
            if code in used:
                break
            used.add(code)
            items.append({
                'code': code,
                'severity': severity,
                'field': _field_of(segment, field),
                'message_zh': message_zh,
                'fix_hint': fix_hint,
                'span': match.group(0),
                'raw': segment[:500],
            })
            break
    return items, (not items)


def diagnose_record(record):
    """PublishRecord/PublishTask dict → {items, unknown}。"""
    items = []
    unknown = []
    for segment in _split_segments(record or {}):
        found, is_unknown = diagnose_text(segment)
        if is_unknown:
            unknown.append({'raw': segment[:500]})
        else:
            items.extend(found)
    return {
        'items': items,
        'unknown': unknown,
        'summary': {
            'segments': len(_split_segments(record or {})),
            'diagnosed': len(items),
            'unknown': len(unknown),
        },
    }
