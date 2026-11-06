# 输入、任务启动与感知 CLI

[English](cli.md) | [简体中文](cli.zh-CN.md)

当前 0.7.7 提供新场景启动、预处理与感知。`restruct build`、参数场景和 Blender 接口见 [参数场景说明](scene-model.zh-CN.md)。

`restruct` 将统一输入包转换为工作坐标点云与候选。通用代码不包含 特定场景的路径、楼层、帧数或类别；数据通过 [v1 契约](contracts.zh-CN.md)、准备配置和运行环境配置提供。下面命令中的路径替换为实际位置；PowerShell 可写成单行。

从照片、COLMAP 输出或 RTAB-Map `.db` 开始时，先读[输入数据准备](input-data.zh-CN.md)。本命令不直接读取 RTAB-Map 数据库，也不把 COLMAP 稀疏特征点当成完整扫描表面。

## 从新扫描创建任务

已有统一 `scene.json` 时可直接执行：

```bash
restruct init-task /data/scene/scene.json --runtime /config/runtime.json --out /tasks/scene-001 --root photos=/data/photos
```

需要 geometry 额外依赖（pip install ".[geometry]"）或配套 Docker 镜像。命令先验证输入声明和资产，运行 CPU prepare，生成独立的 `job/prepare`、`startup.json`、`runtime.local.json` 和 `evidence.json`。感知与 Blender 单独启动。`--config` 沿用下面 prepare 的配置；几何有多份时用其中的 geometry_id 选择。

输出目录必须尚不存在。原输入、旧 job 与 runtime 文件均只读；失败后的部分新输出保留用于诊断，修正输入或选项后使用新目录，不覆盖旧任务。未知源尺度/up 需要补齐声明；已声明为 work 米制的几何不重复换算或旋转。

runtime 使用当前执行环境可用的配置。路径型字段相对原 runtime 文件解析；单个可执行命令名和 Linux/WSL 绝对路径保持。容器内可直接用 `/opt/recon/containers/runtime.container.json`，宿主例子见 config。工具允许只配置当前阶段需要的后端，`missing_runtime` 会列出数据适用但尚未配置的后端；initialized 不表示预测、渲染或质量验收已经执行。

`startup.json` 提供：

- scene_id、版本、源文档哈希、实际 Python/源码路径、job 和 runtime。
- evidence_roots/model_roots 及新参数来源应使用的 task_root_name。若输入已占用 job/task 别名，会选择新别名，保留原引用含义。
- available_frame_ids 和 colored_layout/calibrated_rgb/scan_rays 能力。没有 mesh 时省略 scan 配置，不提供扫描网格射线。
- commands 中的 predict 及 evidence/build/render 的 prefix argv 数组。prefix 由调用者追加 query/model/blend、view 和新输出路径；直接传给 subprocess 等参数数组接口，避免手拼 shell 引号。
- `STATE.md` 默认包含外观取色、PBR、视觉对照、导出检查、重开复核及材质覆盖表；整屋重建按 [外观流程](../skills/reconstruct-structured-interiors/references/blender-materials.md#默认外观流程)执行。
- `commands.check_materials_prefix` 带 model_roots，供最终与重开 GLB 检查；初始化不自动执行建模或外观阶段。

这些路径是本机执行绑定。容器需保持 /inputs 和 /jobs 映射；搬移后显式重绑，不将容器绝对路径当成宿主路径。已有 startup 或已编辑工程按原范围继续；init-task 专用于新扫描，公开源码不包含私有案例启动脚本。

## 输入和准备

已有统一输入包：

```bash
restruct prepare /data/scene/scene.json --out /jobs/run-001 --root photos=/data/photos --config prepare.json
```

`bundle` 默认是 `scene.json` 所在目录。支持网格、点云、标定 RGB-D；彩色几何不依赖照片。`prepare.json` 的可选字段和默认值见 [DEFAULTS](../src/indoor_recon/preparation.py)。示例：

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

`auto` 有标定深度时选择 RGB-D，否则采样网格或直接读取点云。已声明的米制 `work` 几何保持原坐标。源几何先换算单位，再应用对齐；源相机平移同样换算后应用 A。up 采用声明的六种有符号轴，yaw 用近竖直法向的加权四倍角均值估计，网格用面积权重；低集中度会标记人工复核。可传 `yaw_degrees` 或提供契约中的已验证 alignment；未知 up/尺度需要补充输入。

楼层分界是外部配置，按工作坐标 Z 值切分，不内置楼层个数或标高。每层超出模型域时增加重叠窗口，保留原尺度，记录不重叠的 ownership core。SpatialLM 会按实际权重配置再次检查输入域。分区预测解码后恢复工作坐标；跨区同类、同楼层且角点 Hausdorff 距离不超过 0.1 m 的近重复框合并来源。其余候选保留，落在 core 外的中心进入 `review.json`。这用于保守去重，不负责拼接截断墙面或恢复拓扑。

SpatialLM 默认使用彩色点云。Boxer 有网格时使用逐像素 first-hit raycast；只有点云时使用投影网格中的最近可见点，协议另行记录，这条替代路径目前只完成数值小样例验证。缺少 RGB、标定或有效相机尺度时跳过 Boxer；无颜色时跳过当前 SpatialLM 适配器，几何产物仍可用。

## COLMAP 或 ZIP 导入

```bash
restruct import-colmap /archives/scene.zip --model-prefix scene/colmap/sparse/0 --images-prefix scene/images --geometry-path scene/scans/mesh.ply --out /data/scene-demo --scene-id scene-demo --up-axis +Z --metres-per-unit 1 --max-side 768
restruct prepare /data/scene-demo/scene.json --out /jobs/scene-demo
```

输入可以是 ZIP，也可以是已解压目录；输出须是新目录。按注册图像导入，保留不连续的原始 ID；`--max-frames` 会均匀选取注册帧。相机目前支持 `SIMPLE_PINHOLE`、`PINHOLE`、`OPENCV`，其他模型会明确报错。负 up 参数使用如 `--up-axis=-Y`。

导入器保留选中成员的原始照片、三份二进制标定和几何，记录成员 CRC/SHA-256；不改写源 ZIP，也不默认读取未确认单位的 `render_depth`。COLMAP 位姿求逆为 camera-to-source，像素中心减 0.5；OPENCV 径向/切向畸变实际重采样，缩放同步更新 K。`import.json` 记录稀疏点重投影误差。其计算依据是 [COLMAP 格式说明](https://colmap.github.io/format.html)和[相机坐标说明](https://github.com/colmap/colmap/blob/main/doc/cameras.rst)。up 和米制尺度仍需由数据来源或外部标定确认。

## 感知和环境

从零安装和版本选择见 [后端下载与验证](backends.zh-CN.md)。安装后先运行：

```bash
restruct verify-backends --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json --level imports --out artifacts/backend-imports
restruct verify-backends --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json --level smoke --out artifacts/backend-smoke
```

`--level config|imports|load|smoke` 依次增加检查深度，默认 `imports`；`--backend all|spatiallm|boxer` 默认两者，`--timeout` 默认每个后端 600 秒。每次用新 `--out`；`verification.json` 记录源码/权重哈希、默认版本比对、实际包版本（imports 及以上成功时）、达到的验证级别与错误，详细输出在各后端 `worker.log`。源码不同或无 Git 元数据只产生提醒，具体接口、设备、词表或加载错误返回非零退出码。`smoke` 使用合成输入，不能替代真实输入的完整预测。

```bash
restruct predict /jobs/scene-demo --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json
```

运行环境格式见 [runtime.example.json](../config/runtime.example.json)，示例使用 Linux 原生路径。Windows 原生后端填写 Windows 路径；Windows 调用 WSL 时，给该后端增加 `"wsl_distro":"Ubuntu"`，`python` 填 Linux 路径，`source_dir/model_dir/weights_dir` 填宿主可读路径，工具转换挂载路径。不通过 shell 拼接命令。

CLI 环境安装几何依赖；模型在各自解释器运行，读取指定源码和本地权重。需要已经可用的 CUDA/后端依赖，此阶段不自动安装或打包。SpatialLM 使用 `model_dir`；Boxer 可通过 `checkpoints` 中的 `boxer`、`dinov3`、`owlv2` 覆盖文件路径，省略时使用默认文件名。相对 checkpoint 路径以 `weights_dir` 为基准；其他相对路径以 runtime 文件所在目录为基准。源码（含 prompt 模板）、权重、环境包版本、参数、预处理清单和适配代码的哈希或版本写入 `identity.json`，更换后不能复用旧任务的预测缓存；可选 GPU 容器依赖锁定见 containers/locks。

SpatialLM 默认 `--detect-type arch`，可选 `all`；布局输出保留原始文本、解码文本、工作坐标偏移、token 信息和显存统计。Boxer 显式读取标签 JSON，内容为 `{"labels":["chair","table"]}`，条目须属于当前官方词表；保存逐帧 2D/3D 分数、OBB、接受索引、CSV 和官方融合来源。CSV 使用顺序时间戳，原始字符串 frame ID 单独保留。

`--layout none` 或 `--objects none` 可显式选择阶段。`profiles/indoor-68.json` 提供 68 类室内物体顺序；`indoor-fixtures-75.json` 另外加入 7 个卫浴类别，是开发配置，不保证覆盖所有室内物体。OWL 文本缓存与 CUDA 缓存放在 job 内；权重硬链接或复制到私有缓存后只读加载，上游权重目录不会被新缓存污染。

## 输出、搬移和恢复

```text
job/
  bindings.local.json           # 本机资产根绑定；搬移后可重绑
  prepare/
    prepared.json              # 协议、输入哈希、A、质量、分区与帧索引
    global.ply, regions/*.ply, points/*.npz
    state.json                 # 阶段身份和输出哈希
  spatiallm/
    regions/*.json             # 原始/解码文本、偏移、原生 boxes、运行统计
    raw-predictions.json, proposals.json, review.json
  boxer/
    frames/*.json              # 可恢复的逐帧原始检测
    units.json, accepted-index.json, boxer_3dbbs*.csv
    raw-predictions.json, fused-predictions.json, proposals.json
  perception.json
```

两个候选清单继续使用 v1 `proposals` 契约，来源 `root: job` 绑定到 job 根。SpatialLM 零厚度平面有效；非法几何保留原始生成并进入复核记录。候选分数保持原生语义，不补造布局置信度。

```bash
restruct prepare /moved/input/scene.json --out /moved/job --resume
restruct predict /moved/job --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json --resume
python scripts/check_job.py /moved/job --report /reports/job-check.json
```

同一目录重跑须带 `--resume`；输入、选中帧、配置或已记录代码不同则使用新 job。已有输出被修改会报错，缺失的单位产物可重算。推理按帧/分区保存校验记录，恢复后从这些记录重建聚合文件，不追加旧 CSV。模型进程日志写入 `worker.log`，失败写 `failure.json`；重试成功后历史失败记录保留。完整阶段会核对哈希后复用。

搬移时可重新运行 `prepare --resume` 重绑输入，或向 `predict` 提供 `--root NAME=PATH`。场景内容和选项不变才能复用。`bindings.local.json`、`request.local.json` 是本机执行信息，不能当作可迁移的数据契约。恢复应使用相同模型运行环境；当前 Docker 依赖由 containers/locks 固定，见容器说明。

## 运行记录与过程回放

`evidence`、`build`、`render`、`residuals`、`check-geometry`、`check-materials` 结束后，在本次新建的 `--out` 目录写 `activity.json`：命令、开始时间（UTC）、耗时、是否通过、错误信息和主要输入（model/blend/view/query/requests）。失败的运行只要已建出输出目录也会留下记录；输出目录事先存在而被拒绝的运行不写，原目录的记录保持不变。`restruct view` 的跟随模式用它显示最近几次运行。

### 材质导出检查

```bash
restruct check-materials /tasks/scene/model.glb --root task=/tasks/scene --exceptions /tasks/scene/material-exceptions.json --out /tasks/scene/material-check
```

`--exceptions` 可省略；内容是逐参数材质 ID 到非空理由的 JSON 对象，例如 `{"glass": "Uniform transparent glass confirmed in photos"}`。检查每个实际导出部件，要求颜色／粗糙度／法线贴图、可解码内嵌图片、非退化 UV 和米制映射声明；例外只豁免没有声明的纹理，不豁免已声明却丢失或损坏的纹理。`--root` 按模型资产引用提供所有根，检查源图片与实际 GLB 像素相同（粗糙度比较 G 通道）。不传 roots 时仅检查导出内部，报告 `source_pixels_checked: false`，不能替代完整交付检查。

检查写入新目录的 `materials.json`，包含模型哈希、版本、逐部件错误与例外；失败返回非零。`appearance_quality_passed` 为 null：工具不判断取色和照片一致性，仍需按 skill 视觉复核。`build` 可用于中间几何版本，其成功不表示外观交付通过。

`python scripts/activity_timeline.py jobs/task --out <新目录> [--codex-log rollout.jsonl]` 把任务目录里的工具输出（证据、构建、渲染、诊断、射线对比，以及 activity.json 的耗时和失败）按时间排成 `timeline.json`；给出 Codex 会话日志时，再加入 agent 的说明和它实际查看过的图片（按内容对应到磁盘文件，大图被缩放时退回调用里的路径）。查看器 `?process=<目录>/timeline.json` 按这条时间线回放：真实耗时被压缩成固定节奏的步骤，说明作为字幕；`record=1` 时由录制脚本逐帧调用 `window.film.seek(t)` 导出视频。

每个步骤带 `focus_z`（关注的工作坐标高度：照片取相机高度、剖面取剖切高度、新版本取改动对象的中位高度），回放据此在关注点位于某层楼板之下时自动剖到该楼板底面以下。`--redact-object <模型对象 ID>`（可重复）把拍到该对象的照片、裁剪、像素射线和残差拼图标为 `redacted`：对象包围盒取最后一版模型（外扩 0.15 m），相机位姿可能有偏差，所以投影落在画面边缘外 25% 以内、未被扫描遮挡的也算拍到；拼图类无法判断来源的截图一律标记；`--redact-frame <帧号>`（可重复）直接标记某张照片（例如拍到人的），以及由它裁剪或拼出的图。回放略去这些图片，并且不画扫描里落在该包围盒（再外扩 0.3 m）内的三角形；原文件不改。`cut=highlights` 只保留要点：开头一批证据照片，每个版本前最后一次看图加新版本，每组配一条说明，结尾是最终总结（回到整栋），约 80 秒。
