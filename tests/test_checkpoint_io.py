import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from indoor_recon.workers import atomic_io, infer
from indoor_recon.contracts import ContractError
from indoor_recon.jobs import prediction_lock


def windows_lock(code=32):
    error = PermissionError(13, "file temporarily locked")
    error.winerror = code
    return error


class CheckpointIOTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_transient_lock_retries_without_exposing_partial_json(self):
        target = self.root / "state.json"
        atomic_io.write_json(target, {"old": True})
        replace = os.replace
        attempts = []
        def busy_then_replace(source, dest):
            self.assertEqual(json.loads(target.read_text()), {"old": True})
            attempts.append(source)
            if len(attempts) < 3:
                raise windows_lock()
            replace(source, dest)
        with patch.object(atomic_io.os, "replace", side_effect=busy_then_replace), patch.object(atomic_io.time, "sleep") as sleep:
            atomic_io.write_json(target, {"new": "检查点"})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"new": "检查点"})
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [.05, .1])
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_persistent_lock_retains_old_ledger_and_uncommitted_result(self):
        units = infer.Units(self.root)
        units.record("frames/first.json", {"id": "first"})
        old = units.path.read_bytes()
        hashes = dict(units.hashes)
        replace = os.replace
        def lock_ledger(source, dest):
            if Path(dest) == units.path:
                raise windows_lock(5)
            return replace(source, dest)
        with patch.object(atomic_io.os, "replace", side_effect=lock_ledger) as calls, patch.object(atomic_io.time, "sleep") as sleep:
            with self.assertRaises(PermissionError) as raised:
                units.record("frames/second.json", {"id": "second"})
        self.assertEqual(len(sleep.call_args_list), 5)
        self.assertEqual(sum(Path(c.args[1]) == units.path for c in calls.call_args_list), 6)
        self.assertEqual(units.path.read_bytes(), old)
        self.assertEqual(units.hashes, hashes)
        pending = list(self.root.glob(".units.json.*.tmp"))
        self.assertEqual(len(pending), 1)
        self.assertIn("frames/second.json", json.loads(pending[0].read_text()))
        self.assertTrue(any(str(pending[0]) in note for note in raised.exception.__notes__))
        resumed = infer.Units(self.root)
        self.assertEqual(resumed.cached("frames/first.json"), {"id": "first"})
        self.assertIsNone(resumed.cached("frames/second.json"))
        resumed.record("frames/second.json", {"id": "second"})
        self.assertEqual(len(infer.Units(self.root).hashes), 2)

    def test_non_windows_error_is_not_retried(self):
        target = self.root / "state.json"
        atomic_io.write_json(target, {"old": True})
        with patch.object(atomic_io.os, "replace", side_effect=PermissionError(13, "denied")) as replace, patch.object(atomic_io.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                atomic_io.write_json(target, {"new": True})
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(json.loads(target.read_text()), {"old": True})

    def test_standalone_worker_imports_without_cli_on_pythonpath(self):
        request = self.root / "request.json"
        request.write_text(json.dumps(dict(source_dir=str(self.root), output=str(self.root), backend="invalid-test-backend")))
        result = subprocess.run([sys.executable, "-B", infer.__file__, str(request)],
            env=dict(os.environ, PYTHONPATH=""), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("KeyError: 'invalid-test-backend'", result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_real_windows_file_lock_can_clear_during_retry(self):
        import ctypes
        from ctypes import wintypes
        target = self.root / "state.json"
        atomic_io.write_json(target, {"old": True})
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        release = threading.Timer(.15, kernel.CloseHandle, args=(handle,))
        release.start()
        try:
            atomic_io.write_json(target, {"new": True})
        finally:
            release.join()
        self.assertEqual(json.loads(target.read_text()), {"new": True})

    def test_prediction_output_rejects_concurrent_writer_and_releases_lock(self):
        directory = self.root / "work/perception"
        with prediction_lock(directory):
            with self.assertRaisesRegex(ContractError, "in use"):
                with prediction_lock(directory):
                    self.fail("concurrent writer acquired the output")
        with prediction_lock(directory):
            pass


if __name__ == "__main__":
    unittest.main()
