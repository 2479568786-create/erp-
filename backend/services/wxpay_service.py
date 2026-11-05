"""微信支付 APIv3 实现（Native 下单 / 查单 / 关单 / 回调验签与解密）。

配置全部来自环境变量（前缀 WECHATPAY_*，见 /etc/geekozon-erp.env）：
  WECHATPAY_ENABLED / WECHATPAY_MCHID / WECHATPAY_APPID / WECHATPAY_API_V3_KEY
  WECHATPAY_CERT_SERIAL / WECHATPAY_CERT_DIR / WECHATPAY_PRIVATE_KEY_PATH
  WECHATPAY_PUBKEY_ID / WECHATPAY_PUBKEY_PATH / WECHATPAY_NOTIFY_URL
  WECHATPAY_CALLBACK_IPS / WECHATPAY_CALLBACK_IP_MODE(log|strict)

纪律：私钥、APIv3 密钥只从文件/环境读取，绝不写日志、绝不入库、绝不进仓库。
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

API_BASE = 'https://api.mch.weixin.qq.com'
USER_AGENT = 'geekfast-erp/1.0 (wxpay-apiv3)'
MAX_CLOCK_SKEW = 300          # 秒
MAX_CALLBACK_BYTES = 64 * 1024  # 回调报文上限 64KB

_private_key_cache = {}
_public_key_cache = {}

DEFAULT_CERT_DIR = '/etc/geekozon-erp/wechatpay'


def _env(name, default=''):
    return (os.environ.get(name) or default).strip()


def config():
    cert_dir = _env('WECHATPAY_CERT_DIR', DEFAULT_CERT_DIR)
    return {
        'enabled': _env('WECHATPAY_ENABLED', '0') == '1',
        'mchid': _env('WECHATPAY_MCHID'),
        'appid': _env('WECHATPAY_APPID'),
        'apiv3_key': _env('WECHATPAY_API_V3_KEY'),
        'serial': _env('WECHATPAY_CERT_SERIAL'),
        'cert_dir': cert_dir,
        'private_key_path': _env('WECHATPAY_PRIVATE_KEY_PATH') or os.path.join(cert_dir, 'apiclient_key.pem'),
        'cert_path': os.path.join(cert_dir, 'apiclient_cert.pem'),
        'notify_url': _env('WECHATPAY_NOTIFY_URL'),
        'pub_key_id': _env('WECHATPAY_PUBKEY_ID'),
        'pub_key_path': _env('WECHATPAY_PUBKEY_PATH') or os.path.join(cert_dir, 'pub_key.pem'),
        'callback_ips': [ip.strip() for ip in _env('WECHATPAY_CALLBACK_IPS').split(',') if ip.strip()],
        'callback_ip_mode': (_env('WECHATPAY_CALLBACK_IP_MODE', 'log') or 'log').lower(),
    }


def missing_config():
    """缺失项（名字，不含值）。文件类缺项用 NAME:file 形式标出。"""
    cfg = config()
    missing = []
    for name, value in (
        ('WECHATPAY_MCHID', cfg['mchid']),
        ('WECHATPAY_APPID', cfg['appid']),
        ('WECHATPAY_API_V3_KEY', cfg['apiv3_key']),
        ('WECHATPAY_CERT_SERIAL', cfg['serial']),
        ('WECHATPAY_NOTIFY_URL', cfg['notify_url']),
        ('WECHATPAY_PUBKEY_ID', cfg['pub_key_id']),
    ):
        if not value:
            missing.append(name)
    if not os.path.exists(cfg['private_key_path']):
        missing.append('WECHATPAY_PRIVATE_KEY_FILE')
    if not (os.path.exists(cfg['pub_key_path']) or os.path.exists(os.path.join(cfg['cert_dir'], 'wechatpay_platform.pem'))):
        missing.append('WECHATPAY_PUBKEY_FILE')
    return missing


def _load_private_key(cfg):
    path = cfg['private_key_path']
    key = _private_key_cache.get(path)
    if key is None:
        with open(path, 'rb') as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        _private_key_cache[path] = key
    return key


def _load_public_key(cfg):
    path = cfg['pub_key_path']
    if os.path.exists(path):
        key = _public_key_cache.get(path)
        if key is None:
            with open(path, 'rb') as f:
                key = serialization.load_pem_public_key(f.read())
            _public_key_cache[path] = key
        return key
    platform = os.path.join(cfg['cert_dir'], 'wechatpay_platform.pem')
    if os.path.exists(platform):
        cert = x509.load_pem_x509_certificate(open(platform, 'rb').read())
        return cert.public_key()
    raise RuntimeError('缺少微信支付公钥/平台证书')


def build_authorization(method, url_path, body='', cfg=None, private_key=None):
    """Authorization: WECHATPAY2-SHA256-RSA2048 ...（url_path 需带 query）。"""
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
    if not cfg['appid']:
        # 官方文档：Native 下单 appid 必填，且必须与 mchid 绑定
        raise RuntimeError('缺少 WECHATPAY_APPID：Native 下单 appid 必填（需与商户号绑定）')
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
    """验签回调。返回 (ok, reason)。"""
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
        raise RuntimeError('WECHATPAY_API_V3_KEY 必须是 32 位')
    aead = AESGCM(key)
    raw = aead.decrypt(
        str(resource.get('nonce', '')).encode('utf-8'),
        base64.b64decode(resource.get('ciphertext', '')),
        str(resource.get('associated_data', '')).encode('utf-8') or None,
    )
    return json.loads(raw.decode('utf-8'))
