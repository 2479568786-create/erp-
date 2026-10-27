"""JLC 新品采集清单（方案1：粘贴 1688 链接 → 待采集清单 → EPG Edge 插件采集 → JLC 运行）。

全新旁路功能，不触碰 ERP 既有商品/发布链路：
- 用户在 ERP 粘贴一个或多个 1688 商品链接，进入「待采集清单」（persisted in SQLite）；
- 用户随后在 1688 页面用 JLC Edge 插件采集真实数据（插件 POST 到 JLC 内网 :8765）；
- 采集完成后，本清单标记已采集并关联 jlcProductId，ERP 前端据此一键「运行流水线」。

表 jlc_collect_links 由本模块自建（CREATE TABLE IF NOT EXISTS），状态与字段保持最小。
"""
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Blueprint, request

from db import get_connection
from utils.auth import get_current_user, require_login
from utils.response import error_response, success_response

jlc_collect_bp = Blueprint('jlc_collect', __name__)

TABLE_DDL = """CREATE TABLE IF NOT EXISTS jlc_collect_links (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id            INTEGER NOT NULL,
    source_url            TEXT    NOT NULL,
    offer_id              TEXT    NOT NULL DEFAULT '',
    title                 TEXT    NOT NULL DEFAULT '',
    note                  TEXT    NOT NULL DEFAULT '',
    status                TEXT    NOT NULL DEFAULT 'pending',
    jlc_product_id        TEXT    NOT NULL DEFAULT '',
    error_message         TEXT    NOT NULL DEFAULT '',
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    collected_at          TEXT
)"""

STATUS_PENDING = 'pending'
STATUS_COLLECTED = 'collected'
STATUS_SKIPPED = 'skipped'
VALID_STATUSES = (STATUS_PENDING, STATUS_COLLECTED, STATUS_SKIPPED)

_OFFER_RE = re.compile(r'/offer/(\d{6,})')


def _ensure_table():
    conn = get_connection()
    try:
        conn.execute(TABLE_DDL)
        conn.commit()
    finally:
        conn.close()


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _clean_url(raw):
    raw = (raw or '').strip()
    if not raw:
        return ''
    if not raw.lower().startswith(('http://', 'https://')):
        raw = 'https://' + raw
    return raw


def _offer_id_from_url(url):
    match = _OFFER_RE.search(url or '')
    if match:
        return match.group(1)
    parsed = urlparse(url or '')
    path = parsed.path.rstrip('/')
    tail = path.split('/')[-1]
    digit_tail = re.sub(r'\D', '', tail)
    if digit_tail:
        return digit_tail
    return ''


def _is_1688_url(url):
    if not url:
        return False
    host = (urlparse(url).hostname or '').lower()
    return host == '1688.com' or host.endswith('.1688.com')


def _rows_to_payload(rows):
    return [{
        'id': row['id'],
        'sourceUrl': row['source_url'],
        'offerId': row['offer_id'],
        'title': row['title'],
        'note': row['note'],
        'status': row['status'],
        'jlcProductId': row['jlc_product_id'],
        'errorMessage': row['error_message'],
        'createdAt': row['created_at'],
        'collectedAt': row['collected_at'],
    } for row in rows]


@jlc_collect_bp.get('/api/jlc-collect/links')
@require_login
def list_links():
    _ensure_table()
    user = get_current_user()
    status = request.args.get('status') or ''
    sql = 'SELECT * FROM jlc_collect_links WHERE account_id=?'
    params = [user['id']]
    if status:
        sql += ' AND status=?'
        params.append(status)
    sql += ' ORDER BY id DESC'
    conn = get_connection()
    try:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return success_response(data=_rows_to_payload(rows))


@jlc_collect_bp.post('/api/jlc-collect/links')
@require_login
def add_links():
    """粘贴一个或多个 1688 链接，进入待采集清单（去重：同账号同 URL 不重复插入）。"""
    _ensure_table()
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    raw_urls = body.get('urls') or body.get('url') or ''
    if isinstance(raw_urls, str):
        raw_urls = re.split(r'[\r\n,，;；]+', raw_urls)
    if not isinstance(raw_urls, (list, tuple)):
        return error_response('urls 必须是字符串或数组')

    accepted, rejected = [], []
    for raw in raw_urls:
        url = _clean_url(raw)
        if not url:
            continue
        if not _is_1688_url(url):
            rejected.append({'url': raw, 'reason': '仅支持 1688 商品链接'})
            continue
        offer_id = _offer_id_from_url(url)
        if not offer_id:
            rejected.append({'url': raw, 'reason': '无法识别 1688 offer 编号（需形如 /offer/123456789）'})
            continue
        # 幂等去重：同账号同URL已存在则不重复插入
        conn = get_connection()
        try:
            existing = conn.execute(
                'SELECT id FROM jlc_collect_links WHERE account_id=? AND source_url=?',
                (user['id'], url),
            ).fetchone()
            if existing:
                accepted.append({'id': existing['id'], 'sourceUrl': url, 'duplicate': True})
                continue
            cursor = conn.execute(
                """INSERT INTO jlc_collect_links
                   (account_id, source_url, offer_id, status)
                   VALUES (?, ?, ?, ?)""",
                (user['id'], url, offer_id, STATUS_PENDING),
            )
            conn.commit()
            accepted.append({'id': cursor.lastrowid, 'sourceUrl': url, 'offerId': offer_id, 'duplicate': False})
        finally:
            conn.close()

    if not accepted and not rejected:
        return error_response('未解析到任何链接')
    return success_response(data={
        'accepted': accepted,
        'rejected': rejected,
        'count': len(accepted),
    }, msg=f"已加入清单 {len(accepted)} 条" + (f"，{len(rejected)} 条未通过" if rejected else ""))


@jlc_collect_bp.post('/api/jlc-collect/links/<int:link_id>/mark-collected')
@require_login
def mark_collected(link_id):
    """采集完成后标记：关联 JLC product_id（Pxxxx）。供前端在 JLC 采集箱出现该商品后调用。"""
    _ensure_table()
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    jlc_product_id = str(body.get('jlcProductId') or '').strip()
    conn = get_connection()
    try:
        row = conn.execute(
            'SELECT * FROM jlc_collect_links WHERE id=? AND account_id=?',
            (link_id, user['id']),
        ).fetchone()
        if not row:
            return error_response('清单项不存在', 404)
        cur = conn.execute(
            """UPDATE jlc_collect_links
               SET status=?, jlc_product_id=?, collected_at=?, error_message=''
               WHERE id=?""",
            (STATUS_COLLECTED, jlc_product_id, _now_iso(), link_id),
        )
        conn.commit()
        if cur.rowcount != 1:
            return error_response('更新失败', 500)
    finally:
        conn.close()
    return success_response(data={'id': link_id, 'status': STATUS_COLLECTED, 'jlcProductId': jlc_product_id})


@jlc_collect_bp.post('/api/jlc-collect/links/<int:link_id>/skip')
@require_login
def skip_link(link_id):
    _ensure_table()
    user = get_current_user()
    conn = get_connection()
    try:
        cur = conn.execute(
            'UPDATE jlc_collect_links SET status=? WHERE id=? AND account_id=?',
            (STATUS_SKIPPED, link_id, user['id']),
        )
        conn.commit()
        if cur.rowcount != 1:
            return error_response('清单项不存在', 404)
    finally:
        conn.close()
    return success_response(data={'id': link_id, 'status': STATUS_SKIPPED})


@jlc_collect_bp.delete('/api/jlc-collect/links/<int:link_id>')
@require_login
def delete_link(link_id):
    _ensure_table()
    user = get_current_user()
    conn = get_connection()
    try:
        cur = conn.execute(
            'DELETE FROM jlc_collect_links WHERE id=? AND account_id=?',
            (link_id, user['id']),
        )
        conn.commit()
        if cur.rowcount != 1:
            return error_response('清单项不存在', 404)
    finally:
        conn.close()
    return success_response(data={'id': link_id, 'deleted': True})
