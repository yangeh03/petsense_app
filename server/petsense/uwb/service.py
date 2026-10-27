"""Per-owner live positioning; repeated snapshots never refresh measurement age."""
from dataclasses import dataclass
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


class PositionService:
    def __init__(self, registry):
        self.registry = registry
        self.states = {}
        self.lock = threading.RLock()

    def _binding(self, device_id):
        bound = self.registry.for_device(device_id)
        if not bound:
            return None
        with db.session_scope() as session:
            device = session.get(Device, device_id)
            if device is None:
                return None
            owner = (device.owner_id, iso_z(device.claimed_at))
        bundle, tag = bound
        return bundle, tag, (*owner, bundle.fingerprint, tag)

    def _snapshot(self, device_id, state, bundle, now):
        value = state.engine.latest
        fresh = bool(value.get('fresh')) and now <= state.deadline
        position = value.get('position_map_m')
        quality = value.get('quality') or {}
        return {'schemaVersion': 1, 'deviceId': device_id, 'mapId': bundle.data['mapId'],
                'mapVersion': bundle.data['version'], 'tagId': state.engine.tag_id,
                'status': value.get('status', 'waiting') if fresh else ('stale' if position else 'waiting'),
                'fresh': fresh, 'position': {'x': position['x'], 'y': position['y']} if position else None,
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
            return binding[0].public() if binding else None

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
            state.engine.update(MeasurementFrame(selected, tag_id=tag, sequence=sequence,
                                                  device_timer=timer), now_monotonic=now)
            state.key, state.timer = key, timer
            remaining = bundle.config.stale_after_seconds - age / 1000
            state.deadline = now + remaining
            state.expires_at = iso_z((received_at or utc_now()) + timedelta(seconds=remaining))
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
