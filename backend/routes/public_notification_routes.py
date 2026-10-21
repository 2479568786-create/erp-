"""管理员广播通知与业务员只读通知。"""
from datetime import datetime, timezone

from flask import Blueprint, request

from db import execute, query
from utils.auth import get_current_user, require_admin, require_login
from utils.response import error_response, handle_errors, paginate_response, success_response
from utils.validators import extract_pagination

public_notification_bp = Blueprint('public_notifications', __name__)
_PRIORITIES = {'normal', 'important', 'urgent'}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row(row):
    item = dict(row)
    item['is_read'] = bool(item.pop('read_at', None))
    return item


@public_notification_bp.route('/public-notifications', methods=['GET'])
@require_login
@handle_errors
def list_public_notifications():
    user = get_current_user()
    pagination = extract_pagination(request.args)
    now = _now()
    rows = query(
        """SELECT n.id, n.title, n.content, n.priority, n.author_name,
                  n.published_at, n.expires_at, n.created_at, r.read_at
             FROM public_notifications n
             LEFT JOIN public_notification_reads r
               ON r.notification_id = n.id AND r.user_id = ?
            WHERE n.published_at <= ?
              AND (n.expires_at IS NULL OR n.expires_at = '' OR n.expires_at > ?)
            ORDER BY CASE n.priority WHEN 'urgent' THEN 0 WHEN 'important' THEN 1 ELSE 2 END,
                     n.published_at DESC, n.id DESC""",
        (user['id'], now, now),
    )
    total = len(rows)
    unread = sum(1 for row in rows if not row.get('read_at'))
    start = (pagination['page'] - 1) * pagination['pageSize']
    items = [_row(row) for row in rows[start:start + pagination['pageSize']]]
    return paginate_response(items, total, unreadCount=unread, **pagination)


@public_notification_bp.route('/public-notifications', methods=['POST'])
@require_admin
@handle_errors
def create_public_notification():
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    title = str(body.get('title') or '').strip()
    content = str(body.get('content') or '').strip()
    priority = str(body.get('priority') or 'normal').strip().lower()
    expires_at = str(body.get('expires_at') or '').strip() or None
    if not title:
        return error_response('请输入通知标题', 400)
    if not content:
        return error_response('请输入通知内容', 400)
    if len(title) > 120:
        return error_response('通知标题不能超过120个字符', 400)
    if len(content) > 5000:
        return error_response('通知内容不能超过5000个字符', 400)
    if priority not in _PRIORITIES:
        return error_response('通知级别无效', 400)
    published_at = _now()
    notification_id = execute(
        """INSERT INTO public_notifications
           (title, content, priority, author_id, author_name, published_at, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, content, priority, user['id'], user.get('nickname') or user.get('username') or '管理员', published_at, expires_at, published_at),
    )
    return success_response(data={'id': notification_id}, msg='通知已发布')


@public_notification_bp.route('/public-notifications/<int:notification_id>/read', methods=['POST'])
@require_login
@handle_errors
def mark_public_notification_read(notification_id):
    user = get_current_user()
    if not query('SELECT id FROM public_notifications WHERE id = ?', (notification_id,), one=True):
        return error_response('通知不存在', 404)
    execute(
        """INSERT INTO public_notification_reads (notification_id, user_id, read_at)
           VALUES (?, ?, ?)
           ON CONFLICT(notification_id, user_id) DO UPDATE SET read_at = excluded.read_at""",
        (notification_id, user['id'], _now()),
    )
    return success_response(msg='已标记为已读')
