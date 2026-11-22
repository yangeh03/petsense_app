"""宠物资料验证与头像解码；不处理设备关联或 AI 形象。

W03 增加生日（精度感知）与体重记录的解析校验（agent-w03-spec.md §3.2/§3.4），
档案页 PATCH 与 Agent 写入工具共用同一组函数（落库前复核唯一实现）。
"""
import io
import re
import warnings
from datetime import date

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from petsense import config
from petsense.common import shanghai_now
from petsense.db import session_scope, utc_now
from petsense.db.models import PetWeightRecord

DEFAULT_AVATARS = ('🐶', '🐱', '🐰', '🐾')

BIRTH_PRECISIONS = ('day', 'month', 'year')
# 生日合理窗口：太老的档案值多为口误（D1：不伪造精确，也不接受离谱值）
EARLIEST_BIRTH = date(1990, 1, 1)
EARLIEST_WEIGHT_DAY = date(2020, 1, 1)
WEIGHT_KG_RANGE = (0.1, 100.0)

_BIRTH_DAY_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')
_BIRTH_MONTH_RE = re.compile(r'^(\d{4})-(\d{2})$')
_BIRTH_YEAR_RE = re.compile(r'^(\d{4})$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def parse_birth_date(value, precision=None):
    """解析生日输入为 (date, precision)；格式决定精度，显式 precision 只允许降档。

    '2023-09-10'→day、'2023-09'→month、'2023'→year；year/month 分别落在该年/月 1 日。
    precision 显式给出且低于格式精度时按 precision 记（如「大概 2023 年 9 月某天」）。
    """
    if not isinstance(value, str):
        raise ValueError('生日需为文字（2023-09-10 / 2023-09 / 2023）')
    text = value.strip()
    match = _BIRTH_DAY_RE.match(text)
    if match:
        parsed, derived = date(int(match.group(1)), int(match.group(2)), int(match.group(3))), 'day'
    else:
        match = _BIRTH_MONTH_RE.match(text)
        if match:
            parsed, derived = date(int(match.group(1)), int(match.group(2)), 1), 'month'
        else:
            match = _BIRTH_YEAR_RE.match(text)
            if match:
                parsed, derived = date(int(match.group(1)), 1, 1), 'year'
            else:
                raise ValueError('生日格式需为 2023-09-10 / 2023-09 / 2023')
    order = {p: i for i, p in enumerate(BIRTH_PRECISIONS)}
    if precision is not None:
        if precision not in order:
            raise ValueError('生日精度无效')
        if order[precision] < order[derived]:
            raise ValueError('生日精度不能高于日期本身给出的精度')
        derived = precision
    today = shanghai_now().date()
    if parsed > today:
        raise ValueError('生日不能晚于今天')
    if parsed < EARLIEST_BIRTH:
        raise ValueError('生日年份需在 1990 年之后')
    return parsed, derived


def age_years(birth_date, precision, today=None):
    """按上海日期推导年龄；返回 (周岁, 是否估算)。precision 为 month/year 时估算标志为 True。"""
    if birth_date is None or birth_date_precision_invalid(precision):
        return None
    today = today or shanghai_now().date()
    years = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    return max(0, years), precision in ('month', 'year')


def birth_date_precision_invalid(precision):
    return precision not in BIRTH_PRECISIONS


def parse_measured_on(value=None):
    """体重发生日期：缺省今天（上海）；不得晚于今天、早于 2020-01-01。"""
    today = shanghai_now().date()
    if value is None:
        return today
    if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
        raise ValueError('体重日期需为 YYYY-MM-DD')
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError('体重日期无效') from exc
    if parsed > today:
        raise ValueError('体重日期不能晚于今天')
    if parsed < EARLIEST_WEIGHT_DAY:
        raise ValueError('体重日期需在 2020-01-01 之后')
    return parsed


def validate_weight_kg(value):
    """体重 kg 校验，返回整数克（0.1–100 kg；存储用克避免浮点误差）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('体重需为数字（公斤）')
    kg = float(value)
    if not WEIGHT_KG_RANGE[0] <= kg <= WEIGHT_KG_RANGE[1]:
        raise ValueError('体重需在 0.1–100 公斤之间')
    return int(round(kg * 1000))


def validate_weight_record(data):
    """档案页体重提交校验：{measuredOn?, weightKg} → (measured_on, weight_g)。"""
    if not isinstance(data, dict) or 'weightKg' not in data:
        raise ValueError('请提交体重（公斤）')
    extra = set(data) - {'measuredOn', 'weightKg'}
    if extra:
        raise ValueError('体重记录字段无效')
    return parse_measured_on(data.get('measuredOn')), validate_weight_kg(data['weightKg'])


def upsert_weight_record(session, pet_id, measured_on, weight_g, source, raw_text=None, now=None):
    """按日 upsert：同日再记覆盖当日（保留最新），跨日追加。返回记录行。"""
    record = session.execute(select(PetWeightRecord).where(
        PetWeightRecord.pet_id == pet_id, PetWeightRecord.measured_on == measured_on)).scalar_one_or_none()
    if record is None:
        record = PetWeightRecord(pet_id=pet_id, measured_on=measured_on, weight_g=weight_g,
                                 source=source, raw_text=raw_text, created_at=now or utc_now())
        session.add(record)
    else:
        record.weight_g = weight_g
        record.source = source
        record.raw_text = raw_text
    session.flush()
    return record


def weight_record_dict(record):
    """体重记录序列化（kg 两位小数展示）。"""
    return {'id': record.id, 'measuredOn': record.measured_on.isoformat(),
            'weightKg': round(record.weight_g / 1000, 2),
            'rawText': record.raw_text, 'source': record.source}


def latest_weight(pet_id):
    """最新一条体重记录（measured_on 最大）；开独立会话，供 pet_dict 复用。"""
    with session_scope() as s:
        record = s.execute(select(PetWeightRecord).where(PetWeightRecord.pet_id == pet_id)
                           .order_by(PetWeightRecord.measured_on.desc())).scalars().first()
        return weight_record_dict(record) if record else None


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
    if 'birthDate' in data:
        value = data['birthDate']
        if value is None or (isinstance(value, str) and not value.strip()):
            fields['birth_date'] = None
            fields['birth_date_precision'] = None
        else:
            fields['birth_date'], fields['birth_date_precision'] = parse_birth_date(value)
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
