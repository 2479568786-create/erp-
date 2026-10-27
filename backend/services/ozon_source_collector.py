"""Safe parsing primitives for public Ozon source products."""

import ipaddress
import hashlib
import json
import re
import socket
import time
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests


class InvalidSourceUrl(ValueError):
    pass


class UnsafeSourceTarget(ValueError):
    pass


class UnsupportedStructure(ValueError):
    pass


class MissingCoreFields(ValueError):
    pass


class SourceTimeout(TimeoutError):
    pass


class SourceBlocked(RuntimeError):
    pass


class SourceUnavailable(RuntimeError):
    pass


class SourceResponseTooLarge(RuntimeError):
    pass


def normalize_public_ozon_url(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidSourceUrl('仅支持 HTTPS Ozon 商品链接')
    try:
        parts = urlsplit(value.strip())
    except ValueError as exc:
        raise InvalidSourceUrl('Ozon 商品链接格式错误') from exc
    host = (parts.hostname or '').lower()
    path = parts.path.rstrip('/')
    if (parts.scheme.lower() != 'https'
            or not (host == 'ozon.ru' or host.endswith('.ozon.ru'))
            or '/product/' not in path.lower()
            or not re.search(r'(?:^|[-/])(\d+)$', path)):
        raise InvalidSourceUrl('仅支持 HTTPS Ozon 商品链接')
    return urlunsplit(('https', host, path, '', ''))


def _resolve_addresses(host: str):
    return {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}


def validate_public_target(url: str, resolver=None) -> None:
    normalized = normalize_public_ozon_url(url)
    host = urlsplit(normalized).hostname or ''
    addresses = (resolver or _resolve_addresses)(host)
    if not addresses:
        raise UnsafeSourceTarget('来源域名没有可用地址')
    for address in addresses:
        try:
            parsed = address if isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafeSourceTarget('来源域名解析结果无效') from exc
        if not parsed.is_global:
            raise UnsafeSourceTarget('来源域名解析到了非公开地址')


def _normalize_redirect_url(base_url: str, location: str) -> str:
    target = urljoin(base_url, location)
    normalized = normalize_public_ozon_url(target)
    risk_marker = [(key, value) for key, value in parse_qsl(urlsplit(target).query)
                   if key == '__rr']
    return normalized + (('?' + urlencode(risk_marker)) if risk_marker else '')


def _decode_widget(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _first_widget(states, prefix):
    for key, value in states.items():
        if str(key).startswith(prefix):
            return str(key), _decode_widget(value)
    return '', {}


def _text(value):
    if isinstance(value, dict):
        return str(value.get('text') or value.get('value') or '')
    return str(value or '')


def _plain_html(value):
    cleaned = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', str(value or ''), flags=re.I | re.S)
    cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
    return re.sub(r'\s+', ' ', unescape(cleaned)).strip()


def _image_url(value):
    raw = _text(value)
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ''
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return ''
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if key.lower() not in ('width', 'height', 'size')]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))


def _price(value):
    raw = _text(value)
    digits = re.sub(r'[^0-9.,]', '', raw).replace(',', '.')
    if digits.count('.') > 1:
        digits = digits.replace('.', '')
    return digits.rstrip('.0') if digits.endswith('.0') else digits


def parse_ozon_payload(source_url: str, payload: object) -> dict:
    normalized = normalize_public_ozon_url(source_url)
    source_match = re.search(r'(?:^|[-/])(\d+)$', urlsplit(normalized).path)
    states = payload.get('widgetStates') if isinstance(payload, dict) else None
    if not isinstance(states, dict):
        raise UnsupportedStructure('未找到 widgetStates')

    evidence = {}
    heading_key, heading = _first_widget(states, 'webProductHeading')
    price_key, price_data = _first_widget(states, 'webPrice')
    chars_key, chars_data = _first_widget(states, 'webShortCharacteristics')
    gallery_key, gallery = _first_widget(states, 'webGallery')
    aspects_key, aspects = _first_widget(states, 'webAspects')
    description_key, description_data = _first_widget(states, 'webDescription')
    seller_key, seller = _first_widget(states, 'webSeller')

    title = _text(heading.get('title')).strip()
    description = _plain_html(description_data.get('description') or description_data.get('text'))
    if title:
        evidence['title'] = f'widgetStates.{heading_key}.title'
    if description:
        evidence['description'] = f'widgetStates.{description_key}.description'

    attributes = []
    seen_attributes = set()
    for item in chars_data.get('characteristics') or chars_data.get('items') or []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get('name')).strip()
        values = item.get('values')
        value = ', '.join(filter(None, (_text(part).strip() for part in values))) if isinstance(values, list) else _text(item.get('value')).strip()
        marker = (name.casefold(), value.casefold())
        if name and value and marker not in seen_attributes:
            seen_attributes.add(marker)
            attributes.append({'name': name, 'value': value})
    if attributes:
        evidence['attributes'] = f'widgetStates.{chars_key}'

    candidates = [gallery.get('coverImage')]
    candidates.extend(gallery.get('images') or [])
    images = []
    for candidate in candidates:
        url = _image_url(candidate.get('url') if isinstance(candidate, dict) else candidate)
        if url and url not in images:
            images.append(url)
    if images:
        evidence['images'] = f'widgetStates.{gallery_key}'

    sku_list = []
    for aspect in aspects.get('aspects') or []:
        if not isinstance(aspect, dict):
            continue
        aspect_name = _text(aspect.get('name')).strip()
        for variant in aspect.get('variants') or []:
            if not isinstance(variant, dict):
                continue
            sku_id = _text(variant.get('id')).strip()
            name = _text(variant.get('name')).strip()
            if sku_id or name:
                sku_list.append({'skuId': sku_id, 'name': name, 'price': _price(variant.get('price')), 'aspect': aspect_name})
    if sku_list:
        evidence['skuList'] = f'widgetStates.{aspects_key}'

    warnings = []
    if not description:
        warnings.append('未采集到商品描述')
    if not attributes:
        warnings.append('未采集到商品参数')
    if not seller:
        warnings.append('未采集到卖家公开信息')
    elif seller_key:
        evidence['seller'] = f'widgetStates.{seller_key}'

    if not title or not images:
        missing = []
        if not title:
            missing.append('标题')
        if not images:
            missing.append('图片')
        raise MissingCoreFields('缺少核心字段：' + '、'.join(missing))

    raw_price = price_data.get('price') or price_data.get('currentPrice') or ''
    result = {
        'sourceId': source_match.group(1),
        'originalUrl': normalized,
        'title': title,
        'description': description,
        'category': '',
        'price': _price(raw_price),
        'currency': 'RUB' if '₽' in _text(raw_price) else '',
        'attributes': attributes,
        'images': images,
        'detailImages': [],
        'skuList': sku_list,
        'extractionWarnings': warnings,
        'fieldEvidence': evidence,
    }
    if price_key:
        result['fieldEvidence']['price'] = f'widgetStates.{price_key}.price'
    return result


PARSER_VERSION = 'ozon-widget-states-v1'
_REQUEST_HEADERS = {
    'Accept': 'application/json,text/html;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9',
    'User-Agent': 'GeekOzon-ERP/1.0 (+server-side public product import)',
}


def _extract_payload(body: bytes, content_type: str):
    text = body.decode('utf-8', errors='replace')
    if 'json' in (content_type or '').lower() or text.lstrip().startswith(('{', '[')):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UnsupportedStructure('来源返回的 JSON 无法解析') from exc
        if isinstance(decoded, dict):
            if isinstance(decoded.get('widgetStates'), dict):
                return decoded
            for key in ('data', 'result'):
                nested = decoded.get(key)
                if isinstance(nested, dict) and isinstance(nested.get('widgetStates'), dict):
                    return nested
        raise UnsupportedStructure('来源 JSON 中没有 widgetStates')

    patterns = (
        r'"widgetStates"\s*:\s*(\{.*?\})\s*,\s*"',
        r'window\.__WIDGET_STATES__\s*=\s*(\{.*?\})\s*;',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.S)
        if not match:
            continue
        try:
            states = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(states, dict):
            return {'widgetStates': states}
    raise UnsupportedStructure('来源页面中没有可解析的 widgetStates')


def fetch_public_product(url: str, timeout_seconds: int = 15, max_bytes: int = 5_000_000,
                         http_get=None, sleep=time.sleep, resolver=None) -> dict:
    current_url = normalize_public_ozon_url(url)
    getter = http_get or requests.get
    transient_errors = (requests.Timeout, requests.ConnectionError)

    for attempt in range(3):
        try:
            redirects = 0
            while True:
                if http_get is None or resolver is not None:
                    validate_public_target(current_url, resolver=resolver)
                response = getter(
                    current_url,
                    headers=dict(_REQUEST_HEADERS),
                    timeout=timeout_seconds,
                    allow_redirects=False,
                    stream=True,
                )
                if response.status_code in (301, 302, 303, 307, 308):
                    redirects += 1
                    if redirects > 3:
                        raise SourceUnavailable('来源重定向次数过多')
                    location = response.headers.get('Location', '')
                    current_url = _normalize_redirect_url(current_url, location)
                    continue
                if response.status_code in (401, 403, 429):
                    raise SourceBlocked(f'来源拒绝访问（HTTP {response.status_code}）')
                if response.status_code >= 400:
                    raise SourceUnavailable(f'来源请求失败（HTTP {response.status_code}）')
                body = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise SourceResponseTooLarge('来源响应超过大小限制')
                raw = bytes(body)
                return {
                    'payload': _extract_payload(raw, response.headers.get('Content-Type', '')),
                    'responseHash': hashlib.sha256(raw).hexdigest(),
                    'parserVersion': PARSER_VERSION,
                    'finalUrl': current_url,
                }
        except transient_errors as exc:
            if attempt == 2:
                raise SourceTimeout('来源请求超时') from exc
            sleep(0.25 * (2 ** attempt))

    raise SourceUnavailable('来源请求失败')
