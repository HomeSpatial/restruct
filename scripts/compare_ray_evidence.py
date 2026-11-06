"""Repeat pinned scan rays against two GLBs, retaining coverage and hit provenance."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import open3d as o3d
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from indoor_recon.contracts import ContractError, read_json, sha256
from indoor_recon.evidence import Evidence
from indoor_recon.jobs import atomic_json
from indoor_recon import ray_diagnostics
from indoor_recon.glb import WORK_FROM_GLTF, load_meshes  # re-exported for scene scripts and tests

def cast_hits(meshes, object_ids, origins, directions, maximum):
    """Empty selections are explicit misses, useful for added/deleted components."""
    tracer = o3d.t.geometry.RaycastingScene()
    names = {}
    for name in sorted(object_ids):
        xyz, faces = meshes[name]
        names[tracer.add_triangles(o3d.core.Tensor(xyz), o3d.core.Tensor(faces))] = name
    count = len(origins)
    distance = np.full(count, np.inf)
    identity = np.full(count, '', dtype=f'<U{max([1]+[len(n) for n in names.values()])}')
    triangle = np.full(count, -1, dtype=np.int64)
    normals = np.full((count,3), np.nan)
    if names:
        rays = np.column_stack((origins, directions)).astype(np.float32)
        hit = tracer.cast_rays(o3d.core.Tensor(rays))
        distance = hit['t_hit'].numpy().astype(float)
        valid = np.isfinite(distance)
        if maximum is not None:
            valid &= distance <= maximum
        geometry_ids = hit['geometry_ids'].numpy()
        for key, name in names.items():
            identity[valid & (geometry_ids == key)] = name
        triangle[valid] = hit['primitive_ids'].numpy()[valid]
        normals[valid] = hit['primitive_normals'].numpy()[valid]
        distance[~valid] = np.inf
    valid = np.isfinite(distance)
    points = np.full(origins.shape, np.nan)
    points[valid] = origins[valid]+directions[valid]*distance[valid,None]
    return {'distance_m':distance, 'valid':valid, 'points_work_m':points,
            'object_id':identity, 'triangle_index':triangle, 'normal_work':normals}


def cast_model(path, object_ids, origins, directions, maximum):
    # Retain the original helper interface for existing scene scripts.
    meshes = load_meshes(path)
    missing = set(object_ids)-set(meshes)
    if missing:
        raise ContractError(f'Unknown object IDs: {sorted(missing)}')
    hits = cast_hits(meshes, object_ids, origins, directions, maximum)
    return hits['points_work_m'], hits['valid']


def metrics(points, scan, mask, component):
    if not mask.any():
        return None
    delta = points[mask]-scan[mask]
    error = abs(delta[:, 'xyz'.index(component)]) if component in ('x','y','z') else np.linalg.norm(delta, axis=1)
    return {'mean_abs_m':float(error.mean()), 'p90_m':float(np.percentile(error,90)),
            'p99_m':float(np.percentile(error,99)), 'max_m':float(error.max())}


def summarize(scan, scan_valid, old, new, component):
    common = scan_valid & old['valid'] & new['valid']
    paired_old, paired_new = scan_valid & old['valid'], scan_valid & new['valid']
    result = {'common_valid':int(common.sum()), 'scan_misses':int((~scan_valid).sum()),
              'before_misses':int((~old['valid']).sum()), 'after_misses':int((~new['valid']).sum()),
              'coverage_gained':int((scan_valid & ~old['valid'] & new['valid']).sum()),
              'coverage_lost':int((scan_valid & old['valid'] & ~new['valid']).sum()),
              'before':metrics(old['points_work_m'],scan,common,component),
              'after':metrics(new['points_work_m'],scan,common,component),
              'paired_before':{'count':int(paired_old.sum()), 'metrics':metrics(old['points_work_m'],scan,paired_old,component)},
              'paired_after':{'count':int(paired_new.sum()), 'metrics':metrics(new['points_work_m'],scan,paired_new,component)}}
    return result, common


def compare(startup_path, evidence_path, before_path, after_path, object_ids, component, output, object_prefixes=()):
    output = Path(output).resolve()
    arrays_path = output.with_name(output.stem+'.rays.npz')
    if output.exists() or arrays_path.exists():
        raise ContractError('Use a new output report and ray array path')
    if component not in ('x','y','z','distance'):
        raise ContractError('Unknown error component')
    startup, saved = read_json(startup_path), read_json(evidence_path)
    if not saved['passed'] or saved['query']['op'] not in ('pixel_rays','raycast') or saved['scene_id'] != startup['scene_id']:
        raise ContractError('Expected successful ray evidence from the same scene')
    if saved['coordinate_frame'] != 'work_z_up_metres':
        raise ContractError('Evidence must use work Z-up metres')
    if sha256(startup['evidence_config']) != saved['config_sha256']:
        raise ContractError('Evidence configuration hash changed')
    for source, expected in saved['inputs_sha256'].items():
        if sha256(source) != expected:
            raise ContractError(f'Evidence input hash changed: {source}')
    reader = Evidence(read_json(startup['evidence_config']), startup['evidence_roots'])
    protected = [reader.directory, *[Path(v).resolve() for v in startup['evidence_roots'].values()]]
    if any(output.is_relative_to(p) for p in protected):
        raise ContractError('Comparison output must be outside source roots and prepared data')
    repeated = reader.query(saved['query'], output.parent)
    rows, prior_rows = repeated['rays'], saved['data']['rays']
    if len(rows) != len(prior_rows) or repeated['max_distance_m'] != saved['data']['max_distance_m']:
        raise ContractError('Repeated scan ray domain changed')
    for row, prior in zip(rows, prior_rows):
        if row['hit'] != prior['hit']:
            raise ContractError('Repeated scan hit mask changed')
        for key in ('origin_work_m','direction_work','point_work_m','distance_m'):
            if row[key] is None or prior[key] is None:
                same = row[key] is None and prior[key] is None
            else:
                same = np.allclose(row[key], prior[key], rtol=0, atol=1e-7)
            if not same:
                raise ContractError(f'Repeated scan ray changed: {key}')
    origins = np.array([r['origin_work_m'] for r in rows])
    directions = np.array([r['direction_work'] for r in rows])
    scan_valid = np.array([r['hit'] for r in rows])
    scan = np.array([r['point_work_m'] if r['hit'] else [np.nan]*3 for r in rows])
    arrays = {'origins_work_m':origins, 'directions_work':directions, 'scan_valid':scan_valid,
              'scan_points_work_m':scan, 'scan_distance_m':np.array([r['distance_m'] if r['hit'] else np.inf for r in rows]),
              'scan_normal_work':np.array([r['normal_work'] if r['hit'] else [np.nan]*3 for r in rows]),
              'scan_triangle_index':np.array([r['triangle_index'] if r['hit'] else -1 for r in rows])}
    object_ids, object_prefixes = list(object_ids or []), list(object_prefixes or [])
    selected, hits = {}, {}
    for label, path in (('before',before_path),('after',after_path)):
        meshes = load_meshes(path, startup['scene_id'])
        missing = set(object_ids)-set(meshes)
        if missing:
            raise ContractError(f'{label}: unknown explicit object IDs: {sorted(missing)}; use prefixes for added/deleted parts')
        selected[label] = sorted(set(object_ids) | {name for name in meshes if any(name.startswith(p) for p in object_prefixes)})
        hits[label] = {'full_scene':cast_hits(meshes, meshes, origins, directions, repeated['max_distance_m'])}
        if object_ids or object_prefixes:
            hits[label]['target'] = cast_hits(meshes, selected[label], origins, directions, repeated['max_distance_m'])
        for domain, values in hits[label].items():
            arrays.update({f'{label}_{domain}_{key}':value for key,value in values.items()})
    if (object_ids or object_prefixes) and not any(selected.values()):
        raise ContractError('Target selectors match neither model')
    domains = {}
    for domain in hits['before']:
        domains[domain], arrays[domain+'_common_valid'] = summarize(scan,scan_valid,hits['before'][domain],hits['after'][domain],component)
    if 'target' in domains:
        for label in ('before','after'):
            full, target = hits[label]['full_scene'], hits[label]['target']
            mismatch = full['valid'] & target['valid'] & (full['object_id'] != target['object_id'])
            arrays[label+'_full_target_hit_differs'] = mismatch
            domains['target'][label+'_full_target_hit_differs'] = int(mismatch.sum())
    primary = domains.get('target', domains['full_scene'])
    anomalies = {domain: ray_diagnostics.summarize_anomalies(arrays, domain) for domain in domains}
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(arrays_path, **arrays)
    result = dict(primary, passed=True, geometry_quality_passed=None, scene_id=startup['scene_id'],
                  object_ids=object_ids, object_prefixes=object_prefixes, selected_object_ids=selected,
                  component=component, rays=len(rows), domains=domains, source_query_repeated=True,
                  anomaly_summary={'domains': anomalies, 'presentation_limit': 6, 'group_example_limit': 2,
                      'distance_metric': 'euclidean_metres',
                      'implementation_sha256': sha256(ray_diagnostics.__file__),
                      'interpretation': 'Review candidates, not automatic quality failures. Surface rows rank all scan/model hits without a tolerance; largest_increases use common valid hits only. Coverage categories include persistent misses. Identity changes can be renames. Groups use object-ID pairs with spatial bounds, not semantic or spatial segmentation. Counts use all rays; examples are bounded. Scan misses do not prove free space; source holes and occlusion require review.'},
                  query=saved['query'], evidence_sha256=sha256(evidence_path),
                  before_glb_sha256=sha256(before_path), after_glb_sha256=sha256(after_path),
                  implementation_sha256=sha256(__file__), ray_data=arrays_path.name, ray_data_sha256=sha256(arrays_path),
                  interpretation='passed means comparison executed, not geometry acceptance. Compare before/after on common masks; paired metrics use different coverage. Null metrics mean no comparable hits. Scan consistency is not independent physical accuracy.')
    atomic_json(output, result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('startup','evidence','before','after','report'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--object-id',action='append',default=[])
    p.add_argument('--object-prefix',action='append',default=[])
    p.add_argument('--component',choices=['x','y','z','distance'],default='distance')
    args = p.parse_args()
    try:
        result = compare(args.startup,args.evidence,args.before,args.after,args.object_id,args.component,args.report,args.object_prefix)
    except (ContractError,OSError,ValueError,KeyError) as error:
        p.exit(1, str(error)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='query'}))
