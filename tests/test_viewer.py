"""Web viewer server: lists models below its root and never serves files outside it."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from indoor_recon.viewer import STATIC, list_models, list_revisions, make_handler, recent_activity, resolve_model_file


class ViewerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "artifacts"
        (self.root / "run a/build-r1").mkdir(parents=True)
        (self.root / ".hidden").mkdir()
        (self.root / "run a/build-r1/model.glb").write_bytes(b"glTF-one")
        (self.root / "scene.gltf").write_text("{}")
        (self.root / "notes.txt").write_text("x")
        (self.root / ".hidden/skip.glb").write_bytes(b"x")
        (self.base / "outside.glb").write_bytes(b"secret")

    def test_lists_only_visible_models_as_relative_paths(self):
        paths = {m["path"] for m in list_models(self.root)}
        self.assertEqual(paths, {"run a/build-r1/model.glb", "scene.gltf"})

    def test_resolve_rejects_paths_outside_root(self):
        self.assertEqual(resolve_model_file(self.root, "run%20a/build-r1/model.glb"),
                         (self.root / "run a/build-r1/model.glb").resolve())
        for escape in ("../outside.glb", "..%2Foutside.glb", "run%20a/../../outside.glb", "missing.glb"):
            self.assertIsNone(resolve_model_file(self.root, escape), escape)

    def test_http_endpoints(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.root))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}"
        listing = json.loads(urlopen(url + "/api/models").read())
        self.assertEqual(len(listing["models"]), 2)
        with urlopen(url + "/files/run%20a/build-r1/model.glb") as response:
            self.assertEqual(response.read(), b"glTF-one")
            self.assertEqual(response.headers["Content-Type"], "model/gltf-binary")
        with self.assertRaises(HTTPError) as caught:
            urlopen(url + "/files/..%2Foutside.glb")
        self.assertEqual(caught.exception.code, 404)
        self.assertIn(b"importmap", urlopen(url + "/").read())
        self.assertTrue((STATIC / "vendor/three/three.core.min.js").is_file())
        revisions = json.loads(urlopen(url + "/api/revisions?dir=run%20a").read())["revisions"]
        self.assertEqual([r["revision"] for r in revisions], [])  # build-r1 has no build.json yet
        with self.assertRaises(HTTPError) as caught:
            urlopen(url + "/api/revisions?dir=..")
        self.assertEqual(caught.exception.code, 404)

    def test_revisions_pick_one_finished_build_per_revision_with_diagnostics(self):
        task = self.root / "run a"

        def build(name, revision, payload, passed=True):
            (task / name).mkdir(parents=True, exist_ok=True)
            (task / name / "model.glb").write_bytes(payload)
            (task / name / "build.json").write_text(json.dumps({"passed": passed, "revision": revision}))

        build("build-r1", 1, b"glTF-one")
        build("build-r2", 2, b"glTF-two")
        build("build-r2-reopen", 2, b"glTF-two-again")
        build("build-r3", 3, b"glTF-three", passed=False)
        (task / "diag").mkdir()
        (task / "diag/residuals.json").write_text(json.dumps({
            "model_sha256": hashlib.sha256(b"glTF-two").hexdigest(), "scan_area_within_m": {"0.05": .75},
            "unexplained_area_m2": 12.5, "excess": {"excess_area_m2": 3.0}}))
        (task / "diag/geometry.json").write_text(json.dumps({
            "model_sha256": hashlib.sha256(b"glTF-one").hexdigest(), "counts": {"object_interference": 4}}))
        revisions = list_revisions(self.root, "run a")
        self.assertEqual([(r["revision"], r["path"]) for r in revisions],
                         [(1, "run a/build-r1/model.glb"), (2, "run a/build-r2/model.glb")])
        self.assertEqual(revisions[0]["geometry"], {"object_interference": 4})
        self.assertIsNone(revisions[0]["residuals"])
        self.assertEqual(revisions[1]["residuals"], {"within_5cm": .75, "unexplained_m2": 12.5, "excess_m2": 3.0})
        self.assertIsNone(list_revisions(self.root, "../"))
        (task / "build-r3/activity.json").write_text(json.dumps({"command": "build", "passed": False, "seconds": 2.0}))
        (task / "diag/activity.json").write_text(json.dumps({"command": "residuals", "passed": True, "seconds": 9.5}))
        runs = recent_activity(self.root, "run a")
        self.assertEqual({(r["command"], r["name"], r["passed"]) for r in runs},
                         {("build", "build-r3", False), ("residuals", "diag", True)})

    def test_invalid_records_do_not_block_finished_builds_and_activity(self):
        valid = self.root / 'run a/build-r1'
        (valid / 'build.json').write_text('{"passed": true, "revision": 1}')
        (valid / 'activity.json').write_text('{"command": "build", "passed": true}')
        for index, content in enumerate(('{unfinished', '[]', '{"passed": true, "revision": "bad"}')):
            bad = self.root / 'run a' / f'broken-{index}'
            bad.mkdir()
            (bad / 'model.glb').write_bytes(b'partial')
            (bad / 'build.json').write_text(content)
            (bad / 'activity.json').write_text('{unfinished' if index == 0 else '[]')
            (bad / 'geometry.json').write_text('[]' if index == 0 else '{"model_sha256": ["invalid"]}')
        self.assertEqual([r['revision'] for r in list_revisions(self.root, 'run a')], [1])
        self.assertEqual([r['name'] for r in recent_activity(self.root, 'run a')], ['build-r1'])


if __name__ == "__main__":
    unittest.main()
