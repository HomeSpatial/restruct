# SPDX-License-Identifier: Apache-2.0
"""Versioned JSON contracts plus numeric checks JSON Schema cannot express."""
from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path, PurePosixPath, PureWindowsPath

import numpy as np
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


class ContractError(ValueError):
    pass


def read_json(path):
    def invalid(value):
        raise ContractError(f"Non-finite JSON constant: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=invalid)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def rotation(value):
    r = np.asarray(value, dtype=float)
    if r.shape != (3, 3) or not np.isfinite(r).all():
        raise ContractError("Rotation must be a finite 3x3 matrix")
    if not np.allclose(r.T @ r, np.eye(3), atol=1e-5, rtol=0) or not np.isclose(np.linalg.det(r), 1, atol=1e-5, rtol=0):
        raise ContractError("Rotation must be orthonormal and right-handed; encode unit conversion separately")
    return r


def rigid(value):
    t = np.asarray(value, dtype=float)
    if t.shape != (4, 4) or not np.isfinite(t).all() or not np.allclose(t[3], [0, 0, 0, 1], atol=1e-9, rtol=0):
        raise ContractError("Transform must be a finite homogeneous 4x4 matrix")
    rotation(t[:3, :3])
    return t


def camera_matrix(value):
    k = np.asarray(value, dtype=float)
    if k.shape != (3, 3) or not np.isfinite(k).all() or not np.allclose(k[2], [0, 0, 1], atol=1e-9, rtol=0) or k[0, 0] <= 0 or k[1, 1] <= 0:
        raise ContractError("K must be finite, invertible, with positive focal lengths and final row [0,0,1]")
    if not np.isclose(k[1, 0], 0, atol=1e-9, rtol=0):
        raise ContractError("K must use the declared upper-triangular pinhole convention")
    return k


def transform_points(points, matrix):
    t = rigid(matrix)
    return np.asarray(points, dtype=float) @ t[:3, :3].T + t[:3, 3]


def work_camera(source_pose, alignment, metres_per_unit=1.0):
    if not np.isfinite(metres_per_unit) or metres_per_unit <= 0:
        raise ContractError("metres_per_unit must be positive")
    pose = rigid(source_pose).copy()
    pose[:3, 3] *= metres_per_unit
    return rigid(alignment) @ pose


def project(points_work, K, T_work_from_camera):
    pose = rigid(T_work_from_camera)
    camera = (np.asarray(points_work, dtype=float) - pose[:3, 3]) @ pose[:3, :3]
    if not np.isfinite(camera).all() or np.any(camera[:, 2] <= 0):
        raise ContractError("Projection points must be finite and in front of the camera")
    pixels = camera @ camera_matrix(K).T
    return pixels[:, :2] / pixels[:, 2, None], camera[:, 2]


def unproject(pixels, depth_z_m, K, T_work_from_camera):
    uv = np.asarray(pixels, dtype=float)
    z = np.asarray(depth_z_m, dtype=float)
    if uv.ndim != 2 or uv.shape[1] != 2 or z.shape != (len(uv),) or not np.isfinite(uv).all() or not np.isfinite(z).all() or np.any(z <= 0):
        raise ContractError("Unprojection requires finite pixels and positive camera-Z depths")
    rays = np.column_stack((uv, np.ones(len(uv)))) @ np.linalg.inv(camera_matrix(K)).T
    return transform_points(rays * z[:, None], T_work_from_camera)


def resolve_asset(asset, roots):
    relative = asset["path"]
    p = PurePosixPath(relative)
    if "\\" in relative or p.is_absolute() or PureWindowsPath(relative).drive or ".." in p.parts or relative in ("", "."):
        raise ContractError(f"Asset path must be a portable relative path: {relative}")
    if asset["root"] not in roots:
        raise ContractError(f"Missing asset-root binding: {asset['root']}")
    base = Path(roots[asset["root"]]).resolve()
    # Model caches may use deliberate weight-directory junctions. Bindings authorize
    # their logical path; '..' and absolute paths remain invalid contract data.
    return base.joinpath(*p.parts)


def _unique(items, label):
    ids = [x["id"] for x in items]
    if len(ids) != len(set(ids)):
        raise ContractError(f"Duplicate {label} IDs")


def validate(document):
    if isinstance(document, dict) and document.get("kind") == "model" and document.get("schema_version") == 2:
        from .scene_model import validate as validate_parameters
        try:
            schema=json.loads(files("indoor_recon").joinpath("schemas/model-v2.schema.json").read_text(encoding="utf-8"))
            errors=list(Draft202012Validator(schema).iter_errors(document))
            if errors:raise ContractError(errors[0].message)
            return validate_parameters(document)
        except (ValueError, KeyError, TypeError) as error:
            raise ContractError(str(error)) from error
    schema = json.loads(files("indoor_recon").joinpath("schemas/contracts.schema.json").read_text(encoding="utf-8"))
    kind = document.get("kind") if isinstance(document, dict) else None
    if kind not in ("scene", "frames", "alignment", "proposals", "model", "asset_lock"):
        raise ContractError(f"Unknown contract kind: {kind}")
    registry = Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    validator = Draft202012Validator({"$ref": schema["$id"] + "#/$defs/" + kind}, registry=registry)
    errors = list(validator.iter_errors(document))
    if errors:
        first = errors[0]
        raise ContractError(f"{'/'.join(map(str, first.absolute_path))}: {first.message}")

    def walk(value):
        if isinstance(value, float) and not np.isfinite(value):
            raise ContractError("All numeric contract values must be finite")
        if isinstance(value, dict):
            if "root" in value and "path" in value:
                resolve_asset(value, {value["root"]: Path.cwd()})
            for key, item in value.items():
                if key in ("T_work_from_source_m", "T_source_from_camera", "T_work_from_object"):
                    rigid(item)
                elif key == "R_work_from_object":
                    rotation(item)
                elif key == "K":
                    camera_matrix(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(document)
    if kind in ("scene", "frames", "proposals", "model"):
        key = {"scene": "geometry", "frames": "frames", "proposals": "candidates", "model": "objects"}[kind]
        _unique(document[key], key)
    if kind == "frames":
        for frame in document["frames"]:
            if "camera" in frame and "image" not in frame and "depth" not in frame:
                raise ContractError("A calibrated frame must contain an image or depth")
    if kind == "proposals":
        for candidate in document["candidates"]:
            g = candidate["geometry"]
            positive = int(np.count_nonzero(np.asarray(g["size_m"]) > 0))
            if positive != (3 if g["nature"] == "volume" else 2):
                raise ContractError("Volume boxes need three positive dimensions; surface boxes need exactly two")
    if kind == "model":
        by_id = {o["id"]: o for o in document["objects"]}
        edges = {}
        for ob in by_id.values():
            refs = [ob[k] for k in ("parent_id", "host_id") if k in ob]
            if any(ref not in by_id or ref == ob["id"] for ref in refs):
                raise ContractError("Model relationships must reference another existing object")
            children = ob["geometry"].get("children", [])
            if any(ref not in by_id or ref == ob["id"] for ref in children):
                raise ContractError("Assembly children must reference another existing object")
            # host relations describe support, not ownership. Ownership is checked
            # in the parent -> child direction for both representations.
            edges[ob["id"]] = list(children)
        for ob in by_id.values():
            if "parent_id" in ob:
                edges[ob["parent_id"]].append(ob["id"])
        def visit(key, stack, done):
            if key in stack:
                raise ContractError("Cyclic model ownership")
            if key not in done:
                for child in edges[key]:
                    visit(child, stack | {key}, done)
                done.add(key)
        done = set()
        for key in edges:
            visit(key, set(), done)
    return document


def stage_eligibility(scene, frames=None):
    """Eligibility is an input assessment, not a claim that a backend was tested."""
    validate(scene)
    if frames is not None:
        validate(frames)
        if frames["scene_id"] != scene["scene_id"]:
            raise ContractError("Scene and frame manifest scene_id differ")
    rows = [] if frames is None else frames["frames"]
    scale = scene["source_coordinates"]["metres_per_unit"]
    working_geometry = any(g["coordinate_frame"] == "work" for g in scene["geometry"])
    metric_geometry = working_geometry or (bool(scene["geometry"]) and scale is not None)
    rgbd = any("camera" in f and "depth" in f for f in rows)
    calibrated_rgb = any("camera" in f and "image" in f for f in rows)
    color = any(g["has_color"] for g in scene["geometry"]) or any("image" in f and "camera" in f and "depth" in f for f in rows)
    available = metric_geometry or (rgbd and scale is not None)
    return {
        "geometry": "eligible" if available else "needs_input",
        "photos": "eligible" if any("image" in f for f in rows) else "skipped",
        "spatiallm_input": "eligible" if available and color and ("alignment" in scene or working_geometry) else "needs_input",
        "boxer_input": "eligible" if available and calibrated_rgb and scale is not None and "alignment" in scene else "needs_input",
    }


def validate_scene_bundle(scene, roots):
    """Validate references and the known source up axis, without running a model."""
    validate(scene)
    frames = None
    for field in ("frames", "alignment"):
        if field not in scene:
            continue
        value = validate(read_json(resolve_asset(scene[field], roots)))
        if value["kind"] != field or value["scene_id"] != scene["scene_id"]:
            raise ContractError(f"Scene {field} reference has a different kind or scene_id")
        if field == "frames":
            frames = value
        else:
            up = scene["source_coordinates"]["up_axis"]
            if up != "unknown":
                vector = np.eye(3)["XYZ".index(up[1])] * (1 if up[0] == "+" else -1)
                mapped = np.asarray(value["T_work_from_source_m"])[:3, :3] @ vector
                if not np.allclose(mapped, [0, 0, 1], atol=1e-5, rtol=0):
                    raise ContractError("Alignment does not map the declared source up axis to work +Z")
    def assets(value):
        if isinstance(value, dict):
            if "root" in value and "path" in value:
                yield value
            for item in value.values():
                yield from assets(item)
        elif isinstance(value, list):
            for item in value:
                yield from assets(item)
    for document in (scene, frames):
        if document is not None:
            for asset in assets(document):
                path = resolve_asset(asset, roots)
                if not path.is_file():
                    raise ContractError(f"Referenced asset is missing: {asset['path']}")
                if "sha256" in asset and sha256(path) != asset["sha256"]:
                    raise ContractError(f"Referenced asset hash differs: {asset['path']}")
    return stage_eligibility(scene, frames)


def verify_lock(document, roots):
    validate(document)
    errors = []
    for asset in document["files"]:
        path = resolve_asset(asset, roots)
        if not path.is_file():
            errors.append(f"Missing: {asset['root']}:{asset['path']}")
        elif path.stat().st_size != asset["bytes"] or sha256(path) != asset["sha256"]:
            errors.append(f"Changed: {asset['root']}:{asset['path']}")
    return {"passed": not errors, "checked_files": len(document["files"]), "errors": errors}
