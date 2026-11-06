<a id="输入任务启动与感知-cli"></a>

# Input, task startup and perception CLI

[English](cli.md) | [简体中文](cli.zh-CN.md)

Version 0.7.7 provides new-scene startup, preparation and perception. For `restruct build`, parameter scenes and Blender interfaces, see the [parameter-scene guide](scene-model.md).

`restruct` converts unified input packages into working-coordinate point clouds and proposals. General code contains no scene-specific paths, floors, frame counts or categories. Data is supplied through the [v1 contract](contracts.md), preparation settings and runtime configuration. Replace example paths with actual locations; PowerShell commands can be entered on one line.

When starting from photographs, COLMAP output or an RTAB-Map `.db`, first read [input preparation](input-data.md). These commands do not read RTAB-Map databases directly or treat sparse COLMAP feature points as complete scan surfaces.

<a id="从新扫描创建任务"></a>

## Create a task from a new scan

For an existing unified `scene.json`, run:

```bash
restruct init-task /data/scene/scene.json --runtime /config/runtime.json --out /tasks/scene-001 --root photos=/data/photos
```

This requires geometry extras (`pip install ".[geometry]"`) or the matching Docker image. The command validates input declarations/assets, runs CPU preparation and creates independent `job/prepare`, `startup.json`, `runtime.local.json` and `evidence.json` outputs. Perception and Blender run separately. `--config` uses the prepare settings below; select `geometry_id` there if multiple geometries exist.

The output directory must not exist. Original inputs, old jobs and runtime files are read-only. Partial new outputs remain after failures for diagnosis; after correcting inputs/options, choose a new directory instead of overwriting the old task. Unknown scale/up declarations must be supplied. Geometry already declared as metric work coordinates is not converted or rotated again.

Use runtime configuration that works in the current execution environment. Path fields resolve relative to the original runtime file; bare executable names and Linux/WSL absolute paths remain intact. Containers can use `/opt/recon/containers/runtime.container.json`; host examples are under config. You may configure only the backends needed for the current stage. `missing_runtime` lists backends applicable to the data but not configured. Initialized does not mean prediction, rendering or quality acceptance has occurred.

`startup.json` provides:

- `scene_id`, versions, source-document hash, actual Python/source paths, job and runtime.
- `evidence_roots` / `model_roots` and the `task_root_name` for new parameter provenance. If inputs already use job/task aliases, a new alias preserves the existing references' meaning.
- `available_frame_ids` and colored_layout/calibrated_rgb/scan_rays capabilities. Without a mesh, scan configuration is omitted and mesh-ray queries are unavailable.
- A predict command and prefix argv arrays for evidence/build/render. Callers append query/model/blend, view and fresh output paths to prefixes. Pass arrays directly to interfaces such as subprocess instead of assembling shell quoting manually.
- `STATE.md`, including appearance sampling, PBR, visual comparison, export checks, reopen review and a material-coverage table by default. Whole-house work follows the [appearance workflow (Chinese)](../skills/reconstruct-structured-interiors/references/blender-materials.md#默认外观流程).
- `commands.check_materials_prefix`, carrying model_roots for final and reopened GLB checks. Initialization does not automatically model geometry or complete appearance work.

These paths are local execution bindings. Containers must preserve /inputs and /jobs mappings; rebind explicitly after moving data instead of treating container absolute paths as host paths. Continue existing startup files or edited projects within their original scope. `init-task` is for new scans; public source contains no private-case startup scripts.

<a id="输入和准备"></a>

## Input and preparation

For an existing unified input package:

```bash
restruct prepare /data/scene/scene.json --out /jobs/run-001 --root photos=/data/photos --config prepare.json
```

`bundle` defaults to the directory containing `scene.json`. Inputs may be meshes, point clouds or calibrated RGB-D. Colored geometry does not require photographs. See [DEFAULTS](../src/indoor_recon/preparation.py) for optional `prepare.json` fields and defaults. Example:

```json
{
  "point_source": "auto",
  "voxel_m": 0.02,
  "depth_stride": 2,
  "boxer_stride": 3,
  "sample_points": 500000,
  "floor_cuts_m": [],
  "domain_m": 31.975,
  "overlap_m": 0.5,
  "frame_stride": 1,
  "max_frames": null,
  "alignment": "existing_or_auto"
}
```

`auto` selects RGB-D when calibrated depth exists; otherwise it samples a mesh or reads the point cloud directly. Declared metric `work` geometry keeps its coordinates. Source geometry is converted to metres before alignment; source camera translation is likewise converted before A is applied. Up uses the six declared signed axes. Yaw is estimated using a weighted fourth-angle mean of near-vertical normals, with area weights for meshes; low concentration is flagged for review. Supply `yaw_degrees` or a verified contract alignment if available. Unknown up/scale requires additional input.

Floor boundaries are external settings expressed as working-coordinate Z values, with no fixed floor count or elevations. Floors larger than the model domain are split into overlapping windows, retaining physical scale and nonoverlapping ownership cores. SpatialLM rechecks its domain against actual weight configuration. Decoded partition predictions return to working coordinates. Near-duplicate boxes with the same class/floor and corner Hausdorff distance at most 0.1 m are merged across partitions while retaining sources. Other proposals remain; centers outside the core enter `review.json`. This is conservative deduplication, not stitching truncated walls or recovering topology.

SpatialLM uses colored point clouds by default. Boxer uses per-pixel first-hit raycasting with meshes; for point clouds it uses the closest visible point in the projected grid, recorded as a separate protocol. This fallback has only small numerical-fixture validation. Missing RGB, calibration or valid camera scale skips Boxer. Missing color skips the current SpatialLM adapter, while geometry outputs remain usable.

<a id="colmap-或-zip-导入"></a>

## Import COLMAP or ZIP

```bash
restruct import-colmap /archives/scene.zip --model-prefix scene/colmap/sparse/0 --images-prefix scene/images --geometry-path scene/scans/mesh.ply --out /data/scene-demo --scene-id scene-demo --up-axis +Z --metres-per-unit 1 --max-side 768
restruct prepare /data/scene-demo/scene.json --out /jobs/scene-demo
```

Inputs may be ZIP archives or extracted directories; outputs must be new directories. Import follows registered images and retains nonconsecutive original IDs. `--max-frames` evenly selects registered frames. Supported camera models are `SIMPLE_PINHOLE`, `PINHOLE` and `OPENCV`; others fail explicitly. Write negative up arguments as, for example, `--up-axis=-Y`.

The importer retains selected original photos, three binary calibration files and geometry, recording member CRC/SHA-256. It does not rewrite the source ZIP or read `render_depth` by default when units are unconfirmed. COLMAP poses are inverted to camera-to-source and pixel centers shifted by −0.5. OPENCV radial/tangential distortion is actually resampled; resizing updates K. `import.json` records sparse-point reprojection errors. Calculations follow the [COLMAP format](https://colmap.github.io/format.html) and [camera coordinate documentation](https://github.com/colmap/colmap/blob/main/doc/cameras.rst). The source or external calibration must still establish up and metric scale.

<a id="感知和环境"></a>

## Perception and environments

For installation and version selection, see [backend setup and verification](backends.md). After installation, first run:

```bash
restruct verify-backends --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json --level imports --out artifacts/backend-imports
restruct verify-backends --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json --level smoke --out artifacts/backend-smoke
```

`--level config|imports|load|smoke` increases check depth, defaulting to `imports`. `--backend all|spatiallm|boxer` defaults to both, and `--timeout` defaults to 600 seconds per backend. Use a new `--out` each time. `verification.json` records source/weight hashes, default-version comparisons, actual package versions after successful imports or later checks, reached verification levels and errors. Detailed output is in each backend's `worker.log`. Different source or missing Git metadata produces notices; interface, device, vocabulary or loading errors return nonzero. `smoke` uses synthetic input and cannot replace a complete real-input prediction run.

```bash
restruct predict /jobs/scene-demo --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json
```

See [runtime.example.json](../config/runtime.example.json) for the format, using native Linux paths. Native Windows backends need Windows paths. To invoke WSL from Windows, add `"wsl_distro":"Ubuntu"` to that backend, set `python` to a Linux path, and use host-readable source_dir/model_dir/weights_dir paths; the tool converts mount paths without assembling shell commands.

Install geometry dependencies in the CLI environment. Models use their own interpreters, source checkouts and local weights. Working CUDA/backend dependencies must already be installed; this stage does not install or package them. SpatialLM uses `model_dir`. Boxer can override files through `checkpoints.boxer`, `checkpoints.dinov3` and `checkpoints.owlv2`, otherwise default filenames apply. Relative checkpoint paths resolve under `weights_dir`; other relative paths resolve from the runtime file. Hashes or versions of source (including prompt templates), weights, installed packages, settings, the preparation manifest and adapter code are recorded in `identity.json`. Changed identities cannot reuse an old prediction cache. Optional container dependency locks are under containers/locks.

SpatialLM defaults to `--detect-type arch`, with `all` available. Layout outputs retain raw/decoded text, working-coordinate offsets, token information and VRAM statistics. Boxer explicitly reads label JSON such as `{"labels":["chair","table"]}`, whose entries must belong to the current official vocabulary. It saves per-frame 2D/3D scores, OBBs, accepted indices, CSV and official fusion provenance. CSV uses sequential timestamps while original string frame IDs are retained separately.

Use `--layout none` or `--objects none` to select stages explicitly. `profiles/indoor-68.json` provides an ordered set of 68 indoor categories; `indoor-fixtures-75.json` adds seven bathroom-fixture categories. These development profiles do not guarantee coverage of every indoor object. OWL text and CUDA caches live inside the job. Weights are hard-linked or copied into a private cache and loaded read-only, without adding caches to upstream weight directories.

<a id="输出搬移和恢复"></a>

## Outputs, relocation and resume

```text
job/
  bindings.local.json           # Local asset-root bindings; rebind after moving
  prepare/
    prepared.json              # Protocol, input hashes, A, quality, partitions and frame index
    global.ply, regions/*.ply, points/*.npz
    state.json                 # Stage identity and output hashes
  spatiallm/
    regions/*.json             # Raw/decoded text, offsets, native boxes and run statistics
    raw-predictions.json, proposals.json, review.json
  boxer/
    frames/*.json              # Resumable raw detections per frame
    units.json, accepted-index.json, boxer_3dbbs*.csv
    raw-predictions.json, fused-predictions.json, proposals.json
  perception.json
```

Both proposal manifests retain the v1 `proposals` contract, with source `root: job` bound to the job root. Zero-thickness SpatialLM planes are valid. Invalid geometry retains its raw generation and enters review records. Scores retain native meanings; layout confidence is not invented.

```bash
restruct prepare /moved/input/scene.json --out /moved/job --resume
restruct predict /moved/job --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json --resume
python scripts/check_job.py /moved/job --report /reports/job-check.json
```

Rerunning in the same directory requires `--resume`. Changed inputs, selected frames, settings or recorded code require a new job. Modified existing outputs fail checks; missing unit outputs can be recomputed. Inference saves verification records per frame/partition. Resume rebuilds aggregate files from these records instead of appending to old CSV. Model logs go to `worker.log`, and failures to `failure.json`. Historical failure records remain after a successful retry. Complete stages are reused after hash validation.

After moving data, rerun `prepare --resume` to rebind inputs or pass `--root NAME=PATH` to `predict`. Scene content and options must remain unchanged for reuse. `bindings.local.json` and `request.local.json` are local execution details, not portable data contracts. Resume with the same model environment. Current Docker dependencies are fixed by containers/locks; see the container guide.

<a id="运行记录与过程回放"></a>

## Activity records and process replay

After `evidence`, `build`, `render`, `residuals`, `check-geometry` or `check-materials`, the fresh `--out` directory receives `activity.json`: command, UTC start time, duration, pass/fail, error and major inputs (model/blend/view/query/requests). Failed runs also leave records if the output directory was created. Runs rejected because that directory already existed do not write records or alter prior ones. `restruct view` follow mode uses these records to display recent activity.

<a id="材质导出检查"></a>

### Check exported materials

```bash
restruct check-materials /tasks/scene/model.glb --root task=/tasks/scene --exceptions /tasks/scene/material-exceptions.json --out /tasks/scene/material-check
```

`--exceptions` is optional: a JSON object mapping parameter-material IDs to nonempty reasons, for example `{"glass": "Uniform transparent glass confirmed in photos"}`. Every exported part is checked for color/roughness/normal textures, decodable embedded images, nondegenerate UVs and a metric mapping declaration. Exceptions waive only undeclared textures, never declared textures that are missing or corrupt. Supply every model asset root with `--root` to compare source images with actual GLB pixels; roughness compares the G channel. Without roots, checks cover only export internals and report `source_pixels_checked: false`, which is not a full delivery check.

The fresh output's `materials.json` records model hash, revision, per-part errors and exceptions. Failure returns nonzero. `appearance_quality_passed` is null: the tool does not judge photo/color agreement, so the skill's visual review is still required. `build` supports intermediate geometry revisions; success does not imply appearance delivery has passed.

`python scripts/activity_timeline.py jobs/task --out <new-directory> [--codex-log rollout.jsonl]` arranges tool outputs—evidence, builds, renders, diagnostics, ray comparisons and activity.json timings/failures—into `timeline.json`. With a Codex conversation log, it adds agent commentary and images actually viewed, matching content to disk files and falling back to call paths for resized images. The viewer's `?process=<directory>/timeline.json` replays this timeline: real elapsed time is compressed into fixed-paced steps and commentary becomes captions. With `record=1`, the recording script calls `window.film.seek(t)` frame by frame to export video.

Each step carries `focus_z`, the working-coordinate height of interest: camera height for photos, slice height for sections, and the median changed-object height for new revisions. Replay automatically cuts below a floor slab when the focus lies beneath it. Repeatable `--redact-object <model-object-ID>` flags photos, crops, pixel-ray outputs and residual montages depicting that object as `redacted`. It uses the last model's bounding box expanded by 0.15 m. To tolerate pose error, unoccluded projections within 25% beyond the image boundary also count; montage images with unresolved provenance are always flagged. Repeatable `--redact-frame <frame-ID>` flags a specific photo, such as one showing a person, and images derived from it. Replay omits those images and scan triangles within the object box expanded by another 0.3 m; original files remain unchanged. `cut=highlights` keeps an initial group of evidence photos, the last image review before each revision with that revision and a caption, and a final whole-building summary, lasting roughly 80 seconds.
