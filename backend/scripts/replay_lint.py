"""R7 回放：用历史 with_errors 发布记录检验文案过滤命中率（只读）。

口径：
- 从 publish_records.json / publish_tasks.json 取 published_with_errors 记录，按错误文本分类
- 只统计「文案类」告警（BRAND / PROMO / ORIGINAL / HASHTAG-广告词），图片/禁售/单位/价格等不是文案规则范围
- 命中 = 商品当前文案被 lint 出同一类问题（BRAND→BRAND；HASHTAG/PROMO→PROMO；ORIGINAL→ORIGINAL_CLAIM）

用法：
    .venv/bin/python scripts/replay_lint.py --products data/products.json \
        --records data/publish_records.json --tasks data/publish_tasks.json [--out report.json]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

COPY_CATEGORIES = {'BRAND', 'PROMO', 'ORIGINAL', 'HASHTAG'}
CATEGORY_TO_RULES = {
    'BRAND': {'BRAND'},
    'PROMO': {'PROMO'},
    'HASHTAG': {'PROMO', 'BRAND', 'ORIGINAL_CLAIM'},
    'ORIGINAL': {'ORIGINAL_CLAIM'},
}


def _load(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig') as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get('items') or data.get('records') or data.get('tasks') or list(data.values())
    return data or []


def _record_text(record):
    parts = []
    err = record.get('error')
    if isinstance(err, str):
        parts.append(err)
    errors = record.get('errors')
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get('message') or item.get('msg') or item))
    return ' | '.join(parts)


def _classify(text):
    low = text.lower()
    cats = set()
    if 'хештег' in low or 'hashtag' in low:
        cats.add('HASHTAG')
    if 'названия всех брендов' in low or 'бренд' in low or 'brand' in low:
        cats.add('BRAND')
    if 'оригинал' in low:
        cats.add('ORIGINAL')
    if re.search(r'скидк|акци|распродаж|дешев|рекламн', low):
        cats.add('PROMO')
    if not cats:
        cats.add('NON_COPY')
    else:
        cats.add('COPY')
    return cats


def _parse_tags(raw):
    """product_cards 的 tags 字段是 JSON 字符串。"""
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(item) for item in data]
    except Exception:
        pass
    return [str(raw)]


def _brands_from_products(products):
    brands = set()
    for product in products:
        if not isinstance(product, dict):
            continue
        for attr in product.get('attributes') or []:
            if not isinstance(attr, dict):
                continue
            attr_id = attr.get('id')
            name = str(attr.get('name') or '').lower()
            if attr_id in (85,) or 'бренд' in name or '品牌' in name:
                value = str(attr.get('value') or '').strip()
                if 2 <= len(value) <= 40:
                    brands.add(value)
    return brands


def main():
    parser = argparse.ArgumentParser(description='R7 文案过滤回放')
    parser.add_argument('--products', default='data/products.json')
    parser.add_argument('--records', default='data/publish_records.json')
    parser.add_argument('--tasks', default='data/publish_tasks.json')
    parser.add_argument('--cards', default='', help='product_cards 导出 JSON（可选，含历史版本标签）')
    parser.add_argument('--out', default='')
    args = parser.parse_args()

    from services.copy_lint import lint_fields

    products = _load(args.products)
    index = {str(p.get('id')): p for p in products if isinstance(p, dict) and p.get('id')}
    extra_brands = _brands_from_products(products)

    cards_by_product = {}
    if args.cards and os.path.exists(args.cards):
        try:
            with open(args.cards, encoding='utf-8-sig') as cf:
                cards_data = json.load(cf)
        except Exception:
            cards_data = []
        if isinstance(cards_data, dict):
            cards_data = cards_data.get('cards') or []
        for card in cards_data:
            if isinstance(card, dict) and card.get('product_id'):
                cards_by_product.setdefault(str(card['product_id']), []).append(card)

    records = _load(args.records)
    tasks = _load(args.tasks)
    with_errors_records = [r for r in records if str(r.get('status')) == 'published_with_errors']
    with_errors_tasks = [t for t in tasks if str(t.get('status')) == 'published_with_errors']

    stats = {
        'records_with_errors': len(with_errors_records),
        'tasks_with_errors': len(with_errors_tasks),
        'copy_relevant': 0,
        'copy_hit': 0,
        'copy_miss': 0,
        'copy_any': 0,
        'non_copy': 0,
        'product_missing': 0,
        'category_counter': Counter(),
        'hit_counter': Counter(),
        'samples_hit': [],
        'samples_miss': [],
    }

    for record in with_errors_records:
        text = _record_text(record)
        cats = _classify(text)
        stats['category_counter'].update(sorted(cats - {'COPY', 'NON_COPY'}))
        if 'COPY' not in cats:
            stats['non_copy'] += 1
            continue
        stats['copy_relevant'] += 1
        product = index.get(str(record.get('productId') or '')) or {}
        if not product and not record.get('title'):
            stats['product_missing'] += 1
            continue
        tags = []
        for attr in product.get('attributes') or []:
            if isinstance(attr, dict) and (str(attr.get('id')) == '23171'
                                           or '主题标签' in str(attr.get('name') or '')):
                tags.extend(str(attr.get('value') or '').split())
        # 发布记录里的 title 是「发布当时」的真实文本；商品当前 tags/description 补齐
        title_text = str(record.get('title') or product.get('title') or '')
        rules = set()
        lint = lint_fields(title_text,
                           str(product.get('description') or ''),
                           tags, product=product, extra_brands=extra_brands)
        rules |= {v['rule'] for v in lint.get('violations') or []}
        # 商品卡历史版本：当时的 AI 文案/标签（更能反映发布时刻的文本）
        for card in cards_by_product.get(str(record.get('productId') or ''), []):
            card_rules = set()
            card_lint = lint_fields(
                str(card.get('title_ru') or ''),
                str(card.get('description_ru') or ''),
                _parse_tags(card.get('main_tags_ru')) + _parse_tags(card.get('long_tail_tags_ru'))
                + _parse_tags(card.get('auxiliary_tags_ru')),
                product=product, extra_brands=extra_brands,
            )
            card_rules |= {v['rule'] for v in card_lint.get('violations') or []}
            rules |= card_rules
        wanted = set()
        for cat in cats & COPY_CATEGORIES:
            wanted |= CATEGORY_TO_RULES.get(cat, set())
        if rules:
            stats['copy_any'] += 1
        if rules & wanted:
            stats['copy_hit'] += 1
            stats['hit_counter'].update(sorted(rules & wanted))
            if len(stats['samples_hit']) < 5:
                stats['samples_hit'].append({'productId': record.get('productId'),
                                             'warn_cats': sorted(cats & COPY_CATEGORIES),
                                             'lint_rules': sorted(rules),
                                             'title': str(product.get('title') or '')[:60]})
        else:
            stats['copy_miss'] += 1
            if len(stats['samples_miss']) < 8:
                stats['samples_miss'].append({'productId': record.get('productId'),
                                              'warn_cats': sorted(cats & COPY_CATEGORIES),
                                              'lint_rules': sorted(rules),
                                              'text': text[:160]})

    denom = stats['copy_relevant'] or 1
    report = {
        'records_with_errors': stats['records_with_errors'],
        'tasks_with_errors': stats['tasks_with_errors'],
        'copy_relevant_records': stats['copy_relevant'],
        'copy_hit': stats['copy_hit'],
        'copy_miss': stats['copy_miss'],
        'product_missing': stats['product_missing'],
        'hit_rate_on_copy_relevant': round(stats['copy_hit'] / denom, 4),
        'detected_any_copy_risk': stats['copy_any'],
        'detect_rate_any_copy_risk': round(stats['copy_any'] / denom, 4),
        'non_copy_records': stats['non_copy'],
        'warning_categories': dict(stats['category_counter']),
        'hit_rules': dict(stats['hit_counter']),
        'samples_hit': stats['samples_hit'],
        'samples_miss': stats['samples_miss'],
        'note': '分母=文案类告警记录（BRAND/PROMO/ORIGINAL/HASHTAG）；图片/禁售/单位/价格类不计入文案规则',
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print('written', args.out)
    print(text)


if __name__ == '__main__':
    main()
