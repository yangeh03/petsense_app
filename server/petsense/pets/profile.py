"""宠物资料验证与头像解码；不处理设备关联或 AI 形象。"""
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from petsense import config

DEFAULT_AVATARS = ('🐶', '🐱', '🐰', '🐾')


def validate_fields(data, existing_emoji=None):
    if not isinstance(data, dict):
        raise ValueError('请提交有效的宠物资料')
    fields = {}
    limits = {'name': (1, 20), 'breed': (0, 40), 'species': (1, 16), 'avatarEmoji': (1, 16)}
    labels = {'name': '名字', 'breed': '品种', 'species': '物种', 'avatarEmoji': '默认头像'}
    for key, (minimum, maximum) in limits.items():
        if key not in data:
            continue
        value = data[key]
        if not isinstance(value, str):
            raise ValueError(f'{labels[key]}必须是文字')
        value = value.strip()
        if not minimum <= len(value) <= maximum:
            raise ValueError(f'{labels[key]}需为 {minimum}—{maximum} 个字符')
        fields[{'avatarEmoji': 'avatar_emoji'}.get(key, key)] = value
    if 'species' in fields and fields['species'] not in ('dog', 'cat', 'rabbit'):
        raise ValueError('请选择狗狗、猫咪或兔子')
    emoji = fields.get('avatar_emoji')
    if emoji and emoji not in DEFAULT_AVATARS and emoji != existing_emoji:
        raise ValueError('请选择提供的默认头像')
    return fields


def decode_avatar(file):
    data = file.read(config.MAX_UPLOAD_BYTES + 1)
    if not data or len(data) > config.MAX_UPLOAD_BYTES:
        raise ValueError('图片为空或超过 10MB，请选择较小的图片')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise ValueError('仅支持 JPG / PNG / WebP 图片')
                if image.width * image.height > 16_000_000:
                    raise ValueError('图片分辨率过大，请选择小于 1600 万像素的图片')
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                avatar = ImageOps.exif_transpose(image).convert('RGBA')
                avatar.thumbnail((512, 512))
                output = io.BytesIO()
                avatar.save(output, format='PNG')
                return output.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError('图片无法读取，请选择有效的 JPG / PNG / WebP 图片') from exc
