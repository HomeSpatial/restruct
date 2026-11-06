"""Independently check a scene task's files, evidence, edit scope and fresh Blender reopen."""
import argparse
import json
from pathlib import Path, PurePosixPath
import struct
import subprocess
import sys

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from indoor_recon.contracts import read_json, sha256, validate
from indoor_recon.jobs import atomic_json
from indoor_recon.material_checks import inspect_materials


def check_appearance_export(model_path, delivery, roots):
    scope = delivery.get('appearance_scope', 'required')
    if scope == 'geometry_only':
        reason = delivery.get('appearance_scope_reason')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('geometry_only needs the explicit task scope in appearance_scope_reason')
        return {'status': 'not_applicable', 'reason': reason}
    if scope != 'required':
        raise ValueError('appearance_scope must be required or geometry_only')
    report = inspect_materials(model_path, roots, delivery.get('material_exceptions'))
    if not report['passed']:
        failed = [r for r in report['primitives'] if r['errors']]
        raise ValueError('Material export check failed: ' + json.dumps(report['errors'] + failed[:8]))
    return report


def glb(path, expected):
    raw = path.read_bytes()
    length = struct.unpack_from('<I', raw, 12)[0]
    doc = json.loads(raw[20:20+length])
    assert doc['extras']['coordinate_frame'] == 'gltf_y_up_metres'
    assert doc['extras']['parameter_model'] == expected
    assert all('bufferView' in image and 'uri' not in image for image in doc.get('images', []))
    transform = np.array(doc['extras']['T_work_from_gltf'])
    np.testing.assert_array_equal(transform, [[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]])
    scene = trimesh.load(path, force='scene', process=False)
    meshes = {}
    for name in scene.graph.nodes_geometry:
        local, key = scene.graph[name]
        mesh = scene.geometry[key]
        meshes[name] = (trimesh.transform_points(mesh.vertices, transform@local), mesh.faces, mesh.visual.material)
    ids = {o['id'] for o in expected['objects'] if o['geometry']['type'] != 'assembly'
           and not (o['geometry']['type'] == 'opening' and o['geometry'].get('fill', 'none') == 'none')}
    assert set(meshes) == ids
    assert len([n for n in doc['nodes'] if 'mesh' in n]) == len(ids)
    return meshes


def material_equal(a, b):
    for key in ('baseColorFactor', 'metallicFactor', 'roughnessFactor', 'alphaMode', 'alphaCutoff', 'doubleSided'):
        av, bv = getattr(a, key, None), getattr(b, key, None)
        if isinstance(av, np.ndarray) or isinstance(bv, np.ndarray):
            assert np.array_equal(av, bv), key
        else:
            assert av == bv, key
    for key in ('baseColorTexture', 'metallicRoughnessTexture', 'normalTexture'):
        av, bv = getattr(a, key, None), getattr(b, key, None)
        assert (av is None) == (bv is None), key
        if av is not None:
            assert np.array_equal(np.asarray(av), np.asarray(bv)), key


def render_record(path):
    if path.suffix == '.png':
        report = path.parent/'render.json'
        if not report.exists():
            report = path.with_suffix('.render.json')
    else:
        report = path
    data = read_json(report)
    if 'data' in data:
        assert data['passed'] and data['input_blend_unchanged']
        data = data['data']
    image = Path(data['image'])
    assert image.is_file() and sha256(image) == data['image_sha256']
    return data


def check_render_pair(first, second):
    """Compare effective framing/settings; all-scene inventory is model content."""
    assert first['camera'] == second['camera'], 'Before/after camera differs'
    views = [dict(record['view']) for record in (first, second)]
    selected = [set(view.pop('object_ids')) for view in views]
    assert views[0] == views[1], 'Before/after view differs'
    if views[0]['isolate']:
        assert selected[0] == selected[1], 'Before/after isolated selection differs'


def check(startup_path, delivery_path, output):
    startup, delivery = read_json(startup_path), read_json(delivery_path)
    root = Path(startup['output_root']).resolve()
    if output.exists():
        raise ValueError('Use a new verification output directory')
    output.mkdir(parents=True)
    def path(key):
        relative = delivery[key]
        p = PurePosixPath(relative.replace('\\', '/'))
        assert not p.is_absolute() and '..' not in p.parts
        resolved = (root/relative).resolve()
        assert resolved.is_relative_to(root) and resolved.is_file(), (key, resolved)
        return resolved
    assert delivery['scene_id'] == startup['scene_id']
    baseline = validate(read_json(path('baseline_model')))
    edited = validate(read_json(path('edited_model')))
    # Edit tasks freeze their starting parameters in startup; init-task output has none, and the
    # delivery's own first accepted model (inside the task root) is the baseline.
    if 'model_path' in startup:
        assert baseline == read_json(startup['model_path']), 'Task baseline differs from frozen M2 parameters'
    assert baseline['scene_id'] == edited['scene_id'] == startup['scene_id']
    assert edited['revision'] > baseline['revision']
    old, new = ({o['id']: o for o in m['objects']} for m in (baseline, edited))
    changed = {k for k in set(old)|set(new) if old.get(k) != new.get(k)}
    declared = set(delivery['changed_object_ids'])
    assert changed <= declared and changed, (changed, declared)
    nodes = {k for k in set(baseline['vertices'])|set(edited['vertices'])
             if baseline['vertices'].get(k) != edited['vertices'].get(k)}
    affected = declared | {o['id'] for o in [*old.values(), *new.values()]
        if nodes.intersection(o['geometry'].get('boundary_ids', []) + sum(o['geometry'].get('holes_ids', []), []))}
    while True:
        expanded = affected | {o['id'] for o in [*old.values(), *new.values()]
                               if o.get('parent_id') in affected or o.get('host_id') in affected}
        if expanded == affected:
            break
        affected = expanded
    evidence_hashes = set()
    for value in delivery['evidence_reports']:
        relative = PurePosixPath(value.replace('\\', '/'))
        assert not relative.is_absolute() and '..' not in relative.parts
        report_path = root/value
        report = read_json(report_path)
        assert report['passed'] and report['scene_id'] == startup['scene_id']
        for source, expected in report['inputs_sha256'].items():
            assert sha256(source) == expected, source
        for name, expected in read_json(report_path.parent/'files.json').items():
            assert sha256(report_path.parent/name) == expected
        evidence_hashes.add(sha256(report_path))
    assert evidence_hashes
    sources = [s for identity in declared if identity in new for s in new[identity]['sources']]
    assert any(s['reference'].get('sha256') in evidence_hashes for s in sources), 'Edited object lacks pinned evidence provenance'
    before, after = glb(path('baseline_glb'), baseline), glb(path('edited_glb'), edited)
    appearance = check_appearance_export(path('edited_glb'), delivery, startup.get('model_roots', {}))
    magnitude = max([1.] + [float(abs(m[0]).max()) for m in [*before.values(), *after.values()]])
    budget = float(magnitude*8*np.finfo(np.float32).eps)
    unchanged = []
    geometric_changes = []
    worst = 0.
    for identity in set(before)&set(after):
        a, b = before[identity], after[identity]
        same_topology = a[0].shape == b[0].shape and np.array_equal(a[1], b[1])
        difference = float(np.linalg.norm(a[0]-b[0], axis=1).max()) if same_topology else None
        if identity not in affected:
            assert same_topology and difference <= budget, (identity, difference)
            material_equal(a[2], b[2])
            worst = max(worst, difference)
            unchanged.append(identity)
        elif difference is None or difference > budget:
            geometric_changes.append(identity)
    assert geometric_changes or set(before) != set(after), 'No geometric edit beyond numerical roundoff'
    first, second = render_record(path('render_before')), render_record(path('render_after'))
    assert first['scene_id'] == second['scene_id'] == startup['scene_id']
    assert first['model_revision'] == baseline['revision'] and second['model_revision'] == edited['revision']
    check_render_pair(first, second)
    for record, meshes in ((first, before), (second, after)):
        visible = set(meshes) - set(record['view']['hide_ids'])
        if record['view']['isolate']:
            visible &= set(record['view']['object_ids'])
        assert set(record['visible_ids']) == visible, 'Unexpected rendered object visibility'
    assert first['image_sha256'] != second['image_sha256'], 'No observable render change'
    assert path('report').stat().st_size > 0
    # A fresh Blender process reads the delivered .blend, rebuilds embedded parameters and exports again.
    runtime = read_json(startup['runtime'])['blender']
    source_blend = path('edited_blend')
    source_hash = sha256(source_blend)
    commands = [{'op':'validate_scene'}, {'op':'inspect_scene'}, {'op':'rebuild_scene'},
                {'op':'validate_scene'}, {'op':'inspect_scene'},
                {'op':'export_scene','args':{'filepath':str(output/'reopened.glb')}}]
    atomic_json(output/'request.json', commands)
    command = [runtime['executable'],'--background','--factory-startup','--python-exit-code','1',
               '--python',str(Path(runtime['addon_dir'])/'run_agent.py'),'--','--contract-src',startup['core_src'],
               '--blend',str(source_blend),'--request',str(output/'request.json'),'--response',str(output/'response.json')]
    with (output/'blender.log').open('w',encoding='utf-8') as log:
        result = subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
    assert result.returncode == 0
    responses = read_json(output/'response.json')
    assert len(responses) == 6 and all(r['ok'] for r in responses)
    assert responses[0]['data']['passed'] and responses[3]['data']['passed']
    assert responses[1]['data']['model'] == edited == responses[4]['data']['model']
    assert responses[2]['data']['changed_ids'] == []
    assert sha256(source_blend) == source_hash
    reopened = glb(output/'reopened.glb', edited)
    reopened_appearance = check_appearance_export(output/'reopened.glb', delivery, startup.get('model_roots', {}))
    reopen_worst = 0.
    for identity, a in after.items():
        b = reopened[identity]
        assert np.array_equal(a[1], b[1])
        difference = float(np.linalg.norm(a[0]-b[0], axis=1).max())
        assert difference <= budget, (identity, difference)
        material_equal(a[2], b[2])
        reopen_worst = max(reopen_worst, difference)
    report = {'passed':True,'scene_id':startup['scene_id'],'declared_changed_ids':sorted(declared),
              'geometric_changes':sorted(geometric_changes),'non_target_meshes_preserved':len(unchanged),
              'max_non_target_vertex_difference_m':worst,'max_reopen_vertex_difference_m':reopen_worst,
              'float32_roundtrip_budget_m':budget,'evidence_reports_verified':len(delivery['evidence_reports']),
              'fixed_camera_verified':True,'parameters_survive_fresh_rebuild':True,'input_blend_unchanged':True,
              'delivery_sha256':sha256(delivery_path),
              'appearance_export':appearance, 'reopened_appearance_export':reopened_appearance}
    atomic_json(output/'validation.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--startup',required=True,type=Path)
    parser.add_argument('--delivery',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.startup, args.delivery, args.out.resolve())))
