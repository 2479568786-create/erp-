# -*- coding: utf-8 -*-
"""
自动保存草稿路由（auto_save_bp）
- 业务员填的所有表单自动持久化到后端 SQLite（per-user 隔离）
- admin 可查看所有用户草稿（监管用）
- 草稿在 save() 成功后由前端显式 DELETE，避免占空间

接口：
  GET    /api/auto-save/drafts?scope=&key=            取单条（自己）
  GET    /api/auto-save/drafts?scope=                 列自己该 scope 的所有
  PUT    /api/auto-save/drafts                        UPSERT 单条（自己）
  DELETE /api/auto-save/drafts?scope=&key=            删单条（自己）
  GET    /api/auto-save/drafts/all?scope=             admin 专用：按用户聚合所有
"""
import json
from flask import Blueprint, request

from db import query, execute
from utils.response import success_response, error_response, handle_errors
from utils.auth import require_login, require_admin, get_current_user

auto_save_bp = Blueprint('auto_save', __name__)


def _normalize_payload(value):
    """payload 入库前规范化为字符串。None/空 -> '{}'。dict/list -> JSON 字符串。"""
    if value is None or value == '':
        return '{}'
    if isinstance(value, str):
        # 兼容前端直接传 JSON 字符串
        return value
    return json.dumps(value, ensure_ascii=False)


def _row_to_draft(row, with_user=False):
    """sqlite Row -> dict，payload 反序列化回对象便于前端直接用"""
    d = dict(row)
    # 尝试反序列化 payload 方便前端
    try:
        d['payload'] = json.loads(d.get('payload') or '{}')
    except Exception:
        d['payload'] = {}
    try:
        d['meta_json'] = json.loads(d.get('meta_json') or '{}')
    except Exception:
        d['meta_json'] = {}
    if with_user:
        # admin 列表需要附用户名
        u = query("SELECT username, nickname, role FROM accounts WHERE id = ?",
                  (d.get('user_id'),), one=True)
        d['user'] = u or {'username': '?', 'nickname': '?', 'role': '?'}
    return d


@auto_save_bp.route('/auto-save/drafts', methods=['GET'])
@require_login
@handle_errors
def get_draft():
    """取自己的草稿（单条 or 列该 scope 全部）"""
    user = get_current_user()
    scope = (request.args.get('scope') or '').strip()
    key = (request.args.get('key') or '').strip()
    if not scope:
        return error_response(msg='缺少 scope 参数', code=400)

    if key:
        row = query(
            "SELECT * FROM auto_save_drafts WHERE user_id = ? AND scope = ? AND draft_key = ?",
            (user['id'], scope, key), one=True
        )
        if not row:
            return success_response(data=None, msg='无草稿')
        return success_response(data=_row_to_draft(row), msg='ok')

    # 列该 scope 下自己所有草稿
    rows = query(
        "SELECT * FROM auto_save_drafts WHERE user_id = ? AND scope = ? ORDER BY updated_at DESC",
        (user['id'], scope)
    )
    return success_response(
        data=[_row_to_draft(r) for r in rows],
        msg='ok'
    )


@auto_save_bp.route('/auto-save/drafts', methods=['PUT'])
@require_login
@handle_errors
def upsert_draft():
    """UPSERT 自己的草稿（前端防抖调用）"""
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    scope = (body.get('scope') or '').strip()
    draft_key = (body.get('draft_key') or body.get('key') or '').strip()
    if not scope or not draft_key:
        return error_response(msg='缺少 scope/draft_key', code=400)

    payload = _normalize_payload(body.get('payload'))
    meta = _normalize_payload(body.get('meta') or body.get('meta_json') or {})

    # UPSERT：同 (user_id, scope, draft_key) 唯一，更新 payload + updated_at
    existing = query(
        "SELECT id FROM auto_save_drafts WHERE user_id = ? AND scope = ? AND draft_key = ?",
        (user['id'], scope, draft_key), one=True
    )
    if existing:
        execute(
            "UPDATE auto_save_drafts SET payload = ?, meta_json = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (payload, meta, existing['id'])
        )
        return success_response(data={'id': existing['id'], 'updated': True}, msg='已更新')
    else:
        new_id = execute(
            "INSERT INTO auto_save_drafts (user_id, scope, draft_key, payload, meta_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (user['id'], scope, draft_key, payload, meta)
        )
        return success_response(data={'id': new_id, 'updated': False}, msg='已保存')


@auto_save_bp.route('/auto-save/drafts', methods=['DELETE'])
@require_login
@handle_errors
def delete_draft():
    """删自己的草稿（保存成功后清理）"""
    user = get_current_user()
    scope = (request.args.get('scope') or '').strip()
    key = (request.args.get('key') or '').strip()
    if not scope or not key:
        return error_response(msg='缺少 scope/key', code=400)
    execute(
        "DELETE FROM auto_save_drafts WHERE user_id = ? AND scope = ? AND draft_key = ?",
        (user['id'], scope, key)
    )
    return success_response(msg='已删除')


@auto_save_bp.route('/auto-save/drafts/all', methods=['GET'])
@require_admin
@handle_errors
def list_all_drafts():
    """admin 专用：按用户聚合所有草稿（监管用）

    返回结构：{
      scope: [
        { user_id, username, nickname, role, draft_key, meta, updated_at, payload_size },
        ...
      ]
    }
    """
    scope = (request.args.get('scope') or '').strip()
    if scope:
        rows = query(
            """SELECT d.*, a.username, a.nickname, a.role
               FROM auto_save_drafts d
               JOIN accounts a ON a.id = d.user_id
               WHERE d.scope = ?
               ORDER BY d.updated_at DESC""",
            (scope,)
        )
        items = []
        for r in rows:
            d = dict(r)
            try:
                d['meta'] = json.loads(d.get('meta_json') or '{}')
            except Exception:
                d['meta'] = {}
            d.pop('payload', None)         # admin 列表不返回 payload 大文本（按需另开 GET 单条详情）
            d.pop('meta_json', None)
            items.append(d)
        return success_response(data={scope: items}, msg='ok')
    # 无 scope：返回全量聚合（按 scope 分组）
    rows = query(
        """SELECT d.*, a.username, a.nickname, a.role
           FROM auto_save_drafts d
           JOIN accounts a ON a.id = d.user_id
           ORDER BY d.scope, d.updated_at DESC"""
    )
    grouped = {}
    for r in rows:
        d = dict(r)
        sc = d.get('scope', '?')
        try:
            d['meta'] = json.loads(d.get('meta_json') or '{}')
        except Exception:
            d['meta'] = {}
        d.pop('payload', None)
        d.pop('meta_json', None)
        grouped.setdefault(sc, []).append(d)
    return success_response(data=grouped, msg='ok')
