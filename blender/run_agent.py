# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import json
import sys
from pathlib import Path

runner_root=Path(__file__).resolve().parent
sys.path.insert(0, str(runner_root.parent if runner_root.name=="homespatial_model" else runner_root))
import bpy
import homespatial_model
from homespatial_model import api

parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True)
parser.add_argument("--response", required=True)
parser.add_argument("--blend")
parser.add_argument("--contract-src", help="Python source directory containing indoor_recon; installed add-on bundles the contract")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
contract_src = Path(args.contract_src).resolve() if args.contract_src else runner_root.parent / "src"
if args.contract_src or (contract_src / "indoor_recon/scene_model.py").is_file():
    sys.path.insert(0, str(contract_src))
homespatial_model.register()
if args.blend:
    bpy.ops.wm.open_mainfile(filepath=str(Path(args.blend).resolve()))
request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
requests = request if isinstance(request, list) else [request]
responses = []
for command in requests:
    try:
        result = api.dispatch(command)
    except Exception as error:
        result = {"ok": False, "request_id": command.get("request_id"),
                  "error": {"code": "EXECUTION", "message": str(error)}}
    responses.append(result)
    if not result["ok"]:
        break
destination = Path(args.response)
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(responses, ensure_ascii=False, indent=2), encoding="utf-8")
if any(not result["ok"] for result in responses):
    raise RuntimeError("HomeSpatial command failed; inspect the response JSON")
