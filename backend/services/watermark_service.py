import json
import os

from config import DATA_DIR
from db import get_connection
from services.watermark_image import composite_watermark
from utils.auth import visible_store_pks

WATERMARK_DIR = os.path.join(DATA_DIR, 'watermarks')


def validate_generation_store(store_id: int, user: dict) -> None:
    if not user:
        raise PermissionError('请先登录')
    conn = get_connection()
    try:
        store = conn.execute('SELECT id FROM stores WHERE id = ?', (store_id,)).fetchone()
        if not store:
            raise ValueError('目标店铺不存在')
        visible = visible_store_pks(user)
        if visible is not None and store_id not in visible:
            raise PermissionError('无权使用该店铺生成图片')
    finally:
        conn.close()


def prepare_generated_image(image_data: bytes, store_id: int, user: dict) -> tuple[bytes, str]:
    validate_generation_store(store_id, user)
    conn = get_connection()
    try:
        row = conn.execute('SELECT filename, config FROM shop_watermarks WHERE store_id = ?', (store_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row['filename']:
        return image_data, 'not_configured'
    try:
        with open(os.path.join(WATERMARK_DIR, row['filename']), 'rb') as watermark_file:
            watermark_data = watermark_file.read()
        config = json.loads(row['config'] or '{}')
        return composite_watermark(image_data, watermark_data, config), 'applied'
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('水印合成失败，请检查店铺水印配置') from exc
