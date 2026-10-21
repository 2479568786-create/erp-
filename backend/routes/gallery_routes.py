"""图库与 AI 图片生成 API。"""
import base64
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime

from flask import Blueprint, request, send_from_directory

from config import DATA_DIR, Config
from db import execute, query
from services.ai_config_service import get_ai_config
from utils.response import success_response, error_response, handle_errors


gallery_bp = Blueprint('gallery', __name__)
GALLERY_DIR = os.path.join(DATA_DIR, 'gallery')
os.makedirs(GALLERY_DIR, exist_ok=True)
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


def _create_asset(filename, original_name, size, source='upload', prompt='', tags=None, generation_meta=None):
    generation_meta = generation_meta or {}
    asset_id = execute(
        """INSERT INTO gallery_assets
           (filename, original_name, title, mime_type, file_size, source, prompt,
            provider, model, endpoint, reference_count, template_version,
            tags, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (filename, original_name, os.path.splitext(original_name)[0],
         f"image/{os.path.splitext(filename)[1].lower().lstrip('.').replace('jpg', 'jpeg')}",
         size, source, prompt,
         str(generation_meta.get('provider') or ''),
         str(generation_meta.get('model') or ''),
         str(generation_meta.get('endpoint') or ''),
         int(generation_meta.get('reference_count') or 0),
         str(generation_meta.get('template_version') or ''),
         json.dumps(tags or [], ensure_ascii=False), _now(), _now()),
    )
    return _row_to_dict(query('SELECT * FROM gallery_assets WHERE id = ?', (asset_id,), one=True))


def _save_bytes(data, original_name, source='upload', prompt='', tags=None, generation_meta=None):
    if len(data) > Config.MAX_IMAGE_SIZE:
        raise ValueError('图片超过 10MB 限制')
    ext = os.path.splitext(original_name)[1].lower().lstrip('.')
    if ext not in ALLOWED_EXTENSIONS:
        ext = 'png'
    filename = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(GALLERY_DIR, filename), 'wb') as file:
        file.write(data)
    return _create_asset(filename, _safe_name(original_name), len(data), source, prompt, tags, generation_meta)


def _extract_apiyi_image_items(result, count):
    message = ((result.get('choices') or [{}])[0].get('message') or {})
    content = message.get('content') or ''
    candidates = []

    def add(value):
        if not isinstance(value, str):
            return
        value = value.strip().rstrip('.,;')
        if value.startswith('data:image/'):
            candidates.append({'b64_json': value})
        elif value.startswith(('https://', 'http://')):
            candidates.append({'url': value})

    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            add(part.get('text'))
            add(part.get('url'))
            image_url = part.get('image_url')
            add(image_url.get('url') if isinstance(image_url, dict) else image_url)
    else:
        for match in re.finditer(r'!\[[^\]]*\]\((data:image/[^)]+|https?://[^)\s]+)\)', str(content)):
            add(match.group(1))
        if not candidates:
            add(str(content))

    unique = []
    seen = set()
    for item in candidates:
        key = item.get('url') or item.get('b64_json')
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:count]


def _build_apiyi_image_content(prompt, reference_images):
    content = [{'type': 'text', 'text': prompt}]
    content.extend({'type': 'image_url', 'image_url': {'url': image}} for image in reference_images)
    return content


def _build_image_edit_multipart(model, prompt, size, reference_image):
    """构造 OpenAI 图片编辑协议的 multipart 请求，确保参考图真正进入模型。"""
    boundary = f'----OzonImageEdit{uuid.uuid4().hex}'
    mime_header, encoded = reference_image.split(',', 1)
    mime_type = mime_header.split(';', 1)[0].replace('data:', '') or 'image/png'
    image_data = base64.b64decode(encoded)
    chunks = []

    def add_field(name, value):
        chunks.extend([
            f'--{boundary}\r\n'.encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode('utf-8'),
            b'\r\n',
        ])

    add_field('model', model)
    add_field('prompt', prompt)
    add_field('size', size)
    add_field('n', 1)
    extension = mime_type.split('/', 1)[-1].replace('jpeg', 'jpg')
    chunks.extend([
        f'--{boundary}\r\n'.encode(),
        f'Content-Disposition: form-data; name="image"; filename="reference.{extension}"\r\n'.encode(),
        f'Content-Type: {mime_type}\r\n\r\n'.encode(),
        image_data,
        b'\r\n',
        f'--{boundary}--\r\n'.encode(),
    ])
    return b''.join(chunks), f'multipart/form-data; boundary={boundary}'


@gallery_bp.route('/gallery', methods=['GET'])
@handle_errors
def list_gallery():
    keyword = request.args.get('keyword', '').strip()
    source = request.args.get('source', '').strip()
    favorite = request.args.get('favorite', '').strip()
    conditions, params = [], []
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
    generation_meta = {
        'provider': request.form.get('provider', '').strip(),
        'model': request.form.get('model', '').strip(),
        'endpoint': request.form.get('endpoint', '').strip(),
        'reference_count': request.form.get('reference_count', '0').strip() or 0,
        'template_version': request.form.get('template_version', '').strip(),
    }
    assets = []
    for uploaded in files[:30]:
        ext = os.path.splitext(uploaded.filename or '')[1].lower().lstrip('.')
        if ext not in ALLOWED_EXTENSIONS:
            return error_response(f'不支持的图片格式: {uploaded.filename}', 400)
        assets.append(_save_bytes(uploaded.read(), uploaded.filename, source=source, prompt=prompt, tags=tags, generation_meta=generation_meta))
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
    execute(f"UPDATE gallery_assets SET {', '.join(sets)} WHERE id = ?", list(allowed.values()) + [_now(), asset_id])
    row = query('SELECT * FROM gallery_assets WHERE id = ?', (asset_id,), one=True)
    return success_response(data=_row_to_dict(row), msg='素材已更新') if row else error_response('素材不存在', 404)


@gallery_bp.route('/gallery/<int:asset_id>', methods=['DELETE'])
@handle_errors
def delete_gallery(asset_id):
    row = query('SELECT * FROM gallery_assets WHERE id = ?', (asset_id,), one=True)
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
    execute('DELETE FROM gallery_assets WHERE id = ?', (asset_id,))
    return success_response(msg='素材已删除')


@gallery_bp.route('/gallery/files/<path:filename>', methods=['GET'])
def gallery_file(filename):
    return send_from_directory(GALLERY_DIR, os.path.basename(filename))


@gallery_bp.route('/ai/images/generate', methods=['POST'])
@handle_errors
def generate_ai_images():
    body = request.get_json(silent=True) or {}
    prompt = str(body.get('prompt') or '').strip()
    if not prompt:
        return error_response('请输入图片描述', 400)
    count = min(max(int(body.get('count') or 1), 1), 4)
    size = body.get('size') if body.get('size') in ('1024x1024', '1536x1024', '1024x1360', '1024x1365', '1024x1536') else '1024x1024'
    size = '1024x1360' if size == '1024x1365' else size
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
    provider_key = str(body.get('provider') or '').strip() or None
    config = get_ai_config(provider_key)
    if not config.get('api_key') or not config.get('base_url'):
        return error_response('请先在模型管理配置支持图片生成的 API', 400)
    model = str(body.get('model') or 'gpt-image-1').strip()
    is_apiyi_image = config.get('provider') == 'apiyi' or model in {'gpt-image-2.5-all', 'gpt-image-2-all'}
    is_apiyi_banana = config.get('provider') == 'apiyi_banana'
    is_yyds_image = config.get('provider') == 'yyds'
    is_lingkeai = config.get('provider') == 'lingkeai'
    if is_apiyi_banana:
        ratio = {'1024x1360': '3:4', '1024x1365': '3:4', '1024x1536': '2:3', '1536x1024': '3:2'}.get(size, '1:1')
        parts = [{'text': prompt}]
        for current_reference in (reference_images or ([reference_image] if reference_image else [])):
            mime_type, image_data = current_reference.split(',', 1)
            parts.append({'inlineData': {'mimeType': mime_type.split(';', 1)[0].replace('data:', ''), 'data': image_data}})
        payload = {
            'contents': [{'parts': parts}],
            'generationConfig': {
                'responseModalities': ['IMAGE'],
                'imageConfig': {'aspectRatio': ratio, 'imageSize': '1K'},
            },
        }
        endpoint = config['base_url'].rstrip('/') + f'/v1beta/models/{model}:generateContent'
    elif is_apiyi_image:
        message_content = _build_apiyi_image_content(
            prompt,
            reference_images or ([reference_image] if reference_image else []),
        )
        payload = {
            'model': model,
            'messages': [{'role': 'user', 'content': message_content}],
        }
        endpoint = config['base_url'].rstrip('/') + '/chat/completions'
    elif is_yyds_image and reference_image:
        yyds_size = {'1024x1360': '1008x1344', '1024x1365': '1008x1344'}.get(size, size)
        payload = {
            'model': model,
            'prompt': prompt,
            'size': yyds_size,
            'images': [{'image_url': reference_image}],
            'response_format': 'url',
        }
        endpoint = config['base_url'].rstrip('/') + '/images/edits'
    elif is_lingkeai and reference_image:
        payload, content_type = _build_image_edit_multipart(model, prompt, size, reference_image)
        endpoint = config['base_url'].rstrip('/') + '/images/edits'
    else:
        payload = {'model': model, 'prompt': prompt, 'n': count, 'size': size}
        endpoint = config['base_url'].rstrip('/') + '/images/generations'
    generation_meta = {
        'provider': config.get('provider') or provider_key or '',
        'model': model,
        'endpoint': endpoint,
        'reference_count': len(reference_images or ([reference_image] if reference_image else [])),
        'template_version': str(body.get('template_version') or 'single-v2'),
    }
    if is_lingkeai and reference_image:
        request_body = payload
        request_content_type = content_type
    else:
        request_body = json.dumps(payload).encode('utf-8')
        request_content_type = 'application/json'
    req = urllib.request.Request(
        endpoint,
        data=request_body,
        headers={'Content-Type': request_content_type, 'Authorization': f"Bearer {config['api_key']}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:500]
        return error_response(f'图片模型调用失败 HTTP {exc.code}: {detail}', 502)
    except urllib.error.URLError as exc:
        return error_response(f'图片模型连接失败: {exc.reason}', 502)

    if is_apiyi_banana:
        result_items = []
        for candidate in result.get('candidates') or []:
            content = candidate.get('content') or {}
            for part in content.get('parts') or []:
                inline_data = part.get('inlineData') or {}
                if inline_data.get('data'):
                    result_items.append({'b64_json': inline_data['data']})
    elif is_apiyi_image:
        result_items = _extract_apiyi_image_items(result, count)
        if not result_items:
            choices = result.get('choices') or []
            message = (choices[0].get('message') or {}) if choices else {}
            content = message.get('content') or ''
            print(
                '[AI图片] APIYI响应未识别到图片'
                f" model={model} top_keys={sorted(result.keys())}"
                f" choice_count={len(choices)} content_type={type(content).__name__}"
                f" content_length={len(content) if isinstance(content, (str, list)) else 0}"
                f" finish_reason={(choices[0].get('finish_reason') if choices else None)}"
            )
    else:
        result_items = result.get('data') or []

    assets = []
    save_errors = []
    for index, item in enumerate(result_items):
        try:
            if item.get('b64_json'):
                encoded = item['b64_json']
                if isinstance(encoded, str) and ',' in encoded and encoded.startswith('data:'):
                    encoded = encoded.split(',', 1)[1]
                image_data = base64.b64decode(encoded)
            elif item.get('url'):
                image_request = urllib.request.Request(
                    item['url'],
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                with urllib.request.urlopen(image_request, timeout=60) as response:
                    image_data = response.read(Config.MAX_IMAGE_SIZE + 1)
            else:
                continue
            assets.append(_save_bytes(image_data, f'ai-{index + 1}.png', 'ai', prompt, ['AI生成'], generation_meta))
        except Exception as exc:
            save_errors.append(str(exc)[:180])
            print(f'[AI图片] 保存第 {index + 1} 张失败: {exc}')
    if not assets:
        detail = f'；下载/保存错误: {save_errors[-1]}' if save_errors else ''
        return error_response(f'模型未返回可保存的图片{detail}', 502)
    return success_response(data={'assets': assets, 'model': model}, msg=f'已生成并保存 {len(assets)} 张图片')
