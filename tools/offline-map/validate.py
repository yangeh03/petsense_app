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
    json.loads((directory / 'map.json').read_text(encoding='utf-8'), parse_constant=reject_constant)
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
    orientation = bundle.data.get('provenance', {}).get('orientation')
    if orientation:
        matrix = np.asarray(orientation['matrix'], dtype=float)
        translation = np.asarray(orientation['translationM'], dtype=float)
        if (matrix.shape != (2, 2) or translation.shape != (2,) or
                not np.isfinite(matrix).all() or not np.isfinite(translation).all() or
                not np.allclose(matrix @ matrix.T, np.eye(2), rtol=0, atol=1e-8) or
                not np.isclose(np.linalg.det(matrix), 1, rtol=0, atol=1e-8)):
            raise ValueError('Orientation must be a finite rigid rotation')
        source = orientation['sourceUwbTransform']
        actual = bundle.data['uwbTransform']
        if (actual['scale'] != source['scale'] or
                not np.allclose(actual['matrix'], matrix @ np.asarray(source['matrix']), rtol=0, atol=1e-8) or
                not np.allclose(actual['translation'], matrix @ np.asarray(source['translation']) + translation, rtol=0, atol=1e-8)):
            raise ValueError('UWB transform is inconsistent with map orientation')
        source_bg = orientation['sourceBackground']
        origin = np.asarray(source_bg['origin'], dtype=float)
        extent = np.asarray(source_bg['extent'], dtype=float)
        if (origin.shape != (2,) or extent.shape != (2,) or not np.isfinite(origin).all() or
                not np.isfinite(extent).all() or np.any(extent <= 0)):
            raise ValueError('Invalid source background bounds')
        corners = np.array([origin, origin + [extent[0], 0], origin + extent, origin + [0, extent[1]]]) @ matrix.T + translation
        if np.any(corners < low - tolerance) or np.any(corners > high + tolerance):
            raise ValueError('Oriented image canvas clips the source background')
        sidecar = directory / 'orientation.json'
        if sidecar.exists() and json.loads(sidecar.read_text(encoding='utf-8')) != orientation:
            raise ValueError('Orientation sidecar differs from manifest')
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
