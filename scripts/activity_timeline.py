"""Activity timeline of a reconstruction task for the viewer's process mode.

python scripts/activity_timeline.py TASK_DIR --out NEW_DIR [--codex-log ROLLOUT.jsonl ...] [--root VIEWER_ROOT]

Tool runs come from the task directory itself (evidence.json, build.json, render.json, residuals.json,
geometry.json and ray-comparison JSON), timed by file modification; the CLI's activity.json adds durations and
failed runs. A Codex session log adds what the agent
said and the images it looked at; each image is saved under NEW_DIR/img and named after the file it matches.
Paths in timeline.json are relative to the viewer root (default: the parent of NEW_DIR).
Several logs (a task continued over several sessions) join into one timeline: the idle time between sessions is
cut out, and `sessions` records where each one starts so players can show the real clock time.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import numpy as np

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def iso_seconds(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def images_in(directory):
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def tool_events(task):
    """One event per tool output directory, at the time its report was written."""
    events, compares = [], {}
    for directory, dirs, files in os.walk(task):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "job")
        here = Path(directory)
        # activity.json (written by the CLI for task-time commands) adds durations and runs that failed before their report
        activity = read_json(here / "activity.json") if "activity.json" in files else None
        def event(marker, **fields):
            extra = {"seconds": activity["seconds"]} if activity else {}
            events.append({"time": (here / marker).stat().st_mtime, "name": here.name, "dir": here,
                           "images": images_in(here), **extra, **fields})
        if activity and not activity.get("passed"):
            event("activity.json", kind="run", op=activity.get("command"), failed=True, error=activity.get("error"))
            continue
        if "build.json" in files:
            report = read_json(here / "build.json") or {}
            if report.get("passed") and "model.glb" in files:
                event("build.json", kind="revision", revision=report.get("revision"), model=here / "model.glb")
        if "evidence.json" in files:
            query = (read_json(here / "evidence.json") or {}).get("query") or {}
            event("evidence.json", kind="run", op="evidence", query=query.get("op"), frame_id=query.get("frame_id"))
        if "render.json" in files:
            event("render.json", kind="run", op="render")
        if "residuals.json" in files:
            report = read_json(here / "residuals.json") or {}
            event("residuals.json", kind="run", op="residuals", revision=report.get("model_revision"),
                  summary={"within_5cm": report.get("scan_area_within_m", {}).get("0.05"),
                           "unexplained_m2": report.get("unexplained_area_m2")})
        if "geometry.json" in files:
            report = read_json(here / "geometry.json") or {}
            event("geometry.json", kind="run", op="check-geometry", revision=report.get("model_revision"),
                  summary=report.get("counts"))
        for name in files:
            if name.endswith(".json") and Path(name).with_suffix(".rays.npz").name in files:
                compares.setdefault(here, []).append((here / name).stat().st_mtime)
    for here, times in compares.items():
        events.append({"time": max(times), "name": here.name, "dir": here, "images": [], "kind": "run", "op": "compare",
                       "domains": len(times)})
    # a revision rebuilt later (retry after a fix is a new revision; reopen checks are not) counts once
    first = {}
    for e in sorted(events, key=lambda e: e["time"]):
        if e["kind"] == "revision":
            first.setdefault(e["revision"], e)
    return [e for e in events if e["kind"] != "revision" or first[e["revision"]] is e]


def codex_events(log, out, root, search, count=0):
    """Assistant messages and viewed images from a Codex rollout log."""
    by_size = {}
    for directory, dirs, files in os.walk(search):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if "timeline.json" in files or Path(directory).resolve() == out:
            dirs[:] = []  # earlier timelines hold copies of the same images
            continue
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() in IMAGE_SUFFIXES:
                by_size.setdefault(path.stat().st_size, []).append(path)
    (out / "img").mkdir(parents=True, exist_ok=True)
    prefix = str(search).replace("\\", "/").rstrip("/") + "/"
    def requested(call_input):
        """Paths passed to view_image in a tool call, relative to the run; template parts become *."""
        found = re.findall(r"view_image\(\{\s*path\s*:\s*([`\"'])(.*?)\1", call_input or "")
        return [re.sub(r"\$\{[^}]*\}", "*", path.replace("\\\\", "/").replace("\\", "/")).replace(prefix, "")
                for _, path in found]
    calls, events, start, end = {}, [], None, None
    with open(log, encoding="utf-8") as lines:
        for line in lines:
            row = json.loads(line)
            payload, time = row.get("payload") or {}, iso_seconds(row["timestamp"])
            start, end = start or time, time
            if payload.get("type") in ("custom_tool_call", "function_call"):
                calls[payload.get("call_id")] = payload.get("input") or payload.get("arguments")
            elif payload.get("type") == "message" and payload.get("role") == "assistant":
                text = "".join(c.get("text", "") for c in payload.get("content", []) if isinstance(c, dict)).strip()
                if text:
                    events.append({"time": time, "kind": "say", "text": text})
            elif payload.get("type") in ("custom_tool_call_output", "function_call_output"):
                output = payload.get("output")
                pictures = [item for item in output if item.get("type") == "input_image"] if isinstance(output, list) else []
                paths = requested(calls.get(payload.get("call_id")))
                # one path per image, or a single (looped) pattern for all of them
                hints = paths if len(paths) == len(pictures) else [paths[0] if len(paths) == 1 else None] * len(pictures)
                saved = []
                for item, hint in zip(pictures, hints):
                    match = re.match(r"data:image/(\w+);base64,(.*)", item.get("image_url", ""), re.S)
                    if not match:
                        continue
                    data = base64.b64decode(match.group(2))
                    source = next((p for p in by_size.get(len(data), []) if p.read_bytes() == data), None)
                    count += 1
                    target = out / "img" / f"{count:04d}.{'jpg' if match.group(1) == 'jpeg' else match.group(1)}"
                    target.write_bytes(data)
                    # source: the identical file on disk; requested: what the call asked for (images may be resized)
                    saved.append({"path": target.relative_to(root).as_posix(),
                                  "source": source.relative_to(search).as_posix() if source else None,
                                  "requested": hint})
                if saved:
                    events.append({"time": time, "kind": "look", "images": saved})
    return events, start, end, count


def frame_poses(startup):
    """Frame id -> (K, T_work_from_camera, (width, height)) from the task's prepared job."""
    prepare = Path(startup["job"]) / "prepare"
    poses = {}
    for frame in read_json(prepare / "prepared.json")["frames"]:
        with np.load(prepare / frame["points_path"]) as data:
            poses[frame["id"]] = (data["K"], data["T"], frame["size"])
    return poses


def frames_seeing(box, poses, scene, min_points=1, margin=.25):
    """Frames whose photo may show part of the work-space box unoccluded by the scan.

    Errs towards more frames: samples up to `margin` of the image size beyond its edges count, since estimated
    camera poses can put an object just outside a photo that actually shows it at the edge.
    """
    import open3d as o3d
    lo, hi = box
    grid = np.linspace(0, 1, 7)
    samples = []
    for axis in range(3):  # 7 x 7 samples on each of the six faces
        u, v = [a for a in range(3) if a != axis]
        for side in (lo[axis], hi[axis]):
            for a in grid:
                for b in grid:
                    p = np.empty(3)
                    p[axis], p[u], p[v] = side, lo[u] + a*(hi[u]-lo[u]), lo[v] + b*(hi[v]-lo[v])
                    samples.append(p)
    samples = np.array(samples)
    seen = []
    for frame_id, (K, T, (width, height)) in poses.items():
        camera = (samples - T[:3, 3]) @ T[:3, :3]
        front = camera[:, 2] > .1
        uv = (camera @ np.asarray(K).T)[:, :2] / np.where(front, camera[:, 2], 1)[:, None]
        mx, my = margin*width, margin*height
        inside = front & (uv[:, 0] >= -mx) & (uv[:, 0] < width+mx) & (uv[:, 1] >= -my) & (uv[:, 1] < height+my)
        if inside.sum() < min_points:
            continue
        offset = samples[inside] - T[:3, 3]
        distance = np.linalg.norm(offset, axis=1)
        rays = np.hstack([np.repeat(T[None, :3, 3], len(offset), 0), offset / distance[:, None]]).astype(np.float32)
        hit = scene.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
        if (hit >= distance - .15).sum() >= min_points:
            seen.append(frame_id)
    return seen


def annotate_focus(events, task, root):
    """focus_z: the work height a step is about, so players can cut to that storey.

    Photo-based queries use the camera height; sections their cut height; planes and rays the middle of their
    region; images viewed take the focus of the query or frame they came from; a revision the median height of
    the objects it added or changed.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from indoor_recon.glb import load_objects
    poses = frame_poses(read_json(task / "startup.json"))
    camera_z = {frame_id: float(T[2, 3]) for frame_id, (_, T, _) in poses.items()}
    by_name = {}
    for e in events:
        if e["kind"] == "run" and e.get("op") == "evidence":
            query = (read_json(root / e["dir"] / "evidence.json") or {}).get("query") or {}
            z = camera_z.get(query.get("frame_id"))
            if z is None and query.get("op") == "section" and query.get("axis") == "z":
                z = query.get("offset_m")
            elif z is None and query.get("bounds_work_m"):
                z = (query["bounds_work_m"][0][2] + query["bounds_work_m"][1][2]) / 2 if query.get("axis") in (None, "z") else None
            elif z is None and query.get("origins_work_m"):
                z = float(np.mean([o[2] for o in query["origins_work_m"]]))
            e["focus_z"] = by_name[e["name"]] = round(z, 3) if z is not None else None
    for e in events:
        if e["kind"] == "look":
            for image in e["images"]:
                path = image["source"] or image["requested"] or ""
                frame = re.search(r"rgb/(\d+)\.\w+$", path)
                query = re.search(r"evidence/([^/]+)/", path)
                z = camera_z.get(frame.group(1)) if frame else by_name.get(query.group(1)) if query else None
                image["focus_z"] = round(z, 3) if z is not None else None
    previous = {}
    for e in sorted((e for e in events if e["kind"] == "revision"), key=lambda e: e["revision"]):
        objects, _ = load_objects(root / e["model"])
        current = {name: (o["xyz"].shape, float(o["xyz"].sum())) for name, o in objects.items()}
        changed = [name for name in objects if previous.get(name) != current[name]]
        heights = [float(objects[name]["xyz"][:, 2].mean()) for name in changed]
        e["focus_z"] = round(float(np.median(heights)), 3) if heights and previous else None
        previous = current


def redact(events, task, root, object_ids, frame_ids=(), boxes=()):
    """Mark images that show the given model objects or work-space boxes, or come from the given frames, for
    players to leave out (the files stay as they are).

    The objects' box comes from the last revision; a box is useful when the model left the thing out or placed
    it elsewhere. A frame shows a box when its samples project into the photo without the scan in front; photos,
    crops and residual contact-sheet tiles from those frames are marked.
    """
    frames, regions, poses = {str(f) for f in frame_ids}, [], {}
    if object_ids:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from indoor_recon.glb import load_objects
        last = max((e for e in events if e["kind"] == "revision"), key=lambda e: e["revision"])
        objects, _ = load_objects(root / last["model"])
        missing = [i for i in object_ids if i not in objects]
        if missing:
            raise SystemExit(f"Objects not in {last['model']}: {missing}")
        points = np.vstack([objects[i]["xyz"] for i in object_ids])
        regions.append((points.min(0), points.max(0)))
    regions += [(np.array(b[:3], float), np.array(b[3:], float)) for b in boxes]
    regions = [(lo - .15, hi + .15) for lo, hi in regions]
    if regions:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from indoor_recon.evidence import Evidence
        startup = read_json(task / "startup.json")
        evidence = Evidence(read_json(Path(startup["evidence_config"])), startup["evidence_roots"])
        poses, scene = frame_poses(startup), evidence.ray_scene()
        for region in regions:
            frames |= set(frames_seeing(region, poses, scene))
    # residual contact sheets hold 40 clusters each, one tile per cluster from its first view
    sheets = {}
    for report_path in task.rglob("residuals.json"):
        report = read_json(report_path) or {}
        for kind, clusters in (("clusters", report.get("clusters") or []), ("excess", (report.get("excess") or {}).get("clusters") or [])):
            for start in range(0, len(clusters), 40):
                tiles = {(c.get("views") or [{}])[0].get("frame_id") for c in clusters[start:start+40]}
                sheet = report_path.parent / f"{kind}-{start//40+1}.png"
                sheets[sheet.relative_to(root).as_posix()] = bool(tiles & frames)
    shown_frames = {}  # evidence directory name -> whether its frame shows the objects
    def hidden(path):
        if not path:
            return None
        match = re.search(r"rgb/(\d+)\.\w+$", path)
        if match:
            return match.group(1) in frames
        if path in sheets:
            return sheets[path]
        if "*" in path:  # a looped request: hidden when any file it may have shown is
            pattern = re.compile(re.escape(path).replace(r"\*", "[^/]*") + "$")
            matched = [hide for sheet, hide in sheets.items() if pattern.search(sheet)]
            return any(matched) if matched else None
        match = re.search(r"evidence/([^/]+)/", path)
        if match:
            return shown_frames.get(match.group(1))
        return None
    for e in events:
        if e["kind"] == "run" and e.get("op") == "evidence" and e.get("frame_id") is not None:
            shown_frames[e["name"]] = e["frame_id"] in frames
            e["redacted"] = shown_frames[e["name"]]
    known = set(poses) if regions else set()
    model_only = re.compile(r"(render-[^/]+/review\.png|photo-review[^/]*/[^/]+/render\.png|residuals/plan\.png|"
                            r"/appearance\.png|/section\.png)$")
    def verdict(path):
        """True: shows a redacted frame; False: known not to; None: cannot tell."""
        if not path:
            return None
        known_verdict = hidden(path)
        if known_verdict is not None:
            return known_verdict
        if model_only.search(path):
            return False  # renders of the model and plots of the scan, no photo pixels
        # images the agent drew over one photo usually carry its frame number: grid-366.png, photo-250-overlay.png
        numbers = [n for n in re.findall(r"(?<=[-_])(\d{2,})(?=[-_.])", Path(path).name) if n in known]
        return numbers[-1] in frames if numbers else None
    for e in events:
        if e["kind"] == "look":
            for image in e["images"]:
                verdicts = [verdict(image["source"]), verdict(image["requested"])]
                # anything not known to be clear stays out: mosaics, untraceable images, unparsed overlays
                image["redacted"] = any(verdicts) or all(v is None for v in verdicts)
    return {"objects": object_ids, "boxes_work_m": [[lo.round(3).tolist(), hi.round(3).tolist()] for lo, hi in regions],
            "frames": sorted(frames, key=lambda f: (len(f), f))}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("task", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--codex-log", type=Path, action="append", default=[],
                        help="Codex session log; repeat for a task continued over several sessions")
    parser.add_argument("--root", type=Path, help="Viewer root that timeline paths are relative to")
    parser.add_argument("--redact-object", action="append", default=[], metavar="ID",
                        help="Mark images showing this model object (e.g. an entrance door) for players to leave out")
    parser.add_argument("--redact-box", action="append", default=[], metavar="X0,Y0,Z0,X1,Y1,Z1",
                        type=lambda v: [float(x) for x in v.split(",")],
                        help="Mark images showing this work-space box (metres), e.g. an object the model left out")
    parser.add_argument("--redact-frame", action="append", default=[], metavar="ID",
                        help="Mark images from this photo frame (e.g. one with a person in it) for players to leave out")
    args = parser.parse_args()
    task, out = args.task.resolve(), args.out.resolve()
    root = (args.root or out.parent).resolve()
    if out.exists():
        raise SystemExit(f"Output directory already exists: {out}")
    out.mkdir(parents=True)
    rel = lambda p: Path(p).resolve().relative_to(root).as_posix()
    events, sessions, count = [], [], 0
    for log in args.codex_log:
        # images are matched against everything the agent could see: the task's run directory
        said, start, end, count = codex_events(log, out, root, task.parent.parent, count)
        events += said
        sessions.append([start, end])
    sessions.sort()
    for e in tool_events(task):
        if sessions and not any(a <= e["time"] <= b + 60 for a, b in sessions):
            continue  # init-task / predict outputs and later reviews are not part of a session
        e["images"] = [rel(p) for p in e["images"]]
        e["dir"] = rel(e["dir"])
        if "model" in e:
            e["model"] = rel(e["model"])
        events.append(e)
    events.sort(key=lambda e: e["time"])
    if not sessions:
        sessions = [[min(e["time"] for e in events), max(e["time"] for e in events)]]
    for session in sessions:  # a session ends with its last event, including the tool runs it caused
        session[1] = max([session[1]]+[e["time"] for e in events if session[0] <= e["time"] <= session[1] + 60])
    # session time with the gaps between sessions cut out
    offsets, t = [], 0.
    for a, b in sessions:
        offsets.append(t)
        t += b - a
    for e in events:
        k = max(i for i, (a, _) in enumerate(sessions) if a <= e["time"]) if e["time"] >= sessions[0][0] else 0
        e["t"] = round(offsets[k] + max(e.pop("time") - sessions[k][0], 0), 3)
    utc = lambda x: datetime.datetime.fromtimestamp(x, datetime.timezone.utc).isoformat()
    timeline = {"schema": "recon-activity-timeline/1", "task": rel(task), "start_utc": utc(sessions[0][0]),
                "duration_s": round(t, 3), "events": events}
    if len(sessions) > 1:
        timeline["sessions"] = [{"t": round(o, 3), "start_utc": utc(a), "duration_s": round(b - a, 3)}
                                for o, (a, b) in zip(offsets, sessions)]
    annotate_focus(events, task, root)
    if args.redact_object or args.redact_frame or args.redact_box:
        timeline["redaction"] = redact(events, task, root, args.redact_object, args.redact_frame, args.redact_box)
    # the scan the task models, so a player can show the input before the first build
    startup = read_json(task / "startup.json") or {}
    evidence = read_json(Path(startup["evidence_config"])) if startup.get("evidence_config") else None
    scan = (evidence or {}).get("scan")
    if scan and scan["asset"]["root"] in startup.get("evidence_roots", {}):
        path = Path(startup["evidence_roots"][scan["asset"]["root"]]) / scan["asset"]["path"]
        if path.is_file() and root in path.resolve().parents:
            timeline["scan"] = {"path": rel(path), "metres_per_unit": scan["metres_per_unit"],
                                "T_work_from_source_m": scan["T_work_from_source_m"]}
    (out / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    kinds = {}
    for e in events:
        key = e["kind"] if e["kind"] != "run" else "run:" + e["op"]
        kinds[key] = kinds.get(key, 0) + 1
    print(json.dumps({"timeline": str(out / "timeline.json"), "duration_s": timeline["duration_s"], "events": kinds},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
