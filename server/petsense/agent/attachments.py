"""W07 聊天图片附件：校验、封顶重编码、内容寻址存储与读取（agent-w07-spec.md §3.2）。

图片按用户目录隔离（agent_media/<uid>/<sha256>.jpg），内容寻址天然去重；
无独立元数据表——引用与尺寸随 agent_turns.images JSON 落库（spec §2 实现默认）。
校验链对齐 social save_upload_image 的行为基线（decode→verify→EXIF 转正→重编码），
服务端强制封顶长边 1280（D2：客户端压缩只是第一道，不可信其对端）。
"""
import base64
import hashlib
import io
import os
import re
import secrets

from PIL import Image, ImageOps, UnidentifiedImageError

from petsense import config

MAX_EDGE = 1280           # D2 服务端封顶（长边 px）
MAX_PIXELS = 36_000_000   # 解码安全上限（与 social 一致）
JPEG_QUALITY = 85
MAX_PER_USER = 2000       # D3 配额（张）
IMAGE_ID_RE = re.compile(r'^[0-9a-f]{64}$')


class ImageRejected(ValueError):
    """上传被拒（400）：code 为 invalid_image 或 storage_full。"""

    def __init__(self, message, code='invalid_image'):
        super().__init__(message)
        self.code = code


def image_path(user_id, image_id):
    """id 形如 sha256 hex64 才拼路径（防遍历）；归属即路径：只在本用户目录下找。"""
    if not isinstance(image_id, str) or not IMAGE_ID_RE.match(image_id):
        return None
    return os.path.join(config.AGENT_MEDIA_DIR, str(user_id), image_id + '.jpg')


def save_agent_image(file_storage, user_id):
    """校验、封顶重编码并保存；返回 {'id','width','height','bytes'}（幂等：同内容同 id）。"""
    data = file_storage.read(config.MAX_UPLOAD_BYTES + 1)
    if not data or len(data) > config.MAX_UPLOAD_BYTES:
        raise ImageRejected('图片为空或超过 10MB')
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ImageRejected('仅支持 JPG / PNG / WebP 图片')
            if source.width * source.height > MAX_PIXELS:
                raise ImageRejected('图片像素过大')
            source.verify()
        with Image.open(io.BytesIO(data)) as source:
            oriented = ImageOps.exif_transpose(source).convert('RGB')
            if max(oriented.size) > MAX_EDGE:
                oriented.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
            width, height = oriented.size
            output = io.BytesIO()
            oriented.save(output, format='JPEG', quality=JPEG_QUALITY, optimize=True)
            cleaned = output.getvalue()
    except ImageRejected:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageRejected('图片已损坏或格式不支持') from None
    if not cleaned or len(cleaned) > config.MAX_UPLOAD_BYTES:
        raise ImageRejected('处理后的图片超过 10MB')
    image_id = hashlib.sha256(cleaned).hexdigest()
    directory = os.path.join(config.AGENT_MEDIA_DIR, str(user_id))
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, image_id + '.jpg')
    if not os.path.exists(path):
        if len(os.listdir(directory)) >= MAX_PER_USER:
            raise ImageRejected('图片数量已达上限，请先清理历史图片', code='storage_full')
        temp = path + '.' + secrets.token_hex(4) + '.tmp'
        try:
            with open(temp, 'wb') as handle:
                handle.write(cleaned)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.remove(temp)
    return {'id': image_id, 'width': width, 'height': height, 'bytes': len(cleaned)}


def read_meta(user_id, image_id):
    """读取既有图片的尺寸与字节数（发送消息时校验引用并落 images 元数据）。"""
    path = image_path(user_id, image_id)
    if not path or not os.path.isfile(path):
        return None
    with Image.open(path) as image:
        width, height = image.size
    return {'id': image_id, 'width': width, 'height': height, 'bytes': os.path.getsize(path)}


def load_data_url(user_id, image_id):
    """读取图片为 data URL 供模型 input_image 内联；不存在返回 None（调用方降级占位）。"""
    path = image_path(user_id, image_id)
    if not path or not os.path.isfile(path):
        return None
    with open(path, 'rb') as handle:
        return 'data:image/jpeg;base64,' + base64.b64encode(handle.read()).decode()
