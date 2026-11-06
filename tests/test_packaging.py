"""Packaging works from an arbitrarily named checkout without sibling projects."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_addon_zip_bundles_checkout_contract_from_another_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "addon.zip"
            subprocess.run([sys.executable, "-B", str(ROOT / "blender/package_addon.py"), str(out)],
                           cwd=temp, check=True, capture_output=True)
            with zipfile.ZipFile(out) as archive:
                self.assertEqual(archive.read("homespatial_model/_vendor/scene_model.py"),
                                 (ROOT / "src/indoor_recon/scene_model.py").read_bytes())
                self.assertIn("homespatial_model/run_agent.py", archive.namelist())
                self.assertIn("homespatial_model/review.py", archive.namelist())
                self.assertEqual(archive.read("homespatial_model/LICENSE"), (ROOT / "blender/LICENSE").read_bytes())
                self.assertEqual(archive.read("homespatial_model/LICENSE-APACHE-2.0"), (ROOT / "LICENSE").read_bytes())
                self.assertIn("homespatial_model/NOTICE", archive.namelist())
                self.assertIn("homespatial_model/CORE-NOTICE", archive.namelist())
                self.assertFalse(any("__pycache__" in name for name in archive.namelist()))

    def test_missing_contract_leaves_no_incomplete_package(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "addon.zip"
            result = subprocess.run([sys.executable, "-B", str(ROOT / "blender/package_addon.py"),
                                     str(out), "--contract-source", str(Path(temp) / "missing.py")],
                                    cwd=temp, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Model contract is missing", result.stderr)
            self.assertFalse(out.exists())

    def test_context_uses_checkout_or_explicit_repo_without_siblings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "checkout with spaces"
            shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", "*.egg-info", ".venv", "artifacts", "build", "dist"))
            for directory in ("artifacts", "dist"):
                (repo / "blender" / directory).mkdir()
                (repo / "blender" / directory / "local-only.bin").write_bytes(b"not source")
            spec = importlib.util.spec_from_file_location("context_under_test", repo / "containers/make_context.py")
            context = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(context)
            commits = {"spatiallm": "8913c44d84a450c53e9340b13317f8cf7144a738",
                       "boxer": "1f86542dc342a4b1d474c87c97c5d1d6566d9148"}
            for name in commits:
                (root / name).mkdir()
                (root / name / "LICENSE").write_text("fixture license")
            blender = b"fixture archive"
            sums = (hashlib.sha256(blender).hexdigest() + "  blender-5.1.2-linux-x64.tar.xz\n").encode()

            def git_output(command, **kwargs):
                return commits[Path(command[2]).name] if command[3] == "rev-parse" else ""

            def git_archive(command, **kwargs):
                with tarfile.open(command[command.index("--output") + 1], 'w') as archive:
                    archive.add(Path(command[2])/'LICENSE', arcname='LICENSE')

            for explicit in (False, True, 'custom'):
                with self.subTest(explicit_repo=explicit):
                    out = root / ("custom-context" if explicit == 'custom' else "explicit-context" if explicit else "default-context")
                    (out / "resources").mkdir(parents=True)
                    (out / "resources/blender-5.1.2-linux-x64.tar.xz").write_bytes(blender)
                    argv = ["make_context.py", "--out", str(out),
                            "--spatiallm-source", str(root / "spatiallm"), "--boxer-source", str(root / "boxer")]
                    if explicit:
                        argv += ["--repo", str(repo)]
                    if explicit == 'custom':
                        commits['boxer'] = 'a' * 40
                        custom_lock = root/'custom-lock.txt'
                        custom_lock.write_text('fixture custom dependency lock')
                        argv += ['--boxer-ref', 'custom-tag', '--boxer-lock', str(custom_lock)]
                    with mock.patch("sys.argv", argv), \
                         mock.patch.object(context.subprocess, "check_output", side_effect=git_output), \
                         mock.patch.object(context.subprocess, "run", side_effect=git_archive), \
                         mock.patch.object(context.urllib.request, "urlopen", return_value=io.BytesIO(sums)), \
                         contextlib.redirect_stdout(io.StringIO()):
                        context.main()
                    self.assertEqual((out / "addon/_vendor/scene_model.py").read_bytes(),
                                     (repo / "src/indoor_recon/scene_model.py").read_bytes())
                    self.assertEqual((out / "core/blender/run_agent.py").read_bytes(),
                                     (repo / "blender/run_agent.py").read_bytes())
                    self.assertFalse((out / "core/blender/artifacts").exists())
                    self.assertFalse((out / "core/blender/dist").exists())
                    manifest = json.loads((out / "context-files.json").read_text())
                    self.assertIn("addon/run_agent.py", manifest)
                    for name, expected in manifest.items():
                        self.assertEqual(hashlib.sha256((out / name).read_bytes()).hexdigest(), expected, name)
                    sources = json.loads((out/'resources/sources.json').read_text())
                    self.assertEqual(sources['boxer']['commit'], commits['boxer'])
                    self.assertEqual(sources['boxer']['license_files'], ['LICENSE'])
                    self.assertEqual(sources['boxer']['baseline_match'], explicit != 'custom')
                    if explicit == 'custom':
                        self.assertEqual(sources['boxer']['requested_ref'], 'custom-tag')
                        self.assertEqual((out/'locks/boxer.txt').read_bytes(), custom_lock.read_bytes())



if __name__ == "__main__":
    unittest.main()
