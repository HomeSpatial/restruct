# 输入数据准备：COLMAP 与 RTAB-Map

本项目从已有几何和可选的标定照片开始。本文以 COLMAP 工作目录和 RTAB-Map 数据库说明怎样取得可用输入；ScanNet++ 保留为[结果案例](../cases/scannetpp-09c1414f1b/README.md)。本页不附带真实采集数据，命令中的输入路径替换为自己的文件。

## 选择入口

| 当前持有的数据 | 准备步骤 | 是否需要 rtabmap-hloc |
| --- | --- | --- |
| COLMAP 稠密点云／网格、对应照片和相机 | 确认单位、轴向和照片对应，直接 `restruct import-colmap` | 不需要 |
| COLMAP 仅有照片和稀疏模型 | 先用 COLMAP MVS 或其他稠密重建工具生成几何 | 不需要 |
| RTAB-Map `.db`，包含 RGB、相机标定和有效位姿 | 本文路线用 rtabmap-hloc 生成 SfM，再用 remap 生成稠密几何 | 作为上游工具单独使用 |
| 已从 RTAB-Map 导出稠密几何和匹配的 COLMAP／统一输入 | 直接走 COLMAP 或 reconstruction description 入口 | 不需要重复运行 |

要运行照片物体识别和几何投影取色，需要照片、对应的相机内参和外参，以及相机到几何的已知坐标关系。只有彩色几何仍可开展布局和几何工作；没有标定照片时不能宣称照片证据链已完成。只有 `points3D.bin` 不等于有完整房间表面。

所有路径示例都从同一工作目录解析。原始数据可放在已被 Git 忽略的 `private/`，任务输出放在 `artifacts/`；不要将真实采集、数据库或 runtime 本机路径提交到源码。每次导入、初始化和重建使用新的输出目录。

## COLMAP 路线

### 取得并整理数据

已有 COLMAP 工作目录时可以直接检查下面的文件。只有普通照片时，先按 [COLMAP 官方教程](https://colmap.github.io/tutorial.html)完成 SfM 和稠密重建。SfM 恢复相机和稀疏特征，稠密步骤生成 `fused.ply` 或网格。清晰、重叠且覆盖房间不同方向的照片有助于恢复；实际覆盖和表面质量仍须检查。

使用同一个稠密工作目录的**去畸变照片与对应相机**，例如：

```text
private/colmap/
  dense/0/
    images/               # 与此 sparse 中图像名称、分辨率相符
    sparse/
      cameras.bin
      images.bin
      points3D.bin
    fused.ply             # 稠密彩色点云
    meshed-poisson.ply    # 可选网格，不要求两者同时存在
```

不要混用原始照片与去畸变后的相机，也不要把另一重建分量的 `sparse/1` 与此处几何拼在一起。新版 COLMAP 可能另有 `rigs.bin`、`frames.bin`；当前导入器读取上述三份 binary 文件，并使用 `images.bin` 内的图像位姿。保留原始工作目录，特殊 rig 数据先核对照片投影。

如果只有 COLMAP 文本模型，使用官方 `colmap model_converter --output_type BIN` 转换到一个新目录，并把后续 `--model-prefix` 指向它。当前导入器支持 `SIMPLE_PINHOLE`、`PINHOLE`、`OPENCV`；其他模型需要上游实际去畸变／转换，不能只改相机类型名称。原生格式见 [COLMAP 格式文档](https://colmap.github.io/format.html)。

### 确认尺度和朝向

纯视觉 SfM 不自动带有米制尺度。用测量基准或已标定的外部坐标确定 `metres_per_unit`，确认朝上轴；如果需旋转或对齐，几何、相机和稀疏模型必须使用一致变换。存在任意坐标关系时可用[独立描述中的坐标变换](exchange-workflow.md)表达。不能把未知尺度填成 `1` 来使预检通过。

以下命令**假设输入已经确认为米制、+Z 朝上**。`--up-axis` 和 `--metres-per-unit` 描述源坐标；导入不重新估计真实尺度，后续 prepare 才按声明转换工作坐标。

```bash
restruct import-colmap private/colmap --model-prefix dense/0/sparse --images-prefix dense/0/images --geometry-path dense/0/fused.ply --scene-id colmap-room --up-axis +Z --metres-per-unit 1 --out artifacts/colmap-input
restruct inspect artifacts/colmap-input/scene.json
restruct prepare artifacts/colmap-input/scene.json --preflight
```

有网格时将 `--geometry-path` 换成对应的网格文件；导入器按内容区分点云和网格。只有点云时，Boxer 使用点投影替代网格射线，这条替代路径目前只有数值小样例验证；需要扫描表面射线证据时应提供网格。首次只想检查少量帧，可添加 `--max-frames N`，它均匀选择已注册帧，并不保证这些帧覆盖整个房间。

导入后检查 `import.json`、`scene.json` 和 `frames.json`：几何类型、导入帧数、原生相机类型、图像尺寸及重投影摘要应符合预期。`prepare --preflight` 只检查输入，不创建任务，也不证明场景精度。

### 创建任务

先按[后端指南](backends.md)准备 `runtime.local.json`。初次只检查输入时可使用仅含当前阶段配置的 runtime；缺少后端时初始化会记录缺项，不会自动完成预测。

```bash
restruct init-task artifacts/colmap-input/scene.json --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json --out artifacts/colmap-task
restruct predict artifacts/colmap-task/job --runtime artifacts/colmap-task/runtime.local.json --labels-file profiles/indoor-fixtures-75.json --preflight
```

预检通过后，去掉最后一条的 `--preflight` 执行感知。任务的 `startup.json` 也保存了下一步命令及输入绑定；以其中的实际路径和运行状态为准。

## RTAB-Map 数据库路线

### 取得可用的数据库

使用 RTAB-Map 采集并保存 `.db`，或使用已有采集文件。本例要求数据库保留 **RGB 图像、相机标定和有效轨迹**；只有激光点云、没有照片的数据库不满足这条照片重建路线。先用官方 [Database Viewer](https://github.com/introlab/rtabmap/wiki/Tools#database-viewer)检查图像、轨迹及地图是否存在，再保留原数据库作为输入。

### rtabmap-hloc 是可选上游工具

本项目目前不直接读取 `.db`。[rtabmap-hloc](https://github.com/HomeSpatial/rtabmap_hloc) 负责数据库解码、照片／相机导出和稀疏重建；[remap](https://github.com/HomeSpatial/remap) 可从这些产物继续稠密重建。`restruct` 只读取导出的标准文件与 `reconstruction.json`／bundle，不需要安装或导入两个上游 Python 包。

```text
RTAB-Map .db
  → rtabmap-hloc：照片 + 对齐后的 binary COLMAP + reconstruction.json
  → remap 或其他稠密重建：点云／网格 + 照片 + 相机
  → restruct：输入预检 → 任务准备 → 感知 → 参数建模与交付检查
```

`rtabmap-hloc` 当前发布的资产是相机、照片和报告，**没有稠密几何**，因此不能直接把它的 SfM 描述传给 `restruct init-task`。默认会运行完整 SfM 并通过 Sim3 对齐恢复 RTAB-Map 尺度，并非只转换文件格式；`--use-existing-poses` 改为使用已有位姿做三角化，仍不是简单数据库解包。

上游安装包名为 `rtabmap-hloc-reprocess`，命令名为 `rtabmap-hloc`。核对时两个上游 README 均标为尚未公开发布的候选，许可证待确认；需要先取得仓库访问权限，并按各自 README 在 Linux／WSL、Python 3.12 环境安装。不要假定 PyPI 同名包就是这里的工具。它们不加入本项目的默认安装依赖。

### 通过同一个 bundle 交接

各命令在对应工具环境运行；在同一工作目录执行，或将 `--bundle` 改为各环境均可访问的同一绝对路径。runtime 中的路径也必须属于运行 `restruct` 的环境。以下以普通照片尺寸的 `native` 稠密路线为例：

```bash
rtabmap-hloc private/capture.db --bundle artifacts/rtabmap-house
remap reconstruct --bundle artifacts/rtabmap-house --profile native --preflight
remap reconstruct --bundle artifacts/rtabmap-house --profile native
restruct bundle inspect artifacts/rtabmap-house
restruct init-task --bundle artifacts/rtabmap-house --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json
restruct predict --bundle artifacts/rtabmap-house --preflight
```

预检通过后再执行对应的下一条命令；感知预检通过后，去掉最后一条的 `--preflight` 执行感知。工作目录默认位于 `artifacts/rtabmap-house/work/`，已发布快照位于 `stages/`。缺依赖、缺权重或相机不匹配时先修正，不跳过预检继续运行。已发布阶段不能覆盖，下一次实验换 bundle 或显式使用新的 `--stage`。

这里显式选 `--profile native`，其输出为稠密点云。remap 当前默认的共视／RWp profile 另要求匹配的 504×378 去畸变照片、至少六帧和相应 CUDA 环境，不能直接假定任意 `.db` 导出均满足。需要默认网格链路时先按 remap 文档准备并检查这些条件。`native --preset smoke` 仅作少量帧执行验证，不能当作完整房间输入。

不使用 bundle 时，等价的独立描述路线为：

```bash
rtabmap-hloc private/capture.db -o artifacts/rtabmap-sfm --scene-id rtabmap-room
remap reconstruct artifacts/rtabmap-sfm/reconstruction.json --profile native --preflight
remap reconstruct artifacts/rtabmap-sfm/reconstruction.json --profile native --out artifacts/rtabmap-dense
restruct import-description artifacts/rtabmap-dense/result/reconstruction.json --preflight
restruct init-task artifacts/rtabmap-dense/result/reconstruction.json --runtime runtime.local.json --labels-file profiles/indoor-fixtures-75.json --out artifacts/rtabmap-task
```

如果已从 RTAB-Map 导出合适的网格／点云和标定照片，可以直接整理为 COLMAP 目录或[统一输入描述](exchange-workflow.md)，跳过 rtabmap-hloc 和不必要的稠密重建。几何与相机必须对应同一优化后的地图坐标；只导出一个 PLY，不会自动带上所有照片及相机关系。

## 输入通过以后

确认以下信息后再启动耗时推理：

- 几何非空，有足够的房间表面覆盖；颜色存在时才能启用当前 SpatialLM 适配器。
- 相机记录对应实际图像文件及分辨率；图像裁剪、缩放和去畸变后内参同步更新。
- 单位、朝向和相机到几何的关系有依据，已抽查照片投影；预检通过不能替代这些物理确认。
- 后端输入条件、解释器、源码和权重预检通过；保留上游质量报告与未通过项。

感知输出是待核对的候选。随后依据扫描和照片组织、修正参数模型，执行外观取色、PBR、同视角复核、最终导出和重开检查；见[重建 skill](../skills/reconstruct-structured-interiors/SKILL.md)。可以用 `restruct view artifacts/colmap-task --open`，或对 bundle 下的 `work/` 目录启动查看器，检查任务和后续构建。

本页命令按 core 0.7.7、rtabmap-hloc `8987481`、remap `74e20ed` 的接口核对。上游版本更新后先核对 `--help` 和输入预检。接口与格式检查通过不代表任意 RTAB-Map 数据库、完整 GPU 推理或跨场景重建质量已经验证。
