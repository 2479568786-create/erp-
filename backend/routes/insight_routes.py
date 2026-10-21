"""
AI 询盘助手 API 路由（从老 ERP workbench /api/ai-insights 迁移改造）

数据源（按涂总确认的范围）：
- orders 表         —— 逐笔发货单（FBS/FBO，含金额、状态、商品明细、店铺）
- stores 表         —— 多店铺
- Product JSON      —— 采集箱商品（类目、标题、货源链接）
- PublishRecord     —— 上架记录（已发布商品与价格）

不包含：Ozon 商品分析快照（曝光/加购/转化）与财务利润模块。

接口:
- GET /api/ai-insights?days=30&storeId=   经营诊断（确定性指标 + AI 建议增强）
"""
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from flask import Blueprint, request
from db import query
from models.product import Product, PublishRecord
from utils.response import success_response, error_response, handle_errors

insight_bp = Blueprint('insight', __name__)

# 视为成交完成的订单状态（金额计入销售额）
PAID_STATUSES = {
    'awaiting_deliver', 'delivering', 'delivered', 'driver_pickup',
    'awaiting_packaging', 'arbitration', 'client_arbitration',
}
# 视为取消的订单状态
CANCEL_STATUSES = {'cancelled', 'cancelled_by_seller', 'cancelled_by_client', 'not_accepted'}


def _order_products(posting):
    """解析订单商品明细 JSON，返回列表"""
    try:
        items = json.loads(posting.get('products_json') or '[]')
        return items if isinstance(items, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _blank(label):
    return {
        'label': label, 'orders': 0, 'units': 0, 'sales': 0.0,
        'cancellations': 0,
    }


def _publish_index():
    """按 offer_id / ozonProductId 建上架记录索引 {key: record}"""
    idx = {}
    for r in PublishRecord.find_all():
        if r.get('status') in ('published', 'published_with_errors'):
            for key in (r.get('sourceId'), str(r.get('ozonProductId') or '')):
                if key:
                    idx.setdefault(str(key), r)
    return idx


def _product_index():
    """采集箱商品按 URL/SKU 建索引，用于补类目信息"""
    idx = {}
    for p in Product.find_all():
        for key in (p.get('sourceId'), p.get('sku'), p.get('productId')):
            if key:
                idx.setdefault(str(key), p)
    return idx


@insight_bp.route('/ai-insights', methods=['GET'])
@handle_errors
def ai_insights():
    """AI 询盘助手：用可追溯的订单、店铺、商品和上架记录生成经营诊断。"""
    days = min(max(int(request.args.get('days', 30)), 7), 365)
    store_id = request.args.get('storeId', '').strip()

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    conditions = ["COALESCE(in_process_at, created_at) >= ?"]
    params = [since]
    if store_id:
        conditions.append("store_id = ?")
        params.append(store_id)
    postings = query(
        f"SELECT * FROM orders WHERE {' AND '.join(conditions)}", tuple(params))

    if not postings:
        return success_response(data={
            'period_days': days, 'source': 'orders', 'data_complete': False,
            'summary': {'orders': 0, 'units': 0, 'sales': 0, 'stores': 0},
            'stores': [], 'products': [], 'categories': [],
            'recommendations': [{
                'priority': '高', 'title': '先同步订单',
                'body': '当前周期没有可分析的订单数据。请先到订单管理页点击"同步订单"，'
                        '或检查店铺 API 凭证是否配置正确。',
                'evidence': '订单数 0',
            }],
            'ai_used': False,
            'data_note': '数据源：orders 表（FBS/FBO 发货单）。',
        })

    store_names = {r['store_id']: (r['alias'] or r['store_id'])
                   for r in query("SELECT store_id, alias FROM stores")}
    pub_idx = _publish_index()
    prod_idx = _product_index()

    overall = _blank('全部店铺')
    product_rows, category_rows, store_rows = {}, {}, {}

    for posting in postings:
        is_paid = posting['status'] in PAID_STATUSES
        is_cancel = posting['status'] in CANCEL_STATUSES
        store_label = store_names.get(posting['store_id'], posting['store_id'])
        items = _order_products(posting)
        units = sum(int(it.get('quantity') or 1) for it in items) or 1
        amount = float(posting.get('price_amount') or 0)

        store_row = store_rows.setdefault(store_label, _blank(store_label))
        for row in (overall, store_row):
            row['orders'] += 1
            row['units'] += units
            row['sales'] += amount if is_paid else 0
            row['cancellations'] += 1 if is_cancel else 0

        for it in items:
            sku = str(it.get('sku') or it.get('offer_id') or '')
            name = (it.get('name') or it.get('offer_id') or f"SKU {sku}")[:60]
            p_row = product_rows.setdefault(name, _blank(name))
            p_row['orders'] += 1
            p_row['units'] += int(it.get('quantity') or 1)
            p_row['sales'] += amount if is_paid else 0
            p_row['cancellations'] += 1 if is_cancel else 0
            # 类目：优先采集箱商品，其次上架记录关联
            src = prod_idx.get(sku)
            category_label = (src.get('category') if src else None) or '未匹配类目'
            c_row = category_rows.setdefault(category_label, _blank(category_label))
            c_row['orders'] += 1
            c_row['units'] += int(it.get('quantity') or 1)
            c_row['sales'] += amount if is_paid else 0

    product_list = sorted(product_rows.values(),
                          key=lambda r: (r['sales'], r['units']), reverse=True)
    category_list = sorted(category_rows.values(),
                           key=lambda r: r['sales'], reverse=True)
    store_list = sorted(store_rows.values(), key=lambda r: r['sales'], reverse=True)

    # 上架记录概况
    pub_stats = PublishRecord.get_stats()
    published_count = pub_stats.get('published', 0) + pub_stats.get('published_with_errors', 0)
    failed_count = pub_stats.get('failed', 0)
    collected_count = len(Product.find_all())

    summary = {
        'orders': overall['orders'], 'units': overall['units'],
        'sales': round(overall['sales'], 2),
        'cancellations': overall['cancellations'],
        'cancel_rate': round(overall['cancellations'] / overall['orders'] * 100, 1)
        if overall['orders'] else 0,
        'stores': len(store_rows),
        'published_products': published_count,
        'failed_publish': failed_count,
        'collected_products': collected_count,
        'data_complete': True,
    }

    # 确定性建议（老 ERP 规则移植，适配新数据源）
    recommendations = []
    if product_list and product_list[0]['sales'] > 0:
        recommendations.append({
            'priority': '高', 'title': '把预算和库存优先给头部商品',
            'body': f"{product_list[0]['label']} 当前销售额最高，建议优先补货、优化主图和广告词，并建立库存预警。",
            'evidence': f"销售额 ₽{product_list[0]['sales']:,.0f} · {product_list[0]['units']} 件"})
    high_cancel = [r for r in product_list
                   if r['orders'] >= 3 and r['cancellations'] / r['orders'] >= 0.2]
    if high_cancel:
        recommendations.append({
            'priority': '高', 'title': '排查高取消率商品',
            'body': f"重点检查 {high_cancel[0]['label']} 的库存同步、发货时效与商品描述一致性，"
                    f"必要时下调广告预算。",
            'evidence': f"取消 {high_cancel[0]['cancellations']}/{high_cancel[0]['orders']} 单"})
    if category_list:
        best = category_list[0]
        recommendations.append({
            'priority': '中', 'title': '围绕表现突出的类目做相邻扩品',
            'body': f"建议在「{best['label']}」内扩充相近价格带和互补配件，同时保留小批量测试，"
                    f"避免单一类目依赖。",
            'evidence': f"类目销售额 ₽{best['sales']:,.0f} · {best['units']} 件"})
    if failed_count > 0:
        recommendations.append({
            'priority': '中', 'title': '处理上架失败的商品',
            'body': f"当前有 {failed_count} 条上架失败记录，建议到上架记录页查看失败原因"
                    f"（常见：类目属性缺失、图片不合规、令牌限流），修复后重新提交。",
            'evidence': f"失败 {failed_count} 条 · 已成功 {published_count} 条"})
    if collected_count > 0 and published_count == 0:
        recommendations.append({
            'priority': '高', 'title': '采集箱商品尚未转化为上架',
            'body': f"采集箱已有 {collected_count} 个商品但尚无成功上架记录，"
                    f"建议先完善定价与 AI 文案，走上架链路发布测款。",
            'evidence': f"采集 {collected_count} · 上架成功 0"})

    recommendations, ai_used = _enhance_with_ai(summary, product_list[:20],
                                                category_list[:12], recommendations)

    return success_response(data={
        'period_days': days, 'source': 'orders', 'data_complete': True,
        'summary': summary,
        'stores': store_list,
        'products': product_list[:20],
        'categories': category_list[:12],
        'recommendations': recommendations,
        'ai_used': ai_used,
        'data_note': '数据源：orders 逐笔发货单 + 采集箱商品 + 上架记录；'
                     '不含曝光/加购等搜索分析与财务利润数据。',
    })


def _enhance_with_ai(summary, products, categories, fallback):
    """用已配置的大模型对建议做措辞与优先级增强；失败时返回确定性兜底。"""
    try:
        from services.ai_config_service import get_ai_config
        config = get_ai_config()
        if not config.get('api_key'):
            return fallback, False
        base_url = (config.get('base_url') or '').rstrip('/')
        model = config.get('model', '')
        if not base_url or not model:
            return fallback, False

        prompt = json.dumps({'summary': summary, 'products': products,
                             'categories': categories}, ensure_ascii=False)
        body = json.dumps({
            'model': model,
            'messages': [
                {'role': 'system', 'content':
                 '你是跨境电商经营分析师。只输出 JSON，格式为 '
                 '{"recommendations":[{"priority":"高/中/低","title":"短标题",'
                 '"body":"可执行建议","evidence":"引用输入中的数字"}]}。'
                 '不得编造输入中没有的数字；最多输出 5 条。'},
                {'role': 'user', 'content': prompt},
            ],
            'response_format': {'type': 'json_object'},
            'temperature': 0.2,
            'max_tokens': 1500,
        }).encode('utf-8')
        req = urllib.request.Request(f'{base_url}/chat/completions', data=body)
        req.add_header('Content-Type', 'application/json')
        req.add_header('Authorization', f"Bearer {config['api_key']}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
        rows = json.loads(content).get('recommendations', [])
        valid = [{k: str(item[k]) for k in ('priority', 'title', 'body', 'evidence')}
                 for item in rows
                 if all(item.get(k) for k in ('priority', 'title', 'body', 'evidence'))]
        return (valid[:5] or fallback), bool(valid)
    except Exception:
        return fallback, False
