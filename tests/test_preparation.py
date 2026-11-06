import copy
import io
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
from PIL import Image

from indoor_recon.colmap import camera_parameters, distort, read_cameras, read_images, rectify
from indoor_recon.contracts import ContractError, read_json, sha256
from indoor_recon.jobs import Stage, atomic_json
from indoor_recon.perception import BOXER_WEIGHTS, normalize, predict, reconcile, wsl_path
from indoor_recon.preparation import estimate_alignment, partition, prepare, rotation_between_up, visible_points
from indoor_recon.workers.infer import Units


class GeometryTests(unittest.TestCase):
    def test_all_declared_up_axes_and_yaw_preserve_lengths(self):
        for up in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            r = rotation_between_up(up)
            vector = np.eye(3)["XYZ".index(up[1])] * (1 if up[0] == "+" else -1)
            np.testing.assert_allclose(r@vector, [0, 0, 1], atol=1e-12)
            np.testing.assert_allclose(r.T@r, np.eye(3), atol=1e-12)
            self.assertAlmostEqual(np.linalg.det(r), 1)
        a = np.radians(30)
        normals = np.array([[np.cos(a), np.sin(a), 0], [-np.sin(a), np.cos(a), 0], [0, 0, 1]])
        t, quality = estimate_alignment(normals, [4, 4, 20], "+Z")
        np.testing.assert_allclose(t[:3,:3]@normals[0], [1, 0, 0], atol=1e-12)
        self.assertAlmostEqual(quality["four_angle_concentration"], 1)
        with self.assertRaises(ContractError):
            rotation_between_up("unknown")

    def test_large_multifloor_cloud_is_covered_once_by_ownership(self):
        points = np.array(list(__import__("itertools").product(np.arange(0, 70, .5), [0, 1], [-2, 0, 2])))
        regions = partition(points, [0], 32, 2)
        covered, owned = np.zeros(len(points), int), np.zeros(len(points), int)
        for region in regions:
            covered[region["indices"]] += 1
            lo, hi = region["ownership_bounds_m"]
            selected = np.all((points >= lo) & (points < hi), axis=1)
            # Ownership must also respect the floor slab.
            selected &= points[:, 2] < 0 if region["floor_id"] == "floor-0" else points[:, 2] >= 0
            owned += selected
            self.assertLess(np.ptp(points[region["indices"]], axis=0).max(), 32)
        self.assertTrue((covered >= 1).all())
        np.testing.assert_array_equal(owned, np.ones(len(points)))

    def test_cloud_projection_keeps_nearest_visible_point(self):
        points = np.array([[0,0,2], [0,0,4], [0,0,-2], [10,0,1]], float)
        xyz, uv, depth = visible_points(points, np.eye(3), np.eye(4), [4,4], 1)
        np.testing.assert_array_equal(xyz, [[0,0,2]])
        np.testing.assert_array_equal(depth, [2])

    def test_seam_merge_keeps_provenance_and_distinct_walls(self):
        def candidate(name, x):
            return {"id": name, "category": "wall", "floor_id": "floor-0", "sources": [{"record_id": name, "reference":{"path":name+".json"}}],
                "geometry": {"center_m": [x,0,1], "size_m": [2,0,2], "R_work_from_object": np.eye(3).tolist()}}
        kept, merges = reconcile([candidate("a", 0), candidate("b", .02), candidate("c", 2)])
        self.assertEqual([r["id"] for r in kept], ["a", "c"])
        self.assertEqual(len(kept[0]["sources"]), 2)
        self.assertEqual(merges, [{"kept":"a", "duplicate":"b"}])

    def test_wsl_paths_do_not_embed_command_text(self):
        self.assertEqual(wsl_path("E:/data/my scene/input.json"), "/mnt/e/data/my scene/input.json")
        with self.assertRaises(ContractError):
            wsl_path("relative/input.json")

    def test_same_region_candidates_are_preserved_for_review(self):
        candidate = {"id":"a", "category":"wall", "sources":[{"reference":{"path":"same-region.json"}}],
            "geometry":{"center_m":[0,0,0],"size_m":[2,0,2],"R_work_from_object":np.eye(3).tolist()}}
        second = copy.deepcopy(candidate);second["id"]="b"
        kept,merged=reconcile([candidate,second])
        self.assertEqual(len(kept),2)
        self.assertEqual(merged,[])

    def test_equivalent_box_axes_merge_across_regions(self):
        a={"id":"a","category":"cabinet","sources":[{"reference":{"path":"region-a.json"}}],
            "geometry":{"center_m":[0,0,0],"size_m":[2,1,3],"R_work_from_object":np.eye(3).tolist()}}
        b=copy.deepcopy(a);b["id"]="b";b["sources"]=[{"reference":{"path":"region-b.json"}}]
        b["geometry"]["R_work_from_object"]=np.diag([-1,-1,1]).tolist()
        kept,merged=reconcile([a,b])
        self.assertEqual(len(kept),1)
        self.assertEqual(len(kept[0]["sources"]),2)


class ColmapTests(unittest.TestCase):
    def test_binary_ids_and_world_to_camera_inversion(self):
        cameras = struct.pack("<QiiQQdddd", 1, 87, 1, 4, 3, 2, 2, 2, 1.5)
        self.assertEqual(read_cameras(cameras)[87]["model"], "PINHOLE")
        images = struct.pack("<Qidddddddi", 1, 950, 1, 0, 0, 0, -3, -4, -5, 87) + b"photo.jpg\0" + struct.pack("<Qddq", 1, 2.5, 1.5, 120)
        image = read_images(images)[0]
        self.assertEqual(image["id"], "950")
        np.testing.assert_array_equal(image["pose"][:3,3], [3,4,5])
        np.testing.assert_array_equal(image["point_ids"], [120])
        with self.assertRaises(ContractError):
            read_images(images[:-1])

    def test_rectification_pixels_and_resize_intrinsics(self):
        camera = {"model":"PINHOLE", "size":[8,6], "params":[4,4,4,3]}
        image = Image.fromarray(np.arange(8*6*3, dtype=np.uint8).reshape(6,8,3))
        rectified, k = rectify(image, camera, 8)
        np.testing.assert_array_equal(np.asarray(rectified), np.asarray(image))
        np.testing.assert_array_equal(k, [[4,0,3.5], [0,4,2.5], [0,0,1]])
        small, k2 = rectify(image, camera, 4)
        self.assertEqual(small.size, (4,3))
        np.testing.assert_array_equal(k2, [[2,0,1.5],[0,2,1],[0,0,1]])
        np.testing.assert_allclose(distort([[.5,.2]], [.1,0,0,0]), [[.5145,.2058]])
        with self.assertRaises(ContractError):
            camera_parameters(dict(camera, model="OPENCV_FISHEYE"))


class JobTests(unittest.TestCase):
    def test_completed_prediction_rechecks_original_rgb_before_cache_hit(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);image=base/"image.png"
            Image.new("RGB",(2,2),(255,0,0)).save(image)
            prepared={"scene_id":"test", "has_color":True,"frames":[{"id":"one","size":[2,2],"image":{"root":"bundle","path":"image.png"},"points_path":"one.npz"}],"input_hashes":{"bundle:image.png":sha256(image)}}
            atomic_json(base/"job/prepare/prepared.json",prepared)
            atomic_json(base/"job/prepare/state.json",{"status":"complete","files":{"prepared.json":sha256(base/"job/prepare/prepared.json")}})
            atomic_json(base/"job/bindings.local.json",{"bundle":str(base)})
            source=base/"backend";source.mkdir();(source/"backend.py").write_text("# test backend")
            weights=base/"weights";weights.mkdir()
            for name in BOXER_WEIGHTS:(weights/name).write_bytes(b"test fixture")
            atomic_json(base/"labels.json",{"labels":["chair"]})
            runtime={"boxer":{"python":__import__("sys").executable,"source_dir":str(source),"weights_dir":str(weights)}}
            def fake_worker(*args,**kwargs):
                if args[0][2].endswith("probe.py"):
                    return SimpleNamespace(returncode=0, stdout='RECON_PROBE={"passed":true}', stderr="")
                atomic_json(base/"job/boxer/fused-predictions.json",{"objects":[]})
                return SimpleNamespace(returncode=0)
            with patch("indoor_recon.perception.subprocess.run",side_effect=fake_worker) as worker:
                predict(base/"job",runtime,layout="none",labels_file=base/"labels.json")
                self.assertTrue(predict(base/"job",runtime,layout="none",labels_file=base/"labels.json",resume=True)["stages"]["boxer"]["resumed"])
                Image.new("RGB",(2,2),(0,0,255)).save(image)
                with self.assertRaisesRegex(ContractError,"RGB input changed"):
                    predict(base/"job",runtime,layout="none",labels_file=base/"labels.json",resume=True)
                self.assertEqual(sum(not call.args[0][2].endswith("probe.py") for call in worker.call_args_list),1)

    def test_checkpoint_resume_changed_input_and_tamper(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Stage(folder, "input-v1")
            atomic_json(Path(folder)/"out.json", {"value":1})
            stage.record("out.json")
            stage.complete()
            self.assertTrue(Stage(folder,"input-v1",True).valid("out.json"))
            with self.assertRaises(ContractError):
                Stage(folder, "input-v2", True)
            atomic_json(Path(folder)/"out.json", {"value":2})
            with self.assertRaises(ContractError):
                Stage(folder, "input-v1", True)

    def test_interrupted_worker_resumes_only_completed_units(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Units(folder)
            worker.record("frames/000000.json", {"frame_id":"arbitrary", "detections":[]})
            resumed = Units(folder)
            self.assertEqual(resumed.cached("frames/000000.json")["frame_id"], "arbitrary")
            self.assertIsNone(resumed.cached("frames/000001.json"))
            resumed.record("frames/000001.json", {"frame_id":"second", "detections":[]})
            self.assertEqual(len(Units(folder).hashes), 2)
            atomic_json(Path(folder)/"frames/000000.json", {})
            with self.assertRaises(ValueError):
                Units(folder)

    def test_point_cloud_without_photos_relocation_and_skip(self):
        import open3d as o3d
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base/"source"; source.mkdir()
            cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector([[0,0,0],[1,0,0],[0,1,0]]))
            o3d.io.write_point_cloud(str(source/"cloud.ply"), cloud)
            scene = {"schema_version":1,"kind":"scene","scene_id":"pc-only", "source_coordinates":{"handedness":"right","up_axis":"+Z","metres_per_unit":1},
                "geometry":[{"id":"pc","type":"point_cloud","asset":{"root":"bundle","path":"cloud.ply"},"coordinate_frame":"source","has_color":False}]}
            atomic_json(source/"scene.json",scene)
            result = prepare(source/"scene.json", base/"job", {})
            self.assertEqual(result["frames"], 0)
            before = sha256(base/"job/prepare/global.ply")
            shutil.copytree(source, base/"moved")
            self.assertTrue(prepare(base/"moved/scene.json",base/"job",{},resume=True)["resumed"])
            self.assertEqual(sha256(base/"job/prepare/global.ply"),before)
            summary = predict(base/"job",{})
            self.assertEqual(summary["stages"]["boxer"]["status"],"skipped")
            self.assertEqual(summary["stages"]["spatiallm"]["status"],"skipped")

    def test_metric_rgbd_without_mesh(self):
        import open3d as o3d
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            Image.fromarray(np.full((2,2),1000,dtype=np.uint16)).save(base/"depth.png")
            Image.new("RGB",(2,2),(255,0,0)).save(base/"rgb.png")
            frame = {"id":"not-a-number","size":[2,2],"image":{"root":"bundle","path":"rgb.png"},
                "depth":{"asset":{"root":"bundle","path":"depth.png"},"metres_per_unit":.001,"measurement":"camera_z"},
                "camera":{"model":"pinhole_rectified","axes":"x_right_y_down_z_forward","pixel_convention":"top_left_integer_centres", "K":np.eye(3).tolist(),"T_source_from_camera":np.eye(4).tolist()}}
            atomic_json(base/"frames.json",{"schema_version":1,"kind":"frames","scene_id":"rgbd","frames":[frame]})
            scene = {"schema_version":1,"kind":"scene","scene_id":"rgbd","source_coordinates":{"handedness":"right","up_axis":"+Z","metres_per_unit":1},"geometry":[],"frames":{"root":"bundle","path":"frames.json"}}
            atomic_json(base/"scene.json",scene)
            result = prepare(base/"scene.json",base/"job",{}, {"depth_stride":1})
            self.assertEqual(result["points"],4)
            points = np.asarray(o3d.io.read_point_cloud(str(base/"job/prepare/global.ply")).points)
            np.testing.assert_allclose(sorted(points.tolist()), [[0,0,1],[0,1,1],[1,0,1],[1,1,1]])
            self.assertEqual(read_json(base/"job/prepare/prepared.json")["frames"][0]["id"],"not-a-number")


if __name__ == "__main__":
    unittest.main()
