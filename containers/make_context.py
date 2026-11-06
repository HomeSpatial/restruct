"""Prepare a reproducible Docker context with tested defaults or explicit custom backend refs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import runpy
import tarfile
import urllib.request


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url, path, expected=None):
    if path.exists():
        if expected and sha(path) != expected:
            raise ValueError('Existing download differs: '+str(path))
        return
    partial = path.with_suffix(path.suffix+'.part')
    with urllib.request.urlopen(url, timeout=60) as source, partial.open('wb') as target:
        shutil.copyfileobj(source, target, 4*1024*1024)
    if expected and sha(partial) != expected:
        raise ValueError('Downloaded asset hash differs: '+url)
    partial.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1],
                        help='Repository root; defaults to this script\'s checkout')
    parser.add_argument('--spatiallm-source', required=True, type=Path)
    parser.add_argument('--boxer-source', required=True, type=Path)
    parser.add_argument('--spatiallm-ref', help='Git commit/tag/ref; defaults to the tested baseline')
    parser.add_argument('--boxer-ref', help='Git commit/tag/ref; defaults to the tested baseline')
    parser.add_argument('--spatiallm-lock', type=Path, help='Custom self-contained, hash-locked requirements file')
    parser.add_argument('--boxer-lock', type=Path, help='Custom self-contained, hash-locked requirements file')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    core, out = args.repo.resolve(), args.out.resolve()
    release_files = runpy.run_path(str(core/'scripts/check_public_release.py'))['audit'](core)
    baselines = json.loads((core/'src/indoor_recon/backends.json').read_text(encoding='utf-8'))
    if any((out/name).exists() for name in ('core', 'addon', 'context-files.json')):
        raise ValueError('Use a new context directory; existing source trees may contain stale private files')
    out.mkdir(parents=True, exist_ok=True)
    resources = out/'resources'
    resources.mkdir(exist_ok=True)
    sources, selected_locks, warnings = {}, {}, []
    for name in ('spatiallm', 'boxer'):
        source = getattr(args, name+'_source').resolve()
        requested = getattr(args, name+'_ref') or baselines[name]['source_revision']
        actual = subprocess.check_output(['git','-C',str(source),'rev-parse','--verify',
                                          '--end-of-options',requested+'^{commit}'],text=True).strip()
        dirty = subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True).strip()
        if dirty:
            raise ValueError('Backend checkout has uncommitted changes; commit them or use a clean clone: '+str(source))
        lock = (getattr(args, name+'_lock') or core/'containers/locks'/(name+'.txt')).resolve()
        if not lock.is_file():
            raise ValueError('Dependency lock file is missing: '+str(lock))
        selected_locks[name] = lock
        baseline_match = (actual == baselines[name]['source_revision'] and
                          sha(lock) == sha(core/'containers/locks'/(name+'.txt')))
        if not baseline_match:
            warnings.append(name+': custom source/dependencies are unverified; run verify-backends --level smoke in the resulting environment')
        archive = resources/(name+'.tar')
        subprocess.run(['git','-C',str(source),'archive','--format=tar','--output',str(archive),actual],check=True)
        with tarfile.open(archive) as contents:
            licenses = [n for n in contents.getnames() if '/' not in n and n.upper().startswith(('LICENSE','NOTICE','COPYING'))]
        sources[name] = {'upstream_repository':baselines[name]['repository'], 'requested_ref':requested,
                         'commit':actual,'archive_sha256':sha(archive),'license_files':licenses,
                         'dependency_lock_sha256':sha(lock),'baseline_match':baseline_match}
    name = 'blender-5.1.2-linux-x64.tar.xz'
    checksum_url = 'https://download.blender.org/release/Blender5.1/blender-5.1.2.sha256'
    sums = urllib.request.urlopen(checksum_url,timeout=60).read().decode()
    expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1].lstrip('*') == name)
    download('https://download.blender.org/release/Blender5.1/'+name,resources/name,expected)
    sources['blender'] = {'version':'5.1.2','url':'https://download.blender.org/release/Blender5.1/'+name,
                          'sha256':expected,'checksum_url':checksum_url}
    resources.joinpath('SHA256SUMS').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in sorted(resources.iterdir())
                                                    if p.name.endswith(('.tar','.tar.xz'))),encoding='utf-8')
    resources.joinpath('sources.json').write_text(json.dumps(sources,indent=2)+'\n',encoding='utf-8')
    shutil.copy2(core/'containers/Dockerfile',out/'Dockerfile')
    shutil.copytree(core/'containers/locks',out/'locks',dirs_exist_ok=True)
    for name, lock in selected_locks.items():
        shutil.copy2(lock,out/'locks'/(name+'.txt'))
    ignored = shutil.ignore_patterns('__pycache__','*.pyc','*.egg-info','.git','.venv','artifacts','build','dist')
    for name in release_files:
        target = out/'core'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(core/name, target)
    addon = core/'blender'
    shutil.copytree(addon/'homespatial_model',out/'addon',ignore=ignored,dirs_exist_ok=True)
    shutil.copy2(addon/'run_agent.py',out/'addon/run_agent.py')
    shutil.copy2(addon/'README.md',out/'addon/README.md')
    for source, name in ((addon/'LICENSE','LICENSE'),(addon/'NOTICE','NOTICE'),
                         (core/'LICENSE','LICENSE-APACHE-2.0'),(core/'NOTICE','CORE-NOTICE')):
        shutil.copy2(source,out/'addon'/name)
    vendor = out/'addon/_vendor'
    vendor.mkdir(exist_ok=True)
    (vendor/'__init__.py').write_text('',encoding='utf-8')
    shutil.copy2(core/'src/indoor_recon/scene_model.py',vendor/'scene_model.py')
    manifest = {p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob('*'))
                if p.is_file() and p != out/'context-files.json'}
    out.joinpath('context-files.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'context':str(out),'sources':sources,'warnings':warnings}))


if __name__ == '__main__':
    main()
