"""
GeekOzon ERP - Agent 服务令牌鉴权（R1）

用途：让脚本/Agent 以「服务令牌」而非 JWT 调用写接口（建卡、套图、绑图、发布），
不影响现有登录体系：
- 现有 JWT（auth_tokens 表，Authorization: Bearer <token>）完全照旧；
- Agent 令牌支持 `Authorization: Bearer <agent_token>` 或 `X-Agent-Key: <agent_token>`；
- 令牌只存 sha256(pepper + token)，数据库里没有明文；
- 按 scope 授权，每令牌默认 60 req/min 滑动窗口限流（进程内；多 worker 各自计数，见 README）；
- 所有 Agent 接口写 agent_ops 审计（R9 的基础）。

配置（/etc/geekozon-erp.env）：
- AGENT_TOKEN_PEPPER：必填，缺失时 Agent 令牌一律拒绝（fail-closed），并在启动日志告警；
- AGENT_MAX_RPM：每令牌每分钟请求上限，默认 60。
"""
import hashlib
import os
import re
import threading
import time
from functools import wraps

from flask import g, request

from db import execute, query
from utils.response import error_response

# 路径 → 所需 scope。只有这里列出的路径才接受 Agent 令牌（其余仍走 JWT）。
_SCOPE_RULES = (
    (('POST',), r'^/api/products/collect$', 'products:write'),
    (('GET',), r'^/api/products/[^/\s]+$', 'products:read'),
    (('PUT',), r'^/api/products/[^/\s]+$', 'products:write'),
    (('POST',), r'^/api/products/[^/\s]+/images/attach$', 'images:write'),
    (('PUT',), r'^/api/products/[^/\s]+/attributes$', 'products:write'),
    (('POST',), r'^/api/products/[^/\s]+/validate$', 'products:read'),
    (('POST',), r'^/api/product-cards/generate$', 'cards:write'),
    (('POST',), r'^/api/product-cards/\d+/regenerate$', 'cards:write'),
    (('POST',), r'^/api/product-cards/\d+/activate$', 'cards:write'),
    (('POST',), r'^/api/agent/cards/build$', 'cards:write'),
    (('POST',), r'^/api/agent/cards/[^/\s]+/lint$', 'cards:write'),
    (('POST',), r'^/api/agent/images/suite$', 'images:write'),
    (('GET',), r'^/api/gallery$', 'gallery:read'),
    (('POST',), r'^/api/publish$', 'publish:write'),
    (('POST',), r'^/api/publish/[^/\s]+/retry$', 'publish:write'),
    (('GET',), r'^/api/publish/[^/\s]+/status$', 'publish:write'),
    (('GET',), r'^/api/publish-records/[^/\s]+/diagnosis$', 'publish:read'),
    (('GET',), r'^/api/categories/attributes$', 'categories:read'),
    (('GET',), r'^/api/categories/attribute-values$', 'categories:read'),
    (('GET',), r'^/api/categories/attribute-values/search$', 'categories:read'),
    (('GET',), r'^/api/agent/ops$', 'agent:read'),
)

_COMPILED_SCOPE_RULES = tuple(
    (frozenset(methods), re.compile(pattern), scope)
    for methods, pattern, scope in _SCOPE_RULES
)

_RATE_LOCK = threading.Lock()
_RATE_WINDOWS = {}  # token_id -> [timestamps]


def _pepper():
    """读取 pepper；不提供默认值（缺失即 Agent 令牌不可用）。"""
    pepper = os.environ.get('AGENT_TOKEN_PEPPER', '')
    if not pepper:
        try:
            from config import Config
            pepper = getattr(Config, 'AGENT_TOKEN_PEPPER', '') or ''
        except Exception:
            pepper = ''
    return pepper


def pepper_configured():
    return bool(_pepper())


def hash_token(token):
    """sha256(pepper + token)，与签发脚本保持同一算法。"""
    pepper = _pepper()
    if not pepper:
        raise RuntimeError('AGENT_TOKEN_PEPPER 未配置，无法计算 Agent 令牌哈希')
    return hashlib.sha256((pepper + token).encode('utf-8')).hexdigest()


def required_scope(method, path):
    """返回该请求所需的 scope；不在白名单返回 None（仍走 JWT 鉴权）。"""
    method = (method or '').upper()
    for methods, pattern, scope in _COMPILED_SCOPE_RULES:
        if method in methods and pattern.match(path or ''):
            return scope
    return None


def extract_token():
    """从 Authorization: Bearer 或 X-Agent-Key 取令牌。"""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:].strip()
        if token:
            return token
    return (request.headers.get('X-Agent-Key', '') or '').strip() or ''


def _rate_limit_exceeded(token_id):
    try:
        from config import Config
        limit = int(getattr(Config, 'AGENT_MAX_RPM', 60) or 60)
    except Exception:
        limit = 60
    if limit <= 0:
        return False
    now = time.time()
    with _RATE_LOCK:
        window = _RATE_WINDOWS.setdefault(token_id, [])
        cutoff = now - 60
        while window and window[0] < cutoff:
            window.pop(0)
        if len(window) >= limit:
            return True
        window.append(now)
    return False


def lookup_token(token):
    """校验令牌，返回 (row, error_response)。不打印令牌本身。"""
    if not token:
        return None, error_response('需要 Agent 令牌（Authorization: Bearer 或 X-Agent-Key）', 401)
    if not pepper_configured():
        return None, error_response('服务未配置 AGENT_TOKEN_PEPPER，Agent 接口暂不可用', 503)
    try:
        token_hash = hash_token(token)
    except RuntimeError:
        return None, error_response('服务未配置 AGENT_TOKEN_PEPPER，Agent 接口暂不可用', 503)
    row = query(
        """SELECT * FROM agent_tokens
           WHERE token_hash = ? AND revoked = 0
             AND (expires_at IS NULL OR expires_at = '' OR expires_at > datetime('now'))""",
        (token_hash,),
        one=True,
    )
    if not row:
        return None, error_response('Agent 令牌无效、已撤销或已过期', 401)
    if _rate_limit_exceeded(row['id']):
        return None, error_response('请求过于频繁，请稍后重试', 429)
    try:
        execute("UPDATE agent_tokens SET last_used_at = datetime('now') WHERE id = ?", (row['id'],))
    except Exception:
        pass
    return row, None


def token_scopes(row):
    return [s.strip() for s in (row.get('scopes') or '').split(',') if s.strip()]


def has_scope(row, scope):
    scopes = token_scopes(row)
    return scope in scopes or '*' in scopes


def log_agent_op(token_row, scope, action, status='ok', product_id='', request_id='', duration_ms=0,
                 error='', account_id=None, username=''):
    """写 agent_ops 审计（R9 基础；R-A5 起记「谁调的」）。失败不影响主流程。"""
    try:
        execute(
            """INSERT INTO agent_ops
               (token_id, scope, action, product_id, request_id, status, duration_ms, error,
                account_id, username, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (token_row.get('id') if token_row else None, scope or '', action or '',
             product_id or '', request_id or '', status or 'ok', int(duration_ms or 0),
             (error or '')[:1000], account_id, (username or '')[:120]),
        )
    except Exception:
        pass


def agent_request_guard():
    """全局 before_request：路径在白名单且携带 Agent 令牌时完成鉴权。

    - 有效 JWT 优先，走原有登录流程（行为不变）；
    - Agent 令牌校验失败返回 401/403/429/503；
    - 非白名单路径完全不受影响。
    """
    if request.method == 'OPTIONS':
        return None
    scope = required_scope(request.method, request.path)
    if not scope:
        return None

    # 1) 现有 JWT 优先：有登录态就交给 require_api_auth 处理
    try:
        from utils.auth import get_current_user
        if get_current_user():
            return None
    except Exception:
        pass

    token = extract_token()
    if not token:
        # 既无 JWT 也无 Agent 令牌：交回原有逻辑给 401
        return None

    started = time.time()
    row, err = lookup_token(token)
    if err:
        return err
    if not has_scope(row, scope):
        log_agent_op(row, scope, f'{request.method} {request.path}', status='forbidden',
                     duration_ms=(time.time() - started) * 1000,
                     error=f'缺少 scope: {scope}')
        return error_response(f'令牌缺少权限：{scope}', 403)

    g.agent_token = dict(row)
    g.agent_scope = scope
    g.agent_started = time.time()
    g.agent_audit = {}
    # 成功审计统一在 after_request 里写一条（见 agent_audit_after_request），
    # 避免 guard 与路由各写一次造成重复行。
    return None


def set_agent_audit(**fields):
    """路由补充审计字段（product_id / request_id / error），由 after_request 统一落库。"""
    audit = getattr(g, 'agent_audit', None)
    if audit is None:
        g.agent_audit = {}
        audit = g.agent_audit
    audit.update({k: v for k, v in fields.items() if v is not None})


def agent_audit_after_request(response):
    """每个 Agent 调用写且只写一条 agent_ops（R-A5：JWT 调用记到人）。"""
    token = getattr(g, 'agent_token', None)
    scope = getattr(g, 'agent_scope', None)
    user = getattr(g, 'agent_user', None)
    if not scope or (not token and not user):
        return response
    try:
        status_code = getattr(response, 'status_code', 200)
        audit = getattr(g, 'agent_audit', {}) or {}
        status = 'ok' if status_code < 400 else ('invalid' if status_code < 500 else 'error')
        if audit.get('status'):
            status = audit['status']
        started = getattr(g, 'agent_started', None) or time.time()
        if token:
            account_id = token.get('account_id')
            username = 'agent:%s' % (token.get('name') or token.get('id') or '')
        else:
            account_id = user.get('id')
            username = user.get('username') or user.get('nickname') or ''
        log_agent_op(
            token, scope, f'{request.method} {request.path}',
            status=status,
            product_id=str(audit.get('product_id') or ''),
            request_id=str(audit.get('request_id') or ''),
            duration_ms=(time.time() - started) * 1000,
            error=str(audit.get('error') or ''),
            account_id=account_id,
            username=username,
        )
    except Exception:
        pass
    return response


def current_agent():
    """当前请求的 Agent 令牌行（未认证返回 None）。"""
    return getattr(g, 'agent_token', None)


def require_agent_scope(scope):
    """装饰器：要求 Agent 令牌具备 scope；管理员与业务员 JWT 同样可用。

    - Agent 令牌：校验 scope（行为不变）；
    - admin JWT：放行（行为不变，便于页面排障）；
    - salesperson / operator JWT（R-A1 新增）：放行，店铺级校验交给服务层/路由层；
    - 其它角色 JWT：403；未登录：401。

    全局 guard 已对白名单路径做过一次校验，这里用于新增的 /api/agent/* 路由自身兜底。
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            agent = current_agent()
            if agent:
                if not has_scope(agent, scope):
                    return error_response(f'令牌缺少权限：{scope}', 403)
                return fn(*args, **kwargs)
            from utils.agent_scope import SCOPED_ROLES
            try:
                from utils.auth import get_current_user
                user = get_current_user()
            except Exception:
                user = None
            if user and user.get('role') == 'admin':
                return fn(*args, **kwargs)
            if user and user.get('role') in SCOPED_ROLES:
                # R-A1：业务员/操作员用自己的 JWT 直接调 Agent 接口（店铺范围在服务层校验）
                g.agent_user = user
                g.agent_scope = 'jwt'
                g.agent_started = time.time()
                g.agent_audit = {}
                return fn(*args, **kwargs)
            if user:
                return error_response('需要 Agent 令牌或管理员登录', 403)
            return error_response('需要 Agent 令牌', 401)
        return wrapper
    return decorator


def current_agent_user():
    """当前请求的业务员/操作员 JWT 用户（R-A1）；非该场景返回 None。"""
    return getattr(g, 'agent_user', None)
