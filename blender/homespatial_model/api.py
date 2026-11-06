# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import math
import struct
from collections import Counter
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree


class ModelError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _state():
    value = bpy.context.scene.get("hs_project")
    if not value:
        raise ModelError("NO_PROJECT", "Open a HomeSpatial project first")
    return json.loads(value)


def _objects():
    project = _state()["project_id"]
    return sorted((o for o in bpy.context.scene.objects
                   if o.type == "MESH" and o.get("hs_project_id") == project
                   and o.get("hs_role") == "model"), key=lambda o: o["hs_id"])


def _object(object_id):
    matches = [o for o in _objects() if o["hs_id"] == object_id]
    if len(matches) != 1:
        raise ModelError("OBJECT_ID", f"Expected one object with id {object_id!r}; found {len(matches)}")
    return matches[0]


def _points(obj):
    return [obj.matrix_world @ v.co for v in obj.data.vertices]


def _bounds(obj):
    points = _points(obj)
    return [[min(p[k] for p in points) for k in range(3)],
            [max(p[k] for p in points) for k in range(3)]]


def _journal(operation, arguments):
    scene = bpy.context.scene
    edits = json.loads(scene.get("hs_edits", "[]"))
    edits.append({"operation": operation, "arguments": arguments})
    scene["hs_edits"] = json.dumps(edits)


def _require_object_mode():
    if bpy.context.mode != "OBJECT":
        raise ModelError("MODE", "Switch to Object Mode before modifying the project")


def _import_meshes(path, collection, transform):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path), import_pack_images=True,
                              import_select_created_objects=True)
    created = set(bpy.data.objects) - before
    meshes = [o for o in created if o.type == "MESH"]
    worlds = {o: transform @ o.matrix_world for o in meshes}
    for obj in meshes:
        obj.parent = None
        obj.matrix_world = worlds[obj]
        for owner in list(obj.users_collection):
            owner.objects.unlink(obj)
        collection.objects.link(obj)
    for obj in created - set(meshes):
        bpy.data.objects.remove(obj, do_unlink=True)
    return meshes


def open_project(bundle_dir, reference_glb=None):
    _require_object_mode()
    root = Path(bundle_dir).resolve()
    required = [root / name for name in ("structure.json", "model-manifest.json", "structured.glb")]
    if not all(p.is_file() for p in required):
        raise ModelError("BUNDLE", "Bundle requires structure.json, model-manifest.json and structured.glb")
    if reference_glb and not Path(reference_glb).is_file():
        raise ModelError("REFERENCE", "Reference GLB does not exist")
    params, manifest = _read(required[0]), _read(required[1])
    if params.get("units") != "metres" or params.get("coordinates") != "ROS z-up, original origin":
        raise ModelError("COORDINATES", "V1 accepts metre-based ROS z-up bundles only")
    try:
        alignment = Matrix(params["aligned_frame_from_ros"])
        valid = (len(alignment) == 4 and all(math.isfinite(v) for row in alignment for v in row)
                 and abs(alignment.determinant()) > 1e-8)
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ModelError("ALIGNMENT", "Expected a finite invertible 4x4 alignment matrix")
    descriptors = {o["id"]: o for o in manifest["objects"]}
    if len(descriptors) != len(manifest["objects"]):
        raise ModelError("OBJECT_ID", "Manifest contains duplicate object ids")
    project_id = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    for scene in bpy.data.scenes:
        if scene.get("hs_project") and json.loads(scene["hs_project"])["project_id"] == project_id:
            bpy.context.window.scene = scene
            return {"project_id": project_id, "already_loaded": True, "objects": len(_objects())}
    source = {o["id"]: o for o in params["objects"]}
    frames_path = root / "evidence/frames.json"
    frames = _read(frames_path) if frames_path.is_file() else []
    for frame in frames:
        frame["local_image"] = str(root / "evidence/rgb" / f'{frame["id"]}.png')
    prior_scene = bpy.context.window.scene
    scene = bpy.data.scenes.new("HomeSpatial " + project_id[:8])
    bpy.context.window.scene = scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    model = bpy.data.collections.new("HS Model " + project_id[:8])
    scene.collection.children.link(model)
    gltf_to_blender = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    imported = []
    try:
        imported = _import_meshes(required[2], model, alignment @ gltf_to_blender.inverted())
        seen = set()
        for obj in imported:
            identity = obj.get("structure_id", obj.name)
            if identity not in descriptors or identity in seen:
                raise ModelError("OBJECT_ID", f"Unexpected or duplicate imported id: {identity}")
            seen.add(identity)
            descriptor = descriptors[identity]
            original = source.get(identity, {})
            obj["hs_id"] = identity
            obj["hs_project_id"] = project_id
            obj["hs_role"] = "model"
            obj["hs_category"] = descriptor["category"]
            obj["hs_structure_type"] = descriptor["type"]
            obj["hs_source_frames"] = json.dumps(original.get("source_frames", []))
            obj["hs_label"] = original.get("label", identity)
            obj["hs_source_descriptor"] = json.dumps(original or descriptor, ensure_ascii=False)
            obj["hs_initial_bounds"] = json.dumps(_bounds(obj))
        if seen != set(descriptors):
            raise ModelError("OBJECT_ID", "Imported objects do not match the manifest")
        if reference_glb:
            reference = bpy.data.collections.new("HS Reference " + project_id[:8])
            scene.collection.children.link(reference)
            references = _import_meshes(Path(reference_glb), reference, alignment @ gltf_to_blender.inverted())
            for index, obj in enumerate(references):
                obj["hs_id"] = f"reference_{index}"
                obj["hs_project_id"] = project_id
                obj["hs_role"] = "reference"
                obj.hide_select = True
                obj.hide_render = True
                obj.hide_set(True)
        state = {"schema_version": 1, "project_id": project_id, "bundle_dir": str(root),
                 "alignment": params["aligned_frame_from_ros"], "frames": frames,
                 "source_sha256": params.get("source_sha256"), "model_source": str(required[2]),
                 "reference_source": str(Path(reference_glb).resolve()) if reference_glb else None,
                 "working_coordinates": "aligned z-up metres", "export_coordinates": params["coordinates"]}
        scene["hs_project"] = json.dumps(state, ensure_ascii=False)
        scene["hs_edits"] = "[]"
        if hasattr(scene, "hs_settings"):
            scene.hs_settings.bundle_dir = str(root)
            scene.hs_settings.reference_glb = state["reference_source"] or ""
        bpy.context.view_layer.update()
        for obj in scene.objects:
            obj.select_set(False)
        imported[0].select_set(True)
        bpy.context.view_layer.objects.active = imported[0]
        return {"project_id": project_id, "already_loaded": False, "objects": len(imported),
                "frames": len(frames), "working_coordinates": state["working_coordinates"]}
    except Exception:
        for obj in list(scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.context.window.scene = prior_scene
        bpy.data.scenes.remove(scene)
        raise


def list_objects(category=None):
    if _state().get("schema_version") == 2:
        from .parameter_scene import own_objects
        return [{"id":k,"category":o["hs_category"],"label":o["hs_label"],"structure_type":o["hs_structure_type"]} for k,o in sorted(own_objects().items()) if category is None or o["hs_category"]==category]
    return [{"id": o["hs_id"], "label": o["hs_label"], "category": o["hs_category"],
             "structure_type": o["hs_structure_type"]} for o in _objects()
            if category is None or o["hs_category"] == category]


def inspect_object(object_id):
    if _state().get("schema_version") == 2:
        from .parameter_scene import inspect_parameter_object
        return inspect_parameter_object(object_id)
    obj = _object(object_id)
    bounds = _bounds(obj)
    obj.data.calc_loop_triangles()
    materials = []
    for material in obj.data.materials:
        if not material:
            materials.append(None)
            continue
        nodes = list(material.node_tree.nodes) if material.use_nodes else []
        bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
        materials.append({"name": material.name,
                          "base_color_linear": list(bsdf.inputs["Base Color"].default_value) if bsdf else None,
                          "roughness": bsdf.inputs["Roughness"].default_value if bsdf else None,
                          "metallic": bsdf.inputs["Metallic"].default_value if bsdf else None,
                          "textures": [{"name": n.image.name, "packed": bool(n.image.packed_file),
                                        "color_space": n.image.colorspace_settings.name, "size": list(n.image.size)}
                                       for n in nodes if n.type == "TEX_IMAGE" and n.image]})
    return {"id": object_id, "name": obj.name, "label": obj["hs_label"],
            "category": obj["hs_category"], "bounds_m": bounds,
            "dimensions_m": [bounds[1][k] - bounds[0][k] for k in range(3)],
            "triangles": len(obj.data.loop_triangles),
            "materials": materials, "uv_layers": [uv.name for uv in obj.data.uv_layers],
            "source_frames": json.loads(obj["hs_source_frames"])}


def photos(object_id, limit=6):
    if not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ModelError("LIMIT", "limit must be an integer between 1 and 50")
    obj = _object(object_id)
    state = _state()
    explicit = set(json.loads(obj["hs_source_frames"]))
    vertices, polygons = [], []
    for item in _objects():
        start = len(vertices)
        vertices.extend(_points(item))
        polygons.extend([tuple(start + i for i in p.vertices) for p in item.data.polygons])
    tree = BVHTree.FromPolygons(vertices, polygons)
    samples = [obj.matrix_world @ p.center for p in obj.data.polygons]
    stride = max(1, len(samples) // 32)
    samples = samples[::stride][:32] + _points(obj)[::max(1, len(obj.data.vertices) // 16)][:16]
    result = []
    for frame in state["frames"]:
        path = Path(frame["local_image"])
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            header = stream.read(24)
        width, height = struct.unpack(">II", header[16:24])
        camera = Matrix(state["alignment"]) @ Matrix(frame["pose"])
        inverse = camera.inverted()
        k = frame["K"]
        in_frame, visible = 0, 0
        for point in samples:
            q = inverse @ point
            if q.z <= 0:
                continue
            u, v = k[0][0] * q.x / q.z + k[0][2], k[1][1] * q.y / q.z + k[1][2]
            if not (0 <= u < width and 0 <= v < height):
                continue
            in_frame += 1
            direction = point - camera.translation
            length = direction.length
            hit = tree.ray_cast(camera.translation, direction.normalized(), length + .08)
            if hit[0] is not None and abs(hit[3] - length) < .08:
                visible += 1
        declared = frame["id"] in explicit
        if declared or visible:
            result.append({"frame_id": frame["id"], "path": str(path), "declared": declared,
                           "visible_samples": visible, "projected_samples": in_frame,
                           "sample_count": len(samples), "score": visible + (10 if declared else 0)})
    return sorted(result, key=lambda row: (-row["score"], row["frame_id"]))[:limit]


def open_photo(frame_id):
    frame = next((f for f in _state()["frames"] if f["id"] == frame_id), None)
    if frame is None or not Path(frame["local_image"]).is_file():
        raise ModelError("PHOTO", f"Unavailable frame: {frame_id}")
    image = bpy.data.images.load(frame["local_image"], check_existing=True)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.spaces.active.image = image
    return {"frame_id": frame_id, "path": frame["local_image"], "image": image.name,
            "size": list(image.size)}


def set_dimensions(object_id, dimensions_m, anchor="MIN"):
    if _state().get("schema_version") == 2:
        raise ModelError("PARAMETRIC_EDIT", "Use update_scene with geometry.size_m in local axes; world-box edits are the legacy mesh interface")
    _require_object_mode()
    obj = _object(object_id)
    if anchor not in ("MIN", "CENTER", "MAX") or len(dimensions_m) != 3:
        raise ModelError("DIMENSIONS", "Use three dimensions (null preserves an axis) and MIN/CENTER/MAX anchor")
    bounds = _bounds(obj)
    current = [bounds[1][k] - bounds[0][k] for k in range(3)]
    desired = [current[k] if v is None else v for k, v in enumerate(dimensions_m)]
    factors = []
    for k, value in enumerate(desired):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ModelError("DIMENSIONS", "Dimensions must be finite and positive")
        if dimensions_m[k] is not None and (value <= 0 or current[k] < 1e-7):
            raise ModelError("DIMENSIONS", "Cannot scale a zero-thickness axis or set zero thickness")
        factors.append(1 if dimensions_m[k] is None else value / current[k])
    fixed = Vector([(bounds[0][k] + bounds[1][k]) / 2 if anchor == "CENTER"
                    else bounds[0 if anchor == "MIN" else 1][k] for k in range(3)])
    transform = Matrix.Translation(fixed) @ Matrix.Diagonal((*factors, 1)) @ Matrix.Translation(-fixed)
    mesh = obj.data.copy()
    mesh.transform(obj.matrix_world.inverted() @ transform @ obj.matrix_world)
    mesh.update()
    obj.data = mesh
    bpy.context.view_layer.update()
    _journal("set_dimensions", dict(object_id=object_id, dimensions_m=dimensions_m, anchor=anchor))
    return {"before_m": current, "after": inspect_object(object_id), "anchor": anchor}


def _check_material(spec):
    if spec is None:
        return
    allowed = {"name", "base_color_srgb", "roughness", "metallic", "base_color_texture", "roughness_texture", "normal_texture"}
    if not isinstance(spec, dict) or set(spec) - allowed:
        raise ModelError("MATERIAL", "Unknown material fields")
    color = spec.get("base_color_srgb", [1, 1, 1])
    if len(color) != 3 or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in color):
        raise ModelError("MATERIAL", "base_color_srgb requires three values in [0, 1]")
    for key in ("roughness", "metallic"):
        value = spec.get(key, .6 if key == "roughness" else 0)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ModelError("MATERIAL", f"{key} must be in [0, 1]")
    for key in ("base_color_texture", "roughness_texture", "normal_texture"):
        if spec.get(key) and not Path(spec[key]).is_file():
            raise ModelError("MATERIAL", f"Texture does not exist: {spec[key]}")


def _material(spec, name):
    material = bpy.data.materials.new(spec.get("name", name))
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    color = spec.get("base_color_srgb", [1, 1, 1])
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in color]
    bsdf.inputs["Base Color"].default_value = (*linear, 1)
    bsdf.inputs["Roughness"].default_value = spec.get("roughness", .6)
    bsdf.inputs["Metallic"].default_value = spec.get("metallic", 0)
    for key in ("base_color_texture", "roughness_texture", "normal_texture"):
        if not spec.get(key):
            continue
        node = nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.load(str(Path(spec[key]).resolve()), check_existing=False)
        node.image.colorspace_settings.name = "sRGB" if key == "base_color_texture" else "Non-Color"
        node.image.pack()
        if key == "normal_texture":
            normal = nodes.new("ShaderNodeNormalMap")
            links.new(node.outputs["Color"], normal.inputs["Color"])
            links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
        else:
            links.new(node.outputs["Color"], bsdf.inputs["Base Color" if key == "base_color_texture" else "Roughness"])
    return material


def partition_material(object_id, axis, offset_m, negative=None, positive=None):
    if _state().get("schema_version") == 2:
        raise ModelError("PARAMETRIC_EDIT", "Edit scene materials or split parameter surfaces; mesh-only partitions cannot be rebuilt")
    _require_object_mode()
    target = _object(object_id)
    if axis not in ("X", "Y", "Z") or not isinstance(offset_m, (int, float)) or not math.isfinite(offset_m):
        raise ModelError("PLANE", "Use X/Y/Z and a finite aligned-coordinate offset_m")
    if negative is None and positive is None:
        raise ModelError("MATERIAL", "Provide at least one side's material")
    _check_material(negative)
    _check_material(positive)
    index = "XYZ".index(axis)
    bounds = _bounds(target)
    if not bounds[0][index] + 1e-6 < offset_m < bounds[1][index] - 1e-6:
        raise ModelError("PLANE", "Partition plane must cross the object's bounds")
    architecture = target["hs_structure_type"] == "enclosure_surface"
    staged = []
    normal = Vector(tuple(1 if k == index else 0 for k in range(3)))
    point = normal * offset_m
    try:
        target_mesh = target.data.copy()
        staged.append((target, target_mesh))
        bm = bmesh.new()
        try:
            bm.from_mesh(target_mesh)
            local_point = target.matrix_world.inverted() @ point
            local_normal = (target.matrix_world.to_3x3().transposed() @ normal).normalized()
            cut = bmesh.ops.bisect_plane(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces),
                                        plane_co=local_point, plane_no=local_normal, dist=1e-7)
            intersections = [target.matrix_world @ v.co for v in cut["geom_cut"] if isinstance(v, bmesh.types.BMVert)]
            bm.to_mesh(target_mesh)
            target_mesh.update()
        finally:
            bm.free()
        if architecture:
            for obj in _objects():
                if obj == target or obj["hs_structure_type"] != "enclosure_surface":
                    continue
                bm = bmesh.new()
                changed = False
                try:
                    bm.from_mesh(obj.data)
                    for intersection in intersections:
                        for edge in list(bm.edges):
                            a, b = [obj.matrix_world @ v.co for v in edge.verts]
                            direction = b - a
                            if direction.length_squared < 1e-14:
                                continue
                            fraction = (intersection - a).dot(direction) / direction.length_squared
                            if (1e-6 < fraction < 1 - 1e-6
                                    and (a + fraction * direction - intersection).length < 1e-6):
                                bmesh.utils.edge_split(edge, edge.verts[0], fraction)
                                changed = True
                    if changed:
                        mesh = obj.data.copy()
                        staged.append((obj, mesh))
                        bm.to_mesh(mesh)
                        mesh.update()
                finally:
                    bm.free()
        slots = {}
        for sign, spec in ((-1, negative), (1, positive)):
            if spec is not None:
                target_mesh.materials.append(_material(spec, object_id + ("_negative" if sign < 0 else "_positive")))
                slots[sign] = len(target_mesh.materials) - 1
        for polygon in target_mesh.polygons:
            sign = 1 if (target.matrix_world @ polygon.center)[index] >= offset_m else -1
            if sign in slots:
                polygon.material_index = slots[sign]
        for obj, mesh in staged:
            obj.data = mesh
    except Exception:
        for obj, mesh in staged:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise
    bpy.context.view_layer.update()
    arguments = dict(object_id=object_id, axis=axis, offset_m=offset_m, negative=negative, positive=positive)
    _journal("partition_material", arguments)
    return {"object_id": object_id, "plane": [axis, offset_m],
            "subdivided_objects": [o["hs_id"] for o, _ in staged],
            "geometry_positions_preserved": True, "conforming_enclosure_split": architecture,
            "material_slots": slots}


def validate():
    if _state().get("schema_version") == 2:
        from .parameter_scene import validate_scene
        return validate_scene()
    objects = _objects()
    errors, warnings = [], []
    ids = Counter(o["hs_id"] for o in objects)
    if any(count > 1 for count in ids.values()):
        errors.append("Duplicate stable object ids")
    if bpy.context.scene.unit_settings.scale_length != 1:
        errors.append("Scene unit scale must remain one metre")
    vertex_ids, edges = {}, Counter()
    triangle_count, architecture_count, degenerate = 0, 0, 0
    for obj in objects:
        points = _points(obj)
        if any(not math.isfinite(value) for p in points for value in p):
            errors.append("Non-finite coordinates: " + obj["hs_id"])
            continue
        obj.data.calc_loop_triangles()
        triangle_count += len(obj.data.loop_triangles)
        is_architecture = obj["hs_structure_type"] == "enclosure_surface"
        for triangle in obj.data.loop_triangles:
            a, b, c = [points[i] for i in triangle.vertices]
            if (b - a).cross(c - a).length < 1e-10:
                degenerate += 1
            if is_architecture:
                architecture_count += 1
                welded = []
                for point in (a, b, c):
                    key = tuple(round(v * 1e5) for v in point)
                    welded.append(vertex_ids.setdefault(key, len(vertex_ids)))
                for i, j in ((0, 1), (1, 2), (2, 0)):
                    edges[tuple(sorted((welded[i], welded[j])))] += 1
        for material in obj.data.materials:
            if material and material.use_nodes:
                for node in material.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image and len(node.image.pixels) == 0:
                        errors.append("Unavailable image: " + node.image.name)
    boundary = sum(count == 1 for count in edges.values())
    nonmanifold = sum(count > 2 for count in edges.values())
    if architecture_count and (boundary or nonmanifold):
        errors.append(f"Architectural shell has {boundary} boundary and {nonmanifold} nonmanifold edges")
    if degenerate:
        warnings.append(f"{degenerate} degenerate triangles")
    if not objects:
        errors.append("No model objects")
    return {"passed": not errors, "errors": errors, "warnings": warnings, "objects": len(objects),
            "triangles": triangle_count, "architecture_triangles": architecture_count,
            "boundary_edges": boundary, "nonmanifold_edges": nonmanifold,
            "weld_tolerance_m": 1e-5}


def _rewrite_glb(path, state):
    data = Path(path).read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + length])
    for node in document.get("nodes", []):
        identity = node.get("extras", {}).get("hs_id")
        if identity:
            node["name"] = identity
    document.setdefault("extras", {}).update(coordinates=state["export_coordinates"],
                                              aligned_frame_from_ros=state["alignment"],
                                              homespatial_project=state["project_id"])
    if "parameter_model" in state:
        document["extras"].pop("aligned_frame_from_ros",None)
        document["extras"].update(coordinate_frame="gltf_y_up_metres",T_work_from_gltf=state["T_work_from_gltf"],
                                  model_revision=state["parameter_model"]["revision"],parameter_model=state["parameter_model"])
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    remainder = data[20 + length:]
    Path(path).write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(payload) + len(remainder))
                           + struct.pack("<I4s", len(payload), b"JSON") + payload + remainder)
    return document


def export_glb(filepath, overwrite=False):
    if _state().get("schema_version") == 2:
        from .parameter_scene import export_scene
        return export_scene(filepath,overwrite)
    _require_object_mode()
    state = _state()
    path = Path(filepath).resolve()
    if path.suffix.lower() != ".glb":
        raise ModelError("OUTPUT", "Export path must end with .glb")
    if path == Path(state["model_source"]).resolve() or (state["reference_source"] and path == Path(state["reference_source"]).resolve()):
        raise ModelError("SOURCE_OVERWRITE", "Export to a new path; source models are read-only")
    if path.exists() and not overwrite:
        raise ModelError("OUTPUT_EXISTS", "Pass overwrite=true to replace this output")
    checks = validate()
    if not checks["passed"]:
        raise ModelError("VALIDATION", json.dumps(checks))
    objects = _objects()
    prior_scene = bpy.context.window.scene
    temporary = bpy.data.scenes.new("HS Export Temporary")
    copies, meshes = [], []
    temp_path = path.with_name(path.stem + ".partial.glb")
    try:
        inverse = Matrix(state["alignment"]).inverted()
        for obj in objects:
            duplicate = obj.copy()
            duplicate.data = obj.data.copy()
            meshes.append(duplicate.data)
            duplicate.parent = None
            duplicate.matrix_world = inverse @ obj.matrix_world
            temporary.collection.objects.link(duplicate)
            duplicate.hide_viewport = False
            duplicate.hide_render = False
            duplicate.hide_select = False
            copies.append(duplicate)
        bpy.context.window.scene = temporary
        for obj in copies:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = copies[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.export_scene.gltf(filepath=str(temp_path), export_format="GLB", use_selection=True, use_active_scene=True,
                                  export_yup=False, export_extras=True, export_animations=False,
                                  export_cameras=False, export_lights=False, export_apply=True,
                                  export_materials="EXPORT", export_image_format="AUTO")
        document = _rewrite_glb(temp_path, state)
        nodes = [node for node in document.get("nodes", []) if "mesh" in node]
        if len(nodes) != len(objects) or {n.get("extras", {}).get("hs_id") for n in nodes} != {o["hs_id"] for o in objects}:
            raise ModelError("EXPORT_SCOPE", "Exported objects differ from the project model")
        if any("uri" in image for image in document.get("images", [])):
            raise ModelError("EXTERNAL_TEXTURE", "Export unexpectedly references external textures")
        temp_path.replace(path)
    finally:
        bpy.context.window.scene = prior_scene
        for obj in copies:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        bpy.data.scenes.remove(temporary)
        if temp_path.exists():
            temp_path.unlink()
    report = {"path": str(path), "bytes": path.stat().st_size, "validation": checks,
              "coordinates": state["export_coordinates"], "embedded_images": len(document.get("images", [])),
              "objects": list_objects(), "edits": json.loads(prior_scene["hs_edits"])}
    path.with_suffix(".manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _save_project_data(filepath, overwrite=False):
    path = Path(filepath).resolve()
    if path.suffix.lower() != ".blend":
        raise ModelError("OUTPUT", "Project path must end with .blend")
    state = _state()
    if path.exists() and not overwrite:
        raise ModelError("OUTPUT_EXISTS", "Pass overwrite=true to replace this output")
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
    sidecar = dict(state, edits=json.loads(bpy.context.scene["hs_edits"]))
    path.with_suffix(".project.json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(path), "project_id": state["project_id"]}


def save_project(filepath, overwrite=False):
    if _state().get("schema_version") == 2:
        from .parameter_scene import save_scene
        return save_scene(filepath,overwrite)
    return _save_project_data(filepath,overwrite)


COMMANDS = {"open_project": open_project, "list_objects": list_objects, "inspect_object": inspect_object,
            "photos": photos, "open_photo": open_photo, "set_dimensions": set_dimensions,
            "partition_material": partition_material, "validate": validate,
            "export_glb": export_glb, "save_project": save_project}

from . import parameter_scene
COMMANDS.update({name:getattr(parameter_scene,name) for name in ("create_scene","update_scene","apply_scene","rebuild_scene","inspect_scene","capture_transforms","validate_scene","save_scene","export_scene")})
from .review import render_review
COMMANDS["render_review"] = render_review


def describe():
    import inspect
    return {"api_version": 2, "coordinates": "Legacy bundles retain source ROS export; parameter scenes use work Z-up metres and standard glTF Y-up export",
            "commands": {name: str(inspect.signature(function)) for name, function in COMMANDS.items()},
            "material": {"base_color_srgb": "3 floats [0,1]", "roughness": "float [0,1]",
                         "metallic": "float [0,1]", "base_color_texture": "optional local image path",
                         "roughness_texture": "optional grayscale image path", "normal_texture": "optional OpenGL tangent normal image path"},
            "partition_behavior": "Exact planar subdivision. Matching shared edges on architectural neighbors receive the cut vertices to retain a conforming shell; positions do not move.",
            "dimensions_behavior": "Legacy world-axis bounding boxes. Parameter scenes use update_scene geometry.size_m in local axes.",
            "parameter_model": "v2 shared work-coordinate nodes, hosted openings, parent-local components and explicit per-step prisms. Parameters are embedded in .blend and GLB extras; rebuild uses embedded edits.",
            "parameter_edits": "update_scene uses expected_revision; capture_transforms accepts rigid transforms and positive box scale. Direct mesh edits are refused before rebuild/export rather than overwritten."}


def dispatch(request):
    try:
        if not isinstance(request, dict) or set(request) - {"op", "args", "request_id"}:
            raise ModelError("REQUEST", "Expected {op, args, request_id?}")
        operation = request.get("op")
        if operation == "describe":
            data = describe()
        elif operation in COMMANDS:
            arguments = request.get("args", {})
            if not isinstance(arguments, dict):
                raise ModelError("ARGUMENTS", "args must be an object")
            import inspect
            inspect.signature(COMMANDS[operation]).bind(**arguments)
            data = COMMANDS[operation](**arguments)
        else:
            raise ModelError("OPERATION", f"Unknown operation: {operation}")
        return {"ok": True, "request_id": request.get("request_id"), "data": data}
    except (ModelError, TypeError, ValueError, KeyError) as error:
        return {"ok": False, "request_id": request.get("request_id") if isinstance(request, dict) else None,
                "error": {"code": getattr(error, "code", "ARGUMENTS"), "message": str(error)}}
