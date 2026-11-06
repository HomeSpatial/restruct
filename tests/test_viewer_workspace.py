"""Task viewer integration: committed results, provenance, partial jobs and asset boundaries."""
import json
from pathlib import Path
import tempfile
import unittest

from indoor_recon.contracts import read_json, sha256
from indoor_recon.viewer import _cached, resolve_model_file, resolve_model_dir
from indoor_recon.viewer import workspace as ws


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.task = self.root/'task'
        self.job = self.task/'job'
        self.assets = self.root/'declared-assets'
        self.assets.mkdir()
        (self.assets/'photo.png').write_bytes(b'photo')
        self.prepared = dict(scene_id='sample', fingerprint='input-one', has_color=True,
            options={'floor_cuts_m': [2.5]}, regions=[dict(id='floor-0/region-0', floor_id='floor-0',
                ownership_bounds_m=[[-5, -5, -5], [5, 5, 5]])],
            frames=[dict(id='frame-a', size=[640, 480], image=dict(root='photos', path='photo.png', sha256=sha256(self.assets/'photo.png')))])
        self.write('prepare/prepared.json', self.prepared)
        self.write('bindings.local.json', {'photos': str(self.assets)})
        self.candidate = dict(id='boxer/0', category='chair', geometry=dict(type='obb', nature='volume',
            center_m=[0, 0, 1], size_m=[1, 1, 1], R_work_from_object=[[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
            sources=[dict(kind='prediction', reference=dict(root='job', path='boxer/fused-predictions.json'), frame_ids=['frame-a'], record_id='0')])

    def write(self, name, data):
        path = self.job/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')
        return sha256(path)

    def backend(self, backend='boxer', complete=True):
        self.write(backend+'/identity.json', {'prepared': sha256(self.job/'prepare/prepared.json')})
        candidate = dict(self.candidate, id=backend+'/0')
        files = {'proposals.json': self.write(backend+'/proposals.json', dict(schema_version=1, kind='proposals',
            scene_id='sample', backend=backend, coordinate_frame='work_z_up_metres', candidates=[candidate]))}
        units = {}
        if backend == 'boxer':
            units['frames/000000.json'] = self.write(backend+'/frames/000000.json', dict(frame_id='frame-a',
                detections=[dict(id='detection-1', label='chair', score2d=.7, score3d=.8,
                    box2d_native=[10, 40, 20, 80], obb_data=[1]*100)]))
            files['fused-predictions.json'] = self.write(backend+'/fused-predictions.json',
                {'objects': [{'id': 0, 'detection_ids': ['detection-1']}]})
        else:
            units['regions/0000.json'] = self.write(backend+'/regions/0000.json', dict(region_id='floor-0/region-0',
                token_limit_reached=True, new_tokens=4096, boxes=[dict(center=[0, 0, 1], scale=[2, 0, 2],
                rotation=[[1, 0, 0], [0, 1, 0], [0, 0, 1]], **{'class': 'wall'})]))
        files['units.json'] = self.write(backend+'/units.json', units)
        self.write(backend+'/state.json', dict(status='complete' if complete else 'running', files=files))

    def test_cache_separates_decoded_json_from_byte_hash(self):
        path = self.job/'prepare/prepared.json'
        self.assertEqual(_cached(path, read_json), self.prepared)
        self.assertEqual(_cached(path, sha256), sha256(path))
        self.assertEqual(_cached(path, read_json), self.prepared)

    def test_registered_external_task_with_separate_model_history(self):
        viewer = self.root/'viewer'
        viewer.mkdir()
        history = self.root/'model-history'
        build = history/'build-r9'
        build.mkdir(parents=True)
        (build/'model.glb').write_bytes(b'glb')
        (build/'build.json').write_text(json.dumps({'passed': True, 'revision': 9}))
        (viewer/'viewer-tasks.local.json').write_text(json.dumps({'tasks': [dict(id='@clean', name='Clean result',
            task_dir=str(self.task), models_dir=str(history))]}))
        self.backend()
        listing = ws.tasks(viewer)
        self.assertEqual([t['id'] for t in listing], ['@clean'])
        self.assertEqual(listing[0]['label'], 'Clean result')
        description = ws.describe(viewer, '@clean')
        self.assertEqual(description['revisions'][0]['path'], '@clean/build-r9/model.glb')
        self.assertEqual(description['layers']['boxer']['status'], 'complete')
        self.assertEqual(ws.photo_asset(viewer, '@clean', 'frame-a'), (self.assets/'photo.png').resolve())
        self.assertEqual(resolve_model_file(viewer, '@clean/build-r9/model.glb'), (build/'model.glb').resolve())
        self.assertIsNone(resolve_model_file(viewer, '@clean/../declared-assets/photo.png'))
        self.assertIsNone(resolve_model_file(viewer, '@unknown/build-r9/model.glb'))
        self.assertIsNone(resolve_model_dir(viewer, '@clean/..'))
        with self.assertRaises(ValueError):
            ws.context(viewer, '@clean/../task')

    def test_complete_task_has_fused_links_photo_and_native_box(self):
        self.backend()
        description = ws.describe(self.root, 'task')
        self.assertEqual(description['layers']['boxer']['status'], 'complete')
        self.assertEqual(description['layers']['boxer']['detection_ids']['boxer/0'], ['detection-1'])
        self.assertEqual(description['revisions'], [])
        self.assertEqual([t['id'] for t in ws.tasks(self.root)], ['task'])
        frame = ws.frame_data(self.root, 'task', 'frame-a')
        self.assertEqual(frame['detections'][0]['box2d_native'], [10, 40, 20, 80])
        self.assertNotIn('obb_data', frame['detections'][0])
        self.assertEqual(ws.photo_asset(self.root, 'task', 'frame-a'), (self.assets/'photo.png').resolve())

    def test_unavailable_registered_task_does_not_hide_healthy_tasks(self):
        broken = self.root/'broken'
        (broken/'prepare').mkdir(parents=True)
        (broken/'prepare/prepared.json').write_text('{unfinished')
        (self.root/'viewer-tasks.local.json').write_text(json.dumps({'tasks': [
            dict(id='@missing', task_dir='removed'), dict(id='@broken', task_dir='broken')]}))
        self.assertEqual([t['id'] for t in ws.tasks(self.root)], ['task'])
        # Explicit selection still explains the error instead of returning unrelated data.
        for identity in ('@missing', '@broken'):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                ws.describe(self.root, identity)

    def test_prepared_change_rejects_prediction_snapshot(self):
        self.backend()
        self.write('prepare/prepared.json', dict(self.prepared, fingerprint='changed'))
        self.assertEqual(ws.describe(self.root, 'task')['layers']['boxer']['status'], 'invalid')
        with self.assertRaisesRegex(ValueError, 'different prepared'):
            ws.frame_data(self.root, 'task', 'frame-a')

    def test_changed_committed_proposals_are_not_displayed(self):
        self.backend()
        self.write('boxer/proposals.json', {'changed': True})
        data = ws.describe(self.root, 'task')['layers']['boxer']
        self.assertEqual(data['status'], 'invalid')
        self.assertEqual(data['candidates'], [])

    def test_partial_layout_uses_only_committed_regions_and_exposes_truncation(self):
        self.backend('spatiallm', complete=False)
        data = ws.describe(self.root, 'task')['layers']['spatiallm']
        self.assertEqual(data['status'], 'partial')
        self.assertEqual(len(data['candidates']), 1)
        self.assertEqual(data['candidates'][0]['geometry']['nature'], 'surface')
        self.assertTrue(data['regions'][0]['token_limit_reached'])
        self.assertNotIn('scores', data['candidates'][0])
        self.write('spatiallm/regions/0000.json', {'boxes': []})
        self.assertEqual(ws.describe(self.root, 'task')['layers']['spatiallm']['status'], 'invalid')

    def test_partial_boxer_has_raw_frames_but_no_fused_candidates(self):
        self.backend(complete=False)
        data = ws.describe(self.root, 'task')['layers']['boxer']
        self.assertEqual(data['status'], 'partial')
        self.assertEqual(data['candidates'], [])
        self.assertTrue(ws.frame_data(self.root, 'task', 'frame-a')['committed'])

    def test_asset_boundary_unknown_frame_and_changed_photo(self):
        with self.assertRaises(ValueError):
            ws.context(self.root, '..')
        with self.assertRaises(ValueError):
            ws.photo_asset(self.root, 'task', '../secret')
        with self.assertRaises(ValueError):
            ws.declared_asset({'root': 'photos', 'path': '../outside.png'}, {'photos': str(self.assets)})
        (self.assets/'photo.png').write_bytes(b'changed photo')
        with self.assertRaisesRegex(ValueError, 'changed'):
            ws.photo_asset(self.root, 'task', 'frame-a')

    def test_declared_scan_keeps_alignment_scale_and_byte_hash(self):
        (self.assets/'scan.glb').write_bytes(b'glTF')
        transform = [[0, -1, 0, 10], [1, 0, 0, 20], [0, 0, 1, 30], [0, 0, 0, 1]]
        evidence = dict(scene_id='sample', prepared={'sha256': sha256(self.job/'prepare/prepared.json')},
            scan=dict(asset=dict(root='photos', path='scan.glb', sha256=sha256(self.assets/'scan.glb')),
                metres_per_unit=.001, T_work_from_source_m=transform))
        (self.task/'evidence.json').write_text(json.dumps(evidence))
        path, spec = ws.scan_asset(self.root, 'task')
        self.assertEqual(path, (self.assets/'scan.glb').resolve())
        self.assertEqual(spec['transform'], transform)
        self.assertEqual(spec['scale'], .001)
        self.assertFalse(spec['sampled'])
        (self.assets/'scan.glb').write_bytes(b'different')
        self.assertEqual(ws.describe(self.root, 'task')['layers']['scan']['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
