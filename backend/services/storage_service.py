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
    """上传图片到七牛云，返回公网 URL（使用七牛官方 SDK，签名可靠）。

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
        from qiniu import Auth, put_data

        auth = Auth(ak, sk)
        token = auth.upload_token(bucket, filename, 3600)
        ret, info = put_data(token, filename, image_data)
        if info.status_code == 200 and ret and ret.get('key'):
            return f'{domain}/{ret["key"]}'
        print(f'[对象存储] 七牛云上传失败 HTTP {info.status_code}: {info.text_body}')

    except Exception as e:
        print(f'[对象存储] 七牛云上传异常: {e}')

    return None


def _upload_to_oss(image_data, filename):
    """上传图片到阿里云 OSS，返回 HTTPS 公网 URL。

    2026-10-30 修复「OSS 上传卡死拖垮发布队列」：
    - 上传走**内网 endpoint**（同区域免公网抖动），返回 URL 用**公网 endpoint**（Ozon 要能访问）；
    - 显式 connect_timeout/连接池，失败重试 3 次（指数退避），每次重试前丢弃坏 keep-alive 连接；
    - 全部失败返回 None，由上层降级/整单快速失败，绝不无限挂起。
    """
    ak = Config.OSS_ACCESS_KEY_ID
    sk = Config.OSS_ACCESS_KEY_SECRET
    bucket = Config.OSS_BUCKET
    upload_endpoint = (getattr(Config, 'OSS_UPLOAD_ENDPOINT', '') or Config.OSS_ENDPOINT or '').strip()
    public_endpoint = (getattr(Config, 'OSS_PUBLIC_ENDPOINT', '') or Config.OSS_ENDPOINT or '').strip()
    if not all([ak, sk, bucket, upload_endpoint]):
        return None

    try:
        import oss2

        oss2.defaults.connect_timeout = float(os.environ.get('OSS_CONNECT_TIMEOUT', '5'))
        oss2.defaults.connection_pool_size = int(os.environ.get('OSS_CONNECTION_POOL_SIZE', '10'))

        auth = oss2.Auth(ak, sk)
        last_error = None
        for attempt in range(3):
            bucket_obj = oss2.Bucket(
                auth, f'https://{upload_endpoint}', bucket,
                connect_timeout=float(os.environ.get('OSS_CONNECT_TIMEOUT', '5')),
            )
            try:
                bucket_obj.put_object(filename, image_data)
                return f'https://{bucket}.{public_endpoint}/{filename}'
            except Exception as exc:  # noqa: BLE001 - 单次失败要重试
                last_error = exc
                time.sleep(2 ** attempt)
                try:
                    bucket_obj.session.session.close()  # 丢弃可能卡死的 keep-alive 连接
                except Exception:
                    pass
        print(f'[对象存储] OSS 上传失败（已重试 3 次）: {last_error}')
    except Exception as exc:  # noqa: BLE001
        print(f'[对象存储] OSS 上传异常: {exc}')
    return None


def save_image(image_data, ext='jpg'):
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
    filename = f'{_uuid.uuid4().hex[:16]}.{ext}'

    storage_type = (Config.OBJECT_STORAGE_TYPE or 'local').lower()

    # 阿里云 OSS 对象存储（优先）
    if storage_type == 'oss' and Config.OSS_ACCESS_KEY_ID:
        url = _upload_to_oss(image_data, filename)
        if url:
            return url
        print('[对象存储] OSS 上传失败，降级到本地存储')

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

    base_url = Config.IMAGE_BASE_URL.rstrip('/') if Config.IMAGE_BASE_URL else 'http://localhost:5000'
    return f'{base_url}/uploads/{filename}'
