<a id="m2-参数场景与-blender-接口"></a>

# M2 parameter scenes and Blender interfaces

[English](scene-model.md) | [简体中文](scene-model.zh-CN.md)

Parameters are the source of truth for editing. After scene creation, the current parameters live in the `.blend` file's `hs_scene_model`. `update_scene` updates parameters and geometry together. `rebuild_scene` uses project parameters without rerunning perception or old extractors. Saving produces `.blend`, `.scene.json` and `.project.json` for the native project, portable parameters and local asset bindings/operation records respectively.

The machine definition is [model-v2.schema.json](../src/indoor_recon/schemas/model-v2.schema.json); numerical/relationship rules are in [scene_model.py](../src/indoor_recon/scene_model.py). This standard-library-only module is included unchanged in the add-on, so Blender needs neither NumPy, jsonschema nor a model-inference environment. Existing M0/M1 v1 contracts remain supported.

<a id="参数与坐标"></a>

## Parameters and coordinates

Documents use `schema_version:2, kind:model, coordinate_frame:work_z_up_metres` and contain `scene_id / revision / vertices / materials / objects / boundary_policy`.

| Geometry | Parameters and behavior |
| --- | --- |
| `surface` | Shared `boundary_ids` and optional `holes_ids`; arbitrary plane orientation; `thickness_m` may be zero; `offset_m` starts extrusion along the contour normal, defaulting to `-thickness/2` |
| `opening` | Contour from shared vertices with `host_id` referencing a surface; actually cuts the host; `fill:none/panel`, with configurable panel thickness and opening angle |
| `box` | Full local-axis lengths in `size_m`, suitable for furniture panels, cabinets, frames and bounding volumes |
| `prism` | Local XY contour and holes, with independent `bottom_m / top_m` for each step; supports straight flights, irregular steps and polygonal turns/landings |
| `assembly` | A part tree through other objects' `parent_id`; its root pose moves/rotates the whole group |
| `mesh_reference` | Reads immutable measured/existing assets, with optional node selection, `asset_axes:z_up/gltf_y_up` and positive dimensionless `scale_xyz`; retains curved or unreliable-to-parameterize regions |

Shared vertices use working coordinates. Surfaces/openings have identity poses and do not belong to assemblies. Editing a vertex rebuilds every dependent surface; a host wall also depends on its opening contours. Other objects use right-handed rigid `T_parent_from_object`; without a parent, parent means work. All translations use metres. Parent/child transforms compose by left matrix multiplication. Furniture dimensions follow local axes, not world-axis bounding boxes.

The contour normal follows the complete outer-ring winding using the right-hand rule, including concave polygons. Cycling the first vertex preserves the normal; reversing winding reverses it. The solid extends from `offset_m` to `offset_m + thickness_m` along the normal. A +Z-normal floor can use `offset_m=-thickness_m` to place thickness below its measured top; a +Z-normal ceiling can use `offset_m=0` to place thickness above its measured underside. Check actual exported surfaces, not just parameter vertices. Rebuilding after upgrading from an older implementation that inverted normals at concave corners corrects extrusion direction. Preserve the old project, rebuild a copy and review offsets/winding that may have compensated for that bug.

Door panels rotate around the local V axis through the contour's first point. The first edge defines local U; the perpendicular in-plane direction defines V. Changing the first point can therefore change the hinge axis and local UV basis even when it preserves the normal. Order a door contour lower-left, lower-right, upper-right, upper-left so V points up. Open doorways may touch the host boundary. Disconnected surfaces need separate objects, not another outer contour presented as a hole.

`scene_model.furniture()` expands table/chair/cabinet/shelf/sofa/bed templates into explicit parts with stable IDs, editable individually afterward. `scene_model.staircase()` expands supplied per-step contours/elevations into an assembly and prisms without regularizing or recomputing dimensions. Retain reference meshes and provenance for curved or historically unstable contours.

Every object carries `sources`. Guessed thickness, completed regions and bounding volumes should be marked inference/prediction with `review_notes`, not treated as measurements. Document `revision` tracks whole-model edits; object `revision` tracks direct field edits. Dependency changes such as shared vertices are covered by the document revision.

## CLI

Add `blender.executable` and `blender.addon_dir` to [runtime.example.json](../config/runtime.example.json). The latter points to this repository's `blender/` directory containing `run_agent.py`, or an installed `homespatial_model` directory. See the [project quick start](../README.md#quick-start) for an independent clone and data-free demonstration, and the [add-on guide (Chinese)](../blender/README.md) for packaging, APIs and Blender tests.

```bash
restruct validate /data/model.json
restruct build /data/model.json --runtime runtime.local.json --root job=/jobs/perception --out /jobs/build-001
```

The output directory must be new, preserving existing projects. Scene creation does not clear other scenes, and export includes only this project's model meshes. Supply additional edits through `--requests edits.json`, using the add-on's array of `{op,args}` requests.

To continue a saved project, provide a complete parameter file with the immediately following document revision:

```bash
restruct build /data/model-r2.json --blend /jobs/build-001/model.blend --runtime runtime.local.json --root job=/jobs/perception --out /jobs/build-002
```

Here the saved project must be revision 1 and the new file revision 2. An older file cannot overwrite a newer project. To rebuild from the current project parameters, call `rebuild_scene` instead of reapplying the original JSON.

## Blender JSON/Python API

`homespatial_model.api.dispatch()`, `bpy.ops.homespatial.command()` and the sidebar share the same interface. Treat signatures returned by `describe()` as authoritative:

| Operation | Purpose |
| --- | --- |
| `create_scene(model, roots?, source_model_path?)` | Create an independent scene from v2 parameters |
| `update_scene(objects?, vertices?, create?, delete?, expected_revision?, materials?)` | Deep-merge object fields, edit shared vertices, add/remove objects; replace material specifications by ID |
| `apply_scene(model, expected_revision, roots?)` | Explicitly apply the next complete parameter revision |
| `inspect_scene()` / `inspect_object(id)` | Read current parameters, hierarchy and working-coordinate bounds |
| `capture_transforms(object_ids, expected_revision)` | Record UI rigid transforms and positive box scales back into parameters |
| `rebuild_scene(roots?)` | Rebuild from current parameters, optionally rebinding asset roots |
| `validate_scene()` | Check parameters, edit synchronization, finite coordinates, materials and declared enclosure boundaries |
| `save_scene(filepath, overwrite=false)` / `export_scene(...)` | Save the project/parameters or export standard GLB |

For example, change the local dimensions of a cabinet's left panel:

```python
from homespatial_model import api
current = api.dispatch({"op": "inspect_scene"})["data"]["model"]
part = next(o for o in current["objects"] if o["id"] == "cabinet/left")
size = part["geometry"]["size_m"].copy()
size[0] += 0.01
result = api.dispatch({"op": "update_scene", "args": {
    "expected_revision": current["revision"],
    "objects": [{"id": part["id"], "geometry": {"size_m": size}}]
}})
assert result["ok"], result
```

The API validates the full next parameter set and builds temporary geometry before committing edits. Unrelated objects keep their data blocks. Deleting a host or assembly requires explicitly handling related objects; dangling references are rejected. `expected_revision` prevents stale state from overwriting newer edits. Successful updates increment the document revision.

The sidebar can capture selected transforms, rebuild, save and export. Manual vertex/material edits or unsupported transforms produce `UNRECORDED_EDIT` before rebuild/export, retaining current data and requiring explicit parameter edits. Arbitrary mesh operations are not automatically reverse-engineered. Legacy `set_dimensions/partition_material` still support v1 mesh projects; use parameter operations for v2 so edits do not remain only in meshes.

<a id="材质保存与导出"></a>

## Materials, saving and export

Materials support sRGB color, roughness, metallic, alpha, and asset references for color/roughness/normal maps. `source_mesh` can retain an existing mesh's PBR materials. `uv_work` declares two rows of affine UV mapping in working coordinates, a single-plane projection suitable for floors or one wall. `uv_box_m:[width,height]` instead projects each face by its dominant normal axis: top/bottom faces use u=x, v=y; x-facing faces use u=y, v=z; y-facing faces use u=x, v=z, each divided by the texture's physical dimensions. This avoids stretching furniture faces; choose one mapping method only. `uv_box_space:"object"` uses object-local axes so texture rotates with furniture. `uv_rotate_deg` rotates the pattern from u toward v by the given angle; 90 makes wood grain run along y. Replace a referenced material's entire specification when changing it. Roughness/normal maps use data color space, with OpenGL tangent-space normals.

Saving packs associated images into `.blend`, and export embeds them in GLB. Saved geometry can be reopened, viewed and exported while source assets are offline. Regenerating reference meshes from parameters still needs original assets; after moving them, rebind through `rebuild_scene(roots=...)`.

v2 GLB uses standard Y-up metric coordinates. The manifest and GLB extras explicitly record `T_work_from_gltf`. Extras also contain complete parameters, revisions and stable IDs/parent relationships. The legacy v1 ROS Z-up export retains its behavior; do not mix their coordinate conventions.

`boundary_policy:open` retains open scan boundaries without forced completion. `closed` checks whether reconstructed surfaces marked `role:enclosure` form a closed shell. Topology uses a numerical weld distance appropriate to float32 precision, not a scan-fit tolerance. Review independent solids, shared midsurfaces and thickness overlaps for their intended use; closure does not establish navigability or collision correctness.

See [testing and supported configurations](testing.md) for self-contained checks. Evaluate real-scan accuracy separately on the current inputs.
