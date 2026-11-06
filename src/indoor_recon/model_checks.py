# SPDX-License-Identifier: Apache-2.0
"""Model-wide diagnostics that find problems no fixed query was aimed at.

`residuals` samples the source scan surface and clusters the parts no model surface explains
(missing objects, shifted outlines, clutter), and the reverse: model surfaces that the capture
cameras saw through, i.e. model volume in space the scan observed as empty (oversized or shifted
parts). `geometry` checks relations between exported
objects: solid interference, same-facing coplanar overlap (z-fighting), shell leaks seen from
the capture cameras, and semantic category misuse. Neither decides acceptance; both point to
locations, nearby objects and photos so the task can confirm and fix or justify each finding.
"""
from pathlib import Path

import numpy as np

from .contracts import ContractError, read_json, sha256
from .glb import assembly_root, load_objects
from .jobs import atomic_json

ARCHITECTURE = {"floor", "ceiling", "wall", "partition", "beam", "column", "soffit", "header", "stairs", "stair",
                "door", "window", "opening", "roof", "railing", "lightwell", "niche", "skirting"}


def _new_output(output):
    output = Path(output).resolve()
    if output.exists():
        raise ContractError("Diagnostics output requires a new directory")
    output.mkdir(parents=True)
    return output


def _tracer(objects, ids):
    import open3d as o3d
    tracer, names = o3d.t.geometry.RaycastingScene(), {}
    for name in ids:
        xyz, faces = objects[name]["xyz"], objects[name]["faces"]
        if len(faces):
            names[tracer.add_triangles(o3d.core.Tensor(np.asarray(xyz, np.float32)), o3d.core.Tensor(np.asarray(faces, np.uint32)))] = name
    return tracer, names


def _closest(tracer, names, points):
    import open3d as o3d
    result = tracer.compute_closest_points(o3d.core.Tensor(np.asarray(points, np.float32)))
    closest = result["points"].numpy().astype(float)
    ids = result["geometry_ids"].numpy()
    label = np.array([names.get(int(i), "") for i in ids])
    return np.linalg.norm(closest-points, axis=1), label


def sample_surface(vertices, faces, spacing_m, seed=0, max_samples=600_000, return_faces=False):
    """Area-uniform samples; returns points, unit face normals and the area each sample represents
    (plus the sampled face index per point when return_faces)."""
    tri = np.asarray(vertices, float)[np.asarray(faces)]
    cross = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
    area = np.linalg.norm(cross, axis=1)/2
    keep = area > 1e-12
    original = np.flatnonzero(keep)
    tri, cross, area = tri[keep], cross[keep], area[keep]
    total = float(area.sum())
    if total <= 0:
        raise ContractError("Surface has no area to sample")
    count = int(min(max_samples, max(1, round(total/spacing_m**2))))
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(tri), count, p=area/total)
    r1, r2 = rng.random(count), rng.random(count)
    s = np.sqrt(r1)
    points = tri[pick, 0]*(1-s)[:, None]+tri[pick, 1]*(s*(1-r2))[:, None]+tri[pick, 2]*(s*r2)[:, None]
    normals = cross[pick]/np.linalg.norm(cross[pick], axis=1)[:, None]
    if return_faces:
        return points, normals, total/count, original[pick]
    return points, normals, total/count


def voxel_clusters(points, voxel_m):
    """Connected components of occupied voxels (26-neighbourhood); label per point."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    if not len(points):
        return np.zeros(0, int), 0
    keys = np.floor(points/voxel_m).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    lookup = {tuple(k): i for i, k in enumerate(unique.tolist())}
    rows, cols = [], []
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dy, dz) > (0, 0, 0)]
    for i, (x, y, z) in enumerate(unique.tolist()):
        for dx, dy, dz in offsets:
            j = lookup.get((x+dx, y+dy, z+dz))
            if j is not None:
                rows.append(i)
                cols.append(j)
    graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(unique), len(unique)))
    count, labels = connected_components(graph, directed=False)
    return labels[inverse], count


ORIENTATION_CLASSES = ("up", "down", "vertical", "slanted")


def _orientation_class(normals):
    nz = normals[:, 2]
    return np.where(nz > .8, 0, np.where(nz < -.8, 1, np.where(abs(nz) < .3, 2, 3)))


def oriented_clusters(points, normals, voxel_m):
    """Voxel components computed separately per orientation class, so a missing worktop does not merge
    with the cabinet fronts, wall and clutter around it into one mixed cluster."""
    labels, classes, offset = np.full(len(points), -1), _orientation_class(normals), 0
    for c in range(len(ORIENTATION_CLASSES)):
        mask = classes == c
        if mask.any():
            sub, count = voxel_clusters(points[mask], voxel_m)
            labels[mask] = sub+offset
            offset += count
    return labels


def _orientation(normals):
    nz = normals[:, 2]
    shares = {"up": float((nz > .8).mean()), "down": float((nz < -.8).mean()), "vertical": float((abs(nz) < .3).mean())}
    best = max(shares, key=shares.get)
    return (best if shares[best] >= .6 else "mixed"), shares


def _cameras(evidence, limit=None):
    ids = list(evidence.frames)
    if limit and len(ids) > limit:
        ids = [ids[int(i)] for i in np.linspace(0, len(ids)-1, limit)]
    cameras = []
    for identity in ids:
        frame, k, pose = evidence.frame(identity)
        cameras.append((identity, frame, k, pose))
    return cameras


def best_views(evidence, cameras, points, limit=3, margin_px=24):
    """Frames that actually see the points: inside the image and not occluded by the scan."""
    import open3d as o3d
    scene, rows = evidence.ray_scene(), []
    for identity, frame, k, pose in cameras:
        camera = (points-pose[:3, 3]) @ pose[:3, :3]
        front = camera[:, 2] > .05
        if not front.any():
            continue
        uv = camera[front] @ k.T
        uv = uv[:, :2]/uv[:, 2, None]
        inside = np.all((uv >= 0) & (uv < frame["size"]), axis=1)
        if not inside.any():
            continue
        targets = points[front][inside]
        vectors = targets-pose[:3, 3]
        distance = np.linalg.norm(vectors, axis=1)
        rays = np.column_stack((np.repeat(pose[None, :3, 3], len(targets), 0), vectors/distance[:, None])).astype(np.float32)
        hit = scene.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
        visible = hit >= distance-.03
        if not visible.any():
            continue
        seen = uv[inside][visible]
        lo, hi = np.maximum(seen.min(0)-margin_px, 0), np.minimum(seen.max(0)+margin_px, frame["size"])
        rows.append({"frame_id": identity, "image": frame["image"], "visible_samples": int(visible.sum()),
                     "mean_distance_m": float(distance[visible].mean()), "crop_xyxy": [*lo.tolist(), *hi.tolist()],
                     "_pixels": seen})
    rows.sort(key=lambda r: (-r["visible_samples"], r["mean_distance_m"]))
    return rows[:limit]


def contact_sheet(evidence, clusters, path, tile=320, columns=5):
    """One tile per cluster: its best photo crop with the unexplained samples marked in red."""
    from PIL import Image, ImageDraw
    tiles = []
    for cluster in clusters:
        canvas = Image.new("RGB", (tile, tile+34), (245, 245, 245))
        draw = ImageDraw.Draw(canvas)
        view = (cluster.get("views") or [None])[0]
        if view:
            x0, y0, x1, y1 = view["crop_xyxy"]
            side = max(x1-x0, y1-y0, 96)
            cx, cy = (x0+x1)/2, (y0+y1)/2
            box = (int(cx-side/2), int(cy-side/2), int(cx+side/2), int(cy+side/2))
            with Image.open(evidence.resolve(view["image"])) as image:
                crop = image.convert("RGB").crop(box)
            scale = tile/side
            crop = crop.resize((tile, tile))
            mark = ImageDraw.Draw(crop)
            for u, v in view["_pixels"]:
                a, b = (u-box[0])*scale, (v-box[1])*scale
                mark.ellipse((a-2.5, b-2.5, a+2.5, b+2.5), outline=(255, 0, 0), width=2)
            canvas.paste(crop, (0, 34))
        else:
            draw.text((8, 150), "no unoccluded view", fill=(120, 0, 0))
        z = cluster["bounds_work_m"]
        distance = (cluster.get("distance_to_model_m") or cluster["seen_through_m"])["median"]
        near = (cluster.get("nearest_objects") or cluster["objects"])[0]["id"]
        prefix = "E" if "seen_through_m" in cluster else "#"
        draw.text((6, 3), f"{prefix}{cluster['rank']} {cluster['area_m2']:.2f} m2 {cluster['orientation']} d~{distance*100:.0f}cm", fill=(0, 0, 0))
        draw.text((6, 18), f"z {z[0][2]:.2f}-{z[1][2]:.2f} {near[-30:]}" + (f" f{view['frame_id']}" if view else ""), fill=(60, 60, 60))
        tiles.append(canvas)
    if not tiles:
        return None
    rows = (len(tiles)+columns-1)//columns
    sheet = Image.new("RGB", (columns*(tile+6)+6, rows*(tile+40)+6), (255, 255, 255))
    for i, t in enumerate(tiles):
        sheet.paste(t, (6+(i % columns)*(tile+6), 6+(i//columns)*(tile+40)))
    sheet.save(path)
    return path.name


def _plan_image(path, points, labels, ranked, explained, size=1400):
    from PIL import Image, ImageDraw
    lo, hi = points[:, :2].min(0), points[:, :2].max(0)
    scale = (size-40)/max(float((hi-lo).max()), 1e-6)
    width, height = int((hi-lo)[0]*scale)+40, int((hi-lo)[1]*scale)+40
    image = Image.new("RGB", (width, height), (250, 250, 250))
    draw = ImageDraw.Draw(image)
    def px(p):
        return 20+(p[:, 0]-lo[0])*scale, height-20-(p[:, 1]-lo[1])*scale
    x, y = px(points[explained])
    for a, b in zip(x[::3], y[::3]):
        draw.point((a, b), fill=(200, 200, 200))
    palette = [(214, 39, 40), (31, 119, 180), (44, 160, 44), (148, 103, 189), (255, 127, 14), (23, 190, 207), (227, 119, 194), (140, 86, 75)]
    for rank, cluster in enumerate(ranked):
        mask = labels == cluster["label"]
        x, y = px(points[mask])
        color = palette[rank % len(palette)]
        for a, b in zip(x, y):
            draw.point((a, b), fill=color)
        cx, cy = px(np.array([cluster["centroid_work_m"]]))
        draw.text((float(cx[0])+4, float(cy[0])-6), str(cluster["rank"]), fill=(0, 0, 0))
    draw.text((8, 4), "plan view, work X right / Y up; grey = explained scan, colour = unexplained cluster rank", fill=(0, 0, 0))
    image.save(path)


def _normal_gaps(tracer, points, normals, reach_m):
    """Distance along each scan normal (either direction) to the first model surface, inf beyond reach_m.

    The nearest model point can be a neighbouring edge even where the model has no surface at all;
    probing along the normal tells a missing surface (inf) from a displaced parallel one (finite)."""
    import open3d as o3d
    best = np.full(len(points), np.inf)
    for sign in (1., -1.):
        rays = np.column_stack((points, sign*normals)).astype(np.float32)
        t = tracer.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
        best = np.minimum(best, np.where(t <= reach_m, t, np.inf))
    return best


def _hint(median_distance, missing_share):
    if median_distance > .25:
        return "unmodelled_region"
    if missing_share >= .5:
        return "missing_surface_near_model"
    return "offset_surface"


def excess_surfaces(objects, scan_scene, cameras, spacing_m=.04, margin_m=.05, min_cameras=3, ratio=.5,
                    min_incidence=.25, max_depth_m=8., max_samples=400_000, seed=0):
    """Model surface samples the capture cameras saw through: the scan surface along the camera ray lies
    more than margin_m behind the model face (measured along the face normal) in at least `min_cameras`
    frames and in at least `ratio` of the frames that observe the point. Grazing views (|cos| below
    min_incidence) are ignored, since a few centimetres of face offset becomes decimetres along such a
    ray. Rays with no scan hit (holes, glass, outside) count as unknown."""
    import open3d as o3d
    ids = sorted(objects)
    vertices, faces, owner, base = [], [], [], 0
    for index, name in enumerate(ids):
        xyz, tri = np.asarray(objects[name]["xyz"], float), np.asarray(objects[name]["faces"], np.int64)
        if len(tri):
            vertices.append(xyz)
            faces.append(tri+base)
            owner.append(np.full(len(tri), index))
            base += len(xyz)
    points, normals, sample_area, face = sample_surface(np.vstack(vertices), np.vstack(faces), spacing_m, seed, max_samples, True)
    owner = np.concatenate(owner)[face]
    observed, through = np.zeros(len(points), int), np.zeros(len(points), int)
    depth = np.zeros(len(points))
    for _, frame, k, pose in cameras:
        camera = (points-pose[:3, 3]) @ pose[:3, :3]
        index = np.flatnonzero((camera[:, 2] > .1) & (camera[:, 2] < max_depth_m))
        if not len(index):
            continue
        uv = camera[index] @ k.T
        uv = uv[:, :2]/uv[:, 2, None]
        index = index[np.all((uv >= 0) & (uv < frame["size"]), axis=1)]
        if not len(index):
            continue
        vectors = points[index]-pose[:3, 3]
        distance = np.linalg.norm(vectors, axis=1)
        incidence = abs(np.einsum("ij,ij->i", vectors/distance[:, None], normals[index]))
        keep = incidence >= min_incidence
        index, vectors, distance, incidence = index[keep], vectors[keep], distance[keep], incidence[keep]
        if not len(index):
            continue
        rays = np.column_stack((np.repeat(pose[None, :3, 3], len(index), 0), vectors/distance[:, None])).astype(np.float32)
        hit = scan_scene.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
        finite = np.isfinite(hit)
        behind = (hit-distance)*incidence
        seen = finite & (behind >= -margin_m)
        past = finite & (behind > margin_m)
        observed[index] += seen
        through[index] += past
        depth[index] += np.where(past, behind, 0)
    excess = (through >= min_cameras) & (through >= ratio*np.maximum(observed, 1))
    return {"points": points, "normals": normals, "sample_area": sample_area, "owner": owner, "ids": ids,
            "observed": observed, "through": through, "mean_depth": np.where(through > 0, depth/np.maximum(through, 1), 0),
            "excess": excess}


def residuals(startup_path, model_path, output, spacing_m=.04, threshold_m=.05, voxel_m=.12, min_area_m2=.02,
              top=None, views=3, bounds_work_m=None, seed=0, excess_cameras=160, excess_margin_m=.05):
    """Scan surfaces farther than threshold_m from any model surface, clustered and ranked by area,
    and model surfaces the cameras saw through (free-space violations)."""
    from .evidence import Evidence, bounds
    startup = read_json(startup_path)
    evidence = Evidence(read_json(startup["evidence_config"]), startup["evidence_roots"])
    objects, parameters = load_objects(model_path, startup["scene_id"])
    output = _new_output(output)
    vertices, faces = evidence.scan_mesh()
    if bounds_work_m is not None:
        box = bounds(bounds_work_m)
        centers = vertices[faces].mean(1)
        faces = faces[np.all((centers >= box[0]) & (centers <= box[1]), axis=1)]
    points, normals, sample_area = sample_surface(vertices, faces, spacing_m, seed)
    tracer, names = _tracer(objects, sorted(objects))
    distance, nearest = _closest(tracer, names, points)
    unexplained = distance > threshold_m
    labels = np.full(len(points), -1)
    sub_labels = oriented_clusters(points[unexplained], normals[unexplained], voxel_m)
    labels[unexplained] = sub_labels
    gaps = np.full(len(points), np.nan)
    gaps[unexplained] = _normal_gaps(tracer, points[unexplained], normals[unexplained], .3)
    clusters = []
    for label in np.unique(sub_labels):
        mask = labels == label
        area = float(mask.sum()*sample_area)
        if area < min_area_m2:
            continue
        p, d, g = points[mask], distance[mask], gaps[mask]
        orientation, shares = _orientation(normals[mask])
        near, counts = np.unique(nearest[mask], return_counts=True)
        order = np.argsort(-counts)[:3]
        found = np.isfinite(g)
        clusters.append({"label": int(label), "area_m2": area, "samples": int(mask.sum()),
                         "centroid_work_m": p.mean(0).tolist(), "bounds_work_m": [p.min(0).tolist(), p.max(0).tolist()],
                         "orientation": orientation, "orientation_shares": shares,
                         "distance_to_model_m": {"median": float(np.median(d)), "p90": float(np.percentile(d, 90)), "max": float(d.max())},
                         "along_normal": {"no_model_surface_share": float(1-found.mean()),
                                          "median_gap_m": float(np.median(g[found])) if found.any() else None},
                         "nearest_objects": [{"id": str(near[i]), "share": float(counts[i]/mask.sum())} for i in order]})
    clusters.sort(key=lambda c: -c["area_m2"])
    ranked = clusters if top is None else clusters[:top]
    cameras = _cameras(evidence) if views else []
    rng = np.random.default_rng(seed)
    for rank, cluster in enumerate(ranked, 1):
        cluster["rank"] = rank
        # Far from every model surface: unbuilt object. Near a model object but nothing along the scan
        # normal: a surface piece is absent (clipped outline, gap between segments). Otherwise displaced.
        cluster["hint"] = _hint(cluster["distance_to_model_m"]["median"], cluster["along_normal"]["no_model_surface_share"])
        if views:
            members = np.flatnonzero(labels == cluster["label"])
            pick = points[rng.choice(members, min(48, len(members)), replace=False)]
            cluster["views"] = best_views(evidence, cameras, pick, views)
    excess = _excess_report(evidence, objects, cameras, excess_cameras, spacing_m, excess_margin_m, voxel_m, min_area_m2,
                            views, bounds_work_m, seed) if excess_cameras else None
    fine_points, fine_normals, _ = sample_surface(vertices, faces, .012, seed, 3_000_000)
    surface_fit = plan_fit(objects, fine_points, fine_normals)
    turned = facing_fit(objects, points, normals, sample_area, distance, nearest, _detections(startup))
    total = float(len(points)*sample_area)
    coverage = {f"{t:.2f}": float((distance <= t).mean()) for t in (.02, .05, .10, .20)}
    np.savez_compressed(output/"residuals.npz", points_work_m=points.astype(np.float32), normals_work=normals.astype(np.float32),
                        distance_m=distance.astype(np.float32), nearest_object=nearest, cluster_label=labels)
    _plan_image(output/"plan.png", points, labels, ranked, ~unexplained)
    files = {"plan": "plan.png", "samples": "residuals.npz"}
    if views:
        for start in range(0, len(ranked), 40):
            name = contact_sheet(evidence, ranked[start:start+40], output/f"clusters-{start//40+1}.png")
            if name:
                files.setdefault("cluster_sheets", []).append(name)
        if excess and excess["clusters"]:
            for start in range(0, len(excess["clusters"]), 40):
                name = contact_sheet(evidence, excess["clusters"][start:start+40], output/f"excess-{start//40+1}.png")
                if name:
                    files.setdefault("excess_sheets", []).append(name)
        for cluster in ranked+(excess["clusters"] if excess else []):
            for view in cluster.get("views", []):
                view.pop("_pixels", None)
    report = {"passed": True, "geometry_quality_passed": None, "scene_id": startup["scene_id"],
              "model": str(Path(model_path).resolve()), "model_sha256": sha256(model_path),
              "model_revision": (parameters or {}).get("revision"),
              "parameters": {"spacing_m": spacing_m, "threshold_m": threshold_m, "voxel_m": voxel_m, "min_area_m2": min_area_m2,
                             "bounds_work_m": bounds_work_m, "seed": seed, "excess_cameras": excess_cameras,
                             "excess_margin_m": excess_margin_m},
              "scan_area_m2": total, "unexplained_area_m2": float(unexplained.sum()*sample_area),
              "scan_area_within_m": coverage, "clusters_total": len(clusters),
              "clusters_omitted_below_top": len(clusters)-len(ranked), "clusters": ranked, "excess": excess,
              "plan_fit": surface_fit, "facing_fit": turned, "files": files,
              "interpretation": ("Clusters are scan surfaces no model surface explains within threshold_m. Each needs a decision: "
                                 "unbuilt object or structure, shifted/clipped outline, or clutter/scan artefact to omit with local evidence. "
                                 "Excess clusters are model surfaces the capture cameras saw through to scan surfaces behind them: "
                                 "oversized or shifted parts, or scan holes on glass, screens and dark materials. "
                                 "plan_fit.shifted lists tops, seats and shelves whose plan outline matches the scan better after "
                                 "a horizontal shift; plan_fit.poor_fit lists upward faces with no matching scan surface at their height. "
                                 "facing_fit.turned lists objects whose plan orientation disagrees with the nearby upright scan surfaces "
                                 "or with an overlapping detection box; verify the yaw against photos before turning them. "
                                 "Coverage is a completeness indicator, not an accuracy pass.")}
    atomic_json(output/"residuals.json", report)
    return report


def plan_fit(objects, scan_points, scan_normals, cell_m=.01, reach_m=.15, band_m=.025, min_area_m2=.04,
             good_cover=.8, min_gain=.04, min_shift_m=.02):
    """Plan-view fit of each object's upward faces against scan upward surfaces at the same height.

    A top or seat slid sideways in plan keeps its height, so distance checks only see a thin edge strip.
    Rasterising both footprints and searching the horizontal shift that covers the most model cells
    finds such slides directly. Returns objects whose best shift is at least min_shift_m and raises the
    covered share by min_gain to at least good_cover ("shifted"), and objects that stay below good_cover
    even after shifting ("poor_fit": wrong height, size or shape, or occluded/cluttered scan)."""
    from scipy.ndimage import binary_closing, binary_dilation
    up = scan_normals[:, 2] > .9
    scan = scan_points[up]
    reach = int(round(reach_m/cell_m))
    shifted, poor = [], []
    for name in sorted(objects):
        o = objects[name]
        if o["category"] in ARCHITECTURE or not len(o["faces"]):
            continue
        xyz, tri = np.asarray(o["xyz"], float), np.asarray(o["faces"])
        t = xyz[tri]
        n = np.cross(t[:, 1]-t[:, 0], t[:, 2]-t[:, 0])
        area = np.linalg.norm(n, axis=1)/2
        face_up = n[:, 2] > .9*np.maximum(2*area, 1e-12)
        up_area = float(area[face_up].sum())
        if up_area < min_area_m2:
            continue
        model, _, _ = sample_surface(xyz, tri[face_up], cell_m)
        z = float(np.median(model[:, 2]))
        lo, hi = model[:, :2].min(0)-reach_m-cell_m, model[:, :2].max(0)+reach_m+cell_m
        near = scan[(abs(scan[:, 2]-z) < band_m) & np.all((scan[:, :2] >= lo) & (scan[:, :2] <= hi), axis=1)]
        shape = tuple(np.ceil((hi-lo)/cell_m).astype(int)+1)
        cells = np.unique(np.floor((model[:, :2]-lo)/cell_m).astype(int), axis=0)
        occupied = np.zeros(shape, bool)
        if len(near):
            ij = np.floor((near[:, :2]-lo)/cell_m).astype(int)
            occupied[ij[:, 0], ij[:, 1]] = True
            occupied = binary_closing(binary_dilation(occupied))
        scores = np.zeros((2*reach+1, 2*reach+1))
        for a in range(-reach, reach+1):
            for b in range(-reach, reach+1):
                scores[a+reach, b+reach] = occupied[cells[:, 0]+a, cells[:, 1]+b].mean()
        base, top = float(scores[reach, reach]), float(scores.max())
        candidates = np.argwhere(scores >= top-.005)-reach
        a, b = min(candidates.tolist(), key=lambda k: k[0]**2+k[1]**2)
        row = {"id": name, "category": o["category"], "up_area_m2": up_area, "height_m": z,
               "covered_share": base, "best_shift_m": [a*cell_m, b*cell_m], "covered_share_after_shift": top,
               "at_search_limit": bool(max(abs(a), abs(b)) == reach)}
        if top >= good_cover and top-base >= min_gain and np.hypot(a, b)*cell_m >= min_shift_m and not row["at_search_limit"]:
            shifted.append(row)
        elif top < good_cover:
            poor.append(row)
    shifted.sort(key=lambda r: -(r["covered_share_after_shift"]-r["covered_share"])*r["up_area_m2"])
    poor.sort(key=lambda r: -(1-r["covered_share_after_shift"])*r["up_area_m2"])
    # Hidden faces (base tops under cushions, cabinet floors) also land here; keep the largest only.
    return {"shifted": shifted, "poor_fit": poor[:20], "poor_fit_omitted": max(0, len(poor)-20)}


def _detections(startup):
    """Oriented detection boxes from the job's perception stages (empty when there are none)."""
    found = []
    for path in sorted(Path(startup.get("job") or ".").glob("*/proposals.json")):
        for c in (read_json(path) or {}).get("candidates", []):
            g = c.get("geometry") or {}
            if g.get("type") != "obb" or "R_work_from_object" not in g:
                continue
            frames = {f for source in c.get("sources", []) for f in source.get("frame_ids", [])}
            found.append({"id": c["id"], "category": c.get("category"), "center_m": np.asarray(g["center_m"], float),
                          "size_m": np.asarray(g["size_m"], float), "R": np.asarray(g["R_work_from_object"], float),
                          "frames": len(frames)})
    return found


def _wrap90(degrees):
    return (np.asarray(degrees)+45) % 90-45


def facing_fit(objects, scan_points, scan_normals, sample_area, distance, nearest, detections=(), reach_m=.12,
               min_upright_m2=.04, min_span_m=.25, min_scan_m2=.05, min_scan_share=.35, min_turn_deg=8., agree_deg=3.,
               detection_reach_m=.15, min_detection_frames=8, min_detection_size_m=.25, min_facing_strength=.5):
    """Plan orientation of each object's upright faces against the scan and detection boxes nearby.

    Boxes default to the walls' axes, so a monitor, chair or table set at an angle keeps a wall-aligned yaw and
    only shows up as scattered residuals. Orientation is compared modulo 90 degrees (a box looks the same turned
    a quarter); objects narrower than min_span_m in plan (legs, posts) are skipped. Scan: upright samples within
    reach_m of the object that architecture and other objects do not explain; the dominant direction (5 degree
    window) must hold min_scan_share of them. Detection: the largest
    well-observed oriented box whose centre lies within detection_reach_m of the object. An object is listed when
    either source differs by min_turn_deg or more, unless the scan clearly agrees with the model (the detection
    then usually belongs to a neighbouring item). Objects whose upright faces have no dominant direction modulo 90
    degrees (round or many-sided: the area-weighted mean of exp(4i*angle) is below min_facing_strength of the
    upright area) have no yaw to fit and are listed in round instead."""
    up = np.abs(scan_normals[:, 2]) < .35
    theta = np.degrees(np.arctan2(scan_normals[:, 1], scan_normals[:, 0])) % 90
    architecture = np.array([objects.get(str(n), {}).get("category") in ARCHITECTURE for n in nearest]) & (distance < .03)
    centres = np.array([d["center_m"] for d in detections]).reshape(-1, 3)
    grid = np.arange(0, 90, 1.)
    turned, round_ = [], []
    for name in sorted(objects):
        o = objects[name]
        if o["category"] in ARCHITECTURE or not len(o["faces"]):
            continue
        xyz = np.asarray(o["xyz"], float)
        t = xyz[np.asarray(o["faces"])]
        n = np.cross(t[:, 1]-t[:, 0], t[:, 2]-t[:, 0])
        area = np.linalg.norm(n, axis=1)/2
        upright = np.abs(n[:, 2]) < .35*np.maximum(2*area, 1e-12)
        if area[upright].sum() < min_upright_m2 or np.ptp(xyz[:, :2], axis=0).max() < min_span_m:
            continue
        mean = (area[upright]*np.exp(4j*np.arctan2(n[upright, 1], n[upright, 0]))).sum()
        if abs(mean) < min_facing_strength*area[upright].sum():
            round_.append(name)
            continue
        model_yaw = round(float(np.degrees(np.angle(mean))/4), 6) % 90
        lo, hi = xyz.min(0), xyz.max(0)
        row = {"id": name, "category": o["category"], "model_yaw_mod90_deg": model_yaw}
        reach = np.array([reach_m, reach_m, 0])
        mask = (up & ~architecture & np.all((scan_points >= lo-reach) & (scan_points <= hi+reach), axis=1)
                & ((nearest == name) | (distance > .03)))
        if mask.sum()*sample_area >= min_scan_m2:
            offsets = _wrap90(theta[mask][None, :]-grid[:, None])
            votes = (np.abs(offsets) <= 5).sum(1)
            share = float(votes.max()/mask.sum())
            if share >= min_scan_share:
                best = offsets[votes.argmax()]
                peak = grid[votes.argmax()]+best[np.abs(best) <= 5].mean()  # refine within the window
                row["scan"] = {"turn_deg": float(_wrap90(peak-model_yaw)), "share": share,
                               "area_m2": float(mask.sum()*sample_area)}
        if len(centres):
            near = np.flatnonzero(np.all((centres >= lo-detection_reach_m) & (centres <= hi+detection_reach_m), axis=1))
            near = [i for i in near if detections[i]["frames"] >= min_detection_frames
                    and detections[i]["size_m"].max() >= min_detection_size_m]
            if near:
                d = detections[max(near, key=lambda i: np.prod(detections[i]["size_m"]))]
                yaw = np.degrees(np.arctan2(d["R"][1, 0], d["R"][0, 0]))
                row["detection"] = {"id": d["id"], "category": d["category"], "frames": d["frames"],
                                    "turn_deg": float(_wrap90(yaw-model_yaw))}
        scan = row.get("scan")
        scan_agrees = scan is not None and abs(scan["turn_deg"]) <= agree_deg and scan["share"] >= .5
        if (scan and abs(scan["turn_deg"]) >= min_turn_deg) or (
                "detection" in row and abs(row["detection"]["turn_deg"]) >= min_turn_deg and not scan_agrees):
            turned.append(row)
    turned.sort(key=lambda r: -max(abs(r.get("scan", {}).get("turn_deg", 0)), abs(r.get("detection", {}).get("turn_deg", 0))))
    return {"turned": turned, "round": round_}


def _excess_report(evidence, objects, cameras, camera_limit, spacing_m, margin_m, voxel_m, min_area_m2, views, bounds_work_m, seed):
    from .evidence import bounds
    use = cameras if cameras and len(cameras) <= camera_limit else _cameras(evidence, camera_limit)
    result = excess_surfaces(objects, evidence.ray_scene(), use, spacing_m, margin_m, seed=seed)
    points, normals, mask = result["points"], result["normals"], result["excess"].copy()
    if bounds_work_m is not None:
        box = bounds(bounds_work_m)
        mask &= np.all((points >= box[0]) & (points <= box[1]), axis=1)
    observed = result["observed"] > 0
    labels = np.full(len(points), -1)
    labels[mask] = oriented_clusters(points[mask], normals[mask], voxel_m)
    owner_ids = np.array(result["ids"])[result["owner"]]
    clusters, per_object = [], {}
    for name, area in zip(*np.unique(owner_ids[mask], return_counts=True)):
        per_object[str(name)] = float(area*result["sample_area"])
    rng = np.random.default_rng(seed)
    for label in np.unique(labels[mask]):
        member = labels == label
        area = float(member.sum()*result["sample_area"])
        if area < min_area_m2:
            continue
        p = points[member]
        orientation, shares = _orientation(normals[member])
        names, counts = np.unique(owner_ids[member], return_counts=True)
        order = np.argsort(-counts)[:3]
        through = result["mean_depth"][member]
        clusters.append({"label": int(label), "area_m2": area, "samples": int(member.sum()),
                         "centroid_work_m": p.mean(0).tolist(), "bounds_work_m": [p.min(0).tolist(), p.max(0).tolist()],
                         "orientation": orientation, "orientation_shares": shares,
                         "seen_through_m": {"median": float(np.median(through)), "p90": float(np.percentile(through, 90))},
                         "cameras_through_median": float(np.median(result["through"][member])),
                         "objects": [{"id": str(names[i]), "share": float(counts[i]/member.sum())} for i in order]})
    clusters.sort(key=lambda c: -c["area_m2"])
    for rank, cluster in enumerate(clusters, 1):
        cluster["rank"] = rank
        if views:
            members = np.flatnonzero(labels == cluster["label"])
            cluster["views"] = best_views(evidence, use, points[rng.choice(members, min(48, len(members)), replace=False)], views)
    area = len(points)*result["sample_area"]
    return {"cameras": len(use), "model_area_m2": float(area), "observed_share": float(observed.mean()),
            "excess_area_m2": float(mask.sum()*result["sample_area"]),
            "by_object_m2": dict(sorted(per_object.items(), key=lambda kv: -kv[1])),
            "clusters_total": len(clusters), "clusters": clusters}


# ---------------------------------------------------------------- geometry relations

def _closed(xyz, faces):
    import trimesh
    mesh = trimesh.Trimesh(np.asarray(xyz, float), np.asarray(faces), process=False)
    mesh.merge_vertices()
    return bool(len(mesh.faces)) and mesh.is_watertight


def object_group(object_id, parents, category):
    """Parts of one object: the assembly root, or for flat part lists the first two ID segments
    (furniture/sofa/back/cushion-0 -> furniture/sofa).

    Architecture is never grouped by prefix: walls sharing "arch/wall/" are separate elements."""
    root = assembly_root(object_id, parents)
    if root != object_id or category in ARCHITECTURE or object_id.count("/") < 2:
        return root
    return "/".join(object_id.split("/")[:2])


def interference(objects, parents, tolerance_m=.005, spacing_m=.01, include_same_assembly=False):
    """Pairs where one object's surface lies inside another closed object deeper than tolerance_m.

    Overlap between parts of the same object (simplified furniture parts) is skipped unless requested."""
    import open3d as o3d
    ids = sorted(objects)
    lo = np.array([objects[i]["xyz"].min(0) for i in ids])
    hi = np.array([objects[i]["xyz"].max(0) for i in ids])
    closed = {i: _closed(objects[i]["xyz"], objects[i]["faces"]) for i in ids}
    roots = {i: object_group(i, parents, objects[i]["category"]) for i in ids}
    overlap = np.all((lo[:, None] < hi[None]-tolerance_m) & (hi[:, None] > lo[None]+tolerance_m), axis=2)
    found, samples, scenes = [], {}, {}
    for a, b in zip(*np.nonzero(np.triu(overlap, 1))):
        ia, ib = ids[a], ids[b]
        if not include_same_assembly and roots[ia] == roots[ib]:
            continue
        if objects[ia]["host_id"] == ib or objects[ib]["host_id"] == ia:
            continue
        box = np.array([np.maximum(lo[a], lo[b]), np.minimum(hi[a], hi[b])])
        worst = None
        for inner, outer in ((ia, ib), (ib, ia)):
            if not closed[outer]:
                continue
            if inner not in samples:
                pts, _, _ = sample_surface(objects[inner]["xyz"], objects[inner]["faces"], spacing_m, seed=1, max_samples=200_000)
                samples[inner] = np.vstack([pts, objects[inner]["xyz"]])
            pts = samples[inner]
            pts = pts[np.all((pts >= box[0]) & (pts <= box[1]), axis=1)]
            if not len(pts):
                continue
            if outer not in scenes:
                scenes[outer] = o3d.t.geometry.RaycastingScene()
                scenes[outer].add_triangles(o3d.core.Tensor(objects[outer]["xyz"].astype(np.float32)), o3d.core.Tensor(objects[outer]["faces"]))
            scene = scenes[outer]
            query = o3d.core.Tensor(pts.astype(np.float32))
            inside = scene.compute_occupancy(query, nsamples=3).numpy() > .5
            if not inside.any():
                continue
            depth = scene.compute_distance(query).numpy()
            deep = inside & (depth > tolerance_m)
            if deep.any() and (worst is None or depth[deep].max() > worst["depth_m"]):
                worst = {"inside_object": outer, "penetrating_object": inner, "depth_m": float(depth[deep].max()),
                         "samples_inside": int(deep.sum()), "location_work_m": pts[deep].mean(0).tolist(),
                         "bounds_work_m": [pts[deep].min(0).tolist(), pts[deep].max(0).tolist()]}
        if worst:
            arch = [objects[i]["category"] in ARCHITECTURE for i in (ia, ib)]
            worst["objects"] = [ia, ib]
            worst["kind"] = ("architecture_junction" if all(arch) else "embedded_in_architecture" if any(arch) else "object_interference")
            found.append(worst)
    rank = {"object_interference": 0, "embedded_in_architecture": 1, "architecture_junction": 2}
    found.sort(key=lambda r: (rank[r["kind"]], -r["depth_m"]))
    return found


def _triangle_overlap(a, b):
    """Area and vertex centroid of the intersection of two 2D triangles (Sutherland-Hodgman)."""
    def ccw(t):
        return t if (t[1][0]-t[0][0])*(t[2][1]-t[0][1])-(t[1][1]-t[0][1])*(t[2][0]-t[0][0]) > 0 else t[::-1]
    polygon, clip = list(ccw(a)), ccw(b)
    for i in range(3):
        p, q = clip[i], clip[(i+1) % 3]
        side = lambda r: (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
        result = []
        for j in range(len(polygon)):
            c, d = polygon[j], polygon[(j+1) % len(polygon)]
            sc, sd = side(c), side(d)
            if sc >= 0:
                result.append(c)
            if (sc >= 0) != (sd >= 0):
                t = sc/(sc-sd)
                result.append((c[0]+t*(d[0]-c[0]), c[1]+t*(d[1]-c[1])))
        polygon = result
        if not polygon:
            return 0., None
    area = abs(sum(polygon[i][0]*polygon[(i+1) % len(polygon)][1]-polygon[(i+1) % len(polygon)][0]*polygon[i][1] for i in range(len(polygon))))/2
    return area, np.mean(polygon, axis=0)


def coplanar_overlaps(objects, tolerance_m=.001, min_area_m2=.0025):
    """Same-facing coplanar faces of different objects that overlap: visible z-fighting candidates."""
    tris, owner = [], []
    ids = sorted(objects)
    for k, name in enumerate(ids):
        t = objects[name]["xyz"][objects[name]["faces"]].astype(float)
        tris.append(t)
        owner.append(np.full(len(t), k))
    tri, owner = np.concatenate(tris), np.concatenate(owner)
    cross = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
    length = np.linalg.norm(cross, axis=1)
    keep = length > 1e-10
    tri, owner, normal = tri[keep], owner[keep], cross[keep]/length[keep, None]
    offset = np.einsum("ij,ij->i", normal, tri[:, 0])
    key = np.round(normal*50).astype(int)
    pairs = {}
    order = np.lexsort((offset, key[:, 2], key[:, 1], key[:, 0]))
    group_start = 0
    keys_sorted = key[order]
    for end in range(1, len(order)+1):
        if end < len(order) and np.array_equal(keys_sorted[end], keys_sorted[group_start]):
            continue
        group = order[group_start:end]
        group_start = end
        if len(set(owner[group].tolist())) < 2:
            continue
        for x in range(len(group)):
            i = group[x]
            for y in range(x+1, len(group)):
                j = group[y]
                if offset[j]-offset[i] > tolerance_m:
                    break
                if owner[i] == owner[j] or normal[i] @ normal[j] < .9995:
                    continue
                n = normal[i]
                u = np.cross(n, [1, 0, 0] if abs(n[0]) < .9 else [0, 1, 0])
                u /= np.linalg.norm(u)
                v = np.cross(n, u)
                a = [(p @ u, p @ v) for p in tri[i]]
                b = [(p @ u, p @ v) for p in tri[j]]
                if max(p[0] for p in a) <= min(p[0] for p in b) or max(p[0] for p in b) <= min(p[0] for p in a):
                    continue
                if max(p[1] for p in a) <= min(p[1] for p in b) or max(p[1] for p in b) <= min(p[1] for p in a):
                    continue
                area, centre = _triangle_overlap(a, b)
                if area > 1e-8:
                    pair = tuple(sorted((int(owner[i]), int(owner[j]))))
                    record = pairs.setdefault(pair, {"area_m2": 0., "points": [], "normal": n.tolist()})
                    record["area_m2"] += area
                    record["points"].append(offset[i]*n+centre[0]*u+centre[1]*v)
    found = []
    for (a, b), record in pairs.items():
        if record["area_m2"] < min_area_m2:
            continue
        pts = np.array(record["points"])
        found.append({"objects": [ids[a], ids[b]], "overlap_area_m2": record["area_m2"], "normal_work": record["normal"],
                      "location_work_m": pts.mean(0).tolist(), "bounds_work_m": [pts.min(0).tolist(), pts.max(0).tolist()],
                      "_points": pts})
    found.sort(key=lambda r: -r["overlap_area_m2"])
    return found


def camera_visibility(objects, origins, points, normals, offset_m=.002, slack_m=.003):
    """For each point, how many capture positions on its front side see it unobstructed."""
    import open3d as o3d
    tracer, _ = _tracer(objects, sorted(objects))
    origins, points, normals = (np.asarray(x, float) for x in (origins, points, normals))
    lifted = points+normals*offset_m
    vectors = lifted[:, None]-origins[None]
    distance = np.linalg.norm(vectors, axis=2)
    front = np.einsum("pcj,pj->pc", -vectors, normals) > 0
    rays = np.column_stack((np.broadcast_to(origins, vectors.shape).reshape(-1, 3), (vectors/distance[..., None]).reshape(-1, 3))).astype(np.float32)
    hit = tracer.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy().reshape(distance.shape)
    return ((hit >= distance-slack_m) & front).sum(1)


CUTAWAY = {"ceiling", "roof"}


def open_along_normal(objects, points, normals, clear_m=.3, offset_m=.002):
    """For each point, whether the space in front of it stays clear for clear_m with ceilings and roofs removed.

    Capture cameras stand inside rooms, so a face under a ceiling (a beam or partition top) never counts as seen,
    yet cutaway viewers, top-down cameras and plans remove the ceiling and show it."""
    import open3d as o3d
    tracer, _ = _tracer(objects, sorted(n for n in objects if objects[n]["category"] not in CUTAWAY))
    points, normals = np.asarray(points, float), np.asarray(normals, float)
    rays = np.column_stack((points+normals*offset_m, normals)).astype(np.float32)
    hit = tracer.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
    return hit > clear_m


def opening_boxes(parameters, margin_m=.15):
    """Work-space boxes around parameter openings (open doorways export no mesh)."""
    boxes = {}
    for ob in (parameters or {}).get("objects", []):
        if ob["geometry"]["type"] == "opening":
            pts = np.array([parameters["vertices"][k] for k in ob["geometry"]["boundary_ids"]], float)
            boxes[ob["id"]] = (pts.min(0)-margin_m, pts.max(0)+margin_m)
    return boxes


def shell_leaks(objects, origins, directions=1200, near_m=.3, voxel_m=.25, openings=None, max_located=20000, seed=0):
    """Rays from capture positions that leave the model without hitting anything, grouped where they pass the shell."""
    import open3d as o3d
    tracer, names = _tracer(objects, sorted(objects))
    golden = np.pi*(3-np.sqrt(5))
    k = np.arange(directions)
    z = 1-2*(k+.5)/directions
    r = np.sqrt(1-z*z)
    dirs = np.column_stack((r*np.cos(golden*k), r*np.sin(golden*k), z))
    origins = np.asarray(origins, float)
    rays = np.column_stack((np.repeat(origins, directions, 0), np.tile(dirs, (len(origins), 1)))).astype(np.float32)
    escaped = ~np.isfinite(tracer.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy())
    all_xyz = np.vstack([o["xyz"] for o in objects.values()])
    lo, hi = all_xyz.min(0), all_xyz.max(0)
    exits = []
    escaped_index = np.flatnonzero(escaped)
    if len(escaped_index) > max_located:  # locate a reproducible subset; the escaped count stays complete
        escaped_index = np.sort(np.random.default_rng(seed).choice(escaped_index, max_located, replace=False))
    origin_index = np.repeat(np.arange(len(origins)), directions)[escaped_index]
    for (o, d) in zip(rays[escaped_index, :3].astype(float), rays[escaped_index, 3:].astype(float)):
        with np.errstate(divide="ignore", invalid="ignore"):
            t_exit = np.nanmin(np.where(d > 0, (hi-o)/d, np.where(d < 0, (lo-o)/d, np.inf)))
        steps = np.arange(0, max(t_exit, 0)+.05, .05)
        exits.append((o, d, steps))
    if not exits:
        return {"rays": int(len(rays)), "escaped": 0, "located": 0, "groups": []}
    samples = np.concatenate([o+d*s[:, None] for o, d, s in exits])
    owner = np.concatenate([np.full(len(s), i) for i, (_, _, s) in enumerate(exits)])
    distance, nearest = _closest(tracer, names, samples)
    points, near_ids = [], []
    for i in range(len(exits)):
        index = np.flatnonzero(owner == i)
        d = distance[index]
        # Where the ray slips through the shell: the last sample still close to geometry, where "close" adapts
        # to the tightest squeeze on the outer half of the path (a wide doorway never comes within near_m).
        outer = d[len(d)//2:]
        limit = max(near_m, 1.5*float(outer.min())+.05) if len(outer) else near_m
        j = index[np.flatnonzero(d < limit)[-1]] if (d < limit).any() else index[-1]
        points.append(samples[j])
        near_ids.append(nearest[j])
    points = np.array(points)
    labels, _ = voxel_clusters(points, voxel_m)
    openings = openings or {}
    groups = []
    for label in np.unique(labels):
        mask = labels == label
        p = points[mask]
        centre = p.mean(0)
        near, counts = np.unique(np.array(near_ids)[mask], return_counts=True)
        through = [name for name, (a, b) in openings.items() if np.all((centre >= a) & (centre <= b))]
        groups.append({"rays": int(mask.sum()), "cameras": int(len(set(origin_index[mask].tolist()))),
                       "location_work_m": centre.tolist(), "bounds_work_m": [p.min(0).tolist(), p.max(0).tolist()],
                       "nearest_objects": [str(near[i]) for i in np.argsort(-counts)[:3] if near[i]],
                       "within_opening": through})
    groups.sort(key=lambda g: (bool(g["within_opening"]), -g["rays"]))
    return {"rays": int(len(rays)), "escaped": int(escaped.sum()), "located": int(len(escaped_index)), "groups": groups}


def semantic_warnings(model):
    """Category checks against geometry; warnings, not validation failures."""
    from .scene_model import surface_loops
    warnings = []
    vertices = model["vertices"]
    geometry_names = {"surface", "box", "prism", "opening", "assembly", "mesh_reference", "panel", "component"}
    for ob in model["objects"]:
        cat, g = ob["category"].lower(), ob["geometry"]
        if cat in geometry_names:
            warnings.append({"id": ob["id"], "category": ob["category"], "code": "geometry_type_as_category", "issue": "category names a geometry type or role, not what the object is"})
            continue
        if g["type"] != "surface" or cat not in ("wall", "partition", "floor", "ceiling"):
            continue
        (origin, u, v, n), loops = surface_loops(g, vertices)
        pts = np.array([vertices[i] for i in g["boundary_ids"]], float)
        height = float(np.ptp(pts[:, 2]))
        along = np.cross(n, [0, 0, 1])
        span = float(np.ptp(pts @ (along/np.linalg.norm(along)))) if np.linalg.norm(along) > 1e-6 else 0.
        if cat in ("wall", "partition"):
            if abs(n[2]) > .3:
                warnings.append({"id": ob["id"], "category": ob["category"], "code": "wall_not_vertical", "issue": "wall/partition surface is not vertical"})
            elif height < 1.0 or span < .25:
                warnings.append({"id": ob["id"], "category": ob["category"], "code": "wall_too_small", "issue": f"wall/partition only {height:.2f} m tall and {span:.2f} m long; use the object's own category (header, soffit, lamp, panel of a fixture...)"})
        elif abs(n[2]) < .9:
            warnings.append({"id": ob["id"], "category": ob["category"], "code": "slab_not_horizontal", "issue": f"{cat} surface is not horizontal"})
    return warnings


def geometry(model_path, output, startup_path=None, tolerance_m=.005, coplanar_tolerance_m=.001, min_overlap_m2=.0025,
             leak_cameras=120, leak_directions=1200, include_same_assembly=False):
    objects, parameters = load_objects(model_path)
    output = _new_output(output)
    parents = {o["id"]: o.get("parent_id", "") for o in (parameters or {}).get("objects", [])}
    report = {"passed": True, "geometry_quality_passed": None, "model": str(Path(model_path).resolve()),
              "model_sha256": sha256(model_path), "model_revision": (parameters or {}).get("revision"), "objects": len(objects),
              "parameters": {"tolerance_m": tolerance_m, "coplanar_tolerance_m": coplanar_tolerance_m, "min_overlap_m2": min_overlap_m2,
                             "include_same_assembly": include_same_assembly}}
    report["interference"] = interference(objects, parents, tolerance_m, include_same_assembly=include_same_assembly)
    report["coplanar_overlap"] = coplanar_overlaps(objects, coplanar_tolerance_m, min_overlap_m2)
    warnings = semantic_warnings(parameters) if parameters else []
    grouped = {}
    for w in warnings:
        g = grouped.setdefault((w["code"], w["category"]), {"code": w["code"], "category": w["category"], "count": 0, "examples": []})
        g["count"] += 1
        if len(g["examples"]) < 5:
            g["examples"].append({"id": w["id"], "detail": w["issue"]})
    report["semantic_warnings"] = sorted(grouped.values(), key=lambda g: -g["count"])
    for record in report["coplanar_overlap"]:
        pts = np.array(record["_points"])
        pick = pts[np.linspace(0, len(pts)-1, min(8, len(pts))).astype(int)]
        record["_pick"] = pick
        record["open_sample_points"] = int(open_along_normal(objects, pick, np.repeat([record["normal_work"]], len(pick), 0)).sum())
        record["sample_points"] = len(pick)
    if startup_path:
        from .evidence import Evidence
        startup = read_json(startup_path)
        if parameters and parameters.get("scene_id") != startup["scene_id"]:
            raise ContractError("Model scene_id differs from startup")
        evidence = Evidence(read_json(startup["evidence_config"]), startup["evidence_roots"])
        centres = [pose[:3, 3] for _, _, _, pose in _cameras(evidence, leak_cameras)]
        report["shell_leaks"] = shell_leaks(objects, centres, leak_directions, openings=opening_boxes(parameters))
        for record in report["coplanar_overlap"]:
            pick = record["_pick"]
            seen = camera_visibility(objects, centres, pick, np.repeat([record["normal_work"]], len(pick), 0))
            record["visible_sample_points"] = int((seen > 0).sum())
        report["parameters"].update(leak_cameras=len(centres), leak_directions=leak_directions)
    else:
        report["shell_leaks"] = None
    for record in report["coplanar_overlap"]:
        record.pop("_points")
        record.pop("_pick")
    report["coplanar_overlap"].sort(key=lambda r: (-max(r.get("visible_sample_points", 0), r["open_sample_points"]), -r["overlap_area_m2"]))
    report["counts"] = {"object_interference": sum(r["kind"] == "object_interference" for r in report["interference"]),
                        "embedded_in_architecture": sum(r["kind"] == "embedded_in_architecture" for r in report["interference"]),
                        "architecture_junction": sum(r["kind"] == "architecture_junction" for r in report["interference"]),
                        "coplanar_overlap": len(report["coplanar_overlap"]), "semantic_warnings": len(warnings),
                        "shell_leak_groups": None if report["shell_leaks"] is None else len(report["shell_leaks"]["groups"]),
                        "shell_leak_groups_outside_openings": None if report["shell_leaks"] is None else
                        sum(not g["within_opening"] for g in report["shell_leaks"]["groups"])}
    report["interpretation"] = ("Each entry is a candidate with a location. object_interference = solid parts of different objects overlapping; "
                                "architecture_junction = overlapping building elements (often harmless corner overlap); coplanar_overlap = "
                                "same-facing coincident faces that flicker; fix every one, since open_sample_points counts samples left exposed once ceilings are cut "
                                "away (plans, cutaway and top-down views) and visible_sample_points those a capture camera sees (with --startup); "
                                "embedded_in_architecture = an object sunk into a wall/floor/ceiling; shell_leaks = gaps rays from capture cameras escape through "
                                "(within_opening lists openings that explain it). Confirm against scan/photos before changing geometry.")
    atomic_json(output/"geometry.json", report)
    return report
