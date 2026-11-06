# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent trimesh/NumPy verification of Blender parameter scene exports."""
import argparse
from collections import Counter
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image
from scipy.spatial import cKDTree
import trimesh

parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--legacy',type=Path)
args=parser.parse_args();root=args.output


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def glb_doc(path):
    data=Path(path).read_bytes();length=struct.unpack_from('<I',data,12)[0]
    return json.loads(data[20:20+length])


def work_meshes(path):
    scene=trimesh.load(path,force='scene');doc=glb_doc(path);c=np.array(doc['extras']['T_work_from_gltf'])
    meshes={}
    for name in scene.graph.nodes_geometry:
        transform,key=scene.graph[name];mesh=scene.geometry[key].copy();mesh.apply_transform(c@transform);meshes[name]=mesh
    return meshes,doc


report={'passed':True,'exports':{}}
for name,snapshot in [('baseline','baseline-world.json'),('edited','edited-world.json'),('reopened','edited-world.json'),('offline','edited-world.json')]:
    expected=read(root/snapshot);meshes,doc=work_meshes(root/(name+'.glb'))
    assert set(meshes)==set(expected),(name,set(meshes)^set(expected))
    maximum=0
    for identity,mesh in meshes.items():
        source=np.array(expected[identity]['vertices']);target=mesh.vertices
        delta=max(cKDTree(source).query(target)[0].max(),cKDTree(target).query(source)[0].max());maximum=max(maximum,float(delta))
    magnitude=max(1,max(abs(v) for m in expected.values() for p in m['vertices'] for v in p));budget=float(8*np.finfo(np.float32).eps*magnitude)
    assert maximum<=budget,(name,maximum,budget)
    assert doc['extras']['coordinate_frame']=='gltf_y_up_metres'
    assert doc['extras']['parameter_model']['kind']=='model' and doc['extras']['parameter_model']['schema_version']==2
    assert all('uri' not in image and 'bufferView' in image for image in doc.get('images',[]))
    report['exports'][name]={'meshes':len(meshes),'max_roundtrip_m':maximum,'float32_arithmetic_budget_m':budget,'images':len(doc.get('images',[]))}

if args.legacy:
    scene=trimesh.load(args.legacy/'structured.glb',force='scene');parameters=read(args.legacy/'structure.json');alignment=np.array(parameters['aligned_frame_from_ros']);original={}
    for identity in scene.graph.nodes_geometry:
        transform,key=scene.graph[identity];mesh=scene.geometry[key].copy();mesh.apply_transform(alignment@transform);original[identity]=mesh
    new,doc=work_meshes(root/'baseline.glb');extras={n['name']:n.get('extras',{}) for n in doc['nodes']};groups={k:[] for k in original}
    for identity,mesh in new.items():
        source_id=identity if identity in original else extras[identity]['hs_parent_id']
        assert source_id in groups,source_id
        groups[source_id].append(mesh)
    worst=0;rows=[];retriangulated=[]
    budget=float(8*np.finfo(np.float32).eps*max(1,max(abs(v) for m in original.values() for p in m.vertices for v in p)))
    for identity,old in original.items():
        assert groups[identity],identity
        points=np.concatenate([m.vertices for m in groups[identity]])
        distance=max(cKDTree(old.vertices).query(points)[0].max(),cKDTree(points).query(old.vertices)[0].max())
        surface_distance=float(distance)
        if distance>budget:
            new_mesh=trimesh.util.concatenate(groups[identity])
            distances=[]
            for target,samples in [(old,new_mesh),(new_mesh,old)]:
                points=np.concatenate([samples.vertices,samples.triangles_center])
                # These are small planar patches. Float64 triangle queries avoid
                # amplified float32 error on long, very thin source triangles.
                distances.extend(trimesh.proximity.closest_point_naive(target,points)[1].tolist())
            surface_distance=max(distances)
            retriangulated.append({'id':identity,'max_point_to_surface_m':surface_distance,'vertex_set_distance_m':float(distance),'query_precision':'float64'})
        worst=max(worst,surface_distance);rows.append({'id':identity,'surface_sample_distance_m':surface_distance})
    assert worst<=budget,sorted(rows,key=lambda x:x['surface_sample_distance_m'])[-5:]
    for identity in ('floor_lower_0','floor_upper_0'):
        old=original[identity].visual.material;new_material=new[identity].visual.material
        for field in ('baseColorTexture','metallicRoughnessTexture','normalTexture'):
            a=np.asarray(getattr(old,field));b=np.asarray(getattr(new_material,field));assert a.shape[:2]==b.shape[:2] and np.array_equal(a[:,:,:3],b[:,:,:3]),(identity,field)
    shell=[mesh for identity,mesh in new.items() if extras[identity].get('hs_enclosure')]
    shell=trimesh.util.concatenate(shell);points=shell.vertices;parents=np.arange(len(points))
    def representative(i):
        while parents[i]!=i:parents[i]=parents[parents[i]];i=parents[i]
        return i
    tolerance=max(1,np.abs(points).max())*2**-22
    for i,j in cKDTree(points).query_pairs(tolerance):parents[representative(i)]=representative(j)
    ix=np.array([representative(i) for i in range(len(points))]);faces=ix[shell.faces];edges=np.sort(np.concatenate((faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]])),axis=1);_,counts=np.unique(edges,axis=0,return_counts=True)
    assert np.all(counts==2),Counter(counts)
    report['legacy_comparison']={'objects_grouped':len(groups),'max_surface_sample_distance_m':worst,'budget_m':budget,'retriangulated_objects':retriangulated,'floor_pbr_pixels_identical':True,'shell_boundary_edges':int(sum(counts==1)),'shell_nonmanifold_edges':int(sum(counts>2)),'numerical_weld_m':float(tolerance)}
else:
    fixture=read(root/'baseline.scene.json')
    if fixture['scene_id']=='parameter-fixture':
        material=work_meshes(root/'baseline.glb')[0]['floor'].visual.material
        for field,file in [('baseColorTexture','color.png'),('normalTexture','normal.png')]:
            a=np.asarray(getattr(material,field))[:,:,:3];b=np.asarray(Image.open(root/file).convert('RGB'));assert np.array_equal(a,b),field
        actual=np.asarray(material.metallicRoughnessTexture)[:,:,1];expected=np.asarray(Image.open(root/'roughness.png').convert('RGB'))[:,:,0]
        assert np.array_equal(actual,expected),'roughness G channel'
        report['pbr_input_images_preserved']=True
(root/'roundtrip-validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
