# SPDX-License-Identifier: Apache-2.0
"""Read-only evidence queries over prepared cameras, scan points and source meshes."""
from itertools import product
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .contracts import (ContractError, camera_matrix, read_json, resolve_asset,
                        rigid, sha256, transform_points)
from .jobs import atomic_json
from .scene_model import asset as validate_asset


def bounds(value):
    box = np.asarray(value, dtype=float)
    if box.shape != (2, 3) or not np.isfinite(box).all() or np.any(box[0] >= box[1]):
        raise ContractError("bounds_work_m must contain finite, increasing min/max XYZ")
    return box


def corners(box):
    return np.array(list(product(*zip(*bounds(box)))))


def fit_plane(points, threshold_m, seed=42):
    """Fit a dominant plane with RANSAC, then refine its inliers in float64."""
    import open3d as o3d
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 3 or not np.isfinite(points).all():
        raise ContractError("A plane needs at least three finite XYZ points")
    if not np.isfinite(threshold_m) or threshold_m <= 0:
        raise ContractError("distance_threshold_m must be positive")
    o3d.utility.random.seed(seed)
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    _, indices = cloud.segment_plane(threshold_m, 3, 500)
    if len(indices) < 3:
        raise ContractError("Insufficient plane support")
    selected = points[indices]
    center = np.mean(selected, axis=0)
    _, values, vectors = np.linalg.svd(selected-center, full_matrices=False)
    if values[1] < 1e-10:
        raise ContractError("Plane support is collinear")
    normal = vectors[-1]
    if normal[np.argmax(abs(normal))] < 0:
        normal = -normal
    signed = (points-center) @ normal
    residual = abs(signed[indices])
    return {"point_work_m": center.tolist(), "normal_work": normal.tolist(),
            "plane_work": [*normal.tolist(), float(-normal @ center)],
            "points": len(points), "inliers": len(indices), "inlier_fraction": len(indices)/len(points),
            "distance_threshold_m": threshold_m, "seed": seed,
            "residual_m": {"median": float(np.median(residual)), "p90": float(np.percentile(residual, 90)),
                           "p99": float(np.percentile(residual, 99)), "max": float(residual.max())}}, signed


class Evidence:
    def __init__(self, config, roots):
        if config.get("schema_version") != 1 or config.get("kind") != "evidence":
            raise ContractError("Expected evidence config v1")
        if set(config)-{"schema_version", "kind", "scene_id", "prepared", "scan"}:
            raise ContractError("Unknown evidence configuration field")
        self.config, self.roots, self.inputs = config, roots, {}
        self.prepared_path = self.resolve(config["prepared"])
        self.directory = self.prepared_path.parent
        self.prepared = read_json(self.prepared_path)
        if self.prepared["scene_id"] != config["scene_id"]:
            raise ContractError("Evidence scene differs from prepared scene")
        self.frames = {f["id"]: f for f in self.prepared["frames"]}
        self._points = self._scene = None

    def pin(self, path, expected=None):
        path = Path(path).resolve()
        actual = self.inputs.get(str(path)) or sha256(path)
        if expected and actual != expected:
            raise ContractError(f"Evidence asset hash changed: {path}")
        self.inputs[str(path)] = actual
        return path

    def resolve(self, asset):
        validate_asset(asset)
        return self.pin(resolve_asset(asset, self.roots), asset.get("sha256"))

    def prepared_asset(self, relative):
        validate_asset({"root": "prepared", "path": relative})
        path = self.directory / relative
        state_path = self.directory / "state.json"
        if state_path.is_file():
            self.pin(state_path)
            expected = read_json(state_path)["files"].get(relative)
            if not expected:
                raise ContractError("Prepared evidence is absent from stage integrity record")
        else:
            expected = None
        return self.pin(path, expected)

    def frame(self, identity):
        if str(identity) not in self.frames:
            raise ContractError("Unknown calibrated frame ID")
        frame = self.frames[str(identity)]
        with np.load(self.prepared_asset(frame["points_path"]), allow_pickle=False) as data:
            k, pose = camera_matrix(data["K"]), rigid(data["T"])
        return frame, k, pose

    def points(self):
        if self._points is None:
            import open3d as o3d
            cloud = o3d.io.read_point_cloud(str(self.prepared_asset("global.ply")))
            self._points = np.asarray(cloud.points)
            if not len(self._points) or not np.isfinite(self._points).all():
                raise ContractError("Prepared evidence cloud is empty or nonfinite")
        return self._points

    def scan_mesh(self):
        """Declared source scan as (work vertices float64, faces uint32)."""
        if getattr(self, "_scan_mesh", None) is None:
            if "scan" not in self.config:
                raise ContractError("Mesh ray queries unavailable: no source scan mesh declared")
            import trimesh
            scan = self.config["scan"]
            if set(scan) != {"asset", "metres_per_unit", "T_work_from_source_m"}:
                raise ContractError("Scan requires asset, metres_per_unit and T_work_from_source_m")
            scale = scan["metres_per_unit"]
            if not np.isfinite(scale) or scale <= 0:
                raise ContractError("Scan scale must be positive")
            mesh = trimesh.load(self.resolve(scan["asset"]), force="scene", process=False).to_mesh()
            points = transform_points(np.asarray(mesh.vertices)*scale, scan["T_work_from_source_m"])
            if not len(mesh.faces):
                raise ContractError("Declared source scan has no triangles")
            self._scan_mesh = (points, np.asarray(mesh.faces, np.uint32))
        return self._scan_mesh

    def ray_scene(self):
        if self._scene is None:
            import open3d as o3d
            points, faces = self.scan_mesh()
            self._scene = o3d.t.geometry.RaycastingScene()
            self._scene.add_triangles(o3d.core.Tensor(points.astype(np.float32)), o3d.core.Tensor(faces))
        return self._scene

    def raycast(self, origins_work_m, directions_work, max_distance_m=None):
        import open3d as o3d
        origin, direction = np.asarray(origins_work_m, float), np.asarray(directions_work, float)
        if origin.ndim != 2 or origin.shape[1] != 3 or direction.shape != origin.shape or not len(origin):
            raise ContractError("Rays require matching nonempty Nx3 origins and directions")
        length = np.linalg.norm(direction, axis=1)
        if not np.isfinite(origin).all() or not np.isfinite(direction).all() or np.any(length <= 0):
            raise ContractError("Rays must be finite with nonzero directions")
        if max_distance_m is not None and (not np.isfinite(max_distance_m) or max_distance_m <= 0):
            raise ContractError("max_distance_m must be positive")
        direction = direction / length[:, None]
        answer = self.ray_scene().cast_rays(o3d.core.Tensor(np.column_stack((origin, direction)).astype(np.float32)))
        distances = answer["t_hit"].numpy()
        rows = []
        for i, distance in enumerate(distances):
            hit = bool(np.isfinite(distance) and (max_distance_m is None or distance <= max_distance_m))
            rows.append({"hit": hit, "origin_work_m": origin[i].tolist(), "direction_work": direction[i].tolist(),
                         "distance_m": float(distance) if hit else None,
                         "point_work_m": (origin[i]+direction[i]*distance).tolist() if hit else None,
                         "triangle_index": int(answer["primitive_ids"][i].item()) if hit else None,
                         "normal_work": answer["primitive_normals"][i].numpy().tolist() if hit else None})
        return {"rays": rows, "misses": sum(not r["hit"] for r in rows), "max_distance_m": max_distance_m}

    def pixel_rays(self, frame_id, pixels, max_distance_m=None):
        frame, k, pose = self.frame(frame_id)
        uv = np.asarray(pixels, float)
        if uv.ndim != 2 or uv.shape[1] != 2 or not np.isfinite(uv).all() or np.any(uv < 0) or np.any(uv >= frame["size"]):
            raise ContractError("Pixels must be finite and inside the calibrated image")
        direction = np.column_stack((uv, np.ones(len(uv)))) @ np.linalg.inv(k).T @ pose[:3, :3].T
        result = self.raycast(np.broadcast_to(pose[:3, 3], direction.shape), direction, max_distance_m)
        return dict(result, frame_id=frame["id"], pixels=uv.tolist(), K=k.tolist(), T_work_from_camera=pose.tolist(),
                    size=list(frame["size"]))

    def photos(self, bounds_work_m, limit=6, frame_ids=None):
        if type(limit) is not int or limit < 1:
            raise ContractError("Photo limit must be a positive integer")
        box = bounds(bounds_work_m)
        target = corners(box)
        rows = []
        identities = list(self.frames) if frame_ids is None else list(map(str, frame_ids))
        for identity in identities:
            frame, k, pose = self.frame(identity)
            camera = (target-pose[:3, 3]) @ pose[:3, :3]
            if np.any(camera[:, 2] <= 1e-6):
                continue  # AABB crosses the near plane; do not return a misleading giant crop.
            projected = camera @ k.T
            uv = projected[:, :2] / projected[:, 2, None]
            lo, hi = np.maximum(uv.min(0), 0), np.minimum(uv.max(0), frame["size"])
            if np.any(hi <= lo):
                continue
            with np.load(self.prepared_asset(frame["points_path"]), allow_pickle=False) as data:
                samples = data["points"]
                support = int(np.all((samples >= box[0]) & (samples <= box[1]), axis=1).sum())
            area = float(np.prod(hi-lo))
            rows.append({"frame_id": identity, "image": frame["image"], "size": frame["size"],
                         "crop_xyxy": [*lo.tolist(), *hi.tolist()], "projected_area_px2": area,
                         "scan_first_hit_points_in_bounds": support,
                         "camera_work_m": pose[:3, 3].tolist()})
        rows.sort(key=lambda r: (r["scan_first_hit_points_in_bounds"], r["projected_area_px2"]), reverse=True)
        return {"photos": rows[:limit], "tested_frames": len(identities), "bounds_work_m": box.tolist(),
                "visibility_basis": "Rank by prepared first-hit scan samples in bounds, then projected area; not semantic visibility or object correctness"}

    def crop(self, output, frame_id, bounds_work_m=None, crop_xyxy=None, margin_px=12):
        frame, k, pose = self.frame(frame_id)
        if (bounds_work_m is None) == (crop_xyxy is None):
            raise ContractError("Specify either bounds_work_m or crop_xyxy")
        if bounds_work_m is not None:
            result = self.photos(bounds_work_m, 1, [frame_id])
            if not result["photos"]:
                raise ContractError("Requested bounds have no complete front-facing projection in frame")
            crop_xyxy = result["photos"][0]["crop_xyxy"]
        rectangle = np.asarray(crop_xyxy, float)
        if rectangle.shape != (4,) or not np.isfinite(rectangle).all() or np.any(rectangle[2:] <= rectangle[:2]):
            raise ContractError("crop_xyxy must be increasing finite pixel bounds")
        if type(margin_px) is not int or margin_px < 0:
            raise ContractError("margin_px must be a nonnegative integer")
        lo = np.maximum(np.floor(rectangle[:2])-margin_px, 0).astype(int)
        hi = np.minimum(np.ceil(rectangle[2:])+margin_px, frame["size"]).astype(int)
        if np.any(hi <= lo):
            raise ContractError("Crop lies outside image")
        source = self.resolve(frame["image"])
        with Image.open(source) as image:
            if list(image.size) != frame["size"]:
                raise ContractError("Image size differs from prepared calibration")
            image.convert("RGB").crop((*lo, *hi)).save(output / "crop.png")
        crop_k = k.copy()
        crop_k[0, 2] -= lo[0]
        crop_k[1, 2] -= lo[1]
        return {"frame_id": frame["id"], "source_image": str(source), "image": "crop.png",
                "crop_xyxy": [*lo.tolist(), *hi.tolist()], "size": (hi-lo).tolist(),
                "K_original": k.tolist(), "K_crop": crop_k.tolist(), "T_work_from_camera": pose.tolist()}

    def local_points(self, bounds_work_m):
        box, points = bounds(bounds_work_m), self.points()
        selected = points[np.all((points >= box[0]) & (points <= box[1]), axis=1)]
        if not len(selected):
            raise ContractError("No prepared scan points in requested bounds")
        return box, selected

    def section(self, output, bounds_work_m, axis, offset_m, thickness_m):
        if axis not in ("x", "y", "z") or not np.isfinite(offset_m) or not np.isfinite(thickness_m) or thickness_m <= 0:
            raise ContractError("Section requires axis x/y/z and positive finite thickness_m")
        box, points = self.local_points(bounds_work_m)
        index = "xyz".index(axis)
        selected = points[abs(points[:, index]-offset_m) <= thickness_m/2]
        np.savez_compressed(output / "section.npz", points_work_m=selected)
        axes = [i for i in range(3) if i != index]
        image = Image.new("RGB", (1000, 800), "white")
        draw = ImageDraw.Draw(image)
        size = box[1, axes]-box[0, axes]
        scale = min(900/size[0], 680/size[1])
        # PNG is a view; NPZ retains every selected point and the JSON retains the query domain.
        for point in selected[::max(1, len(selected)//100000)]:
            x, y = (point[axes]-box[0, axes])*scale
            draw.point((50+x, 740-y), fill=(30, 70, 120))
        draw.text((25, 15), f"{axis}={offset_m:g} m, slab {thickness_m:g} m; axes {'xyz'[axes[0]]}/{'xyz'[axes[1]]} in metres", fill="black")
        draw.text((25, 35), f"domain min {box[0, axes].tolist()} max {box[1, axes].tolist()}; {len(selected)} points", fill="black")
        image.save(output / "section.png")
        return {"axis": axis, "offset_m": offset_m, "thickness_m": thickness_m,
                "bounds_work_m": box.tolist(), "points": len(selected), "data": "section.npz", "image": "section.png"}

    def plane(self, output, bounds_work_m, distance_threshold_m, seed=42):
        box, selected = self.local_points(bounds_work_m)
        result, signed = fit_plane(selected, distance_threshold_m, seed)
        np.savez_compressed(output / "plane-support.npz", points_work_m=selected, signed_distance_m=signed)
        return dict(result, bounds_work_m=box.tolist(), data="plane-support.npz",
                    interpretation="Dominant local scan plane; semantic surface and occlusion must be reviewed")

    def query(self, query, output):
        operation = query["op"]
        arguments = {k: v for k, v in query.items() if k != "op"}
        if operation == "info":
            if arguments:
                raise ContractError("info takes no arguments")
            return {"frames": len(self.frames), "mesh_rays": "scan" in self.config,
                    "point_source": self.prepared["point_source"], "alignment": self.prepared["alignment"]}
        if operation in ("photos", "raycast", "pixel_rays"):
            return getattr(self, operation)(**arguments)
        if operation in ("crop", "section", "plane"):
            return getattr(self, operation)(output, **arguments)
        if operation == "appearance":
            from .appearance import surface_appearance
            return surface_appearance(self, output, **arguments)
        raise ContractError("Unknown evidence operation")


def run(config_path, query_path, output, roots):
    output = Path(output).resolve()
    if output.exists():
        raise ContractError("Evidence output requires a new directory")
    config_path, query_path = Path(config_path).resolve(), Path(query_path).resolve()
    config, query = read_json(config_path), read_json(query_path)
    evidence = Evidence(config, roots)
    # Prevent new evidence files from being written into a source package or prepared job.
    protected = [evidence.directory]
    protected.extend(Path(v).resolve() for v in roots.values())
    if any(output == p or output.is_relative_to(p) for p in protected):
        raise ContractError("Evidence output must be outside bound source roots and prepared data")
    output.mkdir(parents=True)
    try:
        data = evidence.query(query, output)
    except TypeError as error:
        raise ContractError(str(error)) from error
    result = {"passed": True, "scene_id": config["scene_id"], "coordinate_frame": "work_z_up_metres",
              "query": query, "data": data, "inputs_sha256": evidence.inputs,
              "config_sha256": sha256(config_path), "query_sha256": sha256(query_path),
              "implementation_sha256": sha256(__file__)}
    atomic_json(output / "evidence.json", result)
    atomic_json(output / "files.json", {p.name: sha256(p) for p in output.iterdir() if p.is_file()})
    return result
