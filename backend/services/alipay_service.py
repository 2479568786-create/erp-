"""支付宝开放平台适配器（A1：配置 + RSA2 签名 + 异步通知验签 + 金额换算）。

与微信 APIv3 的差异（**不要照抄 wxpay_service**）：
- 签名在**请求参数**里（`sign` + `sign_type=RSA2`），不是 HTTP 头；
- 待签串 = 剔除 `sign`/`sign_type` 后，**非空参数按 key 升序**拼 `k=v&k=v`（值用原始值，不做二次编码）；
- 请求用**应用私钥**签名，回调验签用**支付宝公钥**（不是「应用公钥」）；
- 回调报文是 **明文表单**，不加密、不需要 APIv3 密钥；
- 回调应答必须是**纯文本 `success`**（由路由层负责，本模块不产生响应）。

纪律：私钥/公钥只从文件路径读取，绝不写日志、不入库、不进仓库。缺项只报变量名，不返回值。
"""
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

PROD_GATEWAY = 'https://openapi.alipay.com/gateway.do'
SANDBOX_GATEWAY = 'https://openapi.alipaydev.com/gateway.do'
DEFAULT_GATEWAY = PROD_GATEWAY
DEFAULT_KEY_DIR = '/etc/geekozon-erp-pay/alipay'

# 参与签名的参数里必须剔除的两个字段
SIGN_EXCLUDE = ('sign', 'sign_type')
SUPPORTED_SIGN_TYPES = ('RSA2',)

_private_key_cache = {}
_public_key_cache = {}

# 只允许 10 / 10.0 / 10.00 这类十进制写法
_AMOUNT_RE = re.compile(r'^\d{1,7}(\.\d{1,2})?$')


# ---------------- 异常（路由层按类型映射 HTTP 状态） ----------------

class AlipayError(RuntimeError):
    """支付宝适配器错误基类。"""


class AlipayConfigError(AlipayError):
    """配置/密钥类问题：路由层应回 503（不是用户的错，也不是伪造）。"""


class AlipayTransportError(AlipayError):
    """网络/网关层失败：路由层应回 502。"""


class AlipayBizError(AlipayError):
    """业务失败（code != 10000）：携带 code / sub_code 供排障，不打日志值时注意。"""

    def __init__(self, code, sub_code='', message=''):
        self.code = str(code or '')
        self.sub_code = str(sub_code or '')
        self.message = str(message or '')
        super(AlipayBizError, self).__init__(
            '支付宝业务失败：code=%s sub_code=%s' % (self.code, self.sub_code))


def _env(name, default=''):
    return (os.environ.get(name) or default).strip()


def config():
    """读取 ALIPAY_* 环境变量（只放路径与开关，不放密钥内容）。"""
    key_dir = _env('ALIPAY_KEY_DIR', DEFAULT_KEY_DIR)
    gateway = _env('ALIPAY_GATEWAY', DEFAULT_GATEWAY)
    return {
        'enabled': _env('ALIPAY_ENABLED', '0') == '1',
        'app_id': _env('ALIPAY_APPID'),
        'private_key_path': _env('ALIPAY_PRIVATE_KEY_PATH')
                            or os.path.join(key_dir, 'app_private_key.pem'),
        'public_key_path': _env('ALIPAY_PUBLIC_KEY_PATH')
                           or os.path.join(key_dir, 'alipay_public_key.pem'),
        'notify_url': _env('ALIPAY_NOTIFY_URL'),
        'refund_notify_url': _env('ALIPAY_REFUND_NOTIFY_URL'),
        'gateway': gateway,
        'seller_id': _env('ALIPAY_SELLER_ID'),
        'sandbox': 'alipaydev' in gateway,
    }


def missing_config(env=None):
    """缺失项清单（只含变量名/文件名标记，不含值）。"""
    if env is None:
        cfg = config()
    else:
        saved = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(env)
            cfg = config()
        finally:
            os.environ.clear()
            os.environ.update(saved)
    missing = []
    for name, value in (
        ('ALIPAY_APPID', cfg['app_id']),
        ('ALIPAY_NOTIFY_URL', cfg['notify_url']),
    ):
        if not value:
            missing.append(name)
    if not os.path.exists(cfg['private_key_path']):
        missing.append('ALIPAY_PRIVATE_KEY_FILE')
    if not os.path.exists(cfg['public_key_path']):
        missing.append('ALIPAY_PUBLIC_KEY_FILE')
    return missing


# ---------------- 签名 / 验签 ----------------

def sign_string(params):
    """支付宝待签串：剔除 sign/sign_type，非空值按 key 升序，拼 k=v&k=v（原始值）。"""
    items = [(k, v) for k, v in params.items()
             if k not in SIGN_EXCLUDE and v is not None and v != '']
    items.sort(key=lambda kv: kv[0])
    return '&'.join('%s=%s' % (k, v) for k, v in items)


def _load_private_key(path):
    key = _private_key_cache.get(path)
    if key is None:
        with open(path, 'rb') as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        _private_key_cache[path] = key
    return key


def _load_public_key(path):
    key = _public_key_cache.get(path)
    if key is None:
        with open(path, 'rb') as f:
            key = serialization.load_pem_public_key(f.read())
        _public_key_cache[path] = key
    return key


def sign(params, private_key=None, private_key_path=None):
    """对参数签名，返回 base64 字符串（RSA2 = SHA256withRSA，PKCS#1 v1.5）。

    可传入已加载的 private_key（单测用），否则按 path 加载。
    """
    text = sign_string(params)
    key = private_key or _load_private_key(
        private_key_path or config()['private_key_path'])
    signature = key.sign(text.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode('ascii')


def verify_notify(params, public_key=None, public_key_path=None):
    """校验支付宝异步通知。返回 (ok, reason)。

    params 应是回调表单的全部字段（含 sign、sign_type）。失败原因只给短标签，便于记事件。
    """
    if not params:
        return False, 'empty_params'
    fields = dict(params)
    signature = (fields.pop('sign', '') or '').strip()
    sign_type = (fields.pop('sign_type', 'RSA2') or 'RSA2').strip().upper()
    if not signature:
        return False, 'missing_sign'
    if sign_type not in SUPPORTED_SIGN_TYPES:
        return False, 'bad_sign_type:%s' % sign_type

    text = sign_string(fields)
    try:
        raw_sig = base64.b64decode(signature, validate=True)
    except Exception:  # noqa: BLE001
        return False, 'bad_sign_encoding'
    # 公钥加载失败属于「我方配置错」，必须抛出去让上层回 503 并告警，
    # 不能混成「签名不合法」（否则运维会以为是被伪造攻击，掩盖真正的缺钥匙问题）。
    key = public_key or _load_public_key(
        public_key_path or config()['public_key_path'])
    try:
        key.verify(raw_sig, text.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:  # noqa: BLE001
        return False, 'bad_signature:%s' % type(exc).__name__
    return True, 'ok'


# ---------------- 金额 ----------------

def amount_to_cents(value):
    """支付宝金额（元，字符串/数字）→ 分（int）。

    "10.00"→1000；"0.01"→1；"10"→1000；最多两位小数，否则抛 ValueError。
    只接受纯十进制写法（拒科学计数法/正负号/空白），避免 '1e3' 这类被 Decimal 宽容解析。
    """
    if value is None:
        raise ValueError('金额为空')
    text = str(value).strip()
    if not text:
        raise ValueError('金额为空')
    if not _AMOUNT_RE.match(text):
        raise ValueError('金额格式不合法: %r' % text)
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError('金额格式不合法: %r' % text)
    if amount <= 0:
        raise ValueError('金额必须大于 0')
    if amount.as_tuple().exponent < -2:
        raise ValueError('金额最多两位小数: %r' % text)
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ValueError('金额换算后不是整数分: %r' % text)
    if cents > Decimal('100000000'):      # 1,000,000 元上限，防误传
        raise ValueError('金额超出上限: %r' % text)
    return int(cents)


def cents_to_yuan(cents):
    """分 → 支付宝要求的元字符串（两位小数）。1000 → '10.00'。"""
    return '%.2f' % (Decimal(int(cents)) / Decimal(100))


# ---------------- 公共参数（A2 下单用） ----------------

def build_common_params(method, biz_content, cfg=None, timestamp=None, notify_url=None,
                        with_notify_url=True):
    """构造公共参数（不含 sign）。biz_content 传 dict，会被序列化为 JSON 字符串。"""
    cfg = cfg or config()
    if timestamp is None:
        # 支付宝要求北京时间 yyyy-MM-dd HH:mm:ss
        from datetime import datetime, timedelta, timezone
        timestamp = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
    params = {
        'app_id': cfg['app_id'],
        'method': method,
        'format': 'JSON',
        'charset': 'utf-8',
        'sign_type': 'RSA2',
        'timestamp': timestamp,
        'version': '1.0',
    }
    if with_notify_url and (notify_url or cfg['notify_url']):
        params['notify_url'] = notify_url or cfg['notify_url']
    if isinstance(biz_content, (dict, list)):
        params['biz_content'] = json.dumps(biz_content, ensure_ascii=False, separators=(',', ':'))
    elif biz_content:
        params['biz_content'] = biz_content
    return params


# ---------------- 请求 / 响应 ----------------

NOTIFY_METHODS = ('alipay.trade.precreate', 'alipay.trade.page.pay', 'alipay.trade.refund')
MAX_RESPONSE_BYTES = 512 * 1024


def _post_form(params, cfg, timeout=15):
    """按支付宝网关要求 POST 表单（不要 JSON body）。"""
    body = urllib.parse.urlencode(params).encode('utf-8')
    req = urllib.request.Request(
        cfg['gateway'], data=body, method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8',
                 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(MAX_RESPONSE_BYTES).decode('utf-8', 'replace')[:300]
        raise AlipayTransportError('支付宝网关 HTTP %s：%s' % (exc.code, detail))
    except Exception as exc:  # noqa: BLE001 - 连接失败/超时/DNS
        raise AlipayTransportError('支付宝网关不可达：%s: %s' % (type(exc).__name__, exc))
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AlipayTransportError('支付宝响应过大')
    return raw.decode('utf-8')


def extract_response_node(raw, node_key):
    """从原始响应里切出 `<node_key>` 对应的 JSON 子串。

    支付宝的响应签名是**对这段原始子串**算的（不是整体 body、也不是重新序列化），
    所以必须先按字节切出来再验，任何 json.dumps 再序列化都会验签失败。
    """
    idx = raw.find('"%s"' % node_key)
    if idx < 0:
        raise RuntimeError('响应缺少节点 %s' % node_key)
    start = raw.find('{', idx)
    if start < 0:
        raise RuntimeError('响应节点不是对象')
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return raw[start:i + 1]
    raise RuntimeError('响应节点未闭合')


def verify_response(raw, node_text, public_key=None, public_key_path=None):
    """验支付宝**响应**签名（与回调一样用支付宝公钥，但待签内容=节点原始子串）。"""
    try:
        data = json.loads(raw)
    except ValueError:
        raise RuntimeError('响应不是合法 JSON')
    signature = data.get('sign')
    if not signature:
        raise RuntimeError('响应缺少 sign')
    key = public_key or _load_public_key(public_key_path or config()['public_key_path'])
    key.verify(base64.b64decode(signature), node_text.encode('utf-8'),
               padding.PKCS1v15(), hashes.SHA256())


def parse_response(raw, node_key, public_key=None, public_key_path=None, verify=True):
    """解析响应节点；verify=True 时先验签。返回节点 dict（含 code/msg/sub_code）。"""
    node_text = extract_response_node(raw, node_key)
    if verify:
        verify_response(raw, node_text, public_key=public_key, public_key_path=public_key_path)
    return json.loads(node_text)


def request(method, biz_content, cfg=None, private_key=None, public_key=None,
            http_post=None, notify_url=None, verify=True):
    """发一次支付宝请求并返回响应节点 dict。http_post 可注入（单测用）。"""
    cfg = cfg or config()
    params = build_common_params(method, biz_content, cfg=cfg, notify_url=notify_url,
                                 with_notify_url=(method in NOTIFY_METHODS))
    try:
        params['sign'] = sign(params, private_key=private_key,
                              private_key_path=cfg['private_key_path'])
    except OSError as exc:      # 私钥缺失/不可读 → 配置问题，不该算作下单失败
        raise AlipayConfigError('读取应用私钥失败：%s' % type(exc).__name__)
    raw = (http_post or _post_form)(params, cfg)
    node_key = method.replace('.', '_') + '_response'
    return parse_response(raw, node_key, public_key=public_key, verify=verify)


def require_ok(node):
    """校验业务码：`code` 缺省或 10000 视为成功，否则抛 AlipayBizError。"""
    code = str(node.get('code') or '')
    if code and code != '10000':
        raise AlipayBizError(code, node.get('sub_code') or '',
                             node.get('sub_msg') or node.get('msg') or '')
    return node


def query_trade(out_trade_no, cfg=None, **kwargs):
    """alipay.trade.query：按商户订单号查单（对账补偿用）。"""
    return request('alipay.trade.query', {'out_trade_no': out_trade_no}, cfg=cfg, **kwargs)


def close_trade(out_trade_no, cfg=None, **kwargs):
    """alipay.trade.close：关单（为避免关掉已支付订单，调用前应先 query）。"""
    return request('alipay.trade.close', {'out_trade_no': out_trade_no}, cfg=cfg, **kwargs)


def precreate(out_trade_no, amount_cents, subject='Geekfast ERP 图片点数充值',
              timeout_express='15m', passback_params=None, cfg=None, **kwargs):
    """alipay.trade.precreate（当面付）：预下单，返回响应节点（含 `qr_code`）。

    - 金额单位是**元**且两位小数字符串（`cents_to_yuan`），不是分；
    - `timeout_express` 与本地订单 TTL（15 分钟）保持一致；
    - `passback_params` 可回传业务标记（支付宝原样带回），这里默认不用。
    """
    biz = {
        'out_trade_no': out_trade_no,
        'total_amount': cents_to_yuan(amount_cents),
        'subject': str(subject or 'Geekfast ERP 充值')[:256],
        'timeout_express': timeout_express,
    }
    if passback_params:
        biz['passback_params'] = str(passback_params)[:512]
    node = require_ok(request('alipay.trade.precreate', biz, cfg=cfg, **kwargs))
    if not str(node.get('qr_code') or '').strip():
        raise AlipayError('预下单成功但未返回 qr_code')
    return node
