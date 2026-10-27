# -*- coding: utf-8 -*-
"""AI 广告监控：俄语商品/活动名 → 中文（批量 AI 翻译 + 本地缓存）。

要点：
- 原始俄语名保留在 `titleRu`，`title` 替换为中文，前端无需改动；
- 一屏最多收集 40 条待翻标题，**每 20 条合并成一次模型调用**（带序号，逐行解析），
  避免逐条请求把首屏拖到几十秒；
- 缓存表 `ad_title_cache(kind, ref, source, zh, updated_at)`，命中直接返回；
- 任何异常（未配 AI / 超时 / 解析失败）回退原文，绝不让页面因翻译报错。
"""
import re

from db import query, execute

_CYR = re.compile('[\u0400-\u04FF]')
MAX_NEW_PER_CALL = 40
BATCH_SIZE = 20
_TABLE_READY = False


def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    execute(
        'CREATE TABLE IF NOT EXISTS ad_title_cache ('
        ' kind TEXT NOT NULL,'
        ' ref TEXT NOT NULL,'
        ' source TEXT NOT NULL,'
        ' zh TEXT NOT NULL,'
        " updated_at TEXT NOT NULL DEFAULT (datetime('now')),"
        ' PRIMARY KEY (kind, ref))'
    )
    _TABLE_READY = True


def _cache_get(text):
    try:
        _ensure_table()
        row = query('SELECT zh FROM ad_title_cache WHERE kind = ? AND ref = ?',
                    ('title', text), one=True)
        return (row or {}).get('zh') or None
    except Exception:
        return None


def _cache_put(text, zh):
    try:
        execute('INSERT OR REPLACE INTO ad_title_cache (kind, ref, source, zh) '
                "VALUES ('title', ?, ?, ?)", (text, text, zh))
    except Exception:
        pass


def _ask_ai_batch(texts):
    """一次调用翻译多条，返回 {原文: 译文}。解析失败记为空，由调用方回退。"""
    from services.ai_config_service import get_ai_config, call_text_model

    config = get_ai_config()
    if not config or not config.get('api_key'):
        raise RuntimeError('AI 未配置')
    numbered = '\n'.join('%d. %s' % (i + 1, t) for i, t in enumerate(texts))
    messages = [
        {'role': 'system',
         'content': '你是电商本地化翻译。用户会给出编号列表，每行是一条俄语 OZON 商品标题或广告活动名。'
                    '请逐行翻译成简体中文，**保持相同编号与行数**，只输出「编号. 译文」，'
                    '不要解释、不要保留俄语原词；数字、型号、单位原样保留。'},
        {'role': 'user', 'content': numbered},
    ]
    out = call_text_model(config, messages, temperature=0.2,
                          max_tokens=200 + 60 * len(texts), timeout=45) or ''
    result = {}
    for line in out.splitlines():
        m = re.match(r'\s*(\d+)\s*[.、:：]\s*(.+?)\s*$', line)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        zh = m.group(2).strip().strip('"').strip()
        if 0 <= idx < len(texts) and zh:
            result[texts[idx]] = zh
    return result


def _translate_missing(texts):
    """按批翻译缺失标题，返回 {原文: 译文}。"""
    mapping = {}
    for start in range(0, len(texts), BATCH_SIZE):
        chunk = texts[start:start + BATCH_SIZE]
        try:
            mapping.update(_ask_ai_batch(chunk))
        except Exception:
            continue
    return mapping


def deep(obj):
    """递归把 dict 里的 title 字段翻译成中文（原值存 titleRu）。返回同一个对象。"""
    # 1) 先收集待翻文本
    pending = []

    def collect(value):
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if key == 'title' and isinstance(item, str) and _CYR.search(item):
                    value.setdefault('titleRu', item)
                    if len(pending) < MAX_NEW_PER_CALL and _cache_get(item) is None:
                        pending.append(item)
                else:
                    collect(item)
        elif isinstance(value, list):
            for entry in value:
                collect(entry)

    collect(obj)

    # 2) 批量翻译缺失项（去重、限量）
    unique = []
    seen = set()
    for text in pending:
        if text not in seen:
            seen.add(text)
            unique.append(text)
    mapping = _translate_missing(unique[:MAX_NEW_PER_CALL]) if unique else {}
    for src, zh in mapping.items():
        if zh:
            _cache_put(src, zh)

    # 3) 回填
    def apply(value):
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if key == 'title' and isinstance(item, str) and _CYR.search(item):
                    value['title'] = _cache_get(item) or item
                else:
                    apply(item)
        elif isinstance(value, list):
            for entry in value:
                apply(entry)

    apply(obj)
    return obj
