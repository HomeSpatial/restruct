# M3 证据查询与渲染

[English](evidence.md) | [简体中文](evidence.zh-CN.md)

`restruct evidence config.json query.json --root NAME=PATH --out new-directory` 读取已有 prepared 数据与扫描，保存可追溯的查询结果。`restruct render` 从已保存工程生成临时视图，不保存相机、灯光或隐藏项到原工程。两者都要求新的输出目录。

## 输入与坐标

evidence 配置独立于 v1/v2 模型文档：

```json
{
  "schema_version": 1,
  "kind": "evidence",
  "scene_id": "my-scene",
  "prepared": {"root": "job", "path": "prepare/prepared.json", "sha256": "actual-sha256"},
  "scan": {
    "asset": {"root": "scan", "path": "scan.ply", "sha256": "actual-sha256"},
    "metres_per_unit": 1,
    "T_work_from_source_m": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
  }
}
```

`scan` 可省略；只有点云时射线查询明确不可用。扫描文件按实际文件坐标读取，包含网格节点变换；先做尺度换算，再乘声明的刚体变换。glTF 读取器不会猜测历史 ROS 文件的轴向。所有查询和输出使用 work Z-up 米制。相机采用 prepared NPZ 内与图像配对的 K、`T_work_from_camera`；不重新估计标定或朝向。

输出须位于绑定的原始资产根及 prepared 目录之外。结果 `evidence.json` 保存原查询、输入 SHA-256、结果与解释；`files.json` 固定本次输出。存在 stage state 时检查读取的 NPZ/点云哈希，资产声明的 SHA-256 也会核验。

## 查询

每次调用接受一个 JSON 对象：

| op | 参数 | 输出 |
|---|---|---|
| `info` | 无 | 帧数、点来源、对齐及 mesh ray 能力 |
| `photos` | `bounds_work_m: [[minX,minY,minZ],[maxX,maxY,maxZ]]`, `limit?:6`, `frame_ids?:[...]` | 投影裁图范围、扫描支持数、相机位置和原图 asset |
| `crop` | `frame_id`, `bounds_work_m` 或 `crop_xyxy:[x0,y0,x1,y1]`, `margin_px?:12` | 实际 `crop.png`、裁图偏移及 `K_crop` |
| `pixel_rays` | `frame_id`, `pixels:[[u,v],...]`, `max_distance_m?` | 原图像素到扫描的首次命中，并给出该帧的 `K`、`T_work_from_camera`、`size` |
| `raycast` | `origins_work_m:[[x,y,z],...]`, `directions_work:[[dx,dy,dz],...]`, `max_distance_m?` | 点、单位方向、欧氏距离、法线和三角形编号；漏射为 null |
| `section` | `bounds_work_m`, `axis:x/y/z`, `offset_m`, `thickness_m` | 点云薄层 `section.png` 和全部选中点 `section.npz` |
| `plane` | `bounds_work_m`, `distance_threshold_m`, `seed?:42` | RANSAC 主平面、float64 精化、支持率/残差、全部查询点 NPZ |
| `appearance` | `bounds_work_m`, `normal_work:[nx,ny,nz]`, `cell_m?:0.05`, `max_frames?:120`, `segment_scale?:1`, `min_region_m2?:0.25`, `min_chroma_gap?:0.015`, `normal_tolerance_deg?:25`, `white_reference?:{bounds_work_m, normal_work, colour_srgb_255?:[235,235,232]}`, `lightness_weight?:0`, `seed?:0` | 表面逐格照片颜色（逐帧逐通道增益归一，可按参照白定绝对色）、按色度的材料分区及各区颜色、区间分界线段、`reliability`、`appearance.png` 与 `appearance.npz` |

照片按 prepared 首次命中点在 bounds 内的数量、投影面积排序。画面有投影不代表目标可见或分类正确，必须看裁图。AABB 跨近面时跳过该帧，可缩小查询范围或直接指定像素 crop。裁图不缩放，`K_crop` 只减去裁图原点；像素射线仍接收原图像素。

appearance 取与 bounds 相交、法向与 `normal_work` 夹角不超过 `normal_tolerance_deg` 的扫描三角形，按 `cell_m/2` 间距面积均匀采样，只保留落在 bounds 内的样本。所有帧先投影，取视野内样本最多的 `max_frames` 帧；每个样本在每帧里经扫描射线确认未被遮挡（命中距离与样本距离差不超过 max(3 cm, 1%)）、视线与表面法向夹角余弦不小于 0.25，才读取像素颜色，剔除任一通道 ≥ 250 的高光、任一通道 ≤ 4 或均值 ≤ 12 的暗部。像素先转为线性光，每次观测按投影面积 cos(视角)/距离² 加权（近处、正对的视角权重大，与摄影测量贴图的视角评分同理）。每个通道的对数值按“样本反照率 + 帧增益”交替求解，同时去掉各帧的曝光差和自动白平衡差（以中位帧为基准）。帧增益只由目标表面的样本求出。给出 `white_reference` 时，参照表面的样本用同一组帧增益校正后取加权中位色，再按通道缩放全部颜色，使参照表面取 `colour_srgb_255`（默认近白墙漆），结果另给出参照面实测色和缩放系数；参照面应与目标处于同一光照（同一房间、同侧光源），天花板常受另一侧光照，不宜作地面的参照。表面按 `u`/`v` 轴（地面为工作 x/y，墙面为水平沿墙与竖直）划成格，至少 2 次观测的格参与分区，分区用未经参照缩放的颜色，所以有无 `white_reference` 分区相同：各格 (r, b) 色度 3×3 中值平滑后做 Felzenszwalb 图分割（`segment_scale` 越大区越少），`lightness_weight` > 0 时特征另加该权重乘以对数亮度，用于分开只差明暗的中性材料（同时会切出阴影）。小于 `min_region_m2` 的碎片并入最相近的邻区，相邻且色度中值差小于 `min_chroma_gap` 的区再合并。区颜色是其全部观测（校正后）的加权中位数，`base_colour_linear` 为对应线性值；`frame_gain_rgb` 记录各帧三通道增益。相邻区的共享边缘格用主成分拟合成直线，给出两端点和直线度。`reliability` 汇总结果能否采用：`blocking` 中任一项（`no_regions`；参照面观测少于 150 次的 `reference_few_observations`；某通道线性缩放大于 4 或小于 0.25 的 `reference_scale_extreme`；校正后超过 5% 的格超白的 `clipped_after_scale`）使 `usable=false`；`warnings` 给出缩放超出 0.5–2 的 `reference_scale_large`、三通道缩放比大于 1.6 的 `reference_cast`，以及少于 3 帧或超白的 `region_weak`。各区另给 `frames` 和 `clipped`。表面上的光照衰减和不同区域光源颜色的差异不做校正：窗光下偏冷、灯下偏暖、强光处褪色的同一材料仍会有差别。

plane 的阈值必须显式指定；统计是局部主平面支持，不是自动的墙/地板语义认证。section 是点云厚度切片，不是网格精确交线。所有比较保留原 bounds、射线和阈值，修改前后复用同一采样域。

## 临时渲染

```text
restruct render edited/model.blend --runtime runtime.local.json --view view.json --out render-after
```

view 接受 `object_ids`（按这些对象取景）、`hide_ids`（显式隐藏）、`isolate`（只渲染 object_ids）、`direction_work`（从目标看向相机的方向）、`bounds_work_m`（可选取景范围）、`resolution:[960,720]`、`samples:16`，以及 `photo_camera:{K, T_work_from_camera, size}`：按该照片的透视相机渲染（OpenCV 针孔，像素中心为整数），与照片逐像素对齐，可直接叠图对照；三项取自对该帧的 `pixel_rays` 结果，`resolution` 须保持照片宽高比，灯光为相机处的中性补光。默认显示当前项目全部网格，自动取景；输入模型无未记录修改才允许渲染。

Blender `render_review` 使用临时 Scene 与模型副本，保留原模型数据块、参数、可见性和当前 Scene。输出 PNG、`.render.json` 记录版本/模型哈希与视图。CLI 另外检查输入 blend SHA-256 不变。相同视图用于前后对照；剖开视图的隐藏对象随结果记录。视觉验收需要 Agent 实际读取 PNG，并在场景报告中记录观察。

## 独立场景任务

`startup.json` 是本机运行绑定：解释器、源码、runtime、证据配置、资产根、输入参数及输出目录。任务以单 Agent 完成证据核查和参数修改，不改通用工具代码；每个场景一个目录、一位参数写入者。

交付 `delivery.json` 至少记录 `scene_id`、`baseline_model`、`baseline_blend`、`baseline_glb`、`edited_model`、`edited_blend`、`edited_glb`、`changed_object_ids`、`evidence_reports`、`render_before`、`render_after`、`report`。路径均相对本任务目录。报告说明证据、实际修改、同域前后指标与视觉观察。保存/重开和 GLB 验证仍走 M2 参数接口；不将任务完成消息当成产物验收。


交付复核使用 `scripts/check_m3_delivery.py --startup ... --delivery ... --out /new/check`：独立核对证据哈希、GLB 参数/坐标、非目标几何/材质与固定相机，再在新 Blender 进程重建并重新导出。`scripts/compare_ray_evidence.py` 可重跑已保存的扫描射线查询，并将同一射线域与两份 GLB 的指定对象对照；显式选择 XYZ 分量或沿射线的空间距离，分别报告漏射与共同有效域。二者不运行模型感知。

交付复核默认还检查最终及重开 GLB 的 PBR 纹理覆盖、UV、内嵌图片与源像素。delivery 可给 `material_exceptions`（材质 ID 到理由的对象）表达玻璃等纯色例外；仅用户明确限定的纯几何任务使用 `appearance_scope: "geometry_only"`，并填写 `appearance_scope_reason`，否则不可跳过。此脚本要求一次有实际几何变化的编辑，纯材质任务用 `restruct check-materials` 加独立的几何保持／重开验证。取色可靠性与视觉复核仍写入 `STATE.md`，不由图片存在性自动判定。


### 同域射线比较

```text
python scripts/compare_ray_evidence.py --startup startup.json --evidence evidence/query/evidence.json --before baseline/model.glb --after edited/model.glb --object-prefix furniture/sofa/ --component z --report new-review/sofa.json
```

输入为当前 model v2 导出的 GLB。脚本核对 scene_id、坐标、证据配置与输入哈希，并重跑原扫描查询核对起点、方向、命中和距离。输出必须新建，且在 source/prepared 根之外；无需重新运行感知。

省略选择器时只比较全场景；`--object-id` 可重复且要求两版均有该 ID；`--object-prefix` 可重复，按每版实际网格选择，适用于部件增删。一版目标为空时保留全部漏射，两个版本都未匹配则报错。指定目标时仍同时保留全场景首次命中。

JSON 的 `domains.full_scene/target` 分别报告共同有效域误差、单版配对误差、漏射和覆盖新增/丢失。顶层误差沿用原脚本接口，取 target（如有）或 full_scene。前后改善只比较共同掩码；`paired_before/after` 的有效域可能不同。无共同命中时误差为 null。`*_full_target_hit_differs` 表示同一射线在全场景与目标中命中不同对象，需检查遮挡，不自动判断哪一个错误。

同名 `.rays.npz` 保存全部射线、扫描命中/法线/三角形、两版全场景和目标的命中 ID/点/距离/法线/三角形、共同掩码。漏射使用 valid=false、空 ID、距离 inf、点/法线 NaN、三角形 -1；文件哈希写入 JSON。`passed=true` 表示比较执行成功，`geometry_quality_passed=null` 表示未自动判定几何验收；阈值与语义结论由任务确定。

固定视角交付检查允许非 isolate 渲染的模型清单随新增对象变化，仍核对相机、bounds、方向、hide_ids、isolate、分辨率、采样及实际可见集合；isolate 模式要求选择集合一致。渲染记录来自原工具，不修改旧记录绕过检查。

### 通用异常诊断摘要

比较报告自动增加 `anomaly_summary`，命令、既有指标及 NPZ 字段保持兼容。`domains.full_scene/target` 各有四类候选：

| 字段 | 查询域与用途 |
|---|---|
| `surface_deviation` | 扫描和新版均命中的全部点，按三维距离降序；`largest_increases` 单独列共同有效域中的误差增加 |
| `scan_hit_model_miss` | 扫描命中、新版漏射，含持续存在的遗漏；不以新版有效掩码过滤 |
| `scan_miss_model_hit` | 扫描未命中、新版命中；扫描洞、推断面和真实错误均可能出现 |
| `hit_identity_change` | 两版均命中但对象 ID 不同；可能是改名、相邻部件或背景切换 |

每类给出全部射线的 `count`、最多6条 `examples`、最多6个 `hit_pair_groups`（每组最多2个例子）及省略的组数。分组按前后命中对象对，给出完整组的位置范围；这是定位辅助，不是语义分割或空间连通聚类。没有误差分数的覆盖类按空间顺序分散选例；偏差类按误差排序。例子包含 `ray_index`、起点、方向、扫描/前后落点、对象 ID 及三维误差，漏射或不可比较值用 null；完整数据由同报告的 NPZ 索引获取。位置范围优先用扫描落点，否则用新命中、旧命中或射线起点。

两个覆盖类另报 `before_count / new_count / persistent_count / resolved_count`。限制的是展示数量，不截断计数；四类可能重叠，不能相加成失败率。摘要距离统一为欧氏米制，不替换 `--component` 指定的原指标。零偏差和浮点微小变化也可能入选，摘要无默认几何验收阈值、不会改变 `passed` 或自动关闭问题；对象改名、扫描孔洞和遮挡需要人工核实。实现哈希和前后模型哈希随报告保存。

工具只分析已有查询，不能保证查询外的完整性。按 skill 的来源覆盖清单发现未建对象或未查特征；补充查询在两版运行、与原固定域区分。按证据关闭问题时引用该模型版本的报告和射线位置，而不是仅引用总体均值或执行成功。


## 模型级诊断

固定射线域只回答被瞄准的位置。以下两个命令面向整个模型，输出目录必须为新目录；报告绑定模型 SHA-256 与版本，`passed` 只表示执行成功，`geometry_quality_passed` 恒为 null。

```text
restruct residuals build-r3/model.glb --startup startup.json --out diag-r3-residuals [--spacing 0.04 --threshold 0.05 --min-area 0.02 --bounds x0 y0 z0 x1 y1 z1 --excess-cameras 160 --excess-margin 0.05]
restruct check-geometry build-r3/model.glb --startup startup.json --out diag-r3-geometry [--tolerance 0.005 --min-overlap 0.0025]
```

`residuals` 在扫描网格上按面积均匀采样，计算到模型表面的距离。超过阈值的采样先按法向分为 up/down/vertical/slanted 四类，再在每类内按体素连通聚簇，相邻但朝向不同的面（台面与柜门、墙）因此不会合成一个混合簇。每簇给出面积、包围盒、朝向、距离分布、最近模型对象、`along_normal`（沿扫描法向两侧 0.3 m 内是否有模型面：无模型面的比例和有面时的中位间距），以及 `hint`：中位距离超过 0.25 m 记为 `unmodelled_region`；否则若过半采样沿法向没有模型面，记为 `missing_surface_near_model`（紧邻已有对象处缺了一块面）；其余记为 `offset_surface`。每簇还给出实际看得到它的照片：要求在画面内且到扫描的射线未被遮挡，同时附带裁图范围。`clusters-*.png` 每簇一格，在最佳照片上以红点标出未解释的采样；`plan.png` 是俯视总览；`residuals.npz` 保存全部采样。`scan_area_within_m` 给出 2/5/10/20 cm 内被模型解释的扫描面积比例。它只能分析扫描到的表面，扫描缺口仍需照片巡视。

`excess` 从反方向检查：在模型表面采样，用均匀抽取的拍摄帧（默认 160 帧）从相机向采样点投射射线并与扫描求交。若扫描交点沿模型面法向落在采样点之后超过 `--excess-margin`，说明这台相机看穿了该模型面。至少 3 台相机、且不少于观测到该点的相机的一半看穿，该点才记为 excess。与面夹角过小的掠射观测（|cos| < 0.25）不计，未命中扫描的射线视为未知。excess 点同样按朝向聚簇，每簇给出所属对象、看穿深度和看穿相机数的中位数、可见照片；`by_object_m2` 按对象汇总面积，`excess-*.png` 在照片上标出模型面的位置。`plan_fit` 在平面上比对每个非建筑对象的朝上面与同高度（±2.5 cm）的扫描朝上面：两者以 1 cm 栅格化，在 ±15 cm 内搜索使模型格被扫描覆盖最多的水平平移。平移后覆盖率至少 0.8、提升至少 0.04 且平移至少 2 cm 的对象列入 `shifted`（附 `best_shift_m`）；平移后覆盖率仍低于 0.8 的列入 `poor_fit`（高度、尺寸或形状不符，也可能是被遮挡的面或扫描上有杂物，只保留前 20 个）。`facing_fit` 比对每个非建筑对象（平面跨度至少 0.25 m，跳过桌腿等细杆）的平面朝向：模型竖直面的主方向与两种参照按 90° 取模比较。一是对象外扩 12 cm 内、未被建筑和其他对象解释的竖直扫描面，取 ±5° 窗口内票数最多的方向，至少占这些样本的 35%；二是中心落在对象外扩 15 cm 内、至少 8 帧观测且最长边至少 0.25 m 的最大定向检测框（job 下各感知阶段的 `R_work_from_object`）。竖直面按 90° 取模的面积加权方向强度（exp(4iθ) 的加权平均模长）低于 0.5 的对象（圆形、多边形柱体）没有可比的朝向，列入 `round` 而不比对。任一参照相差 8° 以上即列入 `turned`，除非扫描以至少一半的样本确认模型朝向（此时检测框多半属于相邻物品）。玻璃、屏幕、深色或反光材质会产生扫描空洞，使相机看似穿过真实表面，这类簇要结合照片判断。

`check-geometry` 读取 GLB 中的对象和 `parameter_model`：

- `interference`：一个对象的表面采样位于另一个闭合对象内部、深度超过 tolerance 的对象对，附位置和深度。按类别分为 `object_interference`、`embedded_in_architecture`、`architecture_junction`。同一对象的部件默认不报，判断依据是共同 assembly 根，或扁平 ID 的前两段（如 `furniture/sofa/...`）；需要时加 `--include-same-assembly`。
- `coplanar_overlap`：不同对象之间同向、共面且有重叠的面，附重叠面积。每条在重叠处取至多 8 个样本：`open_sample_points` 是去掉 ceiling/roof 后沿面法向 0.3 m 内没有遮挡的样本数（剖开视图、平面和俯视会看到）；带 startup 时另给 `visible_sample_points`，即拍摄相机能看到的样本数。按两者较大值排序；两者都为 0 也仍是重合面，导出后任何去掉遮挡的视角都会闪烁。
- `shell_leaks`（需 startup）：从拍摄位置向球面方向发射射线，统计未命中模型就离开的射线，并按穿出外壳的位置分组。`within_opening` 列出能解释该组的参数开口。
- `semantic_warnings`：按代码汇总的类别误用，例如类别写成几何类型、非竖直或过小的 wall、非水平的 floor/ceiling。

两者都只给出候选和定位，修改与关闭仍按任务证据进行。
