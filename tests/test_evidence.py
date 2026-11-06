import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from indoor_recon.contracts import ContractError, sha256, transform_points
from indoor_recon.evidence import Evidence, fit_plane, run


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        import open3d as o3d
        import trimesh
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / "source"
        self.prepared = source / "prepare"
        self.prepared.mkdir(parents=True)
        self.pose = np.eye(4)
        a = .37
        self.pose[:2, :2] = [[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]
        self.pose[:3, 3] = [1, 2, .3]
        self.k = np.array([[100., 0, 80], [0, 100, 60], [0, 0, 1]])
        grid = np.array([[x, y, 2] for x in np.linspace(-.5, .5, 31) for y in np.linspace(-.5, .5, 31)])
        self.points = transform_points(grid, self.pose)
        cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(self.points))
        o3d.io.write_point_cloud(str(self.prepared / "global.ply"), cloud)
        np.savez_compressed(self.prepared / "frame.npz", K=self.k, T=self.pose,
                            points=self.points, uv=grid[:, :2]*50+[80,60], depth=np.full(len(grid), 2.))
        Image.new("RGB", (160, 120), (80, 120, 160)).save(source / "image.png")
        mesh = trimesh.Trimesh([[-10,-10,20],[10,-10,20],[10,10,20],[-10,10,20]], [[0,1,2],[0,2,3]], process=False)
        mesh.export(source / "scan.ply")
        prepared = {"scene_id": "fixture", "point_source": "mesh", "alignment": self.pose.tolist(),
                    "frames": [{"id": "one", "image": {"root": "source", "path": "image.png", "sha256": sha256(source/"image.png")},
                                "size": [160, 120], "points_path": "frame.npz"}]}
        (self.prepared / "prepared.json").write_text(json.dumps(prepared))
        files = {p.name: sha256(p) for p in self.prepared.iterdir()}
        (self.prepared / "state.json").write_text(json.dumps({"files": files}))
        self.config = {"schema_version": 1, "kind": "evidence", "scene_id": "fixture",
                       "prepared": {"root": "source", "path": "prepare/prepared.json", "sha256": files["prepared.json"]},
                       "scan": {"asset": {"root": "source", "path": "scan.ply"}, "metres_per_unit": .1,
                                "T_work_from_source_m": self.pose.tolist()}}
        self.roots = {"source": source}
        self.evidence = Evidence(self.config, self.roots)
        self.box = np.array([self.points.min(0)-.03, self.points.max(0)+.03]).tolist()

    def test_photo_ranking_returns_projection_and_scan_support(self):
        result = self.evidence.photos(self.box)
        row = result["photos"][0]
        self.assertEqual(row["frame_id"], "one")
        self.assertEqual(row["scan_first_hit_points_in_bounds"], len(self.points))
        self.assertGreater(row["projected_area_px2"], 2000)
        self.assertIn(str(self.prepared/"frame.npz"), self.evidence.inputs)

    def test_crop_preserves_pixel_ray_and_pins_source_image(self):
        out = self.root / "crop"
        out.mkdir()
        result = self.evidence.crop(out, "one", crop_xyxy=[40,30,120,90], margin_px=0)
        with Image.open(out/"crop.png") as image:
            self.assertEqual(image.size, (80,60))
        ray_original = np.linalg.inv(self.k) @ [80,60,1]
        ray_crop = np.linalg.inv(result["K_crop"]) @ [40,30,1]
        np.testing.assert_allclose(ray_original, ray_crop)
        self.assertEqual(result["crop_xyxy"], [40,30,120,90])
        self.assertTrue(any(p.endswith("image.png") for p in self.evidence.inputs))

    def test_pixel_ray_applies_camera_pose_mesh_scale_and_alignment(self):
        result = self.evidence.pixel_rays("one", [[80,60]])
        hit = result["rays"][0]
        np.testing.assert_allclose(hit["point_work_m"], self.pose[:3,3]+[0,0,2], atol=2e-6)
        self.assertAlmostEqual(hit["distance_m"], 2, places=5)
        limited = self.evidence.pixel_rays("one", [[80,60]], max_distance_m=1)
        self.assertEqual(limited["misses"], 1)
        self.assertIsNone(limited["rays"][0]["point_work_m"])

    def test_miss_and_nonunit_direction_are_explicit(self):
        result = self.evidence.raycast([[1,2,3], [50,50,3]], [[0,0,-9], [0,0,-1]])
        self.assertTrue(result["rays"][0]["hit"])
        self.assertFalse(result["rays"][1]["hit"])
        self.assertIsNone(result["rays"][1]["distance_m"])

    def test_plane_fit_is_robust_to_outliers_and_retains_residuals(self):
        rng = np.random.default_rng(7)
        xy = rng.uniform(-1, 1, (200,2))
        plane = np.column_stack((xy, .2*xy[:,0]-.3*xy[:,1]+1))
        points = np.concatenate((plane, rng.uniform(-2,2,(20,3))))
        result, residuals = fit_plane(points, .01)
        self.assertGreaterEqual(result["inliers"], 200)
        expected = np.array([-.2,.3,1]); expected /= np.linalg.norm(expected)
        self.assertGreater(np.dot(result["normal_work"], expected), .9999)
        self.assertEqual(len(residuals), len(points))

    def test_section_retains_full_selected_query_points(self):
        out = self.root / "section"
        out.mkdir()
        result = self.evidence.section(out, self.box, "z", 2.3, .02)
        self.assertEqual(result["points"], len(self.points))
        with np.load(out/result["data"]) as data:
            np.testing.assert_allclose(data["points_work_m"], self.points)

    def test_read_only_run_rejects_source_output_and_preserves_hashes(self):
        config, query = self.root/"config.json", self.root/"query.json"
        config.write_text(json.dumps(self.config)); query.write_text(json.dumps({"op":"pixel_rays","frame_id":"one","pixels":[[80,60]]}))
        before = {p: sha256(p) for p in self.roots["source"].rglob("*") if p.is_file()}
        result = run(config, query, self.root/"result", self.roots)
        self.assertTrue(result["passed"])
        self.assertTrue((self.root/"result/evidence.json").is_file())
        self.assertEqual(before, {p: sha256(p) for p in before})
        with self.assertRaises(ContractError):
            run(config, query, self.roots["source"]/"bad", self.roots)
        with self.assertRaises(ContractError):
            run(config, query, self.root/"result", self.roots)

    def test_cli_records_activity_beside_the_output(self):
        from contextlib import redirect_stdout
        import io
        from indoor_recon.__main__ import main
        config, query = self.root/"config.json", self.root/"query.json"
        config.write_text(json.dumps(self.config)); query.write_text(json.dumps({"op":"pixel_rays","frame_id":"one","pixels":[[80,60]]}))
        argv = ["evidence", str(config), str(query), "--root", f"source={self.roots['source']}"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(argv + ["--out", str(self.root/"ok")]), 0)
            # the output directory already exists: the run fails, the existing directory gets no record
            self.assertEqual(main(argv + ["--out", str(self.root/"ok")]), 1)
        record = json.loads((self.root/"ok/activity.json").read_text(encoding="utf-8"))
        self.assertEqual((record["command"], record["passed"], record["error"]), ("evidence", True, None))
        self.assertEqual(record["inputs"], {"query": str(query.resolve())})
        self.assertGreaterEqual(record["seconds"], 0)

    def test_changed_prepared_or_image_assets_fail_before_use(self):
        (self.prepared/"frame.npz").write_bytes(b"changed")
        with self.assertRaisesRegex(ContractError, "hash changed"):
            self.evidence.photos(self.box)
        self.evidence = Evidence(self.config, self.roots)
        self.config["scene_id"] = "wrong"
        with self.assertRaisesRegex(ContractError, "scene differs"):
            Evidence(self.config, self.roots)

    def test_point_only_config_declares_missing_mesh_rays(self):
        del self.config["scan"]
        evidence = Evidence(self.config, self.roots)
        self.assertFalse(evidence.query({"op":"info"}, self.root)["mesh_rays"])
        with self.assertRaisesRegex(ContractError, "unavailable"):
            evidence.pixel_rays("one", [[80,60]])

    def test_appearance_splits_materials_by_hue_not_by_lighting(self):
        # Left half of the photo wood, right half grey; both darken towards the bottom like falling-off light.
        shade = np.linspace(1, .45, 120)[:, None, None]
        pixels = np.empty((120, 160, 3))
        pixels[:, :80] = [170, 120, 60]
        pixels[:, 80:] = [140, 140, 132]
        source = self.root / "source"
        Image.fromarray((pixels*shade).astype(np.uint8)).save(source / "image.png")
        prepared = json.loads((self.prepared / "prepared.json").read_text())
        prepared["frames"][0]["image"]["sha256"] = sha256(source / "image.png")
        (self.prepared / "prepared.json").write_text(json.dumps(prepared))
        files = {p.name: sha256(p) for p in self.prepared.iterdir() if p.name != "state.json"}
        (self.prepared / "state.json").write_text(json.dumps({"files": files}))
        self.config["prepared"]["sha256"] = files["prepared.json"]
        evidence = Evidence(self.config, self.roots)
        out = self.root / "appearance"
        out.mkdir()
        box = [[-1, 0, 2.2], [3, 4, 2.4]]
        result = evidence.query({"op": "appearance", "bounds_work_m": box, "normal_work": [0, 0, 1]}, out)
        self.assertEqual(len(result["regions"]), 2)
        warm = max(result["regions"], key=lambda r: r["chromaticity_rg"][0])
        grey = min(result["regions"], key=lambda r: r["chromaticity_rg"][0])
        self.assertAlmostEqual(warm["area_m2"], 2, delta=.25)
        self.assertAlmostEqual(grey["area_m2"], 2, delta=.25)
        self.assertGreater(warm["colour_srgb_255"][0]-warm["colour_srgb_255"][2], 40)
        self.assertLess(abs(grey["colour_srgb_255"][0]-grey["colour_srgb_255"][2]), 12)
        [edge] = result["boundaries"]
        camera_x = (np.array(edge["segment_work_m"])-self.pose[:3, 3]) @ self.pose[:3, 0]
        np.testing.assert_allclose(camera_x, 0, atol=.06)  # the photo's centre column
        self.assertTrue((out / "appearance.png").is_file() and (out / "appearance.npz").is_file())

    def test_appearance_evens_out_frame_white_balance_and_takes_a_white_reference(self):
        # Two photos from the same pose: the second with a warm cast and brighter exposure (auto white balance).
        pixels = np.empty((120, 160, 3))
        pixels[:, :80] = [170, 120, 60]
        pixels[:, 80:] = [140, 140, 132]
        source = self.root / "source"
        Image.fromarray(pixels.astype(np.uint8)).save(source / "image.png")
        linear = (pixels/255)**2.2*[1.4, 1.1, .8]
        Image.fromarray((np.clip(linear, 0, 1)**(1/2.2)*255).astype(np.uint8)).save(source / "warm.png")
        prepared = json.loads((self.prepared / "prepared.json").read_text())
        prepared["frames"][0]["image"]["sha256"] = sha256(source / "image.png")
        prepared["frames"].append({"id": "two", "image": {"root": "source", "path": "warm.png", "sha256": sha256(source / "warm.png")},
                                   "size": [160, 120], "points_path": "frame.npz"})
        (self.prepared / "prepared.json").write_text(json.dumps(prepared))
        files = {p.name: sha256(p) for p in self.prepared.iterdir() if p.name != "state.json"}
        (self.prepared / "state.json").write_text(json.dumps({"files": files}))
        self.config["prepared"]["sha256"] = files["prepared.json"]
        evidence = Evidence(self.config, self.roots)
        strip = transform_points(np.array([[x, y, 2] for x in (.6, 1) for y in (-.2, .2)]), self.pose)  # inside the grey half
        reference = {"bounds_work_m": [(strip.min(0)-[0, 0, .1]).tolist(), (strip.max(0)+[0, 0, .1]).tolist()],
                     "normal_work": [0, 0, 1], "colour_srgb_255": [200, 200, 200]}
        box = [[-1, 0, 2.2], [3, 4, 2.4]]
        out = self.root / "balanced"
        out.mkdir()
        result = evidence.query({"op": "appearance", "bounds_work_m": box, "normal_work": [0, 0, 1],
                                 "white_reference": reference}, out)
        self.assertEqual(len(result["regions"]), 2)
        gains = result["frame_gain_rgb"]
        np.testing.assert_allclose(np.array(gains["two"])/np.array(gains["one"]), [1.4, 1.1, .8], rtol=.05)
        grey = min(result["regions"], key=lambda r: r["chromaticity_rg"][0])
        warm = max(result["regions"], key=lambda r: r["chromaticity_rg"][0])
        np.testing.assert_allclose(grey["colour_srgb_255"], [200, 200, 200], atol=6)
        self.assertGreater(warm["colour_srgb_255"][0]-warm["colour_srgb_255"][2], 40)
        self.assertEqual(result["white_reference"]["colour_srgb_255"], [200, 200, 200])
        self.assertTrue(result["reliability"]["usable"])
        # The warm half named as white: the scale is extreme and the surface clips, so the colours are not usable.
        warm_strip = transform_points(np.array([[x, y, 2] for x in (-.6, -1) for y in (-.2, .2)]), self.pose)
        wrong = {"bounds_work_m": [(warm_strip.min(0)-[0, 0, .1]).tolist(), (warm_strip.max(0)+[0, 0, .1]).tolist()],
                 "normal_work": [0, 0, 1]}
        out = self.root / "wrong"
        out.mkdir()
        bad = evidence.query({"op": "appearance", "bounds_work_m": box, "normal_work": [0, 0, 1],
                              "white_reference": wrong}, out)["reliability"]
        self.assertFalse(bad["usable"])
        self.assertIn("reference_scale_extreme", [b["code"] for b in bad["blocking"]])

    def test_invalid_camera_pixel_ray_and_geometry_queries_are_rejected(self):
        for pixel in ([[160,60]], [[-1,0]], [[1,float("nan")]]):
            with self.assertRaises(ContractError):
                self.evidence.pixel_rays("one", pixel)
        with self.assertRaises(ContractError):
            self.evidence.raycast([[0,0,0]], [[0,0,0]])
        with self.assertRaises(ContractError):
            self.evidence.local_points([[1,2,3],[0,1,2]])


if __name__ == "__main__":
    unittest.main()
