# SPDX-License-Identifier: GPL-3.0-or-later
"""Temporary review renders, isolated from the saved project and its model data."""
import hashlib
import json
import math
from itertools import product
from pathlib import Path

import bpy
from mathutils import Matrix, Vector
from . import api


def render_review(filepath, object_ids=None, hide_ids=None, direction_work=(.6, -.9, 1.5),
                  bounds_work_m=None, resolution=(960, 720), samples=16, isolate=False, photo_camera=None):
    """Orthographic overview along direction_work, or with photo_camera {K, T_work_from_camera, size} the
    perspective of that photo (OpenCV pinhole: x right, y down, z forward; pixel centres at integers), so the render
    lines up with the photo pixel for pixel. resolution then scales the photo size and must keep its aspect."""
    api._require_object_mode()
    project_state = api._state()
    if project_state.get("schema_version") == 2:
        from . import parameter_scene as ps
        ps.require_clean()
        parameters = ps.model()
        owned = ps.own_objects()
    else:
        parameters = None
        owned = {o["hs_id"]: o for o in api._objects()}
    path = Path(filepath).resolve()
    if path.suffix.lower() != ".png" or path.exists() or path.with_suffix(".render.json").exists():
        raise ValueError("Review requires a new PNG and sidecar path")
    if len(resolution) != 2 or any(type(v) is not int or not 64 <= v <= 4096 for v in resolution):
        raise ValueError("Resolution must contain two integer dimensions in [64,4096]")
    if type(samples) is not int or not 1 <= samples <= 128:
        raise ValueError("Samples must be an integer in [1,128]")
    if photo_camera is not None:
        if not isinstance(photo_camera, dict) or set(photo_camera) != {"K", "T_work_from_camera", "size"}:
            raise ValueError("photo_camera takes K, T_work_from_camera and size")
        K, T, size = photo_camera["K"], photo_camera["T_work_from_camera"], photo_camera["size"]
        values = [v for row in K for v in row]+[v for row in T for v in row]+list(size)
        if (len(K) != 3 or any(len(r) != 3 for r in K) or len(T) != 4 or any(len(r) != 4 for r in T) or len(size) != 2
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values) or min(size) <= 0
                or K[0][0] <= 0 or K[1][1] <= 0):
            raise ValueError("photo_camera needs a 3x3 K, a 4x4 T_work_from_camera and a positive size")
        if abs(resolution[0]/resolution[1]-size[0]/size[1]) > .01:
            raise ValueError("Resolution must keep the photo aspect ratio")
    direction = Vector(direction_work)
    if len(direction) != 3 or not all(math.isfinite(v) for v in direction) or direction.length < 1e-8:
        raise ValueError("Camera direction must be a finite nonzero XYZ vector")
    selected = set(object_ids) if object_ids is not None else set(owned)
    hidden = set(hide_ids or [])
    if (selected | hidden)-set(owned):
        raise ValueError("Unknown review object ID")
    targets = [o for k, o in owned.items() if k in selected-hidden and o.type == "MESH"]
    points = [p for o in targets for p in api._points(o)]
    if not points:
        raise ValueError("Review selection has no mesh geometry")
    if bounds_work_m is None:
        lo = Vector([min(p[k] for p in points) for k in range(3)])
        hi = Vector([max(p[k] for p in points) for k in range(3)])
    else:
        if len(bounds_work_m) != 2 or any(len(p) != 3 or any(not math.isfinite(v) for v in p) for p in bounds_work_m):
            raise ValueError("Review bounds must contain finite min/max XYZ")
        lo, hi = map(Vector, bounds_work_m)
        if any(a >= b for a, b in zip(lo, hi)):
            raise ValueError("Review bounds must be increasing")
    center, extent = (lo+hi)/2, max((hi-lo).length, .01)
    prior = bpy.context.window.scene
    temporary = bpy.data.scenes.new("HS Temporary Review")
    copies, created = [], []
    try:
        bpy.context.window.scene = temporary
        visible = []
        for identity, obj in owned.items():
            if obj.type != "MESH" or identity in hidden or (isolate and identity not in selected):
                continue
            duplicate = obj.copy()
            duplicate.parent = None
            duplicate.matrix_world = obj.matrix_world.copy()
            duplicate.hide_render = False
            duplicate.hide_viewport = False
            temporary.collection.objects.link(duplicate)
            copies.append(duplicate)
            visible.append(identity)
        camera_data = bpy.data.cameras.new("Review camera")
        created.append((bpy.data.cameras, camera_data))
        camera = bpy.data.objects.new("Review camera", camera_data)
        temporary.collection.objects.link(camera)
        copies.append(camera)
        if photo_camera is None:
            camera.location = center+direction.normalized()*extent*2
            camera.rotation_euler = (center-camera.location).to_track_quat("-Z", "Y").to_euler()
            camera.data.type = "ORTHO"
            camera.data.clip_end = extent*8
            inverse_rotation = camera.rotation_euler.to_matrix().transposed()
            projected = [inverse_rotation@(Vector(p)-center) for p in product(*zip(lo, hi))]
            width = max(p.x for p in projected)-min(p.x for p in projected)
            height = max(p.y for p in projected)-min(p.y for p in projected)
            camera.data.ortho_scale = max(width, height*resolution[0]/resolution[1], .01)*1.15
        else:
            # OpenCV camera (y down, z forward) to Blender camera (y up, looking along -z).
            camera.matrix_world = Matrix(T)@Matrix.Diagonal((1, -1, -1, 1))
            w, h = size
            camera.data.type = "PERSP"
            camera.data.sensor_fit = "HORIZONTAL"
            camera.data.sensor_width = 36.
            camera.data.lens = K[0][0]*36/w
            camera.data.shift_x = ((w-1)/2-K[0][2])/w
            camera.data.shift_y = (K[1][2]-(h-1)/2)/w
            temporary.render.pixel_aspect_x, temporary.render.pixel_aspect_y = 1., K[0][0]/K[1][1]
            camera.data.clip_start, camera.data.clip_end = .02, 200.
        temporary.camera = camera
        light_data = bpy.data.lights.new("Review area", "AREA")
        created.append((bpy.data.lights, light_data))
        light = bpy.data.objects.new("Review area", light_data)
        temporary.collection.objects.link(light)
        copies.append(light)
        if photo_camera is None:
            light.location = center+Vector((.2, -.6, 1.5))*extent
            light.rotation_euler = (center-light.location).to_track_quat("-Z", "Y").to_euler()
            light.data.energy = 1000*max(1, extent/8)
            light.data.size = extent
        else:  # neutral fill from the camera; the room's own lighting is not reconstructed
            light.matrix_world = camera.matrix_world.copy()
            light.data.energy = 100.
            light.data.size = 1.5
        light.data.shape = "DISK"
        world = bpy.data.worlds.new("Review world")
        created.append((bpy.data.worlds, world))
        temporary.world = world
        world.use_nodes = True
        background = world.node_tree.nodes.get("Background")
        background.inputs["Color"].default_value = (.65, .72, .8, 1)
        background.inputs["Strength"].default_value = .7
        temporary.render.engine = "CYCLES"
        temporary.cycles.device = "CPU"
        temporary.cycles.samples = samples
        temporary.cycles.use_denoising = True
        temporary.render.resolution_x, temporary.render.resolution_y = resolution
        temporary.render.resolution_percentage = 100
        temporary.render.image_settings.file_format = "PNG"
        temporary.render.filepath = str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.render.render(write_still=True)
        result = {"image": str(path), "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "coordinate_frame": "work_z_up_metres", "scene_id": parameters["scene_id"] if parameters else project_state.get("scene_id"),
                  "model_revision": parameters["revision"] if parameters else None,
                  "model_sha256": hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest() if parameters else None,
                  "camera": {"location_work_m": list(camera.location), "rotation_euler": list(camera.rotation_euler),
                             "type": camera.data.type, "ortho_scale_m": camera.data.ortho_scale if photo_camera is None else None,
                             "photo_camera": photo_camera},
                  "view": {"object_ids": sorted(selected), "hide_ids": sorted(hidden), "isolate": isolate,
                           "direction_work": list(direction), "bounds_work_m": [list(lo), list(hi)],
                           "resolution": list(resolution), "samples": samples}, "visible_ids": sorted(visible)}
        path.with_suffix(".render.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    finally:
        bpy.context.window.scene = prior
        for obj in copies:
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.scenes.remove(temporary)
        for owner, data in created:
            if data.users == 0:
                owner.remove(data)
