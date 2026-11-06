# SPDX-License-Identifier: Apache-2.0
"""Local read-only web viewer for exported GLB models under one directory."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

STATIC = Path(__file__).resolve().parent / "static"
MODEL_SUFFIXES = {".glb", ".gltf"}
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__"}


def list_models(root):
    """GLB/glTF files below root, newest first, as root-relative POSIX paths."""
    root = Path(root).resolve()
    models = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() in MODEL_SUFFIXES:
                stat = path.stat()
                models.append({"path": path.relative_to(root).as_posix(), "size": stat.st_size, "mtime": stat.st_mtime})
    return sorted(models, key=lambda m: (-m["mtime"], m["path"]))


def resolve_model_file(root, relative):
    """Map a URL path below /files/ to a file inside root; None if it escapes root or is missing."""
    root, relative = model_binding(root, unquote(relative))
    if root is None:
        return None
    try:
        path = (root / relative).resolve()
        path.relative_to(root)
    except (ValueError, OSError):
        return None
    return path if path.is_file() else None


_cache = {}


def registered_tasks(root):
    """Explicit local bindings; never accept absolute paths from a URL."""
    path = Path(root).resolve()/'viewer-tasks.local.json'
    if not path.is_file():
        return {}
    data = _cached(path, lambda p: json.loads(p.read_text(encoding='utf-8')))
    result = {}
    for row in data.get('tasks', []):
        identity = row['id']
        if not isinstance(identity, str) or not identity.startswith('@') or len(identity) < 2 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in identity[1:]):
            raise ValueError('Registered task ID must be @ followed by letters, digits, - or _')
        if identity in result:
            raise ValueError('Duplicate registered task ID')
        result[identity] = dict(row, task_dir=(path.parent/row['task_dir']).resolve(),
                               models_dir=(path.parent/row.get('models_dir', row['task_dir'])).resolve())
    return result


def model_binding(root, relative):
    root = Path(root).resolve()
    relative = relative or ''
    first, _, rest = relative.partition('/')
    if first.startswith('@'):
        row = registered_tasks(root).get(first)
        return (row['models_dir'], rest) if row else (None, '')
    return root, relative


def _cached(path, read):
    """read(path) memoised on (path, mtime, size), so polling stays cheap."""
    stat = path.stat()
    reader = (read.__module__, read.__qualname__, getattr(read, '__code__', None))
    key = (str(path), reader, stat.st_mtime_ns, stat.st_size)
    if key not in _cache:
        for old in list(_cache):
            if old[:2] == key[:2]:
                _cache.pop(old, None)
        while len(_cache) >= 512:
            _cache.pop(next(iter(_cache)), None)
        _cache[key] = read(path)
    return _cache[key]


def _sha256(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _diagnostic(path):
    """Summary of a residuals.json / geometry.json, keyed by the model it describes; None if unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("model_sha256"), str):
        return None
    if path.name == "residuals.json":
        if not isinstance(data.get("scan_area_within_m", {}), dict) or not isinstance(data.get("excess") or {}, dict):
            return None
        summary = {"within_5cm": data.get("scan_area_within_m", {}).get("0.05"),
                   "unexplained_m2": data.get("unexplained_area_m2"),
                   "excess_m2": (data.get("excess") or {}).get("excess_area_m2")}
    else:
        summary = data.get("counts")
    return data.get("model_sha256"), path.stat().st_mtime, summary


def list_revisions(root, relative):
    """Finished builds below root/relative (a build.json that passed, beside model.glb), one per model revision.

    Residuals and check-geometry reports anywhere below the directory are attached by the model SHA-256 they
    record. A revision built more than once (retries, reopen checks) is represented by its latest diagnosed
    build, or its first build when none is diagnosed yet.
    """
    root = Path(root).resolve()
    base = resolve_model_dir(root, relative)
    if base is None:
        return None
    bound, _ = model_binding(root, relative)
    prefix = relative.partition('/')[0]+'/' if relative.startswith('@') else ''
    builds, diagnostics = {}, {"residuals": {}, "geometry": {}}
    for directory, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        here = Path(directory)
        if "build.json" in files and "model.glb" in files:
            try:
                report = _cached(here / "build.json", lambda p: json.loads(p.read_text(encoding="utf-8")))
                revision = report.get("revision") if isinstance(report, dict) else None
                if isinstance(report, dict) and report.get("passed") is True and type(revision) is int and revision >= 0:
                    glb = here / "model.glb"
                    entry = {
                        "revision": revision, "path": prefix+glb.relative_to(bound).as_posix(),
                        "mtime": (here / "build.json").stat().st_mtime, "sha256": _cached(glb, _sha256)}
                    builds.setdefault(revision, []).append(entry)
            except (OSError, ValueError):
                pass  # An interrupted or removed build does not invalidate other revisions.
        for name, kind in (("residuals.json", "residuals"), ("geometry.json", "geometry")):
            if name in files:
                try:
                    found = _cached(here / name, _diagnostic)
                except OSError:
                    continue
                if found and found[0] and (found[0] not in diagnostics[kind] or found[1] > diagnostics[kind][found[0]][0]):
                    diagnostics[kind][found[0]] = found[1:]
    revisions = []
    for revision in sorted(builds):
        for entry in builds[revision]:
            for kind in diagnostics:
                entry[kind] = diagnostics[kind].get(entry["sha256"], (None, None))[1]
        diagnosed = [e for e in builds[revision] if e["residuals"] or e["geometry"]]
        revisions.append(max(diagnosed, key=lambda e: e["mtime"]) if diagnosed
                         else min(builds[revision], key=lambda e: e["mtime"]))
    return revisions


def recent_activity(root, relative, limit=5):
    """Latest CLI runs below root/relative from their activity.json records, newest last."""
    root = Path(root).resolve()
    base = resolve_model_dir(root, relative)
    if base is None:
        return None
    runs = []
    for directory, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        if "activity.json" in files:
            path = Path(directory) / "activity.json"
            try:
                record = _cached(path, lambda p: json.loads(p.read_text(encoding="utf-8")))
                if isinstance(record, dict):
                    runs.append({"command": record.get("command"), "name": path.parent.name, "passed": record.get("passed"),
                                 "seconds": record.get("seconds"), "finished": path.stat().st_mtime})
            except (OSError, ValueError):
                continue
    return sorted(runs, key=lambda r: r["finished"])[-limit:]


def resolve_model_dir(root, relative):
    """Directory below root for a decoded query value; None if it escapes root or is missing."""
    root, relative = model_binding(root, relative)
    if root is None:
        return None
    try:
        path = (root / (relative or "")).resolve()
        path.relative_to(root)
    except (ValueError, OSError):
        return None
    return path if path.is_dir() else None


def make_handler(root):
    root = Path(root).resolve()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(STATIC), **kwargs)

        def send_json(self, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlsplit(self.path)
            path = url.path
            if path in {'/api/tasks', '/api/task', '/api/scan', '/api/photo', '/api/frame'}:
                from . import workspace
                query = parse_qs(url.query)
                relative = query.get('task', ['.'])[0]
                try:
                    if path == '/api/tasks':
                        self.send_json({'tasks': workspace.tasks(root)})
                    elif path == '/api/task':
                        self.send_json(workspace.describe(root, relative))
                    elif path == '/api/frame':
                        self.send_json(workspace.frame_data(root, relative, query.get('frame', [''])[0]))
                    else:
                        if path == '/api/scan':
                            target, spec = workspace.scan_asset(root, relative)
                            if spec['kind'] == 'points':
                                self.send_json(_cached(target, workspace.point_preview))
                                return
                        else:
                            target = workspace.photo_asset(root, relative, query.get('frame', [''])[0])
                        self.send_response(200)
                        self.send_header('Content-Type', self.guess_type(str(target)))
                        self.send_header('Content-Length', str(target.stat().st_size))
                        self.end_headers()
                        with target.open('rb') as stream:
                            self.copyfile(stream, self.wfile)
                except (OSError, ValueError, KeyError, ImportError) as exc:
                    self.send_error(400, str(exc).encode('ascii', 'replace').decode())
                return
            if path == "/api/models":
                self.send_json({"root": str(root), "models": list_models(root)})
            elif path == "/api/revisions":
                relative = parse_qs(url.query).get("dir", [""])[0]
                revisions = list_revisions(root, relative)
                if revisions is None:
                    self.send_error(404)
                    return
                self.send_json({"dir": relative, "revisions": revisions, "activity": recent_activity(root, relative)})
            elif path.startswith("/files/"):
                target = resolve_model_file(root, path[len("/files/"):])
                if target is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "model/gltf-binary" if target.suffix.lower() == ".glb" else self.guess_type(str(target)))
                self.send_header("Content-Length", str(target.stat().st_size))
                self.end_headers()
                with open(target, "rb") as source:
                    self.copyfile(source, self.wfile)
            else:
                super().do_GET()

        def end_headers(self):
            self.send_header("Cache-Control", "no-cache")
            super().end_headers()

        def log_message(self, format, *args):
            pass

    return Handler


def serve(root, host="127.0.0.1", port=8770, open_browser=False):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"Model directory does not exist: {root}")
    server = ThreadingHTTPServer((host, port), make_handler(root))
    url = f"http://{host}:{server.server_address[1]}/"
    print(json.dumps({"url": url, "root": str(root), "models": len(list_models(root))}, ensure_ascii=False), flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
