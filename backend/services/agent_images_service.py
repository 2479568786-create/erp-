"""
GeekOzon ERP - Agent 图片绑定服务（R4）

把图库资产按顺序绑到某个 SKU 的图片位，并同步商品主图：
- skus[sku_index].images = 有序 URL 数组；skus[sku_index].image = 首张（封面）
- product.images = 所有 SKU 图片并集（保序去重）；mode=replace 时清掉不在 SKU 里的遗留主图
- add_to_detail=true 时同步 product.detailImages（保序去重）
URL 沿用图库返回的 `/api/gallery/files/<filename>`，发布时 publish_service 会转成公网地址。
"""
from db import query
from models.product import Product

MAX_ATTACH_IMAGES = 30


class AgentImageError(ValueError):
    """入参/资源不合法（路由层映射 422/404）。"""


def _asset_url(filename):
    return f'/api/gallery/files/{filename}'


def _load_assets(asset_ids, user=None):
    ids = []
    for raw in asset_ids:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            raise AgentImageError(f'asset_ids 含非法值：{raw!r}')
    if not ids:
        return []
    placeholders = ','.join('?' * len(ids))
    rows = query(f"SELECT * FROM gallery_assets WHERE id IN ({placeholders})", tuple(ids))
    by_id = {int(r['id']): r for r in rows}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise AgentImageError(f'图库资产不存在：{missing}')
    if user and user.get('role') != 'admin':
        forbidden = [i for i in ids if by_id[i].get('user_id') not in (None, user.get('id'))]
        if forbidden:
            raise AgentImageError(f'无权限使用这些图库资产：{forbidden}')
    return [by_id[i] for i in ids]


def _dedupe(items):
    seen = set()
    out = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def attach_images(product_id, asset_ids, mode='replace', sku_index=0, add_to_detail=False, user=None):
    if not isinstance(asset_ids, (list, tuple)):
        raise AgentImageError('asset_ids 必须是数组')
    mode = (mode or 'replace').lower()
    if mode not in ('replace', 'append'):
        raise AgentImageError("mode 只支持 replace 或 append")
    try:
        sku_index = int(sku_index)
    except (TypeError, ValueError):
        raise AgentImageError('sku_index 必须是整数')
    if sku_index < 0:
        raise AgentImageError('sku_index 不能为负')
    if not asset_ids:
        raise AgentImageError('asset_ids 不能为空（replace 至少 1 张图）')
    if len(asset_ids) > MAX_ATTACH_IMAGES:
        raise AgentImageError(f'单次最多绑定 {MAX_ATTACH_IMAGES} 张图')

    product = Product.find_by_id(str(product_id))
    if not product:
        raise AgentImageError('商品不存在或已删除')

    rows = _load_assets(asset_ids, user=user)
    urls = _dedupe([_asset_url(row['filename']) for row in rows])

    skus = [dict(s) for s in (product.get('skus') or [])]
    while len(skus) <= sku_index:
        skus.append({'combo': {}, 'price': None, 'oldPrice': None, 'stock': None, 'images': []})
    target = skus[sku_index]
    existing = [u for u in (target.get('images') or []) if isinstance(u, str)]
    if mode == 'append':
        images = _dedupe(existing + urls)
    else:
        images = urls
    if not images:
        raise AgentImageError('至少 1 张图')
    target['images'] = images
    target['image'] = images[0]
    skus[sku_index] = target

    union = _dedupe([u for sku in skus for u in (sku.get('images') or []) if isinstance(u, str)])
    if mode == 'replace':
        # 只保留来自 SKU 的主图，清掉历史遗留（避免发布会带上未绑定的旧图）
        product_images = union
    else:
        product_images = _dedupe([u for u in (product.get('images') or []) if isinstance(u, str)] + union)

    update = {
        'skus': skus,
        'images': product_images,
        'coverImage': union[0] if union else '',
    }
    if add_to_detail:
        existing_detail = [u for u in (product.get('detailImages') or []) if isinstance(u, str)]
        update['detailImages'] = _dedupe(existing_detail + urls)
    Product.update(product['id'], update)

    return {
        'product_id': product['id'],
        'sku_index': sku_index,
        'images': product_images,
        'sku_images': images,
        'count': len(images),
    }
