# SPDX-License-Identifier: GPL-3.0-or-later
"""Read a saved scene and render a temporary cutaway; never save scene changes."""
import argparse
from pathlib import Path
import sys
import bpy
from mathutils import Vector

p=argparse.ArgumentParser();p.add_argument('--blend',required=True);p.add_argument('--output',required=True);p.add_argument('--detail',action='store_true');args=p.parse_args(sys.argv[sys.argv.index('--')+1:])
bpy.ops.wm.open_mainfile(filepath=str(Path(args.blend).resolve()))
scene=bpy.context.scene;objects=[o for o in scene.objects if o.type=='MESH' and o.get('hs_role')=='model']
for obj in objects:
    if obj.get('hs_category')=='ceiling' or obj.get('hs_id')=='wall-south':obj.hide_render=True
    if args.detail and obj.get('hs_category') not in ('stairs','railing'):obj.hide_render=True
visible=[o for o in objects if not o.hide_render];points=[o.matrix_world@v.co for o in visible for v in o.data.vertices]
lo=Vector([min(p[k] for p in points) for k in range(3)]);hi=Vector([max(p[k] for p in points) for k in range(3)]);center=(lo+hi)/2;extent=(hi-lo).length
camera=bpy.data.objects.new('Review camera',bpy.data.cameras.new('Review camera'));scene.collection.objects.link(camera)
camera.location=center+Vector((.6,-.9,1.5))*extent;camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.type='ORTHO';camera.data.ortho_scale=max(hi.x-lo.x,hi.y-lo.y,hi.z-lo.z)*1.25;scene.camera=camera
light=bpy.data.objects.new('Review area',bpy.data.lights.new('Review area','AREA'));scene.collection.objects.link(light);light.location=center+Vector((0,-1,2))*extent;light.data.energy=1500;light.data.shape='DISK';light.data.size=extent*1.5;light.rotation_euler=(center-light.location).to_track_quat('-Z','Y').to_euler()
if scene.world is None:scene.world=bpy.data.worlds.new('Review world')
scene.world.use_nodes=True;background=scene.world.node_tree.nodes.get('Background');background.inputs['Color'].default_value=(.65,.72,.8,1);background.inputs['Strength'].default_value=.7
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=16;scene.cycles.use_denoising=True
scene.render.resolution_x=960;scene.render.resolution_y=720;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.filepath=str(Path(args.output).resolve());Path(scene.render.filepath).parent.mkdir(parents=True,exist_ok=True)
bpy.ops.render.render(write_still=True)
print('REVIEW_RENDER',scene.render.filepath)
