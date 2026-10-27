"""
水印模板 API 路由
替代毛子 ERP 云端 /api.watermark/templates，本地管理商品图片水印模板

接口:
- GET    /api/watermark/templates          获取水印模板列表
- POST   /api/watermark/templates          新增水印模板
- PUT    /api/watermark/templates/<id>      更新水印模板
- DELETE /api/watermark/templates/<id>     删除水印模板
"""
import json
import io
import os
import uuid
from flask import Blueprint, request, send_file
from PIL import Image, UnidentifiedImageError
from config import Config, DATA_DIR
from db import get_connection
from utils.response import success_response, error_response, handle_errors
from utils.auth import get_current_user, require_login, visible_store_pks
from services.watermark_image import MAX_IMAGE_PIXELS

watermark_bp = Blueprint('watermark', __name__)
WATERMARK_DIR = os.path.join(DATA_DIR, 'watermarks')
DEFAULT_CONFIG = {'position': 'bottom-right', 'widthRatio': 0.18, 'opacity': 0.7, 'marginRatio': 0.03}
POSITIONS = {'bottom-right', 'bottom-left', 'top-right', 'top-left'}


def _check_store_access(store_id):
    user = get_current_user()
    visible = visible_store_pks(user)
    conn = get_connection()
    try:
        exists = conn.execute('SELECT id FROM stores WHERE id = ?', (store_id,)).fetchone()
    finally:
        conn.close()
    if not exists:
        return error_response('店铺不存在', 404)
    if visible is not None and store_id not in visible:
        return error_response('无权访问该店铺', 403)
    return None


def _shop_watermark(store_id):
    conn = get_connection()
    try:
        row = conn.execute('SELECT * FROM shop_watermarks WHERE store_id = ?', (store_id,)).fetchone()
    finally:
        conn.close()
    config = DEFAULT_CONFIG.copy()
    if row:
        try:
            config.update(json.loads(row['config'] or '{}'))
        except (TypeError, json.JSONDecodeError):
            pass
    return row, config


def _shop_payload(store_id):
    row, config = _shop_watermark(store_id)
    configured = bool(row and row['filename'])
    return {'configured': configured, 'image_url': f'/api/watermark/shops/{store_id}/image' if configured else None,
            'filename': row['filename'] if configured else None, 'original_name': row['original_name'] if configured else None,
            'config': config}


@watermark_bp.route('/watermark/shops/<int:store_id>', methods=['GET'])
@require_login
@handle_errors
def get_shop_watermark(store_id):
    denied = _check_store_access(store_id)
    return denied or success_response(_shop_payload(store_id))


@watermark_bp.route('/watermark/shops/<int:store_id>/image', methods=['GET', 'PUT'])
@require_login
@handle_errors
def shop_watermark_image(store_id):
    denied = _check_store_access(store_id)
    if denied:
        return denied
    row, _ = _shop_watermark(store_id)
    if request.method == 'GET':
        if not row or not row['filename']:
            return error_response('该店铺尚未配置水印', 404)
        return send_file(os.path.join(WATERMARK_DIR, row['filename']), mimetype='image/png' if row['filename'].endswith('.png') else 'image/jpeg')
    uploaded = request.files.get('file')
    if not uploaded or not uploaded.filename:
        return error_response('请选择水印图片', 400)
    data = uploaded.read(Config.MAX_IMAGE_SIZE + 1)
    if not data or len(data) > Config.MAX_IMAGE_SIZE:
        return error_response('水印图片为空或超过大小限制', 400)
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > MAX_IMAGE_PIXELS:
            return error_response('水印图片尺寸过大', 400)
        image.verify()
        fmt = image.format
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return error_response('水印图片无效或格式不受支持', 400)
    if fmt not in ('PNG', 'JPEG'):
        return error_response('水印只支持 PNG 或 JPG 图片', 400)
    extension = '.png' if fmt == 'PNG' else '.jpg'
    filename = f'{uuid.uuid4().hex}{extension}'
    os.makedirs(WATERMARK_DIR, exist_ok=True)
    with open(os.path.join(WATERMARK_DIR, filename), 'wb') as image_file:
        image_file.write(data)
    conn = get_connection()
    try:
        conn.execute("""INSERT INTO shop_watermarks (store_id, filename, original_name, config, updated_at)
            VALUES (?, ?, ?, ?, datetime('now')) ON CONFLICT(store_id) DO UPDATE SET
            filename=excluded.filename, original_name=excluded.original_name, updated_at=datetime('now')""",
            (store_id, filename, os.path.basename(uploaded.filename)[:255], json.dumps(_shop_watermark(store_id)[1])))
        conn.commit()
    finally:
        conn.close()
    return success_response(_shop_payload(store_id), msg='水印图片已保存')


@watermark_bp.route('/watermark/shops/<int:store_id>/settings', methods=['PUT'])
@require_login
@handle_errors
def update_shop_watermark_settings(store_id):
    denied = _check_store_access(store_id)
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    _, current = _shop_watermark(store_id)
    config = {**current, **{key: data[key] for key in DEFAULT_CONFIG if key in data}}
    if config['position'] not in POSITIONS:
        return error_response('水印位置无效', 400)
    for key, lower, upper in (('widthRatio', 0.05, 0.5), ('opacity', 0.1, 1), ('marginRatio', 0, 0.1)):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lower <= value <= upper:
            return error_response(f'{key} 超出允许范围', 400)
    conn = get_connection()
    try:
        conn.execute("""INSERT INTO shop_watermarks (store_id, config) VALUES (?, ?)
            ON CONFLICT(store_id) DO UPDATE SET config=excluded.config, updated_at=datetime('now')""",
            (store_id, json.dumps(config)))
        conn.commit()
    finally:
        conn.close()
    return success_response(_shop_payload(store_id), msg='水印样式已保存')


def _row_to_dict(row):
    """将数据库行转换为前端响应字典（JSON 字段反序列化）"""
    try:
        config = json.loads(row['config']) if row['config'] else {}
    except json.JSONDecodeError:
        config = {}
    return {
        'id': row['id'],
        'name': row['name'],
        'config': config,
        'isDefault': bool(row['is_default']),
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
    }


@watermark_bp.route('/watermark/templates', methods=['GET'])
@handle_errors
def list_watermark_templates():
    """获取水印模板列表

    查询参数:
    - keyword: 按 name 模糊搜索（可空）
    """
    keyword = request.args.get('keyword', '').strip()

    conn = get_connection()
    try:
        sql = "SELECT id, name, config, is_default, created_at, updated_at FROM watermark_templates WHERE 1=1"
        params = []
        if keyword:
            sql += " AND name LIKE ?"
            params.append(f'%{keyword}%')
        sql += " ORDER BY is_default DESC, id ASC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    items = [_row_to_dict(row) for row in rows]
    return success_response(data={'list': items, 'total': len(items)})


@watermark_bp.route('/watermark/templates', methods=['POST'])
@handle_errors
def create_watermark_template():
    """新增水印模板

    请求体:
    {
        "name": "默认模板",          // 必填，模板名称
        "config": {...},            // 必填，水印配置
        "isDefault": false          // 可空，是否设为默认
    }
    """
    data = request.get_json()
    if not data:
        return error_response("请求体不能为空")

    name = str(data.get('name') or '').strip()
    if not name:
        return error_response("模板名称不能为空")

    config = data.get('config')
    if config is None:
        return error_response("config 不能为空")

    config_str = json.dumps(config, ensure_ascii=False) if not isinstance(config, str) else config
    is_default = 1 if data.get('isDefault') else 0

    conn = get_connection()
    try:
        # 设为默认前，清除其他默认标记
        if is_default:
            conn.execute("UPDATE watermark_templates SET is_default = 0 WHERE is_default = 1")

        cur = conn.execute(
            """INSERT INTO watermark_templates (name, config, is_default, created_at, updated_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (name, config_str, is_default),
        )
        conn.commit()
        new_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, name, config, is_default, created_at, updated_at FROM watermark_templates WHERE id = ?",
            (new_id,),
        ).fetchone()
    finally:
        conn.close()

    return success_response(data=_row_to_dict(row), msg="水印模板创建成功")


@watermark_bp.route('/watermark/templates/<int:template_id>', methods=['PUT'])
@handle_errors
def update_watermark_template(template_id):
    """更新水印模板

    请求体（任一字段可选）:
    {
        "name": "新名称",
        "config": {...},
        "isDefault": true
    }
    """
    data = request.get_json()
    if not data:
        return error_response("请求体不能为空")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM watermark_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        if not row:
            return error_response("水印模板不存在", 404)

        set_clauses = []
        params = []

        name = str(data.get('name') or '').strip()
        if name:
            set_clauses.append("name = ?")
            params.append(name)

        config = data.get('config')
        if config is not None:
            config_str = json.dumps(config, ensure_ascii=False) if not isinstance(config, str) else config
            set_clauses.append("config = ?")
            params.append(config_str)

        if 'isDefault' in data:
            is_default = 1 if data['isDefault'] else 0
            # 设为默认前，清除其他默认标记
            if is_default:
                conn.execute("UPDATE watermark_templates SET is_default = 0 WHERE is_default = 1")
            set_clauses.append("is_default = ?")
            params.append(is_default)

        if not set_clauses:
            return error_response("无有效更新字段")

        set_clauses.append("updated_at = datetime('now')")
        params.append(template_id)
        conn.execute(
            f"UPDATE watermark_templates SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        conn.commit()

        row = conn.execute(
            "SELECT id, name, config, is_default, created_at, updated_at FROM watermark_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
    finally:
        conn.close()

    return success_response(data=_row_to_dict(row), msg="水印模板更新成功")


@watermark_bp.route('/watermark/templates/<int:template_id>', methods=['DELETE'])
@handle_errors
def delete_watermark_template(template_id):
    """删除水印模板"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM watermark_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        if not row:
            return error_response("水印模板不存在", 404)

        cur = conn.execute("DELETE FROM watermark_templates WHERE id = ?", (template_id,))
        conn.commit()
        deleted = cur.rowcount
    finally:
        conn.close()

    if deleted == 0:
        return error_response("删除失败", 500)

    return success_response(msg="水印模板已删除")
