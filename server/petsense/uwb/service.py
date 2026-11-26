"""Per-owner live positioning; repeated snapshots never refresh measurement age."""
from dataclasses import dataclass, field
from datetime import timedelta
import os
from pathlib import Path
import threading
import time

from petsense import config, db
from petsense.db.models import Device
from petsense.db import utc_now, iso_z
from .engine import PositionEngine
from .maps import Registry
from .measurement import MeasurementFrame
from .regions import RegionStore, RegionTracker, RegionConflict, validate_regions

MASK = (1 << 32) - 1


def unsigned(value, maximum=MASK):
    return type(value) is int and 0 <= value <= maximum


@dataclass
class LiveState:
    engine: PositionEngine
    identity: tuple
    key: tuple | None = None
    sample: int | None = None
    timer: int | None = None
    deadline: float = 0
    expires_at: str | None = None
    region_tracker: RegionTracker = field(default_factory=RegionTracker)


class PositionService:
    def __init__(self, registry):
        self.registry = registry
        self.states = {}
        self.lock = threading.RLock()
        self.regions = RegionStore(config.RUNTIME_ROOT / 'uwb_regions')

    def region_config(self, device_id, user_id=None):
        with self.lock:
            binding = self._binding(device_id)
            if not binding or (user_id is not None and binding[2][0] != user_id):
                raise ValueError('当前设备尚未绑定标定地图')
            bundle, _, identity = binding
            return {**self.regions.load(device_id, identity), 'mapId': bundle.data['mapId'],
                    'mapVersion': bundle.data['version'], 'mapFingerprint': bundle.fingerprint}

    def set_regions(self, device_id, user_id, data):
        with self.lock:
            current = self.region_config(device_id, user_id)
            if any(data.get(k) != current[k] for k in ('mapId', 'mapVersion', 'mapFingerprint')):
                raise RegionConflict('地图已经变化，请在当前地图上重新确认区域')
            if data.get('confirmed') is not True:
                raise ValueError('请先核对区域，再确认保存')
            binding = self._binding(device_id)
            if not binding or binding[2][0] != user_id or binding[0].fingerprint != current['mapFingerprint']:
                raise RegionConflict('设备归属或地图已变化，请重新读取')
            items = validate_regions(data.get('regions'), binding[0].config.map_polygon_m)
            self.regions.save(device_id, binding[2], data.get('baseRevision'), items)
            return self.region_config(device_id, user_id)

    def _binding(self, device_id):
        with db.session_scope() as session:
            device = session.get(Device, device_id)
            if device is None:
                return None
            owner = (device.owner_id, iso_z(device.claimed_at))
        bound = self.registry.for_device(device_id, owner)
        if not bound:
            return None
        bundle, tag = bound
        return bundle, tag, (*owner, bundle.fingerprint, tag)

    def _snapshot(self, device_id, state, bundle, now):
        value = state.engine.latest
        fresh = bool(value.get('fresh')) and now <= state.deadline
        position = value.get('position_map_m')
        quality = value.get('quality') or {}
        region_config = self.regions.load(device_id, state.identity)
        semantic = state.region_tracker.output if state.region_tracker.revision == region_config['revision'] else {'region': None, 'regionStatus': 'waiting'}
        if not fresh:
            semantic = {**semantic, 'regionStatus': 'stale'}
        return {'schemaVersion': 1, 'deviceId': device_id, 'mapId': bundle.data['mapId'],
                'mapVersion': bundle.data['version'], 'tagId': state.engine.tag_id,
                'status': value.get('status', 'waiting') if fresh else ('stale' if position else 'waiting'),
                'fresh': fresh, 'position': {'x': position['x'], 'y': position['y']} if position else None,
                'rawPosition': value.get('raw_position_map_m'),
                'unconstrainedPosition': value.get('unconstrained_position_map_m'),
                'boundary': {'constrained': value.get('boundary_constrained', False),
                             'correctionM': value.get('boundary_correction_m', 0)},
                **semantic, 'regionsRevision': region_config['revision'],
                'quality': {'score': quality.get('score'), 'residualM': quality.get('range_residual_rms_m'),
                            'insideMap': quality.get('inside_map')},
                'remainingMs': max(0, round((state.deadline - now) * 1000)) if fresh else 0,
                'expiresAt': state.expires_at, 'sequence': value.get('sequence')}

    def latest(self, device_id):
        with self.lock:
            binding = self._binding(device_id)
            state = self.states.get(device_id)
            if not binding or not state or state.identity != binding[2]:
                self.states.pop(device_id, None)
                return None
            return self._snapshot(device_id, state, binding[0], time.monotonic())

    def map(self, device_id):
        with self.lock:
            binding = self._binding(device_id)
            if not binding:
                return None
            regions = self.regions.load(device_id, binding[2])
            return {**binding[0].public(), 'regions': regions['regions'],
                    'regionsRevision': regions['revision'], 'revision': self._map_revision(binding, regions['revision'])}

    @staticmethod
    def _map_revision(binding, region_revision='none'):
        # Include the Tag binding so a change clears the previous Tag's trail.
        return f'{binding[0].fingerprint}:{binding[1]}' + (f':regions:{region_revision}' if region_revision != 'none' else '')

    def map_version(self, device_id):
        with self.lock:
            binding = self._binding(device_id)
            if not binding:
                return None
            d = binding[0].data
            regions = self.regions.load(device_id, binding[2])
            return {'mapId': d['mapId'], 'version': d['version'],
                    'revision': self._map_revision(binding, regions['revision'])}

    def enrich(self, frame, device_id, received_at=None, batch_age_ms=0):
        frame.pop('uwbPosition', None)  # Never trust a device-supplied server position.
        if frame.get('deviceId') != device_id:
            return
        with self.lock:
            binding = self._binding(device_id)
            if not binding:
                return
            bundle, tag, identity = binding
            now = time.monotonic()
            state = self.states.get(device_id)
            if state is None or state.identity != identity:
                state = LiveState(PositionEngine(bundle.config, tag), identity)
                self.states[device_id] = state
            uwb = frame.get('uwb')
            if not isinstance(uwb, dict):
                return
            t, received, timer, sequence = (frame.get('ts_ms'), uwb.get('receivedMs'),
                                           uwb.get('timer'), uwb.get('sequence'))
            if not all(unsigned(v) for v in [t, received, timer]) or not unsigned(sequence, 255):
                return
            if not unsigned(uwb.get('tagId'), 65535) or uwb.get('tagId') != tag:
                return
            age = ((t - received) & MASK) + batch_age_ms
            # Account for buffering within each batch. Absolute network delay
            # cannot be recovered from legacy uptime-only firmware timestamps.
            if uwb.get('valid') is not True or age > bundle.config.stale_after_seconds * 1000:
                state.deadline = min(state.deadline, now - .001)
                frame['uwbPosition'] = self._snapshot(device_id, state, bundle, now)
                return
            reboot = (state.sample is not None and t < 60_000 and state.sample > 90_000
                      and ((t - state.sample) & MASK) > (1 << 31))
            if reboot:
                state = LiveState(PositionEngine(bundle.config, tag), identity)
                self.states[device_id] = state
            if state.sample is not None and ((t - state.sample) & MASK) > (1 << 31):
                return  # Late sample; store() already rejects most late deliveries.
            state.sample = t
            key = (timer, sequence)
            if state.key == key:
                frame['uwbPosition'] = self._snapshot(device_id, state, bundle, now)
                return
            if state.timer is not None and ((timer - state.timer) & MASK) > (1 << 31):
                if now <= state.deadline:
                    return
                # BU03 may restart while the ESP32 uptime continues. Only
                # accept its new timer epoch after the previous fix expired.
                state.engine = PositionEngine(bundle.config, tag)
                state.key, state.timer = None, None
            distances = uwb.get('distanceMm')
            mask = uwb.get('anchorMask')
            if not isinstance(distances, list) or not unsigned(mask, 255):
                return
            selected = {}
            for index in bundle.config.anchor_indices:
                if not mask & (1 << index) or index >= len(distances):
                    return
                mm = distances[index]
                if type(mm) not in (int, float) or not .05 <= mm / 1000 <= 30:
                    return
                selected[index] = mm / 1000
            if now > state.deadline:
                state.engine = PositionEngine(bundle.config, tag)  # No smoothing across gaps.
                state.region_tracker = RegionTracker()
            state.engine.update(MeasurementFrame(selected, tag_id=tag, sequence=sequence,
                                                  device_timer=timer), now_monotonic=now)
            state.key, state.timer = key, timer
            remaining = bundle.config.stale_after_seconds - age / 1000
            state.deadline = now + remaining
            state.expires_at = iso_z((received_at or utc_now()) + timedelta(seconds=remaining))
            result = state.engine.latest
            point = result.get('position_map_m')
            state.region_tracker.update(self.regions.load(device_id, identity),
                                        (point['x'], point['y']) if point else None, now,
                                        result.get('status') == 'ok' and result.get('boundary_correction_m', 0) <= .5)
            frame['uwbPosition'] = self._snapshot(device_id, state, bundle, now)


service = PositionService(Registry(Path(os.environ.get('PETSENSE_UWB_BINDINGS_FILE',
                            str(config.RUNTIME_ROOT / 'uwb_maps' / 'bindings.json')))))


def enrich(frame, device_id, received_at, batch_age_ms=0):
    try:
        service.enrich(frame, device_id, received_at, batch_age_ms)
    except (ValueError, KeyError, OSError, TypeError) as error:
        # A bad/unfinished map configuration must not interrupt health telemetry.
        frame.pop('uwbPosition', None)
        print(f'[UWB] configuration rejected: {type(error).__name__}')
