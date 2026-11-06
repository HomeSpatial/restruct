# 通用工具链与场景任务

适用于 restruct 0.7.7 的扫描启动、prepared 数据、model v2 和 Blender 插件。按输入选择入口：新扫描先生成任务绑定；已有 startup.json 直接读取；已有参数/工程仅执行用户要求的编辑，不重新跑整屋感知。工具包的 `docs/evidence.md` 与插件 `describe()` 是接口依据；不在 skill 中固化 某一场景的对象、帧号、坐标或几何阈值。

## 新场景启动

将原始来源导入统一 `scene.json`（COLMAP/ZIP 使用仓库 `docs/cli.md` 的 import-colmap），确认声明的单位与 up。选择当前执行环境可用的 runtime：容器使用镜像 `/opt/recon/containers/runtime.container.json`，宿主使用配置好的 runtime 文件。

```text
restruct init-task /data/scene/scene.json --runtime /config/runtime.json --out /tasks/new --root photos=/data/photos
```

此命令需要 geometry 额外依赖或配套镜像，执行 CPU 预处理，写入新的 job、startup.json、runtime.local.json 和 evidence.json，模型和 Blender 由后续步骤启动。准备选项由 `--config` 提供，沿用 prepare 的接口；输出目录必须尚不存在。输入校验失败先修正声明或绑定，保留已生成的诊断，再使用新目录；不覆盖旧 job。

startup 的 `evidence_roots/model_roots`、`task_root_name` 和 `commands` 已包含真实绑定。commands 是 argv 数组，不是 shell 字符串：predict 可直接调用；evidence/build/render 的 prefix 需追加 query/model/blend、view 及新输出路径。先检查 `missing_runtime`；初始化成功只表示数据准备与绑定完成，不表示感知或模型验收完成。

`STATE.md` 包含默认外观阶段与材质覆盖表。整屋任务几何稳定后执行 [默认外观流程](blender-materials.md#默认外观流程)；`commands.check_materials_prefix` 已带模型素材 roots，追加 `model.glb --out 新目录`，必要时再加逐材质 `--exceptions` 文件。旧任务缺少该命令时显式拼入 `model_roots`，补齐状态表，不重跑 init-task。

`available_frame_ids` 来自真实准备清单，可能不连续。需要实拍/渲染来源时读取导入 frames 的 sources.note；无来源声明保留未知。点云或 RGB-D 无扫描网格时，evidence 不配置 scan，使用照片、切片或拟合，ray 查询不可用。

这些绑定记录当前执行环境的路径。容器内继续使用相同 /inputs 与 /jobs 映射；搬到宿主时显式重绑。模型资产打包不属于 init-task，不将容器路径直接当作宿主路径。

## 编辑真源和任务范围

当前 `.blend` 内嵌参数与保存的 `.scene.json` 是编辑状态。基线只读；先在任务目录创建基线工程，随后使用 `update_scene(expected_revision=...)` 或下一版本完整参数。`rebuild_scene` 读取工程当前参数，不重新跑感知或旧案例提取器。工具代码维护与场景建模分开：场景任务写自己的 job，不为一个场景改通用代码。

将改动目标具体到对象/共享节点和应保持的条件。不要为了演示编辑而任意平移或改变尺寸；选择证据支持的实际修正。证据不足时保留假设，说明还缺哪张照片、截面或测量。原扫描、M0/M1/M2 结果和来源帧不修改。

## 查证据

从启动文件读取解释器和 roots，使用：

```text
python -B -m indoor_recon evidence evidence-config.json query.json --root NAME=PATH --out new-query-directory
```

所有查询坐标为右手、Z-up、米制 work。每个查询输出 `evidence.json`、输入 SHA-256 和产物哈希清单；输出目录必须新建且在源资产根之外。将查询参数和范围保留，修改前后复用同一组落点/采样域。

按当前问题选择查询，不要求把所有工具跑一遍：

- `photos`：给定 `bounds_work_m`，按 prepared 扫描首次命中点支持数和投影面积返回候选帧。支持数不是语义正确性，也不能证明柜面/门边清晰可见。AABB 穿过相机近面时不会生成误导性的巨大裁图，可缩小查询区域或指定像素范围。
- `crop`：指定 `frame_id` 和工作 bounds，或 `crop_xyxy`；实际读取原图，保存裁图与更新后的 `K_crop`。必须实际查看图片；需要像素射线时使用原图像素或将裁图像素加回 crop 原点。
- `pixel_rays`：给原图像素，返回扫描上的首次命中点、法线、三角形编号和米制距离。未命中为 null。玻璃/孔洞/反射可能命中背景，结合邻近像素和其他视角判断。
- `raycast`：给工作坐标起点、方向和可选最大距离。方向自动单位化，distance 是欧氏距离，不是 camera-Z。
- `section`：给 bounds、axis、offset_m 和 thickness_m，保存点云薄层 PNG 与全部选中点 NPZ；这是点云薄层，不是精确网格交线。
- `plane`：在明确局部 bounds 中拟合主平面，显式给 `distance_threshold_m`；保留支持点和有符号残差。先用照片/截面排除邻近床面、柜面和相邻墙，再解释为目标表面。阈值按数据噪声与用途决定。
- 程序化 PBR 贴图 `indoor_recon.textures.make_texture(kind, stem, colour_srgb_255, **参数)`：生成可平铺的颜色、粗糙度、法线三张图（`<stem>_color.png` 等），颜色中位数等于给定颜色。`kind` 为 `wood_planks`（`plank_width_m`、`plank_length_m`、`joint_m`）、`wood_grain`（木饰面、实木）、`tile`（`tile_m:[宽,高]`、`grout_m`、`grout_srgb_255`）、`fabric`、`leather`、`stone`（台面、水泥）、`plaster`（墙漆）。返回的 `material(root=, base=)` 给出可直接传给 `b.material` 的字段（三张贴图的资产引用、`uv_box_m`、颜色和粗糙度）；图片写在任务目录下，用 startup 的 `task` 根引用。图案沿贴图 u（图片横向）：地板条或木纹方向不沿 x 时，在材质上加 `uv_rotate_deg`（90 即沿 y）；斜放的家具加 `uv_box_space: "object"`，纹理随对象自身坐标轴转动。方向不同的同种材料复制成两个材质。颜色取 `appearance` 的区色，板宽、板长、砖尺寸、缝宽从照片或截面量出。
- `appearance`：给一个表面的 `bounds_work_m`（薄薄包住地面、某面墙或家具面）和朝外的 `normal_work`，把所有能无遮挡看到它的照片像素投到表面采样上，去掉逐帧曝光和白平衡差（逐通道增益，近处正对的视角权重大），按 `cell_m`（默认 5 cm）格汇总颜色，再按色度（与亮度无关的颜色）分区。返回各区面积、`colour_srgb_255`／`base_colour_linear`、工作坐标范围，相邻区之间的直线分界 `boundaries[].segment_work_m`，以及左为实际颜色、右为分区的 `appearance.png`。光照衰减造成的渐变不会分区，木地板／瓷砖／墙漆这类色相或饱和度不同的材料会分开；只差明暗的中性材料（灰墙漆与白瓷砖、白柜）默认分不开：这时加 `lightness_weight: 0.1`，把亮度也纳入分区，它同时会切出阴影，小区要看照片。要接近真实颜色，给 `white_reference`：同一房间、同一光照下的白墙（`bounds_work_m`、朝外 `normal_work`，可给已知 `colour_srgb_255`），颜色按它定白平衡和亮度；天花板常被另一侧的光照亮，不宜作参照。不同区域光源颜色不同（窗光偏冷、灯光偏暖、强光处褪色），同一种材料在不同房间仍可能有差别：按色度相近判断是否同一材料，同一材料取光照最均匀、观测最多的一处的颜色，全屋共用。结果的 `reliability.usable` 为 false 时（参照面观测太少、缩放过大、校正后成片超白、没有分区）不采用其颜色，按 `blocking` 换参照面或范围；`warnings` 中的 `reference_cast`（参照面偏色大，可能不是白面或光照不同）、`reference_scale_large`、`region_weak` 要结合照片判断。

仅点云可使用照片、薄层和拟合；没有扫描网格时 ray 查询明确不可用，不暗中用拟合平面代替扫描落点。

## 编写参数

用 `indoor_recon.authoring.ModelBuilder` 写参数文档，不要每轮重写墙、楼板、开口、盒体的构造函数：

```python
from indoor_recon.authoring import ModelBuilder, source
b = ModelBuilder(scene_id, source=source("measurement", "task", "evidence-arch/evidence.json"))
b.wall("arch/wall/north", (x0, y0), (x1, y1), floor_z, ceiling_z, thickness_m=.1, room_side="right")
b.opening("arch/door/north", "arch/wall/north", 1.0, 1.9, floor_z, 2.05, category="door")
b.slab("arch/floor/living", polygon_xy, floor_z, kind="floor")
b.box("furniture/stool-0/seat", "stool", center, size_m, yaw_degrees=...)
b.write("model-r2.json")          # 内部先 validate
```

圆柱形的腿、杆、管、灯柱用 `b.cylinder(id, category, center, radius_m, length_m, axis=(x, y, z))`，它生成可编辑的正多边形 prism，轴向任意。`category` 是必填参数，没有默认值。墙传入测得的室内面和房间所在侧，厚度自动挤向室外；楼板／天花按测得的顶面／底面挤出；相同坐标共用节点。家具部件用 `furniture/<对象>/<部件>` 的 ID 层级，或用 assembly 作为父对象，诊断据此识别同一件家具。生成后在 Blender 中检查实际导出面的位置。

## 从证据修改参数

记录对象 ID、查询产物、来源帧、拟合/射线支持及推断部分。对象 `sources` 引用证据 JSON 的相对 asset，使用任务根绑定并保留 SHA-256；`review_notes` 说明选择与剩余不确定项。measurement、prediction、inference 分开。

局部 box 尺寸沿父局部轴；共享 surface/opening 节点在 work 坐标。修改共享点前列出依赖面；门窗轮廓需要与宿主共面且在其边界中。楼梯逐级保持非目标标高与轮廓，不能把默认模板当成测量。

## 渲染、保存和复核

```text
python -B -m indoor_recon render edited.blend --runtime runtime.local.json --view view.json --out new-render-directory
# 与照片同视角对照：view.json 写 {"photo_camera": {"K":..., "T_work_from_camera":..., "size":[w,h]}, "resolution":[2w,2h]}，
# 三项取自对该帧的 pixel_rays 结果；渲染图与照片逐像素对齐，可直接叠图
```

view 可按对象取景、显式 hide_ids、设置工作坐标相机方向或 isolate。临时场景渲染不保存到输入工程，返回可见对象、隐藏项、模型版本及图像哈希。前后对照固定 bounds_work_m、方向与分辨率，避免自动取景随对象尺寸改变而掩盖差异；实际查看 PNG，再记录观察。PNG 存在不等于视觉检查通过。剖开预览隐藏项必须声明，必要时另看完整高度/另一方向。

固定视角复核比较相机、bounds、隐藏项、隔离设置与输出设置。全场景渲染中的对象清单允许随模型新增而变化；isolate 模式仍需保持相同选择。

相关验证包括：证据查询前后对照、非目标对象保持、参数版本生效、重建后保持、保存重开、标准 GLB 轴转换、稳定 ID 与内嵌贴图。几何/材质错误不能靠只改渲染角度掩盖。

同域比较自动输出通用异常摘要；字段见 `docs/evidence.md`。模型级诊断用 startup 的 `residuals_prefix` 与 `check_geometry_prefix`，追加模型 GLB 和 `--out 新目录`。

Windows PowerShell 5.1 的 `>` 重定向会把日志写成 UTF-16，读取时显示为乱码。用 `Out-File -Encoding utf8` 或 `Tee-Object` 保存日志，或者直接读取命令写出的 JSON。在现有任务报告维护来源覆盖清单和问题表，按 `reconstruction.md` 的证据关闭规则更新。交付复核只检查证据完整性与工程契约，不会自动判定清单中的语义完整性或近似是否合理。

交付 `.blend / .scene.json / .project.json / .glb / manifest`、证据、前后渲染和简短报告。报告写清改了什么、支持证据、同域前后数值、实际视觉观察、未解决项。拓扑/数值往返通过不等于物理精度通过。
