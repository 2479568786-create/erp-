import io

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_PIXELS = 40_000_000


def image_extension(image_data: bytes) -> str:
    try:
        with Image.open(io.BytesIO(image_data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError('图片尺寸过大')
            return {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp'}[image.format]
    except ValueError as exc:
        if str(exc) == '图片尺寸过大':
            raise
        raise ValueError('生成图片格式无效或不受支持') from exc
    except (UnidentifiedImageError, OSError, KeyError) as exc:
        raise ValueError('生成图片格式无效或不受支持') from exc


def composite_watermark(image_data: bytes, watermark_data: bytes, config: dict) -> bytes:
    """Composite one shop watermark onto a generated image, preserving its format and canvas."""
    settings = {'position': 'bottom-right', 'widthRatio': 0.18, 'opacity': 0.7, 'marginRatio': 0.03}
    settings.update(config or {})
    try:
        with Image.open(io.BytesIO(image_data)) as opened:
            image_format = opened.format
            if opened.width * opened.height > MAX_IMAGE_PIXELS:
                raise ValueError('图片尺寸过大')
            base = opened.convert('RGBA')
        with Image.open(io.BytesIO(watermark_data)) as opened:
            if opened.width * opened.height > MAX_IMAGE_PIXELS:
                raise ValueError('图片尺寸过大')
            watermark = opened.convert('RGBA')
    except ValueError as exc:
        if str(exc) == '图片尺寸过大':
            raise
        raise ValueError('原图或水印图片无法解码') from exc
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, TypeError) as exc:
        raise ValueError('原图或水印图片无法解码') from exc
    if image_format not in ('PNG', 'JPEG', 'WEBP'):
        raise ValueError('生成图片格式不受支持')

    width, height = base.size
    target_width = min(width, max(1, round(width * float(settings['widthRatio']))))
    target_height = max(1, round(watermark.height * target_width / watermark.width))
    if target_height > height:
        target_height = height
        target_width = max(1, round(watermark.width * target_height / watermark.height))
    watermark = watermark.resize((target_width, target_height), Image.Resampling.LANCZOS)
    watermark.putalpha(watermark.getchannel('A').point(lambda value: round(value * float(settings['opacity']))))

    margin = round(width * float(settings['marginRatio']))
    left = margin if settings['position'].endswith('left') else width - target_width - margin
    top = margin if settings['position'].startswith('top') else height - target_height - margin
    left = min(max(0, left), width - target_width)
    top = min(max(0, top), height - target_height)
    base.alpha_composite(watermark, (left, top))

    output = io.BytesIO()
    if image_format == 'JPEG':
        base.convert('RGB').save(output, format='JPEG', quality=95)
    else:
        base.save(output, format=image_format)
    return output.getvalue()
