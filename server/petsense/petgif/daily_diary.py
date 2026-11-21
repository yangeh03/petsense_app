"""Create daily diary jobs from owner-scoped observation snapshots."""
import io
import json
import math
import os
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from flask import Blueprint, request, send_file
from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from petsense import config
from petsense.auth.routes import require_app_user
from petsense.db import session_scope, utc_now
from petsense.db.models import Pet, PetgifDiaryJob, TelemetryMinute
from petsense.telemetry.observations import SHANGHAI

bp = Blueprint('daily_diary', __name__, url_prefix='/api/app/exercise-diaries')
FONT = Path(__file__).with_name('fonts') / 'DiarySans.ttf'
PILOT_STEPS_PER_MINUTE = 150


def _shared_pet_id():
    from petsense.petgif.proxy import PETGIF_KEY

    if os.getenv('PETSENSE_PETGIF_PRIVATE_DIARIES') != '1' or not PETGIF_KEY:
        return None
    try:
        return str(UUID(os.getenv('PETSENSE_PETGIF_DIARY_PET_ID', '')))
    except ValueError:
        return None


def _profile_photo(filename):
    if not filename:
        raise ValueError('请先在宠物档案添加照片')
    path = Path(config.UPLOADS_DIR) / Path(filename).name
    try:
        with Image.open(path) as source:
            if source.width * source.height > 16_000_000:
                raise ValueError('宠物档案照片分辨率过高，请更新照片')
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((1600, 1600))
            image.info.clear()
            photo = io.BytesIO()
            image.save(photo, format='JPEG', quality=90)
            photo.seek(0)
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('宠物档案照片无法读取，请更新照片') from exc
    return FileStorage(photo, filename='pet.jpg', content_type='image/jpeg')


def _snapshot(session, pet, start, now):
    rows = session.scalars(select(TelemetryMinute).where(
        TelemetryMinute.owner_id == pet.user_id, TelemetryMinute.device_id == pet.device_id,
        TelemetryMinute.minute >= start, TelemetryMinute.minute <= now,
    )).all() if pet.device_id else []
    observed = walking = 0.0
    for row in rows:
        actions = json.loads(row.actions)
        observed += sum(actions.values())
        walking += actions.get('walking', 0)
    return {
        'date': start.date().isoformat(),
        'name': pet.name,
        'avatar': pet.avatar_image,
        'walkingSeconds': round(walking) if observed else None,
        'observedSeconds': round(observed),
        'steps': math.floor(walking * PILOT_STEPS_PER_MINUTE / 60 + 0.5) if observed else None,
        'estimator': 'walking-150-spm-v1',
    }


@bp.route('/<int:pet_id>', methods=['GET', 'POST'])
def daily_diaries(pet_id):
    from petsense.petgif import diary_jobs

    user, error = require_app_user()
    if error:
        return error
    now = utc_now()
    start = now.astimezone(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0)
    date = start.date().isoformat()
    with session_scope() as session:
        pet = session.get(Pet, pet_id)
        if pet is None or pet.user_id != user['id']:
            return {'message': '宠物档案不存在'}, 404
        ownership = (
            PetgifDiaryJob.user_id == user['id'], PetgifDiaryJob.pet_id == pet_id,
        )
        upstream_id = _shared_pet_id()
        if request.method == 'GET':
            jobs = session.scalars(select(PetgifDiaryJob).where(
                *ownership, PetgifDiaryJob.idempotency_key.startswith('daily:'),
                PetgifDiaryJob.created_at >= now - timedelta(days=diary_jobs.JOB_TTL_DAYS),
            ).order_by(PetgifDiaryJob.created_at.desc()).limit(30)).all()
            return {'date': date, 'aiAvailable': bool(upstream_id),
                    'jobs': [diary_jobs.job_dict(job) for job in jobs]}
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return {'message': '请求格式无效'}, 400
        if body.get('mode', 'ai') != 'ai':
            return {'message': '请更新 App 后生成 AI 海报'}, 409
        prefix = f'daily:{date}:ai:'
        latest = session.scalar(select(PetgifDiaryJob).where(
            *ownership, PetgifDiaryJob.idempotency_key.startswith(prefix),
        ).order_by(PetgifDiaryJob.created_at.desc()).limit(1))
        replace_id = body.get('replaceJobId')
        if latest and (replace_id != latest.id or latest.status not in ('completed', 'failed')):
            return diary_jobs.job_dict(latest), 202
        if replace_id is not None and latest is None:
            return {'message': '只能更新今天的 AI 海报'}, 409
        if not upstream_id:
            return {'message': 'AI 海报暂未开放'}, 409
        if not pet.avatar_image:
            return {'message': '请先在宠物档案添加照片'}, 422
        fields = _snapshot(session, pet, start, now)
        if fields['steps'] is None or fields['walkingSeconds'] < 60:
            return {'message': '今天的有效走动记录不足 1 分钟'}, 409
        fields.update({'mode': 'ai', 'durationMin': round(fields['walkingSeconds'] / 60)})
        avatar = fields.pop('avatar')
        fields.pop('name')
        key = prefix + (latest.id if latest else 'first')
    try:
        with closing(_profile_photo(avatar)) as photo:
            paths = diary_jobs.store_photos([photo])
    except ValueError as exc:
        return {'message': str(exc)}, 422
    try:
        job, reused = diary_jobs.create_job(user['id'], pet_id, upstream_id, key, fields, paths)
    except Exception:
        diary_jobs.remove_photos(paths)
        raise
    if not reused:
        diary_jobs.enqueue_diary_job(job.id)
    return diary_jobs.job_dict(job), 202


@bp.route('/poster/<uuid:job_id>')
def diary_poster(job_id):
    user, error = require_app_user()
    if error:
        return error
    with session_scope() as session:
        job = session.get(PetgifDiaryJob, str(job_id))
        if (job is None or job.user_id != user['id'] or job.status != 'completed'
                or not job.idempotency_key.startswith('daily:')):
            return {'message': '海报不存在'}, 404
    path = poster_path(job_id)
    if not path.is_file():
        return {'message': '海报不存在'}, 404
    response = send_file(path, mimetype='image/png', download_name='petsense-diary.png', conditional=False)
    response.headers['Cache-Control'] = 'private, no-store'
    return response


def poster_path(job_id):
    return Path(config.RUNTIME_ROOT) / 'diary-posters' / f'{job_id}.png'


def store_poster(job_id, image):
    path = poster_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    try:
        image.save(temporary, format='PNG')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return f'/exercise-diaries/poster/{job_id}'


def render_template(job_id, fields):
    image = Image.new('RGB', (960, 1280), '#FCF6EF')
    draw = ImageDraw.Draw(image)

    def text(x, y, value, size, color='#332821'):
        draw.text((x, y), value, fill=color, font=ImageFont.truetype(str(FONT), size))

    text(68, 65, 'PETSENSE  /  ' + fields['date'], 28, '#8A5442')
    text(67, 128, '运动日记', 76)
    text(70, 234, fields['name'][:20], 39)
    draw.rounded_rectangle((66, 320, 894, 925), radius=36, fill='#F5DFD1')
    has_photo = False
    if fields.get('avatar'):
        try:
            source = Path(config.UPLOADS_DIR) / Path(fields['avatar']).name
            with Image.open(source) as photo:
                fitted = ImageOps.fit(ImageOps.exif_transpose(photo).convert('RGB'), (828, 605))
            mask = Image.new('L', fitted.size)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, 827, 604), radius=36, fill=255)
            image.paste(fitted, (66, 320), mask)
            has_photo = True
        except (OSError, ValueError, Image.DecompressionBombError):
            pass
    if not has_photo:
        for bounds in [(348, 535, 410, 620), (419, 489, 480, 584),
                       (493, 489, 554, 584), (564, 535, 626, 620),
                       (389, 608, 585, 760)]:
            draw.ellipse(bounds, fill='#B5745B')
    if fields['walkingSeconds'] is None:
        text(69, 1000, '今日暂无可用运动观测', 44)
    else:
        minutes, seconds = divmod(fields['walkingSeconds'], 60)
        text(69, 1000, f'观测走动  {minutes} 分 {seconds} 秒', 43)
        text(69, 1090, f'估算步数  约 {fields["steps"]:,} 步', 43)
        text(69, 1180, '仅统计项圈已观测时段', 26, '#8A5442')
    return store_poster(job_id, image)


def store_ai_poster(job_id, content):
    with Image.open(io.BytesIO(content)) as original:
        if original.width * original.height > 16_000_000:
            raise ValueError('海报分辨率超限')
        image = original.convert('RGB')
    ImageDraw.Draw(image).rectangle((0, image.height - 96, image.width, image.height), fill='#FCF6EF')
    ImageDraw.Draw(image).text((24, image.height - 78), '步数为已观测时段的估算值',
                             fill='#332821', font=ImageFont.truetype(str(FONT), 38))
    return store_poster(job_id, image)
