import math
import unittest
from petsense.uwb.boundary import contains, constrain
from petsense.uwb.engine import PositionEngine
from petsense.uwb.maps import EngineConfig
from petsense.uwb.measurement import MeasurementFrame

SQUARE = ((0, 0), (4, 0), (4, 4), (0, 4))
NOTCH = ((0, 0), (4, 0), (4, 4), (3, 4), (3, 1), (1, 1), (1, 4), (0, 4))


class BoundaryTests(unittest.TestCase):
    def test_initial_outside_projects_and_inside_stays_exact(self):
        self.assertEqual(constrain((5, 2), SQUARE), (4, 2))
        self.assertEqual(constrain((2, 2), SQUARE), (2, 2))
        self.assertEqual(constrain((4, 2), SQUARE), (4, 2))

    def test_concave_motion_stops_at_first_exit_even_if_target_is_inside(self):
        self.assertEqual(constrain((3.5, 3), NOTCH, (.5, 3)), (1, 3))
        self.assertEqual(constrain((3.5, 3), NOTCH, (1, 3)), (1, 3))
        self.assertEqual(constrain((.5, 2), NOTCH, (1, 3)), (.5, 2))
        self.assertEqual(constrain((3.5, .5), NOTCH, (.5, .5)), (3.5, .5))

    def test_repeated_vertices_and_boundary_sliding(self):
        polygon = SQUARE + (SQUARE[0],)
        self.assertTrue(contains(constrain((-20, 10), polygon), polygon))
        self.assertEqual(constrain((4, 3), polygon, (4, 2)), (4, 3))

    def test_engine_preserves_unconstrained_quality_and_does_not_modify_filter(self):
        anchors = ((0, 0), (4, 0), (0, 4))
        config = EngineConfig((0, 1, 2), anchors, SQUARE, 1, ((1, 0), (0, 1)), (0, 0), 2)
        engine = PositionEngine(config, 0)
        target = (5, 2)
        result = engine.update(MeasurementFrame({i: math.dist(target, a) for i, a in enumerate(anchors)}, tag_id=0), 10)
        self.assertAlmostEqual(result['position_map_m']['x'], 4)
        self.assertAlmostEqual(result['raw_position_map_m']['x'], 5)
        self.assertAlmostEqual(result['unconstrained_position_map_m']['x'], 5)
        self.assertAlmostEqual(engine.position[0], 5)
        self.assertTrue(result['boundary_constrained'])
        self.assertFalse(result['quality']['inside_map'])
        self.assertAlmostEqual(result['boundary_correction_m'], 1)
        stale = engine.refresh_status(20)
        self.assertFalse(stale['fresh'])
        self.assertEqual(stale['position_map_m'], result['position_map_m'])
