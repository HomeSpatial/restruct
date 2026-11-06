import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from indoor_recon.contracts import (ContractError, project, read_json, resolve_asset,
    sha256, stage_eligibility, transform_points, unproject, validate, verify_lock,
    validate_scene_bundle, work_camera, write_json)


IDENTITY = np.eye(4).tolist()
SOURCE = {"kind": "measurement", "reference": {"root": "input", "path": "scan.ply"}}


def scene():
    return {"schema_version": 1, "kind": "scene", "scene_id": "office-independent",
        "source_coordinates": {"handedness": "right", "up_axis": "+Y", "metres_per_unit": 0.001},
        "geometry": [{"id": "cloud", "type": "point_cloud", "asset": {"root": "input", "path": "scan.ply"}, "coordinate_frame": "source", "has_color": True}],
        "alignment": {"root": "bundle", "path": "alignment.json"}}


def frames():
    return {"schema_version": 1, "kind": "frames", "scene_id": "office-independent", "frames": [{
        "id": "arbitrary-frame-id", "size": [640, 480], "image": {"root": "input", "path": "photos/a.jpg"},
        "camera": {"model": "pinhole_rectified", "axes": "x_right_y_down_z_forward", "pixel_convention": "top_left_integer_centres",
            "K": [[200, 0, 320], [0, 200, 240], [0, 0, 1]], "T_source_from_camera": IDENTITY}}]}


def proposal(nature="volume", size=None):
    return {"schema_version": 1, "kind": "proposals", "scene_id": "independent", "coordinate_frame": "work_z_up_metres", "backend": "fixture",
        "candidates": [{"id": "unrelated-id", "category": "wall" if nature == "surface" else "chair",
            "geometry": {"type": "obb", "nature": nature, "center_m": [1, 2, 3], "size_m": size or [1, 2, 3], "R_work_from_object": np.eye(3).tolist()}, "sources": [SOURCE]}]}


def model():
    pose = [[0, -1, 0, 3], [1, 0, 0, 4], [0, 0, 1, 5], [0, 0, 0, 1]]
    return {"schema_version": 1, "kind": "model", "scene_id": "independent", "coordinate_frame": "work_z_up_metres", "objects": [
        {"id": "a", "category": "wall", "geometry": {"type": "polygon_surface", "boundary_m": [[0, 0], [4, 0], [4, 3], [0, 3]], "T_work_from_object": pose}, "sources": [SOURCE]},
        {"id": "b", "category": "desk", "geometry": {"type": "box", "size_m": [1, 2, 3], "T_work_from_object": pose}, "sources": [SOURCE]}]}


class ContractTests(unittest.TestCase):
    def test_independent_point_cloud_and_ids_are_supported(self):
        validate(scene())
        validate(frames())
        self.assertEqual(stage_eligibility(scene(), frames())["boxer_input"], "eligible")

    def test_geometry_without_photos_preserves_geometry_stage(self):
        result = stage_eligibility(scene())
        self.assertEqual(result["geometry"], "eligible")
        self.assertEqual(result["photos"], "skipped")
        self.assertEqual(result["boxer_input"], "needs_input")

    def test_uncalibrated_photos_are_not_3d_detection_input(self):
        f = frames()
        del f["frames"][0]["camera"]
        result = stage_eligibility(scene(), f)
        self.assertEqual(result["photos"], "eligible")
        self.assertEqual(result["boxer_input"], "needs_input")

    def test_rgbd_can_be_input_without_mesh(self):
        s, f = scene(), frames()
        s["geometry"] = []
        f["frames"][0]["depth"] = {"asset": {"root": "input", "path": "depth/a.png"}, "metres_per_unit": 0.001, "measurement": "camera_z"}
        self.assertEqual(stage_eligibility(s, f)["spatiallm_input"], "eligible")

    def test_unknown_metric_scale_requires_input(self):
        s = scene()
        s["source_coordinates"]["metres_per_unit"] = None
        self.assertEqual(stage_eligibility(s)["geometry"], "needs_input")

    def test_work_frame_cloud_is_already_metric_and_aligned(self):
        s = scene()
        s["geometry"][0]["coordinate_frame"] = "work"
        s["source_coordinates"]["metres_per_unit"] = None
        del s["alignment"]
        self.assertEqual(stage_eligibility(s)["spatiallm_input"], "eligible")
        self.assertEqual(stage_eligibility(s, frames())["boxer_input"], "needs_input")

    def test_scene_frame_mismatch_is_rejected(self):
        f = frames()
        f["scene_id"] = "another-scene"
        with self.assertRaises(ContractError):
            stage_eligibility(scene(), f)

    def test_unit_conversion_then_rigid_alignment_for_camera(self):
        source = np.eye(4)
        source[:3, 3] = [1000, 2000, 3000]
        align = np.array([[0, -1, 0, 10], [1, 0, 0, 20], [0, 0, 1, 30], [0, 0, 0, 1]])
        pose = work_camera(source, align, 0.001)
        np.testing.assert_allclose(pose[:3, 3], [8, 21, 33], rtol=0, atol=1e-12)
        points = transform_points(np.array([[1000, 2000, 3000]]) * 0.001, align)
        np.testing.assert_allclose(points[0], pose[:3, 3], rtol=0, atol=1e-12)

    def test_hand_computed_projection_with_rotated_translated_camera(self):
        pose = np.array([[0, -1, 0, 10], [1, 0, 0, 20], [0, 0, 1, 30], [0, 0, 0, 1]])
        k = [[200, 0, 320], [0, 200, 240], [0, 0, 1]]
        uv, z = project([[9, 22, 34]], k, pose)
        np.testing.assert_allclose(uv, [[420, 290]], atol=1e-12, rtol=0)
        np.testing.assert_allclose(z, [4], atol=1e-12, rtol=0)
        np.testing.assert_allclose(unproject(uv, z, k, pose), [[9, 22, 34]], atol=1e-12, rtol=0)

    def test_behind_camera_and_invalid_depth_are_rejected(self):
        k = frames()["frames"][0]["camera"]["K"]
        with self.assertRaises(ContractError):
            project([[0, 0, -1]], k, IDENTITY)
        with self.assertRaises(ContractError):
            unproject([[0, 0]], [0], k, IDENTITY)

    def test_invalid_rigid_pose_and_camera_conventions_are_rejected(self):
        for key, value in [("axes", "z_backward"), ("T_source_from_camera", np.diag([2, 1, 1, 1]).tolist()), ("T_source_from_camera", np.diag([-1, 1, 1, 1]).tolist()), ("K", [[-1, 0, 0], [0, 1, 0], [0, 0, 1]])]:
            with self.subTest(key=key, value=value):
                f = frames()
                f["frames"][0]["camera"][key] = value
                with self.assertRaises(ContractError):
                    validate(f)

    def test_surface_candidate_can_have_zero_thickness(self):
        validate(proposal("surface", [2, 0, 3]))

    def test_volume_candidate_and_final_solid_need_positive_extent(self):
        with self.assertRaises(ContractError):
            validate(proposal("volume", [2, 0, 3]))
        m = model()
        m["objects"][1]["geometry"]["size_m"][0] = 0
        with self.assertRaises(ContractError):
            validate(m)

    def test_non_axis_aligned_model_surface_and_open_boundary_are_valid(self):
        m = model()
        angle = np.pi / 7
        m["objects"][0]["geometry"]["T_work_from_object"] = [[np.cos(angle), 0, np.sin(angle), 0], [0, 1, 0, 0], [-np.sin(angle), 0, np.cos(angle), 0], [0, 0, 0, 1]]
        validate(m)

    def test_duplicate_ids_dangling_and_cyclic_relationships_are_rejected(self):
        f = frames()
        f["frames"].append(copy.deepcopy(f["frames"][0]))
        with self.assertRaises(ContractError):
            validate(f)
        m = model()
        m["objects"][0]["host_id"] = "missing"
        with self.assertRaises(ContractError):
            validate(m)
        m = model()
        m["objects"][0]["parent_id"] = "b"
        m["objects"][1]["parent_id"] = "a"
        with self.assertRaises(ContractError):
            validate(m)

    def test_nonfinite_and_unknown_fields_are_rejected(self):
        f = frames()
        f["frames"][0]["camera"]["K"][0][0] = float("nan")
        with self.assertRaises(ContractError):
            validate(f)
        s = scene()
        s["geometery"] = []
        with self.assertRaises(ContractError):
            validate(s)

    def test_frozen_lock_detects_changed_and_missing_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "data.bin"
            path.write_bytes(b"original")
            lock = {"schema_version": 1, "kind": "asset_lock", "scene_id": "fixture", "files": [{"root": "data", "path": "data.bin", "bytes": path.stat().st_size, "sha256": sha256(path)}]}
            self.assertTrue(verify_lock(lock, {"data": root})["passed"])
            path.write_bytes(b"modified")
            self.assertFalse(verify_lock(lock, {"data": root})["passed"])
            path.unlink()
            self.assertFalse(verify_lock(lock, {"data": root})["passed"])

    def test_asset_binding_survives_relocation_and_rejects_machine_paths(self):
        asset = {"root": "scan", "path": "rgb/1.png"}
        with tempfile.TemporaryDirectory() as temp:
            target = resolve_asset(asset, {"scan": Path(temp) / "relocated"})
            self.assertEqual(target, (Path(temp) / "relocated/rgb/1.png").resolve())
        for name in ("../scan.ply", "/scan.ply", "C:/scan.ply", "rgb\\1.png"):
            with self.subTest(name=name), self.assertRaises(ContractError):
                resolve_asset({"root": "scan", "path": name}, {"scan": Path.cwd()})

    def test_strict_json_rejects_legacy_infinity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.json"
            path.write_text('{"point": Infinity}', encoding="utf-8")
            with self.assertRaises(ContractError):
                read_json(path)

    def test_assembly_parent_and_child_can_describe_same_ownership(self):
        m = model()
        m["objects"][0]["geometry"] = {"type": "assembly", "children": ["b"]}
        m["objects"][1]["parent_id"] = "a"
        validate(m)

    def test_bundle_checks_up_axis_and_referenced_scene_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scan.ply").write_text("fixture", encoding="utf-8")
            s = scene()
            a = {"schema_version": 1, "kind": "alignment", "scene_id": s["scene_id"],
                "T_work_from_source_m": IDENTITY, "method": "fixture", "source": SOURCE}
            write_json(root / "alignment.json", a)
            roots = {"input": root, "bundle": root}
            with self.assertRaisesRegex(ContractError, "up axis"):
                validate_scene_bundle(s, roots)
            a["T_work_from_source_m"] = [[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]
            write_json(root / "alignment.json", a)
            self.assertEqual(validate_scene_bundle(s, roots)["geometry"], "eligible")
            a["scene_id"] = "mismatched"
            write_json(root / "alignment.json", a)
            with self.assertRaisesRegex(ContractError, "scene_id"):
                validate_scene_bundle(s, roots)


if __name__ == "__main__":
    unittest.main()
