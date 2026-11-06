<a id="m3-证据查询与渲染"></a>

# M3 evidence queries and rendering

[English](evidence.md) | [简体中文](evidence.zh-CN.md)

`restruct evidence config.json query.json --root NAME=PATH --out new-directory` reads existing prepared data and scans and saves traceable query results. `restruct render` creates a temporary view of a saved project without saving cameras, lights or hidden-object changes into the original project. Both require fresh output directories.

<a id="输入与坐标"></a>

## Inputs and coordinates

Evidence configuration is separate from v1/v2 model documents:

```json
{
  "schema_version": 1,
  "kind": "evidence",
  "scene_id": "my-scene",
  "prepared": {"root": "job", "path": "prepare/prepared.json", "sha256": "actual-sha256"},
  "scan": {
    "asset": {"root": "scan", "path": "scan.ply", "sha256": "actual-sha256"},
    "metres_per_unit": 1,
    "T_work_from_source_m": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
  }
}
```

`scan` is optional. With point clouds alone, ray queries are explicitly unavailable. Scan files use their actual file coordinates, including mesh-node transforms: apply scale first, then the declared rigid transform. The glTF reader does not infer axes for historical ROS files. All queries and outputs use metric Z-up working coordinates. Cameras use the paired K and `T_work_from_camera` in prepared NPZ files; calibration and orientation are not re-estimated.

Outputs must lie outside bound original-asset roots and the prepared directory. Result `evidence.json` records the original query, input SHA-256 hashes, results and interpretation. `files.json` fixes this run's outputs. Where stage state exists, hashes of read NPZ/point-cloud data are checked, along with SHA-256 values declared by assets.

<a id="查询"></a>

## Queries

Each call accepts one JSON object:

| op | Parameters | Output |
| --- | --- | --- |
| `info` | None | Frame count, point source, alignment and mesh-ray capability |
| `photos` | `bounds_work_m: [[minX,minY,minZ],[maxX,maxY,maxZ]]`, `limit?:6`, `frame_ids?:[...]` | Projected crop bounds, scan support counts, camera positions and original-image assets |
| `crop` | `frame_id`, `bounds_work_m` or `crop_xyxy:[x0,y0,x1,y1]`, `margin_px?:12` | Actual `crop.png`, crop offset and `K_crop` |
| `pixel_rays` | `frame_id`, `pixels:[[u,v],...]`, `max_distance_m?` | First scan hits from original-image pixels, plus that frame's `K`, `T_work_from_camera` and `size` |
| `raycast` | `origins_work_m:[[x,y,z],...]`, `directions_work:[[dx,dy,dz],...]`, `max_distance_m?` | Hit points, unit directions, Euclidean distances, normals and triangle IDs; misses are null |
| `section` | `bounds_work_m`, `axis:x/y/z`, `offset_m`, `thickness_m` | Point-cloud slice `section.png` and all selected points in `section.npz` |
| `plane` | `bounds_work_m`, `distance_threshold_m`, `seed?:42` | RANSAC dominant plane, float64 refinement, support/residual statistics and all queried points in NPZ |
| `appearance` | `bounds_work_m`, `normal_work:[nx,ny,nz]`, `cell_m?:0.05`, `max_frames?:120`, `segment_scale?:1`, `min_region_m2?:0.25`, `min_chroma_gap?:0.015`, `normal_tolerance_deg?:25`, `white_reference?:{bounds_work_m, normal_work, colour_srgb_255?:[235,235,232]}`, `lightness_weight?:0`, `seed?:0` | Photo colors per surface cell with frame/channel gain normalization and optional absolute white reference; chromatic material regions/colors, boundary segments, `reliability`, `appearance.png` and `appearance.npz` |

Photos are ranked by prepared first-hit points inside the bounds, then projected area. Projection inside a photo does not establish visibility or correct classification; inspect the crop. Frames are skipped when the AABB crosses the near plane. Shrink the query bounds or specify a pixel crop directly. Crops are not resized; `K_crop` only subtracts the crop origin. Pixel-ray queries still accept original-image pixels.

Appearance queries select scan triangles intersecting the bounds whose normals are within `normal_tolerance_deg` of `normal_work`, sample uniformly by area at `cell_m/2` spacing, and retain samples inside the bounds. After projection into all frames, the `max_frames` with the most in-view samples are selected. A sample contributes a pixel only when a scan ray confirms visibility—hit/sample distance difference at most max(3 cm, 1%)—and the view/surface-normal cosine is at least 0.25. Highlights with any channel ≥250 and dark pixels with any channel ≤4 or mean ≤12 are rejected.

Pixels are converted to linear light and weighted by projected area, cos(view angle)/distance², favoring nearby frontal views. Per-channel log values are alternately solved as sample reflectance plus frame gain, removing exposure and automatic white-balance differences relative to the median frame. Gains are estimated only from target-surface samples. With `white_reference`, reference samples are corrected using those same frame gains, reduced to a weighted median color, and used to scale all channels so the reference becomes `colour_srgb_255` (near-white wall paint by default). Results also record measured reference color and scale factors. Reference and target should share lighting, such as the same room and side of the light source. Ceilings often receive different lighting and are poor floor references.

The surface is gridded along u/v: work x/y for floors, horizontal along-wall/vertical for walls. Cells need at least two observations for segmentation. Segmentation uses colors before white-reference scaling, so its regions do not change with `white_reference`. A 3×3 median filter smooths each cell's (r,b) chromaticity before Felzenszwalb graph segmentation; higher `segment_scale` produces fewer regions. With positive `lightness_weight`, weighted log lightness is added to separate neutral materials differing only in brightness, at the cost of also segmenting shadows. Fragments below `min_region_m2` merge into their most similar neighbor. Adjacent regions with median chromaticity gap below `min_chroma_gap` merge again.

Region color is the weighted median of all corrected observations, with its linear equivalent in `base_colour_linear`. `frame_gain_rgb` records per-frame channel gains. Shared boundary cells between neighboring regions are fitted to a line using principal components, reporting endpoints and straightness. `reliability` determines whether results can be used. Any `blocking` item sets `usable=false`: `no_regions`; `reference_few_observations` below 150 reference observations; `reference_scale_extreme` for a linear channel scale above 4 or below 0.25; or `clipped_after_scale` if more than 5% of cells exceed white after correction. `warnings` include `reference_scale_large` outside 0.5–2, `reference_cast` for a channel-scale ratio above 1.6, and `region_weak` for fewer than three frames or clipping. Each region also reports `frames` and `clipped`. Spatial lighting falloff and differences in light color between regions are not corrected: one material may still look cooler by windows, warmer under lamps or washed out in strong light.

Plane thresholds must be explicit. Statistics describe local dominant-plane support, not automatic wall/floor semantic confirmation. A section is a thick point-cloud slice, not an exact mesh intersection. Preserve bounds, rays and thresholds and reuse the same sampling domain before/after edits.

<a id="临时渲染"></a>

## Temporary rendering

```text
restruct render edited/model.blend --runtime runtime.local.json --view view.json --out render-after
```

A view accepts `object_ids` for framing, `hide_ids`, `isolate` to render only selected objects, `direction_work` from target toward camera, optional `bounds_work_m`, `resolution:[960,720]`, `samples:16`, and `photo_camera:{K, T_work_from_camera, size}`. The photo-camera mode uses the corresponding OpenCV pinhole view with integer pixel centers for pixel-aligned overlays. Obtain its three fields from that frame's `pixel_rays` result. `resolution` must preserve the photo aspect ratio; a neutral fill light is placed at the camera. By default, all current-project meshes are shown with automatic framing. Rendering requires a model without unrecorded edits.

Blender `render_review` uses a temporary scene and model copies, preserving original data blocks, parameters, visibility and active scene. Outputs are a PNG and `.render.json` with revision/model hashes and view settings. The CLI also confirms that the input blend's SHA-256 remains unchanged. Use the same view before and after edits; cutaway hidden objects are recorded. Visual acceptance requires the agent to inspect the PNG and record observations in the scene report.

<a id="独立场景任务"></a>

## Independent scene tasks

`startup.json` binds the local interpreter, source, runtime, evidence configuration, asset roots, input parameters and output directories. A single agent performs evidence review and parameter edits without changing general tool code: one directory and one parameter writer per scene.

Delivery `delivery.json` records at least `scene_id`, `baseline_model`, `baseline_blend`, `baseline_glb`, `edited_model`, `edited_blend`, `edited_glb`, `changed_object_ids`, `evidence_reports`, `render_before`, `render_after` and `report`. Paths are relative to the task directory. The report describes evidence, actual edits, before/after metrics on the same domain and visual observations. Save/reopen and GLB checks still use M2 parameter interfaces; a task-complete message is not artifact acceptance.

Use `scripts/check_m3_delivery.py --startup ... --delivery ... --out /new/check` for independent delivery review: it checks evidence hashes, GLB parameters/coordinates, nontarget geometry/materials and the fixed camera, then rebuilds and re-exports in a fresh Blender process. `scripts/compare_ray_evidence.py` can rerun saved scan-ray queries and compare their exact ray domain with selected objects in two GLBs. Choose an XYZ component or spatial distance along the ray, with misses and common valid domains reported separately. Neither tool runs perception.

Delivery review also checks final/reopened GLB PBR texture coverage, UVs, embedded images and source pixels by default. `material_exceptions` may map material IDs to reasons for uniform-color exceptions such as glass. Only an explicitly user-scoped geometry-only task may use `appearance_scope: "geometry_only"` with `appearance_scope_reason`; otherwise appearance checks cannot be skipped. The script expects an edit with an actual geometry change. For material-only work, use `restruct check-materials` plus independent geometry-preservation/reopen checks. Record appearance-sampling reliability and visual review in `STATE.md`; image existence alone does not establish them.

<a id="同域射线比较"></a>

### Compare rays over the same domain

```text
python scripts/compare_ray_evidence.py --startup startup.json --evidence evidence/query/evidence.json --before baseline/model.glb --after edited/model.glb --object-prefix furniture/sofa/ --component z --report new-review/sofa.json
```

Input GLBs must come from the current model-v2 exporter. The script checks scene_id, coordinates, evidence configuration and input hashes, then reruns the original scan query to verify origins, directions, hits and distances. Outputs must be new and outside source/prepared roots. Perception need not be rerun.

Without selectors, only the full scene is compared. Repeatable `--object-id` requires each ID in both revisions. Repeatable `--object-prefix` selects actual meshes separately in each revision, supporting added/removed parts. An empty target in one revision retains all misses; no matches in either revision is an error. Targeted comparisons also retain full-scene first hits.

JSON `domains.full_scene/target` separately reports common-valid-domain errors, independently paired errors for each revision, misses and coverage gains/losses. Top-level errors retain the original interface, using target if selected, otherwise full_scene. Improvement comparisons use only the common mask; `paired_before/after` may have different valid domains. With no common hits, error is null. `*_full_target_hit_differs` means the same ray hits different objects in the full scene and target, requiring occlusion review rather than automatically identifying an error.

The companion `.rays.npz` stores every ray; scan hits/normals/triangles; both revisions' full-scene and target hit IDs/points/distances/normals/triangles; and common masks. Misses use valid=false, empty ID, distance inf, point/normal NaN and triangle −1. Its hash is recorded in JSON. `passed=true` means comparison execution succeeded, while `geometry_quality_passed=null` means no automatic geometry acceptance. The task determines thresholds and semantic conclusions.

Fixed-view delivery checks allow the model list in non-isolated renders to grow with new objects, while still checking camera, bounds, direction, hide_ids, isolate, resolution, samples and actual visibility. Isolate mode requires identical selections. Render records come from the original tool; do not modify old records to bypass checks.

<a id="通用异常诊断摘要"></a>

### General anomaly summaries

Comparison reports add `anomaly_summary` while preserving command, metric and NPZ compatibility. Each of `domains.full_scene/target` includes four proposal categories:

| Field | Domain and purpose |
| --- | --- |
| `surface_deviation` | All points hit by both scan and new model, sorted by 3D distance; `largest_increases` separately lists error increases on the common valid domain |
| `scan_hit_model_miss` | Scan hit and new-model miss, including persistent omissions; not filtered by the new-model valid mask |
| `scan_miss_model_hit` | Scan miss and new-model hit; may be a scan hole, inferred surface or actual error |
| `hit_identity_change` | Both model revisions hit, but object IDs differ; may reflect renaming, neighboring parts or background changes |

Each category reports the full ray `count`, up to six `examples`, up to six `hit_pair_groups` with up to two examples each, and omitted group counts. Groups use before/after object-hit pairs and report complete spatial bounds for each group. They help locate issues, not perform semantic segmentation or spatial connected-component clustering. Coverage categories without error scores spread examples spatially; deviation categories sort by error. Examples include `ray_index`, origin, direction, scan/before/after points, object IDs and 3D error, with null for misses or incomparable values. Retrieve complete data by indexing the report's NPZ. Spatial bounds prefer scan hits, then new hits, old hits or ray origins.

Both coverage categories also report `before_count / new_count / persistent_count / resolved_count`. Display limits do not truncate counts. Categories can overlap and must not be summed into a failure rate. Summary distances are Euclidean metres and do not replace original `--component` metrics. Zero deviation or tiny floating-point changes can appear. There is no default geometric-acceptance threshold, no change to `passed` and no automatic issue closure. Renames, scan holes and occlusion require human review. Implementation and before/after model hashes are saved with the report.

The tool analyzes existing queries only and cannot establish completeness outside them. Use the skill's source-coverage checklist to find unmodeled objects or unqueried features. Run additional queries on both revisions, keeping them distinct from the original fixed domain. Close issues using that model revision's report and ray locations, not only a global mean or successful execution.

<a id="模型级诊断"></a>

## Model-wide diagnostics

Fixed ray domains answer only the locations they target. These two commands examine the full model and require fresh output directories. Reports bind model SHA-256 and revision; `passed` means execution succeeded and `geometry_quality_passed` is always null.

```text
restruct residuals build-r3/model.glb --startup startup.json --out diag-r3-residuals [--spacing 0.04 --threshold 0.05 --min-area 0.02 --bounds x0 y0 z0 x1 y1 z1 --excess-cameras 160 --excess-margin 0.05]
restruct check-geometry build-r3/model.glb --startup startup.json --out diag-r3-geometry [--tolerance 0.005 --min-overlap 0.0025]
```

`residuals` samples the scan mesh uniformly by area and computes distances to the model. Above-threshold samples are first split by normals into up/down/vertical/slanted groups, then clustered by voxel connectivity within each group. Adjacent surfaces with different orientations, such as countertops, cabinet doors and walls, therefore do not form one mixed cluster. Each cluster reports area, bounds, orientation, distance distribution, nearest model objects and `along_normal`: whether model surfaces exist within 0.3 m on either side along the scan normal, the fraction without them, and the median spacing where they exist. A median distance above 0.25 m yields `hint: unmodelled_region`; otherwise, if more than half the samples have no model surface along the normal, it is `missing_surface_near_model`; the remainder is `offset_surface`.

Clusters also list genuinely visible photographs: projections must be inside the image and scan rays unoccluded. Crop bounds are included. `clusters-*.png` shows one tile per cluster, marking unexplained samples in red on its best photo. `plan.png` is a top-view overview and `residuals.npz` contains all samples. `scan_area_within_m` reports fractions of scan area explained within 2/5/10/20 cm. Only scanned surfaces are analyzed; scan gaps still require a photo survey.

`excess` checks in the reverse direction. It samples model surfaces and casts camera-to-sample rays into the scan using evenly selected frames, 160 by default. If the scan hit lies beyond the model sample by more than `--excess-margin` along the model normal, that camera sees through the modeled surface. A point is excess only when at least three cameras and at least half of its observing cameras see through it. Grazing observations with |cos| <0.25 are ignored; scan misses are unknown. Excess points are likewise clustered by orientation. Each cluster records owner objects, median see-through depth/camera count and visible photos. `by_object_m2` summarizes object areas, and `excess-*.png` marks modeled surfaces on photos.

`plan_fit` compares upward faces of each nonarchitectural object with upward scan faces at the same height, within ±2.5 cm. Both are rasterized at 1 cm, then horizontal offsets within ±15 cm are searched to maximize scan coverage of model cells. Objects enter `shifted` with `best_shift_m` if final coverage is at least 0.8, improvement at least 0.04 and shift at least 2 cm. Coverage remaining below 0.8 enters `poor_fit`, limited to the first 20. Causes may include height, size or shape mismatch, occlusion or clutter on scanned surfaces.

`facing_fit` compares planar orientation for nonarchitectural objects spanning at least 0.25 m, skipping narrow elements such as table legs. The dominant direction of vertical model faces is compared modulo 90° with two references. First are vertical scan samples within a 12 cm expanded object region that are unexplained by architecture or other objects; the strongest ±5° vote window must cover at least 35% of those samples. Second is the largest oriented detection box whose center lies within a 15 cm expanded object region, with at least eight observing frames and a longest side of at least 0.25 m, using `R_work_from_object` from job perception stages. Objects whose area-weighted modulo-90° direction strength—the magnitude of the weighted mean of exp(4iθ)—is below 0.5, such as round or polygonal columns, lack a comparable facing and enter `round`. A difference of at least 8° from either reference enters `turned`, unless at least half the scan samples confirm the model direction, suggesting the detection belongs to a neighboring item. Glass, screens, dark and reflective materials can create scan holes that make cameras appear to see through real surfaces; judge these clusters against photographs.

`check-geometry` reads GLB objects and `parameter_model`:

- `interference`: object pairs where samples on one surface lie inside another closed object deeper than tolerance, with positions/depths. Categories are `object_interference`, `embedded_in_architecture` and `architecture_junction`. Same-object parts are omitted by default, based on a common assembly root or the first two flat-ID segments, such as `furniture/sofa/...`; add `--include-same-assembly` if needed.
- `coplanar_overlap`: same-facing, coplanar, overlapping faces belonging to different objects, with overlap area. Up to eight overlap samples are examined. `open_sample_points` counts samples unobstructed within 0.3 m along the normal after removing ceiling/roof, as seen in cutaways/plans/top views. With startup data, `visible_sample_points` also counts samples visible from captured cameras. Results sort by the larger count. Both counts being zero does not remove the overlap: views exposing those faces after export can still flicker.
- `shell_leaks`, requiring startup data: spherical-direction rays from camera positions that leave without hitting the model, grouped by shell-exit location. `within_opening` lists parameter openings that can explain a group.
- `semantic_warnings`: code-defined category misuse, such as geometry types used as semantic categories, nonvertical or undersized walls, or nonhorizontal floors/ceilings.

Both tools provide proposals and locations only. Edits and issue closure still require task evidence.
