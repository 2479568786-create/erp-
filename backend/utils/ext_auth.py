"""/api/ext/* 插件/外部机器人接口的共用鉴权。

与采集插件同一把 key（环境变量 OZON_EXT_API_KEYS，逗号分隔支持轮换；
回退 EXT_API_KEY，最后回退内置默认值，与 ozon_collect_routes 保持一致），
另外允许 ERP 管理员登录态直接调用（方便页面/排障）。
"""
import os
from functools import wraps

from flask import request

from utils.auth import get_current_user


def plugin_api_keys():
    """插件 API Key 白名单：只读 OZON_EXT_API_KEYS（逗号分隔）或 EXT_API_KEY；
    两者都未配置时返回空列表（所有 /api/ext/* 请求按未授权处理）。"""
    raw = os.environ.get('OZON_EXT_API_KEYS') or os.environ.get('EXT_API_KEY') or ''
    return [item.strip() for item in str(raw).split(',') if item.strip()]


def is_ext_key_valid():
    key = (request.headers.get('X-Ext-Key') or '').strip()
    return bool(key) and key in plugin_api_keys()


def current_user():
    try:
        return get_current_user()
    except Exception:
        return None


def ext_authorized(require_admin_for_login=True):
    """X-Ext-Key 命中直接放行；否则要求登录态（默认要求管理员）。"""
    if is_ext_key_valid():
        return True
    user = current_user()
    if not user:
        return False
    if require_admin_for_login and user.get('role') != 'admin':
        return False
    return True


def require_ext_or_admin(f):
    """装饰器：X-Ext-Key 或 admin 登录态，否则 401/403。"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        from utils.response import error_response
        if is_ext_key_valid():
            return f(*args, **kwargs)
        user = current_user()
        if not user:
            return error_response('未授权：缺少有效的 X-Ext-Key 或登录态', 401)
        if user.get('role') != 'admin':
            return error_response('无权限执行此操作', 403)
        return f(*args, **kwargs)
    return wrapper


def require_ext_or_login(f):
    """装饰器：X-Ext-Key 或任意登录态（只读查询用）。"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        from utils.response import error_response
        if is_ext_key_valid() or current_user():
            return f(*args, **kwargs)
        return error_response('未授权：缺少有效的 X-Ext-Key 或登录态', 401)
    return wrapper
