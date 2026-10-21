"""PixPix OAuth MCP endpoints for the AI image workspace."""
import base64
import json
import os
import urllib.request

from flask import Blueprint, redirect, request

from services.pixpix_mcp_service import PixPixMcpService
from routes.gallery_routes import _save_bytes
from utils.auth import get_current_user, require_login
from utils.response import error_response, handle_errors, success_response


pixpix_bp = Blueprint('pixpix', __name__)


def _redirect_uri():
    base_url = os.environ.get('PIXPIX_REDIRECT_BASE_URL', '').strip()
    if not base_url:
        base_url = request.url_root.rstrip('/')
    return f"{base_url.rstrip('/')}/api/pixpix/oauth/callback"


def _service_for_current_user():
    user = get_current_user()
    return PixPixMcpService(user['id'])


@pixpix_bp.route('/pixpix/status', methods=['GET'])
@require_login
@handle_errors
def pixpix_status():
    return success_response(_service_for_current_user().get_public_status())


@pixpix_bp.route('/pixpix/oauth/start', methods=['POST'])
@require_login
@handle_errors
def start_pixpix_oauth():
    authorization_url = _service_for_current_user().begin_authorization(_redirect_uri())
    return success_response({'authorizationUrl': authorization_url}, msg='请在新标签页完成 PixPix 授权')


@pixpix_bp.route('/pixpix/oauth/callback', methods=['GET'])
def pixpix_oauth_callback():
    error = request.args.get('error')
    if error:
        return error_response(f'PixPix 授权未完成: {error}', 400)
    try:
        state = request.args.get('state', '')
        account_id = db.query('SELECT account_id FROM pixpix_oauth_accounts WHERE oauth_state = ?', (state,), one=True)
        if not account_id:
            raise ValueError('invalid OAuth state')
        PixPixMcpService(account_id['account_id']).complete_authorization(request.args.get('code', ''), state, _redirect_uri())
    except ValueError as exc:
        return error_response(str(exc), 400)
    return redirect('/#/ai-image?pixpix=connected')


@pixpix_bp.route('/pixpix/tools', methods=['GET'])
@require_login
@handle_errors
def pixpix_tools():
    service = _service_for_current_user()
    return success_response(service.list_image_tools())


@pixpix_bp.route('/pixpix/disconnect', methods=['POST'])
@require_login
@handle_errors
def disconnect_pixpix():
    _service_for_current_user().disconnect()
    return success_response(msg='PixPix 已断开连接')


def _image_bytes_from_content(content):
    for item in content or []:
        if not isinstance(item, dict):
            continue
        if item.get('data'):
            try:
                return base64.b64decode(item['data'], validate=True), item.get('mimeType', 'image/png')
            except (ValueError, TypeError):
                continue
        text = item.get('text')
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        url = payload.get('url') or payload.get('image_url')
        if isinstance(url, str) and url.startswith('https://'):
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read(10 * 1024 * 1024 + 1)
                if len(data) > 10 * 1024 * 1024:
                    raise ValueError('PixPix 返回的图片超过 10MB 限制')
                return data, response.headers.get_content_type()
    return None, None


@pixpix_bp.route('/pixpix/images/generate', methods=['POST'])
@require_login
@handle_errors
def generate_pixpix_images():
    body = request.get_json(silent=True) or {}
    tool = str(body.get('tool') or '').strip()
    arguments = body.get('arguments') or {}
    prompt = str(arguments.get('prompt') or '').strip()
    if not tool:
        return error_response('请选择 PixPix 图片工具', 400)
    service = _service_for_current_user()
    tools = {item.get('name') for item in service.get_cached_image_tools()}
    if tool not in tools:
        return error_response('请选择 PixPix 当前可用的图片工具', 400)
    result = service.call_tool(tool, arguments)
    image_data, mime_type = _image_bytes_from_content(result.get('content'))
    if not image_data:
        return error_response('PixPix 工具未返回可保存的图片', 502)
    extension = {'image/jpeg': 'jpg', 'image/webp': 'webp'}.get(mime_type, 'png')
    asset = _save_bytes(image_data, f'pixpix-{tool}.{extension}', 'pixpix', prompt, ['AI生成', 'PixPix'])
    return success_response({'assets': [asset], 'tool': tool}, msg='PixPix 图片已保存到图库')
