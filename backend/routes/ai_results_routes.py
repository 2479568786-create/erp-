# -*- coding: utf-8 -*-
"""
AI 生成结果归档路由（ai_results_bp）
- 所有 AI 跑出来的内容永久落库（product_card / ai_write / translate / ...）
- 用途：审计 / 复用 / admin 监管 / 后续 AI 用量统计
- per-user 写入；admin 可看所有

接口：
  POST /api/ai-results                 落一条 AI 结果（@require_login）
  GET  /api/ai-results                 列自己或 admin 看全部（type/product_id 筛选）
  GET  /api/ai-results/<id>            单条详情（自己或 admin）
  GET  /api/ai-results/stats/summary   admin 专用：按 type 统计（基础）
"""
import json
from flask import Blueprint, request

from db import query, execute
from utils.response import success_response, error_response, handle_errors
from utils.auth import require_login, require_admin, get_current_user

ai_results_bp = Blueprint('ai_results', __name__)


def _row_to_result(row):
    d = dict(row)
    try:
        d['result'] = json.loads(d.get('result_json') or '{}')
    except Exception:
        d['result'] = {}
    try:
        d['meta'] = json.loads(d.get('meta_json') or '{}')
    except Exception:
        d['meta'] = {}
    d.pop('result_json', None)
    d.pop('meta_json', None)
    return d


@ai_results_bp.route('/ai-results', methods=['POST'])
@require_login
@handle_errors
def save_result():
    """落一条 AI 生成结果

    请求体：{
      type: 'product_card' | 'ai_write' | 'translate' | 'description' | ...,
      product_id?: 'id_xxx' or '',
      result: {...任意结构...} or 'json 字符串',
      meta?: {model, provider, language, params, ...}
    }
    """
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    type_ = (body.get('type') or '').strip()
    if not type_:
        return error_response(msg='缺少 type 参数', code=400)

    result = body.get('result')
    if result is None:
        result = '{}'
    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False)

    meta = body.get('meta') or {}
    if not isinstance(meta, str):
        meta = json.dumps(meta, ensure_ascii=False)

    product_id = (body.get('product_id') or '').strip()

    new_id = execute(
        "INSERT INTO ai_results (user_id, product_id, type, result_json, meta_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (user['id'], product_id, type_, result, meta)
    )
    return success_response(data={'id': new_id}, msg='已归档')


@ai_results_bp.route('/ai-results', methods=['GET'])
@require_login
@handle_errors
def list_results():
    """列 AI 结果（自己全部 / admin 看全部 + 按用户筛选）"""
    user = get_current_user()
    type_ = (request.args.get('type') or '').strip()
    product_id = (request.args.get('product_id') or '').strip()
    target_user = (request.args.get('user_id') or '').strip()  # admin 才生效

    where = []
    params = []
    if user.get('role') != 'admin':
        where.append('user_id = ?')
        params.append(user['id'])
    elif target_user:
        where.append('user_id = ?')
        params.append(int(target_user))

    if type_:
        where.append('type = ?')
        params.append(type_)
    if product_id:
        where.append('product_id = ?')
        params.append(product_id)

    page = int(request.args.get('page', 1) or 1)
    page_size = min(int(request.args.get('page_size', 50) or 50), 200)
    offset = (page - 1) * page_size

    sql = "SELECT * FROM ai_results"
    if where:
        sql += " WHERE " + " AND ".join(where)
    total = query(f"SELECT COUNT(*) AS c FROM ai_results" + ((" WHERE " + " AND ".join(where)) if where else ""), tuple(params), one=True)['c']
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    rows = query(sql, tuple(params) + (page_size, offset))

    # 列表模式 result 反序列化为简化结构（避免大 payload 卡前端）
    items = []
    for r in rows:
        d = dict(r)
        try:
            meta = json.loads(d.get('meta_json') or '{}')
        except Exception:
            meta = {}
        d['meta'] = meta
        d['result_size'] = len(d.get('result_json') or '')
        d['result_preview'] = (d.get('result_json') or '')[:200]
        d.pop('result_json', None)
        d.pop('meta_json', None)
        items.append(d)

    return success_response(
        data={'list': items, 'total': total, 'page': page, 'pageSize': page_size},
        msg='ok'
    )


@ai_results_bp.route('/ai-results/stats/summary', methods=['GET'])
@require_admin
@handle_errors
def stats_summary():
    """admin 专用：按 type + 用户 统计（基础）"""
    rows = query(
        """SELECT type, user_id, COUNT(*) AS cnt, MAX(created_at) AS latest
           FROM ai_results GROUP BY type, user_id ORDER BY cnt DESC"""
    )
    return success_response(data=rows, msg='ok')


@ai_results_bp.route('/ai-results/<int:result_id>', methods=['GET'])
@require_login
@handle_errors
def get_result(result_id):
    """单条详情（自己或 admin）"""
    user = get_current_user()
    row = query("SELECT * FROM ai_results WHERE id = ?", (result_id,), one=True)
    if not row:
        return error_response(msg='记录不存在', code=404)
    if user.get('role') != 'admin' and row['user_id'] != user['id']:
        return error_response(msg='无权限查看', code=403)
    return success_response(data=_row_to_result(row), msg='ok')
