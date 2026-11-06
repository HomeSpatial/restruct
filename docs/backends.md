<a id="spatiallm--boxer-下载配置与验证"></a>

# Download, configure and verify SpatialLM / Boxer

[English](backends.md) | [简体中文](backends.zh-CN.md)

GPU perception is optional. The core CLI, evidence tools, Blender modeling and viewer do not require either model. Start with the default combinations below for a new installation. For existing or newer environments, bind your own source, interpreter and weights and run the same verification steps.

<a id="1-默认组合与官方来源"></a>

## 1. Default combinations and official sources

Machine-readable defaults are in [backends.json](../src/indoor_recon/backends.json); complete container dependencies and hashes are in [locks](../containers/locks). These combinations have been used and validated by this project. They do not imply that upstream supports only these versions, or that GPU containers for the current source have been revalidated.

| Component | SpatialLM | Boxer |
| --- | --- | --- |
| Official source | [manycore-research/SpatialLM](https://github.com/manycore-research/SpatialLM) | [facebookresearch/boxer](https://github.com/facebookresearch/boxer) |
| Default source commit | `8913c44d84a450c53e9340b13317f8cf7144a738` | `1f86542dc342a4b1d474c87c97c5d1d6566d9148` |
| Official model repository | [SpatialLM1.1-Qwen-0.5B](https://huggingface.co/manycore-research/SpatialLM1.1-Qwen-0.5B) | [facebook/boxer](https://huggingface.co/facebook/boxer) |
| Download snapshot revision | `b3ae9ad7a8689bc130a242c7f922a8cc290c1b59` | `bfba91291fb3f137c18eb6e45362eeda827eac7b` |
| Default Python / PyTorch | 3.11 / 2.4.1+cu124 | 3.12 / 2.8.0+cu128 |
| Input | Colored metric point cloud | Calibrated RGB and matching metric geometry |

The current inference adapters require an NVIDIA CUDA GPU with BF16 support. SpatialLM's FlashAttention, torch-scatter and spconv must match the PyTorch/Python/CUDA ABI. Default locks target Linux x86_64; on Windows, run SpatialLM in WSL2 or a GPU container. Boxer can bind a native Windows or Linux environment. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git, and install the core CLI using the [quick start](quickstart.md).

Components and weights retain their licenses. The core's Apache-2.0 license does not override noncommercial model terms; see the [third-party record (Chinese)](../containers/THIRD_PARTY.md).

<a id="2-克隆默认源码"></a>

## 2. Clone the default source

Run from this repository's root. Use new directories to avoid changing existing backend environments:

```bash
git clone https://github.com/manycore-research/SpatialLM.git artifacts/backends/SpatialLM
git -C artifacts/backends/SpatialLM checkout --detach 8913c44d84a450c53e9340b13317f8cf7144a738
git clone https://github.com/facebookresearch/boxer.git artifacts/backends/boxer
git -C artifacts/backends/boxer checkout --detach 1f86542dc342a4b1d474c87c97c5d1d6566d9148
```

For custom source, replace the commit with the desired tag/commit. Fetch the target with `git fetch --tags origin`, then check it out in your separate clone. Ordinary `restruct` execution runs files in `source_dir` without enforcing a Git revision. Verification records the actual commit, working-tree status and file hashes. Other revisions are marked unverified; version names alone cannot establish compatibility.

<a id="3-下载完整模型权重"></a>

## 3. Download complete model weights

These commands use a temporary download-tool environment rather than installing Hugging Face CLI into the model environments. Run each line in PowerShell or a Linux shell. Downloads require network access; subsequent verification and inference use local files.

```bash
uvx --from huggingface-hub==0.36.0 hf download manycore-research/SpatialLM1.1-Qwen-0.5B --revision b3ae9ad7a8689bc130a242c7f922a8cc290c1b59 --local-dir artifacts/weights/spatiallm
uvx --from huggingface-hub==0.36.0 hf download facebook/boxer boxernet_hw960in2x6d768-c88128f8.ckpt dinov3_vits16plus_pretrain_lvd1689m-4057cbaa.pth owlv2-base-patch16-ensemble.pt --revision bfba91291fb3f137c18eb6e45362eeda827eac7b --local-dir artifacts/weights/boxer
```

Download the entire SpatialLM snapshot, including `config.json`, tokenizer, vocabulary, generation settings and `model.safetensors`. The tensor file alone is insufficient for loading. All three Boxer files come from its official model repository and match the [upstream download script](https://github.com/facebookresearch/boxer/blob/1f86542dc342a4b1d474c87c97c5d1d6566d9148/scripts/download_ckpts.sh); there is no need to guess separate OWL-V2 or DINOv3 versions. Repeat the same command to resume an interrupted download. For authentication/access errors, follow the model page and Hugging Face CLI instructions. Keep credentials out of runtime files and Git.

Expected layout:

```text
artifacts/
  backends/SpatialLM/      # Contains inference.py, code_template.txt, spatiallm/
  backends/boxer/          # Contains boxernet/, owl/, loaders/, utils/
  weights/spatiallm/       # Complete Hugging Face model directory
  weights/boxer/
    boxernet_hw960in2x6d768-c88128f8.ckpt
    dinov3_vits16plus_pretrain_lvd1689m-4057cbaa.pth
    owlv2-base-patch16-ensemble.pt
```

Weight sizes and historical full-file hashes are recorded in [weights.lock.json](../containers/weights.lock.json). The `config` check below records selected files' SHA-256 hashes and compares default tensor hashes: the main `model.safetensors` for SpatialLM and all three checkpoints for Boxer. `baseline_tensor_match` only means those tensors match; it does not validate the complete environment or tokenizer. Later levels check loading and interfaces.

<a id="4-选择运行环境"></a>

## 4. Choose a runtime environment

<a id="方案-a构建-gpu-容器"></a>

### Option A: build a GPU container

Configure Docker GPU support using the [container guide (Chinese)](../containers/README.md), then run from the repository root:

```bash
python -B containers/make_context.py --spatiallm-source artifacts/backends/SpatialLM --boxer-source artifacts/backends/boxer --out artifacts/backend-context
docker build --tag restruct:local artifacts/backend-context
```

By default, source archives use the commits recorded in the manifest. For custom revisions, explicitly pass `--spatiallm-ref <commit/tag>` or `--boxer-ref <commit/tag>` and use a new context directory each time. The tool resolves full commits and records archive/dependency-lock hashes. Use `--spatiallm-lock` or `--boxer-lock` for custom dependency locks; otherwise defaults remain. New source may need different Python or system libraries. These are not upgraded automatically; match them in a separate recipe or your own environment.

Mount `artifacts/weights` above as the read-only weights root at runtime. The launcher records each invocation; both `--record` and verification outputs must use new paths:

```bash
python -B containers/run.py --image restruct:local --weights artifacts/weights --jobs artifacts/gpu-jobs --record artifacts/gpu-imports.json -- verify-backends --runtime /opt/recon/containers/runtime.container.json --labels-file /opt/recon/profiles/indoor-fixtures-75.json --level imports --out /jobs/backend-imports
python -B containers/run.py --image restruct:local --weights artifacts/weights --jobs artifacts/gpu-jobs --record artifacts/gpu-smoke.json -- verify-backends --runtime /opt/recon/containers/runtime.container.json --labels-file /opt/recon/profiles/indoor-fixtures-75.json --level smoke --out /jobs/backend-smoke
```

Backends in the container are archived source. Without `.git`, revision status is `unknown`. Use `/opt/resources/sources.json` to associate the build revision and the verification report's file hashes to check actual contents. `unknown` does not prevent model checks.

<a id="方案-b独立-linux--wsl-环境"></a>

### Option B: separate Linux / WSL environments

Run from the repository root in a Linux/WSL shell. Use separate environments for the two models and install the default Linux locks:

```bash
uv venv --python 3.11.15 artifacts/envs/spatiallm
uv pip install --python artifacts/envs/spatiallm/bin/python --require-hashes --index-strategy unsafe-best-match --index https://download.pytorch.org/whl/cu124 -r containers/locks/spatiallm.txt
uv venv --python 3.12.12 artifacts/envs/boxer
uv pip install --python artifacts/envs/boxer/bin/python --require-hashes --index-strategy unsafe-best-match --index https://download.pytorch.org/whl/cu128 -r containers/locks/boxer.txt
```

These commands install dependencies. At runtime, the CLI imports backend source directly from `source_dir`; neither backend needs to be installed into the core environment. `unsafe-best-match` resolves both PyPI and the official PyTorch index while retaining lockfile hash checks. Default locks require Linux x86_64 with glibc 2.35 or newer; do not apply them unchanged on other platforms.

For your own environment, follow the selected upstream source revision's installation instructions and put the actual interpreter in runtime configuration. For native Windows Boxer, consult its [official installation guide](https://github.com/facebookresearch/boxer#installation), add scipy, matplotlib and Pillow used by this adapter, and complete all checks below. This is an external environment, not a package-for-package reproduction of the Linux lock. Do not install Linux extension wheels on Windows.

<a id="5-配置-runtime-与标签"></a>

## 5. Configure runtime bindings and labels

For native Linux, save this JSON as `runtime.local.json` at the repository root. Paths resolve relative to that file. Use the same form when the core CLI itself runs inside WSL.

```json
{
  "spatiallm": {
    "python": "artifacts/envs/spatiallm/bin/python",
    "source_dir": "artifacts/backends/SpatialLM",
    "model_dir": "artifacts/weights/spatiallm"
  },
  "boxer": {
    "python": "artifacts/envs/boxer/bin/python",
    "source_dir": "artifacts/backends/boxer",
    "weights_dir": "artifacts/weights/boxer",
    "labels_file": "profiles/indoor-fixtures-75.json"
  }
}
```

Merge Blender fields from the [Blender configuration](../config/runtime.blender.example.json). If you use only one perception backend, retain only its fields and pass `--backend spatiallm` or `--backend boxer` during verification.

When a Windows core CLI invokes SpatialLM inside WSL, use the following binding. `python` is an absolute path inside WSL; other paths must be readable from Windows and are converted to WSL mount paths. Both views must refer to the same files. A Linux `/home/...` source path cannot be used directly as a Windows path.

```json
{
  "python": "/mnt/e/recon/artifacts/envs/spatiallm/bin/python",
  "wsl_distro": "Ubuntu",
  "source_dir": "E:/recon/artifacts/backends/SpatialLM",
  "model_dir": "E:/recon/artifacts/weights/spatiallm"
}
```

For custom SpatialLM weights, download another complete model directory, change `model_dir`, and select matching source and environment versions. For custom Boxer weights, add `checkpoints` to the `boxer` binding as below. Override only the roles you need; other roles retain the default filenames.

```json
"checkpoints": {
  "boxer": "custom/boxer.ckpt",
  "dinov3": "custom/backbone.pth",
  "owlv2": "custom/detector.pt"
}
```

Relative checkpoint paths resolve under `weights_dir`; absolute paths readable by the core CLI host are also accepted. Renaming changes file bindings only, not architecture or weight format. The adapter uses upstream-required filenames in a task-private cache without rewriting original weights. New modules/APIs or checkpoint formats may require adapter changes.

The Boxer label file has the form `{"labels":["chair","table"]}`. Entries must be unique and belong to the selected OWL package's vocabulary. Use the [75 indoor categories](../profiles/indoor-fixtures-75.json), or override the runtime path with `--labels-file`.

<a id="6-从配置检查到最小推理"></a>

## 6. From configuration checks to minimal inference

Run in the environment containing the core CLI:

```bash
restruct verify-backends --runtime runtime.local.json --level config --out artifacts/check-config
restruct verify-backends --runtime runtime.local.json --level imports --out artifacts/check-imports
restruct verify-backends --runtime runtime.local.json --level load --out artifacts/check-load
restruct verify-backends --runtime runtime.local.json --level smoke --out artifacts/check-smoke
```

| Level | Checks performed | Not covered |
| --- | --- | --- |
| `config` | Paths, required files, label format, source and weight identity | No model imports, GPU startup or API checks |
| `imports` (default) | Configuration plus adapter imports/some signatures, CUDA/BF16 and vocabulary | No weight loading or guarantee of sufficient VRAM |
| `load` | Previous checks plus full model loading and checkpoint tensor matching | No image/point-cloud forward pass |
| `smoke` | Previous checks plus SpatialLM point-cloud encoding and one generated token; an OWL image forward pass and Boxer 3D lifting with one forced 2D box | No real-scene accuracy, complete generation/fusion or large-input VRAM assessment |

You can run `smoke` directly; it includes earlier checks. Use a new output directory each time, with caches stored there too. The default timeout is 600 seconds per backend; increase it with `--timeout 1200` for slow disks or initial kernel startup. Models load one backend at a time, releasing VRAM when each independent process exits.

`verification.json` records level results and hashes, while each backend's `worker.log` contains details. Custom commits or different weights produce unverified notices. Missing files, interface mismatches, unavailable GPUs, vocabulary errors, loading failures or forward-pass failures return nonzero. SpatialLM/Boxer do not silently skip mismatched primary-model tensors. `passed` refers only to the requested verification level; see `stages.*.verified_level` for the level actually reached. No level establishes scene-quality acceptance.

After the minimal checks pass, run `restruct predict` on your own prepared task and inspect real-input results and logs; see [perception and environments](cli.md#perception-and-environments). Changes to source, templates, weights or installed package versions change prediction identity. Start a new job rather than forcing `--resume` onto an old prediction cache.

<a id="常见问题"></a>

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Git ref cannot be resolved | Fetch the target tag/commit; container builds use only locally available Git objects |
| `baseline_match` is `different` / `unknown` | Check actual source and the build manifest; this is a provenance notice, then continue graded verification |
| CUDA / BF16 unavailable | Check the selected interpreter, GPU driver and container/WSL GPU visibility; current adapters have no CPU fallback |
| FlashAttention / scatter / spconv import or kernel failure | Check Python, PyTorch, CUDA and extension-wheel ABI compatibility; first try the default isolated environment |
| Checkpoint missing or incompatible | Check all three role paths, the complete SpatialLM snapshot and architecture; renaming cannot convert architectures |
| Unknown Boxer vocabulary | Use actual vocabulary entries from the selected OWL version |
| Tokenizer/config missing | Download the complete SpatialLM snapshot again |
| Out of VRAM / timeout | Inspect worker.log, close other GPU-consuming processes and increase timeout if appropriate; minimal checks do not characterize large real inputs |
| An old job refuses to resume | Use a new job after identity hashes change; do not edit old state to bypass checks |
