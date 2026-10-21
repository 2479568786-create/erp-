"""
我的选品 API 路由
每个账号独立维护自己的选品台账，互相不可见；管理员可查看/操作所有账号的选品
"""
from flask import Blueprint, request

from models.my_selection import MySelection
from utils.response import success_response, error_response, handle_errors
from utils.auth import require_login, get_current_user

my_selection_bp = Blueprint('my_selection', __name__)


def _uid():
    user = get_current_user()
    return user.get('id') if user else None


def _is_admin(user):
    return bool(user and user.get('role') == 'admin')


@my_selection_bp.route('/my-selections', methods=['GET'])
@handle_errors
@require_login
def list_my_selections():
    user = get_current_user()
    keyword = (request.args.get('keyword') or '').strip()
    if _is_admin(user):
        rows = MySelection.find_all_admin(keyword=keyword or None)
    else:
        rows = MySelection.find_all(user['id'], keyword=keyword or None)
    return success_response(data=rows)


@my_selection_bp.route('/my-selections', methods=['POST'])
@handle_errors
@require_login
def create_my_selection():
    data = request.get_json(silent=True) or {}
    if not (data.get('product_name') or '').strip():
        return error_response('产品名称不能为空')
    item_id = MySelection.create(_uid(), data)
    row = MySelection.find_by_id(item_id, _uid())
    return success_response(data=row, msg='已加入我的选品')


@my_selection_bp.route('/my-selections/<int:item_id>', methods=['PUT'])
@handle_errors
@require_login
def update_my_selection(item_id):
    user = get_current_user()
    scope = None if _is_admin(user) else user['id']
    if not MySelection.find_by_id(item_id, scope):
        return error_response('条目不存在', 404)
    data = request.get_json(silent=True) or {}
    MySelection.update(item_id, scope, data)
    row = MySelection.find_by_id(item_id, scope)
    return success_response(data=row, msg='已更新')


@my_selection_bp.route('/my-selections/<int:item_id>', methods=['DELETE'])
@handle_errors
@require_login
def delete_my_selection(item_id):
    user = get_current_user()
    scope = None if _is_admin(user) else user['id']
    if not MySelection.find_by_id(item_id, scope):
        return error_response('条目不存在', 404)
    MySelection.delete(item_id, scope)
    return success_response(msg='已删除')


@my_selection_bp.route('/my-selections/batch-delete', methods=['POST'])
@handle_errors
@require_login
def batch_delete_my_selection():
    user = get_current_user()
    is_admin = _is_admin(user)
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return error_response('请选择要删除的条目')
    deleted = 0
    for raw in ids:
        try:
            iid = int(raw)
        except (TypeError, ValueError):
            continue
        if is_admin:
            if MySelection.find_by_id(iid):
                MySelection.delete(iid)
                deleted += 1
        else:
            if MySelection.find_by_id(iid, user['id']):
                MySelection.delete(iid, user['id'])
                deleted += 1
    return success_response(data={'deleted': deleted}, msg=f'已删除 {deleted} 条')
