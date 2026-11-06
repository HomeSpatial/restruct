# SPDX-License-Identifier: Apache-2.0
"""Helpers for writing model-v2 parameter documents from measured evidence.

Scene tasks used to re-implement wall, slab, opening and box constructors in every run, with
defaults such as a category of "wall" for every surface. These helpers make the semantic
category and the provenance explicit arguments, derive extrusion offsets from which side of a
measured face the solid lies on, and share boundary nodes so adjoining surfaces stay connected.

    b = ModelBuilder("my-scene", source=b_source)
    b.material("paint", base_color_srgb=[.9, .9, .88], roughness=.8)
    b.wall("arch/wall/north", (0, 4), (5, 4), 0, 2.6, thickness_m=.1, room_side="right", material_id="paint")
    b.opening("arch/door/north", "arch/wall/north", 1.0, 1.9, 0, 2.05, category="door")
    b.slab("arch/floor/main", [(0, 0), (5, 0), (5, 4), (0, 4)], 0, kind="floor", thickness_m=.1)
    b.box("furniture/table/top", "table", center=(2, 2, .74), size_m=(1.2, .8, .03))
    model = b.build()          # validated document; b.write(path) saves it
"""
import json
import math
from pathlib import Path

from .scene_model import IDENTITY, furniture as furniture_parts, staircase as staircase_parts, validate

SOURCE_KINDS = ("measurement", "prediction", "inference", "user", "legacy_record")


def source(kind, root, path, sha256=None, note=None, frame_ids=None, record_id=None):
    """Provenance record; `root`/`path` name the evidence asset (e.g. an evidence.json in the task root)."""
    if kind not in SOURCE_KINDS:
        raise ValueError(f"Unknown source kind {kind!r}")
    record = {"kind": kind, "reference": {"root": root, "path": path}}
    if sha256:
        record["reference"]["sha256"] = sha256
    if note:
        record["note"] = note
    if frame_ids:
        record["frame_ids"] = [str(f) for f in frame_ids]
    if record_id:
        record["record_id"] = record_id
    return record


def pose(center, yaw_degrees=0.):
    """Rigid transform: rotation about work +Z, then translation to center."""
    c, s = math.cos(math.radians(yaw_degrees)), math.sin(math.radians(yaw_degrees))
    x, y, z = (float(v) for v in center)
    return [[c, -s, 0., x], [s, c, 0., y], [0., 0., 1., z], [0., 0., 0., 1.]]


class ModelBuilder:
    def __init__(self, scene_id, source=None, revision=1, boundary_policy="open", precision=6, notes=None):
        self.scene_id, self.revision, self.boundary_policy, self.notes = scene_id, revision, boundary_policy, notes
        self.default_source, self.precision = source, precision
        self.vertices, self.materials, self.objects, self._ids, self._walls = {}, {}, [], set(), {}

    # ------------------------------------------------------------------ basics
    def vertex(self, xyz):
        """Shared node ID for a work-space point; equal coordinates reuse the same node.

        Points equal after rounding to `precision` decimals share a node; the first point's exact
        coordinates are stored, because rounding the stored values bends rotated or lofted quads out
        of plane and fails surface validation."""
        p = [float(v) for v in xyz]
        key = "v/" + ",".join(f"{round(v, self.precision) + 0.:.{self.precision}f}" for v in p)
        self.vertices.setdefault(key, p)
        return key

    def material(self, material_id, **fields):
        self.materials[material_id] = fields
        return material_id

    def add(self, obj):
        """Add a raw object; fills revision and default provenance, rejects duplicate IDs."""
        if obj["id"] in self._ids:
            raise ValueError(f"Duplicate object ID {obj['id']}")
        if not obj.get("category"):
            raise ValueError(f"{obj['id']}: category is required and must say what the object is")
        obj.setdefault("revision", 1)
        if "sources" not in obj:
            if self.default_source is None:
                raise ValueError(f"{obj['id']}: pass source= or set a builder default source")
            obj["sources"] = [self.default_source]
        self._ids.add(obj["id"])
        self.objects.append(obj)
        return obj["id"]

    def _common(self, identity, category, material_id, sources, note, label=None):
        obj = {"id": identity, "category": category}
        if material_id:
            obj["material_id"] = material_id
        if sources:
            obj["sources"] = list(sources) if isinstance(sources, (list, tuple)) else [sources]
        if note:
            obj["review_notes"] = note
        if label:
            obj["label"] = label
        return obj

    # ------------------------------------------------------------------ architecture
    def surface(self, identity, points, category, thickness_m=0., solid_side="front", holes=None, material_id=None,
                sources=None, note=None, role=None):
        """Planar surface through measured points; the solid lies on `solid_side` of that face.

        The outline normal follows the right-hand rule of `points`; "front" extrudes along it,
        "back" against it, "center" straddles the face."""
        offset = {"front": 0., "back": -thickness_m, "center": -thickness_m/2}[solid_side]
        geometry = {"type": "surface", "boundary_ids": [self.vertex(p) for p in points], "thickness_m": float(thickness_m),
                    "offset_m": offset}
        if holes:
            geometry["holes_ids"] = [[self.vertex(p) for p in hole] for hole in holes]
        obj = self._common(identity, category, material_id, sources, note)
        obj["geometry"] = geometry
        if role:
            obj["role"] = role
        return self.add(obj)

    def wall(self, identity, start_xy, end_xy, bottom_z, top_z, thickness_m=.1, room_side="left", category="wall",
             material_id=None, sources=None, note=None):
        """Vertical wall whose measured room-facing face runs start->end; thickness goes away from the room.

        room_side is "left" or "right" looking from start to end (seen from above), or "center" when the
        line is the wall centre. Use category "partition" for interior walls, never for fixture panels."""
        (x0, y0), (x1, y1) = start_xy, end_xy
        points = [(x0, y0, bottom_z), (x1, y1, bottom_z), (x1, y1, top_z), (x0, y0, top_z)]
        # This winding's normal points to the right of start->end, so a room on the left is behind the face.
        side = {"left": "front", "right": "back", "center": "center"}[room_side]
        self._walls[identity] = (start_xy, end_xy)
        return self.surface(identity, points, category, thickness_m, side, material_id=material_id, sources=sources, note=note)

    def slab(self, identity, polygon_xy, z, kind="floor", thickness_m=.1, holes_xy=None, material_id=None,
             sources=None, note=None, category=None):
        """Horizontal floor (measured top face, solid below) or ceiling (measured underside, solid above)."""
        if kind not in ("floor", "ceiling"):
            raise ValueError("kind is floor or ceiling")
        ring = _ccw(polygon_xy)
        holes = [list(reversed(_ccw(h))) for h in holes_xy or []]
        side = "back" if kind == "floor" else "front"
        return self.surface(identity, [(x, y, z) for x, y in ring], category or kind, thickness_m, side,
                            holes=[[(x, y, z) for x, y in h] for h in holes] or None,
                            material_id=material_id, sources=sources, note=note)

    def opening(self, identity, wall_id, start_m, end_m, bottom_z, top_z, category, fill="none", panel_thickness_m=None,
                angle_degrees=None, material_id=None, sources=None, note=None):
        """Door/window opening in a wall made by wall(): distances are along the wall from its start point."""
        if wall_id not in self._walls:
            raise ValueError(f"{wall_id} was not created with wall(); add openings to other hosts with add()")
        (x0, y0), (x1, y1) = self._walls[wall_id]
        length = math.hypot(x1-x0, y1-y0)
        ux, uy = (x1-x0)/length, (y1-y0)/length
        a, b = (x0+ux*start_m, y0+uy*start_m), (x0+ux*end_m, y0+uy*end_m)
        # Lower-left, lower-right, upper-right, upper-left so the panel V axis points up.
        points = [(*a, bottom_z), (*b, bottom_z), (*b, top_z), (*a, top_z)]
        geometry = {"type": "opening", "boundary_ids": [self.vertex(p) for p in points], "fill": fill}
        if panel_thickness_m is not None:
            geometry["panel_thickness_m"] = panel_thickness_m
        if angle_degrees is not None:
            geometry["angle_degrees"] = angle_degrees
        obj = self._common(identity, category, material_id, sources, note)
        obj.update(geometry=geometry, host_id=wall_id)
        return self.add(obj)

    # ------------------------------------------------------------------ objects
    def assembly(self, identity, category, center, yaw_degrees=0., parent_id=None, sources=None, note=None, label=None):
        obj = self._common(identity, category, None, sources, note, label)
        obj.update(geometry={"type": "assembly"}, T_parent_from_object=pose(center, yaw_degrees))
        if parent_id:
            obj["parent_id"] = parent_id
        return self.add(obj)

    def box(self, identity, category, center, size_m, yaw_degrees=0., parent_id=None, material_id=None, sources=None,
            note=None, label=None):
        """Box with full side lengths along its local axes; center/yaw are relative to the parent (or work)."""
        obj = self._common(identity, category, material_id, sources, note, label)
        obj.update(geometry={"type": "box", "size_m": [float(v) for v in size_m]}, T_parent_from_object=pose(center, yaw_degrees))
        if parent_id:
            obj["parent_id"] = parent_id
        return self.add(obj)

    def prism(self, identity, category, boundary_xy, bottom_z, top_z, holes_xy=None, center=(0, 0, 0), yaw_degrees=0.,
              parent_id=None, material_id=None, sources=None, note=None):
        """Vertical extrusion of a local XY outline (counters, L-shaped tops, treads)."""
        obj = self._common(identity, category, material_id, sources, note)
        geometry = {"type": "prism", "boundary_m": [list(map(float, p)) for p in boundary_xy], "bottom_m": float(bottom_z), "top_m": float(top_z)}
        if holes_xy:
            geometry["holes_m"] = [[list(map(float, p)) for p in h] for h in holes_xy]
        obj.update(geometry=geometry, T_parent_from_object=pose(center, yaw_degrees))
        if parent_id:
            obj["parent_id"] = parent_id
        return self.add(obj)

    def cylinder(self, identity, category, center, radius_m, length_m, axis=(0, 0, 1), segments=16, parent_id=None,
                 material_id=None, sources=None, note=None):
        """Round post, leg, tube or drum: a regular-polygon prism centred on `center` along `axis`."""
        ring = [(radius_m*math.cos(2*math.pi*i/segments), radius_m*math.sin(2*math.pi*i/segments)) for i in range(segments)]
        obj = self._common(identity, category, material_id, sources, note)
        obj.update(geometry={"type": "prism", "boundary_m": [list(p) for p in ring], "bottom_m": -length_m/2, "top_m": length_m/2},
                   T_parent_from_object=_axis_pose(center, axis))
        if parent_id:
            obj["parent_id"] = parent_id
        return self.add(obj)

    def furniture(self, identity, template, size_m, center, yaw_degrees=0., material_id=None, source=None):
        """Expand a scene_model template (table/chair/cabinet/shelf/sofa/bed) into editable parts.

        Template part sizes are defaults, not measurements: calibrate the parts that the evidence controls."""
        for obj in furniture_parts(identity, template, size_m, pose(center, yaw_degrees), source or self.default_source, material_id):
            self.add(_without_empty_material(obj))
        return identity

    def staircase(self, identity, steps, center=(0, 0, 0), yaw_degrees=0., material_id=None, source=None):
        for obj in staircase_parts(identity, steps, pose(center, yaw_degrees), source or self.default_source, material_id):
            self.add(_without_empty_material(obj))
        return identity

    # ------------------------------------------------------------------ output
    def build(self):
        model = {"schema_version": 2, "kind": "model", "scene_id": self.scene_id, "coordinate_frame": "work_z_up_metres",
                 "revision": self.revision, "vertices": dict(self.vertices), "materials": dict(self.materials),
                 "objects": json.loads(json.dumps(self.objects)), "boundary_policy": self.boundary_policy}
        if self.notes:
            model["notes"] = self.notes
        for obj in model["objects"]:
            if obj.get("T_parent_from_object") == IDENTITY:
                del obj["T_parent_from_object"]
        return validate(model)

    def write(self, path):
        model = self.build()
        Path(path).write_text(json.dumps(model, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return model


def _axis_pose(center, axis):
    """Rigid transform taking local +Z to `axis` (any direction) and the origin to `center`."""
    z = [float(v) for v in axis]
    n = math.sqrt(sum(v*v for v in z))
    z = [v/n for v in z]
    helper = (1., 0., 0.) if abs(z[0]) < .9 else (0., 1., 0.)
    x = [helper[1]*z[2]-helper[2]*z[1], helper[2]*z[0]-helper[0]*z[2], helper[0]*z[1]-helper[1]*z[0]]
    n = math.sqrt(sum(v*v for v in x))
    x = [v/n for v in x]
    y = [z[1]*x[2]-z[2]*x[1], z[2]*x[0]-z[0]*x[2], z[0]*x[1]-z[1]*x[0]]
    c = [float(v) for v in center]
    return [[x[0], y[0], z[0], c[0]], [x[1], y[1], z[1], c[1]], [x[2], y[2], z[2], c[2]], [0., 0., 0., 1.]]


def _without_empty_material(obj):
    if obj.get("material_id") is None:
        obj.pop("material_id", None)
    return obj


def _ccw(points):
    area = sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, list(points[1:])+[points[0]]))
    return list(points) if area > 0 else list(reversed(points))
