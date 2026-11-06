# SPDX-License-Identifier: Apache-2.0
"""Content-addressed stage records; outputs are private to a reconstruction job."""
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from .contracts import ContractError, read_json, sha256
from .workers.atomic_io import write_json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def atomic_json(path, data):
    write_json(path, data)


@contextmanager
def prediction_lock(directory):
    """One writer per prediction output; the OS releases the lock after a crash."""
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    path = directory.parent / ("." + directory.name + ".prediction.lock")
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ContractError(f"Prediction output is in use; wait for the active run: {directory}") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Stage:
    def __init__(self, directory, fingerprint, resume=False):
        self.directory = Path(directory)
        self.state_path = self.directory / "state.json"
        if self.state_path.exists():
            if not resume:
                raise ContractError("Existing stage requires --resume or a new job directory")
            self.state = read_json(self.state_path)
            if self.state["fingerprint"] != fingerprint:
                raise ContractError("Input/configuration/code changed; use a new job directory")
            for name in self.state["files"]:
                self.valid(name)
        else:
            if self.directory.exists() and any(self.directory.iterdir()):
                raise ContractError("Stage directory is not empty and has no state record")
            self.state = {"fingerprint": fingerprint, "status": "running", "files": {}}
            self.save()

    def save(self):
        atomic_json(self.state_path, self.state)

    def valid(self, relative):
        path = self.directory / relative
        expected = self.state["files"].get(relative)
        if expected and path.is_file():
            if sha256(path) != expected:
                raise ContractError(f"Stage output changed: {relative}")
            return True
        return False

    def record(self, relative):
        self.state["files"][relative] = sha256(self.directory / relative)
        self.save()

    def complete(self):
        if not all(self.valid(name) for name in self.state["files"]):
            raise ContractError("Stage outputs are missing")
        self.state["status"] = "complete"
        self.save()
