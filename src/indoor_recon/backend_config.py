# SPDX-License-Identifier: Apache-2.0
"""Known backend baselines and user-selected checkpoint bindings."""
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess

from .contracts import ContractError, sha256

BASELINES = json.loads(Path(__file__).with_name('backends.json').read_text(encoding='utf-8'))
BOXER_CHECKPOINTS = BASELINES['boxer']['checkpoints']


def boxer_checkpoints(binding):
    """Role -> source path. Relative overrides are relative to weights_dir."""
    overrides = binding.get('checkpoints', {})
    if not isinstance(overrides, dict) or set(overrides) - set(BOXER_CHECKPOINTS):
        raise ContractError('boxer.checkpoints accepts only boxer, dinov3 and owlv2')
    root = Path(binding['weights_dir']).resolve()
    paths = {}
    for role, default in BOXER_CHECKPOINTS.items():
        value = overrides.get(role, default)
        if not isinstance(value, str) or not value.strip():
            raise ContractError(f'boxer.checkpoints.{role} must be a nonempty path')
        path = Path(value)
        if PureWindowsPath(value).is_absolute() and os.name != 'nt':
            raise ContractError('Use paths accessible to the CLI host for checkpoint overrides')
        paths[role] = (path if path.is_absolute() else root / path).resolve()
    return paths


def private_boxer_weights(binding, directory):
    """Keep upstream-required filenames in a per-run cache; never rename originals."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for role, source in boxer_checkpoints(binding).items():
        dest = directory / BOXER_CHECKPOINTS[role]
        if not dest.exists():
            try:
                os.link(source, dest)
            except OSError:
                shutil.copy2(source, dest)
        if sha256(dest) != sha256(source):
            raise ContractError(f'Private checkpoint differs: {dest.name}')
    return directory


def source_identity(source):
    """Hash code and configuration used by adapters, including the prompt template."""
    source = Path(source)
    excluded = {'.git', '.venv', '__pycache__', 'ckpts', 'output', 'outputs', 'artifacts', '.cache'}
    files = {}
    for directory, dirs, names in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in excluded)
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix in {'.py', '.txt', '.json', '.yaml', '.yml', '.toml'}:
                files[path.relative_to(source).as_posix()] = sha256(path)
    return files


def source_status(backend, source):
    """A different/missing revision is informational, never an incompatibility verdict."""
    source = Path(source).resolve()
    result = {'baseline_revision': BASELINES[backend]['source_revision'], 'revision': None,
              'dirty': None, 'baseline_match': 'unknown'}
    if not (source / '.git').exists():
        return result
    try:
        def git(*args):
            return subprocess.check_output(['git', '-C', str(source), *args], text=True,
                                           stderr=subprocess.DEVNULL, timeout=10).strip()
        result['revision'] = git('rev-parse', 'HEAD')
        result['dirty'] = bool(git('status', '--porcelain', '--untracked-files=normal'))
        result['baseline_match'] = ('match' if result['revision'] == result['baseline_revision']
                                    and not result['dirty'] else 'different')
    except (OSError, subprocess.SubprocessError):
        pass
    return result
