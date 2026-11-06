<a id="重建结果与证据查看器"></a>

# Reconstruction and evidence viewer

[English](viewer.md) | [简体中文](viewer.zh-CN.md)

```bash
restruct view artifacts --port 8770
```

After opening the server URL, tasks containing `prepare/prepared.json` enter the layered workspace. Select a task on the left or use `?task=jobs/task` relative to the server root. If the server root is the task itself, use `?task=.`. Directories without prepared data use the GLB file browser; `?legacy=1` explicitly selects it.

If a task lives outside the served directory, or perception data and model history are stored separately, explicitly register local paths in `viewer-tasks.local.json` under the server root. For example:

```json
{"tasks":[{"id":"@latest-clean","name":"11/06 clean run · Sol high","task_dir":"E:/results/clean/task","models_dir":"E:/results/clean/history"}]}
```

An `id` starts with `@` followed by letters, digits, hyphens or underscores. Paths can be absolute or relative to the configuration file. If omitted, `models_dir` defaults to the task directory. Open `?task=%40latest-clean` to follow that task's live builds while keeping photos/proposals from the registered task. General file access extends only to explicitly registered model directories; URLs cannot name arbitrary external directories. Do not distribute this local configuration in release packages.

<a id="四层对照"></a>

## Compare four layers

| Layer | Contents | Controls |
| --- | --- | --- |
| Scan reference | Original GLB declared by evidence, or the prepared point cloud if no supported GLB exists | Loads on first selection; point previews are limited to 80,000 points and labeled as downsampled; original GLBs retain all meshes |
| SpatialLM layout | Layout proposals such as walls, doors and windows | Visibility, opacity, category/floor filters, selection, partition provenance, generation truncation and review records |
| Boxer objects | Fused object boxes or raw detections from a selected frame | Filter by category, score and source-frame count; inspect matching photos and 2D boxes after selection |
| Structural model | Successful GLB build snapshots | Whole objects and part trees, revision selection, wall cutaways, height clipping, wireframe and focus |

Each layer has independent visibility/opacity controls. The latest structural model is shown by default. Both perception outputs are labeled as proposals awaiting verification. SpatialLM confidence is not invented, and zero-thickness walls remain surfaces. Boxer fusion scores, raw 3D scores and photo-panel 2D/3D scores are displayed separately; none is a geometric-accuracy measure.

Mouse selection hits actual model or proposal-box surfaces; decorative wireframes do not enlarge hit areas. With overlapping layers, the nearest object in the layer associated with the active right-hand tab takes priority. If that layer has no hit, the nearest object in another visible layer is selected. Proposal outlines retain their rotations, while structural highlights follow actual part edges. A fixed three-pixel bright line with a dark backing stays visible when zooming.

Proposals and prepared points use Z-up metric working coordinates. Scans use evidence scale/transforms, and structural GLBs use exported `T_work_from_gltf`. All are converted into the viewer's Y-up metric scene, with a 1 m grid. Proposal floors use saved `floor_id` first; otherwise they are inferred from preparation floor boundaries and object-center heights. Floor labels on cross-floor components are filters only. Scans remain complete and can be clipped by height to inspect interiors.

Structural objects are grouped preferentially by `hs_parent_id` and parameter-model assemblies. Legacy results without parents are grouped by ID path only when direct part categories within the same path agree, and are labeled accordingly. This does not establish a confirmed count of physical objects. Expand parts in object details to distinguish object/named-group counts from mesh-part counts.

<a id="照片与逐帧结果"></a>

## Photographs and per-frame results

After selecting a fused object, choose a source frame to view its photo. 2D boxes are associated strictly through saved `detection_ids`, not guessed from class or approximate location. Legacy results without IDs still show the photo and explain that the 2D box cannot be matched. Structural objects with prediction provenance and frame records can also show source photos.

Switch the Boxer panel to raw per-frame detections to inspect checkpoints before fusion. Only frames recorded in `units.json` with matching hashes are read, and their photos must be declared calibrated task frames. Photos load on demand; missing or changed hashes produce an error.

<a id="版本与进行中的任务"></a>

## Revisions and tasks in progress

The bottom bar groups revision selection and follow-latest controls. The workspace polls every four seconds and pauses polling in hidden tabs. Manually selecting an older revision disables following. Re-enable it to load the newest successful revision while retaining the view. Repeated builds/reopen validations are grouped by revision. Scans and proposals can be inspected before a GLB exists.

Layout partitions, Boxer frame counts and fused proposals have separate status displays. Checkpoints prove that partial results were saved, not that a process is still running; the UI describes saved partial checkpoints rather than claiming active inference. SpatialLM reads only consecutively completed partitions. Boxer fusion results appear only after the full stage is committed.

The backend validates prepared-data identity and committed-result hashes, rejecting proposals from different preparation snapshots or modified files. Outside the server root it reads only scans/photos explicitly declared by the task within bound roots, and model directories explicitly registered in local configuration. Generic file URLs stay inside the server root or the matching registered directory. The default listener is `127.0.0.1`. Viewing neither modifies tasks nor retriggers inference.

<a id="兼容入口"></a>

## Compatibility entry points

- `?legacy=1`: original GLB file list, drag-and-drop and second-model comparison.
- `?follow=.`: task workspace when prepared data exists; original two-second build following for model-only directories.
- `?legacy=1&follow=.`: original build following and change fades.
- `?replay=.&hold=4`: original sequential revision replay.
- `?process=path/to/timeline.json`: original evidence/commentary process replay.

Collapse either side panel with the top buttons. Drag the divider to resize, double-click it to reset, or use left/right arrows when it has focus. Double-click a selected object/proposal or press F to focus; Esc clears selection. Resource cleanup has improved, but no GPU performance gain is promised or measured.
