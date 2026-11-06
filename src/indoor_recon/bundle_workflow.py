# SPDX-License-Identifier: Apache-2.0
"""Bundle entry points for prepared tasks, perception proposals and built models."""
import copy
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from . import __version__
from ._exchange.bundle import atomic_json, bundle_path, copy_snapshot, plan, publish
from ._exchange.verify import asset_refs, check
from .contracts import ContractError, read_json, resolve_asset, sha256
from .jobs import atomic_json as checkpoint_json, prediction_lock


def _runtime_file(args, bundle):
    from .startup import _runtime
    path = Path(bundle).parent / ".bundle/runtime.local.json"
    if args.runtime:
        value = _runtime(args.runtime)
    elif path.exists():
        value = _runtime(path)
    else:
        value = {}
    return path, value


def _file(root, path, role):
    path = Path(path)
    return dict(role=role, ref=dict(root="local", path=path.relative_to(root).as_posix(), sha256=sha256(path)))


def _task_snapshot(output, scene_id, *, perception=False, prediction_defaults=None):
    """Retain portable preparation and inputs, excluding bindings, caches and mutable state."""
    root = output / "exchange"
    if root.exists():
        raise ContractError("Snapshot output already exists")
    root.mkdir()
    shutil.copytree(output / "input", root / "input")
    shutil.copytree(output / "job/prepare", root / "job/prepare")
    # This state is a frozen preparation receipt, not the working inference state.
    scene = read_json(root / "input/scene.json")
    prepared = read_json(root / "job/prepare/prepared.json")
    coordinates = [dict(scene["source_coordinates"], id="source"),
                   dict(id="work", handedness="right", up_axis="+Z", metres_per_unit=1)]
    files = [_file(root, root / "job/prepare/prepared.json", "primary")]
    files += [_file(root, p, "dependency") for directory in (root / "input", root / "job/prepare")
              for p in sorted(directory.rglob("*")) if p.is_file() and p != root / "job/prepare/prepared.json"]
    assets = [dict(id="prepared", type="prepared_task", format="indoor-recon.prepared", format_version="1", files=files),
              dict(id="geometry", type="point_cloud", format="ply", coordinate_system="work",
                   files=[_file(root, root / "job/prepare/global.ply", "primary")])]
    roles = dict(prepared="prepared", geometry="geometry")
    if perception:
        reports = []
        for backend in ("spatiallm", "boxer"):
            backend_root = output / "job" / backend
            for source in sorted(backend_root.rglob("*")):
                if (source.is_file() and "cache" not in source.relative_to(backend_root).parts
                        and not source.name.endswith(".tmp")
                        and source.name not in ("state.json", "identity.json", "request.local.json", "worker.log", "failure.json")):
                    target = root / "job" / backend / source.relative_to(backend_root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    reports.append(_file(root, target, "proposals" if source.name == "proposals.json" else "report"))
        shutil.copyfile(output / "job/perception.json", root / "perception.json")
        reports.append(_file(root, root / "perception.json", "summary"))
        assets.append(dict(id="proposals", type="perception_proposals", format="indoor-recon.proposals", format_version="1", files=reports))
        roles["proposals"] = "proposals"
    doc = dict(kind="reconstruction_description", schema_version=1, scene_id=scene_id,
        packaging="self_contained", coordinate_systems=coordinates, assets=assets,
        coordinate_transforms=[dict(**{"from": "source", "to": "work"}, kind="rigid", units="metres",
                                   T_to_from=prepared["alignment"], method="indoor-preparation",
                                   evidence=[_file(root, root / "job/prepare/prepared.json", "primary")["ref"]])],
        extensions={"org.indoor_recon.task": dict(version=1, input="input", job="job",
                    frame_ids=[f["id"] for f in prepared["frames"]], prediction_defaults=prediction_defaults or {},
                    quality={"status": "not_evaluated"})})
    path = root / "reconstruction.json"
    atomic_json(path, doc)
    check(path)
    return path, roles


def _model_snapshot(output, model_path, roots):
    """Publish the actual model embedded in Blender, with portable dependencies."""
    from .contracts import validate
    root = output / "exchange"
    root.mkdir()
    responses = read_json(output / "response.json")
    parameters = copy.deepcopy(responses[-1]["data"]["model"])
    bindings = dict(roots)
    bindings.setdefault("bundle", Path(model_path).resolve().parent)
    bindings.setdefault("local", Path(model_path).resolve().parent)
    # All model refs are embedded in the parameter document; retain and rebind them.
    dependencies = []
    for ref in asset_refs(parameters):
        source = resolve_asset(ref, bindings)
        digest = sha256(source)
        if ref.get("sha256") and ref["sha256"] != digest:
            raise ContractError("Model dependency changed before publication")
        target = root / "assets" / (digest + source.suffix)
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        ref.update(root="local", path=target.relative_to(root).as_posix(), sha256=digest)
        dependencies.append(target)
    validate(parameters)
    atomic_json(root / "model.json", parameters)
    for name in ("model.glb", "model.blend", "build.json"):
        shutil.copyfile(output / name, root / name)
    assets = [dict(id="model", type="parametric_scene", format="indoor-recon.model", format_version="2", coordinate_system="work",
                   files=[_file(root, root / "model.json", "primary")]),
              dict(id="display", type="mesh", format="glb", coordinate_system="gltf",
                   files=[_file(root, root / "model.glb", "primary")]),
              dict(id="editable", type="editable_scene", format="blender", coordinate_system="work",
                   files=[_file(root, root / "model.blend", "primary")]),
              dict(id="validation", type="validation_report", format="json", files=[_file(root, root / "build.json", "primary")])]
    if dependencies:
        assets.append(dict(id="dependencies", type="model_dependencies", format="native.files",
                           files=[_file(root, p, "dependency") for p in sorted(set(dependencies))]))
    path = root / "reconstruction.json"
    atomic_json(path, dict(kind="reconstruction_description", schema_version=1, scene_id=parameters["scene_id"],
        packaging="self_contained", coordinate_systems=[dict(id="work", handedness="right", up_axis="+Z", metres_per_unit=1),
            dict(id="gltf", handedness="right", up_axis="+Y", metres_per_unit=1)], assets=assets,
        coordinate_transforms=[dict(**{"from": "gltf", "to": "work"}, kind="rigid", units="metres",
            T_to_from=[[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]], method="Blender-glTF-axis-conversion",
            evidence=[_file(root, root / "model.glb", "primary")["ref"]])],
        extensions={"org.indoor_recon.build": {"quality": {"status": "not_evaluated"}, "revision": parameters["revision"]}}))
    check(path)
    return path, {"model": "model", "geometry": "display"}


def _prediction_identity(stage_plan, args, runtime):
    labels = runtime.get("boxer", {}).get("labels_file") if args.objects == "boxer" else None
    return dict(scene_id=stage_plan["scene_id"], stage=stage_plan["stage"],
        input_stage=stage_plan["input"]["id"], input_sha256=stage_plan["input"]["sha256"],
        options=dict(layout=args.layout, objects=args.objects, detect_type=args.detect_type),
        runtime=runtime, labels_sha256=sha256(labels) if labels and Path(labels).is_file() else None)


def _predict_bundle(args, stage_plan, source, output, runtime_file, runtime, receipt):
    from .perception import predict
    if args.job:
        raise ContractError("Bundle prediction derives its job; omit the positional job")
    identity = _prediction_identity(stage_plan, args, runtime)
    receipt_path = output / "prediction-run.json"
    if args.resume:
        if not receipt or receipt.get("version") != 1 or receipt.get("identity") != identity:
            raise ContractError("Resume input/configuration differs or no prediction receipt exists; use the original settings or a new stage")
        cloned_source = output / source.name
        if not cloned_source.is_file() or sha256(cloned_source) != identity["input_sha256"]:
            raise ContractError("Prepared snapshot changed in working output")
        # Check the actual assets, not only the descriptor or inference cache.
        check(source)
        check(cloned_source)
    else:
        # Hydrate privately; the committed prepared stage is never modified.
        copy_snapshot(source, output)
        receipt = dict(version=1, identity=identity, status="predicting")
        checkpoint_json(receipt_path, receipt)

    snapshot = output / "exchange/reconstruction.json"
    if receipt.get("status") == "snapshot_ready":
        if not snapshot.is_file() or sha256(snapshot) != receipt["snapshot_sha256"]:
            raise ContractError("Completed prediction snapshot changed before publication")
        check(snapshot)
        return copy.deepcopy(receipt["result"]), snapshot, receipt["roles"]

    atomic_json(output / "job/bindings.local.json", {"bundle": str(output / "input")})
    atomic_json(runtime_file, runtime)
    result = predict(output / "job", runtime, args.layout, args.objects, args.labels_file,
                     {}, args.resume, args.detect_type)
    if not result.get("passed"):
        return result, None, None
    # A previous snapshot attempt may have stopped while copying. Preserve that
    # private attempt for inspection, then rebuild from verified completed units.
    exchange = output / "exchange"
    if exchange.exists():
        if exchange.is_symlink() or not exchange.resolve().is_relative_to(output.resolve()):
            raise ContractError("Snapshot working directory escapes prediction output")
        archived = output / ("exchange-interrupted-" + uuid4().hex)
        if not archived.resolve().is_relative_to(output.resolve()):
            raise ContractError("Unsafe interrupted snapshot archive")
        exchange.rename(archived)
    snapshot, roles = _task_snapshot(output, stage_plan["scene_id"], perception=True,
        prediction_defaults=dict(layout=args.layout, objects=args.objects))
    receipt.update(status="snapshot_ready", snapshot_sha256=sha256(snapshot), roles=roles, result=result)
    checkpoint_json(receipt_path, receipt)
    return result, snapshot, roles


def _plan_command(args):
    operation = {"init-task": "indoor.prepare", "predict": "indoor.predict", "build": "indoor.build"}[args.command]
    default = {"init-task": "prepared", "predict": "perception", "build": "model"}[args.command]
    required = ("geometry",) if args.command == "init-task" else ("prepared",)
    preferred = {"init-task": "remap", "predict": "indoor.prepare", "build": "indoor.predict"}[args.command]
    return plan(args.bundle, args.stage or default, operation, required=required,
                source=args.from_stage, preferred=preferred, out=getattr(args, "out", None))


def execute(args, roots):
    stage_plan = _plan_command(args)
    if args.command == "predict" and not args.preflight:
        with prediction_lock(Path(stage_plan["output"])):
            # Recheck after acquiring ownership in case another run just published.
            return _execute(args, roots, _plan_command(args))
    return _execute(args, roots, stage_plan)


def _execute(args, roots, stage_plan):
    source = Path(stage_plan["input"]["document"])
    output = Path(stage_plan["output"])
    resume = args.command == "predict" and args.resume
    receipt_path = output / "prediction-run.json"
    receipt = read_json(receipt_path) if resume and receipt_path.is_file() else None
    if output.exists() and not resume and not (args.command == "predict" and args.preflight):
        raise ContractError("Bundle stage work directory exists; choose a new --stage or --out")
    if resume and not output.exists():
        raise ContractError("No prediction work directory to resume; omit --resume for a new run")
    runtime_file, runtime = _runtime_file(args, stage_plan["bundle"])
    if getattr(args, "labels_file", None):
        runtime.setdefault("boxer", {})["labels_file"] = str(args.labels_file.resolve())
    if args.command == "predict":
        defaults = read_json(source).get("extensions", {}).get("org.indoor_recon.task", {}).get("prediction_defaults", {})
        if receipt:
            defaults = receipt.get("identity", {}).get("options", defaults)
        args.layout = args.layout or defaults.get("layout", "spatiallm")
        args.objects = args.objects or defaults.get("objects", "boxer")
        args.detect_type = args.detect_type or defaults.get("detect_type", "arch")
    if args.command == "predict" and args.preflight:
        from .perception import prediction_plan
        prepared = read_json(source.parent / "job/prepare/prepared.json")
        return prediction_plan(prepared, runtime, args.layout, args.objects, args.labels_file)
    if args.command == "init-task":
        from .startup import initialize
        if args.scene:
            raise ContractError("Use scene or --bundle, not both")
        atomic_json(runtime_file, runtime)
        role = stage_plan["input"]["roles"]
        result = initialize(source, output, runtime_file, {}, read_json(args.config) if args.config else None,
            args.layout, args.objects, args.labels_file,
            exchange_selection=dict(geometry_id=args.geometry_asset or role["geometry"],
                sparse_id=args.sparse_asset or role.get("sparse"), images_id=args.images_asset or role.get("images"),
                no_photos=args.no_photos))
        startup_path = output / "startup.json"
        startup = read_json(startup_path)
        cli = [sys.executable, "-B", "-m", "indoor_recon"]
        command = [*cli, "predict", "--bundle", stage_plan["bundle"], "--from", stage_plan["stage"],
                   "--layout", args.layout, "--objects", args.objects]
        startup["commands"]["predict"] = command if result["prediction_preflight"]["passed"] else None
        startup["commands"]["predict_preflight"] = [*command, "--preflight"]
        root_flags = [v for k, v in enumerate(startup["commands"]["build_prefix"])
                      if v == "--root" or (k and startup["commands"]["build_prefix"][k-1] == "--root")]
        startup["commands"]["build_prefix"] = [*cli, "build", "--bundle", stage_plan["bundle"], *root_flags]
        atomic_json(startup_path, startup)
        snapshot, roles = _task_snapshot(output, stage_plan["scene_id"],
            prediction_defaults=dict(layout=args.layout, objects=args.objects))
    elif args.command == "predict":
        result, snapshot, roles = _predict_bundle(args, stage_plan, source, output, runtime_file, runtime, receipt)
    else:
        from .blender import build
        if not args.model:
            raise ContractError("build requires an authored model; proposals are not accepted models")
        if read_json(args.model).get("scene_id") != stage_plan["scene_id"]:
            raise ContractError("Model scene identity disagrees with bundle")
        atomic_json(runtime_file, runtime)
        result = build(args.model, output, runtime, roots, args.blend, read_json(args.requests) if args.requests else None)
        snapshot, roles = _model_snapshot(output, args.model, roots)
    if not result.get("passed"):
        return result
    from ._exchange.exchange import Catalog
    input_catalog = Catalog(source)
    if args.command == "init-task":
        selected = args.geometry_asset or stage_plan["input"]["roles"]["geometry"]
        source_coordinate = input_catalog.assets[selected]["coordinate_system"]
        target_coordinate = "source"
    else:
        source_coordinate = target_coordinate = "work"
    result["publication"] = publish(stage_plan, snapshot, roles=roles,
        execution=result.get("summary", {}).get("execution", "completed"),
        coordinate_link=(source_coordinate, target_coordinate))
    return result
