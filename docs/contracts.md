# v1 数据与坐标契约

M0/M1 的 v1 机器定义位于 [contracts.schema.json](../src/indoor_recon/schemas/contracts.schema.json)，数值和引用检查位于 [contracts.py](../src/indoor_recon/contracts.py)。M2 新增独立的 [model v2](scene-model.md) 与 [JSON Schema](../src/indoor_recon/schemas/model-v2.schema.json)，v1 保持兼容；运行时使用包内 schema，不联网解析。

## 文档与资产

| kind | 含义 |
|---|---|
| `scene` | 场景 ID、源坐标、网格/点云及可选帧和对齐清单 |
| `frames` | 稳定帧 ID、图像尺寸、可选 RGB、深度与标定 |
| `alignment` | 米制源坐标到工作坐标的刚体变换和来源 |
| `proposals` | 指定后端的一批候选、几何、原生分数与来源 |
| `model` | 参数模型的对象、部件/宿主关系和几何表达 |
| `asset_lock` | 不可自动更新的文件大小及 SHA-256 预期值 |

所有文档带 `scene_id`。对象和帧的 ID 在各自清单内唯一，ID 是字符串；不限制帧数、照片号、类别名或物理建筑的方向。重新推理的候选应属于新的结果批次，不能凭相同序号覆盖已编辑对象。

资产引用示例：

```json
{"root": "scan-data", "path": "rgb/42.png", "sha256": "<64位十六进制摘要>"}
```

根路径由运行参数绑定，`bundle` 默认表示文档所在目录。`path` 使用 POSIX 相对路径，禁止绝对路径、盘符、反斜杠和 `..`。外部命名根用于引用既有大文件，避免复制大型私有数据。根内既有模型目录链接允许使用；根绑定本身是执行者指定的可信位置。

JSON 数值必须有限，不接受 NaN 或 Infinity；历史文件中表示漏射的 Infinity 仅在案例适配层识别，原文件哈希保留，未命中不转换成有效坐标。

## 坐标与单位

统一几何工作坐标为右手系、米制、Z 向上。源几何可以声明不同 up 轴与 `metres_per_unit`，未知尺度用 `null` 表示。原生左手系输入须由后续适配器显式转换并记录来源，不能把反射矩阵冒充刚体旋转。

矩阵按行存储，数学运算使用列向量左乘。对齐矩阵 `A = T_work_from_source_m` 的旋转必须正交且行列式为 +1，最后一行为 `[0,0,0,1]`。单位换算不放入旋转矩阵：

```text
p_source_m = metres_per_unit * p_source
p_work = A * [p_source_m, 1]
T_source_m_from_camera = T_source_from_camera，但平移乘 metres_per_unit
T_work_from_camera = A * T_source_m_from_camera
```

源相机位姿的平移与源几何使用同一单位。几何若已经声明为 `work`，其坐标已经是米制，不再乘源单位因子或重复应用 A。工作坐标几何可以独立用于几何/布局准备；将源相机照片投影到它仍需要有效尺度和对齐关系。

输入包检查会验证已知 source up 经 A 后是否指向 work +Z；`unknown` 保持待确认，不能视为已经校正。M1 已实现声明 up 校正、yaw 估计、配置楼层切分和输入域分区，详见 [CLI](cli.md)。

## 图像、相机与深度

`frames[*].size` 为 `[width, height]`；`K` 对应当前图像的分辨率与裁剪。M0 标定类型为已去畸变的针孔相机，光学坐标为 X 向右、Y 向下、Z 向前，左上角像素中心坐标为 `(0,0)`。未经去畸变的输入需在适配阶段处理，不能仅改写类型标签。

`T_source_from_camera` 将相机光学坐标变换到源地图坐标，不能传入其逆矩阵。投影使用其逆变换；世界方向对齐不改变 K，图像缩放/裁剪则须同步更新 K。

深度的 `measurement` 为 `camera_z`，`metres_per_unit` 是深度文件的单位转换，与地图位姿单位独立。例如 PNG 毫米深度为 `0.001`，反投影为 `z * inverse(K) * [u,v,1]`。它不是归一化射线方向上的欧氏距离。

仅有照片时可声明 image 而省略 camera。仅有标定深度时可省略 image。跨文档必须保持 scene_id 一致。尺寸、文件哈希和真实图像内容由资产/基线检查进一步核实，JSON schema 本身不会读取图片。

输入能力评估返回 `eligible`、`needs_input` 或 `skipped`，表示数据条件；它不表示模型已经运行或该输入来源已经实测。完整运行阶段状态由 M1 的执行记录定义。

## 候选与参数模型

候选统一在 `work_z_up_metres` 坐标中。OBB 中心为几何中心，`size_m` 为对象局部轴全长，`R_work_from_object` 的列是局部轴在工作坐标中的方向。

- `nature: volume` 要求三个尺寸均为正。
- `nature: surface` 要求恰好一个尺寸为零，另两个为正；用于 SpatialLM 的墙、门窗等平面候选。
- 原生分数保留有名称的数值，不补造缺失的 SpatialLM 置信度，不将不同后端分数视为相同概率。
- `sources` 区分测量、预测、推断、用户指定及历史记录，引用原文件、记录 ID 和可用的帧 ID。预测不等于测量真值。

参数模型最小表达为任意姿态的 `box`、局部 XY 多边形及孔洞 `polygon_surface`、独立 `mesh_reference` 和 `assembly`。可表达斜墙、斜顶、开放扫描、逐级部件和整体/部件关系；不强制闭合或轴对齐。类别是开放字符串，门窗与其他对象可通过 host_id 建立关系。

v1 的尺寸、位姿、唯一 ID 与关系检查保持原含义。M2 的 v2 增加共享节点、宿主开口、部件树、逐级棱柱、材质与编辑版本，并在实际 Blender 中验证构建、保存和导出；schema 通过仍不等于模型与扫描贴合。

## 基线语义

`asset_lock` 冻结实际文件，`verify-lock` 检查缺失、大小与 SHA-256。项目可另外保存私有回归基线，记录数量、类别、偏移和选定对象的测量。

校验不得自动运行 capture 更新预期。基线只说明“当前已知行为是否保持”，识别准确率、扫描物理精度与跨场景泛化需要另行验证。数值投影预算应根据数据精度确定，不作为任意扫描的几何精度阈值。
