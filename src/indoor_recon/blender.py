# SPDX-License-Identifier: Apache-2.0
"""Launch the existing add-on in an isolated Blender process."""
import os
from pathlib import Path
import subprocess

from .contracts import ContractError, read_json, validate
from .jobs import atomic_json


def render(blend, output, runtime, view):
    """Render in an isolated process; the input .blend is never saved."""
    from .contracts import sha256
    blend, output = Path(blend).resolve(), Path(output).resolve()
    if output.exists():
        raise ContractError("Render requires a new output directory")
    if not blend.is_file() or "filepath" in view or "overwrite" in view:
        raise ContractError("Render needs an existing blend and a view without output path overrides")
    binding = runtime["blender"]
    runner = Path(binding["addon_dir"]) / "run_agent.py"
    if not runner.is_file():
        raise ContractError("Blender add-on runner is missing")
    before = sha256(blend)
    output.mkdir(parents=True)
    atomic_json(output / "request.json", [{"op": "render_review", "args": dict(view, filepath=str(output/"review.png"))}])
    command = [binding["executable"], "--background", "--factory-startup", "--python-exit-code", "1",
               "--python", str(runner.resolve()), "--", "--blend", str(blend),
               "--request", str(output/"request.json"), "--response", str(output/"response.json")]
    with (output / "blender.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8"))
    if result.returncode or not (output/"response.json").is_file():
        raise ContractError(f"Blender render failed; inspect {output/'blender.log'}")
    responses = read_json(output/"response.json")
    if len(responses) != 1 or not responses[0]["ok"] or sha256(blend) != before:
        raise ContractError("Render failed or input blend changed")
    report = {"passed": True, "input_blend_sha256": before, "input_blend_unchanged": True,
              "image_sha256": sha256(output/"review.png"), "data": responses[0]["data"]}
    atomic_json(output/"render.json", report)
    return report


def build(model_path,output,runtime,roots=None,blend=None,requests=None):
    model_path=Path(model_path).resolve();output=Path(output).resolve()
    if output.exists():raise ContractError("Build requires a new output directory")
    parameters=validate(read_json(model_path))
    if parameters.get("schema_version")!=2:raise ContractError("Blender creation requires a parameter model v2")
    roots={k:str(Path(v).resolve()) for k,v in (roots or {}).items()};roots.setdefault("bundle",str(model_path.parent))
    roots.setdefault("local", str(model_path.parent))
    from . import scene_model
    for row in parameters["objects"]:
        if row["geometry"]["type"]=="mesh_reference":scene_model.resolve(row["geometry"]["asset"],roots)
    binding=runtime["blender"];runner=Path(binding["addon_dir"])/"run_agent.py"
    if not runner.is_file():raise ContractError("Blender add-on runner is missing")
    output.mkdir(parents=True)
    commands=[]
    if blend:
        # A supplied file is an explicitly versioned replacement of the embedded model.
        commands.append({"op":"apply_scene","args":{"model":parameters,"expected_revision":parameters["revision"]-1,"roots":roots}})
    else:commands.append({"op":"create_scene","args":{"model":parameters,"roots":roots,"source_model_path":str(model_path)}})
    if requests:commands.extend(requests)
    commands.extend([{"op":"rebuild_scene"},{"op":"validate_scene"},
        {"op":"save_scene","args":{"filepath":str(output/"model.blend")}},
        {"op":"export_scene","args":{"filepath":str(output/"model.glb")}},
        {"op":"inspect_scene"}])
    atomic_json(output/"request.json",commands)
    command=[binding["executable"],"--background","--factory-startup","--python-exit-code","1","--python",str(runner.resolve()),"--","--contract-src",str(Path(__file__).resolve().parents[1]),"--request",str(output/"request.json"),"--response",str(output/"response.json")]
    if blend:command.extend(["--blend",str(Path(blend).resolve())])
    with (output/"blender.log").open("w",encoding="utf-8") as log:
        result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1",PYTHONIOENCODING="utf-8"))
    responses=read_json(output/"response.json") if (output/"response.json").exists() else []
    if result.returncode or not responses or any(not r["ok"] or (r.get("data",{}).get("passed") is False) for r in responses):raise ContractError(f"Blender build failed; inspect {output/'blender.log'} and response.json")
    report={"passed":True,"scene_id":parameters["scene_id"],"output":str(output),"responses":len(responses),"revision":responses[-1]["data"]["model"]["revision"]}
    atomic_json(output/"build.json",report)
    return report
