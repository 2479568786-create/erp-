"""
选品池 API 路由（从老 ERP workbench product-research 迁移）

接口:
- GET    /api/product-research                列表（keyword 筛选）
- POST   /api/product-research                新增
- PUT    /api/product-research/<id>           更新
- DELETE /api/product-research/<id>           删除
- POST   /api/product-research/<id>/copy      复制一条
"""
import json

from flask import Blueprint, request
from models.research import ResearchItem
from utils.response import success_response, error_response, handle_errors
from utils.auth import require_login, require_admin, get_current_user

research_bp = Blueprint('research', __name__)


def _row_out(row):
    d = dict(row)
    for key in ('monthly_revenue', 'revenue_growth_rate', 'top3_brand_share',
                'seller_count', 'brand_count', 'blue_ocean_score',
                'mpstats_source', 'status'):
        d.pop(key, None)
    try:
        d['owner_ids'] = json.loads(d.get('owner_ids') or '[]')
    except (TypeError, ValueError):
        d['owner_ids'] = []
    return d


@research_bp.route('/product-research', methods=['GET'])
@handle_errors
@require_login
def list_research():
    kind = request.args.get('kind', '').strip()
    keyword = request.args.get('keyword', '').strip()
    user = get_current_user()
    rows = ResearchItem.find_all(
        kind=kind or None,
        keyword=keyword or None,
        owner_id=None if user.get('role') == 'admin' else user.get('id'),
        owner_name=None if user.get('role') == 'admin' else (user.get('nickname') or user.get('username')),
    )
    return success_response(data=[_row_out(r) for r in rows])


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
    data.update({
        'source_type': 'manual',
        'source_id': '',
        'platform': 'ozon',
        'source_payload_json': '{}',
        'source_synced_at': None,
    })
    new_id = ResearchItem.create(data)
    row = ResearchItem.find_by_id(new_id)
    return success_response(data=_row_out(row), msg='已复制')


