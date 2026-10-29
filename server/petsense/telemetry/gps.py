"""Normalize optional WGS84 GNSS snapshots without dropping other telemetry."""
import math
from datetime import datetime

STALE_MS = 15000
MASK = 0xffffffff


def finite(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def normalize(frame, batch_age_ms=0):
    if not isinstance(frame, dict) or 'gps' not in frame:
        return
    value = frame['gps']
    result = dict(source='A7670E_GNSS', crs='WGS84', enabled=False,
                  valid=False, status='error', latitude=None, longitude=None,
                  utc=None, altitudeM=None, speedMps=None, courseDeg=None,
                  ageMs=None, remainingMs=0)
    frame['gps'] = result
    if not isinstance(value, dict):
        return
    result['enabled'] = value.get('enabled') is True
    if value.get('source') != result['source'] or value.get('crs') != 'WGS84':
        return
    status = value.get('status')
    if status in ('disabled', 'starting', 'searching', 'fixed', 'stale', 'error'):
        result['status'] = status
    stamp, received = frame.get('ts_ms'), value.get('receivedMs')
    if type(stamp) is not int or type(received) is not int or not 0 <= stamp <= MASK or not 0 <= received <= MASK:
        result['status'] = 'error'
        return
    age = ((stamp - received) & MASK) + max(0, batch_age_ms)
    result.update(receivedMs=received, ageMs=age)
    if type(value.get('sequence')) is int and 0 <= value['sequence'] <= MASK:
        result['sequence'] = value['sequence']
    if not result['enabled'] or value.get('valid') is not True:
        return
    if age > STALE_MS:
        result['status'] = 'stale'
        return
    lat, lon, utc = value.get('latitude'), value.get('longitude'), value.get('utc')
    try:
        if not isinstance(utc, str) or not utc.endswith('Z'):
            raise ValueError('Missing UTC')
        datetime.fromisoformat(utc[:-1] + '+00:00')
    except ValueError:
        result['status'] = 'error'
        return
    if not finite(lat, -90, 90) or not finite(lon, -180, 180):
        result['status'] = 'error'
        return
    result.update(valid=True, status='fixed', latitude=lat, longitude=lon,
                  utc=utc, remainingMs=STALE_MS-age)
    for key, low, high in (('altitudeM', -20000, 100000), ('speedMps', 0, 10000), ('courseDeg', 0, 360)):
        if finite(value.get(key), low, high):
            result[key] = value[key]
