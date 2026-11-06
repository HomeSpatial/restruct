# M2 参数场景与 Blender 接口

[English](scene-model.md) | [简体中文](scene-model.zh-CN.md)

参数是编辑真源。创建场景后，当前参数保存在 `.blend` 的 `hs_scene_model` 中；`update_scene` 同时修改参数和几何，`rebuild_scene` 使用工程内参数，不重新运行感知或旧提取器。保存同时产生 `.blend`、`.scene.json` 和 `.project.json`，分别保存工程、可迁移参数和本机资产绑定/操作记录。

机器定义为 [model-v2.schema.json](../src/indoor_recon/schemas/model-v2.schema.json)，数值与关系规则在 [scene_model.py](../src/indoor_recon/scene_model.py)。该模块只使用标准库；插件安装包包含同一份模块，Blender 无需安装 NumPy、jsonschema 或模型推理环境。原有 M0/M1 的 v1 契约保留。

## 参数与坐标

文档采用 `schema_version:2, kind:model, coordinate_frame:work_z_up_metres`，包含 `scene_id / revision / vertices / materials / objects / boundary_policy`。

| 几何 | 参数与行为 |
|---|---|
| `surface` | 使用共享 `boundary_ids` 和可选 `holes_ids`；平面可任意倾斜，`thickness_m` 可为零；`offset_m` 是沿轮廓法向的挤出起点，默认 `-thickness/2` |
| `opening` | 用共享节点描述轮廓，`host_id` 指向宿主 surface；宿主实际切洞；`fill:none/panel`，可设置门板厚度与开启角 |
| `box` | 局部轴全长 `size_m`，适合家具板件、柜体、门窗框与包围体 |
| `prism` | 局部 XY 轮廓和孔洞，逐级独立的 `bottom_m / top_m`；支持直跑、非均匀踏步及多边形转角/平台 |
| `assembly` | 通过其他对象的 `parent_id` 组成部件树，根位姿控制整组移动/旋转 |
| `mesh_reference` | 读取不可改写的测量/既有资产，可指定节点、`asset_axes:z_up/gltf_y_up` 和正的无量纲 `scale_xyz`；用于曲面或无法可靠参数化的部分 |

共享节点在工作坐标中，surface/opening 使用恒等位姿且不挂 assembly。节点编辑会重建所有依赖它的表面；宿主墙也依赖其门窗轮廓。其他对象用右手刚体 `T_parent_from_object`，未声明父对象时 parent 即 work，单位都是米。父子变换按矩阵左乘组合；家具尺寸沿局部轴，区别于世界轴包围盒。

轮廓法向由外环整体绕序按右手规则确定，支持凹多边形；循环改变首点不改变法向，反转绕序则反转法向。实体沿法向占据 `offset_m` 到 `offset_m + thickness_m`。例如 +Z 法向地板用 `offset_m=-thickness_m` 将厚度放在测得顶面下方，+Z 法向天花用 `offset_m=0` 将厚度放在测得底面上方。检查实际导出面的位置，而不只检查参数节点。旧实现若曾在凹角处取反法向，更新工具后重建会纠正挤出方向；保留旧工程，在副本重建并核对曾为旧错误补偿的 offset／绕序。

门板角度绕轮廓首点的局部 V 轴旋转。第一边定义局部 U，平面内正交方向定义 V；因此改变首点仍会改变门的铰轴和局部 UV 基底。门轮廓建议按左下、右下、右上、左上排序，使 V 向上。开放门洞可以接触宿主边缘。多块不相连的表面分别建对象，不能把另一块外轮廓当成孔洞。

`scene_model.furniture()` 可将 table/chair/cabinet/shelf/sofa/bed 模板展开为稳定 ID 的显式部件；展开后逐件独立编辑。`scene_model.staircase()` 将给定逐级轮廓/标高展开成 assembly 与 prism，不统一或重新推算踏步尺寸。曲面和历史不稳定轮廓保留参考网格及来源。

每个对象带 `sources`。推测厚度、补全范围和包围体应标记为 inference/prediction 并写 `review_notes`，不视为测量。文档 `revision` 是整体编辑版本；对象 `revision` 记录对象字段的直接修改，共享节点等依赖变化由整体版本覆盖。

## CLI

运行环境在 [runtime.example.json](../config/runtime.example.json) 中增加 `blender.executable` 和 `blender.addon_dir`；后者指向本仓库包含 `run_agent.py` 的 `blender/` 目录，或已安装的 `homespatial_model` 目录。独立克隆及无外部数据演示见 [项目入口](../README.zh-CN.md#快速开始)；打包、API 和 Blender 测试见 [插件说明](../blender/README.md)。

```bash
restruct validate /data/model.json
restruct build /data/model.json --runtime runtime.local.json --root job=/jobs/perception --out /jobs/build-001
```

输出目录必须是新目录，保留已有工程。创建过程不清空其他 Scene，导出只包含当前项目的模型网格。额外编辑可以由 `--requests edits.json` 提供，格式沿用插件的 `{op,args}` 请求数组。

继续已保存工程时，使用整体版本严格相邻的新参数文件：

```bash
restruct build /data/model-r2.json --blend /jobs/build-001/model.blend --runtime runtime.local.json --root job=/jobs/perception --out /jobs/build-002
```

此处已保存工程必须是 revision 1，新文件必须是 revision 2；旧文件不能覆盖更新的工程。只想从工程当前参数重建时调用 `rebuild_scene`，无需重新应用原 JSON。

## Blender JSON/Python API

`homespatial_model.api.dispatch()`、`bpy.ops.homespatial.command()` 和侧栏复用同一接口。以 `describe()` 返回的签名为准：

| 操作 | 用途 |
|---|---|
| `create_scene(model, roots?, source_model_path?)` | 从 v2 参数创建独立 Scene |
| `update_scene(objects?, vertices?, create?, delete?, expected_revision?, materials?)` | 深合并对象字段、修改共享节点、增删对象；材质按 ID 整体替换 |
| `apply_scene(model, expected_revision, roots?)` | 显式应用下一版完整参数 |
| `inspect_scene()` / `inspect_object(id)` | 读取当前参数、层级和工作坐标包围盒 |
| `capture_transforms(object_ids, expected_revision)` | 将界面中的刚体变换及正向 box 缩放回写参数 |
| `rebuild_scene(roots?)` | 从当前参数重建，可重新绑定外部资产根 |
| `validate_scene()` | 验证参数、编辑同步、有限坐标、材质与声明的外壳边界 |
| `save_scene(filepath, overwrite=false)` / `export_scene(...)` | 保存工程/参数或导出标准 GLB |

例如修改柜体左板的局部尺寸：

```python
from homespatial_model import api
current = api.dispatch({"op": "inspect_scene"})["data"]["model"]
part = next(o for o in current["objects"] if o["id"] == "cabinet/left")
size = part["geometry"]["size_m"].copy()
size[0] += 0.01
result = api.dispatch({"op": "update_scene", "args": {
    "expected_revision": current["revision"],
    "objects": [{"id": part["id"], "geometry": {"size_m": size}}]
}})
assert result["ok"], result
```

接口先验证下一版完整参数并构建临时几何，再提交变更；无关对象保留原数据块。删除宿主或 assembly 时须显式处理相关对象，悬空引用会拒绝。`expected_revision` 可防止旧状态覆盖新编辑；成功更新整体版本加一。

侧栏可回写所选变换、重建、保存和导出。手工顶点、材质或不支持的变换修改会在重建/导出前报 `UNRECORDED_EDIT`，保留当前数据，要求显式参数编辑；不承诺任意网格操作自动反推。旧 `set_dimensions/partition_material` 继续用于 v1 网格工程，v2 使用参数操作，避免修改仅留在网格中。

## 材质、保存与导出

材质支持 sRGB 颜色、粗糙度、金属度、alpha，以及颜色/粗糙度/法线贴图的资产引用。`source_mesh` 可保留既有网格的 PBR 材质，`uv_work` 可声明两行工作坐标仿射 UV（单一平面投影，适合地面和一面墙）；`uv_box_m:[宽,高]` 按每个面的主法向轴做盒式投影（朝上下的面 u=x、v=y，朝 x 的面 u=y、v=z，朝 y 的面 u=x、v=z，各除以贴图的实际尺寸），家具各面不拉伸，二者只能选一；`uv_box_space:"object"` 改用对象自身坐标轴投影，纹理随转动的家具一起转，`uv_rotate_deg` 把图案方向从 u 朝 v 转过给定角度（90 即木纹沿 y）；改变引用材质时整体替换其规格。粗糙度与法线使用数据色彩空间，法线采用 OpenGL 切线空间约定。

保存将相关图片打包到 `.blend`，导出图片内嵌 GLB。已保存的几何可在源资产离线时重开、查看和导出；从参数重新生成参考网格仍需要原资产，搬移后可用 `rebuild_scene(roots=...)` 重绑。

v2 GLB 使用标准 Y-up 米制，manifest 和 GLB extras 明确记录 `T_work_from_gltf`。extras 还内嵌完整参数、版本和稳定 ID/父子关系。v1 特殊 ROS Z-up 导出保持原行为，二者坐标不能混用。

`boundary_policy:open` 保留开放扫描边界，不强行补齐；`closed` 检查声明为 `role:enclosure` 的面片重组是否闭合。拓扑使用与 float32 表达精度相称的数值焊接距离，不作为扫描贴合容差。独立实体、共享中面和实体厚度的交叠需要按用途审查；闭合不等于可通行或碰撞正确。

自包含验证入口见 [测试与支持范围](testing.zh-CN.md)。真实扫描精度须对当前输入单独评估。
