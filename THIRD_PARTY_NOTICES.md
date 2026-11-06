# Licenses and third-party components

The default license for original project files is [Apache-2.0](LICENSE).
Files under `blender/` use [GPL-3.0-or-later](blender/LICENSE), as stated in
[the add-on notice](blender/NOTICE). The Apache-2.0 model contract bundled into
the add-on keeps its own license. The wheel contains core code and the MIT
viewer dependency, so its package metadata says `Apache-2.0 AND MIT`.

| Component | Distribution and license |
| --- | --- |
| three.js | Bundled offline viewer library, r180, including wide-line add-ons from [upstream](https://github.com/mrdoob/three.js/tree/r180/examples/jsm/lines); MIT. Original notice: [vendor license](src/indoor_recon/viewer/static/vendor/three/LICENSE). |
| ScanNet++ case illustration | One screenshot of the authored reconstruction is included in the source documentation. Dataset attribution, provenance and terms are in the [case record](cases/scannetpp-09c1414f1b/README.md); no source dataset, downloadable reconstructed model or textures are included. The repository's Apache-2.0 license does not grant rights over ScanNet++ data or dataset-derived content. |
| NumPy, jsonschema, Pillow | Installed core dependencies; retain each installed distribution's license. They are not vendored into this source release. |
| trimesh, Open3D, SciPy | Optional `geometry` dependencies; retain their upstream licenses. |
| Blender | Separately installed executable; GPL. Not included in the core wheel or source release. [Upstream license](https://www.blender.org/about/license/). |
| SpatialLM source | Optional external backend at commit `8913c44d84a450c53e9340b13317f8cf7144a738`; upstream file-level terms, including Apache-2.0 Pointcept code, remain intact. [Pinned source](https://github.com/manycore-research/SpatialLM/tree/8913c44d84a450c53e9340b13317f8cf7144a738). |
| SpatialLM1.1-Qwen-0.5B weights | Optional, not distributed. The [model card](https://huggingface.co/manycore-research/SpatialLM1.1-Qwen-0.5B) declares CC-BY-NC-4.0. |
| Boxer source | Optional external backend at commit `1f86542dc342a4b1d474c87c97c5d1d6566d9148`; [CC-BY-NC-4.0](https://github.com/facebookresearch/boxer/blob/1f86542dc342a4b1d474c87c97c5d1d6566d9148/LICENSE), with its original NOTICE. |
| Boxer, OWL-V2 and DINOv3 weights | Optional, not distributed. Follow each model's own terms; file identities are in [weights.lock.json](containers/weights.lock.json). |

The core license does not grant rights to optional model weights, datasets or
external backends. In particular, choosing Apache-2.0 for this project does
not remove non-commercial restrictions on the optional perception components.
The source release and basic example require no model download.

The optional full GPU container assembles additional components with different
licenses. Its pinned provenance and notice locations are documented in
[containers/THIRD_PARTY.md](containers/THIRD_PARTY.md). It is not the default
open-source release artifact and is not described as an Apache-only image.
