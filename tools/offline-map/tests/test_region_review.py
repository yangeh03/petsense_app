import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('region_review', HERE / 'region_review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
ASSET = HERE.parents[1] / 'server/petsense/uwb/assets/reconstruction-room-v1'


class RegionReviewTests(unittest.TestCase):
    def test_page_embeds_local_image_and_escapes_labels_and_rejects_wrong_map(self):
        manifest = json.loads((ASSET / 'map.json').read_text())
        fingerprint = hashlib.sha256((ASSET / 'map.json').read_bytes() + (ASSET / 'floorplan.png').read_bytes()).hexdigest()
        data = {'mapId': manifest['mapId'], 'mapVersion': manifest['version'], 'mapFingerprint': fingerprint,
                'baseRevision': 'none', 'regions': [{'label': '</script><script>bad</script>'}]}
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            draft, output = p / 'draft.json', p / 'review.html'
            draft.write_text(json.dumps(data), encoding='utf-8')
            review.build(ASSET, draft, output)
            page = output.read_text(encoding='utf-8')
            self.assertIn('data:image/png;base64,', page)
            self.assertNotIn('</script><script>bad', page)
            data['mapFingerprint'] = 'wrong'
            draft.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                review.build(ASSET, draft, output)
