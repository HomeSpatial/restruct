# SPDX-License-Identifier: GPL-3.0-or-later
"""Real mesh/export regression for concave surface winding and signed extrusion."""
import argparse
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

parser=argparse.ArgumentParser()
parser.add_argument('--contract-src',required=True)
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(args.contract_src).resolve()))
from homespatial_model import api,parameter_scene as ps

out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
ring=[[0,0,1],[4,0,1],[4,1,1],[1,1,1],[1,4,1],[0,4,1]]
hole=[[.2,.2,1],[.7,.2,1],[.7,.7,1],[.2,.7,1]]
model={'schema_version':2,'kind':'model','scene_id':'concave-winding','coordinate_frame':'work_z_up_metres',
       'revision':1,'boundary_policy':'open','vertices':{},'materials':{},'objects':[]}
expected={}
for sign in (1,-1):
    for start in range(len(ring)):
        for offset in (-.06,.02):
            identity=f'surface/{sign}/{start}/{offset}'
            points=ring[::sign];points=points[start:]+points[:start]
            ids=[f'{identity}/v{i}' for i in range(len(points))]
            holes=[f'{identity}/h{i}' for i in range(len(hole))]
            model['vertices'].update(zip(ids,points));model['vertices'].update(zip(holes,hole))
            model['objects'].append({'id':identity,'category':'floor','revision':1,
                'geometry':{'type':'surface','boundary_ids':ids,'holes_ids':[holes],'offset_m':offset,'thickness_m':.06},
                'sources':[{'kind':'user','reference':{'root':'bundle','path':'fixture.json'}}]})
            expected[identity]=sorted([1+sign*offset,1+sign*(offset+.06)])
(out/'fixture.json').write_text(json.dumps(model),encoding='utf-8')

def call(op,**kwargs):
    result=api.dispatch({'op':op,'args':kwargs});assert result['ok'],result
    return result['data']

def inspect(objects):
    for identity,obj in objects.items():
        points=[obj.matrix_world@v.co for v in obj.data.vertices]
        actual=[min(p.z for p in points),max(p.z for p in points)]
        assert all(abs(a-b)<2e-6 for a,b in zip(actual,expected[identity])),(identity,actual)
        obj.data.calc_loop_triangles()
        triangles=[tuple(t.vertices) for t in obj.data.loop_triangles]
        tree=BVHTree.FromPolygons(points,triangles,all_triangles=True)
        for x,y,wanted in [(2,.5,True),(.5,2,True),(2,2,False),(.4,.4,False)]:
            hit=tree.ray_cast(Vector((x,y,2)),Vector((0,0,-1)),3)[0]
            assert (hit is not None)==wanted,(identity,x,y,hit)
            if wanted:assert abs(hit.z-expected[identity][1])<2e-6
        volume=sum(points[a].dot(points[b].cross(points[c])) for a,b,c in triangles)/6
        assert abs(volume-(7-.25)*.06)<2e-6,(identity,volume)

call('create_scene',model=model,roots={'bundle':str(out)})
inspect(ps.own_objects())
call('save_scene',filepath=str(out/'model.blend'))
call('export_scene',filepath=str(out/'model.glb'))
bpy.ops.wm.open_mainfile(filepath=str(out/'model.blend'))
call('rebuild_scene');inspect(ps.own_objects())
# Import the actual glTF file into an empty factory scene; importer restores Z-up.
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(out/'model.glb'))
objects={obj['hs_id']:obj for obj in bpy.context.scene.objects if obj.type=='MESH'}
assert set(objects)==set(expected)
inspect(objects)
(out/'validation.json').write_text(json.dumps({'passed':True,'surfaces':len(expected),
    'checks':['signed thickness and offset for both windings and every start',
              'concavity and hole rays','outward closed volume','save reopen rebuild','actual GLB reimport']}),encoding='utf-8')
print('SURFACE_WINDING_PASSED',len(expected),flush=True)
