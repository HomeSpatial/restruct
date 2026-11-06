# SPDX-License-Identifier: Apache-2.0
"""Read model-v2 GLB exports back into work Z-up metres with their object metadata."""
import json
from pathlib import Path
import struct

import numpy as np

from .contracts import ContractError

WORK_FROM_GLTF = np.array([[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]])


def document(path):
    raw = Path(path).read_bytes()
    if raw[:4] != b"glTF":
        raise ContractError(f"Not a binary glTF file: {path}")
    return json.loads(raw[20:20+struct.unpack_from("<I", raw, 12)[0]])


def load_meshes(path, scene_id=None):
    """Object ID -> (float32 work vertices, uint32 faces) for a standard model-v2 export."""
    import trimesh
    extra = document(path).get("extras", {})
    if extra.get("coordinate_frame") != "gltf_y_up_metres" or not np.array_equal(extra.get("T_work_from_gltf"), WORK_FROM_GLTF):
        raise ContractError("Expected model-v2 GLB with standard work/glTF axis conversion")
    if scene_id is not None and extra["parameter_model"]["scene_id"] != scene_id:
        raise ContractError("GLB scene_id differs from evidence")
    scene = trimesh.load(path, force="scene", process=False)
    meshes = {}
    for name in scene.graph.nodes_geometry:
        local, key = scene.graph[name]
        mesh = scene.geometry[key]
        xyz = trimesh.transform_points(mesh.vertices, WORK_FROM_GLTF@local).astype(np.float32)
        meshes[name] = (xyz, np.asarray(mesh.faces, np.uint32))
    return meshes


def load_objects(path, scene_id=None):
    """Meshes plus hs_* node metadata and the embedded parameter model."""
    doc = document(path)
    meshes = load_meshes(path, scene_id)
    extras = {node.get("name"): node.get("extras", {}) for node in doc.get("nodes", [])}
    objects = {}
    for name, (xyz, faces) in meshes.items():
        e = extras.get(name, {})
        objects[name] = {"xyz": xyz, "faces": faces, "category": e.get("hs_category", ""),
                         "parent_id": e.get("hs_parent_id", ""), "host_id": e.get("hs_host_id", "")}
    return objects, doc["extras"].get("parameter_model")


def assembly_root(object_id, parents):
    """Topmost assembly ancestor, so parts of one piece of furniture share a root."""
    seen, current = set(), object_id
    while parents.get(current) and current not in seen:
        seen.add(current)
        current = parents[current]
    return current
