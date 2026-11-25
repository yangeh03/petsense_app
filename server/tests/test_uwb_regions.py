import tempfile
import unittest
from pathlib import Path

from petsense.uwb.regions import RegionStore, RegionTracker, RegionConflict, validate_regions

OUTLINE = ((0, 0), (6, 0), (6, 4), (0, 4))


def area(ident, x1, x2, priority=0, confirmed=True):
    return {'id': ident, 'label': ident, 'polygon': [[x1, 0], [x2, 0], [x2, 4], [x1, 4]],
            'priority': priority, 'confirmed': confirmed}


class RegionTests(unittest.TestCase):
    def test_config_scope_revision_conflict_and_clear(self):
        with tempfile.TemporaryDirectory() as root:
            store = RegionStore(Path(root))
            ident = (1, 'claim', 'map-hash', 0)
            data = store.save('tag', ident, 'none', [area('sofa', 0, 2)])
            self.assertNotEqual(data['revision'], 'none')
            for other in [(2, 'claim', 'map-hash', 0), (1, 'new-claim', 'map-hash', 0),
                          (1, 'claim', 'other-map', 0), (1, 'claim', 'map-hash', 1)]:
                self.assertEqual(store.load('tag', other)['regions'], [])
            with self.assertRaises(RegionConflict):
                store.save('tag', ident, 'none', [])
            self.assertEqual(store.save('tag', ident, data['revision'], [])['regions'], [])

    def test_invalid_and_self_crossing_and_outside_shapes(self):
        for bad in [area('../x', 0, 2), area('outside', 0, 7),
                    {**area('x', 0, 2), 'polygon': [[0, 0], [3, 3], [0, 3], [3, 0]]}]:
            with self.assertRaises(ValueError):
                validate_regions([bad], OUTLINE)
        with self.assertRaises(ValueError):
            validate_regions([area('same', 0, 2), area('same', 2, 3)], OUTLINE)
        self.assertEqual(len(validate_regions([area('sofa', 0, 2)], OUTLINE)), 1)

    def test_confirmation_overlap_priority_and_unmapped(self):
        tracker = RegionTracker()
        config = {'revision': 'a', 'regions': [area('draft', 0, 6, 90, False), area('room', 0, 6), area('table', 2, 4, 10)]}
        self.assertEqual(tracker.update(config, (3, 2), 1)['region']['id'], 'table')
        self.assertEqual(tracker.update(config, (5, 2), 2)['regionStatus'], 'transition')
        self.assertEqual(tracker.update(config, (5, 2), 3)['region']['id'], 'room')
        empty = {'revision': 'b', 'regions': [area('draft', 0, 6, confirmed=False)]}
        self.assertEqual(tracker.update(empty, (2, 2), 4)['regionStatus'], 'unconfigured')
        only = {'revision': 'c', 'regions': [area('small', 0, 1)]}
        self.assertEqual(tracker.update(only, (4, 2), 5)['regionStatus'], 'unmapped')

    def test_boundary_jitter_requires_two_distinct_updates_and_inward_margin(self):
        t = RegionTracker()
        cfg = {'revision': 'a', 'regions': [area('left', 0, 3), area('right', 3, 6)]}
        t.update(cfg, (2, 2), 1)
        for now, x in [(2, 3.01), (3, 2.99), (4, 3.02), (5, 3.02)]:
            self.assertEqual(t.update(cfg, (x, 2), now)['region']['id'], 'left')
        self.assertEqual(t.update(cfg, (4, 2), 6)['region']['id'], 'right')
        self.assertEqual(t.update(cfg, (2, 2), 7, reliable=False)['regionStatus'], 'uncertain')
        self.assertIsNone(t.output['region'])

    def test_revision_change_reclassifies_instead_of_reusing_previous_name(self):
        t = RegionTracker()
        t.update({'revision': 'a', 'regions': [area('old', 0, 6)]}, (1, 1), 1)
        self.assertEqual(t.update({'revision': 'b', 'regions': [area('new', 0, 6)]}, (1, 1), 2)['region']['id'], 'new')
