"""JLC 工作台同源代理（所有登录用户可用）。"""
import time

from flask import Blueprint, current_app, make_response, request
from itsdangerous import URLSafeSerializer, BadSignature

from db import query
from utils.auth import get_current_user, require_login

jlc_workspace_bp = Blueprint('jlc_workspace', __name__)
COOKIE_NAME = 'jlc_workspace_session'
SESSION_TTL = 12 * 60 * 60


def _serializer():
    return URLSafeSerializer(current_app.config['SECRET_KEY'], salt='jlc-workspace')


@jlc_workspace_bp.post('/api/jlc-workspace/session')
@require_login
def create_workspace_session():
    user = get_current_user()
    value = _serializer().dumps({'uid': user['id'], 'exp': int(time.time()) + SESSION_TTL})
    response = current_app.make_response({'code': 200})
    response.set_cookie(COOKIE_NAME, value, max_age=SESSION_TTL, httponly=True,
                        secure=request.is_secure, samesite='Lax', path='/jlc-workspace')
    return response


def _workspace_user():
    value = request.cookies.get(COOKIE_NAME)
    if not value:
        return False
    try:
        payload = _serializer().loads(value)
    except BadSignature:
        return False
    if payload.get('exp', 0) < int(time.time()):
        return False
    user = query('SELECT id, role FROM accounts WHERE id = ?', (payload.get('uid'),), one=True)
    return bool(user)


@jlc_workspace_bp.get('/_jlc-workspace-auth')
def authorize_workspace_proxy():
    if not _workspace_user():
        return '', 403
    return make_response('', 204)