# -*- coding: utf-8 -*-
"""JLC GLOBAL 图像生成客户端（纳米香蕉 2.1，按次计费）。

接口文档来自 `GET /v1/skills` / `GET /v1/skills/guide`：
- 提交：POST {BASE}/v1/media/generate  {model, prompt, params:{aspectRatio, imageSize, web_search, images[]}}
- 轮询：GET  {BASE}/v1/skills/task-status?task_id=...
       判定结束用 is_final=true；state 取值 pending/running/success/failed
- 参考图：`images` 必须是数组，元素可为公网直链 URL 或 data:<mime>;base64,...
- 失败：HTTP 200 但 body.code=402 表示余额不足，需读 body.code 不能只看 HTTP 状态

API Key 来自环境变量 `JLC_API_KEY`，绝不写在代码里。
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

API_BASE = 'https://api.lk888.ai/api'
MODEL = 'banana-2.1'
POLL_INTERVAL = 5
DEFAULT_TIMEOUT = 300


class JLCError(Exception):
    """JLC 调用异常"""


def _api_key():
    key = (os.environ.get('JLC_API_KEY') or '').strip()
    if not key:
        raise JLCError('JLC_API_KEY 未配置')
    return key


def _request(method, path, body=None, timeout=90):
    payload = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(
        API_BASE + path,
        data=payload,
        method=method,
        headers={
            'Authorization': 'Bearer ' + _api_key(),
            'Content-Type': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', 'replace')
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = exc.read().decode('utf-8', 'replace')[:300]
        except Exception:
            pass
        raise JLCError('JLC HTTP %s: %s' % (exc.code, detail or exc.reason))
    except urllib.error.URLError as exc:
        raise JLCError('JLC 连接失败: %s' % exc.reason)


def _normalize_images(images):
    """参考图统一成数组：URL 字符串或 dataURL 直接保留；dict 取 url/dataUrl。"""
    out = []
    for item in images or []:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            value = item.get('url') or item.get('dataUrl') or item.get('data_url')
            if value:
                out.append(str(value))
    return out


def _collect_urls(value):
    """从任务结果里递归收集图片 URL。"""
    urls = []

    def walk(node):
        if isinstance(node, str):
            low = node.lower()
            if low.startswith('http') and any(ext in low for ext in ('.png', '.jpg', '.jpeg', '.webp')):
                urls.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for key, item in node.items():
                if key in ('url', 'image', 'image_url', 'output_url') and isinstance(item, str):
                    if item.lower().startswith('http'):
                        urls.append(item)
                else:
                    walk(item)

    walk(value)
    seen, out = set(), []
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def generate_image(prompt, aspect_ratio='3:4', image_size='2K', images=None,
                   model=MODEL, timeout=DEFAULT_TIMEOUT):
    """提交生图任务并轮询到结束，返回 {urls, task_id, cost, model}。"""
    prompt = (prompt or '').strip()
    if not prompt:
        raise JLCError('提示词不能为空')

    params = {
        'aspectRatio': aspect_ratio or '3:4',
        'imageSize': image_size or '2K',
        'web_search': 'false',
    }
    refs = _normalize_images(images)
    if refs:
        params['images'] = refs
    body = {'model': model, 'prompt': prompt, 'params': params}

    submitted = _request('POST', '/v1/media/generate', body)
    code = submitted.get('code')
    if code not in (None, 0, 200):
        raise JLCError('提交失败(%s): %s' % (code, submitted.get('msg') or str(submitted)[:200]))

    data = submitted.get('data') or {}
    task_id = data.get('task_id') or data.get('taskId') or data.get('id')
    if not task_id:
        raise JLCError('未拿到 task_id: %s' % json.dumps(submitted, ensure_ascii=False)[:200])

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _request('GET', '/v1/skills/task-status?task_id=%s' % task_id)
        info = status.get('data') or status
        if info.get('is_final'):
            state = (info.get('state') or '').lower()
            if state == 'success':
                urls = _collect_urls(info)
                if not urls:
                    raise JLCError('任务成功但没有取到图片地址')
                return {'urls': urls, 'task_id': task_id, 'cost': info.get('cost'), 'model': model}
            raise JLCError('任务失败(%s): %s' % (state or 'unknown',
                                               info.get('error_message') or info.get('msg') or ''))
        time.sleep(POLL_INTERVAL)
    raise JLCError('生图超时（%s 秒），任务 %s 仍在进行，稍后可在图库重试' % (timeout, task_id))


def balance():
    """查询当前 Key 余额（算力），用于上线自检。"""
    data = _request('GET', '/v1/skills/balance')
    return data.get('data') or data
