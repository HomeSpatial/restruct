# SPDX-License-Identifier: Apache-2.0
"""Explicit, isolated backend validation without scan data or a reconstruction job."""
import json
import os
from pathlib import Path
import subprocess

from .backend_config import BASELINES, boxer_checkpoints, private_boxer_weights, source_identity
from .contracts import ContractError, read_json, sha256
from .jobs import atomic_json
from .perception import prediction_plan, wsl_path


def verify_backends(runtime, out, backend='all', level='imports', labels_file=None, timeout=600):
    if level not in ('config', 'imports', 'load', 'smoke') or backend not in ('all', 'spatiallm', 'boxer'):
        raise ContractError('Unknown backend or validation level')
    if timeout <= 0:
        raise ContractError('Timeout must be positive')
    out = Path(out).resolve()
    if out.exists():
        raise ContractError('Use a new verification output directory')
    # Force eligibility: installation checks must not skip a missing backend based on scan content.
    result = prediction_plan({'frames': [True], 'has_color': True}, runtime,
        layout='spatiallm' if backend in ('all', 'spatiallm') else 'none',
        objects='boxer' if backend in ('all', 'boxer') else 'none', labels_file=labels_file, probe=False)
    result.update(level=level, quality='not_evaluated')
    out.mkdir(parents=True)
    if result['passed']:
        labels = read_json(result['labels_file'])['labels'] if result['labels_file'] else []
        for name, stage in result['stages'].items():
            binding = runtime[name]
            stage['verified_level'] = 'config'
            stage['identity'] = {'source_files': source_identity(binding['source_dir'])}
            if name == 'spatiallm':
                root = Path(binding['model_dir'])
                weights = {p.relative_to(root).as_posix(): sha256(p) for p in sorted(root.rglob('*'))
                           if p.is_file() and '.cache' not in p.relative_to(root).parts}
                matches = weights.get('model.safetensors') == BASELINES[name]['model_sha256']
            else:
                weights = {role: sha256(path) for role, path in boxer_checkpoints(binding).items()}
                matches = weights == BASELINES[name]['sha256']
            stage['identity']['weights'] = weights
            stage['baseline_tensor_match'] = matches
            if not matches:
                result['warnings'].append(f'{name}: custom weights; baseline hashes differ, compatibility needs validation')
            if level == 'config':
                continue
            directory = out / name
            directory.mkdir()
            request = dict(backend=name, level=level, source_dir=str(Path(binding['source_dir']).resolve()),
                           cache=str(directory / 'cache'), options={'detect_type': 'arch'})
            if name == 'spatiallm':
                request['model_dir'] = str(Path(binding['model_dir']).resolve())
            else:
                request['labels'] = labels
                if level in ('load', 'smoke'):
                    request['private_weights'] = str(private_boxer_weights(binding, directory/'cache/weights'))
            worker = str(Path(__file__).with_name('workers')/'probe.py')
            request_path = directory/'request.local.json'
            command = [binding['python'], '-B', worker, name, request['source_dir'], json.dumps(labels), str(request_path)]
            if binding.get('wsl_distro'):
                for key in ('source_dir', 'cache', 'model_dir', 'private_weights'):
                    if key in request:
                        request[key] = wsl_path(request[key])
                command = ['wsl.exe', '-d', binding['wsl_distro'], '--', binding['python'], '-B',
                           wsl_path(worker), name, request['source_dir'], json.dumps(labels), wsl_path(request_path)]
            atomic_json(request_path, request)
            info = None
            log_path = directory/'worker.log'
            try:
                with log_path.open('w', encoding='utf-8') as log:
                    completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                                 PYTHONIOENCODING='utf-8'))
                lines = [line[len('RECON_PROBE='):] for line in log_path.read_text(encoding='utf-8', errors='replace').splitlines()
                         if line.startswith('RECON_PROBE=')]
                info = json.loads(lines[-1]) if lines else {'passed': False, 'error': 'No worker result; inspect worker.log'}
                if completed.returncode or not info['passed']:
                    raise ValueError(info.get('error', f'Worker exit {completed.returncode}'))
                stage.update(verified_level=level, status='verified', verification=info)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                stage.update(status='blocked', verification=info, error=str(exc),
                             verified_level=(info or {}).get('verified_level', 'config'))
                result['errors'].append(f'{name}: {exc}')
    result['passed'] = not result['errors']
    result['summary'] = {'execution': 'completed' if result['passed'] else 'blocked',
                         'content': level + '_checked', 'quality': 'not_evaluated'}
    result['report'] = str(out/'verification.json')
    atomic_json(out/'verification.json', result)
    return result
