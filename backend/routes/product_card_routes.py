# -*- coding: utf-8 -*-
"""
商品卡路由（product_card_bp）
「AI 自动生成 + 手动填写」双轨制的接口层，纯增量挂载：
- 现有表/蓝图/流程零改动，统一 {code,msg,data} 包装
- AI 轨：POST /generate（或 /regenerate）-> 落库新版本 status=GENERATED
- 手动轨：POST /manual（新建 EDITED）/ PUT /{id}（编辑置 EDITED）/ POST /{id}/activate（采纳）
- 取值：GET /current -> latest EDITED -> latest GENERATED -> 无卡回退 product 原字段
"""
from flask import Blueprint, request

from utils.response import success_response, error_response, handle_errors
from services import card_service

product_card_bp = Blueprint('product_card', __name__)


@product_card_bp.route('/product-cards/generate', methods=['POST'])
@handle_errors
def generate_card():
    """AI 一键生成商品卡（三步全流程：卖点总结 -> SEO 关键词 -> 标题/描述/标签）

    请求体:
      {
        "product_id": "id_xxx",              # 必填，商品 ID（Product JSON id）
        "selling_points_zh": "卖点1\n卖点2",  # 可选；不填则需 base_card_id 复用卖点
        "base_card_id": 3                    # 可选，基于某版本再生成
      }
    每次生成 = 新版本（version+1，status=GENERATED），绝不覆盖既有 EDITED 版本。
    """
    body = request.get_json(silent=True) or {}
    product_id = (body.get('product_id') or '').strip()
    if not product_id:
        return error_response(msg='缺少 product_id 参数', code=400)

    selling_points = body.get('selling_points_zh')
    base_card_id = body.get('base_card_id')
    if base_card_id is not None:
        try:
            base_card_id = int(base_card_id)
        except (TypeError, ValueError):
            return error_response(msg='base_card_id 必须为整数', code=400)

    card = card_service.generate_card(product_id, selling_points, base_card_id=base_card_id)
    return success_response(data=card, msg='商品卡生成成功')


@product_card_bp.route('/product-cards/<int:card_id>/regenerate', methods=['POST'])
@handle_errors
def regenerate_card(card_id):
    """基于某版本重新生成（复用该版本卖点跑全流程，新增版本不覆盖）"""
    card = card_service.get_card(card_id)
    if not card:
        return error_response(msg='商品卡不存在', code=404)
    result = card_service.generate_card(card['product_id'], None, base_card_id=card_id)
    return success_response(data=result, msg='重新生成成功')


@product_card_bp.route('/product-cards/manual', methods=['POST'])
@handle_errors
def create_manual_card():
    """人工新建商品卡落库（status=EDITED，source=manual）

    请求体: { "product_id": "id_xxx", "title_ru": "...", "description_ru": "...",
              "keywords_ru": [...], "main_tags_ru": [...], ... }
    """
    body = request.get_json(silent=True) or {}
    product_id = (body.get('product_id') or '').strip()
    if not product_id:
        return error_response(msg='缺少 product_id 参数', code=400)
    data = {k: v for k, v in body.items() if k != 'product_id'}
    card = card_service.save_manual_card(product_id, data)
    return success_response(data=card, msg='商品卡已保存（人工）')


@product_card_bp.route('/product-cards/<int:card_id>', methods=['PUT'])
@handle_errors
def edit_card(card_id):
    """人工编辑已有卡：原地更新并置 status=EDITED（AI 草稿被人工接手后优先生效）"""
    body = request.get_json(silent=True) or {}
    card = card_service.update_card(card_id, body)
    return success_response(data=card, msg='商品卡已更新（人工编辑优先）')


@product_card_bp.route('/product-cards/<int:card_id>/activate', methods=['POST'])
@handle_errors
def activate_card(card_id):
    """人工采纳某版本：status 置 EDITED，成为上架优先取值"""
    card = card_service.activate_card(card_id)
    return success_response(data=card, msg='已采纳该版本')


@product_card_bp.route('/product-cards/<int:card_id>', methods=['DELETE'])
@handle_errors
def delete_card(card_id):
    """删除版本（仅允许删除非当前生效版本）"""
    card_service.delete_card(card_id)
    return success_response(msg='已删除')


@product_card_bp.route('/product-cards', methods=['GET'])
@handle_errors
def list_cards():
    """版本列表（按版本号倒序，带 isCurrent/isAi 标记）"""
    product_id = (request.args.get('product_id') or '').strip()
    if not product_id:
        return error_response(msg='缺少 product_id 参数', code=400)
    page = int(request.args.get('page', 1) or 1)
    page_size = int(request.args.get('page_size', 20) or 20)
    return success_response(data=card_service.list_versions(product_id, page, page_size), msg='获取成功')


@product_card_bp.route('/product-cards/current', methods=['GET'])
@handle_errors
def current_card():
    """当前生效版本（上架取值：latest EDITED -> latest GENERATED -> null 回退原字段）"""
    product_id = (request.args.get('product_id') or '').strip()
    if not product_id:
        return error_response(msg='缺少 product_id 参数', code=400)
    card = card_service.get_current_card(product_id)
    return success_response(data=card, msg='获取成功')


@product_card_bp.route('/product-cards/<int:card_id>', methods=['GET'])
@handle_errors
def get_card_detail(card_id):
    """单版本详情"""
    card = card_service.get_card(card_id)
    if not card:
        return error_response(msg='商品卡不存在', code=404)
    return success_response(data=card, msg='获取成功')