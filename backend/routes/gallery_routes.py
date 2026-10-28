"""图库与 AI 图片生成 API。

生图只走凌科 AI（JLC / banana-2.1）：apiyi / apiyi_banana / yyds / PixPix 已于 2026-10-28 下线。
计费闸门与扣点由 services/image_billing_guard.py 在应用层统一处理（路由内不扣点，避免重复扣费）。
"""
import json
import os
import re
import urllib.request
import uuid
from datetime import datetime

from flask import Blueprint, request, send_from_directory

from config import DATA_DIR, Config
from db import execute, query
from services import jlc_media_service
from utils.response import success_response, error_response, handle_errors
from utils.auth import get_current_user
from services.watermark_service import prepare_generated_image, validate_generation_store
from services.watermark_image import image_extension


gallery_bp = Blueprint('gallery', __name__)
GALLERY_DIR = os.path.join(DATA_DIR, 'gallery')
os.makedirs(GALLERY_DIR, exist_ok=True)
# The gallery is persistent data and may have been restored from an archive with
# file-like permissions (for example 0640). A directory needs the execute bit
# for its owner to create and open files inside it.
try:
    os.chmod(GALLERY_DIR, 0o750)
except OSError:
    pass
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _safe_name(name):
    name = re.sub(r'[^\w\-. ]+', '_', str(name or ''), flags=re.UNICODE).strip(' ._')
    return name[:120] or 'image'


def _row_to_dict(row):
    item = dict(row)
    item['id'] = str(item['id'])
    item['favorite'] = bool(item.get('favorite'))
    try:
        item['tags'] = json.loads(item.get('tags') or '[]')
    except (TypeError, ValueError):
        item['tags'] = []
    item['url'] = f"/api/gallery/files/{item['filename']}"
    return item


def _folder_to_dict(row):
    item = dict(row)
    item['id'] = str(item['id'])
    item['asset_count'] = int(item.get('asset_count') or 0)
    return item


def _create_asset(filename, original_name, size, source='upload', prompt='', tags=None, user_id=None,
                  provider='', model='', endpoint='', reference_count=0, template_version='',
                  product_id=None, module_key=None, request_id=None, agent_source=None):
    user_id = user_id or (get_current_user() or {}).get('id')
    if not user_id:
        raise ValueError('无法确定图库所属账号，请重新登录')
    asset_id = execute(
        """INSERT INTO gallery_assets
           (user_id, filename, original_name, title, mime_type, file_size, source, prompt, tags,
            provider, model, endpoint, reference_count, template_version,
            product_id, module_key, request_id, agent_source, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, filename, original_name, os.path.splitext(original_name)[0],
         f"image/{os.path.splitext(filename)[1].lower().lstrip('.').replace('jpg', 'jpeg')}",
         size, source, prompt, json.dumps(tags or [], ensure_ascii=False),
         provider or '', model or '', endpoint or '', int(reference_count or 0),
         str(template_version or ''),
         (str(product_id) if product_id else None),
         (str(module_key) if module_key else None),
         (str(request_id) if request_id else None),
         (str(agent_source) if agent_source else None),
         _now(), _now()),
    )
    return _row_to_dict(query('SELECT * FROM gallery_assets WHERE id = ?', (asset_id,), one=True))


def _save_bytes(data, original_name, source='upload', prompt='', tags=None,
                provider='', model='', endpoint='', reference_count=0, template_version='',
                product_id=None, module_key=None, request_id=None, agent_source=None,
                user_id=None):
    if len(data) > Config.MAX_IMAGE_SIZE:
        raise ValueError('图片超过 10MB 限制')
    ext = os.path.splitext(original_name)[1].lower().lstrip('.')
    if ext not in ALLOWED_EXTENSIONS:
        ext = 'png'
    filename = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(GALLERY_DIR, filename), 'wb') as file:
        file.write(data)
    return _create_asset(filename, _safe_name(original_name), len(data), source, prompt, tags,
                         provider=provider, model=model, endpoint=endpoint,
                         reference_count=reference_count, template_version=template_version,
                         product_id=product_id, module_key=module_key,
                         request_id=request_id, agent_source=agent_source,
                         user_id=user_id)


@gallery_bp.route('/gallery', methods=['GET'])
@handle_errors
def list_gallery():
    keyword = request.args.get('keyword', '').strip()
    source = request.args.get('source', '').strip()
    favorite = request.args.get('favorite', '').strip()
    product_id = request.args.get('product_id', '').strip()
    module_key = request.args.get('module_key', '').strip()
    request_id = request.args.get('request_id', '').strip()
    try:
        from utils.agent_auth import current_agent
        agent = current_agent()
    except Exception:
        agent = None
    user_id = (get_current_user() or {}).get('id')
    if agent:
        # Agent 令牌：按 product_id/module_key/request_id 精确查询（未带过滤条件时只看套图产物）
        conditions, params = [], []
        if not (product_id or module_key or request_id or source):
            conditions.append("agent_source = 'agent-suite'")
    else:
        if not user_id:
            return error_response('请先登录', 401)
        conditions, params = ['user_id = ?'], [user_id]
    if product_id:
        conditions.append('product_id = ?')
        params.append(product_id)
    if module_key:
        conditions.append('module_key = ?')
        params.append(module_key)
    if request_id:
        conditions.append('request_id = ?')
        params.append(request_id)
    if keyword:
        conditions.append('(title LIKE ? OR original_name LIKE ? OR tags LIKE ? OR prompt LIKE ?)')
        params.extend([f'%{keyword}%'] * 4)
    if source:
        conditions.append('source = ?')
        params.append(source)
    if favorite in ('1', 'true'):
        conditions.append('favorite = 1')
    where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
    rows = query(f'SELECT * FROM gallery_assets{where} ORDER BY created_at DESC', params)
    return success_response(data={'list': [_row_to_dict(row) for row in rows], 'total': len(rows)})


@gallery_bp.route('/gallery/folders', methods=['GET'])
@handle_errors
def list_gallery_folders():
    user_id = (get_current_user() or {}).get('id')
    if not user_id:
        return error_response('请先登录', 401)
    rows = query(
        """SELECT f.*, COUNT(fa.asset_id) AS asset_count
           FROM gallery_folders f
           LEFT JOIN gallery_folder_assets fa ON fa.folder_id = f.id
           WHERE f.user_id = ?
           GROUP BY f.id
           ORDER BY f.created_at DESC""",
        (user_id,),
    )
    return success_response(data={'list': [_folder_to_dict(row) for row in rows], 'total': len(rows)})


@gallery_bp.route('/gallery/folders', methods=['POST'])
@handle_errors
def create_gallery_folder():
    user_id = (get_current_user() or {}).get('id')
    if not user_id:
        return error_response('请先登录', 401)
    body = request.get_json(silent=True) or {}
    raw_name = str(body.get('name') or '').strip()
    name = _safe_name(raw_name)
    asset_ids = body.get('asset_ids')
    if not raw_name or not name:
        return error_response('请输入套图文件夹名称', 400)
    if not isinstance(asset_ids, list) or not asset_ids:
        return error_response('请选择至少一张套图图片', 400)
    try:
        asset_ids = list(dict.fromkeys(int(asset_id) for asset_id in asset_ids))
    except (TypeError, ValueError):
        return error_response('套图图片标识不正确', 400)
    placeholders = ','.join('?' for _ in asset_ids)
    owned = query(
        f'SELECT id FROM gallery_assets WHERE user_id = ? AND id IN ({placeholders})',
        [user_id, *asset_ids],
    )
    owned_ids = {int(row['id']) for row in owned}
    if len(owned_ids) != len(asset_ids):
        return error_response('套图中包含不存在或无权访问的图片', 403)
    folder_id = execute(
        'INSERT INTO gallery_folders (user_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)',
        (user_id, name, _now(), _now()),
    )
    for position, asset_id in enumerate(asset_ids):
        execute(
            'INSERT INTO gallery_folder_assets (folder_id, asset_id, position) VALUES (?, ?, ?)',
            (folder_id, asset_id, position),
        )
    folder = query(
        'SELECT f.*, COUNT(fa.asset_id) AS asset_count FROM gallery_folders f '
        'LEFT JOIN gallery_folder_assets fa ON fa.folder_id = f.id WHERE f.id = ? GROUP BY f.id',
        (folder_id,), one=True,
    )
    return success_response(data=_folder_to_dict(folder), msg='套图文件夹已保存')


@gallery_bp.route('/gallery/folders/<int:folder_id>', methods=['GET'])
@handle_errors
def get_gallery_folder(folder_id):
    user_id = (get_current_user() or {}).get('id')
    if not user_id:
        return error_response('请先登录', 401)
    folder = query('SELECT * FROM gallery_folders WHERE id = ? AND user_id = ?', (folder_id, user_id), one=True)
    if not folder:
        return error_response('套图文件夹不存在', 404)
    assets = query(
        """SELECT a.* FROM gallery_folder_assets fa
           JOIN gallery_assets a ON a.id = fa.asset_id
           WHERE fa.folder_id = ? AND a.user_id = ?
           ORDER BY fa.position ASC, a.created_at ASC""",
        (folder_id, user_id),
    )
    data = _folder_to_dict({**folder, 'asset_count': len(assets)})
    data['assets'] = [_row_to_dict(row) for row in assets]
    return success_response(data=data)


@gallery_bp.route('/gallery/upload', methods=['POST'])
@handle_errors
def upload_gallery():
    files = request.files.getlist('files') or ([request.files['file']] if 'file' in request.files else [])
    if not files:
        return error_response('请选择图片文件', 400)
    tags = [tag.strip() for tag in request.form.get('tags', '').split(',') if tag.strip()]
    source = request.form.get('source', 'upload').strip()
    source = source if source in ('upload', 'ai') else 'upload'
    prompt = request.form.get('prompt', '').strip()[:10000]
    assets = []
    for uploaded in files[:30]:
        ext = os.path.splitext(uploaded.filename or '')[1].lower().lstrip('.')
        if ext not in ALLOWED_EXTENSIONS:
            return error_response(f'不支持的图片格式: {uploaded.filename}', 400)
        assets.append(_save_bytes(uploaded.read(), uploaded.filename, source=source, prompt=prompt, tags=tags))
    return success_response(data=assets, msg=f'已上传 {len(assets)} 张图片')


@gallery_bp.route('/gallery/<int:asset_id>', methods=['PUT'])
@handle_errors
def update_gallery(asset_id):
    body = request.get_json(silent=True) or {}
    allowed = {}
    if 'title' in body:
        allowed['title'] = _safe_name(body['title'])
    if 'favorite' in body:
        allowed['favorite'] = 1 if body['favorite'] else 0
    if 'tags' in body:
        allowed['tags'] = json.dumps([str(x).strip() for x in body['tags'] if str(x).strip()], ensure_ascii=False)
    if not allowed:
        return error_response('没有可更新字段', 400)
    sets = [f'{key} = ?' for key in allowed] + ['updated_at = ?']
    user_id = (get_current_user() or {}).get('id')
    execute(f"UPDATE gallery_assets SET {', '.join(sets)} WHERE id = ? AND user_id = ?", list(allowed.values()) + [_now(), asset_id, user_id])
    row = query('SELECT * FROM gallery_assets WHERE id = ? AND user_id = ?', (asset_id, user_id), one=True)
    return success_response(data=_row_to_dict(row), msg='素材已更新') if row else error_response('素材不存在', 404)


@gallery_bp.route('/gallery/<int:asset_id>', methods=['DELETE'])
@handle_errors
def delete_gallery(asset_id):
    user_id = (get_current_user() or {}).get('id')
    row = query('SELECT * FROM gallery_assets WHERE id = ? AND user_id = ?', (asset_id, user_id), one=True)
    if not row:
        return error_response('素材不存在', 404)
    path = os.path.join(GALLERY_DIR, row['filename'])
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError as exc:
            # Windows may briefly lock a file that was just previewed/downloaded.
            # Metadata deletion should still succeed; orphan cleanup can remove it later.
            print(f'[图库] 文件暂时无法删除 {path}: {exc}')
    execute('DELETE FROM gallery_assets WHERE id = ? AND user_id = ?', (asset_id, user_id))
    return success_response(msg='素材已删除')


@gallery_bp.route('/gallery/files/<path:filename>', methods=['GET'])
def gallery_file(filename):
    return send_from_directory(GALLERY_DIR, os.path.basename(filename))


JLC_MODEL = 'banana-2.1'


def _resolve_image_model(requested):
    """把前端选的模型解析成配置里可用的模型 id；未配置/未启用则回退默认。"""
    requested = str(requested or '').strip()
    try:
        from services.ai_config_service import get_image_models
        allowed = {item['id'] for item in get_image_models('lingkeai') if item.get('enabled')}
    except Exception:
        allowed = set()
    if requested and requested in allowed:
        return requested
    return JLC_MODEL


JLC_ASPECT = {
    '1024x1024': '1:1',
    '1536x1024': '3:2',
    '1024x1360': '3:4',
    '1024x1365': '3:4',
    '1024x1536': '2:3',
}


def _download_generated_image(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read(Config.MAX_IMAGE_SIZE + 1)


@gallery_bp.route('/ai/images/generate', methods=['POST'])
@handle_errors
def generate_ai_images():
    """单张/多张生图：只走凌科 AI（JLC / banana-2.1）。

    2026-10-27 用户拍板：生图只认凌科 AI，apiyi / apiyi_banana / yyds / PixPix 已下线；
    请求里的 provider / model 参数不再生效。
    计费闸门与扣点由 services/image_billing_guard.py 在应用层统一处理。
    """
    body = request.get_json(silent=True) or {}
    try:
        store_id = int(body.get('store_id') or 0)
    except (TypeError, ValueError):
        store_id = 0
    if store_id > 0:
        validate_generation_store(store_id, get_current_user())
    prompt = str(body.get('prompt') or '').strip()
    if not prompt:
        return error_response('请输入图片描述', 400)
    try:
        count = min(max(int(body.get('count') or 1), 1), 4)
    except (TypeError, ValueError):
        count = 1
    size = body.get('size') if body.get('size') in JLC_ASPECT else '1024x1024'
    reference_image = body.get('reference_image')
    reference_images = body.get('reference_images')
    if reference_images is not None:
        if not isinstance(reference_images, list) or len(reference_images) > 6:
            return error_response('最多上传 6 张参考图', 400)
        if any(not isinstance(item, str) or not item.startswith('data:image/') for item in reference_images):
            return error_response('参考图格式不正确，请重新上传图片', 400)
        if any(len(item) > 14 * 1024 * 1024 for item in reference_images):
            return error_response('单张参考图不能超过 10MB', 400)
        reference_image = reference_images[0] if reference_images else None
    elif reference_image and (not isinstance(reference_image, str) or not reference_image.startswith('data:image/')):
        return error_response('参考图格式不正确，请重新上传图片', 400)
    if reference_image and len(reference_image) > 14 * 1024 * 1024:
        return error_response('参考图不能超过 10MB', 400)

    images = reference_images or ([reference_image] if reference_image else [])
    image_size = str(body.get('imageSize') or '2K').strip() or '2K'
    template_version = str(body.get('template_version') or 'single-jlc-v1')
    selected_model = _resolve_image_model(body.get('model'))
    assets = []
    errors = []
    model_used = selected_model
    watermark_status = 'not_configured'
    for index in range(count):
        try:
            result = jlc_media_service.generate_image(
                prompt,
                aspect_ratio=JLC_ASPECT.get(size, '3:4'),
                image_size=image_size,
                images=images,
                model=selected_model,
            )
        except Exception as exc:  # noqa: BLE001 - 统一回可读错误
            errors.append(str(exc)[:180])
            break
        model_used = result.get('model') or selected_model
        for url_index, url in enumerate(result.get('urls') or [], start=1):
            try:
                image_data = _download_generated_image(url)
                if store_id > 0:
                    image_data, watermark_status = prepare_generated_image(
                        image_data, store_id, get_current_user())
                extension = image_extension(image_data)
                asset = _save_bytes(
                    image_data,
                    'ai-%d-%d.%s' % (index + 1, url_index, extension),
                    'ai',
                    prompt,
                    ['AI生成'],
                    provider='jlc',
                    model=model_used,
                    endpoint='/v1/media/generate',
                    reference_count=len(images),
                    template_version=template_version,
                )
                asset['watermark_status'] = watermark_status
                assets.append(asset)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc)[:180])
                print('[JLC单张] 下载/保存失败 %s: %s' % (url, exc))

    if not assets:
        detail = ('：' + errors[-1]) if errors else ''
        return error_response('凌科 AI 生图失败' + detail, 502)
    return success_response(
        data={'assets': assets, 'provider': 'jlc', 'model': model_used,
              'watermark_status': watermark_status},
        msg='已生成并保存 %d 张图片' % len(assets),
    )


# ============================================================
# JLC GLOBAL 生图（套图）—— 人工提示词 + 参考图，套图唯一出图入口
# ============================================================
@gallery_bp.route('/ai/suite/jlc', methods=['POST'])
@handle_errors
def generate_jlc_suite_image():
    """按人工提示词 + 参考图生成单张套图并保存到图库（只走凌科 AI）。"""
    body = request.get_json(silent=True) or {}
    prompt = (body.get('prompt') or '').strip()
    if not prompt:
        return error_response('缺少提示词')
    aspect_ratio = str(body.get('aspectRatio') or '3:4').strip()
    image_size = str(body.get('imageSize') or '2K').strip()
    module_key = str(body.get('moduleKey') or '').strip()
    selected_model = _resolve_image_model(body.get('model'))
    images = body.get('images') or []
    if not isinstance(images, list):
        return error_response('images 必须是数组')
    try:
        store_id = int(body.get('store_id') or 0)
    except (TypeError, ValueError):
        store_id = 0
    if store_id > 0:
        validate_generation_store(store_id, get_current_user())

    try:
        result = jlc_media_service.generate_image(
            prompt, aspect_ratio=aspect_ratio, image_size=image_size, images=images,
            model=selected_model)
    except Exception as exc:  # noqa: BLE001 - 统一回可读错误
        return error_response('JLC 生图失败：%s' % exc)

    assets = []
    watermark_status = 'not_configured'
    for index, url in enumerate(result.get('urls') or [], start=1):
        try:
            data = _download_generated_image(url)
            if store_id > 0:
                data, watermark_status = prepare_generated_image(data, store_id, get_current_user())
            extension = image_extension(data)
            asset = _save_bytes(
                data,
                'ai-suite-%s-%d.%s' % (module_key or 'img', index, extension),
                'ai',
                prompt,
                ['AI套图'] + ([module_key] if module_key else []),
                provider='jlc',
                model=result.get('model') or selected_model,
                endpoint='/v1/media/generate',
                reference_count=len(images),
                template_version='suite-manual-%s' % (module_key or 'img'),
            )
            asset['watermark_status'] = watermark_status
            assets.append(asset)
        except Exception as exc:  # noqa: BLE001
            print('[JLC套图] 下载/保存失败 %s: %s' % (url, exc))

    if not assets:
        return error_response('JLC 已出图但保存失败，请查看服务日志')
    return success_response(data={
        'assets': assets,
        'taskId': result.get('task_id'),
        'cost': result.get('cost'),
        'provider': 'jlc',
        'model': result.get('model'),
        'watermark_status': watermark_status,
    })


