"""R6 回放脚本：把历史 failed 发布任务喂给发布前校验，产出拦截率报告。

只读：读取 data/ 下的 products.json / publish_tasks.json / publish_records.json 与 SQLite
（本地类目缓存），不调 Ozon、不写任何数据。用法：

    .venv/bin/python scripts/replay_validate.py \
        --products data/products.json \
        --tasks data/publish_tasks.json \
        --records data/publish_records.json \
        --out /tmp/replay_validate.json

输出：
- failed 任务总数、可回放数（商品还在）、被 blocking 拦截数/比例
- 各错误码命中次数
- 未被拦截的样本（供人工看还缺什么规则）
- with_errors 任务的诊断参考（仅统计，不判定）
"""
import argparse
import json
import os
import sys
from collections import Counter

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

BLOCKING_CODES = {
    'MISSING_REQUIRED', 'MISSING_DICTIONARY', 'NO_IMAGE', 'BAD_IMAGE',
    'DENSITY_OUT_OF_RANGE', 'OFFER_ID_TOO_LONG', 'PRICE_INVALID',
    'PAYLOAD_BUILD_FAILED',
}


def _load_json(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig') as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get('items') or data.get('tasks') or data.get('records') or []
    return data or []


def _index_products(items):
    out = {}
    for item in items:
        if isinstance(item, dict) and item.get('id'):
            out[str(item['id'])] = item
    return out


def main():
    parser = argparse.ArgumentParser(description='R6 发布前校验回放')
    parser.add_argument('--products', default='data/products.json')
    parser.add_argument('--tasks', default='data/publish_tasks.json')
    parser.add_argument('--records', default='data/publish_records.json')
    parser.add_argument('--out', default='')
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()

    from services.agent_validate_service import validate_product_dict

    products = _index_products(_load_json(args.products))
    tasks = [t for t in _load_json(args.tasks) if isinstance(t, dict)]
    records = [r for r in _load_json(args.records) if isinstance(r, dict)]

    failed = [t for t in tasks if str(t.get('status')) == 'failed']
    with_errors = [t for t in tasks if str(t.get('status')) == 'published_with_errors']
    if args.limit:
        failed = failed[:args.limit]

    stats = {
        'products_total': len(products),
        'tasks_total': len(tasks),
        'failed_total': len(failed),
        'replayable': 0,
        'product_missing': 0,
        'blocked': 0,
        'not_blocked': 0,
        'code_counter': Counter(),
        'samples_blocked': [],
        'samples_missed': [],
        'with_errors_total': len(with_errors),
    }

    for task in failed:
        product_id = str(task.get('productId') or '')
        product = products.get(product_id)
        if not product:
            stats['product_missing'] += 1
            continue
        stats['replayable'] += 1
        try:
            result = validate_product_dict(product, store_id=task.get('storeId'))
        except Exception as exc:  # noqa: BLE001
            stats['blocked'] += 1
            stats['code_counter']['REPLAY_ERROR'] += 1
            stats['samples_blocked'].append({'productId': product_id, 'codes': ['REPLAY_ERROR'],
                                             'error': str(exc)[:200]})
            continue
        codes = [item.get('code') for item in result.get('blocking') or [] if item.get('code') in BLOCKING_CODES]
        if codes:
            stats['blocked'] += 1
            stats['code_counter'].update(codes)
            if len(stats['samples_blocked']) < 10:
                stats['samples_blocked'].append({'productId': product_id,
                                                 'taskId': task.get('id'),
                                                 'codes': codes,
                                                 'messages': [i.get('message') for i in result['blocking']][:3]})
        else:
            stats['not_blocked'] += 1
            if len(stats['samples_missed']) < 10:
                stats['samples_missed'].append({
                    'productId': product_id,
                    'taskId': task.get('id'),
                    'publish_error': str(task.get('error') or '')[:160],
                })

    replayable = stats['replayable'] or 1
    report = {
        'input': {'products': args.products, 'tasks': args.tasks, 'records': args.records},
        'failed_total': stats['failed_total'],
        'replayable': stats['replayable'],
        'product_missing': stats['product_missing'],
        'blocked': stats['blocked'],
        'not_blocked': stats['not_blocked'],
        'block_rate': round(stats['blocked'] / replayable, 4),
        'code_counter': dict(stats['code_counter']),
        'samples_blocked': stats['samples_blocked'],
        'samples_missed': stats['samples_missed'],
        'with_errors_total': stats['with_errors_total'],
        'records_total': len(records),
        'note': '发布前校验用本地类目缓存，不调 Ozon；block_rate = blocked / replayable',
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print('written', args.out)
    print(text)


if __name__ == '__main__':
    main()
