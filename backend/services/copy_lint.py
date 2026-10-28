"""
GeekOzon ERP - 文案越界过滤器（R7）

规则配置：`backend/configs/copy_lint_rules.json`（避免与 `config.py` 模块同名，放 configs/ 目录）
规则：
- BRAND：标签/文案里出现「非本商品品牌」的品牌名（商品品牌为「无品牌/空」时，出现任何已知品牌名都算）
- ORIGINAL_CLAIM：оригинал / 100% оригинал / 原装正品
- PROMO：скидка / акция / распродажа / дешевле / лучшая цена / хит продаж
- CONTACT：http(s):// / tel: / whatsapp / telegram / @handle
- CJK：俄语 listing 里出现中文
- TITLE_LENGTH：标题 > title_max_chars
- TAG_COUNT：标签 > tags_max_count
- TAG_LENGTH：单个标签 > tag_max_chars

品牌词表来源（按优先级合并）：
1. 规则文件 `brand_lexicon`（人工补充）
2. 运行时从商品库品牌属性收集（缓存 10 分钟，避免每次全量扫描）
3. 调用方传入 `known_brands`
"""
import json
import os
import re
import threading
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(BASE_DIR, 'configs', 'copy_lint_rules.json')

_TAGS_ATTR_ID = 23171
_LEXICON_CACHE = {'ts': 0, 'brands': set(), 'lock': threading.Lock()}
_LEXICON_TTL = 600

_DEFAULT_RULES = {
    'version': 1,
    'rules': [
        {'code': 'BRAND', 'severity': 'warning', 'fields': ['title', 'description', 'tags']},
        {'code': 'ORIGINAL_CLAIM', 'severity': 'warning', 'fields': ['title', 'description', 'tags'],
         'regex': r'(?i)(оригинал\w*|100\s*%\s*оригинал|原装正品|正品保证)'},
        {'code': 'PROMO', 'severity': 'warning', 'fields': ['title', 'description', 'tags'],
         'regex': r'(?i)(скидк\w*|акци\w*|распродаж\w*|дешевле|лучшая\s+цена|хит\s+продаж)'},
        {'code': 'CONTACT', 'severity': 'warning', 'fields': ['title', 'description', 'tags'],
         'regex': r'(?i)(https?://|tel:|whatsapp|telegram|t\.me/|@[A-Za-z0-9_]{4,})'},
        {'code': 'CJK', 'severity': 'warning', 'fields': ['title', 'description', 'tags'],
         'regex': r'[\u4e00-\u9fff]+'},
    ],
    'limits': {'title_max_chars': 200, 'tags_max_count': 30, 'tag_max_chars': 30},
    'brand_whitelist': ['无品牌', 'нет бренда', 'no brand', 'none', 'copy', 'без бренда'],
    'brand_lexicon': [],
    'tags_attribute_id': _TAGS_ATTR_ID,
    'brand_attribute_ids': [85],
    'brand_attribute_keywords': ['бренд', '品牌', 'brand'],
}


def load_rules():
    rules = json.loads(json.dumps(_DEFAULT_RULES))
    try:
        with open(RULES_PATH, encoding='utf-8-sig') as f:
            data = json.load(f)
        rules.update({k: v for k, v in data.items() if k in rules})
        if data.get('rules'):
            rules['rules'] = data['rules']
    except Exception:
        pass
    return rules


def _norm(text):
    return str(text or '').strip().lower()


def whitelist(rules):
    return {_norm(item) for item in rules.get('brand_whitelist') or [] if _norm(item)}


def product_brand(product, rules):
    """商品自身品牌值（属性 85 或品牌关键词属性 / product.brand）。"""
    values = []
    if isinstance(product, dict):
        values.append(_norm(product.get('brand')))
        for attr in product.get('attributes') or []:
            if not isinstance(attr, dict):
                continue
            attr_id = attr.get('id')
            name = _norm(attr.get('name'))
            hit = (attr_id in (rules.get('brand_attribute_ids') or [85]))
            if not hit:
                hit = any(k in name for k in (rules.get('brand_attribute_keywords') or ['бренд', '品牌']))
            if hit:
                values.append(_norm(attr.get('value')))
    cleaned = [v for v in values if v]
    if not cleaned:
        return ''
    return cleaned[0]


def _lexicon_from_products(rules):
    white = whitelist(rules)
    now = time.time()
    with _LEXICON_CACHE['lock']:
        if now - _LEXICON_CACHE['ts'] < _LEXICON_TTL and _LEXICON_CACHE['brands']:
            return set(_LEXICON_CACHE['brands'])
    brands = set()
    try:
        from models.product import Product

        for item in Product.find_all():
            value = product_brand(item, rules)
            if value and value not in white and 2 <= len(value) <= 40:
                brands.add(value)
    except Exception:
        pass
    with _LEXICON_CACHE['lock']:
        _LEXICON_CACHE['brands'] = brands
        _LEXICON_CACHE['ts'] = now
    return brands


def known_brands(rules, extra=None):
    lexicon = set()
    for item in rules.get('brand_lexicon') or []:
        item = _norm(item)
        if item:
            lexicon.add(item)
    lexicon |= _lexicon_from_products(rules)
    for item in extra or []:
        item = _norm(item)
        if item:
            lexicon.add(item)
    return {item for item in lexicon if item not in whitelist(rules)}


def _iter_field_texts(title='', description='', tags=None):
    yield 'title', str(title or '')
    yield 'description', str(description or '')
    for index, tag in enumerate(tags or []):
        yield 'tags[%d]' % index, str(tag or '')


def lint_fields(title='', description='', tags=None, product=None, extra_brands=None):
    """对标题/描述/标签做规则检查，返回 {ok, violations, rules_version}。"""
    rules = load_rules()
    own_brand = _norm(product_brand(product, rules)) if product is not None else ''
    white = whitelist(rules)
    brand_tokens = known_brands(rules, extra=extra_brands)
    if own_brand and own_brand not in white:
        brand_tokens.add(own_brand)  # 自己的品牌也算「已知品牌」

    violations = []
    fields_text = list(_iter_field_texts(title, description, tags))
    for rule in rules.get('rules') or []:
        code = rule.get('code')
        target_fields = rule.get('fields') or ['title', 'description', 'tags']
        if code == 'BRAND':
            if not brand_tokens:
                continue
            for field, text in fields_text:
                if not _field_enabled(field, target_fields):
                    continue
                low = _norm(text)
                if not low:
                    continue
                latin_tokens = set(re.split(r'[^a-z0-9]+', low))
                for token in brand_tokens:
                    if own_brand and not own_brand in white and token == own_brand:
                        continue
                    if not token:
                        continue
                    if re.fullmatch(r'[a-z0-9 .&\-]+', token):
                        # 拉丁品牌：分词命中（#HP_La / Canon_MF3010 都能命中）
                        token_parts = [part for part in re.split(r'[^a-z0-9]+', token) if part]
                        if token_parts and all(part in latin_tokens for part in token_parts):
                            matched = True
                        else:
                            matched = False
                    else:
                        matched = token in low
                    if matched:
                        violations.append({
                            'field': field, 'rule': 'BRAND', 'severity': rule.get('severity', 'warning'),
                            'span': token,
                            'message': '出现非本商品的品牌名「%s」' % token,
                            'suggestion': '删除该品牌词；若非本商品品牌请勿出现',
                        })
                        break
            continue

        pattern = rule.get('regex')
        if not pattern:
            continue
        try:
            compiled = re.compile(pattern)
        except re.error:
            continue
        for field, text in fields_text:
            if not _field_enabled(field, target_fields):
                continue
            for match in compiled.finditer(text or ''):
                span = match.group(0)
                violations.append({
                    'field': field, 'rule': code, 'severity': rule.get('severity', 'warning'),
                    'span': span,
                    'message': rule.get('description') or code,
                    'suggestion': rule.get('fix') or '删除或改写该片段',
                })

    limits = rules.get('limits') or {}
    title_max = int(limits.get('title_max_chars') or 200)
    if len(str(title or '')) > title_max:
        violations.append({'field': 'title', 'rule': 'TITLE_LENGTH', 'severity': 'warning',
                           'span': str(len(str(title))), 'message': '标题超过 %d 字符' % title_max,
                           'suggestion': '精简标题'})
    tags_list = list(tags or [])
    tags_max = int(limits.get('tags_max_count') or 30)
    if len(tags_list) > tags_max:
        violations.append({'field': 'tags', 'rule': 'TAG_COUNT', 'severity': 'warning',
                           'span': str(len(tags_list)), 'message': '标签数超过 %d' % tags_max,
                           'suggestion': '精简标签'})
    tag_max = int(limits.get('tag_max_chars') or 30)
    for index, tag in enumerate(tags_list):
        if len(str(tag or '')) > tag_max:
            violations.append({'field': 'tags[%d]' % index, 'rule': 'TAG_LENGTH', 'severity': 'warning',
                               'span': str(len(str(tag))), 'message': '标签超过 %d 字符' % tag_max,
                               'suggestion': '精简该标签'})

    return {
        'ok': not violations,
        'violations': violations,
        'rules_version': rules.get('version'),
    }


def _field_enabled(field, target_fields):
    base = field.split('[')[0]
    return base in target_fields or 'tags' in target_fields and base == 'tags'


def _strip_pattern(text, pattern):
    try:
        return re.sub(pattern, ' ', text)
    except re.error:
        return text


def sanitize_fields(title='', description='', tags=None, product=None, extra_brands=None):
    """按 lint 结果清理文案，返回 sanitized dict（不写库，调用方决定是否采用）。"""
    rules = load_rules()
    result = lint_fields(title=title, description=description, tags=tags,
                         product=product, extra_brands=extra_brands)
    tag_list = [str(t or '').strip() for t in (tags or []) if str(t or '').strip()]
    tag_limit = int((rules.get('limits') or {}).get('tags_max_count') or 30)
    tag_char_limit = int((rules.get('limits') or {}).get('tag_max_chars') or 30)

    def clean_text(text):
        out = str(text or '')
        for rule in rules.get('rules') or []:
            if rule.get('code') == 'BRAND':
                continue
            pattern = rule.get('regex')
            if pattern:
                out = _strip_pattern(out, pattern)
        return re.sub(r'\s{2,}', ' ', out).strip()

    title_clean = clean_text(title)
    desc_clean = clean_text(description)

    brand_hits = {v['span'] for v in result['violations'] if v['rule'] == 'BRAND'}
    cleaned_tags = []
    for tag in tag_list:
        low = _norm(tag)
        if any(token == low or token in low for token in brand_hits):
            continue
        tag_clean = clean_text(tag)
        if not tag_clean:
            continue
        if len(tag_clean) > tag_char_limit:
            tag_clean = tag_clean[:tag_char_limit]
        if tag_clean not in cleaned_tags:
            cleaned_tags.append(tag_clean)
    cleaned_tags = cleaned_tags[:tag_limit]

    title_limit = int((rules.get('limits') or {}).get('title_max_chars') or 200)
    if len(title_clean) > title_limit:
        title_clean = title_clean[:title_limit]

    return {
        'title_ru': title_clean,
        'description_ru': desc_clean,
        'tags': cleaned_tags,
        'removed': len([v for v in result['violations']]),
    }


def lint_card(card, product=None, extra_brands=None):
    """按卡片结构检查（title_ru / description_ru / main_tags_ru 等）。"""
    card = card or {}
    tags = []
    for key in ('main_tags_ru', 'long_tail_tags_ru', 'auxiliary_tags_ru', 'tags_ru'):
        value = card.get(key)
        if isinstance(value, list):
            tags.extend(value)
    result = lint_fields(card.get('title_ru') or card.get('title') or '',
                         card.get('description_ru') or card.get('description') or '',
                         tags, product=product, extra_brands=extra_brands)
    result['tags'] = tags
    return result


def lint_product(product, extra_brands=None):
    """从商品对象提取标题/描述/主题标签做检查。"""
    rules = load_rules()
    tags = []
    tag_attr_id = rules.get('tags_attribute_id') or _TAGS_ATTR_ID
    for attr in (product or {}).get('attributes') or []:
        if not isinstance(attr, dict):
            continue
        if str(attr.get('id')) == str(tag_attr_id) or '主题标签' in str(attr.get('name') or ''):
            tags.extend(str(attr.get('value') or '').split())
    return lint_fields((product or {}).get('title') or '',
                       (product or {}).get('description') or '',
                       tags, product=product, extra_brands=extra_brands)


def lint_target(target, payload=None):
    """R7 入口：target 可以是 card_id（数字）、产品 id（id_xxx），或 None（payload 直接给文案）。

    返回 {ok, violations, sanitized, rules_version, source}。
    """
    payload = payload if isinstance(payload, dict) else {}
    if payload.get('title_ru') or payload.get('description_ru') or payload.get('tags'):
        result = lint_fields(payload.get('title_ru') or '', payload.get('description_ru') or '',
                             payload.get('tags') or [], product=payload.get('product'))
        sanitized = sanitize_fields(payload.get('title_ru') or '', payload.get('description_ru') or '',
                                    payload.get('tags') or [], product=payload.get('product'))
        result['sanitized'] = sanitized
        result['source'] = 'payload'
        return result

    from models.product import Product

    card = None
    product = None
    source = 'product'
    text = str(target or '')
    if text.isdigit():
        try:
            from services import card_service

            card = card_service.get_card(int(text))
        except Exception:
            card = None
        if not card:
            raise ValueError('卡片不存在：%s' % text)
        product = Product.find_by_id(str(card.get('product_id')))
        result = lint_card(card, product=product)
        source = 'card'
        sanitized = sanitize_fields(card.get('title_ru') or '', card.get('description_ru') or '',
                                    result.get('tags') or [], product=product)
    else:
        product = Product.find_by_id(text)
        if not product:
            raise ValueError('商品不存在：%s' % text)
        result = lint_product(product)
        sanitized = sanitize_fields(product.get('title') or '', product.get('description') or '',
                                    result.get('tags') or [], product=product)
    result['sanitized'] = sanitized
    result['source'] = source
    # R9：审计要能按 product_id 归因，lint 结果里带上被检查的商品
    result['product_id'] = (product or {}).get('id') or ''
    return result
