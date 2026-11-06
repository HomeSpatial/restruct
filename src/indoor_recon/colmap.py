# SPDX-License-Identifier: Apache-2.0
"""COLMAP binary + OPENCV import, including corner-to-centre pixel conversion."""
import io
from pathlib import Path, PurePosixPath
import struct
import zipfile

import numpy as np
from PIL import Image

from .contracts import ContractError, sha256, validate
from .jobs import atomic_json

MODELS = {0: ("SIMPLE_PINHOLE", 3), 1: ("PINHOLE", 4), 2: ("SIMPLE_RADIAL", 4),
    3: ("RADIAL", 5), 4: ("OPENCV", 8), 5: ("OPENCV_FISHEYE", 8),
    6: ("FULL_OPENCV", 12), 7: ("FOV", 5), 8: ("SIMPLE_RADIAL_FISHEYE", 4),
    9: ("RADIAL_FISHEYE", 5), 10: ("THIN_PRISM_FISHEYE", 12)}


def unpack(stream, pattern):
    size = struct.calcsize("<" + pattern)
    data = stream.read(size)
    if len(data) != size:
        raise ContractError("Truncated COLMAP binary")
    return struct.unpack("<" + pattern, data)


def read_cameras(data):
    stream = io.BytesIO(data)
    cameras = {}
    for _ in range(unpack(stream, "Q")[0]):
        cid, model, w, h = unpack(stream, "iiQQ")
        if model not in MODELS:
            raise ContractError(f"Unknown COLMAP camera model ID: {model}")
        name, n = MODELS[model]
        if cid in cameras:
            raise ContractError("Duplicate COLMAP camera ID")
        cameras[cid] = {"model": name, "size": [w, h], "params": unpack(stream, "d" * n)}
    return cameras


def quaternion_matrix(q):
    q = np.asarray(q, float)
    if not np.isfinite(q).all() or abs(np.linalg.norm(q)-1) > 1e-5:
        raise ContractError("COLMAP quaternion must be unit length")
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([[1-2*y*y-2*z*z, 2*x*y-2*w*z, 2*x*z+2*w*y],
        [2*x*y+2*w*z, 1-2*x*x-2*z*z, 2*y*z-2*w*x],
        [2*x*z-2*w*y, 2*y*z+2*w*x, 1-2*x*x-2*y*y]])


def read_images(data):
    stream = io.BytesIO(data)
    images = []
    for _ in range(unpack(stream, "Q")[0]):
        row = unpack(stream, "idddddddi")
        name = bytearray()
        while True:
            c = stream.read(1)
            if not c:
                raise ContractError("Truncated COLMAP image name")
            if c == b"\0":
                break
            name.extend(c)
        n = unpack(stream, "Q")[0]
        data2d = stream.read(n * 24)
        if len(data2d) != n * 24:
            raise ContractError("Truncated COLMAP observations")
        observations = np.frombuffer(data2d, dtype=[("x", "<f8"), ("y", "<f8"), ("point_id", "<i8")])
        r = quaternion_matrix(row[1:5])
        pose = np.eye(4)
        pose[:3, :3], pose[:3, 3] = r.T, -r.T @ np.array(row[5:8])
        images.append({"id": str(row[0]), "camera_id": row[8], "name": name.decode("utf-8"), "pose": pose,
            "uv": np.column_stack((observations["x"], observations["y"])), "point_ids": observations["point_id"]})
    if len({r["id"] for r in images}) != len(images):
        raise ContractError("Duplicate COLMAP image ID")
    return sorted(images, key=lambda r: int(r["id"]))


def read_points(data):
    stream = io.BytesIO(data)
    points = {}
    for _ in range(unpack(stream, "Q")[0]):
        row = unpack(stream, "QdddBBBd")
        points[row[0]] = np.array(row[1:4])
        tracks = unpack(stream, "Q")[0]
        if len(stream.read(tracks * 8)) != tracks * 8:
            raise ContractError("Truncated COLMAP tracks")
    return points


def camera_parameters(camera):
    name, p = camera["model"], camera["params"]
    if name == "SIMPLE_PINHOLE":
        fx, cx, cy = p
        fy, distortion = fx, np.zeros(4)
    elif name == "PINHOLE":
        fx, fy, cx, cy = p
        distortion = np.zeros(4)
    elif name == "OPENCV":
        fx, fy, cx, cy, *distortion = p
        distortion = np.array(distortion)
    else:
        raise ContractError(f"Undistortion is not implemented for {name}; retain raw data and use a supported adapter")
    # COLMAP pixel centres start at .5; our pinhole centres start at 0.
    k = np.array([[fx, 0, cx-.5], [0, fy, cy-.5], [0, 0, 1.]])
    return k, distortion


def distort(xy, distortion):
    k1, k2, p1, p2 = distortion
    x, y = np.asarray(xy)[..., 0], np.asarray(xy)[..., 1]
    r2 = x*x + y*y
    radial = 1 + k1*r2 + k2*r2*r2
    return np.stack((x*radial+2*p1*x*y+p2*(r2+2*x*x), y*radial+p1*(r2+2*y*y)+2*p2*x*y), axis=-1)


def rectify(image, camera, max_side):
    from scipy.ndimage import map_coordinates
    k, distortion = camera_parameters(camera)
    w, h = camera["size"]
    if image.size != (w, h):
        raise ContractError("COLMAP image dimensions do not match camera")
    factor = min(1., max_side / max(w, h)) if max_side else 1.
    nw, nh = max(1, round(w*factor)), max(1, round(h*factor))
    sx, sy = nw/w, nh/h
    target = k.copy()
    target[0, :2] *= sx
    target[1, :2] *= sy
    target[0, 2] = (k[0, 2]+.5)*sx-.5
    target[1, 2] = (k[1, 2]+.5)*sy-.5
    v, u = np.mgrid[:nh, :nw]
    xy = np.stack(((u-target[0, 2])/target[0, 0], (v-target[1, 2])/target[1, 1]), axis=-1)
    warped = distort(xy, distortion)
    source_u = warped[..., 0]*k[0, 0]+k[0, 2]
    source_v = warped[..., 1]*k[1, 1]+k[1, 2]
    rgb = np.asarray(image.convert("RGB"))
    out = np.stack([map_coordinates(rgb[..., channel], [source_v, source_u], order=1, mode="constant", cval=0) for channel in range(3)], axis=-1)
    return Image.fromarray(out), target


def import_colmap(source, model_prefix, images_prefix, geometry_path, output,
                  scene_id, up_axis, metres_per_unit, max_side=768, max_frames=None,
                  geometry_type="auto", frame_ids=()):
    """Read only selected ZIP members; source archive is never extracted in place."""
    source, output = Path(source), Path(output).resolve()
    if output.exists():
        raise ContractError("COLMAP import requires a new output directory")
    if max_side < 1 or (max_frames is not None and max_frames < 1):
        raise ContractError("max_side and max_frames must be positive")
    if frame_ids and max_frames is not None:
        raise ContractError("Use explicit frame IDs or max_frames, not both")
    archive = zipfile.ZipFile(source) if source.is_file() else None
    def read(name):
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or "\\" in name:
            raise ContractError("Unsafe input member path")
        return archive.read(name) if archive else (source / name).read_bytes()
    try:
        camera_bytes = read(model_prefix.rstrip("/") + "/cameras.bin")
        image_bytes = read(model_prefix.rstrip("/") + "/images.bin")
        point_bytes = read(model_prefix.rstrip("/") + "/points3D.bin")
        cameras, images, points = read_cameras(camera_bytes), read_images(image_bytes), read_points(point_bytes)
        if frame_ids:
            by_id = {row["id"]: row for row in images}
            if len(set(frame_ids)) != len(frame_ids) or set(frame_ids) - by_id.keys():
                raise ContractError("Unknown or duplicate COLMAP frame IDs")
            images = [by_id[i] for i in frame_ids]
        # Uniform coverage rather than silently taking only the initial camera trajectory.
        if max_frames and len(images) > max_frames:
            images = [images[i] for i in np.linspace(0, len(images)-1, max_frames, dtype=int)]
        for row in images:
            camera_parameters(cameras[row["camera_id"]])
        output.mkdir(parents=True)
        (output / "source").mkdir()
        members = []
        def retain(name, relative, data=None):
            content = read(name) if data is None else data
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            record = {"member": name, "retained_path": relative, "bytes": len(content), "sha256": sha256(path)}
            if archive:
                record["zip_crc32"] = archive.getinfo(name).CRC
            members.append(record)
            return path
        for name, data in (("cameras.bin", camera_bytes), ("images.bin", image_bytes), ("points3D.bin", point_bytes)):
            retain(model_prefix.rstrip("/") + "/" + name, "source/" + name, data)
        geometry = retain(geometry_path, "source/geometry" + Path(geometry_path).suffix)
        from .exchange import geometry_info
        info = geometry_info(geometry)
        if geometry_type != "auto" and geometry_type != info["type"]:
            raise ContractError("Geometry type does not match native file content")
        frames, errors, anchors = [], [], []
        for index, row in enumerate(images):
            camera = cameras[row["camera_id"]]
            member = images_prefix.rstrip("/") + "/" + row["name"]
            original = retain(member, f"source/images/{index:06}{Path(row['name']).suffix}")
            with Image.open(original) as image:
                rectified, k = rectify(image, camera, max_side)
            relative = f"rgb/{index:06}.png"
            (output / "rgb").mkdir(exist_ok=True)
            rectified.save(output / relative)
            frames.append({"id": row["id"], "size": list(rectified.size), "image": {"root": "bundle", "path": relative, "sha256": sha256(output / relative)},
                "camera": {"model": "pinhole_rectified", "axes": "x_right_y_down_z_forward", "pixel_convention": "top_left_integer_centres", "K": k.tolist(), "T_source_from_camera": row["pose"].tolist()},
                "sources": [{"kind": "legacy_record", "reference": {"root": "bundle", "path": "source/images.bin"}, "record_id": row["id"], "note": row["name"]}]})
            ids = [i for i, key in enumerate(row["point_ids"]) if key >= 0 and int(key) in points][::50]
            if ids:
                xyz = np.array([points[int(row["point_ids"][i])] for i in ids])
                pc = (xyz-row["pose"][:3, 3]) @ row["pose"][:3, :3]
                keep = pc[:, 2] > 0
                native_k, d = camera_parameters(camera)
                uv = distort(pc[keep, :2]/pc[keep, 2, None], d) @ native_k[:2, :2].T + native_k[:2, 2]
                measured = row["uv"][np.array(ids)[keep]]-.5
                errors.extend(np.linalg.norm(uv-measured, axis=1).tolist())
                for world, pixel in zip(xyz[keep][:8], uv[:8]):
                    anchors.append({"frame_id": row["id"], "source_xyz": world.tolist(), "source_distorted_pixel": pixel.tolist()})
        frame_doc = {"schema_version": 1, "kind": "frames", "scene_id": scene_id, "frames": frames}
        scene = {"schema_version": 1, "kind": "scene", "scene_id": scene_id,
            "source_coordinates": {"handedness": "right", "up_axis": up_axis, "metres_per_unit": metres_per_unit},
            "geometry": [{"id": "scan", "type": info["type"], "asset": {"root": "bundle", "path": geometry.relative_to(output).as_posix(), "sha256": sha256(geometry)}, "coordinate_frame": "source", "has_color": info["has_color"]}],
            "frames": {"root": "bundle", "path": "frames.json"}}
        validate(frame_doc)
        validate(scene)
        atomic_json(output / "frames.json", frame_doc)
        atomic_json(output / "scene.json", scene)
        report = {"passed": True, "scene_id": scene_id, "frames": len(frames), "source_members": members,
            "frame_ids": [f["id"] for f in frames], "selection": "explicit_ids" if frame_ids else "uniform" if max_frames else "all",
            "geometry": info,
            "archive_bytes": source.stat().st_size if archive else None, "archive_sha256": None,
            "archive_identity_note": "Selected members CRC-checked by ZipFile and SHA-256 retained; whole archive not hashed",
            "source_camera_models": sorted({c["model"] for c in cameras.values()}), "pixel_conversion": "COLMAP corner centres .5 -> integer centres 0; calibrated distortion remap and resize",
            "sparse_reprojection": {"samples": len(errors), "median_px": float(np.median(errors)) if errors else None, "p95_px": float(np.percentile(errors, 95)) if errors else None},
            "sparse_anchor_samples": anchors, "depth_note": "Archive render_depth is not used; mesh and calibrated RGB supply the development input"}
        atomic_json(output / "import.json", report)
        return {k: report[k] for k in ("passed", "scene_id", "frames", "source_camera_models", "sparse_reprojection")}
    finally:
        if archive:
            archive.close()
