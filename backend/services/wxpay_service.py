"""微信支付 APIv3 最小实现（Native 下单 / 查单 / 回调验签与解密）。

只依赖标准库 + cryptography；配置全部来自环境变量（见 /etc/geekozon-erp.env 的 WXPAY_*）。
**不落地任何密钥**：私钥与微信支付公钥都从 WXPAY_CERT_DIR 里按文件名读取。
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

API_BASE = 'https://api.mch.weixin.qq.com'
USER_AGENT = 'geekozon-erp/1.0 (wxpay-apiv3)'
MAX_CLOCK_SKEW = 300  # 秒

_private_key_cache = {}
_public_key_cache = {}


def _env(name, default=''):
    return (os.environ.get(name) or default).strip()


def config():
    return {
        'enabled': _env('WXPAY_ENABLED', '0') == '1',
        'mchid': _env('WXPAY_MCHID'),
        'appid': _env('WXPAY_APPID'),
        'serial': _env('WXPAY_SERIAL_NO'),
        'apiv3_key': _env('WXPAY_APIV3_KEY'),
        'cert_dir': _env('WXPAY_CERT_DIR', '/etc/geekozon-erp-pay'),
        'notify_url': _env('WXPAY_NOTIFY_URL'),
        'pub_key_id': _env('WXPAY_PUB_KEY_ID'),
        'pub_key_file': _env('WXPAY_PUB_KEY_FILE'),
    }


def missing_config():
    """返回缺失的配置项名字列表（用于 /api/pay/status 与日志，不泄漏值）。"""
    cfg = config()
    need = {
        'WXPAY_MCHID': cfg['mchid'],
        'WXPAY_APPID': cfg['appid'],
        'WXPAY_SERIAL_NO': cfg['serial'],
        'WXPAY_APIV3_KEY': cfg['apiv3_key'],
        'WXPAY_NOTIFY_URL': cfg['notify_url'],
        'WXPAY_PUB_KEY_ID': cfg['pub_key_id'],
    }
    return [k for k, v in need.items() if not v]


def _cert_path(cfg, name):
    return os.path.join(cfg['cert_dir'], name)


def _load_private_key(cfg):
    path = _cert_path(cfg, 'apiclient_key.pem')
    key = _private_key_cache.get(path)
    if key is None:
        with open(path, 'rb') as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        _private_key_cache[path] = key
    return key


def _load_public_key(cfg):
    """微信支付公钥（推荐，PUB_KEY_ID 模式）；没有则回退平台证书。"""
    path = cfg['pub_key_file'] or _cert_path(cfg, 'wechatpay_pub_key.pem')
    if os.path.exists(path):
        key = _public_key_cache.get(path)
        if key is None:
            with open(path, 'rb') as f:
                key = serialization.load_pem_public_key(f.read())
            _public_key_cache[path] = key
        return key
    platform = _cert_path(cfg, 'wechatpay_platform.pem')
    if os.path.exists(platform):
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(open(platform, 'rb').read())
        return cert.public_key()
    raise RuntimeError('缺少微信支付公钥/平台证书')


def build_authorization(method, url_path, body='', cfg=None, private_key=None):
    """构造 Authorization 头（WECHATPAY2-SHA256-RSA2048）。url_path 需带 query。"""
    cfg = cfg or config()
    key = private_key or _load_private_key(cfg)
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex.upper()
    message = '%s\n%s\n%s\n%s\n%s\n' % (method.upper(), url_path, ts, nonce, body)
    signature = key.sign(message.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256())
    token = ('mchid="%s",nonce_str="%s",signature="%s",timestamp="%s",serial_no="%s"'
             % (cfg['mchid'], nonce, base64.b64encode(signature).decode('ascii'), ts, cfg['serial']))
    return 'WECHATPAY2-SHA256-RSA2048 ' + token


def _http(method, url_path, body_dict=None, cfg=None):
    cfg = cfg or config()
    body = json.dumps(body_dict, ensure_ascii=False, separators=(',', ':')) if body_dict is not None else ''
    headers = {
        'Authorization': build_authorization(method, url_path, body, cfg),
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': USER_AGENT,
    }
    data = body.encode('utf-8') if body else None
    req = urllib.request.Request(API_BASE + url_path, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode('utf-8')
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:400]
        raise RuntimeError('微信支付接口 %s %s 失败：HTTP %s %s' % (method, url_path, exc.code, detail))


def create_native_order(out_trade_no, description, amount_cents, cfg=None):
    cfg = cfg or config()
    body = {
        'appid': cfg['appid'],
        'mchid': cfg['mchid'],
        'description': description[:127],
        'out_trade_no': out_trade_no,
        'notify_url': cfg['notify_url'],
        'amount': {'total': int(amount_cents), 'currency': 'CNY'},
    }
    return _http('POST', '/v3/pay/transactions/native', body, cfg)


def query_order(out_trade_no, cfg=None):
    cfg = cfg or config()
    path = '/v3/pay/transactions/out-trade-no/%s?mchid=%s' % (out_trade_no, cfg['mchid'])
    return _http('GET', path, None, cfg)


def close_order(out_trade_no, cfg=None):
    cfg = cfg or config()
    path = '/v3/pay/transactions/out-trade-no/%s/close' % out_trade_no
    return _http('POST', path, {'mchid': cfg['mchid']}, cfg)


def verify_notify(headers, body_bytes, cfg=None, now=None):
    """验签回调。headers 用大小写不敏感取值。返回 (ok, reason)。"""
    cfg = cfg or config()
    get = (lambda name: headers.get(name) or headers.get(name.lower()) or headers.get(name.title()) or '')
    ts = get('Wechatpay-Timestamp')
    nonce = get('Wechatpay-Nonce')
    signature = get('Wechatpay-Signature')
    serial = get('Wechatpay-Serial')
    if not (ts and nonce and signature and serial):
        return False, 'missing_signature_headers'
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        return False, 'bad_timestamp'
    current = now if now is not None else time.time()
    if abs(current - ts_int) > MAX_CLOCK_SKEW:
        return False, 'timestamp_skew'
    if cfg['pub_key_id'] and serial != cfg['pub_key_id']:
        return False, 'unknown_serial'
    body_text = body_bytes.decode('utf-8') if isinstance(body_bytes, bytes) else body_bytes
    message = '%s\n%s\n%s\n' % (ts, nonce, body_text)
    try:
        public_key = _load_public_key(cfg)
        public_key.verify(
            base64.b64decode(signature),
            message.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:  # noqa: BLE001
        return False, 'bad_signature:%s' % type(exc).__name__
    return True, 'ok'


def decrypt_resource(resource, cfg=None):
    """解密回调 resource（AES-256-GCM，key = APIv3 密钥）。"""
    cfg = cfg or config()
    key = cfg['apiv3_key'].encode('utf-8')
    if len(key) != 32:
        raise RuntimeError('WXPAY_APIV3_KEY 必须是 32 位')
    aead = AESGCM(key)
    raw = aead.decrypt(
        str(resource.get('nonce', '')).encode('utf-8'),
        base64.b64decode(resource.get('ciphertext', '')),
        str(resource.get('associated_data', '')).encode('utf-8') or None,
    )
    return json.loads(raw.decode('utf-8'))
