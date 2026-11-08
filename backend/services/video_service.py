# -*- coding: utf-8 -*-
"""AI 视频工坊：商品图 -> 中转站图生视频 -> ERP 视频任务。

使用凌科中转站 `/v1/media/generate`，不依赖本机 ffmpeg。
API Key 只从环境变量 `LK_VIDEO_API_KEY` 读取，不写进代码。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from config import Config
from db import query

API_BASE = 'https://api.lk888.ai/api'
DEFAULT_MODEL = 'hailuo-h3-shouweizhen'
DEFAULT_DURATION = '6'
DEFAULT_RESOLUTION = '768P'
MODEL_HAILUO = 'hailuo-h3-shouweizhen'
MODEL_SEEDANCE = 'seedance-2.0-guanfang'
TASKS_FILE = os.path.join(os.path.dirname(Config.PRODUCTS_FILE), 'video_tasks.json')
_LOCK = threading.Lock()


class VideoError(Exception):
    pass


def _key():
    value = (os.environ.get('LK_VIDEO_API_KEY') or os.environ.get('LK_VIDEO_KEY') or '').strip()
    if not value:
        raise VideoError('未配置 LK_VIDEO_API_KEY')
    return value


def _request(method, path, body=None, timeout=120):
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(
        API_BASE + path,
        data=data,
        method=method,
        headers={
            'Authorization': 'Bearer ' + _key(),
            'Content-Type': 'application/json',
            'Accept-Language': 'zh-CN',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = exc.read().decode('utf-8', 'replace')[:1000]
        except Exception:
            pass
        raise VideoError('中转站 HTTP %s: %s' % (exc.code, detail or exc.reason))
    except urllib.error.URLError as exc:
        raise VideoError('中转站连接失败: %s' % exc.reason)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load():
    if not os.path.exists(TASKS_FILE):
        return []
    try:
        with open(TASKS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items):
    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    tmp = TASKS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TASKS_FILE)


def _patch(task_id, **fields):
    with _LOCK:
        items = _load()
        for item in items:
            if item.get('id') == task_id:
                item.update(fields)
                item['updatedAt'] = _now()
                _save(items)
                return item
    return None


def list_tasks(limit=30):
    with _LOCK:
        items = _load()
    return list(reversed(items))[:max(1, min(int(limit or 30), 100))]


def get_task(task_id):
    with _LOCK:
        for item in _load():
            if item.get('id') == task_id:
                return item
    return None


def _run(task_id):
    task = get_task(task_id)
    if not task:
        return
    try:
        model = task.get('model') or DEFAULT_MODEL
        images = (task.get('imageUrls') or [task['imageUrl']])
        if 'seedance' in str(model):
            params = {
                'images': images[:9],
                'mode': task.get('mode') or 'cankaosheng',
                'version': task.get('version') or '快速',
                'duration': str(task.get('duration') or DEFAULT_DURATION),
                'resolution': task.get('resolution') or '720p',
                'aspect_ratio': 'adaptive',
            }
        else:
            params = {
                'images': images[:2],
                'duration': str(task.get('duration') or DEFAULT_DURATION),
                'resolution': task.get('resolution') or DEFAULT_RESOLUTION,
            }
        body = {
            'model': model,
            'prompt': task.get('prompt') or '',
            'params': params,
        }
        _patch(task_id, status='submitting')
        submitted = _request('POST', '/v1/media/generate', body)
        data = submitted.get('data') or {}
        remote_id = data.get('task_id') or data.get('taskId') or data.get('id') or submitted.get('task_id')
        if not remote_id:
            raise VideoError('提交失败：' + json.dumps(submitted, ensure_ascii=False)[:800])
        _patch(task_id, status='running', remoteTaskId=str(remote_id))
        deadline = time.time() + 600
        while time.time() < deadline:
            status = _request('GET', '/v1/skills/task-status?task_id=' + str(remote_id))
            info = status.get('data') or status
            _patch(task_id, progress=str(info.get('progress') or ''), cost=info.get('cost'))
            if info.get('is_final'):
                if str(info.get('state') or '').lower() == 'success':
                    urls = info.get('result_urls') or ([info.get('result_url')] if info.get('result_url') else [])
                    if not urls:
                        raise VideoError('任务成功但没有返回视频 URL')
                    _patch(task_id, status='success', videoUrl=urls[0], remoteTaskId=str(remote_id),
                           cost=info.get('cost'), progress='100', finishedAt=_now(),
                           channel=info.get('channel_group') or '')
                    return
                raise VideoError('生成失败：' + str(info.get('error') or info.get('msg') or info.get('state') or ''))
            time.sleep(5)
        raise VideoError('生成超时（10 分钟），任务可能仍在运行')
    except Exception as exc:
        _patch(task_id, status='failed', error=str(exc)[:1000], finishedAt=_now())


def create_task(product, image_url='', prompt='', model=DEFAULT_MODEL, duration=DEFAULT_DURATION,
                resolution=DEFAULT_RESOLUTION, operator='', image_urls=None, version='', mode=''):
    product_id = str((product or {}).get('id') or '')
    if not product_id:
        raise VideoError('缺少商品 ID')
    urls = []
    for value in (image_urls or []):
        value = str(value or '').strip()
        if value.startswith(('http://', 'https://')) and value not in urls:
            urls.append(value)
    if not urls and str(image_url or '').strip():
        urls = [str(image_url).strip()]
    max_images = 9 if 'seedance' in str(model or '') else 2
    urls = urls[:max_images]
    if not urls:
        raise VideoError('请至少选择 1 张公网商品图')
    image_url = urls[0]
    title = str((product or {}).get('title') or '')
    prompt = str(prompt or '').strip() or (
        '商品展示视频：' + title + '。镜头缓慢推进，产品保持清晰不变形，'
        '科技感柔光，适合 Ozon 商品卡，竖屏或方形，6 秒。'
    )
    task = {
        'id': 'vid_' + uuid.uuid4().hex[:12],
        'productId': product_id,
        'productTitle': title,
        'imageUrl': image_url,
        'imageUrls': urls,
        'prompt': prompt[:5000],
        'model': model or DEFAULT_MODEL,
        'version': version or ('快速' if 'seedance' in str(model or '') else ''),
        'mode': mode or ('cankaosheng' if 'seedance' in str(model or '') else ''),
        'duration': str(duration or DEFAULT_DURATION),
        'resolution': resolution or DEFAULT_RESOLUTION,
        'status': 'queued',
        'progress': '0',
        'videoUrl': '',
        'error': '',
        'cost': 0,
        'operator': operator,
        'createdAt': _now(),
        'updatedAt': _now(),
    }
    with _LOCK:
        items = _load()
        items.append(task)
        _save(items)
    threading.Thread(target=_run, args=(task['id'],), daemon=True).start()
    return task


def bind_to_product(task, video_name=''):
    if not task or task.get('status') != 'success' or not task.get('videoUrl'):
        raise VideoError('视频尚未生成完成')
    from models.product import Product
    product = Product.find_by_id(str(task.get('productId') or ''))
    if not product:
        raise VideoError('商品不存在')
    url = task['videoUrl']
    name = str(video_name or task.get('productTitle') or 'Product video')[:200]
    update = {
        'videos': [url],
        'videoList': [url],
        'videoNames': [name],
    }
    Product.update(product['id'], update)
    return {'productId': product['id'], 'videoUrl': url, 'videoName': name}


def sync_to_ozon(task, video_name=''):
    if not task or task.get('status') != 'success' or not task.get('videoUrl'):
        raise VideoError('视频尚未生成完成')
    from models.product import Product
    from services.ozon_api import _get_store_credentials, import_products, get_product_info
    from services.publish_service import build_ozon_product_item

    product = Product.find_by_id(str(task.get('productId') or ''))
    if not product:
        raise VideoError('商品不存在')
    offer_id = ''
    for sku in (product.get('skus') or []):
        offer_id = str(sku.get('offerId') or sku.get('skuCode') or sku.get('sku') or '').strip()
        if offer_id:
            break
    offer_id = offer_id or str(product.get('offerId') or '').strip()
    if not offer_id:
        raise VideoError('商品没有 offer_id，无法同步 Ozon')
    if not product.get('ozonProductId') and not (product.get('ozonProductIds') or []):
        raise VideoError('商品尚未发布到 Ozon，请先发布')
    store_id = product.get('storeId')
    if not store_id:
        raise VideoError('商品未绑定店铺')
    store_row = query('SELECT store_id FROM stores WHERE id = ?', (store_id,), one=True)
    ozon_store_id = (store_row or {}).get('store_id') or store_id
    client_id, api_key = _get_store_credentials(ozon_store_id)
    if not client_id or not api_key:
        raise VideoError('店铺 Ozon 凭证不可用')
    existing = get_product_info(offer_id=offer_id, client_id=client_id, api_key=api_key)
    if not existing or not existing.get('id'):
        raise VideoError('Ozon 未找到该 offer_id，已阻止导入以避免新建重复商品')
    name = str(video_name or task.get('productTitle') or 'Product video')[:200]
    # 完整导入才能让 Ozon 的 complex_attributes 视频真正落到商品卡；
    # 单字符属性更新接口对 21841/21837 不保证生效。
    item = build_ozon_product_item(product, store_id=store_id)
    item.pop('videos', None)
    item['attributes'] = [
        a for a in (item.get('attributes') or [])
        if str(a.get('id')) not in ('21841', '21837', '21845', '22273')
    ]
    item['complex_attributes'] = [{
        'attributes': [
            {'complex_id': 100001, 'id': 21841, 'values': [{'value': task['videoUrl']}]},
            {'complex_id': 100001, 'id': 21837, 'values': [{'value': name}]},
        ]
    }]
    result = import_products([item], client_id=client_id, api_key=api_key)
    return {'offerId': offer_id, 'result': result, 'videoUrl': task['videoUrl'], 'videoName': name}




