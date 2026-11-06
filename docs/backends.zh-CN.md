# SpatialLM / Boxer 下载、配置与验证

[English](backends.md) | [简体中文](backends.zh-CN.md)

GPU 感知是可选阶段。核心 CLI、证据工具、Blender 建模和查看器不要求安装这两个模型。首次安装建议使用下面的默认组合；已有环境或需要新版时，可绑定自己的源码、解释器与权重，按相同验证步骤检查。

## 1. 默认组合与官方来源

机器可读默认值在 [backends.json](../src/indoor_recon/backends.json)，完整容器依赖及哈希在 [locks](../containers/locks)。这些是本项目已使用、验证过的组合；不表示上游只能运行这些版本，也不表示当前源码的 GPU 容器已经重新验收。

| 项目 | SpatialLM | Boxer |
| --- | --- | --- |
| 官方源码 | [manycore-research/SpatialLM](https://github.com/manycore-research/SpatialLM) | [facebookresearch/boxer](https://github.com/facebookresearch/boxer) |
| 默认源码 commit | `8913c44d84a450c53e9340b13317f8cf7144a738` | `1f86542dc342a4b1d474c87c97c5d1d6566d9148` |
| 官方模型仓库 | [SpatialLM1.1-Qwen-0.5B](https://huggingface.co/manycore-research/SpatialLM1.1-Qwen-0.5B) | [facebook/boxer](https://huggingface.co/facebook/boxer) |
| 下载快照 revision | `b3ae9ad7a8689bc130a242c7f922a8cc290c1b59` | `bfba91291fb3f137c18eb6e45362eeda827eac7b` |
| 默认 Python / PyTorch | 3.11 / 2.4.1+cu124 | 3.12 / 2.8.0+cu128 |
| 输入 | 有颜色、米制点云 | 标定 RGB 和对应米制几何 |

当前推理适配器要求支持 BF16 的 NVIDIA CUDA GPU。SpatialLM 的 FlashAttention、torch-scatter、spconv 与 PyTorch/Python/CUDA ABI 必须匹配；默认锁面向 Linux x86_64，Windows 上建议在 WSL 2 或 GPU 容器中运行 SpatialLM。Boxer 可绑定 Windows 原生或 Linux 环境。安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Git，核心 CLI 按 [快速入门](quickstart.zh-CN.md) 安装。

各组件和权重保留原许可；核心 Apache-2.0 不覆盖模型的非商业条款，见 [第三方记录](../containers/THIRD_PARTY.md)。

## 2. 克隆默认源码

从本项目仓库根目录执行。以下全部用新目录，避免改动已有后端环境：

```bash
git clone https://github.com/manycore-research/SpatialLM.git artifacts/backends/SpatialLM
git -C artifacts/backends/SpatialLM checkout --detach 8913c44d84a450c53e9340b13317f8cf7144a738
git clone https://github.com/facebookresearch/boxer.git artifacts/backends/boxer
git -C artifacts/backends/boxer checkout --detach 1f86542dc342a4b1d474c87c97c5d1d6566d9148
```

自选源码时，把 commit 换成需要的 tag/commit。先 `git fetch --tags origin` 获取目标，再在自己的独立克隆中 checkout。普通 `restruct` 直接运行 `source_dir` 中的文件，不强制 Git 版本；验证报告记录实际 commit、工作树状态和文件哈希。其他版本标为未验证，不能仅凭版本号判断兼容。

## 3. 下载完整模型权重

下面使用临时下载工具环境，不把 Hugging Face CLI 装进模型环境；命令可在 PowerShell 或 Linux shell 中逐行执行。下载需要网络，之后的验证与推理使用本地文件。

```bash
uvx --from huggingface-hub==0.36.0 hf download manycore-research/SpatialLM1.1-Qwen-0.5B --revision b3ae9ad7a8689bc130a242c7f922a8cc290c1b59 --local-dir artifacts/weights/spatiallm
uvx --from huggingface-hub==0.36.0 hf download facebook/boxer boxernet_hw960in2x6d768-c88128f8.ckpt dinov3_vits16plus_pretrain_lvd1689m-4057cbaa.pth owlv2-base-patch16-ensemble.pt --revision bfba91291fb3f137c18eb6e45362eeda827eac7b --local-dir artifacts/weights/boxer
```

SpatialLM 下载整个快照，包含 `config.json`、tokenizer、词表、生成配置和 `model.safetensors`。只下载张量文件不能完成模型加载。Boxer 的三份文件均来自其官方模型仓库，文件名与 [上游下载脚本](https://github.com/facebookresearch/boxer/blob/1f86542dc342a4b1d474c87c97c5d1d6566d9148/scripts/download_ckpts.sh) 一致；无需分别猜测 OWL-V2、DINOv3 的其他版本。下载中断可重复同一命令恢复；遇到认证/授权错误，按模型页面和 Hugging Face CLI 提示完成，凭据不要写入 runtime 或仓库。

预期目录：

```text
artifacts/
  backends/SpatialLM/      # 包含 inference.py、code_template.txt、spatiallm/
  backends/boxer/          # 包含 boxernet/、owl/、loaders/、utils/
  weights/spatiallm/       # 完整 Hugging Face 模型目录
  weights/boxer/
    boxernet_hw960in2x6d768-c88128f8.ckpt
    dinov3_vits16plus_pretrain_lvd1689m-4057cbaa.pth
    owlv2-base-patch16-ensemble.pt
```

权重大小和历史完整文件哈希在 [weights.lock.json](../containers/weights.lock.json)。下文 `config` 检查记录所选模型文件的 SHA-256，并比较默认张量哈希；SpatialLM 比较主 `model.safetensors`，Boxer 比较全部三份 checkpoint。`baseline_tensor_match` 仅表示这些张量匹配，不等于整个环境或 tokenizer 已验收；加载与接口检查见后续级别。

## 4. 选择运行环境

### 方案 A：构建 GPU 容器

按 [容器说明](../containers/README.md) 配置 Docker GPU 支持，在本项目根目录执行：

```bash
python -B containers/make_context.py --spatiallm-source artifacts/backends/SpatialLM --boxer-source artifacts/backends/boxer --out artifacts/backend-context
docker build --tag restruct:local artifacts/backend-context
```

源码默认归档清单中的 commit。自选版本显式添加 `--spatiallm-ref <commit/tag>` 或 `--boxer-ref <commit/tag>`，每次使用新的上下文目录；工具解析到完整 commit 并记录归档和依赖锁哈希。自选依赖锁可用 `--spatiallm-lock` / `--boxer-lock`，不传则保留默认依赖。新源码可能要求其他 Python/系统库，这些不会自动升级，需在独立配方或自有环境中匹配。

运行时将上面的 `artifacts/weights` 作为只读权重根挂载。启动器生成记录，`--record` 和验证输出都须是新路径：

```bash
python -B containers/run.py --image restruct:local --weights artifacts/weights --jobs artifacts/gpu-jobs --record artifacts/gpu-imports.json -- verify-backends --runtime /opt/recon/containers/runtime.container.json --labels-file /opt/recon/profiles/indoor-fixtures-75.json --level imports --out /jobs/backend-imports
python -B containers/run.py --image restruct:local --weights artifacts/weights --jobs artifacts/gpu-jobs --record artifacts/gpu-smoke.json -- verify-backends --runtime /opt/recon/containers/runtime.container.json --labels-file /opt/recon/profiles/indoor-fixtures-75.json --level smoke --out /jobs/backend-smoke
```

容器中的后端是归档源码，没有 `.git` 时版本状态为 `unknown`，以 `/opt/resources/sources.json` 关联构建版本，以验证报告的文件哈希核对实际内容。提示 `unknown` 不阻止模型检查。

### 方案 B：独立 Linux / WSL 环境

在 Linux/WSL shell 中，从本项目根目录执行。两个模型使用分开的环境，安装默认 Linux 锁：

```bash
uv venv --python 3.11.15 artifacts/envs/spatiallm
uv pip install --python artifacts/envs/spatiallm/bin/python --require-hashes --index-strategy unsafe-best-match --index https://download.pytorch.org/whl/cu124 -r containers/locks/spatiallm.txt
uv venv --python 3.12.12 artifacts/envs/boxer
uv pip install --python artifacts/envs/boxer/bin/python --require-hashes --index-strategy unsafe-best-match --index https://download.pytorch.org/whl/cu128 -r containers/locks/boxer.txt
```

这些命令安装依赖，CLI 运行时直接从 `source_dir` 导入后端源码。无需把两份后端装进核心环境。`unsafe-best-match` 用于同时解析 PyPI 与官方 PyTorch 索引，安装仍校验锁中哈希。默认锁要求 Linux x86_64、glibc 2.35 或更新，其他平台不能直接套用。

使用自有环境时，按所选源码版本的官方安装说明匹配依赖，再将实际解释器写进 runtime。Windows 原生 Boxer 可参考 [官方安装](https://github.com/facebookresearch/boxer#installation)，另装本适配器使用的 scipy、matplotlib、Pillow，并完成下面所有验证；它是外部环境路径，不等于 Linux 锁的逐包复现。不要把 Linux 的扩展 wheel 安装到 Windows。

## 5. 配置 runtime 与标签

Linux 原生运行示例：将以下 JSON 保存为仓库根目录的 `runtime.local.json`；路径相对这个配置文件解析。若从 WSL 中执行核心 CLI，也使用此格式。

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

Blender 字段可合并自 [Blender 配置](../config/runtime.blender.example.json)。只使用一个感知后端时可以只保留对应字段，验证加 `--backend spatiallm` 或 `--backend boxer`。

Windows 核心 CLI 调用 WSL 的 SpatialLM 时，该绑定改用以下形式：`python` 是 WSL 内绝对路径，其余路径须可由 Windows 读取，CLI 将其转换为 WSL 挂载路径。路径必须是相同文件的两端视图，不能把 Linux `/home/...` 源码路径直接当 Windows 路径。

```json
{
  "python": "/mnt/e/recon/artifacts/envs/spatiallm/bin/python",
  "wsl_distro": "Ubuntu",
  "source_dir": "E:/recon/artifacts/backends/SpatialLM",
  "model_dir": "E:/recon/artifacts/weights/spatiallm"
}
```

自选 SpatialLM 权重：下载另一模型完整目录，修改 `model_dir`，选择匹配源码与环境。自选 Boxer 权重：在 `boxer` 绑定内增加如下 `checkpoints`；只覆盖需更换的角色即可，其他角色仍用默认文件名：

```json
"checkpoints": {
  "boxer": "custom/boxer.ckpt",
  "dinov3": "custom/backbone.pth",
  "owlv2": "custom/detector.pt"
}
```

相对 checkpoint 路径以 `weights_dir` 为基准，也接受核心 CLI 主机可读的绝对路径。改名只改变文件绑定，不会转换模型架构或权重格式。适配器在每次任务的私有缓存中使用上游所需文件名，不改写原权重；新版若修改模块/API 或 checkpoint 格式，需要更新适配器。

Boxer 标签文件格式为 `{"labels":["chair","table"]}`，条目须唯一且属于所选 OWL 包的词表。可使用 [室内 75 类](../profiles/indoor-fixtures-75.json)，也可通过 `--labels-file` 覆盖 runtime 中的路径。

## 6. 从配置检查到最小推理

在安装核心 CLI 的环境中执行：

```bash
restruct verify-backends --runtime runtime.local.json --level config --out artifacts/check-config
restruct verify-backends --runtime runtime.local.json --level imports --out artifacts/check-imports
restruct verify-backends --runtime runtime.local.json --level load --out artifacts/check-load
restruct verify-backends --runtime runtime.local.json --level smoke --out artifacts/check-smoke
```

| 级别 | 实际检查 | 未覆盖 |
| --- | --- | --- |
| `config` | 路径、所需文件、标签格式、源码与权重身份 | 不导入模型、启动 GPU 或测试 API |
| `imports`（默认） | 配置，加当前适配器依赖的导入/部分签名、CUDA/BF16、词表 | 不加载权重，不保证显存足够 |
| `load` | 以上检查，加完整模型加载、checkpoint 张量匹配 | 不执行图像/点云前向 |
| `smoke` | 以上检查，加 SpatialLM 点云编码与一个生成 token；OWL 图像前向及强制一个 2D 框的 Boxer 3D 提升 | 不评估真实场景精度、完整生成/融合或大输入显存 |

可以直接运行 `smoke`，它包含前面的检查。每次使用新输出目录，缓存也放在该目录；默认每个后端超时 600 秒，慢盘/首次内核初始化可增加 `--timeout 1200`。模型按后端顺序加载，各自独立进程退出后释放显存。

`verification.json` 保存分级结果与哈希，后端子目录的 `worker.log` 保留细节。自选 commit 或不同权重只产生未验证提醒；文件缺失、接口不匹配、GPU 不可用、词表错误、权重加载失败或前向失败会返回非零退出码。SpatialLM/Boxer 不允许静默跳过不匹配的主模型张量。报告中的 `passed` 只针对本次请求级别，实际达到的级别看 `stages.*.verified_level`；任何级别都不是场景质量验收。

最小验证通过后，使用自己的已准备任务运行 `restruct predict`，核查真实输入的输出与日志，参见 [感知和环境](cli.zh-CN.md#感知和环境)。源码、模板、权重或环境包版本变化会改变预测身份；请用新 job 重新运行，不能用 `--resume` 强行接续旧版本的预测缓存。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| Git ref 无法解析 | 获取目标 tag/commit；容器构建只使用本地已存在的 Git 对象 |
| `baseline_match` 为 `different` / `unknown` | 核对实际源码和构建清单；这是来源提醒，继续运行分级验证 |
| CUDA / BF16 不可用 | 核对所选解释器、GPU 驱动及容器/WSL GPU 可见性；当前适配器没有 CPU 回退 |
| FlashAttention / scatter / spconv 导入或内核失败 | 对照 Python、PyTorch、CUDA、扩展 wheel ABI；优先回到默认独立环境 |
| checkpoint 缺失或加载不匹配 | 检查三角色路径、完整 SpatialLM 快照及架构；不要靠重命名跨架构转换 |
| Unknown Boxer vocabulary | 修改标签为该版本 OWL 的实际词表条目 |
| 缺少 tokenizer/config | 重新下载 SpatialLM 完整快照 |
| 显存不足 / 超时 | 查看对应 worker.log；关闭其他占用进程，按需增加 timeout；最小验证不代表真实大输入内存开销 |
| 旧任务拒绝恢复 | 身份哈希变化后使用新 job；不要修改旧状态文件绕过检查 |
