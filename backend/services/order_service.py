"""
OZON ERP - 订单同步服务
从 Ozon Seller API 拉取 FBS/FBO 发货单并入库（幂等），支持状态刷新
"""
import json
import time
import threading
from models.order import Order
from models.account import Store
from services import ozon_api


def _pick_products_data(posting):
    """从 posting 中提取商品明细（兼容 FBS/FBO 字段差异）"""
    products = posting.get('products') or []
    items = []
    for p in products:
        items.append({
            'sku': p.get('sku'),
            'product_id': p.get('product_id'),
            'name': p.get('name'),
            'offer_id': p.get('offer_id'),
            'quantity': p.get('quantity'),
            'price': p.get('price'),
            'digital_signature_type': p.get('digital_signature_type'),
        })
    return items


def _pick_customer(posting):
    """客户信息（兼容 Ozon list 接口的字段路径）

    实际结构（2026-09 实测）：customer 为 dict，内嵌 address 子 dict；
    addressee.name 存收件人姓名（customer 不一定有 name）。
    """
    customer = posting.get('customer') or {}
    if not isinstance(customer, dict):
        customer = {}
    addr = customer.get('address') if isinstance(customer.get('address'), dict) else {}
    addressee = posting.get('addressee') if isinstance(posting.get('addressee'), dict) else {}
    name = addressee.get('name') or customer.get('name') or customer.get('customer_email') or ''
    return {
        'name': name or None,
        'country': addr.get('country'),
        'city': addr.get('city'),
        'address': addr.get('address_tail'),
    }


def _pick_price(posting):
    """订单金额（兼容 Ozon financial_data 不同返回形态）

    Ozon financial_data 在 posting/list 接口可能返回：
    - dict: {"products_price": {"price": "...", "currency": "..."}, "delivery_price": {...}, "services_price": {...}}
    - list: [{"commission_amount":..., "product_price":..., ...}, ...]  （最简聚合写法）
    - None

    优先级：products_price.price > delivery_price.price > services_price.price > product 行求和
    """
    financial = posting.get('financial_data') or {}
    analytics = posting.get('analytics_data') or {}
    amount = None
    currency = 'RUB'

    if isinstance(financial, dict):
        for key in ('products_price', 'delivery_price', 'services_price'):
            val = financial.get(key)
            if isinstance(val, dict) and val.get('price'):
                amount = float(val['price'])
                currency = val.get('currency', 'RUB')
                break
    elif isinstance(financial, list):
        # list 形态：按行累加 product_price 字段
        total = 0.0
        for row in financial:
            if isinstance(row, dict):
                for k in ('product_price', 'price', 'amount'):
                    v = row.get(k)
                    if v is not None:
                        try:
                            total += float(v)
                        except (TypeError, ValueError):
                            pass
                        break
        if total > 0:
            amount = total

    if amount is None:
        # 回退：商品行求和
        total = 0.0
        for p in posting.get('products') or []:
            try:
                total += float(p.get('price', 0)) * int(p.get('quantity', 1))
            except (TypeError, ValueError):
                continue
        if total > 0:
            amount = total
    return {'amount': amount, 'currency': currency}


def _pick_dates(posting):
    """取关键时间字段（FBS/FBO 字段略有差异）"""
    in_process = posting.get('in_process_at')
    if not in_process:
        in_process = posting.get('order_creation_date')
    ship_date = posting.get('shipment_date')
    delivery = posting.get('delivery_date')
    return in_process, ship_date, delivery


def parse_posting(posting, store_id, fulfillment_type):
    """将一个 posting 原始数据转为入库所需字段"""
    in_process, ship_date, delivery = _pick_dates(posting)
    return {
        'posting_number': posting.get('posting_number'),
        'store_id': store_id,
        'fulfillment_type': fulfillment_type,
        'status': posting.get('status'),
        'in_process_at': in_process,
        'ship_date': ship_date,
        'delivery_date': delivery,
        'cancel_reason': (posting.get('cancellation') or {}).get('cancel_reason') if isinstance(posting.get('cancellation'), dict) else None,
        'customer': _pick_customer(posting),
        'price': _pick_price(posting),
        'products': _pick_products_data(posting),
        'financial': posting.get('financial_data'),
        'raw': posting,
    }


def _extract_postings(result):
    """从响应里取出 postings 列表，兼容两种 Ozon 返回形态

    - FBS (/v3/posting/fbs/list): {"result": {"postings": [...], "has_next": bool}}
    - FBO (/v2/posting/fbo/list): {"result": [posting, ...]}  (result 直接是 list)
    """
    result = (result or {}).get('result')
    if isinstance(result, dict):
        return result.get('postings') or [], bool(result.get('has_next'))
    if isinstance(result, list):
        return result, False
    return [], False


def save_postings(result, store_id, fulfillment_type):
    """将接口返回的 postings 批量入库，返回新增/更新数"""
    postings, _ = _extract_postings(result)
    saved = 0
    for posting in postings:
        try:
            data = parse_posting(posting, store_id, fulfillment_type)
            if data['posting_number']:
                Order.upsert(**data)
                saved += 1
        except Exception as e:
            print(f'[订单同步] 单条入库失败 {posting.get("posting_number")}: {e}')
    return saved


def sync_orders_for_store(store, fulfillment_type='FBS', limit=100, days=30):
    """同步单个店铺的订单（FBS 或 FBO）

    Args:
        store: stores 表记录（含 client_id + 解密后的 api_key）
        fulfillment_type: 'FBS' / 'FBO'
        limit: 本次拉取条数
        days: 拉取最近 N 天
    Returns:
        dict: { saved, fetched, has_next }
    """
    client_id, api_key = store.get('client_id'), store.get('api_key')
    # api_key 在 stores 表为密文（enc$v1$...），必须解密为明文才能调 Ozon API；
    # 兼容历史明文数据（decrypt_secret 对非 enc$v1$ 原样返回）
    from utils.security import decrypt_secret
    client_id = client_id or store.get('store_id')
    api_key = decrypt_secret(api_key or '')
    if not client_id or not api_key:
        return {'saved': 0, 'fetched': 0, 'has_next': False, 'error': '凭证缺失'}

    since = None
    if days:
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=int(days))).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    # Ozon /v3/posting/fbs/list 与 /v2/posting/fbo/list 的时间字段：
    # 2026-09 实测：filter 字段名是 since / to（不是 processed_at_from/to）
    to_iso = None
    if days:
        from datetime import datetime as _dt, timezone as _tz
        to_iso = _dt.now(_tz.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    filter_body = {}
    if since:
        filter_body['since'] = since
    if to_iso:
        filter_body['to'] = to_iso

    func = ozon_api.list_fbs_postings if fulfillment_type == 'FBS' else ozon_api.list_fbo_postings
    try:
        resp = func(filter_body, limit=limit, offset=0, client_id=client_id, api_key=api_key)
    except ozon_api.OzonAPIError as e:
        return {'saved': 0, 'fetched': 0, 'has_next': False, 'error': e.message}

    saved = save_postings(resp, store.get('store_id'), fulfillment_type)
    fetched = len(_extract_postings(resp)[0])
    has_next = _extract_postings(resp)[1]
    return {'saved': saved, 'fetched': fetched, 'has_next': has_next}


def sync_orders(store_id=None, fulfillment_type=None):
    """同步指定店铺（或全部活跃店铺）的订单

    Args:
        store_id: Ozon 店铺 ID；为空则同步全部 active 店铺
        fulfillment_type: 'FBS'/'FBO'；为空则两种都同步
    Returns:
        dict: { "results": [...] }
    """
    if store_id:
        store = Store.find_by_store_id(store_id)
        stores = [store] if store else []
    else:
        # 同步所有"有 client_id 和 api_key"的店铺（auth_status 字段在历史数据里可能为 NULL）
        stores = [s for s in Store.find_all() if s.get('client_id') and s.get('api_key')]

    results = []
    # 重要：fulfillment_type 已经是 'FBS' 或 'FBO' 时不能再迭代
    ftypes = [fulfillment_type] if fulfillment_type in ('FBS', 'FBO') else ['FBS', 'FBO']
    for store in stores:
        for ftype in ftypes:
            try:
                r = sync_orders_for_store(store, ftype)
            except Exception as e:
                r = {'saved': 0, 'fetched': 0, 'has_next': False, 'error': f'解析异常: {type(e).__name__}: {e}'}
            r['store_id'] = store.get('store_id')
            r['store_alias'] = store.get('alias')
            r['fulfillment_type'] = ftype
            results.append(r)
    return {'results': results}


def refresh_detail(posting_number, store_id=None):
    """刷新单条订单详情（状态等字段）

    优先指定店铺，否则用第一个 active 店铺凭证尝试
    """
    if store_id:
        store = Store.find_by_store_id(store_id)
    else:
        stores = Store.find_all(auth_status='active')
        store = stores[0] if stores else None
    if not store:
        return {'error': '未找到店铺凭证'}

    from utils.security import decrypt_secret
    client_id, api_key = store.get('client_id'), decrypt_secret(store.get('api_key') or '')

    posting = Order.find_by_posting(posting_number)
    ftype = (posting or {}).get('fulfillment_type', 'FBS')
    try:
        if ftype == 'FBO':
            resp = ozon_api.get_fbo_posting_detail(posting_number, client_id=client_id, api_key=api_key)
        else:
            resp = ozon_api.get_fbs_posting_detail(posting_number, client_id=client_id, api_key=api_key)
    except ozon_api.OzonAPIError as e:
        return {'error': e.message}

    result = (resp or {}).get('result', {})
    posting_data = result.get('posting') or result
    if posting_data and posting_data.get('posting_number'):
        data = parse_posting(posting_data, store.get('store_id'), ftype)
        Order.upsert(**data)
        return {'refresh': True, 'posting_number': posting_number, 'status': data['status']}
    return {'refresh': False, 'error': '接口未返回有效数据'}