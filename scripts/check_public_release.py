# SPDX-License-Identifier: Apache-2.0
"""Validate the explicit public source manifest without reading Git history."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIRS = {'.git', '.venv', 'artifacts', 'build', 'dist', '__pycache__', 'private', 'public-release', '.pytest_cache'}


def public_files(root):
    root = Path(root).resolve()
    value = json.loads((root / 'release-files.json').read_text(encoding='utf-8'))
    names = value['files']
    if value.get('schema_version') != 1 or not names or len(names) != len(set(names)):
        raise ValueError('Invalid or duplicate public manifest entries')
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or '\\' in name or ':' in name or '..' in path.parts or str(path) != name:
            raise ValueError('Invalid release path: ' + name)
        if any(part in LOCAL_DIRS or part.endswith('.egg-info') for part in path.parts):
            raise ValueError('Local directory in release manifest: ' + name)
        if path.name == 'runtime.local.json' or path.name.startswith('.env') or path.suffix in {'.pem', '.key'}:
            raise ValueError('Local configuration or key in release manifest: ' + name)
        source = root / name
        if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError('Missing or unsafe release source: ' + name)
        if any(parent.is_symlink() for parent in source.parents if parent != root and parent.is_relative_to(root)):
            raise ValueError('Symlink directory in release source: ' + name)
    return sorted(names)


def audit(root):
    root = Path(root).resolve()
    names = public_files(root)
    expected = set(names)
    errors = []
    assets = {}
    manifest = json.loads((root / 'release-files.json').read_text(encoding='utf-8'))
    for asset in manifest.get('reviewed_assets', []):
        name = asset.get('path')
        notice = asset.get('notice')
        if (name not in expected or name in assets or
                PurePosixPath(name).suffix.lower() not in {'.png', '.jpg', '.jpeg'} or
                not re.fullmatch(r'[a-f0-9]{64}', asset.get('sha256', '')) or
                not isinstance(asset.get('provenance'), str) or not asset['provenance'].strip() or
                notice not in expected or PurePosixPath(notice).suffix != '.md'):
            raise ValueError('Invalid reviewed asset record: ' + str(name))
        assets[name] = asset
    # Catch new files requiring an explicit decision; ignored working outputs are never exported.
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in LOCAL_DIRS and not d.endswith('.egg-info')]
        for filename in files:
            if filename.endswith(('.pyc', '.pyo')) or filename == 'runtime.local.json':
                continue
            name = (Path(directory) / filename).relative_to(root).as_posix()
            if name not in expected:
                errors.append('Unlisted source file: ' + name)
    patterns = {
        'private case identifier': re.compile(r'map[ _-]*' + r'5|7bc286c' + r'1b6|1ada7a' + r'0617|286b55' + r'a2bf', re.I),
        'private machine binding': re.compile(r'[A-Z]:[/\\]HomeSpatial' + r'Unity|[A-Z]:[/\\]Users[/\\]ywh' + r'13|/home/ywh' + r'1357|wsl_' + r'pc1|gitlab\.home\.', re.I),
        'credential-like data': re.compile(
            r'-----BEGIN (?:RSA |OPENSSH |EC |DSA |ENCRYPTED )?PRIVATE KEY-----'
            r'|\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'
            r'|\b(?:sk-(?:(?:proj|ant)-[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{32,})|hf_[A-Za-z0-9]{30,})\b'
            r'|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bAIza[A-Za-z0-9_-]{35}\b'
            r'|\bxox[baprs]-[A-Za-z0-9-]{20,}\b'
            r'|https?://[^\s/:<>"\x27]+:[^\s/@<>"\x27]+@'),
    }
    links = re.compile(r'\[[^\]\n]*\]\(([^)\n]+)\)')
    for name in names:
        source = root / name
        raw = source.read_bytes()
        if len(raw) > 5 * 1024 * 1024:
            errors.append('Oversized source file requires separate asset review: ' + name)
        if name in assets:
            if hashlib.sha256(raw).hexdigest() != assets[name]['sha256']:
                errors.append('Reviewed asset checksum changed: ' + name)
            suffix = source.suffix.lower()
            if not (raw.startswith(b'\x89PNG\r\n\x1a\n') if suffix == '.png' else raw.startswith(b'\xff\xd8\xff')):
                errors.append('Reviewed asset is not the declared image type: ' + name)
            for reason, pattern in patterns.items():
                if pattern.search(name) or pattern.search(raw.decode('utf-8', errors='ignore')):
                    errors.append(reason + ': ' + name)
            continue
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            errors.append('Binary file requires a separately reviewed asset policy: ' + name)
            continue
        for reason, pattern in patterns.items():
            if pattern.search(name) or pattern.search(content):
                errors.append(reason + ': ' + name)
        if source.suffix == '.md':
            for match in links.finditer(content):
                href = match.group(1).strip().strip('<>')
                if href.startswith(('#', 'https://', 'http://', 'mailto:', 'data:')):
                    continue
                href = unquote(href.split('#', 1)[0].split('?', 1)[0])
                target = (source.parent / href).resolve()
                if not target.is_relative_to(root) or not target.exists():
                    errors.append(f'Broken or external local link: {name} -> {href}')
                elif target.is_file() and target.relative_to(root).as_posix() not in expected:
                    errors.append(f'Link to non-public file: {name} -> {href}')
    if errors:
        raise ValueError('\n'.join(sorted(set(errors))))
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    names = audit(args.root)
    print(json.dumps({'passed': True, 'public_files': len(names), 'git_history_exported': False}))


if __name__ == '__main__':
    main()
