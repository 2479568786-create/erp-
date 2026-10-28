"""
GeekOzon ERP - 发布前校验（R6，dry-run）

`POST /api/products/{id}/validate` 的实现：
- 复用 `publish_service.build_ozon_product_items` 组装真实发布 payload（本地拼装，不调 Ozon）
- 用**本地类目缓存**（`ozon_category_attributes` / `ozon_attribute_dictionary_values`）校验必填与字典，
  不依赖 Ozon 在线接口（校验接口要稳定、可批量回放；在线口径另有实时接口）
- 图片检查：数量、格式、本地文件存在性（/uploads、/api/gallery/files），外链只做格式校验
- 错误码固定：MISSING_REQUIRED / MISSING_DICTIONARY / NO_IMAGE / BAD_IMAGE /
  DENSITY_OUT_OF_RANGE / OFFER_ID_TOO_LONG / PRICE_INVALID（+ PAYLOAD_BUILD_FAILED 兜底）
- 返回 payload_preview 供 Agent 比对，不写库、不调 Ozon 写接口
"""
import copy
import os
import re

from config import Config
from db import query
from models.product import Product
from services import publish_service
from services.agent_attributes_service import density_check

OFFER_ID_MAX = 50
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp')
GALLERY_PREFIX = '/api/gallery/files/'
UPLOAD_PREFIX = '/uploads/'


class AgentValidateError(ValueError):
    """商品不存在等入参问题（路由层 404/422）。"""


def _local_schema(description_category_id, type_id):
    """本地类目属性：{attr_id: row}。"""
    if not description_category_id or not type_id:
        return {}
    try:
        rows = query(
            """SELECT attribute_id, name, name_ru, name_zh, attr_type, is_required, dictionary_id
               FROM ozon_category_attributes
               WHERE description_category_id = ? AND type_id = ?""",
            (int(description_category_id), int(type_id)),
        )
    except Exception:
        return {}
    return {int(r['attribute_id']): dict(r) for r in rows}


def _local_dict_value_ids(attribute_id):
    try:
        rows = query(
            'SELECT value_id FROM ozon_attribute_dictionary_values WHERE attribute_id = ?',
            (int(attribute_id),),
        )
    except Exception:
        return set()
    return {int(r['value_id']) for r in rows if r['value_id'] is not None}


def _check_images(urls):
    """返回 (blocking, warnings, info)。"""
    blocking, warnings = [], []
    urls = [u for u in (urls or []) if isinstance(u, str) and u.strip()]
    bad = []
    for url in urls:
        path = url.split('?')[0]
        ext = os.path.splitext(path)[1].lower()
        if ext and ext not in IMAGE_EXTENSIONS:
            bad.append({'url': url, 'reason': '格式不支持'})
            continue
        if path.startswith(GALLERY_PREFIX):
            directory = getattr(__import__('routes.gallery_routes', fromlist=['GALLERY_DIR']), 'GALLERY_DIR')
            if not os.path.exists(os.path.join(directory, os.path.basename(path))):
                bad.append({'url': url, 'reason': '本地图库文件不存在'})
        elif path.startswith(UPLOAD_PREFIX):
            if not os.path.exists(os.path.join(Config.UPLOAD_DIR, os.path.basename(path))):
                bad.append({'url': url, 'reason': '本地 uploads 文件不存在'})
        elif not re.match(r'^https?://', path):
            bad.append({'url': url, 'reason': '非 http(s) 地址'})
    if not urls:
        blocking.append({'code': 'NO_IMAGE', 'field': 'images', 'message': '没有任何商品图片',
                         'fix': '先上传/绑定图片（R4 或图库）'})
    elif bad:
        blocking.append({'code': 'BAD_IMAGE', 'field': 'images',
                         'message': '%d 张图片不可用' % len(bad), 'fix': '重新绑定可用图片',
                         'detail': bad[:10]})
    return blocking, warnings, {'count': len(urls), 'bad': bad,
                                'primary': urls[0] if urls else None}


def _check_attributes(item, schema, required_missing, blocking, warnings, dict_cache):
    attrs = item.get('attributes') if isinstance(item.get('attributes'), list) else []
    seen = set()
    for attr in attrs:
        if not isinstance(attr, dict):
            continue
        attr_id = attr.get('id')
        if attr_id in (None, ''):
            continue
        try:
            attr_id = int(attr_id)
        except (TypeError, ValueError):
            continue
        seen.add(attr_id)
        schema_row = schema.get(attr_id)
        values = attr.get('values') if isinstance(attr.get('values'), list) else []
        if schema_row is None:
            warnings.append({'code': 'ATTR_NOT_IN_CATEGORY', 'field': 'attributes[%s]' % attr_id,
                             'message': '属性 %s 不在当前类目 schema 中，发布时可能被忽略' % attr_id})
            continue
        dictionary_id = int(schema_row.get('dictionary_id') or 0)
        if dictionary_id <= 0:
            if not values or all(not str(v.get('value') or '').strip() for v in values if isinstance(v, dict)):
                warnings.append({'code': 'ATTR_EMPTY', 'field': 'attributes[%s]' % attr_id,
                                 'message': '属性 %s 值为空' % (schema_row.get('name_ru') or attr_id)})
            continue
        if attr_id not in dict_cache:
            dict_cache[attr_id] = _local_dict_value_ids(attr_id)
        has_value_id = any(isinstance(v, dict) and v.get('dictionary_value_id') for v in values)
        has_text = any(isinstance(v, dict) and str(v.get('value') or '').strip() for v in values)
        if not has_value_id:
            entry = {
                'code': 'MISSING_DICTIONARY',
                'field': 'attributes[%s]' % attr_id,
                'message': '属性「%s」缺少 dictionary_value_id' % (schema_row.get('name_ru') or attr_id),
                'fix': '调用 PUT /api/products/{id}/attributes 解析字典值',
            }
            if schema_row.get('is_required'):
                blocking.append(entry)
            elif has_text and not dict_cache[attr_id]:
                warnings.append(entry)
    for attr_id in sorted(set(required_missing) - seen):
        row = schema.get(attr_id) or {}
        blocking.append({
            'code': 'MISSING_REQUIRED',
            'field': 'attributes[%s]' % attr_id,
            'message': '缺少必填属性「%s」' % (row.get('name_ru') or row.get('name_zh') or attr_id),
            'fix': '调用 PUT /api/products/{id}/attributes 写入',
        })
    return seen


def validate_product(product_id, store_id=None, dry_run=True):
    """按商品 id 校验（线上接口/库内商品）。"""
    product = Product.find_by_id(str(product_id))
    if not product:
        raise AgentValidateError('商品不存在或已删除')
    return validate_product_dict(product, store_id=store_id, dry_run=dry_run)


def validate_product_dict(product, store_id=None, dry_run=True):
    """按商品 dict 校验（离线回放用：可直接喂 products.json 里的对象，不读库）。"""
    if not isinstance(product, dict) or not product.get('id'):
        raise AgentValidateError('商品对象缺少 id')

    snapshot = copy.deepcopy(product)
    try:
        items = publish_service.build_ozon_product_items(snapshot, store_id=store_id)
    except Exception as exc:  # noqa: BLE001 - 拼装失败本身就是阻断项
        return {
            'ok': False,
            'dry_run': bool(dry_run),
            'blocking': [{'code': 'PAYLOAD_BUILD_FAILED', 'field': 'payload',
                          'message': '发布 payload 拼装失败：%s' % exc, 'fix': '检查商品字段/SKU 数据'}],
            'warnings': [{'code': 'LOCAL_SCHEMA_ONLY',
                          'message': '本接口使用本地类目缓存校验，不调用 Ozon 在线接口'}],
            'density': density_check(product),
            'images': {'count': 0, 'primary': None, 'bad': []},
            'payload_preview': None,
        }

    category_id = product.get('descriptionCategoryId')
    type_id = product.get('typeId')
    schema = _local_schema(category_id, type_id)
    required_missing = {attr_id for attr_id, row in schema.items() if row.get('is_required')}

    blocking = []
    warnings = []
    dict_cache = {}
    images_info = {'count': 0, 'primary': None, 'bad': []}

    if not category_id or not type_id:
        blocking.append({'code': 'MISSING_REQUIRED', 'field': 'category',
                         'message': '缺少 descriptionCategoryId 或 typeId',
                         'fix': '在商品编辑页选择 Ozon 三级类目'})
    elif not schema:
        warnings.append({'code': 'SCHEMA_NOT_CACHED',
                         'message': '本地缓存没有该类目属性（可能未同步），必填校验不完整'})

    for index, item in enumerate(items, start=1):
        prefix = 'item[%d]' % index if len(items) > 1 else ''
        image_blocking, image_warnings, info = _check_images(item.get('images'))
        for entry in image_blocking:
            entry['field'] = (prefix + '.' + entry['field']) if prefix else entry['field']
            blocking.append(entry)
        warnings.extend(image_warnings)
        if index == 1:
            images_info = info

        try:
            price = float(item.get('price') or 0)
        except (TypeError, ValueError):
            price = 0
        if price <= 0:
            blocking.append({'code': 'PRICE_INVALID',
                             'field': (prefix + '.price') if prefix else 'price',
                             'message': '价格必须大于 0', 'fix': '设置 price / old_price'})

        offer_id = str(item.get('offer_id') or '').strip()
        offer_bytes = len(offer_id.encode('utf-8'))
        if any(ord(ch) > 127 for ch in offer_id):
            blocking.append({'code': 'OFFER_ID_TOO_LONG',
                             'field': (prefix + '.offer_id') if prefix else 'offer_id',
                             'message': 'offer_id 含非 ASCII 字符（Ozon 只接受 ASCII，实测会报长度/格式错；'
                                        '当前字符 %d / UTF-8 字节 %d）' % (len(offer_id), offer_bytes),
                             'fix': '让系统按平台 SKU 生成 offer_id，或改成纯英文数字'})
        elif len(offer_id) > OFFER_ID_MAX or offer_bytes > OFFER_ID_MAX:
            blocking.append({'code': 'OFFER_ID_TOO_LONG',
                             'field': (prefix + '.offer_id') if prefix else 'offer_id',
                             'message': 'offer_id 长度超限（字符 %d / UTF-8 字节 %d，上限 %d）'
                                        % (len(offer_id), offer_bytes, OFFER_ID_MAX),
                             'fix': '缩短 offer_id 或让系统按平台 SKU 生成（建议纯 ASCII）'})
        if not offer_id:
            warnings.append({'code': 'OFFER_ID_EMPTY',
                             'message': 'offer_id 为空，发布时会按平台 SKU 生成'})

        _check_attributes(item, schema, required_missing, blocking, warnings, dict_cache)

        for field, label in (('weight', '重量'), ('width', '宽'), ('height', '高'), ('depth', '深')):
            try:
                value = float(item.get(field) or 0)
            except (TypeError, ValueError):
                value = 0
            if value <= 0 and label in ('重量', '宽', '高', '深'):
                warnings.append({'code': 'DIMENSION_MISSING',
                                 'message': '%s必须大于 0（当前 %s）' % (label, value)})

    density = density_check(product)
    if density.get('checked') and not density.get('ok'):
        blocking.append({
            'code': 'DENSITY_OUT_OF_RANGE',
            'field': 'density',
            'message': '密度 %.4f g/cm³ 超出合理范围 [0.001, 13.55]' % density['value'],
            'fix': '核对重量与长宽高单位（克 / 毫米）',
        })

    preview = None
    if items:
        preview = copy.deepcopy(items[0])
        if isinstance(preview.get('images'), list):
            preview['images'] = preview['images'][:3]
        preview['_items_total'] = len(items)

    return {
        'ok': not blocking,
        'dry_run': bool(dry_run),
        'blocking': blocking,
        'warnings': warnings,
        'density': density,
        'images': images_info,
        'payload_preview': preview,
        'items_count': len(items),
        'schema_source': 'local-cache',
    }
