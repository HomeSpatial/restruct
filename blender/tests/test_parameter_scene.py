# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender create/edit/save/reopen/rebuild/export checks for model v2."""
import argparse
import copy
import json
from pathlib import Path
import sys

import bpy
from mathutils import Matrix,Vector
from mathutils.bvhtree import BVHTree

parser=argparse.ArgumentParser()
parser.add_argument("--contract-src",required=True)
parser.add_argument("--mode",choices=["fixture"],required=True)
parser.add_argument("--model")
parser.add_argument("--root",action="append",default=[])
parser.add_argument("--out",required=True)
args=parser.parse_args(sys.argv[sys.argv.index("--")+1:])
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,args.contract_src)
from homespatial_model import api,parameter_scene as ps
from indoor_recon import scene_model as sm
out=Path(args.out).resolve()
if out.exists():raise ValueError("Use a fresh test output")
out.mkdir(parents=True)
checks={}


def write(name,value):
    (out/name).write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2),encoding="utf-8")


def check(name,condition,details=True):
    assert condition,(name,details)
    checks[name]=details


def call(op,**kwargs):
    result=api.dispatch({"op":op,"args":kwargs})
    assert result['ok'],(op,result)
    return result['data']


def snapshot():
    bpy.context.view_layer.update();result={}
    for identity,obj in ps.own_objects().items():
        if obj.type!='MESH':continue
        obj.data.calc_loop_triangles()
        result[identity]={'vertices':[list(p) for p in api._points(obj)],'faces':[list(t.vertices) for t in obj.data.loop_triangles],
            'parent_id':obj.get('hs_parent_id',''),'enclosure':bool(obj.get('hs_enclosure'))}
    return result


def fixture():
    m={'schema_version':2,'kind':'model','scene_id':'parameter-fixture','coordinate_frame':'work_z_up_metres','revision':1,'boundary_policy':'open','vertices':{},'materials':{},'objects':[]}
    source={'kind':'user','reference':{'root':'bundle','path':'fixture.json'},'note':'Analytical geometry fixture, not a scanned building'}
    colors={'color':[(.2,.5,.8,1),(.8,.2,.1,1),(.5,.8,.2,1),(.9,.9,.9,1)],'roughness':[(x,x,x,1) for x in (.25,.5,.75,1)],'normal':[(.5,.5,1,1)]*4}
    for name,pixels in colors.items():
        image=bpy.data.images.new('Fixture '+name,width=2,height=2)
        if name!='color':image.colorspace_settings.name='Non-Color'
        image.pixels[:]=[v for p in pixels for v in p];image.filepath_raw=str(out/(name+'.png'));image.file_format='PNG';image.save()
    m['materials']={'plain':{'base_color_srgb':[.6,.7,.8],'roughness':.7},'pbr':{'base_color_srgb':[1,1,1],'roughness':1,'base_color_texture':{'root':'bundle','path':'color.png'},'roughness_texture':{'root':'bundle','path':'roughness.png'},'normal_texture':{'root':'bundle','path':'normal.png'}},'glass':{'base_color_srgb':[.65,.85,.9],'roughness':.2,'alpha':.35}}
    m['vertices']={'a':[0,0,0],'b':[4,1,0],'c':[4,1,3],'d':[0,0,3],'e':[4,4,0],'f':[0,3,0],'g':[4,4,3],'h':[0,3,3],
        'd0':[1,.25,0],'d1':[2,.5,0],'d2':[2,.5,2.2],'d3':[1,.25,2.2],
        'w0':[2.8,.7,1],'w1':[3.6,.9,1],'w2':[3.6,.9,2.2],'w3':[2.8,.7,2.2],
        'hole0':[.5,1,0],'hole1':[1,1,0],'hole2':[1,1.6,0],'hole3':[.5,1.6,0]}
    for identity,category,boundary in [('wall','wall',['a','b','c','d']),('floor','floor',['a','f','e','b']),('ceiling','ceiling',['d','c','g','h'])]:
        g={'type':'surface','boundary_ids':boundary,'thickness_m':0}
        if identity=='floor':g['holes_ids']=[['hole0','hole1','hole2','hole3']]
        m['objects'].append({'id':identity,'category':category,'revision':1,'geometry':g,'material_id':'pbr' if identity=='floor' else 'plain','role':'enclosure','sources':[source]})
    for identity,category,ids,fill in [('door','door',['d0','d1','d2','d3'],'none'),('window','window',['w0','w1','w2','w3'],'panel')]:
        m['objects'].append({'id':identity,'category':category,'revision':1,'host_id':'wall','geometry':{'type':'opening','boundary_ids':ids,'fill':fill,'panel_thickness_m':.02},'material_id':'glass','sources':[source]})
    transform=Matrix.Rotation(.53,4,'Z');transform.translation=Vector((1.2,2.2,0))
    m['objects']+=sm.furniture('table','table',[1.1,.7,.7],[list(r) for r in transform],source,'plain')
    m['objects']+=sm.staircase('stairs',[{'id':'one','boundary_m':[[2,2],[3,2],[3,2.3],[2,2.3]],'bottom_m':0,'top_m':.12},
        {'id':'two','boundary_m':[[2,2.3],[3,2.3],[3,2.6],[2,2.6]],'bottom_m':.12,'top_m':.29},
        {'id':'turn','boundary_m':[[2,2.6],[3,2.6],[3,3],[2.5,3],[2.5,2.9],[2,2.9]],'bottom_m':.29,'top_m':.45}],sm.IDENTITY,source,'plain')
    sm.validate(m);write('fixture.json',m);return m


parameters=fixture() if args.mode=='fixture' else json.loads(Path(args.model).read_text(encoding='utf-8'))
roots={k:v for k,v in (s.split('=',1) for s in args.root)};roots.setdefault('bundle',str(out))
sentinel=bpy.data.objects.new('Unrelated sentinel',None);bpy.context.scene.collection.objects.link(sentinel);sentinel.location=(7,8,9)
result=call('create_scene',model=parameters,roots=roots)
check('create_count',result['objects']==len(parameters['objects']),result['objects'])
check('other_scene_preserved',tuple(sentinel.location)==(7,8,9))
initial_validation=call('validate_scene');check('initial_validation',initial_validation['passed'],initial_validation)
write('baseline-world.json',snapshot());call('save_scene',filepath=str(out/'baseline.blend'));call('export_scene',filepath=str(out/'baseline.glb'))

target='table/top'
before_model=ps.model();by={o['id']:o for o in before_model['objects']};before={k:ps.actual_digest(o) for k,o in ps.own_objects().items()};pointers={k:o.data.as_pointer() for k,o in ps.own_objects().items() if o.type=='MESH'}
size=by[target]['geometry']['size_m'].copy();size[0]+=.1
result=call('update_scene',objects=[{'id':target,'geometry':{'size_m':size}}],expected_revision=before_model['revision'])
check('local_dimensions_updated',next(o for o in ps.model()['objects'] if o['id']==target)['geometry']['size_m']==size)
check('single_component_scope',all(ps.actual_digest(o)==before[k] and (o.type!='MESH' or o.data.as_pointer()==pointers[k]) for k,o in ps.own_objects().items() if k!=target))
saved=copy.deepcopy(ps.model());digests={k:ps.actual_digest(o) for k,o in ps.own_objects().items()}
invalid=api.dispatch({'op':'update_scene','args':{'objects':[{'id':target,'geometry':{'size_m':[-1,1,1]}}],'expected_revision':saved['revision']}})
stale=api.dispatch({'op':'update_scene','args':{'objects':[{'id':target,'label':'stale'}],'expected_revision':1}})
check('invalid_and_stale_no_mutation',not invalid['ok'] and not stale['ok'] and ps.model()==saved and all(ps.actual_digest(o)==digests[k] for k,o in ps.own_objects().items()))

if args.mode == 'fixture':
    host='wall'
    wall=ps.own_objects()[host];points=api._points(wall);wall.data.calc_loop_triangles();tree=BVHTree.FromPolygons(points,[tuple(t.vertices) for t in wall.data.loop_triangles],all_triangles=True)
    ids=next(o for o in ps.model()['objects'] if o['id']=='door')['geometry']['boundary_ids'];corners=[Vector(ps.model()['vertices'][k]) for k in ids];center=sum(corners,Vector())/4;normal=(corners[1]-corners[0]).cross(corners[2]-corners[0]).normalized()
    hit=tree.ray_cast(center+normal,-normal,2)[0];check('host_has_actual_door_void',hit is None)
    next_nodes={ids[1]:list(corners[1]+(corners[1]-corners[0]).normalized()*.04),ids[2]:list(corners[2]+(corners[1]-corners[0]).normalized()*.04)}
    changed=call('update_scene',vertices=next_nodes,expected_revision=ps.model()['revision'])
    check('opening_updates_host',host in changed['changed_ids'] and 'door' in changed['changed_ids'])
    call('update_scene',objects=[{'id':'door','geometry':{'fill':'panel','angle_degrees':35}}],expected_revision=ps.model()['revision'])
    check('opening_fill_can_create_mesh',ps.own_objects()['door'].type=='MESH')
    call('update_scene',objects=[{'id':'door','geometry':{'fill':'none'}}],expected_revision=ps.model()['revision'])
    check('opening_fill_can_remove_mesh',ps.own_objects()['door'].type=='EMPTY')
    if args.mode == 'fixture':
        check('slanted_wall_preserved',all(abs(v.y-v.x*.25)<1e-6 for v in api._points(ps.own_objects()['wall'])))
        floor=ps.own_objects()['floor'];floor.data.calc_loop_triangles();tree=BVHTree.FromPolygons(api._points(floor),[tuple(t.vertices) for t in floor.data.loop_triangles],all_triangles=True)
        check('floor_hole_stays_open',tree.ray_cast(Vector((.75,1.3,1)),Vector((0,0,-1)),2)[0] is None)
        check('partial_boundary_not_auto_closed',call('validate_scene')['passed'] and call('validate_scene')['boundary_edges']>0)
    obj=ps.own_objects()[target];old_size=next(o for o in ps.model()['objects'] if o['id']==target)['geometry']['size_m'][0]
    obj.scale.x*=1.15
    refused=api.dispatch({'op':'rebuild_scene'});check('unrecorded_transform_not_overwritten',not refused['ok'] and obj.scale.x>1.1)
    call('capture_transforms',object_ids=[target],expected_revision=ps.model()['revision'])
    captured=next(o for o in ps.model()['objects'] if o['id']==target)['geometry']['size_m'][0]
    check('ui_scale_captured_in_local_parameters',abs(captured-old_size*1.15)<1e-6)
    if args.mode=='fixture':
        recorded=copy.deepcopy(ps.model());mat=ps.own_objects()[target].data.materials[0]
        shader=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED');alpha=shader.inputs['Alpha'].default_value;shader.inputs['Alpha'].default_value=.5
        rejected=api.dispatch({'op':'rebuild_scene'})
        check('manual_shader_edit_is_not_overwritten',not rejected['ok'] and shader.inputs['Alpha'].default_value==.5 and ps.model()==recorded)
        shader.inputs['Alpha'].default_value=alpha
        floor_mat=ps.own_objects()['floor'].data.materials[0];texture=next(n for n in floor_mat.node_tree.nodes if n.type=='TEX_IMAGE');image=texture.image;texture.image=None
        rejected=api.dispatch({'op':'export_scene','args':{'filepath':str(out/'must-not-export.glb')}})
        check('manual_texture_edit_refuses_export',not rejected['ok'] and not (out/'must-not-export.glb').exists());texture.image=image
        rejected=api.dispatch({'op':'update_scene','args':{'materials':{'plain':{'base_color_texture':{'root':'bundle','path':'missing.png'}}},'expected_revision':ps.model()['revision']}})
        check('asset_failure_does_not_commit_parameters',not rejected['ok'] and ps.model()==recorded)

current=copy.deepcopy(ps.model());before={k:ps.actual_digest(o) for k,o in ps.own_objects().items()}
call('rebuild_scene');check('rebuild_keeps_parameter_edits',ps.model()==current and all(ps.actual_digest(o)==before[k] for k,o in ps.own_objects().items()))
call('save_scene',filepath=str(out/'edited.blend'));call('export_scene',filepath=str(out/'edited.glb'));write('edited-world.json',snapshot())
bpy.ops.wm.open_mainfile(filepath=str(out/'edited.blend'))
check('saved_reopen_keeps_parameters',ps.model()==current)
call('rebuild_scene');check('saved_reopen_rebuild_keeps_geometry',all(ps.actual_digest(o)==before[k] for k,o in ps.own_objects().items()))
call('export_scene',filepath=str(out/'reopened.glb'))
# Packed materials and saved geometry allow export without access to source assets.
state=api._state();original_roots=state['roots'].copy();state['roots']={k:str(out/'unavailable'/k) for k in original_roots};bpy.context.scene['hs_project']=json.dumps(state)
call('export_scene',filepath=str(out/'offline.glb'));check('offline_export_from_saved_geometry',True)
state['roots']=original_roots;bpy.context.scene['hs_project']=json.dumps(state)
write('validation.json',{'passed':True,'mode':args.mode,'checks':checks,'objects':len(ps.own_objects()),'revision':ps.model()['revision'],'blender':bpy.app.version_string})
print('PARAMETER_SCENE_PASSED',args.mode,len(checks),flush=True)
