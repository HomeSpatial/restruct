# HouseLayout3D / Matterport3D i5noydFURQK · selected, awaiting raw inputs

Recorded 2026-11-06. The scene was selected from 16 public HouseLayout3D structure
annotations as a compact two-level building with a clear turning staircase.
**A complete reconstruction has not run.** The user has confirmed that authorized
Matterport3D raw data is not available yet. The current GLB is explicitly an
annotation reference, not a reconstruction or perception result.

The current showcase uses the completed ScanNet++ case. This selection is retained
only as preparation for a possible future run; it is not an active deliverable.

## Source and access

[HouseLayout3D dataset card](https://huggingface.co/datasets/houselayout3d/HouseLayout3D)
and [project](https://houselayout3d.github.io/) provide layout annotations based on
Matterport3D. The dataset card declares MIT metadata; this does not supply the
missing Matterport3D mesh and RGB input. Follow the separate
[Matterport3D access instructions](https://niessner.github.io/Matterport/).
The official process requires a signed Terms of Use request from the applicant.
No agreement or email was submitted on the user's behalf.

Matterport3D attribution: Angel Chang, Angela Dai, Thomas Funkhouser, Maciej
Halber, Matthias Nießner, Manolis Savva, Shuran Song, Andy Zeng and Yinda Zhang,
3DV 2017. This source directory contains documentation only; annotation assets
and derived previews remain in ignored local artifacts and are not bundled.

## Selection evidence

Local measurement of the public annotations found:

| Property | Annotation measurement |
| --- | --- |
| Bounding horizontal extent | about 10.5 × 12.1 m |
| Main floor levels | about -3.36 m and +0.03 m |
| Staircase | 2 turning flights; landing about -1.325 m |
| Intermediate horizontal stair entities | 16 treads and 1 landing |
| Openings / evaluation cameras | 13 doors, 17 windows, 1008 cameras |
| Base structure entities | 190 |

These are annotation properties, not measured reconstruction accuracy. Individual
`stairs/*.ply` files are four-vertex sloped envelopes; detailed treads are in the
base structural entities. `poses/*.json` refers to the author's image paths and
does not imply those images are available locally.

Local paths relative to the repository root:

```text
artifacts/case-houselayout3d-selection-20261106/
  candidates.json / candidates.png
  download-manifest.json / selected-entities-manifest.json
  selected-entity-measures.json
artifacts/case-houselayout3d-i5noydFURQK-077-20261106/
  STATE.md / selection.json
  reference/ANNOTATION_REFERENCE_ONLY-i5noydFURQK.glb
  reference/viewer-reference.jpg
```

The reference applies only a documented axis conversion to the public entity
polygons, with inferred preview category labels. It has no recovered furniture
or PBR appearance. In the same viewer service, its separate file-preview URL is:

```text
http://127.0.0.1:8791/?legacy=1#m=case-houselayout3d-i5noydFURQK-077-20261106%2Freference%2FANNOTATION_REFERENCE_ONLY-i5noydFURQK.glb
```

The preview was opened in the browser and its two-level structure checked.
Coordinate metadata was corrected at the glTF root so cutaway/section heights
use the original vertical axis; geometry buffers were not changed.

## Inputs needed for the actual run

Obtain the official scene `i5noydFURQK` packages after access is approved:

1. `matterport_mesh.zip`, including OBJ/MTL and texture images.
2. `undistorted_color_images.zip`.
3. `undistorted_camera_parameters.zip`.
4. Optionally `house_segmentations.zip` for independent level/region checks.

Verify package hashes, units, axis convention, camera intrinsics/extrinsics and
image correspondences before constructing the generic scene/frames input. Keep
the HouseLayout annotations separate as evaluation references; do not feed them
as the scan and then claim accuracy against the same labels.

Once those inputs exist, run skill 0.7.7 in a new task: `init-task` → SpatialLM and
Boxer → scan/photo evidence → architectural structure, openings and per-step
stairs → major furniture → `residuals`/`check-geometry` repair loop → appearance,
PBR and same-camera review → final and reopened `check-materials` → four-layer
viewer registration. All these reconstruction stages remain pending today.
