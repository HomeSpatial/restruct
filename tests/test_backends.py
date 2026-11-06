# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from indoor_recon.backend_config import BOXER_CHECKPOINTS, boxer_checkpoints, private_boxer_weights, source_identity
from indoor_recon.backend_verification import verify_backends
from indoor_recon.contracts import ContractError, sha256
from indoor_recon.jobs import atomic_json
from indoor_recon.perception import predict, wsl_path
from indoor_recon.startup import _runtime
from indoor_recon.workers.infer import strict_boxer_state


class BackendTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root/'source'
        self.source.mkdir()
        (self.source/'backend.py').write_text('# fixture')
        (self.source/'code_template.txt').write_text('prompt v1')
        self.weights = self.root/'weights'
        self.weights.mkdir()
        for role, name in BOXER_CHECKPOINTS.items():
            (self.weights/name).write_bytes(role.encode())
        (self.weights/'model.safetensors').write_bytes(b'model')
        self.runtime = {
            'boxer': {'python': sys.executable, 'source_dir': str(self.source), 'weights_dir': str(self.weights)},
            'spatiallm': {'python': sys.executable, 'source_dir': str(self.source), 'model_dir': str(self.weights)}}
        self.labels = self.root/'labels.json'
        atomic_json(self.labels, {'labels': ['chair']})

    def test_relative_checkpoints_use_weight_root_and_do_not_rename_sources(self):
        custom = self.weights/'renamed.ckpt'
        custom.write_bytes(b'custom checkpoint')
        runtime = self.root/'runtime.local.json'
        atomic_json(runtime, {'boxer': dict(self.runtime['boxer'], weights_dir='weights', checkpoints={'boxer': 'renamed.ckpt'})})
        binding = _runtime(runtime)['boxer']
        self.assertEqual(boxer_checkpoints(binding)['boxer'], custom)
        cache = private_boxer_weights(binding, self.root/'cache')
        self.assertEqual((cache/BOXER_CHECKPOINTS['boxer']).read_bytes(), custom.read_bytes())
        self.assertTrue(custom.is_file())
        self.assertEqual((self.weights/BOXER_CHECKPOINTS['boxer']).read_bytes(), b'boxer')
        (cache/BOXER_CHECKPOINTS['boxer']).unlink()
        (cache/BOXER_CHECKPOINTS['boxer']).write_bytes(b'wrong cache')
        with self.assertRaisesRegex(ContractError, 'differs'):
            private_boxer_weights(binding, cache)

    def test_bad_checkpoint_role_blocks_before_worker(self):
        self.runtime['boxer']['checkpoints'] = {'typo': 'missing'}
        with patch('indoor_recon.backend_verification.subprocess.run') as run:
            report = verify_backends(self.runtime, self.root/'check', backend='boxer', labels_file=self.labels)
        self.assertFalse(report['passed'])
        self.assertIn('accepts only', report['errors'][0])
        run.assert_not_called()

    def test_custom_or_archive_sources_are_warnings_not_rejection(self):
        with patch('indoor_recon.backend_verification.subprocess.run') as run:
            report = verify_backends(self.runtime, self.root/'check', level='config', labels_file=self.labels)
        self.assertTrue(report['passed'])
        run.assert_not_called()
        self.assertTrue(report['warnings'])
        self.assertEqual(report['stages']['boxer']['source']['baseline_match'], 'unknown')
        self.assertFalse(report['stages']['boxer']['baseline_tensor_match'])
        self.assertEqual(report['stages']['spatiallm']['identity']['source_files']['code_template.txt'], sha256(self.source/'code_template.txt'))
        with self.assertRaisesRegex(ContractError, 'new verification'):
            verify_backends(self.runtime, self.root/'check', level='config')

    def test_smoke_routes_custom_weights_and_captures_worker_failure(self):
        self.runtime['boxer']['checkpoints'] = {'boxer': 'model.safetensors'}
        def worker(command, **kwargs):
            request = json.loads(Path(command[-1]).read_text())
            private = Path(request['private_weights'])
            self.assertEqual((private/BOXER_CHECKPOINTS['boxer']).read_bytes(), b'model')
            self.assertEqual(request['labels'], ['chair'])
            kwargs['stdout'].write('RECON_PROBE='+json.dumps({'passed': False, 'verified_level': 'load', 'error': 'forward incompatible'})+'\n')
            return SimpleNamespace(returncode=1)
        with patch('indoor_recon.backend_verification.subprocess.run', side_effect=worker):
            report = verify_backends(self.runtime, self.root/'check', backend='boxer', level='smoke', labels_file=self.labels)
        self.assertFalse(report['passed'])
        self.assertEqual(report['stages']['boxer']['verified_level'], 'load')
        self.assertIn('forward incompatible', report['errors'][0])
        self.assertFalse(report['stages']['boxer']['baseline_tensor_match'])

    def test_wsl_request_uses_translated_paths_and_no_shell(self):
        self.runtime['spatiallm'].update(python='/opt/env/bin/python', wsl_distro='Fixture')
        out = self.root/'wsl-check'
        def worker(command, **kwargs):
            self.assertEqual(command[:4], ['wsl.exe', '-d', 'Fixture', '--'])
            self.assertEqual(command[-1], wsl_path(out/'spatiallm/request.local.json'))
            request = json.loads((out/'spatiallm/request.local.json').read_text())
            self.assertEqual(request['source_dir'], wsl_path(self.source))
            self.assertEqual(request['model_dir'], wsl_path(self.weights))
            self.assertNotIn('shell', kwargs)
            kwargs['stdout'].write('RECON_PROBE={"passed":true,"verified_level":"imports"}\n')
            return SimpleNamespace(returncode=0)
        with patch('indoor_recon.perception.shutil.which', return_value='wsl.exe'), \
             patch('indoor_recon.backend_verification.subprocess.run', side_effect=worker):
            report = verify_backends(self.runtime, out, backend='spatiallm')
        self.assertTrue(report['passed'])

    def test_timeout_returns_failure_report(self):
        with patch('indoor_recon.backend_verification.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 1)):
            report = verify_backends(self.runtime, self.root/'check', backend='spatiallm', timeout=1)
        self.assertFalse(report['passed'])
        self.assertEqual(report['stages']['spatiallm']['verified_level'], 'config')
        self.assertTrue(Path(report['report']).is_file())

    def test_strict_boxer_checkpoint_rejects_silent_partial_load(self):
        tensor = SimpleNamespace(shape=(3, 4))
        with self.assertRaisesRegex(ValueError, 'missing'):
            strict_boxer_state({'weight': tensor}, {})
        with self.assertRaisesRegex(ValueError, 'shapes'):
            strict_boxer_state({'weight': tensor}, {'weight': SimpleNamespace(shape=(2, 4))})
        self.assertEqual(strict_boxer_state({'weight': tensor}, {'weight': tensor}), {'weight': tensor})

    def test_template_weights_and_package_changes_invalidate_prediction_resume(self):
        job = self.root/'job'
        prepared = {'scene_id': 'fixture', 'has_color': True, 'frames': [], 'regions': []}
        atomic_json(job/'prepare/prepared.json', prepared)
        atomic_json(job/'prepare/state.json', {'status': 'complete', 'files': {'prepared.json': sha256(job/'prepare/prepared.json')}})
        atomic_json(job/'bindings.local.json', {})
        packages = {'torch': 'fixture-v1'}
        def worker(command, **kwargs):
            if command[2].endswith('probe.py'):
                return SimpleNamespace(returncode=0, stdout='RECON_PROBE='+json.dumps({'passed': True, 'packages': packages}), stderr='')
            atomic_json(job/'spatiallm/raw-predictions.json', {'regions': []})
            return SimpleNamespace(returncode=0)
        with patch('indoor_recon.perception.subprocess.run', side_effect=worker):
            predict(job, self.runtime, objects='none')
            self.assertTrue(predict(job, self.runtime, objects='none', resume=True)['stages']['spatiallm']['resumed'])
            for path in (self.source/'code_template.txt', self.weights/'model.safetensors'):
                before = path.read_bytes()
                path.write_bytes(before+b'changed')
                with self.assertRaises(ContractError):
                    predict(job, self.runtime, objects='none', resume=True)
                path.write_bytes(before)
            packages['torch'] = 'fixture-v2'
            with self.assertRaises(ContractError):
                predict(job, self.runtime, objects='none', resume=True)


if __name__ == '__main__':
    unittest.main()
