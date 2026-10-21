# -*- coding: utf-8 -*-
"""
Ozon Performance API 客户端（广告数据）

【为什么是独立模块】
Ozon 把「商品经营数据」和「广告数据」拆成两套完全独立的 API：

  ┌ 商品 Seller API   https://api-seller.ozon.ru     认证：Client-Id + Api-Key（请求头）
  └ 广告 Performance  https://api-performance.ozon.ru 认证：OAuth2 client_credentials → Bearer Token

两边的凭证**不通用**，需要在 seller.ozon.ru →「设置 → API-keys → Performance API」
单独创建，client_id 形如 xxx@advertising.performance.ozon.ru。
因此本模块不复用 stores.api_key，而是使用 stores.perf_client_id / perf_client_secret。

【认证流程】
  1. POST /api/client/token   {client_id, client_secret, grant_type:"client_credentials"}
     → {access_token, expires_in:1800, token_type:"Bearer"}
  2. 后续请求带 Authorization: Bearer <access_token>
  3. Token 有效期 1800 秒，本模块把 Token 缓存进 stores 表，过期前 5 分钟自动续签。

【时区】
广告报表一律按**莫斯科时间（MSK, UTC+3）**结算，「当天」以 MSK 自然日为准，
不能用服务器本地时间。本模块提供 today_msk() / date_msk(days_ago) 两个工具。
"""
from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

from db import query, execute
from utils.security import encrypt_secret, decrypt_secret

BASE_URL = 'https://api-performance.ozon.ru:443'

# Token 提前续签窗口（秒）：剩余有效期少于该值就重新申请
TOKEN_REFRESH_MARGIN = 300

MSK = timezone(timedelta(hours=3))


class OzonPerformanceError(ValueError):
    """Performance API 调用失败（业务错误）。

    继承 ValueError：utils.response.handle_errors 会把它自动转成 HTTP 400
    并回传可读中文提示，前端直接展示即可，无需额外错误分支。
    """


# ============================================================
# 时区工具（Ozon 广告按 MSK 结算）
# ============================================================

def now_msk() -> datetime:
    """当前莫斯科时间"""
    return datetime.now(MSK)


def today_msk() -> str:
    """今天（MSK）的 YYYY-MM-DD"""
    return now_msk().strftime('%Y-%m-%d')


def date_msk(days_ago: int = 0) -> str:
    """MSK 往前推 N 天的 YYYY-MM-DD"""
    return (now_msk() - timedelta(days=days_ago)).strftime('%Y-%m-%d')


# ============================================================
# 凭证与 Token
# ============================================================

def _load_store(store_pk: int) -> dict:
    store = query('SELECT * FROM stores WHERE id = ?', (store_pk,), one=True)
    if not store:
        raise OzonPerformanceError('店铺不存在')
    return store


def get_credentials(store: dict) -> tuple[str, str]:
    """读取店铺的 Performance 凭证（secret 自动解密）。"""
    client_id = (store.get('perf_client_id') or '').strip()
    raw_secret = store.get('perf_client_secret') or ''
    client_secret = decrypt_secret(raw_secret) if raw_secret else ''
    return client_id, (client_secret or '').strip()


def has_credentials(store: dict) -> bool:
    cid, csec = get_credentials(store)
    return bool(cid and csec)


def save_credentials(store_pk: int, client_id: str, client_secret: str = '') -> None:
    """保存 Performance 凭证（secret 加密入库），并清空缓存 Token 强制重签。

    client_secret 为空时**只更新 client_id**，不覆盖已保存的密钥
    （典型场景：运维只想改 client_id，不该被迫重新粘贴 secret）。
    """
    sets = ['perf_client_id = ?']
    params = [(client_id or '').strip()]
    if client_secret:
        sets.append('perf_client_secret = ?')
        params.append(encrypt_secret(client_secret))
    sets.extend(["perf_token = ''", 'perf_token_expires_at = 0',
                 "updated_at = datetime('now','localtime')"])
    params.append(store_pk)
    execute('UPDATE stores SET %s WHERE id = ?' % ', '.join(sets), params)


def _fetch_token(client_id: str, client_secret: str) -> tuple[str, int]:
    """向授权服务申请 Token，返回 (access_token, expires_in)。"""
    url = BASE_URL + '/api/client/token'
    payload = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'client_credentials',
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'), method='POST'
    )
    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = exc.read().decode('utf-8', 'replace')[:300]
        except Exception:  # noqa: BLE001
            pass
        if exc.code in (400, 401, 403):
            raise OzonPerformanceError(
                'Performance API 凭证无效或已过期，请在「店铺管理」重新填写 client_id / client_secret'
                + ('：' + detail if detail else '')
            ) from exc
        raise OzonPerformanceError('申请广告 Token 失败（HTTP %d）%s' % (exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise OzonPerformanceError('无法连接 Ozon 广告接口：%s' % exc.reason) from exc

    token = data.get('access_token') or ''
    if not token:
        raise OzonPerformanceError('Ozon 未返回 access_token：%s' % str(data)[:200])
    return token, int(data.get('expires_in') or 1800)


def get_token(store_pk: int, force: bool = False) -> str:
    """获取可用 Token（带缓存；过期前 5 分钟自动续签）。"""
    store = _load_store(store_pk)
    client_id, client_secret = get_credentials(store)
    if not client_id or not client_secret:
        raise OzonPerformanceError(
            '店铺「%s」尚未配置广告 API 凭证，请到「店铺管理」填写 Performance API 的 '
            'client_id 与 client_secret' % (store.get('alias') or store.get('store_id') or store_pk)
        )

    cached = store.get('perf_token') or ''
    expires_at = int(store.get('perf_token_expires_at') or 0)
    if cached and not force and expires_at - TOKEN_REFRESH_MARGIN > int(time.time()):
        return cached

    token, expires_in = _fetch_token(client_id, client_secret)
    execute(
        'UPDATE stores SET perf_token = ?, perf_token_expires_at = ?, '
        "updated_at = datetime('now','localtime') WHERE id = ?",
        (token, int(time.time()) + expires_in, store_pk),
    )
    return token


# ============================================================
# 通用请求
# ============================================================

def _request(store_pk: int, method: str, path: str, *, body=None,
             params=None, raw=False, binary=False, timeout: int = 60,
             _retry_auth: bool = True):
    """调用 Performance API。

    Args:
        raw:    True 时返回响应原文（文本 CSV 用），否则解析 JSON。
        binary: True 时返回**原始字节**。报表下载接口返回的是 ZIP，
                若按文本 decode 会破坏压缩数据（BadZipFile），必须走这条路径。
    """
    url = BASE_URL + path
    if params:
        from urllib.parse import urlencode
        pairs = []
        for k, v in params.items():
            if v is None or v == '':
                continue
            if isinstance(v, (list, tuple)):
                # 数组参数按重复键序列化：campaignIds=1&campaignIds=2
                pairs.extend((k, item) for item in v)
            else:
                pairs.append((k, v))
        if pairs:
            url += ('&' if '?' in url else '?') + urlencode(pairs)

    token = get_token(store_pk)
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf-8')

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Accept', 'application/json')
    if data is not None:
        req.add_header('Content-Type', 'application/json')

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            blob = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = exc.read().decode('utf-8', 'replace')[:400]
        except Exception:  # noqa: BLE001
            pass
        # 401：Token 可能被提前吊销 → 强制重签一次再试
        if exc.code == 401 and _retry_auth:
            get_token(store_pk, force=True)
            return _request(store_pk, method, path, body=body, params=params,
                            raw=raw, binary=binary, timeout=timeout, _retry_auth=False)
        raise OzonPerformanceError(
            'Ozon 广告接口返回 %d（%s）%s' % (exc.code, path, detail)
        ) from exc
    except urllib.error.URLError as exc:
        raise OzonPerformanceError('无法连接 Ozon 广告接口：%s' % exc.reason) from exc

    if binary:
        return blob
    payload = blob.decode('utf-8', 'replace')
    if raw:
        return payload
    if not payload.strip():
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OzonPerformanceError(
            'Ozon 广告接口返回了非 JSON 内容（%s）：%s' % (path, payload[:200])
        ) from exc


# ============================================================
# 业务接口
# ============================================================

def list_campaigns(store_pk: int, page: int = 1, page_size: int = 100) -> list[dict]:
    """活动列表 GET /api/client/campaign

    返回字段（截取关心的）：
      id, title, paymentType(CPC/CPO...), placement, state,
      advObjectType, budget, dailyBudget, fromDate, toDate
    """
    result = _request(store_pk, 'GET', '/api/client/campaign',
                      params={'page': page, 'pageSize': page_size})
    items = (result or {}).get('list') or []
    return items if isinstance(items, list) else []


def list_all_campaigns(store_pk: int, max_pages: int = 20) -> list[dict]:
    """翻页拉取全部活动"""
    out, page = [], 1
    while page <= max_pages:
        batch = list_campaigns(store_pk, page=page, page_size=100)
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


def product_sku_stats(store_pk: int, campaign_ids, date_from: str, date_to: str) -> list[dict]:
    """商品级广告统计 POST /api/client/statistics/products/sku（同步返回 JSON）

    每行含：sku, campaignId, views, clicks, ctr(小数), expense(₽), avgCpc,
            orders, sales, toCart, drr, date

    这是「30 次点击 / CTR 低于 4% / 花费超 1000 卢布无单」三条规则的数据源。
    """
    body = {
        'campaignIds': [str(c) for c in (campaign_ids or [])],
        'dateFrom': date_from,
        'dateTo': date_to,
    }
    result = _request(store_pk, 'POST', '/api/client/statistics/products/sku', body=body)
    rows = (result or {}).get('rows')
    if isinstance(rows, list):
        return rows
    # 部分账号返回顶层数组
    return result if isinstance(result, list) else []


def daily_stats(store_pk: int, date_from: str = '', date_to: str = '',
                campaign_ids=None) -> list[dict]:
    """按天统计 GET /api/client/statistics/daily/json

    报表字段（Ozon 顺序）：活动ID、活动名称、日期、展示、点击、花费(₽)、
    平均点击价格(₽)、订单数、订单金额(₽)

    ⚠️ 官方 spec 里该接口的 query 参数名为 campaignIds，但官方文档示例用的是
    campaigns。这里默认**不传** campaignIds —— 该参数可选，不传即返回全部活动，
    正好符合「统计整店当日总花费」的用途，同时规避参数名歧义。
    """
    params = {'dateFrom': date_from or None, 'dateTo': date_to or None}
    if campaign_ids:
        params['campaignIds'] = [str(c) for c in campaign_ids]
    result = _request(store_pk, 'GET', '/api/client/statistics/daily/json', params=params)
    if isinstance(result, list):
        return result
    for key in ('rows', 'result', 'data'):
        if isinstance((result or {}).get(key), list):
            return result[key]
    return []


def cpa_products(store_pk: int, page: int = 1, page_size: int = 100) -> dict:
    """按订单付费（Оплата за заказ）在推商品 POST /api/client/campaign/search_promo/v2/products

    返回 {products:[...], total:'...'}；products 含 sku/title/bid/views/visibilityIndex。
    """
    result = _request(store_pk, 'POST', '/api/client/campaign/search_promo/v2/products',
                      body={'page': page, 'pageSize': page_size})
    return result or {}


def cpc_campaign_products(store_pk: int, campaign_id: str,
                          page: int = 1, page_size: int = 100) -> list[dict]:
    """按点击付费活动内的商品 GET /api/client/campaign/{id}/v2/products"""
    result = _request(store_pk, 'GET',
                      '/api/client/campaign/%s/v2/products' % campaign_id,
                      params={'page': page, 'pageSize': page_size})
    items = (result or {}).get('products')
    return items if isinstance(items, list) else []


# ============================================================
# 异步报表：商品级(SKU)统计的唯一可靠来源
# ============================================================
# 为什么不用 /api/client/statistics/products/sku：
#   该接口实测只接受「今天或昨天」的区间，传 30 天直接报
#   400 {"error":"date range must contain only today or yesterday"}，
#   无法支撑「30 天累计点击」这类判断。
# 异步报表 /api/client/statistics 支持最长 62 天，且 CSV 里**逐行给出
#   日期 + SKU + 商品名 + 曝光 + 点击 + CTR + 花费 + 订单 + 销售额**，
#   正好覆盖 R1/R2/R3 的全部字段，因此以它为主数据源。
#
# 流程：提交 → 轮询状态(state=OK) → 下载(ZIP/CSV，二进制) → 解析

# CSV 表头（俄文）→ 内部字段名。归一化后按精确名匹配，
# 避免把「Продано товаров」和「Продано товаров модели」混淆。
_HEADER_MAP = {
    'день': 'stat_date',
    'sku': 'sku',
    'название товара': 'title',
    'показы': 'impressions',
    'клики': 'clicks',
    'ctr': 'ctr_percent',
    'добавления в корзину': 'to_cart',
    'средняя стоимость клика': 'avg_cpc',
    'расход': 'expense',
    'продано товаров': 'orders',
    'продажи в продвижении': 'sales',
    'дрр': 'drr_percent',
}


def _norm_header(text: str) -> str:
    """归一化表头：去 BOM、小写、去掉 ₽ 与 %、压缩空白。

    'Расход, ₽'  -> 'расход'
    'CTR, %'     -> 'ctr'
    'ДРР в продвижении, %' -> 'дрр в продвижении'（与 'дрр' 区分开）
    """
    s = str(text or '').replace('\ufeff', '').lower()
    for ch in ('₽', '%', ',', '\xa0'):
        s = s.replace(ch, ' ')
    return ' '.join(s.split())


def _num(value) -> float:
    """解析俄式数字：'1 234,56' -> 1234.56；支持普通空格与不换行空格。"""
    if value is None:
        return 0.0
    s = str(value)
    for ch in ('\xa0', ' ', '₽', '%', '\ufeff'):
        s = s.replace(ch, '')
    s = s.replace(',', '.')
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def submit_campaign_report(store_pk: int, campaign_ids, date_from: str,
                           date_to: str, group_by: str = 'DATE') -> str:
    """提交报表生成请求，返回 UUID。"""
    body = {
        'campaigns': [str(c) for c in (campaign_ids or [])],
        'dateFrom': date_from,
        'dateTo': date_to,
        'groupBy': group_by,
    }
    result = _request(store_pk, 'POST', '/api/client/statistics', body=body)
    uuid = (result or {}).get('UUID')
    if not uuid:
        raise OzonPerformanceError('Ozon 未返回报表 UUID：%s' % str(result)[:200])
    return uuid


def report_status(store_pk: int, uuid: str) -> str:
    """查询报表状态，返回 state 字符串（NOT_STARTED / OK / ERROR...）。"""
    result = _request(store_pk, 'GET', '/api/client/statistics/' + uuid)
    return str((result or {}).get('state') or '')


def wait_for_report(store_pk: int, uuid: str, timeout: int = 180,
                    interval: int = 3) -> None:
    """轮询直到报表就绪（实测约 3~6 秒）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = report_status(store_pk, uuid)
        if state == 'OK':
            return
        if state in ('ERROR', 'FAILED', 'CANCELLED'):
            raise OzonPerformanceError('报表生成失败（state=%s）' % state)
        time.sleep(interval)
    raise OzonPerformanceError('报表生成超时（%d 秒内未就绪）' % timeout)


def download_report(store_pk: int, uuid: str) -> bytes:
    """下载报表原始字节（多活动为 ZIP，单活动为 CSV）——必须走二进制。"""
    return _request(store_pk, 'GET', '/api/client/statistics/report',
                    params={'UUID': uuid}, binary=True, timeout=120)


def _split_report_blob(blob: bytes) -> list[tuple[str, str]]:
    """把报表字节拆成 [(campaign_id, csv_text), ...]。"""
    if blob[:2] == b'PK':                      # ZIP：每活动一个 CSV
        zf = zipfile.ZipFile(io.BytesIO(blob))
        out = []
        for name in zf.namelist():
            if not name.lower().endswith('.csv'):
                continue
            text = zf.read(name).decode('utf-8-sig', 'replace')
            campaign_id = name.split('_', 1)[0]
            out.append((campaign_id, text))
        return out
    return [('', blob.decode('utf-8-sig', 'replace'))]


def parse_report_csv(campaign_id: str, text: str) -> list[dict]:
    """解析单个活动的报表 CSV，返回逐日逐 SKU 的行。

    文件结构：
        第 1 行  元信息（';Кампания по продвижению товаров № ...'）
        第 2 行  表头（分号分隔，俄文）
        之后     数据行；末行可能是 'Всего'（活动合计，无 sku）——跳过
    """
    rows = []
    header = None
    for raw_line in text.splitlines():
        line = raw_line.strip('\ufeff').strip()
        if not line:
            continue
        cells = line.split(';')
        if header is None:
            norm = [_norm_header(c) for c in cells]
            if 'sku' in norm:                  # 找到表头行
                header = norm
            continue
        if len(cells) < 3:
            continue
        record = {}
        for idx, key in enumerate(header):
            field = _HEADER_MAP.get(key)
            if field and idx < len(cells):
                record[field] = cells[idx].strip()
        stat_date = str(record.get('stat_date') or '').strip()
        sku = str(record.get('sku') or '').strip()
        # 跳过「Всего」合计行（无 sku / 无日期）——我们按天自己累计
        if not sku or not stat_date or stat_date.lower() in ('всего', 'итого'):
            continue
        record['campaignId'] = str(campaign_id or '')
        rows.append(record)
    return rows


def campaign_report_rows(store_pk: int, campaign_ids, date_from: str, date_to: str,
                         group_by: str = 'DATE', timeout: int = 180) -> list[dict]:
    """一站式：提交 → 等待 → 下载 → 解析，返回逐日逐 SKU 的统计行。"""
    if not campaign_ids:
        return []
    uuid = submit_campaign_report(store_pk, campaign_ids, date_from, date_to, group_by)
    wait_for_report(store_pk, uuid, timeout=timeout)
    blob = download_report(store_pk, uuid)
    out = []
    for campaign_id, text in _split_report_blob(blob):
        out.extend(parse_report_csv(campaign_id, text))
    return out


def test_connection(store_pk: int) -> dict:
    """连通性自检：签 Token + 拉一次活动列表。供「店铺管理」页做凭证校验。"""
    store = _load_store(store_pk)
    token = get_token(store_pk, force=True)
    campaigns = list_campaigns(store_pk, page=1, page_size=10)
    return {
        'ok': True,
        'storeId': store.get('store_id', ''),
        'alias': store.get('alias', ''),
        'tokenPreview': token[:12] + '...',
        'campaignCount': len(campaigns),
        'sample': [
            {'id': c.get('id'), 'title': c.get('title'),
             'paymentType': c.get('paymentType'), 'state': c.get('state'),
             'placement': c.get('placement')}
            for c in campaigns[:5]
        ],
    }
