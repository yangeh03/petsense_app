#!/usr/bin/env python3
"""Check an offline map using the same pure contract as the online server."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

# This module imports only the server's stdlib-only map contract, never Flask,
# database configuration, credentials or live services. Run from the repository.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server'))
from petsense.uwb.maps import MapBundle, validate_config


def validate_bundle(directory):
    directory = Path(directory)
    def reject_constant(value):
        raise ValueError(f'Non-finite JSON value: {value}')
    json.loads((directory / 'map.json').read_text(), parse_constant=reject_constant)
    bundle = MapBundle(directory)
    try:
        with Image.open(bundle.image) as image:
            if image.format != 'PNG':
                raise ValueError('Background must be PNG')
            image.verify()  # Chunk checksums, including IDAT.
        with Image.open(bundle.image) as image:
            image.load()  # A correct header/CRC alone does not prove the stream decodes.
    except (SyntaxError, OSError) as error:
        raise ValueError(f'PNG verification/decoding failed: {error}') from error
    bg = bundle.data['background']
    low = np.asarray(bg['origin'])
    high = low + np.array([bg['width'], bg['height']]) * bg['metersPerPixel']
    polygon = np.asarray(bundle.config.map_polygon_m)
    tolerance = bg['metersPerPixel'] * 1e-6
    if np.any(polygon < low - tolerance) or np.any(polygon > high + tolerance):
        raise ValueError('Polygon extends beyond the background; export both in the same map frame')
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        bundle = validate_bundle(args.directory)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f'Map validation failed: {error}\n')
    bg = bundle.public()['background']
    print(f"Validated {bundle.data['mapId']}/{bundle.data['version']}: "
          f"{bg['width']} x {bg['height']}, {bg['byteLength']} bytes, CRC32 {bg['crc32']}")


if __name__ == '__main__':
    main()
