# -*- coding: utf-8 -*-
"""AI 视频工坊 API。"""
from flask import Blueprint, request

from models.product import Product
from services import video_service
from utils.auth import require_login, get_current_user, visible_store_pks
from utils.response import success_response, error_response, handle_errors

video_bp = Blueprint('video', __name__)


def _product(product_id):
    product = Product.find_by_id(str(product_id or '').strip())
    if not product:
        raise video_service.VideoError('商品不存在')
    return product


def _can_access(product):
    user = get_current_user() or {}
    pks = visible_store_pks(user)
    if pks is None:
        return True
    return str(product.get('storeId')) in {str(x) for x in pks}


@video_bp.route('/video/config', methods=['GET'])
@require_login
def video_config():
    return success_response(data={
        'model': video_service.DEFAULT_MODEL,
        'duration': video_service.DEFAULT_DURATION,
        'resolution': video_service.DEFAULT_RESOLUTION,
        'models': [
            {'id': 'hailuo-h3-shouweizhen', 'label': '海螺 H3 首尾帧', 'duration': '6', 'resolution': '768P', 'maxImages': 2},
            {'id': 'seedance-2.0-guanfang', 'label': 'SD 2.0 多图参考', 'duration': '6', 'resolution': '720p', 'maxImages': 9},
        ],
    })


@video_bp.route('/video/tasks', methods=['GET'])
@require_login
def video_tasks():
    tasks = video_service.list_tasks(request.args.get('limit', 30))
    filtered = []
    for task in tasks:
        try:
            if _can_access(_product(task.get('productId'))):
                filtered.append(task)
        except Exception:
            continue
    return success_response(data={'list': filtered})


@video_bp.route('/video/tasks/<task_id>', methods=['GET'])
@require_login
def video_task(task_id):
    task = video_service.get_task(task_id)
    if not task:
        return error_response('视频任务不存在', 404)
    try:
        if not _can_access(_product(task.get('productId'))):
            return error_response('无权查看该视频任务', 403)
    except Exception:
        return error_response('商品不存在', 404)
    return success_response(data=task)


@video_bp.route('/video/tasks', methods=['POST'])
@require_login
@handle_errors
def create_video_task():
    body = request.get_json(silent=True) or {}
    product = _product(body.get('productId'))
    if not _can_access(product):
        return error_response('无权操作该商品', 403)
    images = [u for u in (product.get('images') or []) if str(u).startswith(('http://', 'https://'))]
    image_urls = body.get('imageUrls') if isinstance(body.get('imageUrls'), list) else []
    image_url = str(body.get('imageUrl') or (images[0] if images else '')).strip()
    if not image_urls:
        image_urls = [image_url] if image_url else []
    user = get_current_user() or {}
    task = video_service.create_task(
        product,
        image_url=image_url,
        image_urls=image_urls,
        prompt=body.get('prompt') or '',
        model=body.get('model') or video_service.DEFAULT_MODEL,
        version=body.get('version') or '',
        mode=body.get('mode') or '',
        duration=str(body.get('duration') or video_service.DEFAULT_DURATION),
        resolution=body.get('resolution') or video_service.DEFAULT_RESOLUTION,
        operator=str(user.get('username') or user.get('nickname') or ''),
    )
    return success_response(data=task, msg='视频任务已创建')


@video_bp.route('/video/tasks/<task_id>/bind', methods=['POST'])
@require_login
@handle_errors
def bind_video(task_id):
    task = video_service.get_task(task_id)
    if not task:
        return error_response('视频任务不存在', 404)
    if not _can_access(_product(task.get('productId'))):
        return error_response('无权操作该视频任务', 403)
    body = request.get_json(silent=True) or {}
    data = video_service.bind_to_product(task, body.get('videoName') or '')
    return success_response(data=data, msg='视频已绑定到商品')


@video_bp.route('/video/tasks/<task_id>/sync', methods=['POST'])
@require_login
@handle_errors
def sync_video(task_id):
    task = video_service.get_task(task_id)
    if not task:
        return error_response('视频任务不存在', 404)
    if not _can_access(_product(task.get('productId'))):
        return error_response('无权操作该视频任务', 403)
    body = request.get_json(silent=True) or {}
    data = video_service.sync_to_ozon(task, body.get('videoName') or '')
    return success_response(data=data, msg='已提交 Ozon 视频更新')


