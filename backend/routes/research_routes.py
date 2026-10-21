"""
选品池 API 路由（从老 ERP workbench product-research 迁移）

接口:
- GET    /api/product-research                列表（kind/status/keyword 筛选）
- GET    /api/product-research/stats          状态统计
- POST   /api/product-research                新增
- PUT    /api/product-research/<id>           更新
- DELETE /api/product-research/<id>           删除
- POST   /api/product-research/<id>/copy      复制一条
- POST   /api/product-research/extract-mpstats 从 MPStats 截图提取量化指标（视觉模型）
"""
import json
import urllib.request
import urllib.error

from flask import Blueprint, request
from models.research import ResearchItem
from utils.response import success_response, error_response, handle_errors
from utils.auth import require_login, require_admin

research_bp = Blueprint('research', __name__)


def _row_out(row):
    d = dict(row)
    return d


@research_bp.route('/product-research', methods=['GET'])
@handle_errors
@require_login
def list_research():
    kind = request.args.get('kind', '').strip()
    status = request.args.get('status', '').strip()
    keyword = request.args.get('keyword', '').strip()
    rows = ResearchItem.find_all(kind=kind or None, status=status or None,
                                 keyword=keyword or None)
    return success_response(data=[_row_out(r) for r in rows])


@research_bp.route('/product-research/stats', methods=['GET'])
@handle_errors
@require_login
def research_stats():
    counts = ResearchItem.status_counts()
    return success_response(data={'counts': counts, 'total': sum(counts.values())})


@research_bp.route('/product-research', methods=['POST'])
@handle_errors
@require_admin
def create_research():
    data = request.get_json(silent=True) or {}
    if not (data.get('product_name') or '').strip():
        return error_response('商品名称不能为空')
    item_id = ResearchItem.create(data)
    row = ResearchItem.find_by_id(item_id)
    return success_response(data=_row_out(row), msg='已加入选品池')


@research_bp.route('/product-research/<int:item_id>', methods=['PUT'])
@handle_errors
@require_admin
def update_research(item_id):
    data = request.get_json(silent=True) or {}
    if not ResearchItem.update(item_id, data):
        return error_response('条目不存在或无更新字段', code=404)
    row = ResearchItem.find_by_id(item_id)
    return success_response(data=_row_out(row), msg='已更新')


@research_bp.route('/product-research/<int:item_id>', methods=['DELETE'])
@handle_errors
@require_admin
def delete_research(item_id):
    if not ResearchItem.find_by_id(item_id):
        return error_response('条目不存在', code=404)
    ResearchItem.delete(item_id)
    return success_response(msg='已删除')


@research_bp.route('/product-research/<int:item_id>/copy', methods=['POST'])
@handle_errors
@require_admin
def copy_research(item_id):
    src = ResearchItem.find_by_id(item_id)
    if not src:
        return error_response('条目不存在', code=404)
    data = dict(src)
    data.pop('id', None)
    data['product_name'] = (src['product_name'] or '') + '（副本）'
    new_id = ResearchItem.create(data)
    row = ResearchItem.find_by_id(new_id)
    return success_response(data=_row_out(row), msg='已复制')


@research_bp.route('/product-research/extract-mpstats', methods=['POST'])
@handle_errors
@require_admin
def extract_mpstats():
    """从 MPStats 市场分析截图提取量化指标（复用新 ERP 的 AI 配置）。

    body: { image: 'data:image/...;base64,...' }
    返回: monthly_revenue / revenue_growth_rate / top3_brand_share /
          seller_count / brand_count / detected
    """
    payload = request.get_json(silent=True) or {}
    image_data_url = payload.get('image', '')
    if not image_data_url.startswith('data:image'):
        return error_response('请提供 data:image 格式的截图 base64')

    from services.ai_config_service import get_ai_config
    config = get_ai_config()
    if not config.get('api_key'):
        return error_response('AI API Key 未配置，请先到模型管理页配置')
    base_url = (config.get('base_url') or '').rstrip('/')
    model = config.get('vision_model') or config.get('model', '')
    if not base_url or not model:
        return error_response('AI base_url 或 model 未配置')

    prompt = (
        "这是一张 MPStats 市场分析截图（Ozon/Wildberries 类目或子类目数据）。"
        "请从中提取以下指标并输出 JSON：\n"
        "- monthly_revenue: 月销售额（卢布，去掉千分位分隔符，纯数字）\n"
        "- revenue_growth_rate: 销售额增速/增长率（%，纯数字）\n"
        "- top3_brand_share: Top3 品牌 GMV 占比（%，纯数字）\n"
        "- seller_count: 卖家数量（纯数字）\n"
        "- brand_count: 品牌数量（纯数字）\n"
        "规则：只提取截图里明确显示的数字；没有的字段用 null。"
        "输出严格 JSON，不要额外文字。"
    )
    req_body = json.dumps({
        'model': model,
        'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': image_data_url}},
            {'type': 'text', 'text': prompt},
        ]}],
        'response_format': {'type': 'json_object'},
        'temperature': 0.1,
    }).encode('utf-8')

    req = urllib.request.Request(f'{base_url}/chat/completions', data=req_body)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f"Bearer {config['api_key']}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
        raw = json.loads(content)
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode('utf-8', errors='replace')[:200]
        except Exception:
            pass
        return error_response(f'视觉模型调用失败 HTTP {e.code}: {body or e.reason}')
    except Exception as e:
        return error_response(f'截图识别失败: {e}')

    def clean(key):
        value = raw.get(key)
        if value is None or value == '':
            return None
        try:
            return float(str(value).replace(',', '').replace(' ', ''))
        except (TypeError, ValueError):
            return None

    extracted = {
        'monthly_revenue': clean('monthly_revenue'),
        'revenue_growth_rate': clean('revenue_growth_rate'),
        'top3_brand_share': clean('top3_brand_share'),
        'seller_count': clean('seller_count'),
        'brand_count': clean('brand_count'),
        'detected': any(raw.get(k) not in (None, '') for k in (
            'monthly_revenue', 'revenue_growth_rate', 'top3_brand_share',
            'seller_count', 'brand_count')),
    }
    return success_response(data=extracted, msg='识别完成' if extracted['detected'] else '截图中未识别到量化指标')
