"""
GeekOzon ERP - 认证与权限工具
统一管理登录令牌、当前用户解析、角色校验、店铺可见性过滤

设计：
- 令牌：SQLite 持久化（auth_tokens 表），天然支持 gunicorn 多 worker / 多进程共享；
        登出即删行，token 自带过期时间（默认 7 天，可经 Config.TOKEN_EXPIRE_HOURS 调整）
- 角色：admin（管理员，全量）+ salesperson（业务员，仅自己归属店铺）+ operator（旧角色，按受限处理）
- 店铺可见性：业务员仅能访问 stores.account_id = 自己 id 的店铺
"""
import secrets
import time
from functools import wraps
from flask import request

from db import query, execute
from utils.response import error_response
from utils.image_proxy import is_public_image_url


_PUBLIC_API_PATHS = {
    '/api/auth/login',
    '/api/user/info',
    '/api/health',
    '/api/config',
}


def require_api_auth():
    """Require a valid login token for API routes except public entry points.

    插件端点（/api/ext/*）由各路由内部用 X-Ext-Key 自行鉴权，跳过全局登录校验。
    """
    if not request.path.startswith('/api/') or request.method == 'OPTIONS':
        return None
    if request.path in _PUBLIC_API_PATHS:
        return None
    if request.path.startswith('/api/ext/'):
        return None
    if request.path == '/api/image_proxy' and is_public_image_url(request.args.get('url')):
        return None
    if request.method == 'GET' and request.path.startswith('/api/gallery/files/'):
        return None
    if not get_current_user():
        return error_response('请先登录', 401)
    return None

# Token 有效期（秒）：优先读 Config.TOKEN_EXPIRE_HOURS，默认 7 天
try:
    from config import Config
    TOKEN_TTL = getattr(Config, 'TOKEN_EXPIRE_HOURS', 168) * 3600
except Exception:
    TOKEN_TTL = 168 * 3600

# 惰性建表（幂等，兼容任何首次加载顺序；db.py 迁移同样会建）
try:
    execute("""CREATE TABLE IF NOT EXISTS auth_tokens (
        token       TEXT PRIMARY KEY,
        account_id  INTEGER NOT NULL,
        expires_at  INTEGER NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    execute("CREATE INDEX IF NOT EXISTS idx_auth_tokens_account ON auth_tokens(account_id)")
except Exception:
    pass


def create_token(account):
    """登录成功后签发 token（写入 SQLite，跨 worker 可见）"""
    token = secrets.token_hex(24)
    expires_at = int(time.time()) + TOKEN_TTL
    execute(
        "INSERT INTO auth_tokens (token, account_id, expires_at) VALUES (?, ?, ?)",
        (token, account['id'], expires_at),
    )
    return token


def destroy_token(token):
    """登出：删除 token（所有 worker 立即失效）"""
    if token:
        execute("DELETE FROM auth_tokens WHERE token = ?", (token,))


def _extract_token():
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:].strip()
    return ''


def get_current_user():
    """解析当前登录用户（accounts 行 dict），未登录/过期/不存在返回 None"""
    token = _extract_token()
    if not token:
        return None
    row = query(
        """SELECT a.* FROM auth_tokens t
           JOIN accounts a ON a.id = t.account_id
           WHERE t.token = ? AND t.expires_at > ?""",
        (token, int(time.time())),
        one=True,
    )
    return row


def require_login(f):
    """登录校验装饰器：未登录返回 401"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return error_response("请先登录", 401)
        return f(*args, **kwargs)
    return wrapper


def require_admin(f):
    """管理员校验装饰器：未登录 401，非管理员 403"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return error_response("请先登录", 401)
        if user.get('role') != 'admin':
            return error_response("无权限执行此操作", 403)
        return f(*args, **kwargs)
    return wrapper


def visible_store_pks(user):
    """可见店铺主键集合：admin 返回 None（表示不过滤/全量）；业务员返回 [stores.id...]"""
    if not user:
        return None
    if user.get('role') == 'admin':
        return None
    from db import query as _q
    rows = _q("SELECT id FROM stores WHERE account_id = ?", (user['id'],))
    return [r['id'] for r in rows]


def visible_ozon_store_ids(user):
    """可见 Ozon 店铺 ID 集合：admin 返回 None（全量）；业务员返回 [stores.store_id...]"""
    if not user:
        return None
    if user.get('role') == 'admin':
        return None
    from db import query as _q
    rows = _q("SELECT store_id FROM stores WHERE account_id = ?", (user['id'],))
    return [str(r['store_id']) for r in rows]
