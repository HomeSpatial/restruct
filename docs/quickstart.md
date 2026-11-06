# 快速入门

此示例是仓库内手工构造的合成房间，不含真实建筑或照片。安装核心后即可验证参数；构建和渲染另外需要 Blender。核心 0.7.7、插件 0.3.1、skill 0.7.7 配套，参数格式为 model v2。

## 安装

在仓库根目录创建并激活 Python 3.11 或 3.12 虚拟环境：

```bash
python -m venv .venv
python -m pip install .
restruct --help
restruct validate blender/examples/room.json
```

第二行之前先激活环境：Linux/macOS 执行 `source .venv/bin/activate`，Windows PowerShell 执行 `.venv/Scripts/Activate.ps1`。当前发行候选从源码或附带 wheel 安装；不要假定 PyPI 上的同名包就是此项目。

## 配置 Blender

安装 Blender 5.1.2（当前验证版本），将 [配置示例](../config/runtime.blender.example.json) 复制为仓库根的 `runtime.local.json`。填写 Blender 可执行文件以及本仓库 `blender/` 的绝对路径。Windows JSON 路径可使用 `/`。配置保留在本机，不提交到仓库。

```json
{
  "blender": {
    "executable": "/absolute/path/to/blender",
    "addon_dir": "/absolute/path/to/restruct/blender"
  }
}
```

CLI 直接运行独立 Blender 进程，不要求安装插件到全局配置。若需要侧栏界面，可以打包并在 Blender 的 Install from Disk 中安装：

```bash
python -B blender/package_addon.py dist/homespatial_model-0.3.1.zip
```

安装包自带纯标准库参数模块和完整许可文件。安装后也可将 `addon_dir` 改为安装的 `homespatial_model/` 目录。

## 创建、编辑、查看

```bash
restruct build blender/examples/room.json --runtime runtime.local.json --out artifacts/demo/create
restruct build blender/examples/room.json --runtime runtime.local.json --requests blender/examples/room-edit.json --out artifacts/demo/edited
restruct render artifacts/demo/edited/model.blend --runtime runtime.local.json --view blender/examples/review.json --out artifacts/demo/render
restruct view artifacts/demo --open
```

构建输出包括 `model.blend`、`model.glb`、参数副本、构建报告与日志。原始参数和前次构建保持不变；每次使用新的输出目录，重复运行时更换 `demo` 名称。第二次构建执行桌面尺寸更新，报告应为 revision 2。渲染隐藏天花和南墙以便看到内部。

默认查看地址为 `http://127.0.0.1:8770/`，有冲突时使用 `--port`。查看器支持类别显隐、墙体剖开、高度剖切、线框和第二模型叠加；库随包内置，可以离线运行。

工作参数是右手 Z-up 米制；导出的标准 GLB 为 Y-up，坐标变换保存在元数据中。保存、重开和导出通过证明工具流程可用，不证明任意真实扫描的几何质量。

## 使用自己的扫描

安装 `python -m pip install ".[geometry]"`，先按 [COLMAP / RTAB-Map 输入示例](input-data.md)获取并整理数据。已有独立描述文件时使用[数据交接](exchange-workflow.md)。用 `restruct inspect` 和预检确认资产、单位、轴向和相机绑定，再使用 `restruct init-task`。

感知模型是可选步骤，源码/权重和独立运行环境需自行配置；见 [许可说明](../THIRD_PARTY_NOTICES.md)。最终结构需要结合证据修正，可使用仓库内 [重建 skill](../skills/reconstruct-structured-interiors/SKILL.md)。
