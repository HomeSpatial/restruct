# SPDX-License-Identifier: Apache-2.0
"""Prepare scans, run perception, and build editable parameter scenes in Blender."""
import argparse
import datetime
import json
from pathlib import Path
import time
from jsonschema import ValidationError

from .contracts import ContractError, read_json, validate, validate_scene_bundle, verify_lock

# Task-time commands leave <out>/activity.json, so a task's history (including failed runs) can be replayed
ACTIVITY_COMMANDS = {"evidence", "build", "render", "residuals", "check-geometry", "check-materials"}
ACTIVITY_INPUTS = ("model", "blend", "view", "query", "requests")


def record_activity(args, started, result, error, out_existed):
    out = getattr(args, "out", None)
    if args.command not in ACTIVITY_COMMANDS or out is None or out_existed or not Path(out).is_dir():
        return  # only a directory this run created gets the record; a rejected rerun leaves the old one alone
    inputs = {name: str(Path(getattr(args, name)).resolve()) for name in ACTIVITY_INPUTS if getattr(args, name, None)}
    record = {"schema": "recon-activity/1", "command": args.command,
              "started_utc": datetime.datetime.fromtimestamp(started, datetime.timezone.utc).isoformat(),
              "seconds": round(time.time() - started, 3), "passed": bool(result and result.get("passed")),
              "error": error, "inputs": inputs}
    try:
        (Path(out) / "activity.json").write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(prog="restruct", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("verify-backends", help="Check selected backend configuration, interfaces, model loading or synthetic inference")
    p.add_argument("--runtime", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--backend", choices=["all", "spatiallm", "boxer"], default="all")
    p.add_argument("--level", choices=["config", "imports", "load", "smoke"], default="imports")
    p.add_argument("--labels-file", type=Path)
    p.add_argument("--timeout", type=int, default=600, help="Maximum seconds per backend worker")
    p = commands.add_parser("bundle", help="Inspect a bundle or publish an existing successful result without rerunning models")
    actions = p.add_subparsers(dest="bundle_action", required=True)
    q = actions.add_parser("append")
    q.add_argument("document", type=Path)
    q.add_argument("--result", required=True, type=Path)
    q.add_argument("--stage", required=True)
    q.add_argument("--from", dest="from_stage")
    q.add_argument("--operation", default="manual-result")
    q = actions.add_parser("inspect")
    q.add_argument("document", type=Path)
    for name in ("validate", "verify-lock"):
        p = commands.add_parser(name)
        p.add_argument("document", type=Path)
        p.add_argument("--root", action="append", default=[], metavar="NAME=PATH")
    p = commands.add_parser("init-task", help="Prepare a new scan and generate runtime/evidence/task bindings")
    p.add_argument("scene", type=Path, nargs="?")
    p.add_argument("--out", type=Path)
    p.add_argument("--runtime", type=Path)
    p.add_argument("--bundle", type=Path)
    p.add_argument("--from", dest="from_stage")
    p.add_argument("--stage", help="New immutable stage; default prepared")
    p.add_argument("--root", action="append", default=[], metavar="NAME=PATH")
    p.add_argument("--config", type=Path, help="Preparation options; inference is started separately")
    p.add_argument("--layout", choices=["spatiallm", "none"], default="spatiallm")
    p.add_argument("--objects", choices=["boxer", "none"], default="boxer")
    p.add_argument("--labels-file", type=Path)
    for flag in ("geometry-asset", "sparse-asset", "images-asset"):
        p.add_argument("--" + flag)
    p.add_argument("--no-photos", action="store_true")
    p = commands.add_parser("prepare")
    p.add_argument("scene", type=Path)
    p.add_argument("--out", type=Path, help="New job directory; optional for --preflight")
    p.add_argument("--root", action="append", default=[], metavar="NAME=PATH")
    p.add_argument("--config", type=Path)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--preflight", action="store_true", help="Validate inputs and show choices without creating a job")
    p.add_argument("--profile", choices=["layout", "handoff"], help="layout: existing analysis defaults; handoff: preserve +Z source axes")
    p.add_argument("--point-source", choices=["auto", "mesh", "point_cloud", "rgbd"])
    p.add_argument("--geometry-id")
    p.add_argument("--alignment", choices=["existing_or_auto", "auto", "preserve"])
    p.add_argument("--voxel-m", type=float)
    p = commands.add_parser("build")
    p.add_argument("model",type=Path)
    p.add_argument("--out",type=Path)
    p.add_argument("--runtime",type=Path)
    p.add_argument("--bundle", type=Path)
    p.add_argument("--from", dest="from_stage")
    p.add_argument("--stage", help="New immutable stage; default model")
    p.add_argument("--root",action="append",default=[],metavar="NAME=PATH")
    p.add_argument("--blend",type=Path)
    p.add_argument("--requests",type=Path,help="Additional versioned edit requests before save/export")
    p = commands.add_parser("evidence", help="Read-only camera, scan and photo queries")
    p.add_argument("config", type=Path)
    p.add_argument("query", type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--root", action="append", default=[], metavar="NAME=PATH")
    p = commands.add_parser("render", help="Render a saved project without saving review changes")
    p.add_argument("blend", type=Path)
    p.add_argument("--view", required=True, type=Path)
    p.add_argument("--runtime", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p = commands.add_parser("predict")
    p.add_argument("job", type=Path, nargs="?")
    p.add_argument("--runtime", type=Path)
    p.add_argument("--bundle", type=Path)
    p.add_argument("--from", dest="from_stage")
    p.add_argument("--stage", help="New immutable stage; default perception")
    p.add_argument("--out", type=Path, help="Bundle-mode working output; derived by default")
    p.add_argument("--layout", choices=["spatiallm", "none"], help="Defaults to bundle preparation choice, otherwise spatiallm")
    p.add_argument("--objects", choices=["boxer", "none"], help="Defaults to bundle preparation choice, otherwise boxer")
    p.add_argument("--labels-file", type=Path)
    p.add_argument("--root", action="append", default=[], metavar="NAME=PATH")
    p.add_argument("--detect-type", choices=["arch", "all"], help="Defaults to arch; bundle resume retains the previous choice")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--preflight", action="store_true", help="Check every requested stage before starting any model")
    p = commands.add_parser("inspect", help="Inspect a reconstruction description or scene without modifying it")
    p.add_argument("document", type=Path)
    p.add_argument("--root", action="append", default=[], metavar="NAME=PATH")
    p = commands.add_parser("import-description", help="Import a v1 reconstruction description or bundle with explicit asset/frame selection")
    p.add_argument("document", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--preflight", action="store_true")
    p.add_argument("--root", action="append", default=[], metavar="NAME=PATH")
    for flag in ("geometry-asset", "sparse-asset", "images-asset"):
        p.add_argument("--" + flag)
    p.add_argument("--frame-id", action="append", default=[])
    p.add_argument("--no-photos", action="store_true")
    p.add_argument("--max-side", type=int, default=768)
    p = commands.add_parser("import-colmap")
    p.add_argument("source", type=Path)
    for name in ("model-prefix", "images-prefix", "geometry-path", "scene-id"):
        p.add_argument("--"+name, required=True)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--up-axis", choices=["+X", "-X", "+Y", "-Y", "+Z", "-Z"], required=True)
    p.add_argument("--metres-per-unit", type=float, required=True)
    p.add_argument("--max-side", type=int, default=768)
    p.add_argument("--max-frames", type=int)
    p.add_argument("--frame-id", action="append", default=[])
    p.add_argument("--geometry-type", choices=["auto", "mesh", "point_cloud"], default="auto")
    p = commands.add_parser("residuals", help="Cluster scan surfaces that no model surface explains")
    p.add_argument("model", type=Path, help="Exported model-v2 GLB")
    p.add_argument("--startup", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--spacing", type=float, default=.04, help="Scan sample spacing in metres")
    p.add_argument("--threshold", type=float, default=.05, help="Distance beyond which a scan sample is unexplained")
    p.add_argument("--voxel", type=float, default=.12, help="Cluster connectivity voxel size")
    p.add_argument("--min-area", type=float, default=.02, help="Smallest reported cluster area in m2")
    p.add_argument("--top", type=int, help="Report only the largest N clusters (default: all above --min-area)")
    p.add_argument("--views", type=int, default=3, help="Best photos per cluster; 0 skips view search")
    p.add_argument("--bounds", type=float, nargs=6, metavar=("MINX", "MINY", "MINZ", "MAXX", "MAXY", "MAXZ"))
    p.add_argument("--excess-cameras", type=int, default=160, help="Frames used to find model surfaces the cameras saw through; 0 skips")
    p.add_argument("--excess-margin", type=float, default=.05, help="Scan depth beyond a model point that counts as seeing through it")
    p = commands.add_parser("check-geometry", help="Interference, coplanar overlap, shell leaks and category warnings")
    p.add_argument("model", type=Path, help="Exported model-v2 GLB")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--startup", type=Path, help="Enables shell-leak rays from capture cameras")
    p.add_argument("--tolerance", type=float, default=.005, help="Penetration depth reported as interference")
    p.add_argument("--min-overlap", type=float, default=.0025, help="Smallest reported coplanar overlap in m2")
    p.add_argument("--include-same-assembly", action="store_true")
    p = commands.add_parser("check-materials", help="Check exported PBR coverage, embedded images, UVs and source pixels")
    p.add_argument("model", type=Path, help="Exported model-v2 GLB")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--root", action="append", default=[], metavar="NAME=PATH")
    p.add_argument("--exceptions", type=Path, help="JSON mapping untextured material IDs to explicit reasons")
    p = commands.add_parser("view", help="Browse GLB models below a directory in a local web viewer")
    p.add_argument("directory", type=Path, nargs="?", default=Path("."))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8770)
    p.add_argument("--open", action="store_true", help="Open the viewer in the default browser")
    args = parser.parse_args(argv)
    if args.command == "view":
        from .viewer import serve
        try:
            return serve(args.directory, args.host, args.port, args.open)
        except (OSError, ValueError) as exc:
            print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
            return 1
    started, result, error = time.time(), None, None
    out_existed = getattr(args, "out", None) is not None and Path(args.out).exists()
    try:
        result = run_command(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["passed"] else 1
    except (ContractError, OSError, ValueError, KeyError, ValidationError) as exc:
        error = getattr(exc, "message", str(exc))
        print(json.dumps({"passed": False, "error": error}, ensure_ascii=False))
        return 1
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record_activity(args, started, result, error, out_existed)


def run_command(args):
    from ._exchange.exchange import root_bindings
    roots = root_bindings(getattr(args, "root", []))
    if args.command == "verify-backends":
        from .backend_verification import verify_backends
        from .startup import _runtime
        return verify_backends(_runtime(args.runtime), args.out, args.backend, args.level, args.labels_file, args.timeout)
    if args.command == "bundle":
        from ._exchange.bundle import EXTENSION, bundle_path, plan, publish
        from ._exchange.exchange import Catalog
        if args.bundle_action == "inspect":
            catalog = Catalog(bundle_path(args.document))
            return dict(passed=True, **catalog.summary(), stages=catalog.document.get("extensions", {}).get(EXTENSION, {}))
        scene_id = read_json(args.result)["scene_id"]
        stage_plan = plan(args.document, args.stage, args.operation, source=args.from_stage, scene_id=scene_id)
        return dict(passed=True, publication=publish(stage_plan, args.result))
    if getattr(args, "bundle", None):
        from .bundle_workflow import execute
        return execute(args, roots)
    if args.command in {"init-task", "predict", "build"}:
        if getattr(args, "from_stage", None) or getattr(args, "stage", None):
            raise ContractError("--from/--stage require --bundle")
        required = {"init-task": ("scene", "out", "runtime"), "predict": ("job", "runtime"), "build": ("out", "runtime")}[args.command]
        if any(getattr(args, field, None) is None for field in required):
            raise ContractError("Without --bundle, supply " + ", ".join(required))
    if args.command == "inspect":
        document = read_json(args.document)
        if document.get("kind") in ("reconstruction_description", "reconstruction_bundle"):
            from ._exchange.exchange import Catalog
            return dict(passed=True, **Catalog(args.document, roots).summary())
        from .preparation import prepare
        return prepare(args.document, None, roots, preflight=True)
    if args.command == "import-description":
        from .exchange import import_description
        return import_description(args.document, args.out, roots, args.geometry_asset, args.sparse_asset,
                                  args.images_asset, args.frame_id, args.no_photos, args.max_side, args.preflight)
    if args.command == "residuals":
        from .model_checks import residuals
        box = [args.bounds[:3], args.bounds[3:]] if args.bounds else None
        result = residuals(args.startup, args.model, args.out, args.spacing, args.threshold, args.voxel, args.min_area,
                           args.top, args.views, box, excess_cameras=args.excess_cameras, excess_margin_m=args.excess_margin)
        excess = result["excess"] or {}
        result = {k: result[k] for k in ("passed", "scan_area_m2", "unexplained_area_m2", "scan_area_within_m", "clusters_total")} | {
            "excess_area_m2": excess.get("excess_area_m2"), "excess_clusters": excess.get("clusters_total"), "plan_fit_shifted": len(result["plan_fit"]["shifted"]),
            "facing_fit_turned": len(result["facing_fit"]["turned"]),
            "report": str(args.out / "residuals.json")}
    elif args.command == "check-geometry":
        from .model_checks import geometry
        result = geometry(args.model, args.out, args.startup, args.tolerance, min_overlap_m2=args.min_overlap,
                          include_same_assembly=args.include_same_assembly)
        result = {"passed": result["passed"], "counts": result["counts"], "report": str(args.out / "geometry.json")}
    elif args.command == "check-materials":
        from .material_checks import check_materials
        report = check_materials(args.model, args.out, roots if args.root else None,
                                 read_json(args.exceptions) if args.exceptions else None)
        result = {k: report[k] for k in ("passed", "objects", "images", "textured_primitives", "source_pixels_checked")}
        result["report"] = str(args.out / "materials.json")
    elif args.command == "import-colmap":
        from .colmap import import_colmap
        result = import_colmap(args.source, args.model_prefix, args.images_prefix, args.geometry_path, args.out,
            args.scene_id, args.up_axis, args.metres_per_unit, args.max_side, args.max_frames,
            geometry_type=args.geometry_type, frame_ids=args.frame_id)
    elif args.command == "evidence":
        from .evidence import run
        result = run(args.config, args.query, args.out, roots)
    elif args.command == "render":
        from .blender import render
        result = render(args.blend, args.out, read_json(args.runtime), read_json(args.view))
    elif args.command == "build":
        from .blender import build
        result=build(args.model,args.out,read_json(args.runtime),roots,args.blend,read_json(args.requests) if args.requests else None)
    elif args.command == "init-task":
        from .startup import initialize
        result = initialize(args.scene, args.out, args.runtime, roots, read_json(args.config) if args.config else None,
                            args.layout, args.objects, args.labels_file,
                            exchange_selection=dict(geometry_id=args.geometry_asset, sparse_id=args.sparse_asset,
                                                    images_id=args.images_asset, no_photos=args.no_photos))
    elif args.command == "prepare":
        from .preparation import prepare
        options = {"alignment": "preserve"} if args.profile == "handoff" else {}
        options.update(read_json(args.config) if args.config else {})
        options.update({k: getattr(args, k) for k in ("point_source", "geometry_id", "alignment", "voxel_m") if getattr(args, k) is not None})
        result = prepare(args.scene, args.out, roots, options, args.resume, args.preflight)
    elif args.command == "predict":
        from .perception import predict
        from .startup import _runtime
        result = predict(args.job, _runtime(args.runtime), args.layout or "spatiallm", args.objects or "boxer", args.labels_file, roots, args.resume, args.detect_type or "arch", args.preflight)
    else:
        roots.setdefault("bundle", args.document.parent)
        raw = read_json(args.document)
        if args.command == "validate" and raw.get("kind") in ("reconstruction_description", "reconstruction_bundle"):
            from ._exchange.verify import check
            return dict(passed=True, **check(args.document, roots))
        document = validate(raw)
        result = verify_lock(document, roots) if args.command == "verify-lock" else {"passed": True, "kind": document["kind"]}
        if args.command == "validate" and document["kind"] == "scene":
            result["input_eligibility"] = validate_scene_bundle(document, roots)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
