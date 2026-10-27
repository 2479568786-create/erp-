"""JLC 自动上架流水线编排（ERP 原生入口，替代 iframe sidecar）。

方案 B（服务化复用）+ C（渐进原生化）的关键站点：
- ERP 是唯一前端与数据入口；
- JLC（/opt/jlc-research，:8765）保留为内网 worker，ERP 以服务端身份直连（loopback，永不暴露给浏览器）；
- 本路由把「采集导入 → 单个商品走 15 步流水线 → 批次状态/结果」串成一条可观察、
  可恢复的自动上架流，供 ERP 前端 /jlc-workspace 原生页调用。

复用 jlc_bridge_routes.build_collector_payload 完成 ERP 商品 → JLC 采集载荷的翻译，
避免双份维护；这里只负责编排与直连代理。
"""
import json
import urllib.error
import urllib.request

from flask import Blueprint, request

from routes.jlc_bridge_routes import build_collector_payload
from models.product import Product
from utils.auth import require_login
from utils.response import error_response, success_response

jlc_pipeline_bp = Blueprint('jlc_pipeline', __name__)

JLC_BASE = 'http://127.0.0.1:8765'
JLC_TIMEOUT = 30


def _jlc_request(method, path, payload=None, timeout=JLC_TIMEOUT):
    """ERP 服务端直连 JLC 内网 worker（loopback，JLC 的 local_network_only 恒放行）。"""
    url = JLC_BASE + path
    data = None
    headers = {'Accept': 'application/json'}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', 'replace')
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(body)
        except Exception:
            detail = body[:800]
        return exc.code, detail
    except urllib.error.URLError as exc:
        return 502, {'message': f'JLC 内部服务不可达：{exc}'}


def _jlc_error_message(detail):
    if isinstance(detail, dict):
        for key in ('message', 'msg', 'detail', 'error'):
            value = detail.get(key)
            if value:
                if isinstance(value, dict):
                    return str(value.get('message') or value)[:300]
                return str(value)[:300]
        return json.dumps(detail, ensure_ascii=False)[:300]
    return str(detail)[:300]


@jlc_pipeline_bp.get('/api/jlc-pipeline/products-importable')
@require_login
def jlc_pipeline_importable_products():
    """可导入 JLC 的 ERP 1688 商品列表。

    独立实现、只读，不触碰 ERP 既有商品/发布链路。仅读取 Product 集合并
    生成只读视图，用于 JLC 流水线（全新旁路功能）。
    """
    import json as _json
    keyword = (request.args.get('keyword') or '').strip()
    items = Product.find_all()
    rows = []
    for item in items:
        platform = str(item.get('platform') or '').lower()
        if platform not in ('1688', 'alibaba', 'alibaba1688'):
            continue
        blob = _json.dumps(item, ensure_ascii=False)
        if keyword and keyword.lower() not in blob.lower():
            continue
        rows.append({
            'id': item.get('id'),
            'title': item.get('title') or '',
            'platform': item.get('platform'),
            'originalUrl': item.get('originalUrl') or item.get('sourceLink') or '',
            'sourceName': item.get('sourceName') or '',
            'sourcePrice': item.get('sourcePrice'),
            'images': (item.get('images') or [])[:3],
            'skuCount': len(item.get('skus') or item.get('skuList') or []),
            'categoryPath': item.get('categoryPath') or item.get('category') or '',
            'jlcProductId': item.get('jlcProductId'),
            'jlcImportStatus': item.get('jlcImportStatus'),
            'updatedAt': item.get('updatedAt'),
        })
    rows.sort(key=lambda row: row.get('updatedAt') or '', reverse=True)
    return success_response(data=rows)


@jlc_pipeline_bp.get('/api/jlc-pipeline/health')
@require_login
def jlc_pipeline_health():
    status, data = _jlc_request('GET', '/health', timeout=5)
    return success_response(data={
        'reachable': status == 200,
        'service': (data or {}).get('service'),
        'root': (data or {}).get('root'),
        'status_code': status,
    })


@jlc_pipeline_bp.get('/api/jlc-pipeline/categories/rules')
@require_login
def jlc_categories_rules():
    """透传 JLC 类目规则（ERP 前端选类目用）。"""
    status, data = _jlc_request(
        'POST', '/api/collector/categories/rules',
        {'category_id': request.args.get('categoryId'), 'type_id': request.args.get('typeId'),
         'allow_readonly_fetch': True},
    )
    if status != 200:
        return error_response(f'JLC 类目规则获取失败：{_jlc_error_message(data)}', 502)
    return success_response(data=data)


@jlc_pipeline_bp.post('/api/jlc-pipeline/import')
@require_login
def jlc_pipeline_import():
    """ERP 采集箱 1688 商品 → JLC 采集收件箱（复用 bridge 翻译），返回 jlcProductId。"""
    body = request.get_json(silent=True) or {}
    product_id = str(body.get('productId') or '')
    category_id = body.get('categoryId')
    type_id = body.get('typeId')
    sku_indexes = body.get('skuIndexes')
    if not product_id:
        return error_response('缺少 productId')
    if not category_id or not type_id:
        return error_response('缺少 categoryId / typeId')
    product = Product.find_by_id(product_id)
    if not product:
        return error_response('商品不存在', 404)
    platform = str(product.get('platform') or '').lower()
    if platform not in ('1688', 'alibaba', 'alibaba1688'):
        return error_response('仅支持 1688 商品进入 JLC 流水线')

    try:
        payload = build_collector_payload(product, category_id, type_id, sku_indexes)
    except ValueError as exc:
        return error_response(str(exc), 422)
    status, data = _jlc_request('POST', '/api/collector/products', payload)
    if status not in (200, 201):
        return error_response(f'JLC 导入失败：{_jlc_error_message(data)}',
                              status if 400 <= status < 600 else 502)
    jlc_product_id = (data or {}).get('product_id') or (data or {}).get('productId') or ''
    if not jlc_product_id:
        return error_response('JLC 返回异常：缺少 product_id', 502)
    Product.update(product_id, {
        'jlcProductId': jlc_product_id,
        'jlcImportStatus': (data or {}).get('status') or 'COLLECTED',
        'jlcImportedAt': _now_iso(),
        'jlcCategory': {'categoryId': str(category_id), 'typeId': str(type_id)},
    })
    return success_response(data={
        'productId': product_id,
        'jlcProductId': jlc_product_id,
        'status': (data or {}).get('status'),
        'warnings': (data or {}).get('warnings') or [],
    })


@jlc_pipeline_bp.post('/api/jlc-pipeline/run')
@require_login
def jlc_pipeline_run():
    """对单个 JLC 商品发起 15 步自动上架流（JLC 内部自动建批、后台调度、断点恢复）。

    body: { jlcProductId: 'P...', storeIds?: [...], autoUpload?: bool }
    JLC 的 /run 是「唯一授权点」：点运行后无人值守跑完生成管线，直到按 autoUpload
    决定是否自动提交 Ozon。
    """
    body = request.get_json(silent=True) or {}
    jlc_product_id = str(body.get('jlcProductId') or '')
    if not jlc_product_id:
        return error_response('缺少 jlcProductId')
    run_payload = {}
    store_ids = body.get('storeIds')
    if store_ids is not None:
        if not isinstance(store_ids, list):
            return error_response('storeIds 必须是数组', 422)
        run_payload['store_ids'] = store_ids
    status, data = _jlc_request('POST', f'/api/workbench/products/{jlc_product_id}/run', run_payload)
    if status not in (200, 201):
        return error_response(f'JLC 流水线启动失败：{_jlc_error_message(data)}',
                              status if 400 <= status < 600 else 502)
    return success_response(data=data)


@jlc_pipeline_bp.get('/api/jlc-pipeline/tasks/status')
@require_login
def jlc_pipeline_status():
    """当前批次运行状态（running / current_batch / last_result）。"""
    status, data = _jlc_request('GET', '/api/tasks/status')
    if status != 200:
        return error_response(f'JLC 状态获取失败：{_jlc_error_message(data)}', 502)
    return success_response(data=data)


@jlc_pipeline_bp.get('/api/jlc-pipeline/product')
@require_login
def jlc_pipeline_product():
    """查询单个 JLC 商品的流水线详情（completed/pending steps、进度、失败信息）。"""
    product_id = str(request.args.get('jlcProductId') or '')
    if not product_id:
        return error_response('缺少 jlcProductId')
    status, data = _jlc_request('GET', f'/api/collector/products/{product_id}')
    if status != 200:
        return error_response(f'JLC 商品查询失败：{_jlc_error_message(data)}',
                              status if 400 <= status < 600 else 502)
    return success_response(data=data)


@jlc_pipeline_bp.post('/api/jlc-pipeline/batches/control')
@require_login
def jlc_pipeline_control():
    """透传批次控制（暂停/继续/停止），body 原样转发给 JLC。"""
    body = request.get_json(silent=True) or {}
    status, data = _jlc_request('POST', '/api/workbench/batches/control', body)
    if status not in (200, 201):
        return error_response(f'JLC 批次控制失败：{_jlc_error_message(data)}',
                              status if 400 <= status < 600 else 502)
    return success_response(data=data)


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
