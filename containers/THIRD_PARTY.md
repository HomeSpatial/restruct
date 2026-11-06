# 第三方来源

该镜像用于本项目本地重建验证，尚未发布到公共仓库。固定源码与安装资源 SHA-256 保存在构建上下文和镜像的 `/opt/resources/sources.json`、`SHA256SUMS`；原许可、NOTICE、包 metadata 与 Blender distribution/license 文件保留在各自安装目录。Python 完整依赖名称、版本和哈希见 `locks/*.txt`，运行环境清单随验收报告导出。

| 组件 | 固定来源与许可记录 |
|---|---|
| NVIDIA CUDA | Dockerfile 中的 `nvidia/cuda` digest；保留基础镜像许可 |
| Ubuntu | Ubuntu 24.04；APT snapshot `20261023T000000Z`，各包版权在 `/usr/share/doc`，实际版本在 `/opt/release/dpkg-packages.txt` |
| uv | `ghcr.io/astral-sh/uv:0.11.31` digest；[MIT/Apache-2.0](https://github.com/astral-sh/uv) |
| CPython | uv 管理的 CPython 3.11.15/3.12.12；各安装保留自己的许可目录 |
| Blender | 官方 5.1.2 Linux x64 tar，GPL；保留发行目录及许可，见 [Blender license](https://www.blender.org/about/license/) |
| SpatialLM | `manycore-research/SpatialLM@8913c44d84a450c53e9340b13317f8cf7144a738`；根 README 的 License 段及各源码头保留，Pointcept 代码声明 Apache-2.0 |
| SpatialLM 权重 | 单独只读挂载，不打包；SpatialLM1.1-Qwen-0.5B 的模型卡声明 CC-BY-NC-4.0，Qwen 及 Sonata 来源见模型卡 |
| Boxer | `facebookresearch/boxer@1f86542dc342a4b1d474c87c97c5d1d6566d9148`；保留根 `LICENSE`（CC-BY-NC-4.0）与 `NOTICE`（含第三方声明） |
| Boxer 权重 | 单独只读挂载；Boxer、OWL-V2、DINOv3 权重沿用各自许可与来源，文件 SHA-256 见 `weights.lock.json` |
| PyTorch / CUDA 扩展及几何依赖 | 锁中完整记录 PyTorch、FlashAttention、torch-scatter、spconv/cumm、Open3D 等版本/来源，安装包内许可保留 |

许可记录来自固定源码及模型卡，不是新增授权。若要商业使用或再分发，按各组件的实际许可和权重授权确定使用范围。
