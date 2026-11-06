import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from indoor_recon._exchange.exchange import Catalog, write_description
from indoor_recon.colmap import import_colmap
from indoor_recon.contracts import ContractError, read_json
from indoor_recon.exchange import import_description
from indoor_recon.perception import predict, prediction_plan, BOXER_WEIGHTS
from indoor_recon.preparation import prepare
from indoor_recon.startup import initialize
from indoor_recon.jobs import atomic_json


class ExchangeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        raw = self.root / "raw"; raw.mkdir()
        (raw / "cameras.bin").write_bytes(struct.pack("<QiiQQdddd", 1, 87, 1, 4, 3, 2, 2, 2, 1.5))
        (raw / "images.bin").write_bytes(struct.pack("<Qidddddddi", 1, 950, 1, 0, 0, 0, -3, -4, -5, 87) + b"photo.jpg\0" + struct.pack("<Q", 0))
        (raw / "points3D.bin").write_bytes(struct.pack("<Q", 0))
        Image.new("RGB", (4, 3), (40, 80, 120)).save(raw / "photo.jpg")
        (raw / "cloud.ply").write_text("ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n0 0 0 100 100 100\n1 0 0 100 100 100\n0 1 0 100 100 100\n1 1 0 100 100 100\n")
        self.doc = write_description(self.root / "portable", "unrelated-scene",
            dict(handedness="right", up_axis="+Z", metres_per_unit=1),
            {n: raw / (n + ".bin") for n in ("cameras", "images", "points3D")},
            {"photo.jpg": raw / "photo.jpg"}, geometry=("point_cloud", "ply", raw / "cloud.ply"),
            quality={"status": "not_accepted", "promotable": False})

    def write_doc(self, doc):
        atomic_json(self.doc, doc)

    def test_arbitrary_asset_ids_and_preflight_create_no_output(self):
        doc = read_json(self.doc)
        for a in doc["assets"]:
            a["id"] = {"geometry": "cloud-A", "photos": "pictures", "cameras": "pose-set"}[a["id"]]
            if "coverage" in a: a["coverage"]["reference_asset"] = "pose-set"
        self.write_doc(doc)
        out = self.root / "unused"
        result = import_description(self.doc, out, preflight=True)
        self.assertEqual(result["frame_ids"], ["950"])
        self.assertEqual(result["selected_geometry"], "cloud-A")
        self.assertFalse(out.exists())
        self.assertFalse(result["catalog"]["quality"]["producer_extensions"]["entry"]["org.reconstruction.workflow"]["quality"]["promotable"])

    def test_native_point_cloud_import_and_prepare_preserve_coordinates(self):
        out = self.root / "import"
        import_description(self.doc, out)
        scene = read_json(out / "scene.json")
        self.assertEqual(scene["geometry"][0]["type"], "point_cloud")
        frame = read_json(out / "frames.json")["frames"][0]
        np.testing.assert_allclose(np.array(frame["camera"]["T_source_from_camera"])[:3, 3], [3, 4, 5])
        result = prepare(out / "scene.json", self.root / "job", {}, {"alignment": "preserve"})
        np.testing.assert_array_equal(result["alignment"], np.eye(4))

    def test_ambiguous_geometry_requires_selection_and_does_not_write(self):
        doc = read_json(self.doc)
        second = copy.deepcopy(doc["assets"][-1]); second["id"] = "alternative"
        doc["assets"].append(second); self.write_doc(doc)
        with self.assertRaisesRegex(ValueError, "explicitly"):
            import_description(self.doc, self.root / "not-created")
        self.assertFalse((self.root / "not-created").exists())
        result = import_description(self.doc, geometry_id="alternative", preflight=True)
        self.assertEqual(result["selected_geometry"], "alternative")

    def test_explicit_sim3_converts_camera_translation_and_preserves_rotation(self):
        doc = read_json(self.doc)
        doc["coordinate_systems"].append(dict(id="target", handedness="right", up_axis="+Z", metres_per_unit=1))
        doc["assets"][-1]["coordinate_system"] = "target"
        doc["coordinate_transforms"] = [{"from": "source", "to": "target", "kind": "similarity", "units": "native",
            "T_to_from": [[2,0,0,10],[0,2,0,0],[0,0,2,0],[0,0,0,1]], "method": "fixture",
            "evidence": [doc["assets"][-1]["files"][0]["ref"]]}]
        self.write_doc(doc)
        out = self.root / "transformed"
        import_description(self.doc, out)
        pose = np.array(read_json(out / "frames.json")["frames"][0]["camera"]["T_source_from_camera"])
        np.testing.assert_allclose(pose[:3, 3], [16,8,10])
        np.testing.assert_allclose(pose[:3, :3], np.eye(3))

    def test_native_colmap_import_detects_point_cloud_and_uses_exact_ids(self):
        result = import_colmap(self.doc.parent, "sparse", "images", "geometry.ply", self.root / "colmap",
                               "scene", "+Z", 1, frame_ids=["950"])
        self.assertEqual(result["frames"], 1)
        self.assertEqual(read_json(self.root / "colmap/scene.json")["geometry"][0]["type"], "point_cloud")

    def test_init_description_reports_missing_predict_configuration(self):
        runtime = self.root / "runtime.json"; atomic_json(runtime, {})
        result = initialize(self.doc, self.root / "task", runtime)
        startup = read_json(self.root / "task/startup.json")
        self.assertIsNone(startup["commands"]["predict"])
        self.assertIn("--preflight", startup["commands"]["predict_preflight"])
        self.assertTrue(any("labels" in e for e in result["prediction_preflight"]["errors"]))

    def test_prediction_missing_boxer_configuration_fails_before_any_model(self):
        imported = self.root / "imported"; import_description(self.doc, imported)
        job = self.root / "job"; prepare(imported / "scene.json", job, {})
        source = self.root / "backend"; source.mkdir(); (source / "a.py").write_text("# source")
        weights = self.root / "weights"; weights.mkdir(); (weights / "model.safetensors").write_bytes(b"weights")
        runtime = {"spatiallm": dict(python=sys.executable, source_dir=str(source), model_dir=str(weights))}
        with patch("indoor_recon.perception.subprocess.run") as run:
            with self.assertRaisesRegex(ContractError, "boxer"):
                predict(job, runtime)
            run.assert_not_called()
        self.assertFalse((job / "spatiallm").exists())

    def test_prepare_multiple_geometries_requires_id(self):
        out = self.root / "imported"; import_description(self.doc, out)
        scene = read_json(out / "scene.json")
        scene["geometry"].append(dict(scene["geometry"][0], id="other"))
        atomic_json(out / "scene.json", scene)
        with self.assertRaisesRegex(ContractError, "Multiple geometry"):
            prepare(out / "scene.json", None, {}, preflight=True)
        result = prepare(out / "scene.json", None, {}, {"geometry_id":"other"}, preflight=True)
        self.assertEqual(result["geometry_id"], "other")

    def test_backend_import_failure_checks_all_stages_without_starting_models(self):
        from types import SimpleNamespace
        source = self.root / "code"; source.mkdir(); (source / "a.py").write_text("# fixture")
        weights = self.root / "weights"; weights.mkdir()
        for name in ["model.safetensors", *BOXER_WEIGHTS]:
            (weights / name).write_bytes(b"fixture")
        labels = self.root / "labels.json"; atomic_json(labels, {"labels": ["chair"]})
        runtime = {"spatiallm": dict(python=sys.executable, source_dir=str(source), model_dir=str(weights)),
                   "boxer": dict(python=sys.executable, source_dir=str(source), weights_dir=str(weights))}
        def probe(command, **kwargs):
            self.assertTrue(command[2].endswith("probe.py"))
            self.assertLessEqual(kwargs["timeout"], 45)
            return SimpleNamespace(returncode=1, stdout='RECON_PROBE={"passed":false,"error":"missing dependency"}', stderr="")
        with patch("indoor_recon.perception.subprocess.run", side_effect=probe) as run:
            result = prediction_plan({"frames": ["950"], "has_color": True}, runtime, labels_file=labels)
        self.assertFalse(result["passed"])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(result["errors"]), 2)

    def test_mismatched_photo_asset_is_rejected(self):
        doc = read_json(self.doc)
        other = copy.deepcopy(doc["assets"][0]); other["id"] = "different-camera-set"
        doc["assets"].append(other); self.write_doc(doc)
        with self.assertRaisesRegex(ContractError, "different sparse asset"):
            import_description(self.doc, sparse_id=other["id"], preflight=True)

    def test_init_explicit_alignment_override_is_preserved(self):
        runtime = self.root / "runtime.json"; atomic_json(runtime, {})
        initialize(self.doc, self.root / "task", runtime, options={"alignment": "auto"}, layout="none", objects="none")
        prepared = read_json(self.root / "task/job/prepare/prepared.json")
        self.assertEqual(prepared["options"]["alignment"], "auto")


if __name__ == "__main__":
    unittest.main()
