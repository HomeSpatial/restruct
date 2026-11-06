# SPDX-License-Identifier: Apache-2.0
"""Surface appearance from calibrated photos: exposure-normalised colour per cell and colour regions.

The scan's vertex colours mix exposure and lighting from whichever frame textured them, so the same tile can
look grey at one end and near black at the other. Here every visible photo observation of a surface sample is
kept, a per-frame, per-channel gain (exposure and automatic white balance) is solved jointly with the sample
colours in linear light, and regions are split on chromaticity (colour independent of brightness), which
separates materials such as wood and tile under uneven light. An optional white reference surface sets absolute
colour by a per-channel scale; it does not change the regions.
"""
import numpy as np
from PIL import Image, ImageDraw

from .contracts import ContractError


def _basis(normal):
    n = np.asarray(normal, float)
    if n.shape != (3,) or not np.isfinite(n).all() or np.linalg.norm(n) == 0:
        raise ContractError("normal_work must be a finite nonzero 3-vector")
    n = n/np.linalg.norm(n)
    if abs(n[2]) > .9:  # floors and ceilings: plan axes
        u = np.array([1., 0, 0])-n[0]*n
    else:               # walls: horizontal along the wall, then up
        u = np.cross([0., 0, 1], n)
    u /= np.linalg.norm(u)
    return n, u, np.cross(n, u)


def _samples(vertices, faces, spacing_m, rng):
    tri = vertices[faces]
    area = np.linalg.norm(np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0]), axis=1)/2
    expected = area/spacing_m**2
    count = np.floor(expected+rng.random(len(expected))).astype(int)
    owner = np.repeat(np.arange(len(faces)), count)
    a, b = rng.random((2, len(owner)))
    flip = a+b > 1
    a[flip], b[flip] = 1-a[flip], 1-b[flip]
    t = tri[owner]
    return t[:, 0]+a[:, None]*(t[:, 1]-t[:, 0])+b[:, None]*(t[:, 2]-t[:, 0])


def _segment(feature, observed, scale, min_cells, merge_gap):
    """Felzenszwalb-Huttenlocher merging on a cell grid of features ((r, b) chromaticity, optionally log
    luminance), 3x3 median smoothed.

    Returns a label grid (-1 where unobserved or in a fragment smaller than min_cells)."""
    h, w = observed.shape
    feature = np.where(observed[..., None], feature, np.nan)
    pad = np.pad(feature, ((1, 1), (1, 1), (0, 0)), constant_values=np.nan)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            feature = np.nanmedian(np.stack([pad[i:i+h, j:j+w] for i in range(3) for j in range(3)]), 0)
    index = np.full((h, w), -1)
    index[observed] = np.arange(observed.sum())
    values = feature[observed]
    pairs = []
    for di, dj in ((1, 0), (0, 1)):
        a, b = index[:h-di, :w-dj], index[di:, dj:]
        both = (a >= 0) & (b >= 0)
        pairs.append(np.column_stack((a[both], b[both])))
    edges = np.concatenate(pairs)
    weight = np.linalg.norm(values[edges[:, 0]]-values[edges[:, 1]], axis=1)
    order = np.argsort(weight, kind="stable")
    parent, size, internal = np.arange(len(values)), np.ones(len(values)), np.zeros(len(values))

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for e in order:
        a, b = find(edges[e, 0]), find(edges[e, 1])
        if a != b and weight[e] <= min(internal[a]+scale/size[a], internal[b]+scale/size[b]):
            parent[b], size[a], internal[a] = a, size[a]+size[b], max(internal[a], internal[b], weight[e])
    for e in order:  # absorb fragments into their most similar neighbour
        a, b = find(edges[e, 0]), find(edges[e, 1])
        if a != b and min(size[a], size[b]) < min_cells:
            parent[b], size[a] = a, size[a]+size[b]
    _, label = np.unique([find(i) for i in range(len(values))], return_inverse=True)
    while True:
        median = np.array([np.median(values[label == r], 0) for r in range(label.max()+1)])
        touching = {tuple(sorted(p)) for p in zip(label[edges[:, 0]], label[edges[:, 1]]) if p[0] != p[1]}
        closest = min(touching, key=lambda p: np.linalg.norm(median[p[0]]-median[p[1]]), default=None)
        if closest is None or np.linalg.norm(median[closest[0]]-median[closest[1]]) >= merge_gap:
            break
        label[label == closest[1]] = closest[0]
        _, label = np.unique(label, return_inverse=True)
    counts = np.bincount(label)
    keep = counts >= min_cells  # isolated cells with no observed neighbour stay unassigned
    remap = np.full(len(counts), -1)
    remap[keep] = np.arange(keep.sum())
    grid = np.full((h, w), -1)
    grid[observed] = remap[label]
    return grid


REFERENCE_MIN_OBSERVATIONS = 150
SCALE_BLOCK = (.25, 4.)   # linear per-channel reference scale outside this: reference wrong or clipped
SCALE_WARN = (.5, 2.)
CAST_WARN = 1.6           # largest / smallest channel scale
CLIPPED_BLOCK = .05       # share of cells brighter than white after scaling
REGION_MIN_FRAMES = 3


def _to_linear(srgb_255):
    c = np.asarray(srgb_255, float)/255
    return np.where(c <= .04045, c/12.92, ((c+.055)/1.055)**2.4)


def _to_srgb(linear):
    c = np.clip(np.asarray(linear, float), 0, 1)
    return 255*np.where(c <= .0031308, c*12.92, 1.055*c**(1/2.4)-.055)


def _weighted_median(values, weights):
    order = np.argsort(values)
    cumulative = np.cumsum(weights[order])
    return values[order][np.searchsorted(cumulative, cumulative[-1]/2)]


def _surface_points(vertices, faces, bounds_work_m, normal_work, tolerance_deg, spacing_m, rng, label):
    from .evidence import bounds
    box = bounds(bounds_work_m)
    n, u, v = _basis(normal_work)
    tri = vertices[faces]
    normals = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
    length = np.linalg.norm(normals, axis=1)
    keep = (np.all((tri.max(1) >= box[0]) & (tri.min(1) <= box[1]), axis=1) & (length > 0)
            & ((normals @ n) > np.cos(np.radians(tolerance_deg))*length))
    points = _samples(vertices, faces[keep], spacing_m, rng) if keep.any() else np.empty((0, 3))
    points = points[np.all((points >= box[0]) & (points <= box[1]), axis=1)]
    if not len(points):
        raise ContractError(f"No scan surface in {label} bounds faces along its normal")
    if len(points) > 200_000:
        points = points[rng.choice(len(points), 200_000, replace=False)]
    return box, (n, u, v), points


def surface_appearance(evidence, output, bounds_work_m, normal_work, cell_m=.05, max_frames=120, segment_scale=1.,
                       min_region_m2=.25, min_chroma_gap=.015, normal_tolerance_deg=25., white_reference=None,
                       lightness_weight=0., seed=0):
    if not (np.isfinite(cell_m) and .01 <= cell_m <= .5):
        raise ContractError("cell_m must be between 0.01 and 0.5")
    if type(max_frames) is not int or max_frames < 1 or not segment_scale > 0:
        raise ContractError("max_frames must be a positive integer and segment_scale positive")
    if not (np.isfinite(lightness_weight) and 0 <= lightness_weight <= 1):
        raise ContractError("lightness_weight must be between 0 and 1")
    rng = np.random.default_rng(seed)
    vertices, faces = evidence.scan_mesh()
    box, (n, u, v), points = _surface_points(vertices, faces, bounds_work_m, normal_work, normal_tolerance_deg,
                                             cell_m/2, rng, "surface")
    target = len(points)
    reference = None
    if white_reference is not None:
        if not isinstance(white_reference, dict) or set(white_reference)-{"bounds_work_m", "normal_work", "colour_srgb_255"}:
            raise ContractError("white_reference takes bounds_work_m, normal_work and optional colour_srgb_255")
        wanted = np.asarray(white_reference.get("colour_srgb_255", [235, 235, 232]), float)
        if wanted.shape != (3,) or np.any(wanted <= 0) or np.any(wanted > 255):
            raise ContractError("white_reference colour_srgb_255 must be three values in (0, 255]")
        _, (rn, _, _), extra = _surface_points(vertices, faces, white_reference["bounds_work_m"],
                                               white_reference["normal_work"], normal_tolerance_deg, .05, rng, "white_reference")
        reference = {"wanted": wanted, "normal": rn}
        points = np.vstack((points, extra))
    sample_normal = np.repeat([n], len(points), 0)
    if reference:
        sample_normal[target:] = reference["normal"]

    # Every frame that sees a sample unoccluded and not at a grazing angle contributes one colour. Like
    # photogrammetry texturing, near and head-on views weigh more: the weight is the sample's projected area,
    # cos(angle)/distance^2. The max_frames frames with the most target samples in view are used.
    def project(identity):
        frame, k, pose = evidence.frame(identity)
        camera = (points-pose[:3, 3]) @ pose[:3, :3]
        front = camera[:, 2] > .1
        uv = (camera @ k.T)[:, :2]/np.where(front, camera[:, 2], 1)[:, None]
        width, height = frame["size"]
        inside = front & (uv[:, 0] >= 0) & (uv[:, 0] < width-1) & (uv[:, 1] >= 0) & (uv[:, 1] < height-1)
        return frame, pose, uv, np.flatnonzero(inside)
    in_view = {identity: int((project(identity)[3] < target).sum()) for identity in evidence.frames}
    frames = sorted((i for i in in_view if in_view[i]), key=lambda i: -in_view[i])[:max_frames]
    sample_ids, frame_ids, colours, weights = [], [], [], []
    scene = evidence.ray_scene()
    import open3d as o3d
    for index, identity in enumerate(frames):
        frame, pose, uv, seen = project(identity)
        offset = points[seen]-pose[:3, 3]
        distance = np.linalg.norm(offset, axis=1)
        direction = offset/distance[:, None]
        cosine = np.abs(np.einsum("ij,ij->i", direction, sample_normal[seen]))
        rays = np.hstack([np.repeat(pose[None, :3, 3], len(seen), 0), direction]).astype(np.float32)
        hit = scene.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
        visible = (np.abs(hit-distance) <= np.maximum(.03, .01*distance)) & (cosine >= .25)
        seen, weight = seen[visible], (cosine/distance**2)[visible]
        if not len(seen):
            continue
        with Image.open(evidence.resolve(frame["image"])) as image:
            pixels = np.asarray(image.convert("RGB"), float)
        ij = np.round(uv[seen]).astype(int)
        rgb = pixels[ij[:, 1], ij[:, 0]]
        usable = (rgb.max(1) < 250) & (rgb.min(1) > 4) & (rgb.mean(1) > 12)  # clipped highlights, deep shadow
        sample_ids.append(seen[usable])
        frame_ids.append(np.full(usable.sum(), index))
        colours.append(rgb[usable])
        weights.append(weight[usable])
    if not sample_ids or not sum(map(len, sample_ids)):
        raise ContractError("No photo sees the requested surface")
    s, f, rgb, w = map(np.concatenate, (sample_ids, frame_ids, colours, weights))
    w = w/np.median(w)

    # Joint radiometry in linear light, per channel: log value = sample albedo + frame gain. A per-channel gain
    # removes each frame's exposure and its automatic white balance together, relative to the median frame.
    log = np.log(_to_linear(rgb))
    gain = np.zeros((len(frames), 3))
    used = np.unique(f)
    total = np.maximum(np.bincount(s, w, len(points)), 1e-12)
    on_target = s < target  # gains come from the target surface alone; a reference only rescales the result
    for _ in range(8):
        albedo = np.stack([np.bincount(s, w*(log[:, c]-gain[f, c]), len(points)) for c in range(3)], 1)/total[:, None]
        residual = log-albedo[s]
        for i in used:
            mine = on_target & (f == i)
            gain[i] = np.median(residual[mine], 0) if mine.any() else 0
        gain -= np.median(gain[used], 0)
    corrected = np.exp(log-gain[f])
    scale = np.ones(3)
    if reference:
        ref = s >= target
        if ref.sum() < 20:
            raise ContractError("No photo sees the white reference surface")
        measured = np.array([_weighted_median(corrected[ref, c], w[ref]) for c in range(3)])
        scale = _to_linear(reference["wanted"])/measured
        corrected = corrected*scale
        reference.update(measured_srgb_255=_to_srgb(measured).round(1).tolist(), observations=int(ref.sum()))
        keep = ~ref
        s, f, corrected, w = s[keep], f[keep], corrected[keep], w[keep]
        points = points[:target]

    # Cells on the surface plane hold the weighted mean colour of their observations.
    uvw = np.column_stack((points @ u, points @ v))
    lo = uvw.min(0)
    cell = np.floor((uvw-lo)/cell_m).astype(int)
    shape = tuple(cell.max(0)+1)
    flat = np.ravel_multi_index(cell[s].T, shape)
    count = np.bincount(flat, minlength=np.prod(shape))
    weight_sum = np.maximum(np.bincount(flat, w, np.prod(shape)), 1e-12)
    linear = np.stack([np.bincount(flat, w*corrected[:, c], np.prod(shape)) for c in range(3)], 1)/weight_sum[:, None]
    colour = _to_srgb(linear)
    observed = count >= 2
    grid_colour = colour.reshape(*shape, 3)
    unscaled = _to_srgb(linear/scale)  # regions do not depend on whether a white reference was given
    chroma = unscaled/np.maximum(unscaled.sum(1, keepdims=True), 1e-6)

    # Regions: graph segmentation on chromaticity. Light falling off across a floor changes colour gradually,
    # a material edge changes it abruptly, so neighbouring cells merge unless the step between them stands out
    # against the variation already inside both regions; adjacent regions with similar colour are merged after.
    # Neutral materials (grey paint next to white tile) share chromaticity; lightness_weight adds log luminance,
    # which also splits shadows, so it is opt-in.
    min_cells = max(1, int(round(min_region_m2/cell_m**2)))
    feature = chroma[:, [0, 2]]
    if lightness_weight:
        luminance = np.maximum(linear/scale, 1e-4) @ [.2126, .7152, .0722]
        feature = np.column_stack((feature, lightness_weight*np.log(luminance)))
    grid = _segment(feature.reshape(*shape, -1), observed.reshape(shape), segment_scale, min_cells, min_chroma_gap)
    regions = int(grid.max())+1

    def to_work(cells):
        centre = lo+(np.asarray(cells, float)+.5)*cell_m
        offset = np.median(points @ n)
        return centre[:, :1]*u+centre[:, 1:]*v+offset*n

    rows = []
    for r in range(regions):
        mask = grid == r
        if not mask.any():
            continue
        members = np.isin(flat, np.flatnonzero(mask.ravel()))
        base = np.array([_weighted_median(corrected[members, c], w[members]) for c in range(3)])
        srgb = _to_srgb(base)
        idx = np.argwhere(mask)
        work = to_work(idx)
        rows.append({"label": r, "area_m2": float(mask.sum()*cell_m**2), "cells": int(mask.sum()),
                     "colour_srgb_255": srgb.round(1).tolist(), "base_colour_linear": np.clip(base, 0, 1).round(4).tolist(),
                     "chromaticity_rg": (srgb/max(srgb.sum(), 1e-6))[:2].round(4).tolist(),
                     "spread_srgb_255": _to_srgb(np.percentile(corrected[members], [25, 75], 0)).round(1).tolist(),
                     "observations": int(members.sum()), "frames": int(len(np.unique(f[members]))),
                     "clipped": bool(base.max() > 1),
                     "bounds_work_m": [work.min(0).round(3).tolist(), work.max(0).round(3).tolist()]})

    # Shared edges between regions, fitted as straight segments where the cells line up.
    from scipy import ndimage
    boundaries = []
    for a in range(regions):
        for b in range(a+1, regions):
            edge = (grid == a) & ndimage.binary_dilation(grid == b)
            idx = np.argwhere(edge)
            if len(idx) < 4:
                continue
            work = to_work(idx)
            centre = work.mean(0)
            _, _, axes = np.linalg.svd(work-centre, full_matrices=False)
            along = (work-centre) @ axes[0]
            across = (work-centre) @ axes[1]
            boundaries.append({"regions": [a, b], "cells": int(len(idx)), "length_m": float(np.ptp(along)),
                               "segment_work_m": [(centre+along.min()*axes[0]).round(3).tolist(),
                                                  (centre+along.max()*axes[0]).round(3).tolist()],
                               "straightness_rms_m": float(np.sqrt((across**2).mean()))})

    reliability = _reliability(reference, scale, rows, linear[observed])
    _image(output/"appearance.png", grid_colour, observed.reshape(shape), grid, rows, cell_m)
    np.savez_compressed(output/"appearance.npz", origin_uv_m=lo, cell_m=cell_m, axis_u_work=u, axis_v_work=v,
                        normal_work=n, colour_srgb=grid_colour.astype(np.float32), observations=count.reshape(shape),
                        region=grid)
    used = np.unique(f)
    return {"bounds_work_m": box.tolist(), "normal_work": n.tolist(), "axes_work": {"u": u.tolist(), "v": v.tolist()},
            "cell_m": cell_m, "samples": len(points), "observations": len(s),
            "frames_used": [frames[i] for i in used], "frame_gain_rgb": {frames[i]: np.exp(gain[i]).round(4).tolist() for i in used},
            "white_reference": None if reference is None else {
                "colour_srgb_255": reference["wanted"].tolist(), "measured_srgb_255": reference["measured_srgb_255"],
                "observations": reference["observations"], "scale_linear_rgb": scale.round(4).tolist()},
            "cells_observed": int(observed.sum()), "regions": rows, "reliability": reliability, "boundaries": boundaries,
            "image": "appearance.png", "data": "appearance.npz",
            "interpretation": ("Colours are photo pixels in linear light with a per-frame, per-channel gain removed, which "
                               "evens out exposure and automatic white balance across frames relative to the median frame; "
                               "near, head-on views weigh more. Without white_reference the absolute white balance is that "
                               "of the median frame; with it, colours are scaled so the reference surface takes its stated "
                               "colour. Lighting falloff across the surface remains. Regions split on "
                               "chromaticity, so they separate materials of different hue or saturation (wood, tile, "
                               "paint) but not same-hue materials differing only in brightness. Boundary segments are "
                               "straight-line fits of the shared cell edges; check them against a photo before cutting. Use colours "
                               "only when reliability.usable is true; read its warnings either way.")}


def _reliability(reference, scale, rows, cells):
    """Checks a result can fail while the query still runs: a reference seen too little, a scale so large it
    clips the surface, a reference with a strong cast (not white, or under other light), thin regions."""
    blocking, warnings = [], []
    if not rows:
        blocking.append({"code": "no_regions", "detail": "no region reached min_region_m2 with two observations per cell"})
    if reference is not None:
        if reference["observations"] < REFERENCE_MIN_OBSERVATIONS:
            blocking.append({"code": "reference_few_observations", "detail": f"{reference['observations']} observations"})
        if scale.max() > SCALE_BLOCK[1] or scale.min() < SCALE_BLOCK[0]:
            blocking.append({"code": "reference_scale_extreme", "detail": f"linear scale {scale.round(3).tolist()}"})
        elif scale.max() > SCALE_WARN[1] or scale.min() < SCALE_WARN[0]:
            warnings.append({"code": "reference_scale_large", "detail": f"linear scale {scale.round(3).tolist()}"})
        if scale.max()/scale.min() > CAST_WARN:
            warnings.append({"code": "reference_cast", "detail": "reference colour far from neutral: check it is a white "
                             "surface under the same light as the target"})
        clipped = float((cells.max(1) > 1).mean()) if len(cells) else 0.
        if clipped > CLIPPED_BLOCK:
            blocking.append({"code": "clipped_after_scale", "detail": f"{clipped:.1%} of cells exceed white"})
    for r in rows:
        if r["frames"] < REGION_MIN_FRAMES or r["clipped"]:
            warnings.append({"code": "region_weak", "detail": f"region {r['label']}: {r['frames']} frames"
                             + (", clipped" if r["clipped"] else "")})
    return {"usable": not blocking, "absolute_colour": reference is not None and not blocking,
            "blocking": blocking, "warnings": warnings}


def _image(path, colour, observed, grid, rows, cell_m):
    h, w = grid.shape  # u along image x, v up
    scale = max(1, int(600/max(h, w)))
    left = np.where(observed[..., None], colour, 30).astype(np.uint8)
    by_label = {r["label"]: np.array(r["colour_srgb_255"]) for r in rows}
    right = np.full((h, w, 3), 30, np.uint8)
    for label, c in by_label.items():
        right[grid == label] = c.astype(np.uint8)
    edge = np.zeros_like(grid, bool)
    edge[:-1] |= (grid[:-1] != grid[1:]) & (grid[:-1] >= 0) & (grid[1:] >= 0)
    edge[:, :-1] |= (grid[:, :-1] != grid[:, 1:]) & (grid[:, :-1] >= 0) & (grid[:, 1:] >= 0)
    right[edge] = (255, 60, 60)
    panels = [Image.fromarray(np.flipud(p.transpose(1, 0, 2))).resize((h*scale, w*scale), Image.NEAREST) for p in (left, right)]
    sheet = Image.new("RGB", (panels[0].width*2+10, panels[0].height+40), (20, 20, 20))
    sheet.paste(panels[0], (0, 40))
    sheet.paste(panels[1], (panels[0].width+10, 40))
    draw = ImageDraw.Draw(sheet)
    draw.text((6, 6), f"exposure-normalised photo colour, {cell_m*100:g} cm cells (u right, v up)", fill="white")
    x = panels[0].width+16
    for r in sorted(rows, key=lambda r: r["label"]):
        draw.rectangle((x, 8, x+14, 22), fill=tuple(int(c) for c in r["colour_srgb_255"]))
        draw.text((x+18, 10), f"#{r['label']} {r['area_m2']:.1f} m2", fill="white")
        x += 120
    sheet.save(path)
