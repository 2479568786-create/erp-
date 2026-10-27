"""
用户认证与账号管理相关 API 路由

账号墙基础：
- 登录：真实账号 + PBKDF2 校验，**不再**支持任意账密码自动创建管理员
- 登出：销毁 Token
- 用户信息：返回 role，供前端控制菜单/按钮权限

管理员专属（require_admin）：
- GET  /api/users            账号列表
- POST /api/users            创建业务员账号
- PUT  /api/users/<id>       修改业务员（昵称/状态/密码）
- DEL  /api/users/<id>        删除账号（下线其店铺归属）
"""
from flask import Blueprint, request
from models.account import Account
from services import image_billing_service as image_billing
from utils.auth import (
    create_token, destroy_token, get_current_user, require_admin,
)
from utils.response import success_response, error_response, handle_errors

auth_bp = Blueprint('auth', __name__)

# 允许的角色枚举（兼容历史账号）；用户管理仅新增/分配业务员。
_VALID_ROLES = ('admin', 'operator', 'salesperson')
_CREATABLE_ROLES = ('salesperson',)
_ASSIGNABLE_STORE_OWNER_ROLES = ('salesperson',)


def _image_billing_fields(account):
    """账号生图计费字段（用户信息 / 账号列表共用）。

    imageBalance 单位是「点」（1 点 = 1 元），保留 2 位小数；
    imageBalanceCents 是库里真实存储的整数（分）。
    """
    cents = int(account.get('image_balance_cents') or 0)
    raw_enabled = account.get('image_enabled')
    enabled = True if raw_enabled is None else bool(int(raw_enabled))
    return {
        'imageBalance': image_billing.cents_to_points(cents),
        'imageBalanceCents': cents,
        'imageEnabled': enabled,
        'imageAdminExempt': account.get('role') == 'admin',
        'imagePricePoints': image_billing.get_billing_config()['price_points_per_image'],
    }



@auth_bp.route('/auth/login', methods=['POST'])
@handle_errors
def login():
    """用户登录：真实账号 + 密码校验（不再自动创建管理员）"""
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    # 密码去除首尾空白：容错复制粘贴时带入的空格/换行（误输空格不应导致登录失败）
    password = (data.get('password') or '').strip()

    if not username or not password:
        return error_response("用户名和密码不能为空", 400)

    # 真实校验：账号必须存在且密码正确
    from utils.security import verify_password
    account = Account.find_by_username(username)
    if not account or not verify_password(password, account.get('password_hash', '')):
        return error_response("用户名或密码错误", 401)

    if account.get('status') == 'disabled':
        return error_response("账号已禁用，请联系管理员", 4031)

    token = create_token(account)
    return success_response(data={
        "token": token,
        "user": {
            "id": account['id'],
            "username": account['username'],
            "nickname": account.get('nickname', ''),
            "role": account.get('role', 'operator'),
        }
    }, msg="登录成功")


@auth_bp.route('/auth/logout', methods=['POST'])
@handle_errors
def logout():
    """登出：销毁当前 Token"""
    auth_header = request.headers.get('Authorization', '')
    token = auth_header[7:].strip() if auth_header.startswith('Bearer ') else ''
    destroy_token(token)
    return success_response(msg="已退出登录")


@auth_bp.route('/user/info', methods=['GET'])
@handle_errors
def get_user_info():
    """获取当前用户信息（登录态由前端判断是否显示账号墙）"""
    account = get_current_user()
    if not account:
        return success_response(data={
            "user": {"id": None, "username": "", "nickname": "游客", "role": "guest"},
            "isLoggedIn": False,
        })
    return success_response(data={
        "user": {
            "id": account['id'],
            "username": account['username'],
            "nickname": account.get('nickname', ''),
            "role": account.get('role', 'operator'),
            **_image_billing_fields(account),
        },
        "isLoggedIn": True,
    })


# ===================== 管理员专属：用户管理 =====================

@auth_bp.route('/users', methods=['GET'])
@handle_errors
@require_admin
def list_users():
    """管理员：账号列表（含每个账号名下店铺数）"""
    accounts = Account.find_all()
    # 统计每个账号名下的店铺数
    from db import query
    store_counts = query("SELECT account_id, COUNT(*) AS cnt FROM stores WHERE account_id IS NOT NULL GROUP BY account_id")
    counts = {r['account_id']: r['cnt'] for r in store_counts}
    users = []
    for a in accounts:
        users.append({
            "id": a['id'],
            "username": a['username'],
            "nickname": a.get('nickname', ''),
            "role": a.get('role', 'operator'),
            "status": a.get('status', 'active'),
            "lastLoginAt": a.get('last_login_at', ''),
            "createdAt": a.get('created_at', ''),
            "storeCount": counts.get(a['id'], 0),
            **_image_billing_fields(a),
        })
    return success_response(data={"list": users, "total": len(users)})


@auth_bp.route('/users', methods=['POST'])
@handle_errors
@require_admin
def create_user():
    """管理员：创建账号"""
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    role = (data.get('role') or 'salesperson').strip()

    if not username or not password:
        return error_response("用户名和密码不能为空", 400)
    if role not in _CREATABLE_ROLES:
        return error_response("管理员只能创建业务员账号", 400)
    if Account.find_by_username(username):
        return error_response(f"用户名 {username} 已存在", 400)
    if len(password) < 6:
        return error_response("密码至少 6 位", 400)

    Account.create(
        username=username,
        password=password,
        nickname=data.get('nickname') or username,
        role=role,
    )
    return success_response(msg=f"已创建账号 {username}")


@auth_bp.route('/users/<int:uid>', methods=['PUT'])
@handle_errors
@require_admin
def update_user(uid):
    """管理员：修改账号（昵称/角色/状态/密码）"""
    account = Account.find_by_id(uid)
    if not account:
        return error_response("账号不存在", 404)
    data = request.get_json() or {}

    update_fields = {}
    if 'role' in data:
        role = (data['role'] or '').strip()
        if account.get('role') != 'salesperson' or role != 'salesperson':
            return error_response("用户管理仅允许维护业务员账号", 400)
    if 'nickname' in data:
        update_fields['nickname'] = data['nickname']
    if 'status' in data:
        update_fields['status'] = data['status']
    if 'password' in data and data['password']:
        if len(data['password']) < 6:
            return error_response("密码至少 6 位", 400)
        Account.update_password(uid, data['password'])

    if update_fields:
        Account.update(uid, **update_fields)
    return success_response(msg="账号已更新")


@auth_bp.route('/users/<int:uid>', methods=['DELETE'])
@handle_errors
@require_admin
def delete_user(uid):
    """管理员：删除账号（关联店铺归属自动解除，变为公共店铺）"""
    account = Account.find_by_id(uid)
    if not account:
        return error_response("账号不存在", 404)
    # 不能删除当前登录账号
    from utils.auth import get_current_user
    cur = get_current_user()
    if cur and uid == cur['id']:
        return error_response("不能删除当前登录账号", 400)
    # 防止删除最后一个管理员
    if account.get('role') == 'admin':
        others = Account.find_all()
        if sum(1 for a in others if a.get('role') == 'admin') <= 1:
            return error_response("不能删除最后一个管理员", 400)
    Account.delete(uid)
    return success_response(msg="账号已删除")


@auth_bp.route('/stores/owner', methods=['PUT'])
@handle_errors
@require_admin
def bind_store_owner():
    """管理员：绑定业务员与店铺（绑定后业务员登录仅见该店铺）

    body: { "storePk": 1, "accountId": 3 }
          或 { "storeId": "216800183", "accountId": 3 }
          accountId=null 表示解绑（店铺升为公共店铺）
    """
    data = request.get_json() or {}
    account_id = data.get('accountId')
    account_id = int(account_id) if account_id else None

    # 定位店铺
    from models.account import Store
    store = None
    if data.get('storePk'):
        store = Store.find_by_id(int(data['storePk']))
    elif data.get('storeId'):
        store = Store.find_by_store_id(data['storeId'])
    if not store:
        return error_response("店铺不存在", 404)

    target = None
    if account_id:
        target = Account.find_by_id(account_id)
        if not target:
            return error_response("业务员账号不存在", 404)
        if target.get('role') not in _ASSIGNABLE_STORE_OWNER_ROLES:
            return error_response("店铺只能绑定给业务员账号", 400)

    Store.update(store['id'], account_id=account_id)
    if target:
        msg = f"店铺「{store['alias'] or store['store_id']}」已绑定给业务员 {target['username']}"
    else:
        msg = f"店铺「{store['alias'] or store['store_id']}」已解绑为公共店铺"
    return success_response(msg=msg)
