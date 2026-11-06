# SPDX-License-Identifier: Apache-2.0
"""Generic metric input preparation. No model or dataset-specific paths."""
from itertools import product
from pathlib import Path
import time

import numpy as np
from PIL import Image

from .contracts import (ContractError, read_json, resolve_asset, rigid, sha256,
    transform_points, unproject, validate, work_camera)
from .jobs import Stage, atomic_json, digest
from ._exchange.exchange import result_status

DEFAULTS = {"point_source": "auto", "geometry_id": None, "voxel_m": .02,
    "depth_stride": 2, "boxer_stride": 3, "depth_range_m": [.05, 6.],
    "sample_points": 500000, "seed": 42, "floor_cuts_m": [],
    "domain_m": 31.975, "overlap_m": .5, "max_frames": None,
    "frame_stride": 1, "alignment": "existing_or_auto", "yaw_degrees": None}


def rotation_between_up(up):
    if up == "unknown":
        raise ContractError("Source up is unknown; declare a verified up axis before preparation")
    u = np.eye(3)["XYZ".index(up[1])] * (1 if up[0] == "+" else -1)
    v = np.array([0., 0., 1.])
    c = float(u @ v)
    if c > 1 - 1e-12:
        return np.eye(3)
    if c < -1 + 1e-12:
        return np.diag([1., -1., -1.])
    axis = np.cross(u, v)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def estimate_alignment(normals, weights, up, yaw_degrees=None):
    r = rotation_between_up(up)
    normals = np.asarray(normals) @ r.T
    keep = np.isfinite(normals).all(1) & (np.abs(normals[:, 2]) < .2)
    w = np.asarray(weights)[keep]
    angles = np.arctan2(normals[keep, 1], normals[keep, 0])
    c, s = np.sum(w * np.cos(4 * angles)), np.sum(w * np.sin(4 * angles))
    strength = float(np.hypot(c, s) / w.sum()) if w.sum() else 0.
    estimated = float(-np.arctan2(s, c) / 4) if strength > 1e-8 else 0.
    yaw = estimated if yaw_degrees is None else np.radians(yaw_degrees)
    cz, sz = np.cos(yaw), np.sin(yaw)
    result = np.eye(4)
    result[:3, :3] = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]) @ r
    return result, {"applied_yaw_degrees": float(np.degrees(yaw)), "estimated_yaw_degrees": float(np.degrees(estimated)),
        "four_angle_concentration": strength, "vertical_support_weight": float(w.sum()), "review_recommended": strength < .25}


def partition(points, cuts, domain, overlap):
    """Overlapping model windows with disjoint ownership cores; no input scaling."""
    p = np.asarray(points)
    if not len(p) or not np.isfinite(p).all():
        raise ContractError("Point cloud must be nonempty and finite")
    if not (0 <= overlap < domain) or domain <= 0:
        raise ContractError("Require 0 <= overlap < model domain")
    cuts = sorted(cuts)
    if len(cuts) != len(set(cuts)) or not np.isfinite(cuts).all():
        raise ContractError("Floor cuts must be unique finite values")
    slabs = [-np.inf, *cuts, np.inf]
    regions = []
    for level, (lo, hi) in enumerate(zip(slabs, slabs[1:])):
        mask = (p[:, 2] >= lo) & (p[:, 2] < hi)
        if not mask.any():
            continue
        lower, upper = p[mask].min(0), p[mask].max(0)
        axes = []
        for dim in range(3):
            starts = [float(lower[dim])]
            while upper[dim] - starts[-1] >= domain:
                starts.append(starts[-1] + domain - overlap)
            windows = []
            for i, start in enumerate(starts):
                end = start + domain
                owner_min = start + (overlap / 2 if i else 0)
                owner_max = end - (overlap / 2 if i < len(starts) - 1 else 0)
                windows.append((start, end, owner_min, owner_max))
            axes.append(windows)
        for index, window in enumerate(product(*axes)):
            bounds = np.array(window)
            selected = mask & np.all(p >= bounds[:, 0], axis=1) & np.all(p < bounds[:, 1], axis=1)
            if not selected.any():
                continue
            regions.append({"id": f"floor-{level}/region-{index}", "floor_id": f"floor-{level}",
                "bounds_m": [bounds[:, 0].tolist(), bounds[:, 1].tolist()],
                "ownership_bounds_m": [bounds[:, 2].tolist(), bounds[:, 3].tolist()],
                "indices": np.flatnonzero(selected)})
    return regions


def visible_points(points, K, pose, size, stride):
    camera = (np.asarray(points) - pose[:3, 3]) @ pose[:3, :3]
    valid = np.isfinite(camera).all(1) & (camera[:, 2] > 0)
    indices = np.flatnonzero(valid)
    uvh = camera[valid] @ np.asarray(K).T
    uv = uvh[:, :2] / uvh[:, 2, None]
    inside = np.all(uv >= 0, axis=1) & np.all(uv < size, axis=1)
    indices, uv = indices[inside], uv[inside]
    bins = np.floor(uv / stride).astype(int)
    keys = bins[:, 1] * int(np.ceil(size[0] / stride)) + bins[:, 0]
    order = np.argsort(camera[indices, 2], kind="stable")
    _, first = np.unique(keys[order], return_index=True)
    kept = order[first]
    return np.asarray(points[indices[kept]], np.float32), uv[kept], camera[indices[kept], 2].astype(np.float32)


def _cloud(points, colors):
    import open3d as o3d
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(points)
    if colors is not None:
        cloud.colors = o3d.utility.Vector3dVector(colors)
    return cloud


def prepare(scene_path, job, roots, options=None, resume=False, preflight=False):
    started = time.perf_counter()
    scene_path, job = Path(scene_path), Path(job).resolve() if job is not None else None
    roots = {k: Path(v).resolve() for k, v in roots.items()}
    roots.setdefault("bundle", scene_path.parent.resolve())
    scene = validate(read_json(scene_path))
    frames = validate(read_json(resolve_asset(scene["frames"], roots)))["frames"] if "frames" in scene else []
    if frames and read_json(resolve_asset(scene["frames"], roots))["scene_id"] != scene["scene_id"]:
        raise ContractError("Frame manifest belongs to another scene")
    config = dict(DEFAULTS)
    if options:
        if set(options) - set(config):
            raise ContractError(f"Unknown preparation options: {sorted(set(options)-set(config))}")
        config.update(options)
    for key in ("depth_stride", "boxer_stride", "sample_points", "frame_stride"):
        if not isinstance(config[key], int) or config[key] < 1:
            raise ContractError(f"{key} must be a positive integer")
    if config["voxel_m"] <= 0 or config["depth_range_m"][0] <= 0 or config["depth_range_m"][0] >= config["depth_range_m"][1]:
        raise ContractError("Invalid sampling or depth range")
    if config["max_frames"] is not None and config["max_frames"] < 1:
        raise ContractError("max_frames must be positive")
    if config["alignment"] not in ("existing_or_auto", "auto", "preserve"):
        raise ContractError("alignment must be existing_or_auto, auto or preserve")
    frames = frames[::config["frame_stride"]][:config["max_frames"]]
    geometries = scene["geometry"]
    if len(geometries) > 1 and config["geometry_id"] is None:
        raise ContractError(f"Multiple geometry assets; specify geometry_id: {[g['id'] for g in geometries]}")
    geometry = next((g for g in geometries if g["id"] == config["geometry_id"]), None) if config["geometry_id"] else next(iter(geometries), None)
    if config["geometry_id"] and geometry is None:
        raise ContractError("Unknown geometry_id")
    scale = scene["source_coordinates"]["metres_per_unit"]
    if scale is None and not (geometry and geometry["coordinate_frame"] == "work"):
        raise ContractError("Metric source scale is required")
    if not (geometry and geometry["coordinate_frame"] == "work") and scene["source_coordinates"]["up_axis"] == "unknown":
        raise ContractError("Declare the source up axis before preparation")
    if config["alignment"] == "preserve" and not (geometry and geometry["coordinate_frame"] == "work") and scene["source_coordinates"]["up_axis"] != "+Z":
        raise ContractError("preserve requires +Z source axes; use a declared alignment or auto to convert other up axes")
    if config["alignment"] == "preserve" and config["yaw_degrees"] not in (None, 0):
        raise ContractError("preserve cannot be combined with a nonzero yaw")
    # Hash actual referenced files, excluding absolute bindings from identity.
    assets = {}
    def check_asset(asset):
        path = resolve_asset(asset, roots)
        if job is not None and (job == path.resolve() or job.is_relative_to(path.resolve())):
            raise ContractError("Job output must be separate from the input asset")
        value = sha256(path)
        if "sha256" in asset and value != asset["sha256"]:
            raise ContractError(f"Input asset hash differs: {asset['path']}")
        assets[asset["root"] + ":" + asset["path"]] = value
        return path
    if geometry:
        geometry_path = check_asset(geometry["asset"])
    for f in frames:
        for key in ("image", "depth"):
            if key in f:
                check_asset(f[key] if key == "image" else f[key]["asset"])
    existing_alignment = None
    if "alignment" in scene and config["alignment"] == "existing_or_auto":
        existing_alignment = validate(read_json(check_asset(scene["alignment"])))
        if existing_alignment["scene_id"] != scene["scene_id"]:
            raise ContractError("Alignment belongs to another scene")
    has_depth = scale is not None and any("depth" in f and "camera" in f for f in frames)
    selected_source = config["point_source"]
    if selected_source == "auto":
        selected_source = "rgbd" if has_depth else geometry["type"] if geometry else None
    if selected_source not in ("rgbd", "mesh", "point_cloud"):
        raise ContractError("No usable point source; select mesh, point_cloud or calibrated rgbd")
    if selected_source == "rgbd" and not has_depth:
        raise ContractError("RGB-D preparation needs calibrated depth frames")
    if selected_source in ("mesh", "point_cloud") and (not geometry or geometry["type"] != selected_source):
        raise ContractError(f"Selected point_source={selected_source} does not match the selected geometry")
    warnings = []
    if geometry and has_depth and config["point_source"] == "auto":
        warnings.append("Both geometry and depth exist; auto chooses RGB-D. Set point_source to use the geometry instead.")
    if config["voxel_m"]:
        warnings.append("Voxel sampling may resample an already filtered reconstruction; this is not an accuracy guarantee.")
    plan = dict(passed=True, **result_status(execution="ready", content="references_checked",
                next_steps=["Run prepare with a new output directory"]),
                scene_id=scene["scene_id"], geometry_id=geometry["id"] if geometry else None,
                point_source=selected_source, source_coordinates=scene["source_coordinates"],
                alignment_policy=config["alignment"], provided_alignment=existing_alignment,
                options=config, frames=[f["id"] for f in frames], warnings=warnings)
    if preflight:
        return plan
    if job is None:
        raise ContractError("prepare requires --out unless --preflight is used")
    import open3d as o3d
    import trimesh
    fingerprint = digest({"scene": scene, "frames": frames, "assets": assets, "options": config, "code": sha256(__file__)})
    stage = Stage(job / "prepare", fingerprint, resume)
    atomic_json(job / "bindings.local.json", {k: str(v) for k, v in roots.items()})
    if stage.state["status"] == "complete" and all(stage.valid(p) for p in stage.state["files"]):
        return {"passed": True, "resumed": True, "manifest": str(stage.directory / "prepared.json"),
                "summary": result_status(content="passed", next_steps=["Continue with predict or evidence"])}
    mesh, cloud = None, None
    if geometry and geometry["type"] == "mesh":
        mesh = trimesh.load(geometry_path, force="scene").to_geometry()
        if not len(mesh.faces):
            raise ContractError("Declared mesh has no faces")
        mesh.vertices *= (1 if geometry["coordinate_frame"] == "work" else scale)
        normals, weights = mesh.face_normals, mesh.area_faces
    elif geometry:
        cloud = o3d.io.read_point_cloud(str(geometry_path))
        if not len(cloud.points):
            raise ContractError("Input point cloud is empty")
        cloud.scale(1 if geometry["coordinate_frame"] == "work" else scale, center=(0, 0, 0))
        cloud.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=.2, max_nn=30))
        normals, weights = np.asarray(cloud.normals), np.ones(len(cloud.points))
    else:
        normals, weights = np.empty((0, 3)), np.empty(0)
    quality = {}
    if config["alignment"] == "preserve":
        alignment = np.eye(4)
        quality["method"] = "preserve_declared_axes"
    elif existing_alignment is not None:
        alignment = rigid(existing_alignment["T_work_from_source_m"])
        quality["method"] = "provided_alignment"
    elif geometry and geometry["coordinate_frame"] == "work":
        alignment = np.eye(4)
        quality["method"] = "already_in_work_frame"
    else:
        alignment, quality = estimate_alignment(normals, weights, scene["source_coordinates"]["up_axis"], config["yaw_degrees"])
        quality["method"] = "vertical_normals_four_angle_mean" if len(weights) else "declared_up_and_optional_yaw"
    source_up = scene["source_coordinates"]["up_axis"]
    if source_up != "unknown" and not (geometry and geometry["coordinate_frame"] == "work"):
        up = np.eye(3)["XYZ".index(source_up[1])] * (1 if source_up[0] == "+" else -1)
        if not np.allclose(alignment[:3, :3] @ up, [0, 0, 1], atol=1e-5, rtol=0):
            raise ContractError("Alignment does not map declared source up to +Z")
    if mesh is not None and geometry["coordinate_frame"] != "work":
        mesh.apply_transform(alignment)
    if cloud is not None and geometry["coordinate_frame"] != "work":
        cloud.transform(alignment)
    point_source = selected_source
    if point_source not in ("rgbd", "mesh", "point_cloud"):
        raise ContractError("Unknown point source")
    xyzs, colors, depth_counts = [], [], {}
    if point_source == "rgbd":
        if scale is None:
            raise ContractError("RGB-D camera translations require metric source scale")
        for f in frames:
            if "depth" not in f or "camera" not in f:
                continue
            d = f["depth"]
            depth = np.asarray(Image.open(resolve_asset(d["asset"], roots)))
            if list(depth.shape) != [f["size"][1], f["size"][0]]:
                raise ContractError("Depth shape does not match calibration")
            stride = config["depth_stride"]
            z = depth[::stride, ::stride].astype(np.float32) / (1 / d["metres_per_unit"])
            v, u = np.mgrid[0:depth.shape[0]:stride, 0:depth.shape[1]:stride]
            keep = np.isfinite(z) & (z >= config["depth_range_m"][0]) & (z <= config["depth_range_m"][1])
            if "invalid_value" in d:
                keep &= depth[::stride, ::stride] != d["invalid_value"]
            pose = work_camera(f["camera"]["T_source_from_camera"], alignment, scale)
            xyzs.append(unproject(np.column_stack((u[keep], v[keep])), z[keep], f["camera"]["K"], pose).astype(np.float32))
            if "image" in f:
                image = np.asarray(Image.open(resolve_asset(f["image"], roots)).convert("RGB"))
                if list(image.shape[:2]) != [f["size"][1], f["size"][0]]:
                    raise ContractError("Image size does not match calibration")
                colors.append(image[::stride, ::stride][keep] / 255.)
            depth_counts[f["id"]] = int(keep.sum())
        if not xyzs:
            raise ContractError("RGB-D preparation needs calibrated depth frames")
        global_cloud = _cloud(np.concatenate(xyzs), np.concatenate(colors) if len(colors) == len(xyzs) else None)
    elif point_source == "mesh":
        if mesh is None:
            raise ContractError("Mesh sampling needs a mesh")
        legacy = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(mesh.vertices), o3d.utility.Vector3iVector(mesh.faces))
        rgb = mesh.visual.vertex_colors[:, :3] / 255. if mesh.visual.kind == "vertex" else None
        if rgb is not None:
            legacy.vertex_colors = o3d.utility.Vector3dVector(rgb)
        o3d.utility.random.seed(config["seed"])
        global_cloud = legacy.sample_points_uniformly(config["sample_points"])
    else:
        if cloud is None:
            raise ContractError("Point-cloud preparation needs a point cloud")
        global_cloud = cloud
    raw_count = len(global_cloud.points)
    global_cloud = global_cloud.voxel_down_sample(config["voxel_m"])
    points = np.asarray(global_cloud.points)
    regions = partition(points, config["floor_cuts_m"], config["domain_m"], config["overlap_m"])
    prepared = {"schema_version": 1, "scene_id": scene["scene_id"], "fingerprint": fingerprint,
        "options": config, "input_hashes": assets, "alignment": alignment.tolist(), "alignment_quality": quality,
        "point_source": point_source, "raw_points": raw_count, "points": len(points), "has_color": len(global_cloud.colors) == len(points),
        "depth_frame_counts": depth_counts, "regions": [], "frames": [], "boxer_point_protocol": "mesh_first_hit_raycast" if mesh is not None else "projected_cloud_nearest_per_pixel_bin"}
    o3d.io.write_point_cloud(str(stage.directory / "global.ply"), global_cloud)
    stage.record("global.ply")
    for i, region in enumerate(regions):
        relative = f"regions/{i:04}.ply"
        (stage.directory / "regions").mkdir(exist_ok=True)
        selected = global_cloud.select_by_index(region.pop("indices"))
        o3d.io.write_point_cloud(str(stage.directory / relative), selected)
        stage.record(relative)
        prepared["regions"].append(dict(region, path=relative, points=len(selected.points)))
    ray_scene = None
    if mesh is not None:
        ray_scene = o3d.t.geometry.RaycastingScene()
        ray_scene.add_triangles(o3d.core.Tensor(np.asarray(mesh.vertices, np.float32)), o3d.core.Tensor(np.asarray(mesh.faces, np.uint32)))
    for i, f in enumerate(frames):
        if "image" not in f or "camera" not in f or scale is None:
            continue
        relative = f"points/{i:06}.npz"
        pose = work_camera(f["camera"]["T_source_from_camera"], alignment, scale)
        if not stage.valid(relative):
            k = np.array(f["camera"]["K"])
            if ray_scene is not None:
                u, v = np.meshgrid(np.arange(0, f["size"][0], config["boxer_stride"]), np.arange(0, f["size"][1], config["boxer_stride"]))
                pixels = np.column_stack((u.ravel(), v.ravel(), np.ones(u.size)))
                directions = (pixels @ np.linalg.inv(k).T) @ pose[:3, :3].T
                rays = np.column_stack((np.broadcast_to(pose[:3, 3], directions.shape), directions)).astype(np.float32)
                z = ray_scene.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
                keep = np.isfinite(z) & (z > 0)
                xyz, uv, z = rays[keep, :3] + z[keep, None] * rays[keep, 3:], pixels[keep, :2], z[keep]
            else:
                xyz, uv, z = visible_points(points, k, pose, f["size"], config["boxer_stride"])
            (stage.directory / "points").mkdir(exist_ok=True)
            np.savez_compressed(stage.directory / relative, points=xyz, uv=uv, depth=z, K=k, T=pose)
            stage.record(relative)
        prepared["frames"].append({"id": f["id"], "size": f["size"], "image": f["image"], "points_path": relative})
    atomic_json(stage.directory / "prepared.json", prepared)
    stage.record("prepared.json")
    stage.complete()
    return {"passed": True, "manifest": str(stage.directory / "prepared.json"), "points": len(points), "regions": len(regions), "frames": len(prepared["frames"]), "seconds": time.perf_counter()-started,
            "summary": result_status(content="passed", next_steps=["Continue with init-task or predict; geometric accuracy remains to be evaluated"]),
            "selection": plan, "alignment": alignment.tolist(), "warnings": warnings}
