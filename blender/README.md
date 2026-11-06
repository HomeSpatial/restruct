# HomeSpatial Structured Model · Blender 插件 v0.3.1

面向 Agent 的结构模型创建与编辑接口。v0.3 增加临时 review 渲染，v0.2 增加通用参数场景；v0.1 的导入与网格编辑接口保持兼容。核心函数不依赖鼠标选择或区域布局，面板和 JSON 操作符复用同一接口。当前自包含集成检查使用 Blender 5.1.2。

插件源码位于 `restruct` 仓库的 `blender/`，与核心一起克隆和维护，不依赖 Unity 工程、Unity Editor 或 Unity Python 包。运行接口需要 Blender 自带的 Python；模型目录和扫描是外部输入，不随插件源码复制。

## 安装与入口

安装包包含 `homespatial_model` 目录。在 Blender Preferences → Add-ons → Install from Disk 安装 ZIP，启用 HomeSpatial Structured Model。面板位于 3D View 的 N 侧栏 → HomeSpatial。

Agent 也可以直接使用源码，不必安装或修改 Blender 全局设置：把本目录加入 Blender Python 的 `sys.path` 后导入 `homespatial_model.api`。图形界面操作符需要调用一次 `homespatial_model.register()`；纯 API 不需要注册面板。

```python
import sys
sys.path.insert(0, "/absolute/path/to/restruct/blender")
sys.path.insert(0, "/absolute/path/to/restruct/src")
from homespatial_model import api
result = api.dispatch({"op": "describe"})
```

这段代码可以通过现有 Blender MCP 的 Python 执行工具调用。没有新增网络服务、监听端口或第三方运行依赖。

## Agent 调用约定

请求：`{"op": "操作名", "args": {...}, "request_id": "可选调用编号"}`。

成功返回 `{"ok": true, "request_id": ..., "data": ...}`；参数或业务错误返回 `{"ok": false, "error": {"code": ..., "message": ...}}`。未知参数会拒绝，避免拼写错误静默失效。`validate` 的调用成功与检查通过分开：检查结论看 `data.passed`。

### v0.2 参数场景

`create_scene(model, roots?)` 接收 restruct 的 model v2，支持共享墙地顶节点、实际宿主门窗开口、家具部件树、任意姿态 box、逐级多边形棱柱和测量网格引用。`update_scene` 修改参数后构建新几何，带 `expected_revision` 可拒绝过期编辑；`rebuild_scene` 使用 `.blend` 内当前参数，保存/重开不会回到原始生成器配置。

主要操作为 `create_scene / update_scene / apply_scene / inspect_scene / capture_transforms / rebuild_scene / validate_scene / save_scene / export_scene`，准确签名见 `describe()`。现有 `list_objects / inspect_object / validate / save_project / export_glb` 也识别参数场景。参数场景的局部尺寸与共享节点通过 JSON 编辑；侧栏可以回写选中对象的刚体变换和正向 box 缩放，再重建、保存或导出。未回写的网格/材质改动会明确报错并保留，不会在重建时静默覆盖。

参数对象的稳定 ID、版本、来源和部件关系写入工程及 GLB extras。保存同时写 `.scene.json` 参数和 `.project.json` 绑定/日志，并打包贴图。v2 导出采用标准 glTF Y-up，明确记录 `T_work_from_gltf`；旧 v1 工程继续保留原 ROS 导出。已保存的几何支持原资产离线时重开导出，重新生成参考网格仍需原资产。

完整契约与 CLI 见 [参数场景说明](../docs/scene-model.md)。直接导入 API 时将本仓库 `src/` 加入 Blender Python `sys.path`；源码 `run_agent.py` 自动定位仓库 `src/`，也可用 `--contract-src` 显式覆盖；完整安装 ZIP 自带纯标准库参数模块，不需要安装 jsonschema 或模型依赖。

### v0.3 证据与渲染

`render_review(filepath, object_ids?, hide_ids?, direction_work?, bounds_work_m?, resolution?, samples?, isolate?)` 在临时 Scene 中渲染当前项目网格副本，返回 PNG、模型/图像哈希、版本、固定相机及可见/隐藏对象；清理后恢复原 Scene，不改参数、网格或保存的工程。指定相同 `bounds_work_m`、方向和分辨率可固定前后对照相机。已有 PNG 不覆盖，未记录的参数工程编辑会拒绝渲染。

扫描射线、照片投影/实际裁图、点云薄层和主平面拟合由核心 CLI `restruct evidence` 提供，prepared 与原扫描只读。CLI `restruct render` 在独立 Blender 进程调用本接口并核对输入工程哈希。查询格式与场景交付约定见 [M3 证据工具](../docs/evidence.md)。

### v0.1 导入与网格编辑

以下 bundle、世界轴尺寸和网格材质分区约定继续用于原有 v1 工程。参数场景使用上面的版本化编辑接口。

先调用 `open_project` 确定项目，再通过稳定对象 ID 操作。多个项目可以存在不同 Scene；`open_project` 会切换到指定项目，重复打开同一目录保留已有修改。

| 操作 | 主要参数 | 返回内容 |
|---|---|---|
| `describe` | 无 | 接口签名、材质字段、坐标与编辑契约 |
| `open_project` | `bundle_dir`, `reference_glb?` | 项目 ID、对象数、照片数 |
| `list_objects` | `category?` | 稳定 ID、名称、分类、结构类型 |
| `inspect_object` | `object_id` | 米制包围盒、尺寸、材质参数、内嵌贴图、UV、照片编号 |
| `photos` | `object_id`, `limit=6` | 关联照片路径、已声明来源、投影和可见性采样分数 |
| `open_photo` | `frame_id` | 加载照片到图像数据块，更新已打开的 Image Editor，返回路径与尺寸 |
| `set_dimensions` | `object_id`, `dimensions_m`, `anchor` | 修改前后尺寸、固定端 |
| `partition_material` | `object_id`, `axis`, `offset_m`, `negative?`, `positive?` | 精确分区结果和受影响的共享边对象 |
| `validate` | 无 | 错误、警告、建筑外壳开放边和非流形边 |
| `export_glb` | `filepath`, `overwrite=false` | GLB 路径、检查结果、内嵌贴图数；同时写出 `.manifest.json` |
| `save_project` | `filepath`, `overwrite=false` | `.blend` 与 `.project.json` 编辑记录 |

### 导入与坐标

模型目录需要 `structure.json`、`model-manifest.json` 和 `structured.glb`。照片来自 `evidence/frames.json` 与 `evidence/rgb/<id>.png`。V1 支持目录中声明的 `ROS z-up, original origin`、米制模型。

导入时处理 Blender 的 glTF 轴转换，再应用 `aligned_frame_from_ros`。编辑接口中的坐标、分界位置和尺寸均为对齐后的 Z-up 米制坐标。导出恢复原始 ROS 坐标，保留当前模型现有的坐标约定，并在 GLB extras 标注。它不自动改成通常的 glTF Y-up 空间。

原始扫描可作为独立参考层导入；默认锁定选择并隐藏，永不进入模型导出。现有 Blender 场景不会被清空。

### 尺寸编辑

```json
{"op":"set_dimensions","args":{"object_id":"furniture_sofa_side_table","dimensions_m":[0.65,null,null],"anchor":"MIN"}}
```

`dimensions_m` 是沿对齐世界坐标轴的包围盒尺寸，`null` 保留该轴。`MIN` 固定最小端，`CENTER` 固定中心，`MAX` 固定最大端。修改目标的独立网格，避免共享网格连带改变其他对象。V1 不自动修改相连墙体或整组家具；建筑尺寸变化后必须通过检查才允许导出。

### 材质分区

```json
{"op":"partition_material","args":{"object_id":"enclosure_wall_living_gray","axis":"X","offset_m":-1.73,"negative":{"base_color_srgb":[0.67,0.69,0.67],"roughness":0.8},"positive":{"base_color_srgb":[0.84,0.84,0.80],"roughness":0.8}}}
```

沿指定平面切分跨界面片，再分配材质，分界不会跟随旧三角形斜边。建筑对象相邻面的匹配共享边同步插入切点，保持外壳拓扑闭合；既有表面位置不移动。省略一侧时保留该侧原材质。

材质支持 `name`、`base_color_srgb`（三个 0～1 值）、`roughness`、`metallic`，以及 `base_color_texture`、`roughness_texture`、`normal_texture` 的本地绝对路径。粗糙度输入为灰度贴图，法线为 OpenGL 切线空间贴图；图片会打包进材质。沿用已有 UV，不自动重新展开 UV。面板提供颜色与粗糙度快捷项；完整贴图参数通过 API 设置。

### 检查、保存与导出

检查有限坐标、稳定 ID、单位、贴图可用性和重组后的建筑外壳闭合性。退化三角形列为警告。这里检查模型结构，不替代与原始扫描的精度评估，也不证明 NavMesh 可通行。

导出使用临时场景，只包含当前项目模型对象；参考扫描、其他场景、灯光与相机均排除。稳定 ID、分类、来源数据保存在 extras；PBR 贴图内嵌 GLB。源模型和扫描不允许原地覆盖，已有输出需要明确传入 `overwrite=true`。

`.blend` 是插件编辑后的工程源，`.project.json` 保存来源、坐标与按序编辑记录。原来的 `structure.json` 不会被回写，旧生成器也不会自动吸收 Blender 修改。批量命令遇错停止，已经完成的命令不会自动回滚；可先保存检查点并重开恢复。

## 后台 CLI

`run_agent.py` 接收单个请求或请求数组。使用 Blender 自带 Python，无需系统 Python 安装 `bpy`。

```powershell
& 'D:\Program Files\Blender Foundation\Blender 5.1\blender.exe' `
  --background --factory-startup --python-exit-code 1 `
  --python '.\run_agent.py' -- `
  --request '.\request.json' --response '.\response.json'
```

以上命令在本文件所在的 `blender/` 目录运行。继续已保存工程时加 `--blend 'E:\path\model.blend'`。批量命令不依赖 UI 当前选中物体。无需外部数据的参数场景演示见 [快速入门](../docs/quickstart.md)。

## 打包

在仓库根目录运行，使用普通 Python 即可，不需要 Blender：

```powershell
python -B blender/package_addon.py dist/homespatial_model-0.3.1.zip
```

安装目录中的插件是独立副本。修改本项目源码后，需重新打包安装或由 Agent 直接加载本项目源码，已安装副本不会自动更新。

打包器默认将同一仓库的 `src/indoor_recon/scene_model.py` 原文件放入 `_vendor`；`--contract-source` 可显式覆盖来源，不维护第二套实现。安装包同时包含 `run_agent.py`，`restruct build` 可将 `addon_dir` 指向已安装的 `homespatial_model` 目录。包中同时包含 GPL、Apache 许可全文及组件通知。

## 验证

从仓库根目录可运行自包含构造场景，不使用历史扫描或权重：

```powershell
& '/path/to/blender' --background --factory-startup --python-exit-code 1 --python blender/tests/test_parameter_scene.py -- --contract-src src --mode fixture --out artifacts/fixture
python -B blender/tests/verify_parameter_exports.py --output artifacts/fixture
```

第二条使用核心的 geometry 依赖，独立检查 GLB 坐标、PBR、门窗和楼梯。完整安装包、离线重开和 review 渲染检查见 [测试说明](../docs/testing.md)。

凹多边形法向与带厚度表面的回归：

```powershell
& '/path/to/blender' --background --factory-startup --python-exit-code 1 --python blender/tests/test_surface_winding.py -- --contract-src src --out artifacts/surface-winding
```

检查正反绕序、所有起点、正负 offset、凹口与孔洞、实体体积，以及保存重建和实际 GLB 重新导入后的表面位置。


## 许可

本目录的原始插件代码、工具和示例采用 [GPL-3.0-or-later](LICENSE)，见 [NOTICE](NOTICE)。打包的 `_vendor/scene_model.py` 保持 Apache-2.0；ZIP 附其许可全文和通知。发行 ZIP 包含完整 Python 源码，打包方法由本目录 `package_addon.py` 提供。
