# SPDX-License-Identifier: GPL-3.0-or-later
"""Render from an existing parameter project without changing its model or saved file."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import homespatial_model
from homespatial_model import api, parameter_scene as ps

p = argparse.ArgumentParser()
p.add_argument('--blend', required=True)
p.add_argument('--out', required=True)
p.add_argument('--contract-src', required=True)
args = p.parse_args(sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0, args.contract_src)
out = Path(args.out).resolve()
if out.exists():
    raise ValueError('Use a new test directory')
out.mkdir(parents=True)
source = Path(args.blend).resolve()
before_file = hashlib.sha256(source.read_bytes()).hexdigest()
homespatial_model.register()
bpy.ops.wm.open_mainfile(filepath=str(source))
scene = bpy.context.scene
model = ps.model()
before = {k: ps.actual_digest(o) for k, o in ps.own_objects().items()}
pointers = {k: o.data.as_pointer() if o.data else None for k, o in ps.own_objects().items()}
scenes = {s.as_pointer() for s in bpy.data.scenes}
target = next(o['id'] for o in model['objects'] if o['geometry']['type'] == 'box')
result = api.dispatch({'op':'render_review','args':{'filepath':str(out/'review.png'), 'object_ids':[target],
                      'isolate':True, 'resolution':[320,240], 'samples':4}})
assert result['ok'], result
assert result['data']['visible_ids'] == [target]
assert bpy.context.scene == scene and ps.model() == model
assert before == {k: ps.actual_digest(o) for k, o in ps.own_objects().items()}
assert pointers == {k: o.data.as_pointer() if o.data else None for k, o in ps.own_objects().items()}
assert scenes == {s.as_pointer() for s in bpy.data.scenes}
assert hashlib.sha256(source.read_bytes()).hexdigest() == before_file
image = bpy.data.images.load(str(out/'review.png'), check_existing=False)
assert tuple(image.size) == (320,240)
pixels = list(image.pixels)
assert max(pixels)-min(pixels) > .1
bpy.data.images.remove(image)
denied = api.dispatch({'op':'render_review','args':{'filepath':str(out/'review.png')}})
assert not denied['ok']
row = next(o for o in model['objects'] if o['id'] == target)
size = row['geometry']['size_m'].copy()
size[0] *= 1.2
changed = api.dispatch({'op':'update_scene','args':{'expected_revision':model['revision'],
                       'objects':[{'id':target,'geometry':{'size_m':size}}]}})
assert changed['ok'], changed
comparison = api.dispatch({'op':'render_review','args':{'filepath':str(out/'after.png'),'object_ids':[target],
    'isolate':True,'resolution':[320,240],'samples':4,'bounds_work_m':result['data']['view']['bounds_work_m']}})
assert comparison['ok'], comparison
assert comparison['data']['camera'] == result['data']['camera']
report = {'passed':True,'checks':{'model_parameters_unchanged':True,'model_mesh_and_shader_digests_unchanged':True,
    'mesh_pointers_unchanged':True,'temporary_scene_removed':True,'input_blend_unchanged':True,
    'selected_scope_only':True,'nonblank_png':True,'existing_output_rejected':True,
    'fixed_bounds_keep_camera_when_geometry_changes':True},'render':result['data']}
(out/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('REVIEW_TEST_PASSED',len(report['checks']))
