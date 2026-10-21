"""
图片存储抽象层
支持本地存储（默认）和七牛云对象存储（可选，生产环境推荐）

通过 backend/.env 中 OBJECT_STORAGE_TYPE 切换：
- local  : 保存到本地 uploads 目录，依赖 IMAGE_BASE_URL 生成公网 URL
- qiniu  : 上传到七牛云对象存储，返回永久公网 URL（需配置七牛云凭证）

七牛云有免费额度（10GB 存储 + 10GB/月流量），适合中小型 ERP。
"""
import os
import hmac
import hashlib
import base64
import json
import time
import uuid as _uuid
import urllib.request
import urllib.error
from config import Config


_IMAGE_MAGIC = (
    (b'\xff\xd8\xff', 'jpg'),
    (b'\x89PNG\r\n\x1a\n', 'png'),
    (b'GIF8', 'gif'),
)


def sniff_image_ext(data, declared_ext=None):
    """按文件内容（magic bytes）判断真实图片格式，返回正确扩展名。

    为什么必须这么做：/api/images/upload 过去直接采信客户端上传的**文件名后缀**，
    一张 WebP 只要被命名为 photo.jpg 就会以 .jpg 落盘，于是对外声明为 image/jpeg、
    实际内容是 WebP。Ozon 会按真实内容校验，结果就是「图片传上去了但平台不认」——
    这正是"图片无法展示"最隐蔽的一类成因。
    """
    if not data:
        return (declared_ext or 'jpg')
    head = bytes(data[:16])
    for magic, ext in _IMAGE_MAGIC:
        if head.startswith(magic):
            return ext
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'webp'
    if head[:2] == b'BM':
        return 'bmp'
    # 无法识别：保守沿用调用方声明的后缀（仍会被下游格式校验拦截）
    ext = (declared_ext or 'jpg').lower().lstrip('.')
    return 'jpg' if ext == 'jpeg' else ext


def _request_base_url():
    """从当前 Flask 请求推断公网基地址（支持反向代理的 X-Forwarded-* 头）。

    没有请求上下文（后台任务/线程）时返回空字符串。
    """
    try:
        from flask import has_request_context, request
        if has_request_context():
            proto = (request.headers.get('X-Forwarded-Proto')
                     or request.headers.get('X-Forwarded-Protocol')
                     or request.scheme or 'http')
            host = (request.headers.get('X-Forwarded-Host')
                    or request.headers.get('Host')
                    or request.host)
            if host:
                return f'{proto}://{host}'
    except Exception:
        pass
    return ''


def _is_usable_base(base):
    """基地址必须是非 localhost 的公网地址才可用于 Ozon"""
    if not base:
        return False
    b = str(base).lower()
    return ('://localhost' not in b) and ('://127.0.0.1' not in b) and ('://0.0.0.0' not in b)


def resolve_base_url(explicit=None):
    """解析图片公网基地址。

    优先级：调用方显式传入 → 环境变量 IMAGE_BASE_URL → 当前请求 Host → localhost 兜底。

    这样即使运维忘记配置 IMAGE_BASE_URL，上传接口也会返回
    http://<用户访问的域名或IP>/uploads/xxx 而不是无法被 Ozon 访问的 localhost。
    """
    for candidate in (explicit, Config.IMAGE_BASE_URL, _request_base_url()):
        if _is_usable_base(candidate):
            return str(candidate).rstrip('/')
    # 兜底：优先仍返回请求 Host（哪怕它是内网地址），最后才是 localhost
    for candidate in (explicit, Config.IMAGE_BASE_URL, _request_base_url()):
        if candidate:
            return str(candidate).rstrip('/')
    return 'http://localhost:5000'


def _urlsafe_b64encode(data):
    """七牛云 URL Safe Base64 编码（替换 +/ 为 -_）"""
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')


def _gen_qiniu_token(access_key, secret_key, bucket, key=None):
    """生成七牛云上传 token

    Args:
        access_key: 七牛云 AK
        secret_key: 七牛云 SK
        bucket: 存储空间名
        key: 指定上传的文件名（可选，不指定则由七牛云自动生成）
    """
    # putPolicy: scope=bucket:key（指定 key）或 scope=bucket（自动生成 key）
    scope = f'{bucket}:{key}' if key else bucket
    put_policy = {
        'scope': scope,
        'deadline': int(time.time()) + 3600,  # 1小时有效期
    }
    encoded_put_policy = _urlsafe_b64encode(json.dumps(put_policy).encode('utf-8'))
    sign = hmac.new(secret_key.encode('utf-8'), encoded_put_policy.encode('utf-8'), hashlib.sha1).digest()
    encoded_sign = _urlsafe_b64encode(sign)
    return f'{access_key}:{encoded_sign}:{encoded_put_policy}'


def _upload_to_qiniu(image_data, filename):
    """上传图片到七牛云，返回公网 URL

    使用七牛云 HTTP 上传 API（无需安装 SDK）
    API 文档: https://developer.qiniu.com/kodo/1312/upload

    Returns:
        str: 公网可访问的图片 URL，失败返回 None
    """
    ak = Config.QINIU_ACCESS_KEY
    sk = Config.QINIU_SECRET_KEY
    bucket = Config.QINIU_BUCKET
    domain = Config.QINIU_DOMAIN.rstrip('/')

    if not all([ak, sk, bucket, domain]):
        return None

    try:
        token = _gen_qiniu_token(ak, sk, bucket, filename)

        # 构造 multipart/form-data
        boundary = '----GeekOzonBoundary' + _uuid.uuid4().hex[:8]
        body = b''
        # token 字段
        body += f'--{boundary}\r\n'.encode()
        body += b'Content-Disposition: form-data; name="token"\r\n\r\n'
        body += token.encode() + b'\r\n'
        # key 字段
        body += f'--{boundary}\r\n'.encode()
        body += b'Content-Disposition: form-data; name="key"\r\n\r\n'
        body += filename.encode() + b'\r\n'
        # file 字段
        body += f'--{boundary}\r\n'.encode()
        body += f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
        body += b'Content-Type: application/octet-stream\r\n\r\n'
        body += image_data + b'\r\n'
        body += f'--{boundary}--\r\n'.encode()

        req = urllib.request.Request(
            'https://upload.qiniup.com',
            data=body,
            method='POST',
        )
        req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            if result.get('key'):
                return f'{domain}/{result["key"]}'

    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')
        print(f'[对象存储] 七牛云上传失败 HTTP {e.code}: {err_body[:200]}')
    except Exception as e:
        print(f'[对象存储] 七牛云上传异常: {e}')

    return None


def save_image(image_data, ext='jpg', base_url=None):
    """保存图片二进制数据，返回公网可访问的 URL

    根据配置自动选择存储方式：
    - OBJECT_STORAGE_TYPE=local（默认）: 保存到本地 uploads，URL 依赖 IMAGE_BASE_URL
    - OBJECT_STORAGE_TYPE=qiniu: 上传到七牛云，返回永久公网 URL

    Args:
        image_data: 图片二进制数据
        ext: 文件扩展名（jpg/png/webp/gif）

    Returns:
        str: 图片 URL
    """
    # 以真实内容为准修正扩展名（不信任调用方声明的后缀）
    ext = sniff_image_ext(image_data, ext)
    filename = f'{_uuid.uuid4().hex[:16]}.{ext}'

    storage_type = (Config.OBJECT_STORAGE_TYPE or 'local').lower()

    # 七牛云对象存储
    if storage_type == 'qiniu' and Config.QINIU_ACCESS_KEY:
        url = _upload_to_qiniu(image_data, filename)
        if url:
            return url
        # 上传失败，降级到本地存储
        print('[对象存储] 七牛云上传失败，降级到本地存储')

    # 本地存储
    os.makedirs(Config.UPLOAD_DIR, exist_ok=True)
    filepath = os.path.join(Config.UPLOAD_DIR, filename)
    with open(filepath, 'wb') as f:
        f.write(image_data)

    return f'{resolve_base_url(base_url)}/uploads/{filename}'
