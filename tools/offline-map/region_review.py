"""Embed a reviewed-map region draft in a fully offline editing page."""
import argparse
import base64
import json
import hashlib
from pathlib import Path


def build(bundle, draft, output):
    manifest = json.loads((bundle / 'map.json').read_text(encoding='utf-8'))
    config = json.loads(draft.read_text(encoding='utf-8'))
    if (config['mapId'], config['mapVersion']) != (manifest['mapId'], manifest['version']):
        raise ValueError('Region draft and map bundle differ')
    image = (bundle / manifest['background']['file']).resolve()
    if not image.is_relative_to(bundle.resolve()):
        raise ValueError('Image escapes map bundle')
    if hashlib.sha256((bundle / 'map.json').read_bytes() + image.read_bytes()).hexdigest() != config['mapFingerprint']:
        raise ValueError('Region draft fingerprint does not match this map')
    data = {**config, 'background': manifest['background'], 'outline': manifest['polygon'],
            'image': 'data:image/png;base64,' + base64.b64encode(image.read_bytes()).decode()}
    template = Path(__file__).with_name('region-review-template.html').read_text(encoding='utf-8')
    output.write_text(template.replace('__REGION_DATA__', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--draft', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.bundle, args.draft, args.output)
