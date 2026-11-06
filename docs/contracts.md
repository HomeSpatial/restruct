<a id="v1-数据与坐标契约"></a>

# v1 data and coordinate contracts

[English](contracts.md) | [简体中文](contracts.zh-CN.md)

The M0/M1 v1 machine definition is in [contracts.schema.json](../src/indoor_recon/schemas/contracts.schema.json), with numerical and reference checks in [contracts.py](../src/indoor_recon/contracts.py). M2 adds a separate [model v2](scene-model.md) and [JSON Schema](../src/indoor_recon/schemas/model-v2.schema.json), preserving v1 compatibility. Runtime validation uses packaged schemas without network resolution.

<a id="文档与资产"></a>

## Documents and assets

| kind | Meaning |
| --- | --- |
| `scene` | Scene ID, source coordinates, meshes/point clouds and optional frame/alignment manifests |
| `frames` | Stable frame IDs, image dimensions and optional RGB, depth and calibration |
| `alignment` | Rigid transform from metric source coordinates to working coordinates, with provenance |
| `proposals` | One backend's proposal batch, geometry, native scores and sources |
| `model` | Parameter-model objects, part/host relationships and geometry representations |
| `asset_lock` | Expected file sizes and SHA-256 hashes that cannot be updated automatically |

Every document carries `scene_id`. Object and frame IDs are strings and must be unique within their respective manifests. There are no fixed frame counts, photo numbers, category names or physical building directions. Rerun predictions belong to a new result batch; matching sequence numbers must not overwrite edited objects.

Example asset reference:

```json
{"root": "scan-data", "path": "rgb/42.png", "sha256": "<64-hex-character-digest>"}
```

Root paths are bound through execution arguments; `bundle` defaults to the document directory. `path` uses POSIX relative paths and forbids absolute paths, drive letters, backslashes and `..`. External named roots reference existing large assets without copying private data. Existing model-directory links within a root are allowed; a root binding itself is a trusted location chosen by the executor.

JSON numbers must be finite: NaN and Infinity are rejected. Historical Infinity values representing missed rays are recognized only in case adapters. Original file hashes are retained, and misses are not turned into valid coordinates.

<a id="坐标与单位"></a>

## Coordinates and units

The common geometry working frame is right-handed, Z-up and measured in metres. Source geometry may declare a different up axis and `metres_per_unit`; unknown scale is `null`. Native left-handed input needs an explicit adapter conversion and provenance. A reflection matrix must not be presented as a rigid rotation.

Matrices are stored row by row; mathematical operations multiply column vectors on the left. The rotation in alignment matrix `A = T_work_from_source_m` must be orthogonal with determinant +1, and the last row must be `[0,0,0,1]`. Unit conversion is separate from rotation:

```text
p_source_m = metres_per_unit * p_source
p_work = A * [p_source_m, 1]
T_source_m_from_camera = T_source_from_camera, with translation multiplied by metres_per_unit
T_work_from_camera = A * T_source_m_from_camera
```

Source camera translations use the same units as source geometry. Geometry already declared in `work` coordinates is already metric: do not rescale it or apply A twice. Working-frame geometry can independently support geometry/layout preparation. Projecting source-camera photographs onto it still needs valid scale and alignment.

Input checks verify that a known source up axis becomes work +Z after A. `unknown` remains unresolved rather than being treated as corrected. M1 implements declared-up correction, yaw estimation, configurable floor splits and input-domain partitioning; see the [CLI guide](cli.md).

<a id="图像相机与深度"></a>

## Images, cameras and depth

`frames[*].size` is `[width, height]`. `K` must match the current image resolution and crop. M0 calibration uses an undistorted pinhole camera: optical X points right, Y down and Z forward, with the top-left pixel center at `(0,0)`. Distorted input must be processed by an adapter; changing only the type label is insufficient.

`T_source_from_camera` maps optical camera coordinates to source-map coordinates; do not supply its inverse. Projection uses the inverse transform. World-frame alignment does not change K, whereas resizing or cropping an image requires updating K.

Depth `measurement` is `camera_z`. Its `metres_per_unit` converts depth-file units independently of map-pose units. For example, millimetre PNG depth uses `0.001`, with back-projection `z * inverse(K) * [u,v,1]`. This is not Euclidean distance along a normalized ray.

Photographs may declare an image without a camera, and calibrated depth may omit the image. `scene_id` must agree across documents. Asset/baseline checks separately verify dimensions, file hashes and actual image content; JSON Schema does not read images.

Capability assessment returns `eligible`, `needs_input` or `skipped` to describe data readiness. It does not mean a model has run or that the input source has been empirically validated. M1 execution records define complete run-stage status.

<a id="候选与参数模型"></a>

## Proposals and parameter models

Proposals share `work_z_up_metres` coordinates. An OBB center is its geometric center; `size_m` contains full lengths along the object's local axes, and columns of `R_work_from_object` give those axes in working coordinates.

- `nature: volume` requires all three dimensions to be positive.
- `nature: surface` requires exactly one zero dimension and two positive dimensions, supporting planar SpatialLM proposals such as walls and openings.
- Native scores retain their named values. Do not invent missing SpatialLM confidence or treat scores from different backends as equivalent probabilities.
- `sources` distinguishes measurements, predictions, inferences, user specifications and historical records, referencing original files, record IDs and available frame IDs. Predictions are not measured ground truth.

The minimal parameter representations are arbitrarily oriented `box`, local-XY `polygon_surface` with holes, independent `mesh_reference` and `assembly`. They support sloped walls/ceilings, open scans, individual steps and whole/part relationships without requiring closure or axis alignment. Categories are open strings. Openings and other objects can use `host_id` relationships.

v1 dimension, pose, unique-ID and relationship checks retain their meaning. M2 v2 adds shared vertices, hosted openings, part trees, per-step prisms, materials and edit revisions, with build/save/export validated in real Blender. Schema validity still does not establish agreement with the scan.

<a id="基线语义"></a>

## Baseline semantics

`asset_lock` freezes actual files. `verify-lock` checks missing files, size and SHA-256. A project may also maintain private regression baselines for counts, categories, offsets and selected object measurements.

Validation must not automatically run capture to update expected values. Baselines answer whether known behavior is preserved. Recognition accuracy, physical scan accuracy and cross-scene generalization need separate evaluation. Numerical projection budgets depend on data precision and are not universal scan-geometry tolerances.
