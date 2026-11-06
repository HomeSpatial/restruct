# Changelog

## 0.7.7 — appearance workflow and evidence viewer

- Name the public project Restruct, use `homespatial-restruct` for the Python distribution, and replace the pre-release `recon` command with `restruct`.
- Add a four-layer workspace for scans, SpatialLM layouts, Boxer detections and structured models, with source-photo overlays, object parts and revision switching.
- Support explicit local task/model-directory bindings and fixed-width selection outlines in the offline viewer.
- New tasks track appearance evidence, procedural PBR, visual comparison, export and reopen checks in STATE.md.
- `restruct check-materials` checks actual primitive coverage, embedded images, UVs and source pixels, including roughness in G; uniform materials need explicit reasons.
- Delivery review checks final and reopened material exports by default; geometry-only scope must be recorded explicitly.
- Skill 0.7.7 makes the appearance workflow part of whole-scene completion and distinguishes blocked evidence from scope exclusions.
- Document the corrected ScanNet++ r15 PBR case with a viewer screenshot and retained limitations; keep the unrun HouseLayout3D selection as a preparation record.
- Use COLMAP and RTAB-Map as input walkthroughs, explaining the optional rtabmap-hloc/remap handoff, dense-geometry requirements and coordinate checks.
- Bind individually reviewed documentation images to checksums and provenance in the source-release manifest; other binary assets remain excluded.

## 0.7.6 — public-source preparation

- Define Apache-2.0 core, GPL-3.0-or-later Blender component and third-party license boundaries.
- Add a public quick start, synthetic-only examples, contribution/security guidance and automated release checks.
- Archive private case data and internal validation reports outside the public source tree.
- Build core wheel/sdist, a complete-source Blender add-on ZIP and a history-free allowlisted source ZIP with SHA-256 manifests.
- Add CPU/platform CI and a separately triggered Blender integration workflow. Public dataset examples and a public package registry release remain pending.

Blender add-on 0.3.1 updates license packaging; the parameter model remains v2. Skill 0.7.6 removes local case bindings without changing its generic reconstruction method.

## Earlier tool changes

0.6.1 修正新增对象后的固定视角误报，扩展同域射线对比与逐射线命中记录，并完善 skill 的结构/家具核查和修正流程。现有已验收 Docker 镜像仍为 0.6.0；本次源码更新未重建镜像。

0.6.2 的射线比较自动汇总表面偏差、漏射、额外命中及命中对象变化，保留全场景/目标域与完整计数。配套 skill 0.6.4 增加来源到模型的覆盖清单和按证据关闭问题的规则。

0.7.0 增加模型级诊断与建模辅助库：`restruct residuals` 把模型解释不了的扫描表面聚类并给出照片拼图，用来发现漏建和轮廓偏移；`restruct check-geometry` 报告对象穿插、可见共面重叠、从拍摄位置看到的外壳漏缝和类别误用；`indoor_recon.authoring` 提供墙、楼板、开口、盒体等构造函数，类别必须显式给出。`init-task` 另生成 `STATE.md` 作为任务进度真源。配套 skill 0.7.0。接口见 [证据说明](docs/evidence.md#模型级诊断)。

0.7.1 中，`restruct residuals` 的簇按朝向分开计算，并沿扫描法向判断缺面还是偏移；新增反向的 excess 检查，用拍摄相机找出被看穿的模型面（部件偏大、外凸、敞开处建成实体）。`ModelBuilder` 保存精确坐标、只按微米舍入去重，并新增 `cylinder`；M3 交付检查器接受没有冻结 `model_path` 的新建任务。配套 skill 0.7.1。

0.7.2 在 residuals 中增加 `plan_fit`：比对每个对象朝上面与同高度扫描面的平面轮廓，给出台面、座面、层板在平面内错位的平移量。配套 skill 0.7.2。

0.7.3 在 residuals 中增加 `facing_fit`：按扫描立面法向与检测框朝向找出平面内转角错误的对象；共面重叠增加 `open_sample_points`（沿法向无遮挡的样本），被吊顶以外的物体遮住才算不可见。新增 evidence 操作 `appearance`：在线性光下逐帧逐通道求增益、按视角加权，按色度分出材料区并给出各区颜色，可用白参照定绝对色。配套 skill 0.7.3。

0.7.4 新增程序化 PBR 贴图 `indoor_recon.textures.make_texture`（木地板、瓷砖、木饰面、布艺、皮革、石材、墙漆，颜色/粗糙度/法线三图可平铺）和材质盒式投影 `uv_box_m`；`appearance` 增加 `reliability` 与 `lightness_weight`；`facing_fit` 把无正面的圆形对象列入 `round`。skill 增加楼梯逐级照片核对、柜门与嵌入式柜体、玻璃护栏与扶手、深色家电按照片定位、整面墙分色和纹理交付要求。配套 skill 0.7.4。

0.7.5 材质新增 `uv_box_space`（按对象自身坐标轴投影）与 `uv_rotate_deg`（纹理方向）；`restruct render` 的 view 支持 `photo_camera`，按照片相机渲染并与照片逐像素对齐，`pixel_rays` 同时返回该帧 `K`、`T_work_from_camera`、`size`。过程视频的自动剖切按停留时间（`dwell`）合并短片段，避免上下层频繁切换，并可用 `clip=off` 看整栋。配套 skill 0.7.5。
