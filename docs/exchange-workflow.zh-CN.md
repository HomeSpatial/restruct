# 独立重建数据的导入与预检

[English](exchange-workflow.md) | [简体中文](exchange-workflow.zh-CN.md)

这些命令不需要 HomeSpatial 或 remap 安装。`reconstruction_description` / `reconstruction_bundle` 的版本保持 1；现有 `scene.json` 仍可使用，工具版本不变。

如果还没有可用输入，见 [COLMAP / RTAB-Map 数据准备](input-data.zh-CN.md)。`rtabmap-hloc` 的 SfM 描述只含相机、照片和报告；需要先由 remap 或其他上游补充稠密几何。此处的导入器消费原生文件和描述，不导入上游工具的 Python 包。

## 从描述文件开始

在源码仓库根目录执行 `python -m pip install ".[geometry]"` 安装 Restruct 及几何依赖；当前尚未发布到 PyPI。输入可以是本地自包含描述，也可以通过 `--root 名称=目录` 绑定外部资产。`local/manifest` 是文档根的保留名称。

```bash
restruct inspect result/reconstruction.json
restruct import-description result/reconstruction.json --preflight
restruct import-description result/reconstruction.json --out imported
restruct prepare imported/scene.json --profile handoff --preflight
restruct prepare imported/scene.json --profile handoff --out job
```

有多个几何时必须提供 `--geometry-asset ID`；有多个相机或图像资产时提供 `--sparse-asset ID` / `--images-asset ID`。bundle 子资产的 ID 为 `重建ID/资产ID`。不依赖 sfm/rgb/dense 等固定名称。`--no-photos` 显式只导入几何，不能与照片/帧选择同时使用。

当前适配器支持 PLY 点云、PLY/GLB 网格，以及 binary COLMAP＋照片。点云与网格按文件内容区分；照片按 COLMAP 相对文件名绑定。输入需要已声明右手坐标、朝上轴和公制尺度；相机与几何位于不同坐标时要求显式的 rigid/similarity 关系。未知格式仍可被独立格式校验器接受，但该消费入口会报告不支持。

默认导入全部已声明且匹配的照片，无固定 3 帧限制，也不重新抽样。重复使用 `--frame-id COLMAP_ID` 可以选择准确子集。照片默认缩到长边不超过 768，并同步内参和去畸变；原生源照片、相机模型、坐标变换与质量报告保留。导入不会把源质量状态改为通过，`import.json` 保留来源摘要。

## 坐标、采样和点来源

`prepare` 默认 `layout` 配置保留原有分析行为：已有 alignment 优先，否则估计工作朝向；2 cm voxel；有标定深度时优先 RGB-D。`--profile handoff` 使用 `alignment=preserve`，要求输入 +Z 朝上，并保留坐标方向与原点，单位仍按声明转换成米。其他朝上轴必须用已有变换或 `--alignment auto` 转换到工作 +Z。

支持 `--alignment preserve|auto|existing_or_auto`、`--point-source auto|mesh|point_cloud|rgbd`、`--geometry-id`、`--voxel-m`。配置优先级是 profile → JSON `--config` → 显式 CLI 选项。2 cm 是布局分析的起点，可能重复采样，不能当成精度承诺。精细检查需要单独验证参数。

`prepare --preflight` 不创建 job，显示资产、点来源、帧名单、单位、朝上轴、对齐策略、采样参数和风险提示。它校验结构及引用文件；自动估计的实际矩阵在执行完成后写入 `prepared.json`。预检不会声称尚未运行的几何运算或推理质量已经通过。

## 创建任务与模型预检

```bash
# 直接接受新描述，导入并准备，同时生成下一步命令
restruct init-task result/reconstruction.json --out task --runtime runtime.local.json --labels-file labels.json
# 或暂时只启用布局模型，显式关闭对象检测
restruct init-task result/reconstruction.json --out layout-task --runtime runtime.local.json --objects none
# 使用任务内已解析的 runtime，先检查全部选中阶段
restruct predict task/job --runtime task/runtime.local.json --labels-file labels.json --preflight
restruct predict task/job --runtime task/runtime.local.json --labels-file labels.json
```

`labels.json` 格式为 `{"labels":["chair","table"]}`；请按当前 Boxer 官方词表及任务范围提供真实类别。也可在 `runtime.boxer.labels_file` 配置，路径相对 runtime 文件解析。不会静默补造类别。相对源码/权重路径也以 runtime 文件为基准。

开始任何模型前先检查所有启用阶段的输入能力、解释器、源码、权重文件与类别配置；配置完整后分别用目标解释器检查原生 imports、CUDA/BF16 和 Boxer 词表。每个探针最长 45 秒，不构造模型、不下载权重。权重内容、显存能否容纳模型以及实际预测效果仍由执行检验。只有所有选中阶段预检通过才开始预测；输入不具备的阶段注明 skipped。

`init-task` 始终保留可用的几何与证据；若推理缺配置，`startup.json.commands.predict` 为 null，`prediction_preflight.errors` 列出缺项，并生成可重复执行的 `predict_preflight` 命令。配置完整时生成的 predict 命令包含后端选择和标签文件绝对路径。

所有新导入和任务必须使用新目录。`prepare/predict --resume` 继续沿用身份与内容哈希检查。输出摘要分别表示执行、数据、质量、下一步；完成导入或预测不等于结构/精度验收通过。

## 维护与验证

独立格式的 `verify.py`、`exchange.py` 和 schema 由参考目录的 `vendor.py` 原样嵌入 `_exchange`，`SOURCE.json` 记录哈希；消费工具不从 HomeSpatial 路径导入。更新参考代码后同步，并使用 `vendor.py --target src/indoor_recon/_exchange --check` 检查一致性。

回归入口：`python -B -m unittest discover -s tests -v`。新交接测试覆盖任意资产 ID、多几何选择、点云类型、确切帧选择、相机 Sim3、预检无输出，以及缺少后续阶段配置时不启动前一模型。

## 自动追加同一个 bundle

```bash
restruct init-task --bundle house --runtime runtime.local.json
restruct predict --bundle house
# 中断后：恢复同一未发布的感知阶段，不重复已经完成的模型/帧
restruct predict --bundle house --resume
restruct build authored-model.json --bundle house
restruct bundle inspect house
```

各命令自动选择唯一输入阶段、沿用 scene_id 并派生输出目录。--from 指定输入，--stage 指定新的阶段名。默认追加 prepared、perception、model，旧阶段禁止覆盖。runtime 首次传入后保存在 `.bundle/runtime.local.json`，后续不必重复；迁移主机请重新指定本机配置。类别文件可放在 runtime 的 boxer.labels_file 中。

init-task 执行导入和预处理；predict 从已锁定的 prepared 快照复制新工作任务，不修改原快照。模型候选需检查/编辑成参数模型再 build；不会把候选自动视为验收通过。build 同时发布可编辑参数、原生工程、GLB 与依赖，明确记录 Z-up/Y-up 变换。构建成功仍不代表几何质量通过。

`predict --bundle --resume` 要求工作目录内有本版本生成的 `prediction-run.json`；它核对场景、目标阶段、输入阶段及文档哈希、运行配置与类别文件哈希。未重新指定的 layout、objects、detect-type 沿用上次选择；自定义 `--stage/--out/--from` 仍需指向原任务。底层还会验证准备资产、照片、模型代码、权重和逐帧检查点。输入或设置变化应创建新阶段，旧版本没有恢复记录的目录不会被自动接管。

同一预测输出只允许一个写入进程，进程退出后操作系统自动释放锁。恢复途中若快照复制曾中断，会保留 `exchange-interrupted-*` 副本并重新生成；若完整快照已保存而发布失败，则检查快照后直接重试发布，不启动模型。已发布阶段继续拒绝覆盖；残留的发布锁或未提交阶段目录仍按 bundle 发布器的错误提示检查，不能盲目删除。

模型工作进程和任务状态的 JSON 检查点采用唯一临时文件与原子替换。Windows access/sharing/lock 错误最多尝试 6 次，等待总计不超过 1.55 秒；其他错误立即返回。持续失败保留原检查点及临时文件，并在异常中记录位置；临时文件不会作为已完成单元或发布资产。更新 worker/适配器代码会使旧推理指纹失效，不会静默把旧代码结果当作新代码结果复用。

如果仅发布失败，可用 `restruct bundle append house --result result/reconstruction.json --stage <new-id> --from <input-id> --operation <producer>` 发布已有成功结果，无需重复模型计算。该显式入口核验格式与完整性，结果是否成功由调用方确认。

init-task 生成的后续命令也带有 bundle 关联；其 layout/objects 选择作为准备阶段默认值传给 predict，显式传入的 labels-file 会保存到本机运行配置，避免下一阶段再次填写。可用 predict 的参数明确覆盖这些选择。
