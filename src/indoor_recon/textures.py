# SPDX-License-Identifier: Apache-2.0
"""Procedural, tileable PBR material textures for parametric models.

The scan photos are too coarse and too cluttered to texture a model directly, but they do show what a material is
and its measurable layout: plank width and length, tile size and grout, the colour from `appearance`. These
generators turn that into repeating base-colour, roughness and normal images; the colour's median matches the
measured colour, joints and grout are recessed in the normal map and rougher. Every image wraps around at its
edges, so a material maps them with `uv_box_m` (box faces) or `uv_work` (one plane).

    from indoor_recon.textures import make_texture
    oak = make_texture("wood_planks", task / "textures/oak", [153.7, 127.9, 90.1],
                       plank_width_m=.19, plank_length_m=1.2)
    b.material("oak", **oak["material"](root="task", base=task))
"""
from pathlib import Path

import numpy as np
from PIL import Image

KINDS = ("wood_planks", "wood_grain", "tile", "fabric", "leather", "stone", "plaster")
ROUGHNESS = {"wood_planks": .45, "wood_grain": .5, "tile": .3, "fabric": .9, "leather": .55, "stone": .35, "plaster": .85}


def _to_linear(srgb_255):
    c = np.asarray(srgb_255, float)/255
    return np.where(c <= .04045, c/12.92, ((c+.055)/1.055)**2.4)


def _to_srgb(linear):
    c = np.clip(np.asarray(linear, float), 0, 1)
    return 255*np.where(c <= .0031308, c*12.92, 1.055*c**(1/2.4)-.055)


def _noise(shape, cells, rng):
    """Tileable value noise: a periodic random lattice of cells (rows, columns), smoothly interpolated."""
    h, w = shape
    cy, cx = max(1, int(cells[0])), max(1, int(cells[1]))
    lattice = rng.random((cy, cx))
    y = np.arange(h)*cy/h
    x = np.arange(w)*cx/w
    y0, x0 = np.floor(y).astype(int), np.floor(x).astype(int)
    fy, fx = y-y0, x-x0
    fy, fx = fy*fy*(3-2*fy), fx*fx*(3-2*fx)
    a = lattice[y0[:, None] % cy, x0[None, :] % cx]
    b = lattice[y0[:, None] % cy, (x0[None, :]+1) % cx]
    c = lattice[(y0[:, None]+1) % cy, x0[None, :] % cx]
    d = lattice[(y0[:, None]+1) % cy, (x0[None, :]+1) % cx]
    return (a*(1-fx)+b*fx)*(1-fy[:, None])+(c*(1-fx)+d*fx)*fy[:, None]


def _fbm(shape, cells, rng, octaves=4):
    total, amplitude, norm = np.zeros(shape), 1., 0.
    for i in range(octaves):
        total += amplitude*_noise(shape, (cells[0]*2**i, cells[1]*2**i), rng)
        norm += amplitude
        amplitude /= 2
    return total/norm-.5  # about [-.5, .5], zero mean


def _grain(shape, lines, rng, waviness=.04):
    """Wood grain along image x: long streaks (noise stretched along x) and soft growth bands, gently bent."""
    h, w = shape
    y = np.arange(h)[:, None]/h
    warp = waviness*_fbm(shape, (3, 2), rng, 3)
    bands = np.abs(np.sin(np.pi*(lines*(y+warp)+2*_fbm(shape, (lines, 1), rng, 2))))**3
    streaks = 2*_fbm(shape, (max(1, h//3), 2), rng, 3)
    return .45*(bands-.25)+.55*streaks


def make_texture(kind, stem, colour_srgb_255, pixels_per_m=512, seed=0, contrast=1., roughness=None,
                 bump_mm=.6, **params):
    """Write tileable <stem>_color.png, _roughness.png and _normal.png; return their size and material fields.

    kind: wood_planks (plank_width_m, plank_length_m, joint_m=.002, rows=4, courses=2), wood_grain (size_m=.6,
    grain_lines=14) for veneer and solid wood, tile (tile_m=[w, h], grout_m=.003, grout_srgb_255, columns=2, rows=2),
    fabric (size_m=.2, weave_m=.002), leather (size_m=.3), stone (size_m=.5) for counters and concrete, plaster
    (size_m=1) for paint with a faint roll texture. Image x runs along the texture's u. contrast scales the
    pattern's light-dark variation around the measured colour; roughness overrides the kind's typical value;
    bump_mm is the depth of joints and relief in the normal map (0 for a flat normal)."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    colour = np.asarray(colour_srgb_255, float)
    if colour.shape != (3,) or np.any(colour < 0) or np.any(colour > 255):
        raise ValueError("colour_srgb_255 must be three values in [0, 255]")
    if not 64 <= pixels_per_m <= 4096 or not 0 <= contrast <= 3:
        raise ValueError("pixels_per_m must be 64..4096 and contrast 0..3")
    rng = np.random.default_rng(seed)
    base = _to_linear(colour)

    def canvas(size_m):
        size = np.asarray(size_m, float)*[1, 1]
        shape = (max(8, int(round(size[1]*pixels_per_m))), max(8, int(round(size[0]*pixels_per_m))))
        return size, shape

    tint = np.ones(3)
    gap = None
    if kind == "wood_planks":
        width, length = float(params.pop("plank_width_m")), float(params.pop("plank_length_m"))
        joint, rows, courses = params.pop("joint_m", .002), int(params.pop("rows", 4)), int(params.pop("courses", 2))
        size, shape = canvas([length*courses, width*rows])
        h, w = shape
        row = (np.arange(h)*rows//h)[:, None]
        stagger = rng.random(rows)*length  # each row's end joints at a different offset
        x = ((np.arange(w)[None, :]+.5)/pixels_per_m+stagger[row]) % (length*courses)
        board = row*courses+(x//length).astype(int)
        shade = .16*(rng.random(rows*courses)-.5)
        pattern = shade[board]+.10*_grain(shape, 6*rows, rng)+.05*_fbm(shape, (rows*4, courses*12), rng)
        edge_y = ((np.arange(h)[:, None]+.5) % (h/rows))/pixels_per_m
        edge_x = x % length
        half = max(joint/2, .75/pixels_per_m)  # at least one pixel wide
        gap = (np.minimum(edge_y, width-edge_y) < half) | (np.minimum(edge_x, length-edge_x) < half)
        pattern = np.where(gap, -.45, pattern)
        relief = np.where(gap, -1, .15*pattern)
    elif kind == "wood_grain":
        size, shape = canvas(params.pop("size_m", .6))
        pattern = .12*_grain(shape, params.pop("grain_lines", 14), rng)+.04*_fbm(shape, (6, 6), rng)
    elif kind == "tile":
        tile = np.asarray(params.pop("tile_m"), float)*[1, 1]
        grout, columns, rows = params.pop("grout_m", .003), int(params.pop("columns", 2)), int(params.pop("rows", 2))
        grout_colour = params.pop("grout_srgb_255", None)
        size, shape = canvas([tile[0]*columns, tile[1]*rows])
        h, w = shape
        iy, ix = (np.arange(h)[:, None]+.5)/pixels_per_m, (np.arange(w)[None, :]+.5)/pixels_per_m
        index = (iy//tile[1]).astype(int)*columns+(ix//tile[0]).astype(int)
        shade = .06*(rng.random(rows*columns)-.5)
        pattern = shade[index]+.05*_fbm(shape, (rows*6, columns*6), rng)+.03*_fbm(shape, (h//4, w//4), rng, 1)
        ey, ex = iy % tile[1], ix % tile[0]
        half = max(grout/2, .75/pixels_per_m)
        gap = (np.minimum(ey, tile[1]-ey) < half) | (np.minimum(ex, tile[0]-ex) < half)
        if grout_colour is not None:
            tint = np.where(gap[..., None], _to_linear(grout_colour)/np.maximum(base, 1e-4), 1)
            pattern = np.where(gap, 0, pattern)
        else:
            pattern = np.where(gap, .35, pattern)
        relief = np.where(gap, -1, .2*pattern)
    elif kind == "fabric":
        size, shape = canvas(params.pop("size_m", .2))
        period = max(2, int(round(params.pop("weave_m", .002)*pixels_per_m)))
        h, w = shape
        weave = np.sin(2*np.pi*np.arange(h)[:, None]/period)*np.sin(2*np.pi*np.arange(w)[None, :]/period)
        pattern = .05*weave+.07*_fbm(shape, (8, 8), rng)
    elif kind == "leather":
        size, shape = canvas(params.pop("size_m", .3))
        pebble = _fbm(shape, (shape[0]//6 or 1, shape[1]//6 or 1), rng, 2)
        pattern = .10*np.abs(pebble)-.04+.06*_fbm(shape, (4, 4), rng)
    elif kind == "stone":
        size, shape = canvas(params.pop("size_m", .5))
        speck = _noise(shape, (shape[0]//2 or 1, shape[1]//2 or 1), rng)
        pattern = .08*_fbm(shape, (6, 6), rng)+.25*((speck > .85)*1.-(speck < .12)*1.)
    else:  # plaster
        size, shape = canvas(params.pop("size_m", 1.))
        pattern = .025*_fbm(shape, (16, 16), rng, 5)
    if params:
        raise ValueError(f"Unknown parameters for {kind}: {sorted(params)}")
    if kind not in ("wood_planks", "tile"):
        relief = pattern/max(np.abs(pattern).max(), 1e-6)

    image = base*tint*np.exp(contrast*pattern)[..., None]
    image = image*(base/np.median(image.reshape(-1, 3), 0))  # median matches the measured colour
    rough = np.clip((ROUGHNESS[kind] if roughness is None else roughness)-.15*pattern
                    + (.3*gap if gap is not None else 0), 0, 1)
    # Normal map from the relief as a height field (OpenGL convention, green up); periodic differences tile.
    height = relief*bump_mm/1000
    dx = (np.roll(height, -1, 1)-np.roll(height, 1, 1))*pixels_per_m/2
    dy = (np.roll(height, -1, 0)-np.roll(height, 1, 0))*pixels_per_m/2  # array rows run with v
    normal = np.dstack((-dx, -dy, np.ones_like(height)))
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)

    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    files = {"base_color_texture": stem.with_name(stem.name+"_color.png"),
             "roughness_texture": stem.with_name(stem.name+"_roughness.png"),
             "normal_texture": stem.with_name(stem.name+"_normal.png")}
    # Array rows run with v (up); images store rows top-down, so flip.
    Image.fromarray(np.flipud(_to_srgb(image).round().astype(np.uint8))).save(files["base_color_texture"])
    Image.fromarray(np.flipud((rough*255).round().astype(np.uint8))).save(files["roughness_texture"])
    Image.fromarray(np.flipud(((normal*.5+.5)*255).round().astype(np.uint8))).save(files["normal_texture"])
    size_list = size.round(4).tolist()

    def material(root, base, **extra):
        """Material fields referencing the images under asset root `root`, which is bound to directory `base`."""
        fields = {key: {"root": root, "path": Path(file).resolve().relative_to(Path(base).resolve()).as_posix()}
                  for key, file in files.items()}
        fields.update(base_color_srgb=(colour/255).round(4).tolist(), roughness=float(rough.mean().round(3)),
                      uv_box_m=size_list)
        fields.update(extra)
        return fields
    return {"files": {k: str(v) for k, v in files.items()}, "kind": kind, "size_m": size_list,
            "pixels": [shape[1], shape[0]], "colour_srgb_255": colour.tolist(), "material": material}
