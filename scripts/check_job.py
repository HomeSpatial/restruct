"""Read-only output integrity, projection and candidate lineage checks for a CLI job."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
import numpy as np
from indoor_recon.contracts import ContractError, project, read_json, rigid, sha256, validate
from indoor_recon.jobs import atomic_json


def check(job):
    job=Path(job)
    prepared=read_json(job/"prepare/prepared.json")
    report={"scene_id":prepared["scene_id"],"frames":len(prepared["frames"]),"regions":len(prepared["regions"]),"stages":{}}
    rigid(prepared["alignment"])
    known_frames={f["id"] for f in prepared["frames"]}
    for backend in ("prepare","spatiallm","boxer"):
        directory=job/backend
        if not (directory/"state.json").exists():continue
        state=read_json(directory/"state.json")
        if state["status"]!="complete":raise ContractError(f"Incomplete stage: {backend}")
        for relative,expected in state["files"].items():
            if sha256(directory/relative)!=expected:raise ContractError(f"Changed output {backend}/{relative}")
        report["stages"][backend]={"pinned_files":len(state["files"])}
        if backend=="prepare":continue
        proposals=validate(read_json(directory/"proposals.json"))
        if proposals["scene_id"]!=prepared["scene_id"]:raise ContractError("Candidate scene differs")
        for candidate in proposals["candidates"]:
            for source in candidate["sources"]:
                ref=source["reference"]
                if ref["root"]!="job" or not (job/ref["path"]).is_file():raise ContractError("Candidate raw reference is unavailable")
                if not set(source.get("frame_ids",[]))<=known_frames:raise ContractError("Unknown source frame")
        report["stages"][backend]["candidates"]=len(proposals["candidates"])
        if backend=="spatiallm":
            raw=read_json(directory/"raw-predictions.json")
            report["stages"][backend]["token_limits_reached"]=sum(r["token_limit_reached"] for r in raw["regions"])
            report["stages"][backend]["offsets_work_m"]=[r["offset_work_m"] for r in raw["regions"]]
        else:
            raw=read_json(directory/"raw-predictions.json")["detections"]
            by_id={r["id"]:r for r in raw}
            if len(by_id)!=len(raw):raise ContractError("Duplicate per-frame detection IDs")
            accepted=read_json(directory/"accepted-index.json")
            for row in accepted:
                if row["id"] not in by_id or by_id[row["id"]]["score3d"]<.5:raise ContractError("Invalid accepted detection")
            for row in read_json(directory/"fused-predictions.json")["objects"]:
                support=[by_id[k] for k in row["detection_ids"]]
                if len(support)!=row["support_count"] or sorted({r["frame_id"] for r in support})!=row["source_frames"]:raise ContractError("Fusion lineage differs")
            report["stages"][backend].update(raw=len(raw),accepted=len(accepted))
    max_px,max_m,samples=0.,0.,0
    for frame in prepared["frames"]:
        data=np.load(job/"prepare"/frame["points_path"])
        if not len(data["points"]):continue
        indices=np.linspace(0,len(data["points"])-1,min(16,len(data["points"])),dtype=int)
        points=data["points"][indices]
        uv,z=project(points,data["K"],data["T"])
        max_px=max(max_px,float(np.linalg.norm(uv-data["uv"][indices],axis=1).max()))
        max_m=max(max_m,float(np.abs(z-data["depth"][indices]).max()))
        samples+=len(indices)
    report["projection"]={"samples":samples,"max_uv_error_px":max_px,"max_camera_z_error_m":max_m,
        "interpretation":"Numerical consistency of stored points/calibration; no physical-accuracy claim"}
    # Loose relative to float32 arithmetic at these input resolutions, unrelated to scene fit quality.
    if max_px>.01 or max_m>1e-4:raise ContractError("Stored points and camera calibration are inconsistent")
    report["passed"]=True
    return report


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("job",type=Path);p.add_argument("--report",required=True,type=Path)
    args=p.parse_args();result=check(args.job);atomic_json(args.report,result)
    print(__import__("json").dumps(result))
