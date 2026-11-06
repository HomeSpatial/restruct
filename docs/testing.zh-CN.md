# 测试与支持范围

[English](testing.md) | [简体中文](testing.zh-CN.md)

核心声明 Python 3.11+；自动化测试矩阵限定 Windows/Linux、Python 3.11/3.12。
Blender 插件声明最低 4.2，当前真实集成验证目标为 **5.1.2**。
其他 Python、Blender、macOS 和 GPU 组合尚不视为已验收。

## CPU 测试

```bash
python -m pip install ".[geometry,dev]"
python -B -m unittest discover -s tests -v
python -B scripts/check_public_release.py
python -m build
python -m twine check dist/*
python -B blender/package_addon.py dist/homespatial_model-0.3.1.zip
```

测试使用合成夹具，不需要私有扫描或权重。覆盖契约、坐标、证据查询、模型检查、纹理、交换格式、任务启动、包资源、skill 同步和发布边界。CPU CI 另从 wheel 在源码目录之外运行 CLI。

## 真实 Blender 集成

以下命令在仓库根运行，将 `/path/to/blender` 换成已安装的 Blender。PowerShell 可在带引号的可执行路径前加 `&`。

```bash
/path/to/blender --background --factory-startup --python-exit-code 1 --python blender/tests/test_parameter_scene.py -- --contract-src src --mode fixture --out artifacts/fixture
python -B blender/tests/verify_parameter_exports.py --output artifacts/fixture
/path/to/blender --background --factory-startup --python-exit-code 1 --python blender/tests/test_installed_parameter_addon.py -- --zip dist/homespatial_model-0.3.1.zip --blend artifacts/fixture/edited.blend --exclude src --out artifacts/installed
/path/to/blender --background --factory-startup --python-exit-code 1 --python blender/tests/test_review.py -- --contract-src src --blend artifacts/fixture/edited.blend --out artifacts/review
```

这些检查创建/编辑、保存/重开、重建、GLB 坐标与 PBR、实际门洞、离线导出、安装 ZIP 的独立性及临时 review 不改原工程。
Blender workflow 手动触发并独立下载经官方 SHA-256 校验的 5.1.2；CPU CI 不下载 Blender。

## 未覆盖的验收

GPU 模型推理、数据集重建质量、完整室内覆盖、导航和碰撞精度属于单独验收。仓库已加入 [ScanNet++ 本地案例记录与截图](../cases/scannetpp-09c1414f1b/README.md)，但没有分发真实数据或通用基准测试。测试通过不意味着自动重建达到了用户所需的建筑精度。
