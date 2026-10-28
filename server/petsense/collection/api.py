"""Versioned collection storage. Only acknowledge sensor batches after a durable commit."""
import functools
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time

from flask import Blueprint, request, abort, send_file, g
from sqlalchemy import func, select, update

from petsense import db
from petsense.auth.routes import app_user_from_request
from petsense.db import session_scope
from petsense.db.models import CollectionFrame, CollectionSession, CollectionVideo, JournalCrc, Device

EVENTS = ['01_standing', '02_sitting', '03_resting', '04_sleeping', '05_walking',
          '06_running', '07_eating', '08_rolling', '09_toilet', '10_jumping', 'calibration']


def beijing(ms):
    return datetime.fromtimestamp(ms / 1000, timezone(timedelta(hours=8))).isoformat(timespec='milliseconds')


class CollectionStore:
    def __init__(self, root, token, publish):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.publish = publish
        self.lock = threading.RLock()
        self.latest = {}
        self.media_locks = {}

    @staticmethod
    def ident(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value):
            raise ValueError('invalid identifier')
        return value

    def atomic_json(self, path, value):
        temporary = path.with_suffix('.json.pending')
        with temporary.open('w', encoding='utf8') as file:
            json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def media_lock(self, sid, vid):
        key = (self.ident(sid), self.ident(vid))
        with self.lock:
            return self.media_locks.setdefault(key, threading.RLock())

    def validate_media(self, path):
        try:
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                                    '-of', 'json', str(path)], capture_output=True, timeout=30, check=True)
            info = json.loads(probe.stdout)
            duration = float(info['format']['duration']) * 1000
            if duration <= 0 or not any(s.get('codec_type') == 'video' for s in info['streams']):
                raise ValueError('video stream or duration missing')
            decoded = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path),
                                      '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'],
                                     capture_output=True, timeout=300, check=True)
            if decoded.stderr:
                raise ValueError('media decode reported errors')
            return {'duration_ms': duration, 'streams': info['streams'], 'decode_verified': True}
        except (subprocess.SubprocessError, OSError, KeyError) as error:
            raise ValueError('media validation failed; upload retained for retry') from error

    def ingest(self, topic, body):
        if topic == 'petsense/sensor/collection_status':
            device = self.ident(body['device'])
            with self.lock:
                self.latest[device] = {'received_us': time.time_ns() // 1000,
                                      'frame': body['frame'], 'audit': body.get('audit')}
            return True
        if topic == 'petsense/sensor/clock_v2':
            received = time.time_ns() // 1000
            device = self.ident(body['device'])
            reply = dict(type='clock_reply', boot=body['boot'], nonce=body['nonce'],
                         t0_us=body['t0_us'], t1_us=received, t2_us=time.time_ns() // 1000)
            self.publish(device, reply)
            return True
        if topic != 'petsense/sensor/collection_v2':
            return False
        device = self.ident(body['device'])
        frames = body.get('frames')
        if not isinstance(frames, list) or not 1 <= len(frames) <= 10:
            raise ValueError('batch must contain 1..10 frames')
        if body.get('delta'):
            def merge(base, patch):
                result = copy.deepcopy(base)
                for key, value in patch.items():
                    result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
                return result
            frames = [frames[0]] + [merge(frames[0], patch) for patch in frames[1:]]
        crcs = body.get('crcs')
        if crcs is not None and (len(crcs) != len(frames) or any(type(c) is not int or not 0 <= c <= 0xffffffff for c in crcs)):
            raise ValueError('invalid journal CRC list')
        received = time.time_ns() // 1000
        # 整批一个事务：只有提交落盘后才 publish ACK（持久化契约保持）
        with self.lock, session_scope() as s:
            for index, frame in enumerate(frames):
                boot = self.ident(frame['boot_id'])
                seq = frame['seq']
                if type(seq) is not int or seq < 0 or not isinstance(frame.get('mono_us'), int):
                    raise ValueError('invalid sequence/monotonic timestamp')
                doc = json.dumps(frame, separators=(',', ':'), allow_nan=False)
                existing = s.execute(select(CollectionFrame.document)
                                     .where(CollectionFrame.device == device,
                                            CollectionFrame.boot == boot,
                                            CollectionFrame.seq == seq)).scalar_one_or_none()
                if existing is not None and json.loads(existing) != frame:
                    raise ValueError('sequence reused with different payload')
                stmt = db.dialect_insert(s, CollectionFrame).values(
                    device=device, boot=boot, seq=seq, session=frame.get('session_id', ''),
                    mono_us=frame['mono_us'], utc_us=frame.get('utc_us'),
                    received_us=received, document=doc)
                s.execute(stmt.on_conflict_do_nothing(index_elements=['device', 'boot', 'seq']))
                if crcs is not None:
                    stmt = db.dialect_insert(s, JournalCrc).values(
                        device=device, boot=boot, seq=seq, crc=crcs[index])
                    s.execute(stmt.on_conflict_do_update(
                        index_elements=['device', 'boot', 'seq'], set_={'crc': stmt.excluded.crc}))
            if device not in self.latest or 'audit' not in self.latest[device]:
                self.latest[device] = {'received_us': received, 'frame': frames[-1]}
        self.publish(device, {'type': 'collection_ack', 'batch': body['batch']})
        return True

    def blueprint(self):
        bp = Blueprint('collection_v1', __name__, url_prefix='/api/collection/v1')

        @bp.after_request
        def collection_headers(response):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type, X-Chunk-SHA256, X-Petsense-Session'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, OPTIONS'
            return response

        def protected(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                credential = request.headers.get('Authorization', '').removeprefix('Bearer ')
                try:
                    g.collection_user = None
                    is_admin = bool(self.token and hmac.compare_digest(credential, self.token))
                    if not is_admin:
                        user = app_user_from_request()
                        if not user:
                            abort(401)
                        g.collection_user = user['id']
                        device = kwargs.get('device')
                        sid = kwargs.get('sid')
                        if fn.__name__ == 'create_session':
                            body = request.get_json() or {}
                            device, sid = body.get('device'), body.get('id')
                        with session_scope() as s:
                            if sid:
                                row = s.get(CollectionSession, self.ident(sid))
                                if row:
                                    doc = json.loads(row.document)
                                    if doc.get('owner_id') != user['id']:
                                        abort(404)
                                    # Existing recordings stay private to their creator after transfer.
                                    if fn.__name__ == 'control':
                                        device = doc['device']
                                elif fn.__name__ != 'create_session':
                                    abort(404)
                            if device:
                                owned = s.get(Device, self.ident(device))
                                if not owned or owned.owner_id != user['id']:
                                    abort(404)
                    return fn(*args, **kwargs)
                except (ValueError, KeyError, TypeError) as error:
                    return {'error': str(error)}, 400
            return wrapper

        @bp.get('/time')
        @protected
        def clock():
            return {'server_us': time.time_ns() // 1000, 'timezone': 'Asia/Shanghai'}

        @bp.get('/device/<device>')
        @protected
        def device_status(device):
            return self.latest.get(device, {'received_us': None, 'frame': None})

        @bp.post('/sessions')
        @protected
        def create_session():
            body = request.get_json()
            sid = self.ident(body['id'])
            device = self.ident(body['device'])
            doc = dict(schema_version=2, id=sid, device=device, state='created',
                       created_utc_ms=time.time_ns() // 1000000, clock=body.get('clock'),
                       owner_id=g.collection_user)
            with self.lock, session_scope() as s:
                previous = s.execute(select(CollectionSession.document)
                                     .where(CollectionSession.id == sid)).scalar_one_or_none()
                if previous:
                    existing = json.loads(previous)
                    if existing['device'] != device:
                        return {'error': 'session device mismatch'}, 409
                    return existing
                s.add(CollectionSession(id=sid, document=json.dumps(doc)))
                (self.root / sid).mkdir(exist_ok=True)
                self.atomic_json(self.root / sid / 'session.json', doc)
            return doc, 201

        @bp.post('/sessions/<sid>/control')
        @protected
        def control(sid):
            self.ident(sid)
            body = request.get_json()
            action = body['action']
            if action not in ('start', 'stop', 'arm', 'audit', 'pause_transport'):
                raise ValueError('invalid action')
            with session_scope() as s:
                row = s.execute(select(CollectionSession.document)
                                .where(CollectionSession.id == sid)).scalar_one_or_none()
            if not row:
                abort(404)
            session = json.loads(row)
            if action == 'start':
                state = self.latest.get(session['device'], {})
                frame = state.get('frame') or {}
                if (not frame.get('utc_us') or frame.get('clock_age_us', 0) > 300000000
                        or time.time_ns()//1000 - state.get('received_us', 0) > 15000000):
                    return {'error': 'device clock not ready; wait for synchronized live status'}, 409
                if frame.get('capture_active') and frame.get('session_id') != sid:
                    return {'error': 'another session is already recording'}, 409
                if frame.get('sd_ready') is False or frame.get('disk_errors') or frame.get('queue_drops') or frame.get('storage_draining') and not frame.get('capture_active'):
                    return {'error': 'SD not ready, draining, or reporting errors'}, 409
                free = frame.get('sd_free_bytes')
                if isinstance(free, (int, float)) and free <= 1048576:
                    return {'error': 'SD free space is below the 1 MiB reserve'}, 409
            # A queued MQTT command is not a device acknowledgement.
            command = dict(type='collection_' + action, session=sid)
            if action == 'arm':
                command['marker'] = self.ident(body['marker'])
            if action == 'pause_transport':
                seconds = int(body.get('seconds', 15))
                if not 1 <= seconds <= 60:
                    raise ValueError('pause must be 1..60 seconds')
                command['seconds'] = seconds
            published = self.publish(session['device'], command)
            return {'published': published, 'device_acknowledged': False}, 202 if published else 503

        @bp.get('/sessions/<sid>/quality')
        @protected
        def quality(sid):
            self.ident(sid)
            with session_scope() as s:
                session = s.get(CollectionSession, sid)
                if not session:
                    abort(404)
                session_device = json.loads(session.document)['device']
                rows = s.execute(select(CollectionFrame.boot, CollectionFrame.seq,
                                        CollectionFrame.mono_us, CollectionFrame.document)
                                 .where(CollectionFrame.session == sid, CollectionFrame.device == session_device)
                                 .order_by(CollectionFrame.boot, CollectionFrame.seq)).fetchall()
            gaps = []
            previous = None
            markers = []
            max_jitter = 0
            missing_utc = 0
            for boot, seq, mono, doc in rows:
                frame = json.loads(doc)
                missing_utc += frame.get('utc_us') is None
                if previous and previous[0] == boot:
                    if seq != previous[1] + 1:
                        gaps.append({'boot': boot, 'after': previous[1], 'before': seq})
                    max_jitter = max(max_jitter, abs(mono - previous[2] - (seq - previous[1]) * 100000))
                previous = (boot, seq, mono)
                if frame.get('marker_id'):
                    markers.append({'boot': boot, 'seq': seq, 'mono_us': mono,
                                    'utc_us': frame.get('utc_us'), 'id': frame['marker_id']})
            reconciled = []
            with self.lock:
                statuses = copy.deepcopy(list(self.latest.items()))
            with session_scope() as s:
                for device, status in statuses:
                    if device != session_device:
                        continue
                    for file in (status.get('audit') or {}).get('files', []):
                        name = Path(file['path']).name
                        if not name.startswith(sid + '_'):
                            continue
                        boot = name.removesuffix('.psj').rsplit('_', 1)[-1]
                        count, checksum = s.execute(
                            select(func.count(), func.sum(JournalCrc.crc))
                            .where(JournalCrc.device == device, JournalCrc.boot == boot,
                                   JournalCrc.seq.between(file['first'], file['last']))).fetchone()
                        matched = (count == file['rows'] and ((checksum or 0) & 0xffffffff) == file['crc_sum']
                                   and file['errors'] == 0 and file['gaps'] == 0 and file['valid_bytes'] == file['bytes'])
                        reconciled.append({**file, 'cloud_crc_rows': count, 'matched': matched})
            storage_complete = bool(reconciled) and all(f['matched'] for f in reconciled) and not gaps and sum(f['rows'] for f in reconciled) == len(rows)
            complete = storage_complete and missing_utc == 0
            return {'count': len(rows), 'gaps': gaps, 'max_interval_error_us': max_jitter,
                    'markers': markers, 'complete': complete, 'sd_reconciliation': reconciled,
                    'storage_complete': storage_complete, 'missing_utc': missing_utc,
                    'note': 'Storage CRC/count reconciliation and non-null UTC are checked separately. Visual alignment and sensor validity still require review.'}

        @bp.post('/sessions/<sid>/videos')
        @protected
        def create_video(sid):
            self.ident(sid)
            body = request.get_json()
            vid = self.ident(body['id'])
            if body.get('event_id') not in EVENTS:
                raise ValueError('invalid event')
            if not isinstance(body.get('start_utc_ms'), (int, float)):
                raise ValueError('missing video timestamp')
            with self.lock, session_scope() as s:
                if not s.get(CollectionSession, sid):
                    abort(404)
                row = s.execute(select(CollectionVideo.session, CollectionVideo.document)
                                .where(CollectionVideo.id == vid)).fetchone()
                if row:
                    if row.session != sid:
                        return {'error': 'video belongs to another session'}, 409
                    return json.loads(row.document)
                doc = {**body, 'schema_version': 2, 'session_id': sid,
                       'start_beijing': beijing(body['start_utc_ms']), 'complete': False,
                       'time_alignment': 'estimated_until_visual_marker_confirmed'}
                folder = self.root / sid / vid
                folder.mkdir(exist_ok=True)
                self.atomic_json(folder / 'annotation.json', doc)
                s.add(CollectionVideo(id=vid, session=sid, document=json.dumps(doc), complete=0))
            return doc, 201

        def video_folder(sid, vid):
            self.ident(sid)
            self.ident(vid)
            with session_scope() as s:
                row = s.execute(select(CollectionVideo.document, CollectionVideo.complete)
                                .where(CollectionVideo.id == vid,
                                       CollectionVideo.session == sid)).fetchone()
            if not row:
                abort(404)
            return self.root / sid / vid, json.loads(row.document), row.complete

        @bp.route('/sessions/<sid>/videos/<vid>/upload', methods=['GET', 'PUT'])
        @protected
        def upload(sid, vid):
            with self.media_lock(sid, vid):
                folder, doc, complete = video_folder(sid, vid)
                path = folder / ('video.mov' if complete else 'video.partial')
                size = path.stat().st_size if path.exists() else 0
                if request.method == 'GET':
                    return {'offset': size, 'complete': bool(complete)}
                if complete:
                    return {'error': 'already complete'}, 409
                offset = int(request.args['offset'])
                if offset != size:
                    return {'offset': size, 'error': 'offset mismatch'}, 409
                data = request.get_data(cache=False)
                if not 0 < len(data) <= 4 * 1024 * 1024 or size + len(data) > 8 * 1024**3:
                    abort(413)
                digest = hashlib.sha256(data).hexdigest()
                if not hmac.compare_digest(digest, request.headers.get('X-Chunk-SHA256', '')):
                    return {'error': 'chunk checksum mismatch'}, 422
                with path.open('ab') as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                return {'offset': size + len(data), 'sha256': digest}

        @bp.post('/sessions/<sid>/videos/<vid>/finish')
        @protected
        def finish(sid, vid):
            with self.media_lock(sid, vid):
                folder, doc, complete = video_folder(sid, vid)
                if complete:
                    return doc
                body = request.get_json()
                partial = folder / 'video.partial'
                final = folder / 'video.mov'
                path = partial if partial.exists() else final
                if not path.exists() or path.stat().st_size != body['size'] or body['size'] <= 0:
                    return {'error': 'video size mismatch'}, 409
                # MP4/MOV ISO BMFF header. Keep malformed uploads unfinished.
                with path.open('rb') as file:
                    header = file.read(64)
                if b'ftyp' not in header:
                    return {'error': 'not an MP4/MOV container'}, 422
                media = self.validate_media(path)
                digest = hashlib.sha256()
                with path.open('rb') as file:
                    for block in iter(lambda: file.read(1024 * 1024), b''):
                        digest.update(block)
                if path == partial:
                    os.replace(partial, final)
                doc.update(complete=True, size=body['size'], sha256=digest.hexdigest(),
                           end_utc_ms=body.get('end_utc_ms'), duration_ms=media['duration_ms'],
                           capture_elapsed_ms=body.get('duration_ms'), media=media,
                           filename='video.mov', timestamp_method=body.get('timestamp_method', 'recordAsync_call'),
                           interrupted=bool(body.get('interrupted')), labels=body.get('labels', []),
                           clock=body.get('clock'), start_call_bounds=body.get('start_call_bounds'))
                self.atomic_json(folder / 'annotation.json', doc)
                with session_scope() as s:
                    s.execute(update(CollectionVideo).where(CollectionVideo.id == vid)
                              .values(complete=1, document=json.dumps(doc)))
                return doc

        @bp.post('/sessions/<sid>/videos/<vid>/alignment')
        @protected
        def align(sid, vid):
            with self.media_lock(sid, vid):
                folder, doc, complete = video_folder(sid, vid)
                body = request.get_json()
                position = float(body['video_ms'])
                if not complete or not math.isfinite(position) or position < 0 or position > (doc.get('duration_ms') or 0):
                    raise ValueError('invalid video position')
                with session_scope() as s:
                    session_device = json.loads(s.get(CollectionSession, sid).document)['device']
                    row = s.execute(select(CollectionFrame.document)
                                    .where(CollectionFrame.session == sid,
                                           CollectionFrame.device == session_device,
                                           CollectionFrame.boot == body['boot'],
                                           CollectionFrame.seq == int(body['seq']))).scalar_one_or_none()
                    if not row:
                        raise ValueError('sensor marker missing')
                    frame = json.loads(row)
                    if not frame.get('marker_id') or not frame.get('utc_us'):
                        raise ValueError('marker has no synchronized UTC')
                    if doc.get('calibration_marker') and doc['calibration_marker'] != frame['marker_id']:
                        raise ValueError('marker belongs to another calibration clip')
                    doc['alignment'] = dict(method='manual_visual_imu_marker', sensor_boot=body['boot'],
                        sensor_seq=body['seq'], marker_id=frame['marker_id'], video_ms=position,
                        aligned_start_utc_ms=frame['utc_us'] / 1000 - position,
                        sensor_clock_uncertainty_us=frame.get('clock_uncertainty_us'),
                        sample_quantization_ms=100, visual_error_ms='not_measured')
                    doc['time_alignment'] = 'manual_marker_confirmed'
                    anchor = doc['alignment']['aligned_start_utc_ms']
                    doc['aligned_labels'] = [
                        {**label, 'start_utc_ms': anchor + label['start_ms'],
                         'end_utc_ms': anchor + label['end_ms']}
                        for label in doc.get('labels', [])
                    ]
                    s.execute(update(CollectionVideo).where(CollectionVideo.id == vid)
                              .values(document=json.dumps(doc)))
                self.atomic_json(folder / 'annotation.json', doc)
            return doc

        @bp.get('/sessions/<sid>/videos/<vid>/annotation')
        @protected
        def annotation(sid, vid):
            folder, doc, _ = video_folder(sid, vid)
            return doc

        return bp
