"""R8 回放：37 failed + 82 with_errors 的告警归因覆盖率（只读）。

用法：
    .venv/bin/python scripts/replay_diagnosis.py \
        --records data/publish_records.json --tasks data/publish_tasks.json [--out report.json]

口径：
- 每条记录都经 diagnose_record 处理：能匹配规则的进 items，匹配不上的原文进 unknown（不丢信息）
- 报告：记录级覆盖（有归因/仅 unknown）、片段级归因率、错误码分布、unknown 样本
"""
import argparse
import json
import os
import sys
from collections import Counter

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _load(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig') as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get('items') or data.get('records') or data.get('tasks') or []
    return data or []


def _diagnose_all(items, source, service):
    stats = {
        'total': len(items),
        'with_diagnosis': 0,
        'unknown_only': 0,
        'no_message': 0,
        'segments': 0,
        'diagnosed_segments': 0,
        'unknown_segments': 0,
        'codes': Counter(),
        'unknown_samples': [],
        'sample_items': [],
    }
    for item in items:
        result = service.diagnose_record(item)
        segments = result['summary']['segments']
        stats['segments'] += segments
        stats['diagnosed_segments'] += len(result['items'])
        stats['unknown_segments'] += len(result['unknown'])
        if segments == 0:
            stats['no_message'] += 1
            continue
        if result['items']:
            stats['with_diagnosis'] += 1
            for diag in result['items']:
                stats['codes'][diag['code']] += 1
            if len(stats['sample_items']) < 5:
                stats['sample_items'].append({
                    'source': source,
                    'recordId': item.get('id'),
                    'codes': [d['code'] for d in result['items']],
                })
        else:
            stats['unknown_only'] += 1
        if result['unknown'] and len(stats['unknown_samples']) < 8:
            stats['unknown_samples'].append({
                'source': source,
                'recordId': item.get('id'),
                'raw': result['unknown'][0]['raw'][:200],
            })
    return stats


def main():
    parser = argparse.ArgumentParser(description='R8 归因回放')
    parser.add_argument('--records', default='data/publish_records.json')
    parser.add_argument('--tasks', default='data/publish_tasks.json')
    parser.add_argument('--out', default='')
    args = parser.parse_args()

    from services import publish_diagnosis as service

    records = [r for r in _load(args.records)
               if isinstance(r, dict) and str(r.get('status')) in ('failed', 'published_with_errors')]
    tasks = [t for t in _load(args.tasks)
             if isinstance(t, dict) and str(t.get('status')) in ('failed', 'published_with_errors')]

    rec_failed = [r for r in records if str(r.get('status')) == 'failed']
    rec_warn = [r for r in records if str(r.get('status')) == 'published_with_errors']
    task_failed = [t for t in tasks if str(t.get('status')) == 'failed']
    task_warn = [t for t in tasks if str(t.get('status')) == 'published_with_errors']

    report = {
        'records': {
            'failed': _diagnose_all(rec_failed, 'records.failed', service),
            'with_errors': _diagnose_all(rec_warn, 'records.with_errors', service),
        },
        'tasks': {
            'failed': _diagnose_all(task_failed, 'tasks.failed', service),
            'with_errors': _diagnose_all(task_warn, 'tasks.with_errors', service),
        },
    }

    total = 0
    diagnosed = 0
    codes = Counter()
    for group in report.values():
        for part in group.values():
            total += part['total']
            diagnosed += part['with_diagnosis'] + part['unknown_only']
            codes.update(part['codes'])
    report['summary'] = {
        'total_records_and_tasks': total,
        'handled_all': diagnosed == total,
        'records_with_diagnosis': sum(p['with_diagnosis'] for g in report.values() for p in g.values()) if False else None,
        'codes_total': dict(codes),
        'note': 'unknown 表示原文已保留但未匹配规则；handled_all=True 即「全部有归因或明确 unknown」',
    }
    report['summary'].pop('records_with_diagnosis', None)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print('written', args.out)
    print(text)


if __name__ == '__main__':
    main()
