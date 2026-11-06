# SPDX-License-Identifier: Apache-2.0
"""Native reconstruction-description v1 import; no private workspace dependencies."""
from pathlib import Path
import shutil

import numpy as np
from PIL import Image

from ._exchange.exchange import Catalog, result_status, write_description
from .colmap import camera_parameters, read_cameras, read_images, rectify
from .contracts import ContractError, sha256, validate
from .jobs import atomic_json


def geometry_info(path):
    import trimesh
    value = trimesh.load(path, force="scene", process=False)
    parts = list(value.geometry.values()) if hasattr(value, "geometry") else [value]
    vertices = sum(len(p.vertices) for p in parts)
    faces = sum(len(p.faces) for p in parts if hasattr(p, "faces"))
    if not vertices or any(not np.isfinite(p.vertices).all() for p in parts):
        raise ContractError("Geometry must contain nonempty finite vertices")
    color = all((getattr(p, "colors", None) is not None and len(p.colors) == len(p.vertices))
                or getattr(getattr(p, "visual", None), "kind", None) in ("vertex", "face", "texture") for p in parts)
    return {"type": "mesh" if faces else "point_cloud", "vertices": vertices,
            "faces": faces, "has_color": color}


def native_transform(catalog, source, target):
    transform = np.eye(4)
    for row, reverse in catalog.transform_path(source, target):
        matrix = np.array(row["T_to_from"], dtype=float)
        if row["units"] == "metres":
            src = catalog.spaces[row["from"]]["metres_per_unit"]
            dst = catalog.spaces[row["to"]]["metres_per_unit"]
            if src is None or dst is None:
                raise ContractError("Metre coordinate transforms require declared scales")
            matrix = np.diag([1/dst]*3 + [1]) @ matrix @ np.diag([src]*3 + [1])
        transform = (np.linalg.inv(matrix) if reverse else matrix) @ transform
    return transform


def inspect_description(document, roots=None, geometry_id=None, sparse_id=None,
                        images_id=None, frame_ids=(), no_photos=False, max_side=768):
    if max_side < 1:
        raise ContractError("max_side must be positive")
    catalog = Catalog(document, roots)
    geometry = catalog.select({"mesh", "point_cloud"}, geometry_id)
    if geometry["format"] not in ("ply", "glb"):
        raise ContractError(f"Unsupported geometry format: {geometry['format']}")
    coords = catalog.spaces[geometry["coordinate_system"]]
    if coords["handedness"] != "right" or coords["metres_per_unit"] is None or coords["up_axis"] == "unknown":
        raise ContractError("Import requires declared right-handed coordinates, metric scale and up axis")
    path = catalog.file(geometry)
    info = geometry_info(path)
    if geometry["type"] != info["type"]:
        raise ContractError(f"Declared {geometry['type']} but native file is {info['type']}")
    sparse = images = None
    sparse_files, photos, rows, cameras = {}, {}, [], {}
    transform = np.eye(4)
    if no_photos and (sparse_id or images_id or frame_ids):
        raise ContractError("--no-photos cannot be combined with photo or frame selections")
    if not no_photos:
        sparse = catalog.select({"sparse_reconstruction"}, sparse_id, optional=True)
        images = catalog.select({"image_collection"}, images_id, optional=True)
        if bool(sparse) != bool(images):
            raise ContractError("Calibrated photo import requires both COLMAP and image assets; use --no-photos for geometry only")
        if sparse:
            reference = images.get("coverage", {}).get("reference_asset")
            if reference and reference != sparse["id"]:
                raise ContractError("Selected image coverage refers to a different sparse asset")
            if sparse["format"] != "colmap.sparse.binary":
                raise ContractError("Photo import currently supports colmap.sparse.binary")
            sparse_files = {f["role"]: catalog.resolve(f["ref"]) for f in sparse["files"]}
            cameras = read_cameras(sparse_files["cameras"].read_bytes())
            all_rows = read_images(sparse_files["images"].read_bytes())
            photos = catalog.image_map(images, [r["name"] for r in all_rows])
            rows = [r for r in all_rows if r["name"] in photos]
            if frame_ids:
                requested = list(frame_ids)
                if len(set(requested)) != len(requested):
                    raise ContractError("Duplicate frame IDs")
                by_id = {r["id"]: r for r in rows}
                if set(requested) - by_id.keys():
                    raise ContractError(f"Unknown or unavailable frame IDs: {sorted(set(requested)-by_id.keys())}")
                rows = [by_id[i] for i in requested]
                photos = {r["name"]: photos[r["name"]] for r in rows}
            for row in rows:
                camera_parameters(cameras[row["camera_id"]])
                with Image.open(photos[row["name"]]) as image:
                    if image.size != tuple(cameras[row["camera_id"]]["size"]):
                        raise ContractError(f"Image dimensions disagree with COLMAP: {row['name']}")
            transform = native_transform(catalog, sparse["coordinate_system"], geometry["coordinate_system"])
    if frame_ids and not rows:
        raise ContractError("Frame selection requires calibrated photos")
    report = dict(passed=True, **result_status(execution="ready", content="passed",
        next_steps=["Import to a new directory, then prepare or init-task"]),
        catalog=catalog.summary(), selected_geometry=geometry["id"], geometry=info,
        selected_sparse=sparse["id"] if sparse else None, selected_images=images["id"] if images else None,
        frame_ids=[r["id"] for r in rows], frame_names=[r["name"] for r in rows],
        coordinates=coords, camera_transform_native=transform.tolist(), max_side=max_side,
        warnings=["Coordinate scale/up are producer declarations; physical accuracy is not evaluated",
                  "Source quality and producer extensions are retained; import does not promote quality"])
    return report, (catalog, geometry, path, sparse_files, photos, rows, cameras, transform)


def import_description(document, output=None, roots=None, geometry_id=None, sparse_id=None,
                       images_id=None, frame_ids=(), no_photos=False, max_side=768, preflight=False):
    report, context = inspect_description(document, roots, geometry_id, sparse_id, images_id,
                                           frame_ids, no_photos, max_side)
    if preflight:
        return report
    if output is None or Path(output).exists():
        raise ContractError("Import requires --out pointing to a new directory")
    output = Path(output).resolve()
    catalog, geometry, path, sparse_files, photos, rows, cameras, transform = context
    # Native COLMAP remains in its own coordinates in the archived exchange; converted frames below
    # explicitly record the transform into the chosen geometry coordinate system.
    output.mkdir(parents=True)
    (output / "source").mkdir()
    geom_copy = output / "source" / ("geometry" + path.suffix)
    shutil.copyfile(path, geom_copy)
    retained = []
    for role, original in sparse_files.items():
        dest = output / "source" / (role + ".bin")
        shutil.copyfile(original, dest)
        retained.append({"path": dest.relative_to(output).as_posix(), "sha256": sha256(dest)})
    quality_files = [catalog.resolve(f["ref"]) for a in catalog.assets.values()
                     if a["type"] == "validation_report" for f in a["files"]]
    for index, original in enumerate(quality_files):
        dest = output / "source" / f"quality-{index}-{original.name}"
        shutil.copyfile(original, dest)
        retained.append({"path": dest.relative_to(output).as_posix(), "sha256": sha256(dest)})
    frames = []
    (output / "rgb").mkdir()
    for index, row in enumerate(rows):
        camera = cameras[row["camera_id"]]
        original = output / "source/images" / row["name"]
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(photos[row["name"]], original)
        with Image.open(original) as image:
            rectified, k = rectify(image, camera, max_side)
        relative = f"rgb/{index:06}.png"
        rectified.save(output / relative)
        pose = transform @ row["pose"]
        scale = np.cbrt(np.linalg.det(transform[:3, :3]))
        pose[:3, :3] /= scale
        frames.append(dict(id=row["id"], size=list(rectified.size),
            image=dict(root="bundle", path=relative, sha256=sha256(output / relative)),
            camera=dict(model="pinhole_rectified", axes="x_right_y_down_z_forward",
                        pixel_convention="top_left_integer_centres", K=k.tolist(), T_source_from_camera=pose.tolist()),
            sources=[dict(kind="legacy_record", reference=dict(root="bundle", path="source/images.bin"),
                          record_id=row["id"], note=row["name"])]))
    coordinates = {k: report["coordinates"][k] for k in ("handedness", "up_axis", "metres_per_unit")}
    frame_doc = dict(kind="frames", schema_version=1, scene_id=catalog.scene_id, frames=frames)
    scene = dict(kind="scene", schema_version=1, scene_id=catalog.scene_id, source_coordinates=coordinates,
        geometry=[dict(id="imported", type=geometry["type"], coordinate_frame="source",
                       has_color=report["geometry"]["has_color"], asset=dict(root="bundle", path=geom_copy.relative_to(output).as_posix(), sha256=sha256(geom_copy)))],
        frames=dict(root="bundle", path="frames.json"))
    validate(scene); validate(frame_doc)
    atomic_json(output / "scene.json", scene)
    atomic_json(output / "frames.json", frame_doc)
    # Preserve upstream status and references separately from the narrow scene v1 schema.
    report.update(execution="completed", source_document_sha256=sha256(document),
                  retained_reports=retained, scene=str(output / "scene.json"),
                  next_steps=[f"restruct prepare {output / 'scene.json'} --out <new-job> --alignment "
                              + ("preserve" if coordinates["up_axis"] == "+Z" else "auto")])
    atomic_json(output / "import.json", report)
    return report
