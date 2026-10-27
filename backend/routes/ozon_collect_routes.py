"""Ozon 对标商品采集接口（浏览器插件专用）。

链路
----
插件在 ozon.ru 商品页内读取 entrypoint-api（同源请求 + 用户自己的 Cookie），
把商品数据 POST 到本模块 → 服务端负责下载图片并托管到 /uploads →
商品写入 ERP 采集箱（Product JSON）。

为什么不让服务端直接抓：Ozon 对机房 IP 会返回验证码挑战（403 + challengeURL），
服务端直连拿不到商品数据；图片 CDN 不受影响，所以下载图片仍放在服务端做。

鉴权：X-Ext-Key（与 /api/ext/* 同一把 key）或 ERP 登录态。
"""
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from models.product import Product
from utils.auth import get_current_user

ozon_collect_bp = Blueprint('ozon_collect', __name__)

SERVICE_VERSION = '1.0.0'

MAX_MAIN_IMAGES = 15
MAX_DETAIL_IMAGES = 30
MAX_IMAGES_TOTAL = 45
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_TIMEOUT = 25
IMAGE_WORKERS = 4
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
ALLOWED_EXT = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
# 采集图入库统一 JPEG 的参数（2026-10-28 定）
# 线上实测：uploads 614 张图长边最大 1445（p50 1360），体积 1.1GB —— 全是 PNG 存照片
# 导致（中位 1.9MB）；同分辨率转 JPEG q90 中位 224KB（约 0.12x），像素不损失。
MAX_IMAGE_SIDE = 1600        # 长边上限，超过才缩放
JPEG_QUALITY = 90
JPEG_PASSTHROUGH_BYTES = 1200000  # 已是 JPEG 且 ≤1.2MB 时不再二次压缩，避免画质叠加损失


# ---------------------------------------------------------------- 基础工具

def _plugin_api_keys():
    """插件 API Key 白名单：OZON_EXT_API_KEYS（逗号分隔）> EXT_API_KEY；未配置返回空列表。

    支持多 key 并行是为了轮换：新 key 上线后旧 key 保留一段时间，
    等所有使用方换成 chrome.storage 配置后再删掉旧值。
    """
    import os
    raw = os.environ.get('OZON_EXT_API_KEYS') or os.environ.get('EXT_API_KEY') or ''
    return [item.strip() for item in str(raw).split(',') if item.strip()]


def _plugin_api_key():
    keys = _plugin_api_keys()
    return keys[0] if keys else ''


def _authorized():
    key = request.headers.get('X-Ext-Key', '')
    if key and key in _plugin_api_keys():
        return True
    return bool(get_current_user())


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _now_text():
    return datetime.now().strftime('%Y-%m-%d %H:%M')


def _clean_text(value, limit=2000):
    if value is None:
        return ''
    text = str(value).replace('\x00', ' ').strip()
    text = re.sub(r'[ \t]+', ' ', text)
    return text[:limit]


def _json_error(message, code=400):
    return jsonify({'code': code, 'msg': message}), code


def _ok(data, msg='成功'):
    return jsonify({'code': 200, 'msg': msg, 'data': data})


def _is_http_url(url):
    return isinstance(url, str) and url.lower().startswith(('http://', 'https://'))


def _normalize_image_url(url):
    url = _clean_text(url, 1200)
    if not _is_http_url(url):
        return ''
    # Ozon 图床会带尺寸参数，去掉后拿到原图
    return re.sub(r'[?&](width|height|size)=\d+', '', url)


def _extract_ozon_id(url):
    match = re.search(r'/product/(?:[^/?#]*-)?(\d{6,})', url or '')
    if match:
        return match.group(1)
    match = re.search(r'(\d{6,})', url or '')
    return match.group(1) if match else ''


# ---------------------------------------------------------------- 图片下载

def _download_image(url):
    """下载单张图片，返回 (bytes, ext)。失败抛异常。"""
    req = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9',
        'Referer': 'https://www.ozon.ru/',
    })
    with urllib.request.urlopen(req, timeout=IMAGE_TIMEOUT) as resp:
        data = resp.read(MAX_IMAGE_BYTES + 1)
        content_type = (resp.headers.get('Content-Type') or '').lower()
    if not data:
        raise ValueError('空图片')
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('图片超过 10MB')
    is_image = 'image' in content_type or data[:4].startswith(
        (b'\x89PNG', b'\xff\xd8', b'GIF8', b'RIFF', b'<svg'))
    if not is_image:
        raise ValueError(f'非图片内容({content_type[:30] or "unknown"})')

    ext = ''
    for candidate in ('webp', 'png', 'jpeg', 'jpg', 'gif'):
        if url.lower().split('?')[0].endswith('.' + candidate):
            ext = 'jpg' if candidate == 'jpeg' else candidate
            break
    if not ext:
        if data[:4] == b'\x89PNG':
            ext = 'png'
        elif data[:3] == b'GIF':
            ext = 'gif'
        elif data[:4] == b'RIFF':
            ext = 'webp'
        else:
            ext = 'jpg'
    if ext not in ALLOWED_EXT:
        ext = 'jpg'
    return data, ext


def _normalize_collected_image(data, ext):
    """采集图入库前统一成 JPEG（长边 ≤1600，q90），返回 (bytes, ext)。

    2026-10-28 线上实测：`data/uploads` 614 张图长边最大 1445、p50 1360，
    体积却有 1.1GB —— 全是 PNG 存照片（中位 1.9MB），与该分辨率不匹配。
    同分辨率转 JPEG q90 中位 224KB（≈0.12x），像素不变、页面与参考图链路都快。

    规则（只影响新采集，历史图不动）：
    - 动图（多帧 GIF/WebP）原样保留，避免丢帧
    - 已是 JPEG、长边 ≤1600 且 ≤1.2MB、无 EXIF 旋转时原样返回，避免二次压缩叠加损失
    - 有透明通道铺白底；EXIF 方向先转正；CMYK/P/16bit 统一转 RGB
    - 转完比原图还大就保留原图（小尺寸纯色 PNG 会这样）
    - 任何异常一律回退原图，绝不因为压缩让采集失败
    """
    try:
        import io

        from PIL import Image, ImageOps
    except Exception:
        return data, ext
    try:
        with Image.open(io.BytesIO(data)) as im:
            if getattr(im, 'n_frames', 1) > 1:
                return data, ext
            try:
                orientation = im.getexif().get(274, 1)
            except Exception:
                orientation = 1
            im = ImageOps.exif_transpose(im)
            width, height = im.size
            longest = max(width, height)
            if (ext in ('jpg', 'jpeg') and longest <= MAX_IMAGE_SIDE
                    and len(data) <= JPEG_PASSTHROUGH_BYTES and im.mode == 'RGB'
                    and orientation in (None, 1)):
                return data, ext
            if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
                rgba = im.convert('RGBA')
                canvas = Image.new('RGB', rgba.size, (255, 255, 255))
                canvas.paste(rgba, mask=rgba.split()[-1])
                im = canvas
            else:
                im = im.convert('RGB')
            if longest > MAX_IMAGE_SIDE:
                ratio = MAX_IMAGE_SIDE / float(longest)
                im = im.resize((max(1, int(round(width * ratio))),
                                max(1, int(round(height * ratio)))), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format='JPEG', quality=JPEG_QUALITY, optimize=True, progressive=True)
            converted = buf.getvalue()
        if not converted or len(converted) >= len(data):
            return data, ext
        return converted, 'jpg'
    except Exception:
        return data, ext


def _host_image(url):
    """下载并托管到 ERP /uploads，返回公网 URL。失败抛异常。"""
    from services.storage_service import save_image
    data, ext = _download_image(url)
    data, ext = _normalize_collected_image(data, ext)
    hosted = save_image(data, ext)
    if not hosted:
        raise ValueError('托管返回空 URL')
    return hosted


def _collect_images(items, title, kind):
    """并发下载一批图片。items: [{url, ...}]，返回 (hosted_list, failed_list, skipped_list)。"""
    results = {}
    failed = []
    skipped = []
    seen = set()
    queue = []
    for item in items:
        url = _normalize_image_url(item.get('url') if isinstance(item, dict) else item)
        if not url:
            continue
        key = url.split('?')[0]
        if key in seen:
            skipped.append({'url': url, 'reason': '重复图片'})
            continue
        seen.add(key)
        queue.append(url)

    def worker(url):
        try:
            return url, _host_image(url), None
        except Exception as exc:  # noqa: BLE001 - 单张失败不影响整体
            return url, None, str(exc)[:160]

    if queue:
        with ThreadPoolExecutor(max_workers=min(IMAGE_WORKERS, len(queue))) as pool:
            for url, hosted, error in pool.map(worker, queue):
                if hosted:
                    results[url] = hosted
                else:
                    failed.append({'url': url, 'error': error})

    ordered = [results[u] for u in queue if u in results]
    return ordered, failed, skipped


# ---------------------------------------------------------------- 数据整理

def _normalize_attributes(raw):
    result = []
    if isinstance(raw, dict):
        raw = [{'name': k, 'value': v} for k, v in raw.items()]
    if not isinstance(raw, list):
        return result
    seen = set()
    for item in raw:
        if isinstance(item, str):
            item = {'name': item, 'value': ''}
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get('name') or item.get('key') or '', 200)
        if name == '[object Object]':
            continue
        value = item.get('value')
        if isinstance(value, (list, tuple)):
            value = ', '.join(_clean_text(v, 300) for v in value if _clean_text(v, 300))
        value = _clean_text(value, 1500)
        if not name or not value or value == '[object Object]':
            continue
        if name in seen:
            continue
        seen.add(name)
        attribute = {'name': name, 'value': value}
        attr_id = item.get('id') or item.get('attrId') or item.get('attribute_id') or item.get('attributeId')
        try:
            attr_id = int(attr_id)
        except (TypeError, ValueError):
            attr_id = None
        if attr_id and attr_id > 0:
            attribute['id'] = attr_id
        result.append(attribute)
    return result[:300]


def _normalize_variants(raw):
    result = []
    if not isinstance(raw, list):
        return result
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get('name') or item.get('label') or '', 100)
        options = item.get('options') or item.get('values') or []
        if isinstance(options, list):
            options = [_clean_text(o, 120) for o in options if _clean_text(o, 120)]
        else:
            options = []
        options = list(dict.fromkeys(options))[:60]
        if name and options:
            result.append({'name': name, 'options': options})
    return result


def _normalize_skus(raw):
    """SKU 行可选：sku_id 允许为空（按用户裁定，无 ID 时插件只发 variants 不发 skus）。

    空行（无 combo 值、无价格、无图、无 sku_id）直接丢弃，避免污染采集箱。
    """
    result = []
    if not isinstance(raw, list):
        return result
    for item in raw:
        if not isinstance(item, dict):
            continue
        combo = item.get('combo') if isinstance(item.get('combo'), dict) else {}
        combo = {(_clean_text(k, 100)): _clean_text(v, 200) for k, v in combo.items()}
        combo = {k: v for k, v in combo.items() if k and v}
        sku_id = _clean_text(item.get('sku_id') or item.get('skuId') or '', 60)
        price = item.get('price') if item.get('price') not in (None, '') else None
        image = _clean_text(item.get('image') or '', 1000)
        if not combo and not sku_id and price is None and not image:
            continue
        result.append({
            'skuId': sku_id,
            'combo': combo,
            'sourcePrice': price,
            'price': price,
            'stock': None,
            'image': image,
        })
    return result[:60]


MAX_REASONABLE_PRICE = 100_000_000  # 1 亿卢布，超过视为解析错误


def _parse_price(value):
    """价格解析：字符串取第一个数字（避免把多行价格拼成一个天文数字），并做合理性兜底。"""
    if isinstance(value, bool) or value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value)
        match = re.search(r'\d[\d\s\u00a0.,]*', text)
        if not match:
            return None
        raw = match.group(0).replace('\u00a0', ' ').strip()
        # 去掉千分位空格；逗号视作小数点
        cleaned = raw.replace(' ', '').replace(',', '.')
        # 形如 1.234.567 的欧洲写法：多个点视为千分位
        if cleaned.count('.') > 1:
            parts = cleaned.split('.')
            cleaned = parts[0] + ''.join(parts[1:]) if len(parts[-1]) == 3 else ''.join(parts)
        try:
            number = float(cleaned)
        except ValueError:
            return None
    if number <= 0 or number > MAX_REASONABLE_PRICE:
        return None
    return round(number, 2)


def _image_item_url(item):
    """图片项兼容两种形态：URL 字符串 / {url, type}。服务端按数组区分主图与详情图，type 不参与分桶。"""
    if isinstance(item, str):
        return _normalize_image_url(item)
    if isinstance(item, dict):
        return _normalize_image_url(item.get('url'))
    return ''


def _rich_content_to_text(rich):
    """把 Ozon 富内容 JSON 里的文字抽出来，供 description 使用。"""
    chunks = []

    def walk(node, depth=0):
        if depth > 12 or len(chunks) > 400:
            return
        if isinstance(node, str):
            text = node.strip()
            if text and not text.startswith(('http://', 'https://')):
                chunks.append(text)
        elif isinstance(node, dict):
            for key in ('text', 'title', 'content', 'body', 'value', 'caption'):
                if key in node:
                    walk(node[key], depth + 1)
            for key, value in node.items():
                if key not in ('text', 'title', 'content', 'body', 'value', 'caption') \
                        and isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(rich)
    seen = set()
    lines = []
    for chunk in chunks:
        line = re.sub(r'\s+', ' ', chunk)
        if line in seen or len(line) < 2:
            continue
        seen.add(line)
        lines.append(line)
    return '\n'.join(lines)[:12000]


def _build_product(payload, images, detail_images, image_stats):
    title = _clean_text(payload.get('title'), 800)
    source_url = _clean_text(payload.get('source_url') or payload.get('url'), 1200)
    source_id = _clean_text(payload.get('source_id'), 60) or _extract_ozon_id(source_url)
    attributes = _normalize_attributes(payload.get('attributes'))
    variants = _normalize_variants(payload.get('variants'))
    skus = _normalize_skus(payload.get('skus'))

    description = _clean_text(payload.get('description'), 30000)
    rich_raw = payload.get('rich_content')
    # rich_content 可选：仅用于在 description 为空时抽取文案纯文本。
    # 按用户裁定，富内容原文不进 attributes、不落 ERP，由插件侧存独立快照。
    if not description and rich_raw:
        description = _rich_content_to_text(rich_raw)
    if not description:
        description = _clean_text(payload.get('description_text'), 30000)

    price = _parse_price(payload.get('price'))
    old_price = _parse_price(payload.get('old_price'))

    note_bits = [f'Ozon插件采集 {_now_text()}']
    if payload.get('brand'):
        note_bits.append(f"品牌 {payload['brand']}")
    if image_stats.get('failed'):
        note_bits.append(f"图片失败 {len(image_stats['failed'])} 张")
    if payload.get('collector_version'):
        note_bits.append(f"插件 v{payload['collector_version']}")

    product = {
        'title': title,
        'platform': 'ozon',
        'originalUrl': source_url,
        'sourceLink': source_url,
        'sourceId': source_id,
        'sku': source_id,
        'description': description,
        'category': _clean_text(payload.get('category_path') or payload.get('category'), 800),
        'images': images,
        'detailImages': detail_images,
        'attributes': attributes,
        'variants': variants,
        'skus': skus,
        'skuList': [{'name': (list(s['combo'].values())[0] if s['combo'] else ''),
                     'skuId': source_id} for s in skus][:50],
        'note': '，'.join(note_bits),
        'status': 'unpublished',
        'contentType': 'plain_description' if description else 'none',
        'richContent': '',
        'ozonPrice': price,
        'ozonOldPrice': old_price,
        'ozonCurrency': _clean_text(payload.get('currency') or 'RUB', 10),
        'ozonBrand': _clean_text(payload.get('brand'), 200),
        'ozonRating': payload.get('rating'),
        'ozonReviews': payload.get('reviews'),
        'ozonSeller': _clean_text(payload.get('seller'), 200),
        'sourceImages': {
            'main': list(dict.fromkeys(u for u in (_image_item_url(i) for i in (payload.get('images') or [])) if u))[:MAX_MAIN_IMAGES],
            'detail': list(dict.fromkeys(u for u in (_image_item_url(i) for i in (payload.get('detail_images') or [])) if u))[:MAX_DETAIL_IMAGES],
        },
        'collectedAt': _now_iso(),
    }
    return product


def _merge_into_existing(existing, incoming):
    """同链接幂等：只补空字段，不覆盖人工编辑内容。"""
    changed = {}
    for key, value in incoming.items():
        if key in ('id', 'createdAt', 'status'):
            continue
        current = existing.get(key)
        if current in (None, '', [], {}):
            if value not in (None, '', [], {}):
                changed[key] = value
        elif key == 'attributes' and isinstance(current, list) and isinstance(value, list):
            names = {str(a.get('name')) for a in current if isinstance(a, dict)}
            extra = [a for a in value if isinstance(a, dict) and str(a.get('name')) not in names]
            if extra:
                changed['attributes'] = current + extra
    if changed:
        changed['updatedAt'] = _now_iso()
        Product.update(existing['id'], changed)
    return changed


# ---------------------------------------------------------------- 路由

@ozon_collect_bp.get('/api/ext/ozon/ping')
def ozon_ping():
    """插件连接自检：不需要 key，仅回服务标识。"""
    return _ok({
        'service': 'ozon-collect',
        'version': SERVICE_VERSION,
        'server_time': _now_iso(),
        'max_images': MAX_IMAGES_TOTAL,
    })


@ozon_collect_bp.post('/api/ext/ozon/collect')
def ozon_collect():
    """接收插件采集的 Ozon 对标商品，落 ERP 采集箱。"""
    if not _authorized():
        return _json_error('未授权：插件 API Key 缺失或无效', 401)

    payload = request.get_json(silent=True) or {}
    title = _clean_text(payload.get('title'), 800)
    source_url = _clean_text(payload.get('source_url') or payload.get('url'), 1200)
    if not title:
        return _json_error('商品标题为空，页面上没有取到俄语标题')
    if not source_url or 'ozon' not in source_url:
        return _json_error('来源链接无效，请确认当前页是 Ozon 商品页')

    # 1. 图片清单（主图优先，控制总量）
    raw_main = payload.get('images') if isinstance(payload.get('images'), list) else []
    raw_detail = payload.get('detail_images') if isinstance(payload.get('detail_images'), list) else []
    main_items = [i for i in raw_main if (_normalize_image_url(i.get('url') if isinstance(i, dict) else i))][:MAX_MAIN_IMAGES]
    detail_items = [i for i in raw_detail if (_normalize_image_url(i.get('url') if isinstance(i, dict) else i))][:MAX_DETAIL_IMAGES]
    skip_images = bool(payload.get('skip_images'))

    images, detail_images = [], []
    failed, skipped = [], []
    # 已有记录且已经托管过图片时，重复采集不再下载图片（除非显式 refresh_images）
    existing = Product.find_by_original_url(source_url)
    reused_images = bool(existing and (existing.get('images') or existing.get('detailImages'))
                         and not payload.get('refresh_images'))
    if reused_images:
        images = list(existing.get('images') or [])
        detail_images = list(existing.get('detailImages') or [])
    elif not skip_images:
        main_items = main_items[:MAX_IMAGES_TOTAL]
        detail_items = detail_items[:max(0, MAX_IMAGES_TOTAL - len(main_items))]
        main_hosted, main_failed, main_skipped = _collect_images(main_items, title, '主图')
        detail_hosted, detail_failed, detail_skipped = _collect_images(detail_items, title, '详情图')
        images, detail_images = main_hosted, detail_hosted
        failed = main_failed + detail_failed
        skipped = main_skipped + detail_skipped

    image_stats = {
        'main_saved': len(images),
        'detail_saved': len(detail_images),
        'reused': reused_images,
        'failed': failed[:20],
        'skipped': skipped[:20],
    }
    product_data = _build_product(payload, images, detail_images, image_stats)

    # 2. 幂等写入采集箱
    if existing:
        changed = _merge_into_existing(existing, product_data)
        return _ok({
            'productId': existing['id'],
            'created': False,
            'merged': sorted(changed.keys()),
            'title': existing.get('title') or title,
            'images': image_stats,
            'attributeCount': len(product_data['attributes']),
            'collectUrl': '/#/collect',
        }, msg='该链接已在采集箱，已补充空字段')

    created = Product.create(product_data)
    return _ok({
        'productId': created['id'],
        'created': True,
        'title': created.get('title'),
        'images': image_stats,
        'attributeCount': len(product_data['attributes']),
        'collectUrl': '/#/collect',
    }, msg='已进入 ERP 采集箱')
