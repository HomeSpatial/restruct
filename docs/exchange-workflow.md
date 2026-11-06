<a id="独立重建数据的导入与预检"></a>

# Import and preflight independent reconstruction data

[English](exchange-workflow.md) | [简体中文](exchange-workflow.zh-CN.md)

These commands do not require HomeSpatial or remap installations. `reconstruction_description` and `reconstruction_bundle` remain at version 1. Existing `scene.json` inputs still work, and the tool version is unchanged.

If you do not yet have usable input, see [COLMAP / RTAB-Map input preparation](input-data.md). An `rtabmap-hloc` SfM description contains cameras, photos and reports only; remap or another upstream tool must first add dense geometry. This importer consumes native files and descriptions without importing upstream Python packages.

<a id="从描述文件开始"></a>

## Start from a description

Run `python -m pip install ".[geometry]"` from the source repository root to install Restruct and geometry dependencies; this project is not yet released on PyPI. Input may be a local self-contained description or may bind external assets with `--root NAME=DIRECTORY`. `local/manifest` are reserved document-root names.

```bash
restruct inspect result/reconstruction.json
restruct import-description result/reconstruction.json --preflight
restruct import-description result/reconstruction.json --out imported
restruct prepare imported/scene.json --profile handoff --preflight
restruct prepare imported/scene.json --profile handoff --out job
```

For multiple geometry assets, provide `--geometry-asset ID`. For multiple camera or image assets, provide `--sparse-asset ID` / `--images-asset ID`. Bundle child IDs use `reconstructionID/assetID`; no fixed names such as sfm/rgb/dense are assumed. `--no-photos` explicitly imports geometry alone and cannot be combined with photo/frame selection.

The adapter currently supports PLY point clouds, PLY/GLB meshes and binary COLMAP models with photos. It distinguishes meshes and point clouds by file content and binds photos by COLMAP relative filenames. Inputs must declare right-handed coordinates, an up axis and metric scale. Different camera/geometry coordinate systems require an explicit rigid/similarity relationship. The independent format validator may accept other formats, but this consumer reports them as unsupported.

By default, all declared, matching photos are imported without a fixed three-frame limit or additional frame sampling. Repeat `--frame-id COLMAP_ID` to select an exact subset. Photos are resized to a maximum long side of 768 by default, with matching intrinsics and undistortion updates. Native source photos, camera models, transforms and quality reports are retained. Import does not change source quality to passed; `import.json` preserves the provenance summary.

<a id="坐标采样和点来源"></a>

## Coordinates, sampling and point sources

The default `layout` profile preserves the existing `prepare` analysis behavior: prefer an existing alignment, otherwise estimate working orientation; use a 2 cm voxel and prefer calibrated RGB-D when available. `--profile handoff` uses `alignment=preserve`, requiring +Z-up input and preserving orientation and origin while still converting declared units to metres. Other up axes must be transformed to work +Z using an existing transform or `--alignment auto`.

Options include `--alignment preserve|auto|existing_or_auto`, `--point-source auto|mesh|point_cloud|rgbd`, `--geometry-id` and `--voxel-m`. Precedence is profile → JSON `--config` → explicit CLI options. The 2 cm setting is a starting point for layout analysis, may resample already sampled data and is not an accuracy guarantee. Detailed inspection needs separately validated settings.

`prepare --preflight` does not create a job. It shows assets, point source, frame list, units, up axis, alignment policy, sampling settings and risks. It checks structure and referenced files; the actual automatically estimated matrix is written to `prepared.json` after execution. Preflight does not claim that unexecuted geometry operations or inference have passed quality checks.

<a id="创建任务与模型预检"></a>

## Create a task and preflight models

```bash
# Import and prepare a new description, generating next-step commands
restruct init-task result/reconstruction.json --out task --runtime runtime.local.json --labels-file labels.json
# Or enable layout only for now and explicitly disable object detection
restruct init-task result/reconstruction.json --out layout-task --runtime runtime.local.json --objects none
# Use the task-resolved runtime and preflight every selected stage first
restruct predict task/job --runtime task/runtime.local.json --labels-file labels.json --preflight
restruct predict task/job --runtime task/runtime.local.json --labels-file labels.json
```

`labels.json` has the form `{"labels":["chair","table"]}`. Provide real categories from the current Boxer vocabulary that fit the task. You may also configure `runtime.boxer.labels_file`, resolving relative to the runtime file. Categories are never silently invented. Relative source/weight paths also resolve from the runtime file.

Before any model starts, all enabled stages' data capabilities, interpreters, source, weight files and categories are checked. Once configuration is complete, the target interpreters probe native imports, CUDA/BF16 and the Boxer vocabulary. Each probe has a 45-second limit and neither constructs a model nor downloads weights. Weight contents, model VRAM requirements and prediction behavior are checked during actual execution. Prediction begins only after all selected stages pass preflight. Stages not supported by the input are marked skipped.

`init-task` always retains usable geometry and evidence. If inference configuration is incomplete, `startup.json.commands.predict` is null, `prediction_preflight.errors` lists missing items, and a repeatable `predict_preflight` command is generated. When configuration is complete, the generated predict command includes backend selections and the absolute label-file path.

Every new import/task needs a new directory. `prepare/predict --resume` retains identity and content-hash checks. Summaries distinguish execution, data, quality and next steps. Completing import or prediction does not establish structural or accuracy acceptance.

<a id="维护与验证"></a>

## Maintenance and validation

The independent format's `verify.py`, `exchange.py` and schemas are embedded unchanged into `_exchange` by the reference directory's `vendor.py`, with hashes in `SOURCE.json`. Consumers do not import from HomeSpatial paths. Synchronize reference updates and run `vendor.py --target src/indoor_recon/_exchange --check` to check consistency.

Run regression tests with `python -B -m unittest discover -s tests -v`. Handoff tests cover arbitrary asset IDs, multiple-geometry selection, point-cloud types, exact frame selection, camera Sim3, preflight without outputs and preventing an earlier model from starting when a later selected stage lacks configuration.

<a id="自动追加同一个-bundle"></a>

## Automatically append to one bundle

```bash
restruct init-task --bundle house --runtime runtime.local.json
restruct predict --bundle house
# After interruption: resume the same unpublished stage without rerunning completed models/frames
restruct predict --bundle house --resume
restruct build authored-model.json --bundle house
restruct bundle inspect house
```

Commands automatically select a unique input stage, retain `scene_id` and derive output directories. `--from` selects the input; `--stage` names a new stage. Default stages are prepared, perception and model, and existing stages cannot be overwritten. The initial runtime is saved at `.bundle/runtime.local.json`, so it need not be repeated. Supply a new local configuration after changing hosts. The category file may be specified in runtime's `boxer.labels_file`.

`init-task` imports and prepares. `predict` copies a new working task from the locked prepared snapshot without modifying it. Review/edit proposals into a parameter model before `build`; proposals are not automatically accepted. `build` publishes editable parameters, the native project, GLB and dependencies with explicit Z-up/Y-up transforms. Build success is not geometric acceptance.

`predict --bundle --resume` requires a `prediction-run.json` generated by this version in the working directory. It checks the scene, destination stage, input stage and document hash, runtime configuration and label-file hashes. Unspecified layout, objects and detect-type options retain their prior values. Custom `--stage/--out/--from` must still refer to the original task. Lower layers also check prepared assets, photos, model code, weights and per-frame checkpoints. Changed input/settings require a new stage. Old directories without resume records are not automatically adopted.

Only one process may write a prediction output at a time; the OS releases the lock when the process exits. If snapshot copying was interrupted, resume retains an `exchange-interrupted-*` copy and regenerates it. If a complete snapshot exists but publication failed, resume checks it and retries publication without starting models. Published stages still reject overwrites. Inspect residual publication locks or uncommitted stage directories according to publisher errors rather than blindly deleting them.

Model-worker and task-state JSON checkpoints use unique temporary files and atomic replacement. Windows access/sharing/lock errors are retried at most six times, with at most 1.55 seconds total wait; other errors return immediately. Persistent failures preserve the original checkpoint and temporary file and report their locations. Temporary files are never treated as completed units or published assets. Updating worker/adapter code invalidates old inference fingerprints; old-code results are not silently reused as new-code results.

If only publication failed, use `restruct bundle append house --result result/reconstruction.json --stage <new-id> --from <input-id> --operation <producer>` to publish an existing successful result without rerunning models. This explicit entry point checks format and completeness; the caller must confirm that the result is successful.

Next-step commands generated by `init-task` also retain bundle association. Its layout/objects choices become preparation-stage defaults for predict, and an explicit labels-file is saved in local runtime configuration to avoid reentering it. Predict arguments can explicitly override those selections.
