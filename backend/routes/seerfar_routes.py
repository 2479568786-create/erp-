"""Seerfar Ozon selection proxy and import endpoints."""
from flask import Blueprint, request

from models.research import ResearchItem
from services.seerfar_client import SeerfarApiError, SeerfarConfigError
from services.seerfar_selection_service import SeerfarSelectionService
from utils.auth import require_admin, require_login
from utils.response import error_response, handle_errors, success_response


seerfar_bp = Blueprint('seerfar', __name__)


def _json_body():
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError('请求体必须为 JSON 对象')
    return data


def _service_error(exc):
    if isinstance(exc, SeerfarConfigError):
        return error_response('Seerfar API 未配置', 503)
    if isinstance(exc, SeerfarApiError):
        upstream_code = exc.status_code or exc.code
        messages = {
            401: 'Seerfar API Key 无效或已失效',
            403: 'Seerfar 账号或平台授权无权限',
            429: 'Seerfar 请求过于频繁',
            8000: 'Seerfar API 额度已耗尽',
        }
        message = messages.get(upstream_code, 'Seerfar 请求失败')
        return error_response(message, 502)
    raise exc


@seerfar_bp.route('/seerfar/quota', methods=['GET'])
@handle_errors
@require_login
def quota():
    try:
        return success_response(data=SeerfarSelectionService().quota())
    except (SeerfarApiError, SeerfarConfigError) as exc:
        return _service_error(exc)


@seerfar_bp.route('/seerfar/rankings', methods=['POST'])
@handle_errors
@require_login
def rankings():
    try:
        return success_response(data=SeerfarSelectionService().search_rankings(
            _json_body()
        ))
    except (SeerfarApiError, SeerfarConfigError) as exc:
        return _service_error(exc)


@seerfar_bp.route('/seerfar/overview', methods=['POST'])
@handle_errors
@require_login
def overview():
    try:
        return success_response(data=SeerfarSelectionService().overview_category_shares(
            _json_body()
        ))
    except (SeerfarApiError, SeerfarConfigError) as exc:
        return _service_error(exc)


@seerfar_bp.route('/seerfar/market-keywords', methods=['POST'])
@handle_errors
@require_login
def market_keywords():
    try:
        return success_response(data=SeerfarSelectionService().search_market_keywords(
            _json_body()
        ))
    except (SeerfarApiError, SeerfarConfigError) as exc:
        return _service_error(exc)


@seerfar_bp.route('/seerfar/shops/<shop_id>/products', methods=['POST'])
@handle_errors
@require_login
def shop_products(shop_id):
    try:
        if not str(shop_id).isdigit():
            return error_response('店铺 ID 必须为数字', 400)
        return success_response(data=SeerfarSelectionService().search_shop_detail(
            shop_id, _json_body()
        ))
    except (SeerfarApiError, SeerfarConfigError) as exc:
        return _service_error(exc)


@seerfar_bp.route('/seerfar/categories/<category_id>/products', methods=['POST'])
@handle_errors
@require_login
def category_products(category_id):
    try:
        return success_response(data=SeerfarSelectionService().search_category_products(
            category_id, _json_body()
        ))
    except (SeerfarApiError, SeerfarConfigError) as exc:
        return _service_error(exc)


@seerfar_bp.route('/seerfar/products/<sku>', methods=['POST'])
@handle_errors
@require_login
def product_detail(sku):
    try:
        date_range = _json_body().get(
            'dateRange', 'past_30_days'
        )
        return success_response(data=SeerfarSelectionService().product_detail(sku, date_range))
    except (SeerfarApiError, SeerfarConfigError) as exc:
        return _service_error(exc)


@seerfar_bp.route('/seerfar/products/<sku>/similar', methods=['POST'])
@handle_errors
@require_login
def similar_products(sku):
    try:
        return success_response(data=SeerfarSelectionService().similar_products(sku))
    except (SeerfarApiError, SeerfarConfigError) as exc:
        return _service_error(exc)


@seerfar_bp.route('/seerfar/import', methods=['POST'])
@handle_errors
@require_admin
def import_research_item():
    data = _json_body()
    platform = (data.get('platform') or 'ozon').strip().lower()
    source_id = str(data.get('source_id') or '').strip()
    if platform != 'ozon':
        return error_response('当前选品模块仅支持 Ozon')
    if not source_id or source_id in {'-', 'none', 'null'}:
        return error_response('Seerfar 商品缺少 source_id')
    if not (data.get('product_name') or '').strip():
        return error_response('商品名称不能为空')
    data['platform'] = 'ozon'
    data['source_id'] = source_id
    row, created = ResearchItem.create_from_seerfar(data)
    return success_response(
        data={'item': row, 'created': created},
        msg='已加入选品池' if created else '选品已在选品池中',
    )
