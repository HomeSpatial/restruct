import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import trimesh

from indoor_recon.contracts import ContractError
from indoor_recon.evidence import run

spec = importlib.util.spec_from_file_location('compare_rays', Path(__file__).resolve().parents[1]/'scripts/compare_ray_evidence.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def plane(z, xmin=-1, xmax=1):
    return trimesh.Trimesh([[xmin,-1,z],[xmax,-1,z],[xmax,1,z],[xmin,1,z]], [[0,1,2],[0,2,3]], process=False)


class RayComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root/'source'; source.mkdir()
        plane(0).export(source/'scan.ply')
        (source/'prepared.json').write_text(json.dumps({'scene_id':'fixture','frames':[]}))
        config = {'schema_version':1,'kind':'evidence','scene_id':'fixture',
                  'prepared':{'root':'source','path':'prepared.json'},
                  'scan':{'asset':{'root':'source','path':'scan.ply'},'metres_per_unit':1,'T_work_from_source_m':np.eye(4).tolist()}}
        self.config = self.root/'config.json'; self.config.write_text(json.dumps(config))
        self.startup = self.root/'startup.json'
        self.startup.write_text(json.dumps({'scene_id':'fixture','evidence_config':str(self.config),'evidence_roots':{'source':str(source)}}))
        self.query = self.root/'query.json'
        self.query.write_text(json.dumps({'op':'raycast','origins_work_m':[[-.5,0,2],[.5,0,2],[3,0,2]],'directions_work':[[0,0,-7]]*3}))
        run(self.config,self.query,self.root/'evidence',{'source':source})
        self.evidence = self.root/'evidence/evidence.json'
        self.before = self.export('before',{'target/old':plane(.2,-1,0),'occluder':plane(1)})
        self.after = self.export('after',{'target/old':plane(0,-1,0),'target/new':plane(0,0,1),'occluder':plane(1)})

    def export(self, name, meshes, scene_id='fixture', transform=None):
        scene = trimesh.Scene()
        for identity, mesh in meshes.items():
            gltf = mesh.copy(); gltf.apply_transform(np.linalg.inv(audit.WORK_FROM_GLTF))
            scene.add_geometry(gltf,node_name=identity,geom_name=identity)
        scene.metadata = {'coordinate_frame':'gltf_y_up_metres','T_work_from_gltf':(audit.WORK_FROM_GLTF if transform is None else transform).tolist(),'parameter_model':{'scene_id':scene_id}}
        path = self.root/(name+'.glb')
        path.write_bytes(trimesh.exchange.gltf.export_glb(scene, tree_postprocessor=lambda tree: tree.update(extras=scene.metadata)))
        return path

    def compare(self, **kw):
        args = dict(startup_path=self.startup,evidence_path=self.evidence,before_path=self.before,
                    after_path=self.after,object_ids=[],component='z',output=self.root/'report.json',object_prefixes=['target/'])
        args.update(kw); return audit.compare(**args)

    def test_added_part_coverage_and_full_scene_occlusion_have_ray_provenance(self):
        result = self.compare()
        target, full = result['domains']['target'],result['domains']['full_scene']
        self.assertEqual(target['common_valid'],1)
        self.assertEqual(target['coverage_gained'],1)
        self.assertEqual(target['coverage_lost'],0)
        self.assertAlmostEqual(target['before']['mean_abs_m'],.2,places=6)
        self.assertAlmostEqual(target['after']['mean_abs_m'],0,places=6)
        self.assertEqual(target['paired_after']['count'],2)
        self.assertAlmostEqual(full['after']['mean_abs_m'],1,places=6)
        self.assertEqual(target['after_full_target_hit_differs'],2)
        self.assertIsNone(result['geometry_quality_passed'])
        with np.load(self.root/result['ray_data'],allow_pickle=False) as data:
            self.assertEqual(data['after_target_object_id'].tolist(),['target/old','target/new',''])
            self.assertEqual(data['after_full_scene_object_id'].tolist(),['occluder','occluder',''])
            self.assertEqual(data['target_common_valid'].tolist(),[True,False,False])
            self.assertEqual(data['scan_triangle_index'][-1],-1)

    def test_empty_common_domain_reports_missing_coverage_without_fake_metrics(self):
        result = self.compare(object_prefixes=['target/new'])
        self.assertEqual(result['common_valid'],0)
        self.assertEqual(result['coverage_gained'],1)
        self.assertIsNone(result['before']); self.assertIsNone(result['after'])
        self.assertEqual(result['before_misses'],3)

    def test_full_scene_default_and_original_explicit_id_interface(self):
        result = self.compare(object_prefixes=[])
        self.assertEqual(set(result['domains']),{'full_scene'})
        result = self.compare(object_ids=['target/old'],object_prefixes=[],output=self.root/'explicit.json')
        self.assertEqual(result['common_valid'],1)
        with self.assertRaisesRegex(ContractError,'unknown explicit'):
            self.compare(object_ids=['target/new'],output=self.root/'missing.json')

    def test_distance_limit_applies_to_both_models_and_scan(self):
        query = json.loads(self.query.read_text()); query['max_distance_m']=1.1
        self.query.write_text(json.dumps(query))
        run(self.config,self.query,self.root/'limited',{'source':self.root/'source'})
        result = self.compare(evidence_path=self.root/'limited/evidence.json')
        self.assertEqual(result['scan_misses'],3)
        self.assertEqual(result['after_misses'],3)
        self.assertEqual(result['domains']['full_scene']['after_misses'],1)

    def test_changed_evidence_domain_or_source_rejected(self):
        saved = json.loads(self.evidence.read_text())
        saved['data']['rays'][0]['origin_work_m'][0] += .1
        self.evidence.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ContractError,'origin_work_m'):
            self.compare()
        (self.root/'source/scan.ply').write_bytes(b'changed')
        with self.assertRaisesRegex(ContractError,'hash changed'):
            self.compare()

    def test_wrong_scene_or_axes_and_unknown_prefix_rejected(self):
        wrong = self.export('wrong',{'target/old':plane(0)},scene_id='other')
        with self.assertRaisesRegex(ContractError,'scene_id'):
            self.compare(after_path=wrong)
        wrong = self.export('axes',{'target/old':plane(0)},transform=np.eye(4))
        with self.assertRaisesRegex(ContractError,'axis conversion'):
            self.compare(after_path=wrong)
        with self.assertRaisesRegex(ContractError,'neither model'):
            self.compare(object_prefixes=['typo/'])

    def test_sources_and_existing_reports_are_preserved(self):
        with self.assertRaisesRegex(ContractError,'outside source'):
            self.compare(output=self.root/'source/report.json')
        self.compare()
        with self.assertRaisesRegex(ContractError,'new output'):
            self.compare()

    def test_diagnostics_find_shift_deleted_surface_and_false_obstruction(self):
        before = self.export('complete', {'target/left': plane(0,-1,0), 'target/right': plane(0,0,1)})
        after = self.export('damaged', {'target/renamed': plane(.2,-1,0),
                                     'background': plane(-1,0,1), 'blocker': plane(1,2,4)})
        result = self.compare(before_path=before, after_path=after)
        summaries = result['anomaly_summary']['domains']
        target, full = summaries['target'], summaries['full_scene']
        missing = target['scan_hit_model_miss']
        self.assertEqual((missing['count'], missing['new_count'], missing['persistent_count']), (1,1,0))
        self.assertEqual(missing['examples'][0]['ray_index'], 1)
        self.assertIsNone(missing['examples'][0]['after_point_work_m'])
        # A background hit must not hide a deleted target surface.
        self.assertEqual(full['scan_hit_model_miss']['count'], 0)
        self.assertEqual(full['surface_deviation']['examples'][0]['after_object_id'], 'background')
        self.assertEqual(full['scan_miss_model_hit']['count'], 1)
        self.assertEqual(full['scan_miss_model_hit']['examples'][0]['after_object_id'], 'blocker')
        self.assertEqual(full['hit_identity_change']['count'], 2)
        self.assertAlmostEqual(target['surface_deviation']['examples'][0]['after_error_distance_m'], .2, places=6)
        json.dumps(result['anomaly_summary'], allow_nan=False)

    def test_diagnostics_preserve_persistent_misses_and_distinguish_renames(self):
        result = self.compare(before_path=self.before, after_path=self.before)
        missing = result['anomaly_summary']['domains']['target']['scan_hit_model_miss']
        self.assertEqual((missing['count'], missing['new_count'], missing['persistent_count']), (1,0,1))
        after = self.export('renamed', {'renamed': plane(0)})
        before = self.export('original', {'original': plane(0)})
        result = self.compare(before_path=before, after_path=after, object_prefixes=[], output=self.root/'renamed.json')
        summary = result['anomaly_summary']['domains']['full_scene']
        self.assertEqual(summary['hit_identity_change']['count'], 2)
        self.assertEqual(summary['surface_deviation']['largest_increases']['count'], 0)
        self.assertEqual(summary['surface_deviation']['examples'][0]['after_error_distance_m'], 0)
        self.assertIsNone(result['geometry_quality_passed'])

    def test_source_led_query_exposes_thin_surface_outside_original_query(self):
        # A query below a missing panel gives a clean but incomplete review.
        trimesh.util.concatenate([plane(0), plane(1.5,-.25,.25)]).export(self.root/'source/scan.ply')
        before = self.export('panel-present', {'floor':plane(0), 'panel':plane(1.5,-.25,.25)})
        after = self.export('panel-absent', {'floor':plane(0)})
        reports = []
        for label, height in [('blind',1), ('source-led',2)]:
            self.query.write_text(json.dumps({'op':'raycast','origins_work_m':[[.1,0,height]],'directions_work':[[0,0,-1]]}))
            run(self.config,self.query,self.root/label,{'source':self.root/'source'})
            reports.append(self.compare(before_path=before, after_path=after, object_prefixes=[],
                           evidence_path=self.root/label/'evidence.json', output=self.root/(label+'.json')))
        blind, discovered = [r['anomaly_summary']['domains']['full_scene'] for r in reports]
        self.assertEqual(blind['surface_deviation']['examples'][0]['after_error_distance_m'], 0)
        self.assertEqual(discovered['hit_identity_change']['count'], 1)
        self.assertAlmostEqual(discovered['surface_deviation']['examples'][0]['after_error_distance_m'], 1.5, places=6)
        # Restoring the surface and replaying the discovery query closes this defect.
        repaired = self.compare(before_path=after, after_path=before, object_prefixes=[],
                   evidence_path=self.root/'source-led/evidence.json', output=self.root/'repaired.json')
        self.assertAlmostEqual(repaired['anomaly_summary']['domains']['full_scene']['surface_deviation']['examples'][0]['after_error_distance_m'], 0, places=6)

    def test_summary_limits_examples_without_hiding_total_coverage(self):
        count = 20
        origins = np.column_stack([np.arange(count), np.zeros(count), np.ones(count)])
        empty = np.full((count,3),np.nan)
        arrays = {'origins_work_m':origins, 'directions_work':np.tile([0,0,-1],(count,1)),
                  'scan_valid':np.ones(count,dtype=bool), 'scan_points_work_m':origins-np.array([0,0,1])}
        for phase in ['before','after']:
            arrays.update({f'{phase}_full_scene_valid':np.zeros(count,dtype=bool),
                           f'{phase}_full_scene_object_id':np.full(count,''),
                           f'{phase}_full_scene_points_work_m':empty})
        result = audit.ray_diagnostics.summarize_anomalies(arrays,'full_scene',limit=3)
        missing = result['scan_hit_model_miss']
        self.assertEqual((missing['count'],missing['persistent_count']), (20,20))
        self.assertEqual(len(missing['examples']), 3)
        self.assertEqual(missing['examples'][-1]['ray_index'], 19)
        self.assertEqual(missing['hit_pair_groups'][0]['anchor_bounds_work_m'], [[0,0,0],[19,0,0]])
        json.dumps(result,allow_nan=False)
