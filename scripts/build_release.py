# SPDX-License-Identifier: Apache-2.0
"""Build reviewed source/core/add-on packages in a fresh isolated staging tree."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

from check_public_release import audit


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(root, out):
    root, out = Path(root).resolve(), Path(out).resolve()
    names = audit(root)
    if out.exists():
        raise ValueError('Use a new release output directory')
    out.mkdir(parents=True)
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    version = project['version']
    source_name = f"{project['name']}-{version}"
    manifest = {name: sha(root / name) for name in names}
    with tempfile.TemporaryDirectory(prefix='recon-release-') as temp:
        stage = Path(temp) / source_name
        for name in names:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, target)
        audit(stage)
        source_zip = out / (source_name + '-source.zip')
        with zipfile.ZipFile(source_zip, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                archive.write(stage / name, source_name + '/' + name)
        subprocess.run([sys.executable, '-m', 'build', '--outdir', str(out)], cwd=stage, check=True)
        subprocess.run([sys.executable, '-B', str(stage / 'blender/package_addon.py'),
                        str(out / 'homespatial_model-0.3.1.zip')], cwd=stage, check=True)
        wheels = list(out.glob('*.whl'))
        sdists = list(out.glob('*.tar.gz'))
        assert len(wheels) == len(sdists) == 1
        with zipfile.ZipFile(wheels[0]) as archive:
            entries = archive.namelist()
            assert all(n.startswith('indoor_recon/') or '.dist-info/' in n for n in entries)
            metadata = archive.read(next(n for n in entries if n.endswith('.dist-info/METADATA'))).decode()
            assert 'License-Expression: Apache-2.0 AND MIT' in metadata
            assert any(n.endswith('.dist-info/licenses/LICENSE') for n in entries)
            assert 'indoor_recon/viewer/static/vendor/three/LICENSE' in entries
        with tarfile.open(sdists[0]) as archive:
            entries = [name.split('/') for name in archive.getnames()]
            assert not any(any(part in {'blender', 'cases', '.git', 'private', 'artifacts'} for part in p) for p in entries)
        subprocess.run([sys.executable, '-m', 'twine', 'check', str(wheels[0]), str(sdists[0])], check=True)
    (out / 'SOURCE_MANIFEST.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    (out / 'SHA256SUMS').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in sorted(out.iterdir()) if p.is_file()), encoding='utf-8')
    return {'passed': True, 'version': version, 'source_files': len(names), 'output': str(out)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.out)))


if __name__ == '__main__':
    main()
