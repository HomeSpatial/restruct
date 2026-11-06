# 可选 GPU 容器（自行构建）

这套配方包含 SpatialLM、Boxer 和 Blender，不属于默认核心 wheel 的依赖。
**没有已发布的官方公共镜像。** 下方 `homespatial/restruct:local`
仅是可自行选择的本地构建标签，不表示本次 0.7.7 源码已完成 GPU 镜像验收。
新构建应自定义本地标签，并执行运行环境与实际输入验证。

后端的非商业限制及权重条款不因项目核心采用 Apache-2.0 而改变；参见
[第三方许可](THIRD_PARTY.md)。权重使用只读挂载，不放进源码包。
当前 make_context 先检查公开文件清单，不复制私有案例或 Git 历史。


在 Windows PowerShell 调用 Docker Desktop 即可构建和运行；镜像中的系统和程序仍为 Linux。Windows 使用 Docker Desktop 的 WSL 2 Linux 后端及 NVIDIA GPU 支持，无需在 Ubuntu 发行版内另装 Docker 引擎。Linux 主机使用 Docker Engine 和 NVIDIA Container Toolkit。宿主要求见 [Docker GPU 文档](https://docs.docker.com/desktop/features/gpu/)和 [NVIDIA 安装说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。

## 构建

尚未下载源码或权重时，先完成 [后端安装指南](../docs/backends.md)。

从本仓库根目录执行，克隆目录可任意命名。宿主 Python 只需标准库；构建不使用宿主的模型 Python 环境。

```powershell
python -B containers/make_context.py --spatiallm-source /path/to/SpatialLM --boxer-source /path/to/boxer --out artifacts/docker-context
docker build --progress plain --tag homespatial/restruct:local artifacts/docker-context
```

`make_context.py` 默认从脚本所在仓库读取核心及 `blender/` 插件，不访问同级工具项目；可用 `--repo /path/to/checkout` 显式选择另一份仓库。两个后端 Git 源码通过参数绑定，工作树须干净。默认归档 [后端清单](../src/indoor_recon/backends.json) 的已验证 commit，不取当前 HEAD；也可用 `--spatiallm-ref <commit/tag>`、`--boxer-ref <commit/tag>` 选择本地 Git 可解析的其他版本。不会切换或修改原 checkout。

自选版本仅标为未验证，不按 commit 拒绝；`resources/sources.json` 保存请求 ref、解析 commit、归档哈希、依赖锁哈希和许可文件清单。选其他依赖可传 `--spatiallm-lock /path/to/requirements.txt`、`--boxer-lock ...`，锁须自包含并带完整包哈希，安装仍强制 `--require-hashes`。默认 Python/CUDA 镜像和扩展 wheel ABI 不随源码 ref 自动变化，需要不同 Python/系统环境时使用独立环境或单独维护配方。

构建上下文只含代码、契约、测试和安装资源；不复制扫描、照片、权重或历史 job。Blender 从官方站下载，核对官方 SHA-256；两个后端通过 `git archive` 保留完整源码和许可文件。单独分发构建上下文时，将其整个目录交给 Docker 即可。

固定项包括基础镜像 digest、uv、Python 3.11.15/3.12.12、Blender 5.1.2、后端 Git commit、带哈希的完整 Python 依赖及 Ubuntu APT 快照 `20261023T000000Z`。核心、SpatialLM 和 Boxer 使用三个独立环境，避免 NumPy/PyTorch/CUDA ABI 冲突。FlashAttention 和 torch-scatter 使用锁中指定的预编译 wheel；运行验收实际执行 CUDA 扩展内核。系统包清单位于镜像 `/opt/release/dpkg-packages.txt`。

## 新任务入口

0.6 镜像包含 `restruct init-task`，可用已安装的运行配置生成完整的新扫描任务绑定：

```text
restruct init-task /inputs/scene/scene.json --runtime /opt/recon/containers/runtime.container.json --out /jobs/task-001
```

输入仍只读，新的 prepare 及配置写入 /jobs/task-001。后续从 startup.json 读取 predict 和 evidence/build/render 命令，在同样的容器映射下执行。宿主调用仍使用下面 run.py，权重根和原始输入按需显式绑定。

## 运行

权重目录须包含 `spatiallm/` 和 `boxer/`，文件清单与 SHA-256 见 [weights.lock.json](weights.lock.json)。权重单独提供并只读挂载。SpatialLM 权重和 Boxer 有非商业许可限制，许可来源见 [第三方记录](THIRD_PARTY.md)。

下面的宿主启动器可在 Windows/Linux 使用，解析镜像到不可变 ID，并记录命令、耗时、日志和容器资源采样。所有原始输入和权重只读，网络关闭，新输出写入专用 jobs；`--record` 必须是新路径。

```powershell
python -B containers/run.py --image homespatial/restruct:local --weights E:/Data/model-cache/indoor --jobs E:/jobs/recon --record E:/jobs/check.json --entrypoint /opt/envs/core/bin/python -- -B /opt/recon/containers/verify_runtime.py --report /jobs/runtime-check.json
python -B containers/run.py --image homespatial/restruct:local --weights E:/Data/model-cache/indoor --jobs E:/jobs/recon --input scene=E:/scans/input-package --record E:/jobs/prepare.json -- prepare /inputs/scene/scene.json --out /jobs/run-001
python -B containers/run.py --image homespatial/restruct:local --weights E:/Data/model-cache/indoor --jobs E:/jobs/recon --input scene=E:/scans/input-package --record E:/jobs/predict.json -- predict /jobs/run-001 --runtime /opt/recon/containers/runtime.container.json --labels-file /opt/recon/profiles/indoor-fixtures-75.json
```

清单引用的资产根通过 `--root NAME=/inputs/scene/...` 显式绑定。后续调用需要保持相同 `/inputs/NAME` 与 `/jobs` 映射；`restruct --help` 和 [CLI](../docs/cli.md)、[证据工具](../docs/evidence.md)、[参数接口](../docs/scene-model.md)给出各阶段参数。`--entrypoint /opt/envs/core/bin/python -- -B -m unittest discover -s /opt/recon/tests -v` 可在镜像内运行测试。

容器内插件位于 `/opt/addons/homespatial_model`，skill 位于 `/opt/recon/skills/reconstruct-structured-interiors`。Agent 在宿主查阅证据、编辑 job 的参数 JSON，再调用容器的 build/render；认证信息由宿主管理。需要继续编辑时，宿主安装同版插件，打开输出 `.blend`；已保存图片打包在工程中。若要从参数重新生成 `mesh_reference`，在 `rebuild_scene(roots=...)` 中把资产根重新绑定到宿主位置。

## 更新依赖

修改对应 `requirements-*.in` 后，在独立缓存中用固定版本 uv 重新生成锁，重新构建并运行验证。Linux 平台显式要求 glibc 2.35 或更新，匹配 Open3D wheel；镜像 Ubuntu 24.04 为 glibc 2.39。

```text
uv pip compile containers/requirements-core.in --python-version 3.12 --python-platform x86_64-manylinux_2_35 --generate-hashes --no-build --output-file containers/locks/core.txt
uv pip compile containers/requirements-spatiallm.in --python-version 3.11 --python-platform x86_64-manylinux_2_35 --generate-hashes --no-build --index-strategy unsafe-best-match --index https://download.pytorch.org/whl/cu124 --output-file containers/locks/spatiallm.txt
uv pip compile containers/requirements-boxer.in --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --generate-hashes --no-build --index-strategy unsafe-best-match --index https://download.pytorch.org/whl/cu128 --output-file containers/locks/boxer.txt
```

`unsafe-best-match` 只用于生成/安装包含官方 PyTorch 索引的锁；实际安装强制验证每个包哈希。依赖变更后重新评估 Python、GPU 架构和扩展 ABI，不以能 import 代替模型推理和实际内核检查。
