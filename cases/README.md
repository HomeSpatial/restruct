# Examples and local case records

The current public example is the [synthetic room](../blender/examples/room.json).
It is authored analytical geometry, not a reconstruction of a real home or a
benchmark result. Its provenance and commands are in
[the quick start](../docs/quickstart.md).

Private scans, photographs, camera poses, generated proposals, layouts,
regression snapshots and internal validation reports are not distributed.

The real-scene showcase is the corrected ScanNet++ case. It is a local experiment,
not a bundled dataset or a benchmark evaluation of the release:

| Case | Status | Record |
| --- | --- | --- |
| ScanNet++ `09c1414f1b` | Corrected r14 geometry with r15 PBR, export/reopen checks and four-layer viewer verification | [Case record](scannetpp-09c1414f1b/README.md) |

The previously selected HouseLayout3D / Matterport3D two-level scene is retained
as an [input preparation record](houselayout3d-i5noydFURQK/README.md). Its full
reconstruction has not run and is outside the current showcase scope.

The case folders contain documentation and an individually reviewed screenshot
of the reconstructed ScanNet++ model. Its exact bytes and provenance are listed
under `reviewed_assets` in `release-files.json`; this is not blanket approval for
dataset media. Source scans, photographs, poses, derived GLB/Blender files,
textures and other diagnostic images stay in ignored local `artifacts/`
directories. Dataset access and output redistribution remain subject to their
respective terms; the repository license does not grant those rights.
Before publishing assets, record the dataset/version, source URL, attribution,
redistribution terms, permitted output assets, preprocessing and limitations.
