"""
老 ERP Chrome 采集插件兼容层
让「OZON ERP 采集插件」（老精铺助手插件壳子改造）直连新 ERP。

⚠️ 响应格式约定：老插件按 FastAPI 风格直接解析原始 JSON
（background.js: `data.image_filter` / `data.detail`；popup.js: `data.stores` /
`data.operator_name`；context: `{store_id, operator_member_id, collection_batch_id}` 顶层字段），
因此本模块所有端点返回原始 JSON 而**不是**新 ERP 的 {code,msg,data} 包装，
错误返回 {"detail": "..."} + 4xx 状态码。

插件调用链（extension/background.js + content.js）:
- GET  /api/config                    运行时配置（在线状态、图片过滤参数）
- GET  /api/auth/me                   登录状态（本地单用户，返回默认操作员）
- GET  /api/assignment/stores         采集归属店铺列表 {operator_name, stores:[{id,name}]}
- POST /api/collection-contexts       确认采集归属上下文
- POST /api/collect/from-extension    提交采集数据（字段映射到新 ERP 采集箱）
- POST /api/collect/images            采集商品图片（服务端下载入库到图库）
"""
import json
import re
import urllib.request
from datetime import datetime

from flask import Blueprint, jsonify, request
from db import query
from models.product import Product

compat_bp = Blueprint('compat', __name__)

_DEFAULT_OPERATOR = {'id': 1, 'name': '涂总'}


def _detail(msg, code=400):
    return jsonify({'detail': msg}), code


@compat_bp.route('/config', methods=['GET'])
def extension_config():
    """插件运行时配置。字段结构与老 ERP workbench /api/config 对齐。"""
    return jsonify({
        'service': 'ozon-erp',
        'online': True,
        'logged_in': True,
        'demo_mode': False,
        'port': 5000,
        'extension_min_version': '1.3.0',
        'extension_recommended_version': '1.3.0',
        'image_filter': {'min_width': 600, 'min_height': 600, 'max_candidates': 5},
        'message': '已连接 OZON ERP',
    })


@compat_bp.route('/auth/me', methods=['GET'])
def auth_me():
    """插件登录状态查询（background 读取 me.name）。"""
    return jsonify({
        'name': _DEFAULT_OPERATOR['name'],
        'role': 'ADMIN',
        'member_id': _DEFAULT_OPERATOR['id'],
    })


@compat_bp.route('/assignment/stores', methods=['GET'])
def assignment_stores():
    """采集归属店铺列表（popup 解析 data.operator_name / data.stores）。"""
    rows = query(
        "SELECT id, alias, store_id FROM stores WHERE auth_status != 'disabled' "
        "ORDER BY id")
    return jsonify({
        'operator_name': _DEFAULT_OPERATOR['name'],
        'operator_member_id': _DEFAULT_OPERATOR['id'],
        'stores': [{'id': r['id'],
                    'name': r['alias'] or f"店铺 {r['store_id']}"} for r in rows],
    })


@compat_bp.route('/collection-contexts', methods=['POST'])
def create_collection_context():
    """确认采集归属上下文（background `...data` 直接作为 context 存储）。"""
    body = request.get_json(silent=True) or {}
    store_local_id = body.get('store_id')
    store = None
    if store_local_id is not None:
        store = query("SELECT id, alias, store_id FROM stores WHERE id = ?",
                      (store_local_id,), one=True)
    if not store:
        store = query("SELECT id, alias, store_id FROM stores "
                      "WHERE auth_status != 'disabled' ORDER BY id LIMIT 1", one=True)
    if not store:
        return _detail('还没有可用店铺，请先在店铺管理中添加', 404)
    batch_id = 'BATCH-' + datetime.now().strftime('%Y%m%d%H%M%S')
    return jsonify({
        'store_id': store['id'],
        'store_name': store['alias'] or f"店铺 {store['store_id']}",
        'operator_member_id': _DEFAULT_OPERATOR['id'],
        'operator_name': _DEFAULT_OPERATOR['name'],
        'collection_batch_id': batch_id,
    })


def _platform_name(source_platform):
    return {'1688': '1688', 'TAOBAO': 'taobao', 'OZON': 'ozon'}.get(
        (source_platform or '').upper(), (source_platform or '1688').lower())


def _extract_source_id(source_url):
    """从 1688/淘宝 URL 中提取商品数字 ID"""
    m = re.search(r'(\d{8,})', source_url or '')
    return m.group(1) if m else ''


def _map_collection_to_product(payload):
    """老 ERP CollectionIn → 新 ERP Product 字段映射"""
    platform = _platform_name(payload.get('source_platform'))
    source_url = payload.get('source_url') or ''
    source_id = payload.get('source_product_id') or _extract_source_id(source_url)

    attributes = payload.get('attributes') or {}
    attr_list = [{'name': k, 'value': str(v)} for k, v in attributes.items()
                 if isinstance(v, (str, int, float))]

    # selected_skus → variants + skus
    selected_skus = payload.get('selected_skus') or []
    spec_map = {}
    skus = []
    for s in selected_skus:
        spec_name = s.get('spec_name') or '规格'
        spec_value = s.get('spec_value') or ''
        spec_map.setdefault(spec_name, []).append(spec_value)
        skus.append({
            'combo': {spec_name: spec_value},
            'price': None,
            'sourcePrice': s.get('purchase_price') or None,
            'stock': None,
            'image': s.get('image') or '',
            'netWeight': s.get('net_weight') or '',
            'packageWeight': s.get('package_weight') or '',
        })
    variants = [{'name': name, 'options': list(dict.fromkeys(opts))}
                for name, opts in spec_map.items()]

    return {
        'title': payload.get('title') or '',
        'platform': platform,
        'originalUrl': source_url,
        'sourceLink': source_url,
        'sourceId': source_id,
        'sku': source_id,
        'description': payload.get('raw_description') or '',
        'category': payload.get('category_path') or '',
        'images': payload.get('main_images') or [],
        'detailImages': payload.get('detail_images') or [],
        'attributes': attr_list,
        'variants': variants,
        'skus': skus,
        'skuList': [{'name': s.get('spec_value', ''), 'skuId': source_id}
                    for s in selected_skus[:50]],
        'note': f"老插件采集 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                f"（批次 {payload.get('collection_batch_id') or '-'}，"
                f"采集人 {payload.get('collected_by') or _DEFAULT_OPERATOR['name']}）",
        'status': 'unpublished',
    }


@compat_bp.route('/collect/from-extension', methods=['POST'])
def collect_from_extension():
    """接收老插件采集数据，映射后写入新 ERP 采集箱（Product JSON 存储）。

    幂等：同 originalUrl 已存在时仅填充空字段，不覆盖用户已编辑内容。
    """
    payload = request.get_json(silent=True) or {}
    if not payload.get('title'):
        return _detail('商品标题不能为空')

    product_data = _map_collection_to_product(payload)

    existing = Product.find_by_original_url(product_data['originalUrl'])
    if existing:
        changed = {}
        for k, v in product_data.items():
            if k in ('id', 'createdAt', 'status'):
                continue
            cur = existing.get(k)
            empty = cur in (None, '', [], {})
            if empty and v not in (None, '', [], {}):
                changed[k] = v
        if changed:
            Product.update(existing['id'], changed)
        return jsonify({
            'id': existing['id'], 'created': False,
            'title': existing.get('title', ''),
            'record': {'id': existing['id'], 'status': '已存在，合并更新'},
        })

    created = Product.create(product_data)
    return jsonify({
        'id': created['id'], 'created': True, 'title': created.get('title', ''),
        'record': {'id': created['id'], 'status': '待处理'},
    })


# ===== 商品图片采集（插件面板「采图」功能，落库到 ERP 图库） =====

_IMG_MAX_COUNT = 20
_IMG_MAX_BYTES = 8 * 1024 * 1024
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')


@compat_bp.route('/collect/images', methods=['POST'])
def collect_images():
    """服务端下载商品图片并存入 ERP 图库（绕开 1688/淘宝 CDN 防盗链与页面 CSP）。

    body: {
      product_title: str,
      source_url: str,
      images: [{url: str, source: 'main_gallery'|'sku'|'detail'}]  # 最多 20 张
    }
    返回: {saved: n, failed: [{url, error}], assets: [...]}
    """
    from routes.gallery_routes import _save_bytes  # 复用图库落库逻辑

    payload = request.get_json(silent=True) or {}
    images = payload.get('images') or []
    title = (payload.get('product_title') or '未命名商品')[:60]
    if not images:
        return _detail('未选择图片')
    images = images[:_IMG_MAX_COUNT]

    saved, failed = [], []
    for idx, item in enumerate(images):
        url = item.get('url') if isinstance(item, dict) else str(item)
        source = (item.get('source') if isinstance(item, dict) else '') or 'main_gallery'
        if not url or not url.startswith('http'):
            failed.append({'url': str(url)[:120], 'error': 'URL 无效'})
            continue
        try:
            # 1688/淘宝 CDN 校验 Referer：用图片自身域名的根地址伪装同源请求
            host_match = re.match(r'https?://([^/]+)', url)
            referer = f"https://{host_match.group(1)}/" if host_match else ''
            req = urllib.request.Request(url, headers={
                'User-Agent': _UA,
                'Referer': referer,
                'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read(_IMG_MAX_BYTES + 1)
                content_type = resp.headers.get('Content-Type', '')
            if len(data) > _IMG_MAX_BYTES:
                failed.append({'url': url[:120], 'error': '图片超过 8MB'})
                continue
            if 'image' not in content_type.lower() and not data[:4].startswith((b'\x89P', b'\xff\xd8', b'GIF8', b'RIFF')):
                failed.append({'url': url[:120], 'error': f'非图片内容({content_type[:30]})'})
                continue
            ext = 'jpg'
            low = url.lower().split('?')[0]
            for cand in ('png', 'jpeg', 'jpg', 'webp'):
                if low.endswith('.' + cand):
                    ext = cand
                    break
            asset = _save_bytes(
                data,
                original_name=f"{idx + 1:02d}_{source}.{ext}",
                source='extension-collect',
                prompt='',
                tags=[title, '插件采图', source],
            )
            saved.append(asset)
        except Exception as exc:  # noqa: BLE001
            failed.append({'url': url[:120], 'error': str(exc)[:120]})

    return jsonify({
        'saved': len(saved),
        'failed': failed,
        'assets': [{'id': a['id'], 'url': a['url'], 'title': a['title']}
                   for a in saved],
    })
