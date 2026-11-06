"""Exercise the built wheel and installed Blender add-on outside either source tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("wheel", "addon", "blender", "model", "update", "v1", "output"):
        p.add_argument("--" + name, required=True, type=Path)
    args = p.parse_args()
    out = args.output.resolve()
    if out.exists():
        raise ValueError("Use a new verification output directory")
    out.mkdir(parents=True)
    package = out / "wheel"
    with zipfile.ZipFile(args.wheel) as archive:
        archive.extractall(package)
    runtime = out / "runtime.local.json"
    runtime.write_text(json.dumps({"blender": {
        "executable": str(args.blender.resolve()), "addon_dir": str(args.addon.resolve())
    }}, indent=2), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(package), PYTHONDONTWRITEBYTECODE="1")
    commands = []

    def cli(name, *arguments, success=True):
        command = [sys.executable, "-B", "-m", "indoor_recon", *map(str, arguments)]
        result = subprocess.run(command, cwd=out, env=env, capture_output=True, text=True)
        (out / (name + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
        assert (result.returncode == 0) == success, (name, result.stdout, result.stderr)
        commands.append({"name": name, "command": command, "exit_code": result.returncode})
        return result

    cli("v1-contract", "validate", args.v1.resolve())
    cli("v2-contract", "validate", args.model.resolve())
    cli("create", "build", args.model.resolve(), "--runtime", runtime, "--out", out / "create")
    blend = out / "create/model.blend"
    original_hash = hashlib.sha256(blend.read_bytes()).hexdigest()
    cli("update", "build", args.update.resolve(), "--runtime", runtime,
        "--blend", blend, "--out", out / "update")
    assert hashlib.sha256(blend.read_bytes()).hexdigest() == original_hash
    edited = out / "update/model.blend"
    edited_hash = hashlib.sha256(edited.read_bytes()).hexdigest()
    cli("reject-stale", "build", args.model.resolve(), "--runtime", runtime,
        "--blend", edited, "--out", out / "stale", success=False)
    rejected = json.loads((out / "stale/response.json").read_text(encoding="utf-8"))[0]
    assert not rejected["ok"] and "revision" in rejected["error"]["message"].lower(), rejected
    assert hashlib.sha256(edited.read_bytes()).hexdigest() == edited_hash
    assert not (out / "stale/model.blend").exists()
    created = json.loads((out / "create/build.json").read_text(encoding="utf-8"))
    updated = json.loads((out / "update/build.json").read_text(encoding="utf-8"))
    assert (created["revision"], updated["revision"]) == (1, 2)
    report = {"passed": True, "created_revision": 1, "updated_revision": 2,
              "stale_rejected": True, "input_blends_unchanged": True,
              "wheel_sha256": hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
              "commands": commands}
    (out / "validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "commands"}))


if __name__ == "__main__":
    main()
