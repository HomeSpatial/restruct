import contextlib
import io
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_exchange_workflow as fixtures
from indoor_recon.__main__ import main
from indoor_recon._exchange.bundle import plan, publish
from indoor_recon._exchange.verify import check, read_json, sha256
from indoor_recon.contracts import ContractError
from indoor_recon.jobs import atomic_json


class BundleWorkflowTests(unittest.TestCase):
    def setUp(self):
        fixtures.ExchangeWorkflowTests.setUp(self)
        self.bundle = self.root / "scene/bundle.json"
        publish(plan(self.bundle, "dense", "remap", scene_id="unrelated-scene"), self.doc)

    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = main(list(map(str, args)))
        self.cli_output = output.getvalue()
        return result

    def init(self):
        result = self.cli("init-task", "--bundle", self.bundle, "--layout", "none", "--objects", "none")
        self.assertEqual(result, 0, self.cli_output)

    def test_prepare_appends_and_preserves_frames_coordinates_and_old_hashes(self):
        previous = sha256(self.bundle.parent / "stages/dense/reconstruction.json")
        self.init()
        self.assertEqual(previous, sha256(self.bundle.parent / "stages/dense/reconstruction.json"))
        prepared = read_json(self.bundle.parent / "stages/prepared/job/prepare/prepared.json")
        startup = read_json(self.bundle.parent / "work/prepared/startup.json")
        self.assertIn("--bundle", startup["commands"]["predict"])
        self.assertEqual([r["id"] for r in prepared["frames"]], ["950"])
        self.assertEqual(prepared["alignment"], [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]])
        self.assertEqual(check(self.bundle)["lock_validation"], "passed")
        from indoor_recon._exchange.exchange import Catalog
        self.assertEqual(len(Catalog(self.bundle).transform_path("dense/source", "prepared/work")), 2)

    def test_relocated_prepared_stage_predicts_without_old_workspace(self):
        self.init()
        moved = self.root / "moved"
        shutil.move(str(self.bundle.parent), moved)
        self.bundle = moved / "bundle.json"
        self.assertEqual(self.cli("predict", "--bundle", self.bundle), 0)
        self.assertEqual(check(self.bundle)["lock_validation"], "passed")
        self.assertEqual([r["id"] for r in read_json(self.bundle)["reconstructions"]], ["dense", "prepared", "perception"])
        self.assertFalse((moved / "stages/prepared/job/perception.json").exists())

    def test_prediction_failure_does_not_append_or_mutate_input(self):
        self.init()
        before = self.bundle.read_bytes()
        original = sha256(self.bundle.parent / "stages/prepared/job/prepare/prepared.json")
        self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--layout", "spatiallm", "--objects", "boxer"), 1)
        self.assertEqual(before, self.bundle.read_bytes())
        self.assertEqual(original, sha256(self.bundle.parent / "stages/prepared/job/prepare/prepared.json"))

    def test_preflight_is_read_only(self):
        self.init()
        def hashes():
            return {str(p): sha256(p) for p in self.bundle.parent.rglob("*") if p.is_file()}
        before = hashes()
        self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--preflight", "--layout", "spatiallm", "--objects", "boxer"), 1)
        self.assertEqual(before, hashes())

    def test_duplicate_stage_does_not_run_prepare(self):
        self.init()
        with patch("indoor_recon.startup.initialize") as prepare:
            self.assertEqual(self.cli("init-task", "--bundle", self.bundle), 1)
            prepare.assert_not_called()

    def test_append_existing_result_does_not_require_runtime(self):
        self.assertEqual(self.cli("bundle", "append", self.bundle, "--result", self.doc,
                                 "--stage", "dense-alternative", "--from", "dense"), 0)
        self.assertEqual(check(self.bundle)["lock_validation"], "passed")

    def test_worker_interruption_resumes_units_and_publishes_once(self):
        from indoor_recon.perception import BOXER_WEIGHTS
        from indoor_recon.workers.infer import Units
        self.init()
        before = self.bundle.read_bytes()
        source = self.root / "backend"; source.mkdir(); (source / "backend.py").write_text("# fixture")
        weights = self.root / "weights"; weights.mkdir()
        for name in BOXER_WEIGHTS: (weights / name).write_bytes(b"fixture")
        labels = self.root / "labels.json"; atomic_json(labels, {"labels": ["chair"]})
        runtime = self.root / "runtime.json"
        atomic_json(runtime, {"boxer": dict(python=sys.executable, source_dir=str(source), weights_dir=str(weights), labels_file=str(labels))})
        inferred = []
        attempts = []
        def worker(command, **kwargs):
            if command[2].endswith("probe.py"):
                return SimpleNamespace(returncode=0, stdout='RECON_PROBE={"passed":true}', stderr="")
            request = read_json(command[3]); out = Path(request["output"])
            units = Units(out)
            for frame in request["frames"]:
                name = f"frames/{frame['id']}.json"
                if units.cached(name) is None:
                    inferred.append(frame["id"])
                    units.record(name, {"detections": []})
            attempts.append(1)
            if len(attempts) == 1:
                (out / ".units.json.failed.tmp").write_text("uncommitted temporary data")
                return SimpleNamespace(returncode=1)
            atomic_json(out / "fused-predictions.json", {"objects": []})
            return SimpleNamespace(returncode=0)
        with patch("indoor_recon.perception.subprocess.run", side_effect=worker):
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--runtime", runtime, "--objects", "boxer", "--detect-type", "all"), 1)
            self.assertEqual(self.bundle.read_bytes(), before)
            # Choices and runtime are recovered without repeating flags.
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume"), 0, self.cli_output)
        self.assertEqual(inferred, ["950"])
        self.assertEqual(len(attempts), 2)
        self.assertFalse(list((self.bundle.parent / "stages/perception").rglob("*.tmp")))
        self.assertEqual(check(self.bundle)["lock_validation"], "passed")
        with patch("indoor_recon.perception.predict") as predict:
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume"), 1)
            predict.assert_not_called()

    def test_resume_rejects_changed_options_input_and_cloned_assets(self):
        self.init()
        with patch("indoor_recon.perception.predict", side_effect=ContractError("interrupted")):
            self.assertEqual(self.cli("predict", "--bundle", self.bundle), 1)
        with patch("indoor_recon.perception.predict") as predict:
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume", "--detect-type", "all"), 1)
            self.assertIn("configuration differs", self.cli_output)
            prepared = self.bundle.parent / "stages/prepared/reconstruction.json"
            publish(plan(self.bundle, "prepared-other", "indoor.prepare", source="prepared", required=("prepared",)), prepared)
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume", "--from", "prepared-other"), 1)
            self.assertIn("configuration differs", self.cli_output)
            cloned = self.bundle.parent / "work/perception/job/prepare/global.ply"
            cloned.write_bytes(cloned.read_bytes() + b"changed")
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume", "--from", "prepared"), 1)
            predict.assert_not_called()
        self.assertNotIn("perception", [r["id"] for r in read_json(self.bundle)["reconstructions"]])

    def test_resume_after_snapshot_copy_failure_preserves_partial_attempt(self):
        self.init()
        out = self.bundle.parent / "work/perception"
        def interrupted_snapshot(*args, **kwargs):
            (out / "exchange").mkdir()
            (out / "exchange/partial.txt").write_text("incomplete snapshot")
            raise ContractError("copy interrupted")
        with patch("indoor_recon.bundle_workflow._task_snapshot", side_effect=interrupted_snapshot):
            self.assertEqual(self.cli("predict", "--bundle", self.bundle), 1)
        self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume"), 0, self.cli_output)
        self.assertEqual(len(list(out.glob("exchange-interrupted-*/partial.txt"))), 1)
        self.assertEqual(check(self.bundle)["lock_validation"], "passed")

    def test_resume_after_publish_failure_reuses_completed_snapshot(self):
        self.init()
        before = self.bundle.read_bytes()
        with patch("indoor_recon.bundle_workflow.publish", side_effect=ContractError("publish interrupted")):
            self.assertEqual(self.cli("predict", "--bundle", self.bundle), 1)
        self.assertEqual(before, self.bundle.read_bytes())
        snapshot_asset = self.bundle.parent / "work/perception/exchange/job/prepare/global.ply"
        original = snapshot_asset.read_bytes()
        with patch("indoor_recon.perception.predict") as predict:
            snapshot_asset.write_bytes(original + b"changed")
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume"), 1)
            self.assertEqual(before, self.bundle.read_bytes())
            snapshot_asset.write_bytes(original)
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume"), 0, self.cli_output)
            predict.assert_not_called()
        self.assertEqual(check(self.bundle)["lock_validation"], "passed")

    def test_resume_rejects_changed_runtime_and_label_contents(self):
        self.init()
        labels = self.root / "labels.json"; atomic_json(labels, {"labels": ["chair"]})
        runtime = self.root / "runtime.json"
        atomic_json(runtime, {"boxer": {"labels_file": str(labels)}})
        with patch("indoor_recon.perception.predict", side_effect=ContractError("interrupted")):
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--runtime", runtime, "--objects", "boxer"), 1)
        with patch("indoor_recon.perception.predict") as predict:
            atomic_json(labels, {"labels": ["table"]})
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume"), 1)
            self.assertIn("configuration differs", self.cli_output)
            atomic_json(labels, {"labels": ["chair"]})
            atomic_json(runtime, {"boxer": {"labels_file": str(labels), "python": sys.executable}})
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume", "--runtime", runtime), 1)
            self.assertIn("configuration differs", self.cli_output)
            predict.assert_not_called()

    def test_resume_requires_recorded_unpublished_work(self):
        self.init()
        self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume"), 1)
        out = self.bundle.parent / "work/perception"; out.mkdir()
        with patch("indoor_recon.perception.predict") as predict:
            self.assertEqual(self.cli("predict", "--bundle", self.bundle, "--resume"), 1)
            self.assertIn("no prediction receipt", self.cli_output)
            predict.assert_not_called()


if __name__ == "__main__":
    unittest.main()
