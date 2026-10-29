"""Owner-bound display plans, separate from calibrated metric positioning maps."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path

from PIL import Image

from petsense import config, db
from petsense.db import iso_z
from petsense.db.models import Device


def read_plan(device_id):
    root = Path(os.environ.get('PETSENSE_UWB_PLAN_ROOT', config.RUNTIME_ROOT / 'uwb_plans')).resolve()
    bindings = root / 'bindings.json'
    if not bindings.exists():
        return None
    if bindings.stat().st_size > 100000:
        raise ValueError('Plan bindings too large')
    entry = json.loads(bindings.read_text(encoding='utf-8')).get('devices', {}).get(device_id)
    if not entry:
        return None
    with db.session_scope() as session:
        device = session.get(Device, device_id)
        if not device or (entry.get('ownerId'), entry.get('claimedAt')) != (device.owner_id, iso_z(device.claimed_at)):
            return None
    directory = (root / entry['directory']).resolve()
    if not directory.is_relative_to(root):
        raise ValueError('Plan escapes root')
    source = directory / 'plan.json'
    if source.stat().st_size > 64000:
        raise ValueError('Plan metadata too large')
    raw = source.read_bytes()
    data = json.loads(raw)
    if data.get('schemaVersion') != 1 or data.get('units') != 'relative' or data.get('calibrationStatus') != 'required':
        raise ValueError('Unsupported display plan')
    bg = data['image']
    image_path = (directory / bg['file']).resolve()
    if not image_path.is_relative_to(directory) or image_path.suffix.lower() != '.png' or image_path.stat().st_size > 4000000:
        raise ValueError('Invalid plan image')
    content = image_path.read_bytes()
    width, height = bg['width'], bg['height']
    if type(width) is not int or type(height) is not int or min(width, height) < 1 or max(width, height) > 10000 or width * height > 20000000:
        raise ValueError('Invalid plan dimensions')
    with Image.open(io.BytesIO(content)) as image:
        if image.format != 'PNG' or image.size != (width, height):
            raise ValueError('Plan dimensions mismatch')
        image.verify()
    return dict(schemaVersion=1, deviceId=device_id, planId=data['planId'],
                name=data.get('name', '场地平面图'), units='relative', calibrationStatus='required',
                revision=hashlib.sha256(raw + content).hexdigest(),
                image=dict(width=width, height=height, byteLength=len(content),
                           imageDataUri='data:image/png;base64,' + base64.b64encode(content).decode()))
