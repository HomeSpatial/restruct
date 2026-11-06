# SPDX-License-Identifier: Apache-2.0
"""Content-pinned model launchers and traceable proposal normalization."""
from itertools import product
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import time
import numpy as np
from .contracts import ContractError, read_json, resolve_asset, sha256, validate
from .jobs import Stage, atomic_json, digest
from ._exchange.exchange import result_status
from .backend_config import BOXER_CHECKPOINTS, boxer_checkpoints, private_boxer_weights, source_identity, source_status

BOXER_WEIGHTS = list(BOXER_CHECKPOINTS.values())


def wsl_path(value):
    p = PureWindowsPath(str(value))
    if p.drive and len(p.drive) == 2:
        return "/mnt/" + p.drive[0].lower() + "/" + "/".join(p.parts[1:])
    if str(value).startswith("/"):
        return str(value)
    raise ContractError(f"WSL requires an absolute local-drive or Linux path: {value}")


def corners(candidate):
    g = candidate["geometry"]
    return np.array(list(product((-1, 1), repeat=3))) * np.array(g["size_m"])/2 @ np.array(g["R_work_from_object"]).T + g["center_m"]


def reconcile(candidates, tolerance=.1):
    """Conservative seam deduplication; retain unmatched boundary predictions."""
    kept, merged = [], []
    for candidate in candidates:
        points = corners(candidate)
        match = None
        for previous in kept:
            if previous["category"] != candidate["category"] or previous.get("floor_id") != candidate.get("floor_id"):
                continue
            # A seam merge requires evidence from different input regions.
            references = lambda c: {s.get("reference", {}).get("path") for s in c["sources"]}
            if references(previous) & references(candidate):
                continue
            distances = np.linalg.norm(points[:, None, :] - corners(previous)[None, :, :], axis=-1)
            if max(distances.min(0).max(), distances.min(1).max()) <= tolerance:
                match = previous
                break
        if match is None:
            kept.append(candidate)
        else:
            match["sources"].extend(candidate["sources"])
            merged.append({"kept": match["id"], "duplicate": candidate["id"]})
    return kept, merged


def normalize(backend, raw, prepared):
    candidates, review = [], []
    def candidate(identifier, row, source, floor=None):
        sizes = np.asarray(row["scale"], float)
        return {"id": identifier, "category": row.get("class", row.get("label")),
            "geometry": {"type": "obb", "nature": "surface" if (sizes == 0).sum() == 1 else "volume",
                "center_m": row["center"], "size_m": sizes.tolist(), "R_work_from_object": row["rotation"]},
            "sources": [source], **({"floor_id": floor} if floor else {})}
    if backend == "spatiallm":
        for index, (region, prediction) in enumerate(zip(prepared["regions"], raw["regions"], strict=True)):
            for j, row in enumerate(prediction["boxes"]):
                identifier = f"spatiallm/{index}/{j}"
                c = candidate(identifier, row, {"kind": "prediction", "reference": {"root": "job", "path": f"spatiallm/regions/{index:04}.json"}, "record_id": str(j)}, region["floor_id"])
                try:
                    validate({"schema_version": 1, "kind": "proposals", "scene_id": prepared["scene_id"], "coordinate_frame": "work_z_up_metres", "backend": backend, "candidates": [c]})
                except ContractError as exc:
                    review.append({"id": identifier, "reason": str(exc)})
                    continue
                owner = region["ownership_bounds_m"]
                if not np.all((np.array(c["geometry"]["center_m"]) >= owner[0]) & (np.array(c["geometry"]["center_m"]) < owner[1])):
                    review.append({"id": identifier, "reason": "prediction centre outside ownership core; retained pending seam review"})
                candidates.append(c)
        candidates, merged = reconcile(candidates)
        review.extend(merged)
    else:
        for row in raw["objects"]:
            c = candidate(f"boxer/{row['id']}", row, {"kind": "prediction", "reference": {"root": "job", "path": "boxer/fused-predictions.json"}, "record_id": str(row["id"]), "frame_ids": row["source_frames"]})
            c["scores"] = {"confidence": row["confidence"], "support_detections": row["support_count"]}
            candidates.append(c)
    document = {"schema_version": 1, "kind": "proposals", "scene_id": prepared["scene_id"], "coordinate_frame": "work_z_up_metres", "backend": backend, "candidates": candidates}
    return validate(document), review


def prediction_plan(prepared, runtime, layout="spatiallm", objects="boxer", labels_file=None, probe=True):
    """Check all requested stages before any model starts or job files are changed."""
    errors, stages, labels, warnings = [], {}, [], []
    if layout not in ("spatiallm", "none") or objects not in ("boxer", "none"):
        raise ContractError("Unsupported prediction backend")
    labels_path = labels_file or runtime.get("boxer", {}).get("labels_file")
    for backend in (layout, objects):
        if backend == "none":
            continue
        if backend == "boxer" and not prepared["frames"]:
            stages[backend] = {"status": "skipped", "reason": "No calibrated RGB frames"}
            continue
        if backend == "spatiallm" and not prepared["has_color"]:
            stages[backend] = {"status": "skipped", "reason": "No colored point cloud"}
            continue
        binding = runtime.get(backend, {})
        required = ("python", "source_dir", "model_dir" if backend == "spatiallm" else "weights_dir")
        missing = [key for key in required if not binding.get(key)]
        if missing:
            errors.append(f"{backend}: missing runtime fields {missing}")
        else:
            executable = str(binding["python"])
            if binding.get("wsl_distro"):
                if shutil.which("wsl.exe") is None:
                    errors.append(f"{backend}: wsl.exe is unavailable")
            elif not Path(executable).is_file() and shutil.which(executable) is None:
                errors.append(f"{backend}: interpreter not found: {executable}")
            if not any(Path(binding["source_dir"]).rglob("*.py")):
                errors.append(f"{backend}: source directory contains no Python code")
            if backend == "spatiallm":
                model = Path(binding["model_dir"])
                if not any(model.glob("*.safetensors")) and not any(model.glob("pytorch_model*.bin")):
                    errors.append("spatiallm: no model weight files found")
            else:
                try:
                    missing_weights = [str(p) for p in boxer_checkpoints(binding).values() if not p.is_file()]
                    if missing_weights:
                        errors.append(f"boxer: missing weights {missing_weights}")
                except ContractError as exc:
                    errors.append(str(exc))
        if backend == "boxer":
            if not labels_path:
                errors.append("boxer: supply --labels-file or runtime.boxer.labels_file")
            else:
                try:
                    labels = read_json(labels_path)["labels"]
                    if not isinstance(labels, list) or not labels or any(not isinstance(x, str) or not x.strip() for x in labels) or len(set(labels)) != len(labels):
                        errors.append("boxer: labels must be a nonempty list of unique strings")
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    errors.append(f"boxer: invalid labels file: {exc}")
        stages[backend] = {"status": "configuration_checked", "native_imports_and_gpu": "not_checked"}
        if binding.get("source_dir"):
            status = source_status(backend, binding["source_dir"])
            stages[backend]["source"] = status
            if status["baseline_match"] != "match":
                warnings.append(f"{backend}: source revision is different from or cannot be matched to the tested baseline; compatibility needs validation")
    # Do not import any backend until every selected stage has complete configuration.
    if not errors and probe:
        for backend, stage in stages.items():
            if stage["status"] == "skipped":
                continue
            binding = runtime[backend]
            probe = str(Path(__file__).with_name("workers") / "probe.py")
            source = str(Path(binding["source_dir"]).resolve())
            command = [binding["python"], "-B", probe, backend, source, json.dumps(labels)]
            if binding.get("wsl_distro"):
                command = ["wsl.exe", "-d", binding["wsl_distro"], "--", binding["python"], "-B",
                           wsl_path(probe), backend, wsl_path(source), json.dumps(labels)]
            try:
                checked = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                    timeout=45, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1",
                                         TRANSFORMERS_OFFLINE="1", PYTHONIOENCODING="utf-8"))
                lines = [line.removeprefix("RECON_PROBE=") for line in checked.stdout.splitlines()
                         if line.startswith("RECON_PROBE=")]
                info = json.loads(lines[-1]) if lines else {"passed": False, "error": checked.stderr[-1500:] or "No probe result"}
                stage["native_imports_and_gpu"] = info
                stage["status"] = "ready" if info["passed"] and not checked.returncode else "blocked"
                if stage["status"] == "blocked":
                    errors.append(f"{backend}: {info.get('error', 'backend probe failed')}")
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                stage["status"] = "blocked"
                errors.append(f"{backend}: probe failed: {exc}")
    return dict(passed=not errors, errors=errors, warnings=warnings, stages=stages,
                labels_file=str(Path(labels_path).resolve()) if labels_path else None,
                summary=result_status(execution="ready" if not errors else "blocked", content="configuration_checked",
                    next_steps=["Run predict" if not errors else "Resolve all listed configuration errors before starting models"]))


def predict(job, runtime, layout="spatiallm", objects="boxer", labels_file=None, roots=None, resume=False, detect_type="arch", preflight=False):
    job = Path(job).resolve()
    prepared_path = job / "prepare/prepared.json"
    prepared = read_json(prepared_path)
    prep_state = read_json(job / "prepare/state.json")
    if prep_state["status"] != "complete":
        raise ContractError("Preparation has not completed")
    for path, expected in prep_state["files"].items():
        if not (job / "prepare" / path).is_file() or sha256(job / "prepare" / path) != expected:
            raise ContractError(f"Prepared asset is missing or changed: {path}")
    bindings = read_json(job / "bindings.local.json")
    bindings.update(roots or {})
    bindings = {k: Path(v) for k, v in bindings.items()}
    if objects == "boxer":
        for frame in prepared["frames"]:
            path = resolve_asset(frame["image"], bindings)
            expected = prepared["input_hashes"][frame["image"]["root"] + ":" + frame["image"]["path"]]
            if sha256(path) != expected:
                raise ContractError(f"RGB input changed: {frame['id']}")
    plan = prediction_plan(prepared, runtime, layout, objects, labels_file)
    if preflight:
        return plan
    if not plan["passed"]:
        raise ContractError("Prediction preflight failed: " + "; ".join(plan["errors"]))
    labels_file = plan["labels_file"]
    labels = read_json(labels_file)["labels"] if labels_file else None
    summary = {"scene_id": prepared["scene_id"], "stages": {}, "warnings": plan["warnings"]}
    worker = Path(__file__).parent / "workers/infer.py"
    for backend in (layout, objects):
        if backend == "none":
            continue
        reason = None
        if backend == "boxer" and not prepared["frames"]:
            reason = "No RGB frames with metric camera calibration; geometry remains available"
        if backend == "spatiallm" and not prepared["has_color"]:
            reason = "Current SpatialLM adapter requires a colored point cloud"
        if reason:
            summary["stages"][backend] = {"status": "skipped", "reason": reason}
            continue
        if backend not in runtime:
            raise ContractError(f"Missing runtime binding for {backend}")
        binding = runtime[backend]
        source = Path(binding["source_dir"])
        code = source_identity(source)
        if not code:
            raise ContractError("Backend source directory contains no Python code")
        if backend == "spatiallm":
            model = Path(binding["model_dir"])
            weights = {p.relative_to(model).as_posix(): sha256(p) for p in sorted(model.rglob("*")) if p.is_file() and ".cache" not in p.parts}
            options = {"detect_type": detect_type, "seed": 42, "generation": {"max_new_tokens": 4096, "temperature": .6, "top_p": .95, "top_k": 10, "num_beams": 1}}
        else:
            model = Path(binding["weights_dir"])
            selected = boxer_checkpoints(binding)
            weights = {BOXER_CHECKPOINTS[role]: sha256(path) for role, path in selected.items()}
            if labels is None:
                raise ContractError("Supply --labels-file with an explicit Boxer vocabulary")
            options = {"labels": labels, "threshold2d": .25, "threshold3d": .5, "fusion_iou": .3, "fusion_min_detections": 4, "fusion_confidence": .55}
            bound_frames = []
            for frame in prepared["frames"]:
                path = resolve_asset(frame["image"], bindings)
                expected = prepared["input_hashes"][frame["image"]["root"]+":"+frame["image"]["path"]]
                if sha256(path) != expected:
                    raise ContractError(f"RGB input changed: {frame['id']}")
                bound_frames.append(dict(frame, image_path=str(path.resolve())))
        identity = {"prepared": sha256(prepared_path), "backend": backend, "upstream_python": code, "weights": weights,
            "options": options, "worker": sha256(worker), "worker_io": sha256(worker.with_name("atomic_io.py")), "adapter": sha256(__file__),
            "backend_config": sha256(Path(__file__).with_name("backend_config.py")),
            "runtime": {k: binding[k] for k in ("python", "wsl_distro") if k in binding},
            "source": plan["stages"][backend].get("source"),
            "environment": {key: plan["stages"][backend]["native_imports_and_gpu"].get(key) for key in ("python", "cuda_runtime")},
            "packages": plan["stages"][backend]["native_imports_and_gpu"].get("packages", {})}
        stage = Stage(job / backend, digest(identity), resume)
        if stage.state["status"] == "complete" and all(stage.valid(p) for p in stage.state["files"]):
            summary["stages"][backend] = {"status": "complete", "resumed": True, "candidates": len(read_json(stage.directory / "proposals.json")["candidates"])}
            continue
        request = {"backend": backend, "source_dir": str(source.resolve()), "prepared": str(prepared_path), "output": str(stage.directory.resolve()), "options": options}
        if backend == "spatiallm":
            request["model_dir"] = str(model.resolve())
        else:
            private = private_boxer_weights(binding, job / "boxer/cache/weights")
            request["private_weights"] = str(private)
            request["labels"] = labels
            request["frames"] = bound_frames
        executable, worker_path = binding["python"], str(worker.resolve())
        request_path = stage.directory / "request.local.json"
        if binding.get("wsl_distro"):
            for key in ("source_dir", "prepared", "output", "model_dir", "private_weights"):
                if key in request:
                    request[key] = wsl_path(request[key])
            for f in request.get("frames", []):
                f["image_path"] = wsl_path(f["image_path"])
            command = ["wsl.exe", "-d", binding["wsl_distro"], "--", executable, "-B", wsl_path(worker_path), wsl_path(request_path.resolve())]
        else:
            command = [executable, "-B", worker_path, str(request_path)]
        atomic_json(request_path, request)
        atomic_json(stage.directory / "identity.json", identity)
        start = time.perf_counter()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONIOENCODING="utf-8")
        with (stage.directory / "worker.log").open("a", encoding="utf-8") as log:
            result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            atomic_json(stage.directory / "failure.json", {"returncode": result.returncode, "log": "worker.log"})
            raise ContractError(f"{backend} worker failed ({result.returncode}); see {stage.directory / 'worker.log'}; completed units can be resumed")
        raw_path = "raw-predictions.json" if backend == "spatiallm" else "fused-predictions.json"
        proposals, review = normalize(backend, read_json(stage.directory / raw_path), prepared)
        atomic_json(stage.directory / "proposals.json", proposals)
        atomic_json(stage.directory / "review.json", review)
        for p in sorted(stage.directory.rglob("*")):
            if p.is_file() and "cache" not in p.relative_to(stage.directory).parts and p.name not in ("state.json", "request.local.json", "worker.log", "failure.json"):
                stage.record(p.relative_to(stage.directory).as_posix())
        stage.complete()
        summary["stages"][backend] = {"status": "complete", "candidates": len(proposals["candidates"]), "review_records": len(review), "seconds": time.perf_counter()-start}
    summary["passed"] = True
    summary["summary"] = result_status(content="passed", quality="not_evaluated",
        execution="partial" if any(s["status"] == "skipped" for s in summary["stages"].values()) else "completed",
        next_steps=["Review model proposals and geometry before accepting reconstruction quality"])
    atomic_json(job / "perception.json", summary)
    return summary
