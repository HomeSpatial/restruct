<a id="输入数据准备colmap-与-rtab-map"></a>

# Prepare input data: COLMAP and RTAB-Map

[English](input-data.md) | [简体中文](input-data.zh-CN.md)

Restruct starts from existing geometry and optional calibrated photographs. This guide uses COLMAP workspaces and RTAB-Map databases to explain how to obtain usable inputs. ScanNet++ remains a [result case study](../cases/scannetpp-09c1414f1b/README.md). No real capture data is included here; replace example input paths with your own files.

<a id="选择入口"></a>

## Choose an entry point

| Data you have | Preparation | Is rtabmap-hloc required? |
| --- | --- | --- |
| COLMAP dense point cloud/mesh with matching photos and cameras | Confirm units, axes and photo correspondence, then run `restruct import-colmap` | No |
| COLMAP photos and sparse model only | Create geometry with COLMAP MVS or another dense reconstruction tool first | No |
| RTAB-Map `.db` with RGB, calibration and valid poses | This guide uses rtabmap-hloc for SfM, then remap for dense geometry | Used separately as an upstream tool |
| Dense geometry already exported from RTAB-Map with matching COLMAP/unified input | Use the COLMAP or reconstruction-description entry point directly | No need to rerun it |

Photo-based object recognition and geometry-projected appearance sampling require photographs, matching intrinsics/extrinsics and a known camera-to-geometry coordinate relationship. Colored geometry alone can support layout and geometry work. Without calibrated photos, do not claim that the photo-evidence workflow is complete. `points3D.bin` alone is not a complete set of room surfaces.

All example paths resolve from the same working directory. Keep raw data under the Git-ignored `private/` directory and task outputs under `artifacts/`. Do not commit real captures, databases or local runtime paths. Use fresh output directories for imports, initialization and reconstruction.

<a id="colmap-路线"></a>

## COLMAP route

<a id="取得并整理数据"></a>

### Obtain and organize data

If you already have a COLMAP workspace, inspect the files below. If you only have photographs, first complete SfM and dense reconstruction using the [official COLMAP tutorial](https://colmap.github.io/tutorial.html). SfM recovers cameras and sparse features; the dense stage produces `fused.ply` or a mesh. Sharp, overlapping photographs covering different room directions help reconstruction, but coverage and surface quality still need review.

Use **undistorted photographs and their corresponding cameras** from the same dense workspace, for example:

```text
private/colmap/
  dense/0/
    images/               # Matches image names and resolutions in this sparse model
    sparse/
      cameras.bin
      images.bin
      points3D.bin
    fused.ply             # Dense colored point cloud
    meshed-poisson.ply    # Optional mesh; both geometry files are not required
```

Do not mix original photographs with undistorted cameras, or combine a different reconstruction component's `sparse/1` with this geometry. Newer COLMAP models may also contain `rigs.bin` and `frames.bin`. The current importer reads the three binary files above and uses the image poses in `images.bin`. Retain the original workspace and check photo projections before using unusual rig data.

For a text-only COLMAP model, use the official `colmap model_converter --output_type BIN` to convert into a new directory and point `--model-prefix` there. The importer supports `SIMPLE_PINHOLE`, `PINHOLE` and `OPENCV`. Other camera models require actual upstream undistortion/conversion; changing only the camera-type name is insufficient. See the [native COLMAP format](https://colmap.github.io/format.html).

<a id="确认尺度和朝向"></a>

### Establish scale and orientation

Purely visual SfM has no automatic metric scale. Establish `metres_per_unit` using measured references or calibrated external coordinates, and confirm the up axis. If alignment or rotation is needed, apply consistent transforms to geometry, cameras and the sparse model. Arbitrary known coordinate relationships can be represented with [transforms in an independent description](exchange-workflow.md). Do not replace unknown scale with `1` just to pass preflight.

The commands below **assume that the input is known to use metres with +Z up**. `--up-axis` and `--metres-per-unit` describe source coordinates. Import does not re-estimate physical scale; the later prepare step converts to working coordinates according to the declarations.

```bash
restruct import-colmap private/colmap --model-prefix dense/0/sparse --images-prefix dense/0/images --geometry-path dense/0/fused.ply --scene-id colmap-room --up-axis +Z --metres-per-unit 1 --out artifacts/colmap-input
restruct inspect artifacts/colmap-input/scene.json
restruct prepare artifacts/colmap-input/scene.json --preflight
```

If you have a mesh, use its path for `--geometry-path`; the importer distinguishes meshes from point clouds by content. With only a point cloud, Boxer uses projected points instead of mesh rays. This fallback currently has only small numerical-fixture validation. Provide a mesh when scan-surface ray evidence is required. To inspect a few frames initially, add `--max-frames N`; it selects registered frames evenly but does not guarantee full room coverage.

After import, inspect `import.json`, `scene.json` and `frames.json`: geometry type, imported frame count, native camera models, image dimensions and the reprojection summary should match expectations. `prepare --preflight` checks inputs without creating a task or establishing scene accuracy.

<a id="创建任务"></a>

### Create a task

Prepare `runtime.local.json` using the [backend guide](backends.md). For initial input inspection, runtime can contain only the configuration needed for that stage. Initialization records missing backends rather than automatically completing prediction.

```bash
restruct init-task artifacts/colmap-input/scene.json --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json --out artifacts/colmap-task
restruct predict artifacts/colmap-task/job --runtime artifacts/colmap-task/runtime.local.json --labels-file profiles/indoor-fixtures-75.json --preflight
```

After preflight passes, remove `--preflight` from the last command to run perception. The task's `startup.json` also records next-step commands and input bindings; use its actual paths and execution status.

<a id="rtab-map-数据库路线"></a>

## RTAB-Map database route

<a id="取得可用的数据库"></a>

### Obtain a usable database

Capture and save a `.db` using RTAB-Map, or use an existing capture. This example requires **RGB images, camera calibration and a valid trajectory**. A lidar-only database without photographs does not satisfy this photo-reconstruction route. First use the official [Database Viewer](https://github.com/introlab/rtabmap/wiki/Tools#database-viewer) to inspect images, trajectory and map availability, and retain the original database as input.

<a id="rtabmap-hloc-是可选上游工具"></a>

### rtabmap-hloc is an optional upstream tool

Restruct does not currently read `.db` files directly. [rtabmap-hloc](https://github.com/HomeSpatial/rtabmap_hloc) decodes the database, exports photographs/cameras and performs sparse reconstruction. [remap](https://github.com/HomeSpatial/remap) can continue from those outputs to dense reconstruction. `restruct` reads exported standard files and `reconstruction.json`/bundles without installing or importing either upstream Python package.

```text
RTAB-Map .db
  → rtabmap-hloc: photos + aligned binary COLMAP + reconstruction.json
  → remap or other dense reconstruction: point cloud/mesh + photos + cameras
  → restruct: input preflight → task preparation → perception → parameter modeling and delivery checks
```

The assets currently published by `rtabmap-hloc` are cameras, photographs and reports, with **no dense geometry**. Its SfM description therefore cannot be passed directly to `restruct init-task`. By default it performs full SfM and recovers RTAB-Map scale through Sim3 alignment, rather than only converting file formats. `--use-existing-poses` selects triangulation from input poses, which is still more than database unpacking.

The upstream distribution is `rtabmap-hloc-reprocess`, with the `rtabmap-hloc` command. Its original code is now licensed under Apache-2.0. Follow each upstream repository's README for access, installation and current licensing, particularly remap's own terms. Install the upstream tools in their respective Linux/WSL Python 3.12 environments. Do not assume similarly named PyPI packages are these tools. They are not default dependencies of this project.

<a id="通过同一个-bundle-交接"></a>

### Handoff through the same bundle

Run commands in their respective tool environments from the same working directory, or use a `--bundle` absolute path that refers to the same directory in every environment. Runtime paths must also belong to the environment executing `restruct`. This example uses the `native` dense route for ordinary photo dimensions:

```bash
rtabmap-hloc private/capture.db --bundle artifacts/rtabmap-house
remap reconstruct --bundle artifacts/rtabmap-house --profile native --preflight
remap reconstruct --bundle artifacts/rtabmap-house --profile native
restruct bundle inspect artifacts/rtabmap-house
restruct init-task --bundle artifacts/rtabmap-house --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json
restruct predict --bundle artifacts/rtabmap-house --preflight
```

Run the next operation only after its preflight succeeds. After perception preflight passes, remove `--preflight` from the last command to run it. Work directories default to `artifacts/rtabmap-house/work/`, and published snapshots live under `stages/`. Fix missing dependencies, missing weights or camera mismatches instead of bypassing preflight. Published stages cannot be overwritten; use another bundle or explicitly choose a new `--stage` for the next experiment.

The explicit `--profile native` produces a dense point cloud. The documented remap default covisibility/RWp profile additionally requires matching 504×378 undistorted photographs, at least six frames and the corresponding CUDA environment. Arbitrary `.db` exports should not be assumed to satisfy those requirements. Follow remap's documentation to prepare and verify them when using the default mesh pipeline. `native --preset smoke` checks execution on a small frame subset and is not a complete room input.

Without bundles, the equivalent independent-description route is:

```bash
rtabmap-hloc private/capture.db -o artifacts/rtabmap-sfm --scene-id rtabmap-room
remap reconstruct artifacts/rtabmap-sfm/reconstruction.json --profile native --preflight
remap reconstruct artifacts/rtabmap-sfm/reconstruction.json --profile native --out artifacts/rtabmap-dense
restruct import-description artifacts/rtabmap-dense/result/reconstruction.json --preflight
restruct init-task artifacts/rtabmap-dense/result/reconstruction.json --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json --out artifacts/rtabmap-task
```

If suitable geometry and calibrated photographs have already been exported from RTAB-Map, organize them into a COLMAP directory or [unified input description](exchange-workflow.md), skipping rtabmap-hloc and unnecessary dense reconstruction. Geometry and cameras must correspond to the same optimized map frame. Exporting a PLY alone does not automatically preserve all photographs and camera relationships.

<a id="输入通过以后"></a>

## After input checks pass

Confirm these points before expensive inference:

- Geometry is nonempty and covers enough room surfaces. Color is required to enable the current SpatialLM adapter.
- Camera records match real image files and dimensions. Update intrinsics after cropping, resizing or undistortion.
- Units, orientation and the camera-to-geometry relationship are supported by evidence, with spot-checked photo projections. Preflight cannot replace these physical checks.
- Backend input requirements, interpreters, source and weights pass preflight. Preserve upstream quality reports and unresolved checks.

Perception outputs are proposals for review. Use scans and photographs to organize and refine the parameter model, then perform appearance sampling, PBR, matched-view review, final export and reopen checks; see the [reconstruction skill (Chinese)](../skills/reconstruct-structured-interiors/SKILL.md). Use `restruct view artifacts/colmap-task --open`, or start the viewer on the bundle's `work/` directory, to inspect the task and later builds.

These commands were checked against core 0.7.7, rtabmap-hloc `f490044` and remap `74e20ed` interfaces. After upstream changes, check `--help` and input preflight again. Interface/format checks do not establish compatibility with arbitrary RTAB-Map databases, complete GPU inference or reconstruction quality across scenes.
