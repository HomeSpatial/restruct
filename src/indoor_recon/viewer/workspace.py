# SPDX-License-Identifier: Apache-2.0
"""Read-only task views. External assets are limited to declared scans/photos."""
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from ..contracts import ContractError, read_json, resolve_asset, sha256, validate
from . import _cached, list_revisions, resolve_model_dir, registered_tasks


def read(path, default=None):
    return _cached(path, read_json) if path.is_file() else default


def tasks(root):
    root = Path(root).resolve()
    found = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                   {'cache', 'node_modules', '__pycache__', 'repo', 'input', 'inputs', 'vendor'}]
        base = Path(directory)
        if 'startup.json' in files or (base/'prepare/prepared.json').is_file() or (base/'job/prepare/prepared.json').is_file():
            try:
                _, job, prepared, _ = context(root, base.relative_to(root).as_posix())
                found.append(dict(id=base.relative_to(root).as_posix(), scene_id=prepared['scene_id'],
                                  name=base.name, mtime=(job/'prepare/prepared.json').stat().st_mtime))
                dirs[:] = []
            except (OSError, ValueError, KeyError):
                pass
    for identity, row in registered_tasks(root).items():
        try:
            _, job, prepared, _ = context(root, identity)
            found.append(dict(id=identity, scene_id=prepared['scene_id'], name=row.get('name', identity),
                              label=row.get('name'), mtime=(job/'prepare/prepared.json').stat().st_mtime))
        except (OSError, ValueError, KeyError):
            # A removed or incomplete external task must not hide healthy tasks.
            # Selecting its ID explicitly still returns context's diagnostic.
            continue
    return sorted(found, key=lambda item: (-item['mtime'], item['id']))


def context(root, relative):
    if relative.startswith('@'):
        row = registered_tasks(root).get(relative)
        base = row['task_dir'] if row else None
    else:
        base = resolve_model_dir(root, relative)
    if base is None:
        raise ValueError('Task directory is unavailable')
    # Prefer adjacent files so moving a task does not silently read the old job.
    job = base/'job' if (base/'job/prepare/prepared.json').is_file() else base
    prepared = read(job/'prepare/prepared.json')
    if not isinstance(prepared, dict) or not prepared:
        raise ValueError('No prepared task in this directory')
    startup = read(base/'startup.json', {})
    if not isinstance(startup, dict):
        raise ValueError('Invalid task startup record')
    if startup.get('scene_id', prepared['scene_id']) != prepared['scene_id']:
        raise ValueError('Task and preparation scene IDs differ')
    return base, job, prepared, startup


def url(route, relative, **kwargs):
    return '/api/'+route+'?'+urlencode(dict(task=relative, **kwargs))


def checked(path, expected):
    if not expected or not path.is_file() or _cached(path, sha256) != expected:
        raise ValueError('Missing or changed committed result: '+path.name)
    return read(path)


def backend_data(job, prepared, name):
    directory = job/name
    state = read(directory/'state.json', {})
    identity = read(directory/'identity.json', {})
    units = read(directory/'units.json', {})
    total = len(prepared['regions' if name == 'spatiallm' else 'frames'])
    result = dict(status='not_started', completed=len(units), total=total, candidates=[], review=[])
    if not state:
        if not total or (name == 'spatiallm' and not prepared.get('has_color')):
            result['status'] = 'skipped'
        return result
    if identity.get('prepared') != _cached(job/'prepare/prepared.json', sha256):
        raise ValueError(name+': results belong to a different prepared snapshot')
    result['status'] = 'partial'  # checkpoints alone do not prove a process is still running
    failure = read(directory/'failure.json')
    if failure:
        result['status'] = 'failed'
    if state.get('status') == 'complete':
        if 'units.json' in state.get('files', {}):
            units = checked(directory/'units.json', state['files']['units.json'])
        data = checked(directory/'proposals.json', state.get('files', {}).get('proposals.json'))
        validate(data)
        if data['scene_id'] != prepared['scene_id'] or data['backend'] != name:
            raise ValueError('Proposal scene/backend mismatch')
        review_hash = state.get('files', {}).get('review.json')
        result.update(status='complete', candidates=data['candidates'], review=checked(directory/'review.json', review_hash) if review_hash else [])
        if name == 'boxer' and 'fused-predictions.json' in state.get('files', {}):
            fused = checked(directory/'fused-predictions.json', state['files']['fused-predictions.json'])
            result['detection_ids'] = {f"boxer/{row['id']}": row.get('detection_ids', []) for row in fused['objects']}
    elif name == 'spatiallm':
        # Only committed, contiguous regions; never parse an in-flight token stream.
        regions = []
        for i in range(total):
            key = f'regions/{i:04}.json'
            if key not in units:
                break
            regions.append(checked(directory/key, units[key]))
        if regions:
            from ..perception import normalize
            data, review = normalize(name, {'regions': regions}, dict(prepared, regions=prepared['regions'][:len(regions)]))
            result.update(candidates=data['candidates'], review=review)
    if name == 'spatiallm':
        result['regions'] = []
        for i, region in enumerate(prepared['regions']):
            key = f'regions/{i:04}.json'
            if key in units:
                row = checked(directory/key, units[key])
                result['regions'].append(dict(id=region['id'], path='spatiallm/'+key,
                    token_limit_reached=row.get('token_limit_reached'), new_tokens=row.get('new_tokens')))
    return result


def declared_asset(asset, roots):
    path = resolve_asset(asset, roots).resolve()
    bound = Path(roots[asset['root']]).resolve()
    if not path.is_relative_to(bound) or not path.is_file():
        raise ValueError('Declared asset is missing or escapes its binding')
    if asset.get('sha256') and _cached(path, sha256) != asset['sha256']:
        raise ValueError('Declared asset changed: '+path.name)
    return path


def scan_asset(root, relative):
    base, job, prepared, startup = context(root, relative)
    evidence = read(base/'evidence.json', {})
    if evidence.get('scan'):
        if evidence.get('scene_id') != prepared['scene_id']:
            raise ValueError('Scan evidence belongs to a different scene')
        expected = evidence.get('prepared', {}).get('sha256')
        if expected and expected != _cached(job/'prepare/prepared.json', sha256):
            raise ValueError('Scan alignment belongs to a different preparation')
        spec = evidence['scan']
        roots = {**read(job/'bindings.local.json', {}), **startup.get('evidence_roots', {})}
        path = declared_asset(spec['asset'], roots)
        if path.suffix.lower() == '.glb':
            return path, dict(kind='glb', transform=spec['T_work_from_source_m'], scale=spec['metres_per_unit'],
                              sampled=False, label='原始扫描')
    # Prepared point clouds are already metric work coordinates; works for portable snapshots too.
    path = job/'prepare/global.ply'
    if not path.is_file():
        raise ValueError('No supported scan or prepared point cloud')
    expected = read(job/'prepare/state.json', {}).get('files', {}).get('global.ply')
    if expected and _cached(path, sha256) != expected:
        raise ValueError('Prepared point cloud has changed')
    return path, dict(kind='points', transform=[[int(i == j) for j in range(4)] for i in range(4)],
                      scale=1, sampled=True, label='预处理点云 · 最多 8 万点预览')


def point_preview(path):
    import numpy as np
    import open3d as o3d
    cloud = o3d.io.read_point_cloud(str(path))
    points = np.asarray(cloud.points)
    ids = np.linspace(0, len(points)-1, min(len(points), 80000), dtype=int)
    colors = np.asarray(cloud.colors)
    return dict(positions=points[ids].reshape(-1).tolist(),
                colors=colors[ids].reshape(-1).tolist() if len(colors) == len(points) else None,
                total=len(points), shown=len(ids))


def photo_asset(root, relative, frame_id):
    _, job, prepared, startup = context(root, relative)
    frame = next((f for f in prepared['frames'] if f['id'] == frame_id), None)
    if frame is None:
        raise ValueError('Unknown frame')
    roots = {**startup.get('evidence_roots', {}), **read(job/'bindings.local.json', {})}
    # Portable prepared snapshots store source assets under input/.
    roots.setdefault('bundle', str(job.parent/'input'))
    asset = dict(frame['image'])
    asset.setdefault('sha256', prepared.get('input_hashes', {}).get(asset['root']+':'+asset['path']))
    path = declared_asset(asset, roots)
    if path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
        raise ValueError('Unsupported photo format')
    return path


def frame_data(root, relative, frame_id):
    _, job, prepared, _ = context(root, relative)
    index = next((i for i, f in enumerate(prepared['frames']) if f['id'] == frame_id), None)
    if index is None:
        raise ValueError('Unknown frame')
    backend_data(job, prepared, 'boxer')  # rejects a stale prediction snapshot
    key = f'frames/{index:06}.json'
    units = read(job/'boxer/units.json', {})
    data = checked(job/'boxer'/key, units[key]) if key in units else {'detections': []}
    if data.get('frame_id', frame_id) != frame_id:
        raise ValueError('Detection frame does not match the requested photo')
    detections = [{k: v for k, v in item.items() if k != 'obb_data'} for item in data['detections']]
    return dict(id=frame_id, size=prepared['frames'][index]['size'], detections=detections,
                image=url('photo', relative, frame=frame_id), committed=key in units)


def describe(root, relative):
    base, job, prepared, _ = context(root, relative)
    result = dict(id=relative, scene_id=prepared['scene_id'], fingerprint=prepared.get('fingerprint'),
                  prepared_sha256=_cached(job/'prepare/prepared.json', sha256), layers={}, warnings=[],
                  frames=[dict(id=f['id'], size=f['size']) for f in prepared['frames']],
                  regions=prepared['regions'], floor_cuts_m=prepared.get('options', {}).get('floor_cuts_m', []),
                  revisions=list_revisions(root, relative))
    for name in ('spatiallm', 'boxer'):
        try:
            result['layers'][name] = backend_data(job, prepared, name)
        except (OSError, ValueError, KeyError, ContractError) as exc:
            result['layers'][name] = dict(status='invalid', candidates=[], error=str(exc))
    try:
        path, scan = scan_asset(root, relative)
        result['layers']['scan'] = dict(scan, url=url('scan', relative), bytes=path.stat().st_size, status='available')
    except (OSError, ValueError, KeyError) as exc:
        result['layers']['scan'] = dict(status='unavailable', error=str(exc))
    return result
