# SPDX-License-Identifier: GPL-3.0-or-later
"""Load the packaged add-on without the core source on Blender's Python path."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile
import bpy

p=argparse.ArgumentParser();p.add_argument('--zip',required=True);p.add_argument('--blend',required=True);p.add_argument('--out',required=True);p.add_argument('--exclude',action='append',default=[])
args=p.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(args.out).resolve()
if out.exists():raise ValueError('Use a fresh installation test directory')
out.mkdir(parents=True);install=out/'installed'
with zipfile.ZipFile(args.zip) as archive:archive.extractall(install)
excluded=[Path(x).resolve() for x in args.exclude]
sys.path[:]=[s for s in sys.path if not any(Path(s).resolve()==x or Path(s).resolve().is_relative_to(x) for x in excluded)]
sys.path.insert(0,str(install))
import homespatial_model
from homespatial_model import api,parameter_scene as ps
assert Path(homespatial_model.__file__).is_relative_to(install)
assert ps.contract().__name__=='homespatial_model._vendor.scene_model'
homespatial_model.register()
source=Path(args.blend).resolve();source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
bpy.ops.wm.open_mainfile(filepath=str(source))
for obj in bpy.context.selected_objects:obj.select_set(False)
target=next(o for o in ps.own_objects().values() if o.type=='MESH' and next(r for r in ps.model()['objects'] if r['id']==o['hs_id'])['geometry']['type']=='box')
target.select_set(True);bpy.context.view_layer.objects.active=target;revision=ps.model()['revision'];target.location.x+=.02
assert bpy.ops.homespatial.action(action='capture')=={'FINISHED'}
assert ps.model()['revision']==revision+1
assert bpy.ops.homespatial.action(action='rebuild')=={'FINISHED'}
assert api.dispatch({'op':'save_scene','args':{'filepath':str(out/'installed.blend')}})['ok']
assert api.dispatch({'op':'export_glb','args':{'filepath':str(out/'installed.glb')}})['ok']
assert source_hash==hashlib.sha256(source.read_bytes()).hexdigest()
result={'passed':True,'contract_module':ps.contract().__name__,'addon_module':homespatial_model.__file__,'ui_capture_and_rebuild':True,'saved_and_exported':True,'input_blend_unchanged':True,'blender':bpy.app.version_string,'revision':ps.model()['revision']}
(out/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print('INSTALLED_ADDON_PASSED',json.dumps(result))
