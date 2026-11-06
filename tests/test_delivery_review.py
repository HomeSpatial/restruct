import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('delivery', Path(__file__).resolve().parents[1]/'scripts/check_m3_delivery.py')
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class RenderPairTests(unittest.TestCase):
    def setUp(self):
        self.before = {'camera': {'location_work_m': [1, 2, 3]},
                       'view': {'object_ids': ['floor'], 'hide_ids': [], 'isolate': False,
                                'direction_work': [1, 1, 1], 'bounds_work_m': [[0,0,0],[4,4,3]],
                                'resolution': [640,480], 'samples': 16}}
        self.after = copy.deepcopy(self.before)

    def test_all_scene_accepts_new_objects_with_fixed_framing(self):
        self.after['view']['object_ids'].append('chair')
        delivery.check_render_pair(self.before, self.after)

    def test_actual_camera_or_render_setting_changes_are_rejected(self):
        for field, value in [('hide_ids',['floor']), ('isolate',True), ('resolution',[800,600]),
                             ('samples',32), ('direction_work',[0,1,1]),
                             ('bounds_work_m',[[0,0,0],[8,8,3]])]:
            with self.subTest(field=field):
                after = copy.deepcopy(self.before); after['view'][field] = value
                with self.assertRaises(AssertionError):
                    delivery.check_render_pair(self.before, after)
        self.after['camera']['location_work_m'][0] = 2
        with self.assertRaises(AssertionError):
            delivery.check_render_pair(self.before, self.after)


    def test_isolated_selection_stays_strict_but_order_is_irrelevant(self):
        self.before['view'].update(isolate=True, object_ids=['floor','chair'])
        self.after = copy.deepcopy(self.before)
        self.after['view']['object_ids'].reverse()
        delivery.check_render_pair(self.before, self.after)
        self.after['view']['object_ids'].append('table')
        with self.assertRaises(AssertionError):
            delivery.check_render_pair(self.before, self.after)


class AppearanceDeliveryTests(unittest.TestCase):
    def test_default_delivery_rejects_missing_pbr(self):
        report = {'passed': False, 'primitives': [], 'errors': [{'error': 'No images'}]}
        with patch.object(delivery, 'inspect_materials', return_value=report) as checker:
            with self.assertRaisesRegex(ValueError, 'Material export'):
                delivery.check_appearance_export('model.glb', {}, {})
            checker.assert_called_once()

    def test_geometry_only_requires_a_scope_reason(self):
        with self.assertRaises(ValueError):
            delivery.check_appearance_export('unused.glb', {'appearance_scope': 'geometry_only'}, {})
        report = delivery.check_appearance_export('unused.glb', {
            'appearance_scope': 'geometry_only', 'appearance_scope_reason': 'User requested stair height correction only'}, {})
        self.assertEqual(report['status'], 'not_applicable')
