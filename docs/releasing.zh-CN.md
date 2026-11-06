# 生成公开发行候选

[English](releasing.md) | [简体中文](releasing.zh-CN.md)

核心 wheel/sdist、Blender 插件 ZIP 和完整源码 ZIP 分开分发。当前版本是源码候选，不自动上传 PyPI、创建远端仓库或发布镜像。

## 边界

`release-files.json` 是完整源码的逐文件白名单。打包器只复制清单文件，不复制 `.git`、忽略目录、模型权重、运行产物或未列入清单的文件。私有开发历史不能通过清理当前工作树变为公开历史；首次公开应从生成的源码 ZIP 在新目录初始化 Git，不推送原开发仓库历史。

新增公开文件后显式更新清单，再运行检查。不要通过扫描整个目录并自动加入所有文件来更新白名单。

文档截图需逐项在清单的 `reviewed_assets` 中记录路径、SHA-256、来源说明和已列入发布清单的归属说明文档。检查器仅放行这些字节完全匹配的 PNG/JPEG；替换截图需重新审阅并更新摘要。该记录不授予数据集使用权，原始数据和模型仍按案例政策保留在本地。

```bash
python -B scripts/check_public_release.py
python -B scripts/build_release.py --out /new/path/release
```

输出包含：

- `homespatial_restruct-*.whl`、核心 `.tar.gz`：Apache-2.0 核心与 MIT three.js。
- `homespatial_model-*.zip`：GPL-3.0-or-later 插件完整源码及 Apache 共享模块的通知。
- `homespatial-restruct-*-source.zip`：可独立运行测试/打包的白名单完整源码。
- `SOURCE_MANIFEST.json` 与 `SHA256SUMS`：源码和发行文件的摘要。

构建在独立临时目录完成，并校验 wheel/sdist 内容与许可元数据。输出目录必须不存在；失败的部分输出保留用于诊断，不覆盖已完成候选。

## 发布前在目标主机完成

1. 用源码 ZIP 新建无私有历史的仓库，审阅文件与许可通知。
2. 在公开主机启用 CI 和私密漏洞报告；实际跑通 CPU 与 Blender workflow。
3. 项目名为 Restruct，目标仓库为 `HomeSpatial/restruct`；包元数据已使用该地址。首次推送后核实项目、文档与 issue 链接可用。
4. 按 [样例政策](../cases/README.md)逐个加入许可允许的公开数据集演示，并记录真实质量限制。

发布包不会包含模型权重或完整 GPU 容器。可选后端沿用各自授权，见 [第三方清单](../THIRD_PARTY_NOTICES.md)。
