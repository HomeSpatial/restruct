"""Model-wide diagnostics and authoring helpers on small synthetic scenes with known answers."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from indoor_recon.authoring import ModelBuilder, source
from indoor_recon.glb import WORK_FROM_GLTF
from indoor_recon import model_checks as checks


def box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    mesh = trimesh.creation.box(extents=hi-lo)
    mesh.apply_translation((lo+hi)/2)
    return mesh


def objects(**meshes):
    result = {}
    for name, value in meshes.items():
        mesh, category = value if isinstance(value, tuple) else (value, "furniture")
        name = name.replace("__", "/")
        result[name] = {"xyz": np.asarray(mesh.vertices, np.float32), "faces": np.asarray(mesh.faces, np.uint32),
                        "category": category, "parent_id": "", "host_id": ""}
    return result


def room(gap=None):
    """Closed 4 x 3 x 2.5 m shell of 0.1 m slabs; optional ceiling gap [x0, x1]."""
    parts = {"floor": (box([-.1, -.1, -.1], [4.1, 3.1, 0]), "floor"),
             "wall_s": (box([-.1, -.1, 0], [4.1, 0, 2.5]), "wall"), "wall_n": (box([-.1, 3, 0], [4.1, 3.1, 2.5]), "wall"),
             "wall_w": (box([-.1, 0, 0], [0, 3, 2.5]), "wall"), "wall_e": (box([4, 0, 0], [4.1, 3, 2.5]), "wall")}
    if gap is None:
        parts["ceiling"] = (box([-.1, -.1, 2.5], [4.1, 3.1, 2.6]), "ceiling")
    else:
        parts["ceiling_a"] = (box([-.1, -.1, 2.5], [gap[0], 3.1, 2.6]), "ceiling")
        parts["ceiling_b"] = (box([gap[1], -.1, 2.5], [4.1, 3.1, 2.6]), "ceiling")
    return objects(**parts)


class GeometryChecks(unittest.TestCase):
    def test_interference_reports_depth_and_ignores_contact_and_same_object_parts(self):
        found = checks.interference(objects(
            chair__seat=box([0, 0, 0], [.5, .5, .45]), table__top=box([.48, 0, .4], [1.5, 1, .45]),
            stool__seat=box([3, 0, 0], [3.4, .4, .5]), shelf__side=box([3.4, 0, 0], [3.5, .4, 1]),
            furniture__sofa__base=box([0, 2, 0], [2, 3, .4]), furniture__sofa__back=box([0, 2.8, 0], [2, 3, .9])), {})
        self.assertEqual([r["objects"] for r in found], [["chair/seat", "table/top"]])
        self.assertAlmostEqual(found[0]["depth_m"], .02, delta=.006)
        self.assertEqual(found[0]["kind"], "object_interference")

    def test_embedded_and_architecture_junction_kinds(self):
        found = checks.interference(objects(
            wall_a=(box([0, 0, 0], [3, .1, 2.5]), "wall"), wall_b=(box([0, 0, 0], [.1, 3, 2.5]), "wall"),
            picture=box([1, .05, 1.2], [1.6, .15, 1.8])), {})
        kinds = {tuple(r["objects"]): r["kind"] for r in found}
        self.assertEqual(kinds[("wall_a", "wall_b")], "architecture_junction")
        self.assertEqual(kinds[("picture", "wall_a")], "embedded_in_architecture")

    def test_coplanar_same_facing_overlap_only(self):
        found = checks.coplanar_overlaps(objects(
            cabinet__top=box([0, 0, 0], [1, .5, .9]), cabinet__lid=box([.2, 0, .8], [.8, .5, .9]),
            crate=box([2, 0, 0], [2.5, .5, .5]), crate_on_top=box([2, 0, .5], [2.5, .5, .9])))
        pairs = [r["objects"] for r in found]
        self.assertIn(["cabinet/lid", "cabinet/top"], pairs)
        self.assertNotIn(["crate", "crate_on_top"], pairs)  # touching opposite faces do not flicker
        top = next(r for r in found if r["objects"] == ["cabinet/lid", "cabinet/top"])
        self.assertAlmostEqual(top["overlap_area_m2"], .6*.5+2*.6*.1, places=6)  # top face plus both side faces

    def test_tops_under_a_ceiling_count_as_open_but_not_under_a_shelf(self):
        model = objects(ceiling=(box([-1, -1, 2.5], [3, 3, 2.6]), "ceiling"),
                        shelf=box([1.5, 0, 1.2], [2.5, .5, 1.3]))
        points = [[.5, .2, 2.4], [2, .2, 1.1]]  # beam top below the ceiling; cabinet top 10 cm under the shelf
        is_open = checks.open_along_normal(model, points, [[0, 0, 1], [0, 0, 1]])
        self.assertEqual(is_open.tolist(), [True, False])

    def test_shell_leak_is_found_at_a_ceiling_gap_and_explained_by_an_opening(self):
        cameras = [[1, 1.5, 1.4], [3, 1.5, 1.4]]
        self.assertEqual(checks.shell_leaks(room(), cameras, 400)["escaped"], 0)
        leaks = checks.shell_leaks(room(gap=(1.9, 2.1)), cameras, 2000)
        self.assertGreater(leaks["escaped"], 0)
        top = leaks["groups"][0]
        self.assertAlmostEqual(top["location_work_m"][0], 2, delta=.3)
        self.assertGreater(top["location_work_m"][2], 2.3)
        self.assertEqual(top["within_opening"], [])
        explained = checks.shell_leaks(room(gap=(1.9, 2.1)), cameras, 2000,
                                       openings={"skylight": (np.array([1.8, -.2, 2.3]), np.array([2.2, 3.2, 2.8]))})
        self.assertEqual(explained["groups"][0]["within_opening"], ["skylight"])

    def test_semantic_warnings_catch_panels_tagged_as_walls(self):
        b = ModelBuilder("fixture", source=source("inference", "task", "notes.json"))
        b.wall("arch/wall/north", (0, 3), (4, 3), 0, 2.5, room_side="right")
        b.surface("fan/blade", [(1, 1, 2.3), (1.4, 1, 2.3), (1.4, 1.1, 2.3)], category="wall")
        b.surface("lamp/shade", [(2, 2, 1), (2.2, 2, 1), (2.2, 2, 1.2)], category="wall")
        b.box("kitchen/plinth", "component", (1, 1, .05), (1, .1, .1))
        codes = {(w["id"], w["code"]) for w in checks.semantic_warnings(b.build())}
        self.assertEqual(codes, {("fan/blade", "wall_not_vertical"), ("lamp/shade", "wall_too_small"),
                                 ("kitchen/plinth", "geometry_type_as_category")})


class Authoring(unittest.TestCase):
    def setUp(self):
        self.b = ModelBuilder("fixture", source=source("measurement", "task", "evidence/plane.json", note="fit"))

    def test_room_offsets_shared_nodes_and_opening(self):
        b = self.b
        b.wall("arch/wall/south", (0, 0), (4, 0), 0, 2.5, thickness_m=.1, room_side="left")
        b.wall("arch/wall/east", (4, 0), (4, 3), 0, 2.5, thickness_m=.1, room_side="left")
        b.opening("arch/door/south", "arch/wall/south", 1, 1.9, 0, 2.05, category="door")
        b.slab("arch/floor/main", [(0, 0), (0, 3), (4, 3), (4, 0)], 0, kind="floor", thickness_m=.08)
        b.slab("arch/ceiling/main", [(0, 0), (4, 0), (4, 3), (0, 3)], 2.5, kind="ceiling", thickness_m=.1)
        model = b.build()
        by = {o["id"]: o for o in model["objects"]}
        self.assertEqual(by["arch/wall/south"]["geometry"]["offset_m"], 0)       # solid outside (-Y), away from the room
        self.assertEqual(by["arch/floor/main"]["geometry"]["offset_m"], -.08)    # below the measured top
        self.assertEqual(by["arch/ceiling/main"]["geometry"]["offset_m"], 0)     # above the measured underside
        self.assertEqual(by["arch/door/south"]["host_id"], "arch/wall/south")
        corner = b.vertex((4, 0, 0))
        self.assertIn(corner, by["arch/wall/south"]["geometry"]["boundary_ids"])
        self.assertIn(corner, by["arch/wall/east"]["geometry"]["boundary_ids"])
        from indoor_recon.scene_model import surface_loops
        (_, _, _, normal), _ = surface_loops(by["arch/wall/south"]["geometry"], model["vertices"])
        self.assertAlmostEqual(normal[1], -1)  # room on the left of +X is +Y; normal and solid point to -Y

    def test_category_is_required_and_ids_unique(self):
        with self.assertRaises(ValueError):
            self.b.box("thing", "", (0, 0, 0), (1, 1, 1))
        self.b.box("thing", "crate", (0, 0, .5), (1, 1, 1))
        with self.assertRaises(ValueError):
            self.b.box("thing", "crate", (0, 0, .5), (1, 1, 1))

    def test_assembly_parts_and_template(self):
        self.b.assembly("furniture/stool", "stool", (1, 1, 0), yaw_degrees=30)
        self.b.box("furniture/stool/seat", "stool", (0, 0, .7), (.35, .35, .03), parent_id="furniture/stool")
        self.b.furniture("furniture/table", "table", [1.2, .8, .75], (2, 2, 0))
        model = self.b.build()
        self.assertTrue(any(o["id"].startswith("furniture/table/") for o in model["objects"]))


class Residuals(unittest.TestCase):
    def test_missing_shelf_is_one_cluster_and_modelled_floor_is_explained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            src = root/"source"
            src.mkdir()
            floor = trimesh.Trimesh([[0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0]], [[0, 1, 2], [0, 2, 3]], process=False)
            shelf = trimesh.Trimesh([[1, 2.7, 1.6], [2.5, 2.7, 1.6], [2.5, 3, 1.6], [1, 3, 1.6]], [[0, 1, 2], [0, 2, 3]], process=False)
            trimesh.util.concatenate([floor, shelf]).export(src/"scan.ply")
            (src/"prepared.json").write_text(json.dumps({"scene_id": "fixture", "frames": []}))
            config = {"schema_version": 1, "kind": "evidence", "scene_id": "fixture",
                      "prepared": {"root": "source", "path": "prepared.json"},
                      "scan": {"asset": {"root": "source", "path": "scan.ply"}, "metres_per_unit": 1, "T_work_from_source_m": np.eye(4).tolist()}}
            (root/"config.json").write_text(json.dumps(config))
            startup = root/"startup.json"
            startup.write_text(json.dumps({"scene_id": "fixture", "evidence_config": str(root/"config.json"), "evidence_roots": {"source": str(src)}}))
            scene = trimesh.Scene()
            model_floor = floor.copy()
            model_floor.apply_transform(np.linalg.inv(WORK_FROM_GLTF))
            scene.add_geometry(model_floor, node_name="arch/floor", geom_name="arch/floor")
            meta = {"coordinate_frame": "gltf_y_up_metres", "T_work_from_gltf": WORK_FROM_GLTF.tolist(), "parameter_model": {"scene_id": "fixture", "revision": 3}}
            glb = root/"model.glb"
            glb.write_bytes(trimesh.exchange.gltf.export_glb(scene, tree_postprocessor=lambda tree: tree.update(extras=meta)))
            report = checks.residuals(startup, glb, root/"residuals", spacing_m=.03, views=0)
            self.assertEqual(report["clusters_total"], 1)
            cluster = report["clusters"][0]
            self.assertAlmostEqual(cluster["area_m2"], 1.5*.3, delta=.05)
            self.assertEqual(cluster["orientation"], "up")
            self.assertAlmostEqual(cluster["centroid_work_m"][2], 1.6, places=3)
            self.assertEqual(cluster["hint"], "unmodelled_region")
            self.assertEqual(report["model_revision"], 3)
            self.assertAlmostEqual(report["scan_area_within_m"]["0.05"], 12/12.45, delta=.01)
            self.assertTrue((root/"residuals/plan.png").is_file())
            with self.assertRaises(Exception):
                checks.residuals(startup, glb, root/"residuals", views=0)


class ScanModelDirections(unittest.TestCase):
    def setUp(self):
        # L-shaped worktop at z 1.0 on cabinet fronts; the model leaves the corner square out.
        self.top = [box([0, 0, .97], [2, .6, 1]), box([0, .6, .97], [.6, 2, 1])]
        self.fronts = [box([.6, .6, 0], [2, .62, .97]), box([.6, .62, 0], [.62, 2, .97])]

    def test_missing_corner_is_its_own_up_cluster_and_named_missing(self):
        scan = trimesh.util.concatenate(self.top+self.fronts)
        points, normals, _ = checks.sample_surface(scan.vertices, scan.faces, .02)
        model = objects(top_a=box([.6, 0, .97], [2, .6, 1]), top_b=box([0, .6, .97], [.6, 2, 1]),
                        front_a=self.fronts[0], front_b=self.fronts[1])
        tracer, names = checks._tracer(model, sorted(model))
        distance, _ = checks._closest(tracer, names, points)
        miss = distance > .05
        labels = checks.oriented_clusters(points[miss], normals[miss], .12)
        up = checks._orientation_class(normals[miss]) == 0
        self.assertEqual(len(set(labels[up]) & set(labels[~up])), 0)
        corner = up & np.all(points[miss][:, :2] < .55, axis=1)
        gaps = checks._normal_gaps(tracer, points[miss][corner], normals[miss][corner], .3)
        self.assertGreater(np.isinf(gaps).mean(), .9)  # nothing along the normal: a surface is absent
        self.assertEqual(checks._hint(.1, float(np.isinf(gaps).mean())), "missing_surface_near_model")
        self.assertEqual(checks._hint(.1, 0.), "offset_surface")

    def test_cameras_see_through_an_oversized_table_top(self):
        import open3d as o3d
        scan = trimesh.util.concatenate([box([-1, -1, -.05], [3, 3, 0]), box([0, 0, .67], [1, 1, .7])])
        scene = o3d.t.geometry.RaycastingScene()
        scene.add_triangles(o3d.core.Tensor(np.asarray(scan.vertices, np.float32)), o3d.core.Tensor(np.asarray(scan.faces, np.uint32)))
        k = np.array([[300., 0, 320], [0, 300, 240], [0, 0, 1]])
        cameras = []
        for x, y in ((.2, .3), (.8, .5), (.5, 1.2), (.3, 1.4), (.9, 1.0)):
            pose = np.eye(4)
            pose[:3, :3] = [[1, 0, 0], [0, -1, 0], [0, 0, -1]]  # looking straight down
            pose[:3, 3] = (x, y, 2.6)
            cameras.append((f"{x},{y}", {"size": [640, 480]}, k, pose))
        model = objects(floor=(box([-1, -1, -.05], [3, 3, 0]), "floor"), table__top=box([0, 0, .67], [1, 1.4, .7]))
        result = checks.excess_surfaces(model, scene, cameras, spacing_m=.02)
        owners = np.array(result["ids"])[result["owner"][result["excess"]]]
        hits = result["points"][result["excess"]]
        self.assertGreater(len(hits), 100)
        self.assertEqual(set(owners), {"table/top"})
        self.assertTrue(np.all(hits[:, 1] > 1-.03))  # only the 0.4 m the scan saw floor through
        self.assertTrue(result["observed"][result["owner"] == result["ids"].index("floor")].any())


class PlanFit(unittest.TestCase):
    def test_table_top_slid_in_plan_is_reported_with_its_shift(self):
        scan = trimesh.util.concatenate([box([0, 0, 0], [3, 3, .01]), box([1, 1, .68], [2.1, 1.5, .7]),
                                         box([.2, 2, .44], [.6, 2.4, .46])])
        points, normals, _ = checks.sample_surface(scan.vertices, scan.faces, .008, max_samples=2_000_000)
        model = objects(coffee__top=box([1, 1.05, .68], [2.1, 1.55, .7]),        # slid 5 cm in +y
                        stool__seat=box([.2, 2, .44], [.6, 2.4, .46]),              # matches
                        shelf__board=box([2.4, .2, 1.2], [2.8, .5, 1.22]))          # nothing at that height
        fit = checks.plan_fit(model, points, normals)
        self.assertEqual([r["id"] for r in fit["shifted"]], ["coffee/top"])
        dx, dy = fit["shifted"][0]["best_shift_m"]
        self.assertAlmostEqual(dx, 0, delta=.011)
        self.assertAlmostEqual(dy, -.05, delta=.011)
        self.assertEqual([r["id"] for r in fit["poor_fit"]], ["shelf/board"])


class FacingFit(unittest.TestCase):
    def test_turned_screen_is_reported_from_scan_and_detection(self):
        turn = trimesh.transformations.rotation_matrix(np.radians(25), [0, 0, 1], [1, 1, 0])
        screen = box([.7, .98, .2], [1.3, 1.02, .6])
        scan = trimesh.util.concatenate([box([0, 0, -.01], [3, 3, 0]), screen.copy().apply_transform(turn),
                                         box([2, 1, .2], [2.6, 1.04, .6])])
        points, normals, area = checks.sample_surface(scan.vertices, scan.faces, .01, max_samples=2_000_000)
        model = objects(floor=(box([0, 0, -.01], [3, 3, 0]), "floor"), desk__screen=screen,   # built wall-aligned
                        shelf__panel=box([2, 1, .2], [2.6, 1.04, .6]))                         # matches the scan
        tracer, names = checks._tracer(model, sorted(model))
        distance, nearest = checks._closest(tracer, names, points)
        R = turn[:3, :3]
        detections = [{"id": "boxer/1", "category": "monitor", "center_m": np.array([1, 1, .4]), "size_m": np.array([.6, .04, .4]),
                       "R": R, "frames": 20},
                      {"id": "boxer/2", "category": "shelf", "center_m": np.array([2.3, 1.02, .4]), "size_m": np.array([.6, .04, .4]),
                       "R": np.eye(3), "frames": 20}]
        turned = checks.facing_fit(model, points, normals, area, distance, nearest, detections)["turned"]
        self.assertEqual([r["id"] for r in turned], ["desk/screen"])
        self.assertAlmostEqual(abs(turned[0]["scan"]["turn_deg"]), 25, delta=2)
        self.assertAlmostEqual(abs(turned[0]["detection"]["turn_deg"]), 25, delta=.5)

    def test_round_objects_have_no_facing_to_fit(self):
        drum = trimesh.creation.cylinder(radius=.2, height=.6, sections=20)
        drum.apply_translation([1, 1, .3])
        scan = trimesh.util.concatenate([box([0, 0, -.01], [3, 3, 0]), drum])
        points, normals, area = checks.sample_surface(scan.vertices, scan.faces, .01, max_samples=2_000_000)
        model = objects(floor=(box([0, 0, -.01], [3, 3, 0]), "floor"), purifier__body=drum)
        tracer, names = checks._tracer(model, sorted(model))
        distance, nearest = checks._closest(tracer, names, points)
        turned = [{"id": "boxer/3", "category": "heater", "center_m": np.array([1, 1, .3]), "size_m": np.array([.4, .4, .6]),
                   "R": trimesh.transformations.rotation_matrix(np.radians(20), [0, 0, 1])[:3, :3], "frames": 30}]
        fit = checks.facing_fit(model, points, normals, area, distance, nearest, turned)
        self.assertEqual(fit["turned"], [])
        self.assertEqual(fit["round"], ["purifier/body"])


class AuthoringPrimitives(unittest.TestCase):
    def test_cylinder_along_any_axis_and_rotated_quads_stay_planar(self):
        b = ModelBuilder("fixture", source=source("inference", "task", "notes.json"))
        b.cylinder("bike/tube", "bicycle", (1, 1, .6), .02, .5, axis=(1, 1, .3))
        b.cylinder("lamp/pole", "lamp", (2, 2, .8), .015, 1.6)
        import math
        # A small tilted panel on a 17-degree loft: rounding stored coordinates would make it non-planar.
        a, tilt = math.radians(17), math.radians(23)
        u, w = (math.cos(a), math.sin(a), 0), (-math.sin(a)*math.sin(tilt), math.cos(a)*math.sin(tilt), math.cos(tilt))
        corner = (3.1234567, 2.7654321, 1.0123457)
        quad = [tuple(c+s*.137*du+t*.091*dw for c, du, dw in zip(corner, u, w)) for s, t in ((0, 0), (1, 0), (1, 1), (0, 1))]
        b.surface("sign/panel", quad, "sign")
        model = b.build()
        pole = next(o for o in model["objects"] if o["id"] == "lamp/pole")
        self.assertAlmostEqual(pole["T_parent_from_object"][2][2], 1)
        self.assertEqual(len(pole["geometry"]["boundary_m"]), 16)


if __name__ == "__main__":
    unittest.main()
