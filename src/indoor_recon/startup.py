# SPDX-License-Identifier: Apache-2.0
"""Initialize a new scene task without starting inference or Blender."""
import copy
from pathlib import Path, PureWindowsPath
import sys

from . import __version__
from .contracts import ContractError, read_json, resolve_asset, sha256, validate, validate_scene_bundle
from .jobs import atomic_json


def _runtime(path):
    path = Path(path).resolve()
    value = read_json(path)
    if not isinstance(value, dict):
        raise ContractError("Runtime must be a JSON object")
    # Paths in a reusable runtime are relative to that configuration, not a later task cwd.
    fields = {"python", "source_dir", "model_dir", "weights_dir", "executable", "addon_dir", "labels_file"}
    for backend, binding in value.items():
        if not isinstance(binding, dict):
            raise ContractError(f"Runtime binding must be an object: {backend}")
        for key in fields.intersection(binding):
            item = binding[key]
            if not isinstance(item, str) or not item.strip():
                raise ContractError(f"Runtime {backend}.{key} must be a nonempty path/command")
            foreign_absolute = item.startswith("/") or PureWindowsPath(item).is_absolute()
            command_name = key in {"python", "executable"} and "/" not in item and "\\" not in item
            if not foreign_absolute and not command_name:
                binding[key] = str((path.parent / item).resolve())
    return value


def _add_root(roots, preferred, path):
    path = str(Path(path).resolve())
    name = preferred
    index = 2
    while name in roots and roots[name] != path:
        name = f"{preferred}-{index}"
        index += 1
    roots[name] = path
    return name


STATE_TEMPLATE = """# 任务状态：{scene_id}

每轮修改后更新本文件；上下文重置、换代理或继续任务时先读它。只记录当前事实，历史放在各轮报告里。

## 目标与验收（开始建模前写定）

- 用户目标与范围：
- 结构：
- 主体家具：
- 对象关系（穿插、漏缝、共面重叠）：
- 外观：整屋重建默认包含取色、PBR、同视角复核和实际导出检查；仅用户明确的纯几何／诊断任务可不适用，写明范围依据。
- 交付：参数、Blender、GLB、证据绑定

## 当前接受版本

- 模型版本 / build 目录 / GLB SHA-256：
- 最近一次 residuals 与 check-geometry 报告：

## 覆盖清单

| 来源区域／观察项 | 来源证据及类型 | 对应模型 ID 或未建 | 待核实特征与检查覆盖 | 当前状态／问题 ID |
|---|---|---|---|---|

## 问题表

| ID | 对象／位置 | 失败证据 | 拟修改参数 | 复测方法 | 状态 | 关闭证据（最终版本） |
|---|---|---|---|---|---|---|

## 外观阶段（几何稳定后、最终交付前）

| 步骤 | 状态（待做／通过／阻塞／不适用） | 产物／证据与当前版本 | 未完成原因 |
|---|---|---|---|
| appearance 取色与可靠性筛选 | 待做 | 各层地面、墙面和主要家具；低可靠结果不用作实测色 | |
| 程序化 PBR 生成与赋材质 | 待做 | 颜色、粗糙度、法线；米制尺寸、UV 方向、推断项 | |
| 同视角照片与渲染复核 | 待做 | 材质边界、纹理方向、尺度、明暗 | |
| check-materials 导出检查 | 待做 | 当前 GLB 哈希；内嵌图片、通道、UV、来源像素 | |
| 保存重开与查看器复核 | 待做 | 重开导出报告和实际显示观察 | |

材质覆盖（同种材料可共用一行，但列出覆盖区域；玻璃／屏幕等纯色例外逐项说明）：

| 区域／对象 ID | material_id／材料类型 | 取色证据／可靠性／采用或推断理由 | PBR 文件／米制 UV | 视觉复核／导出检查 |
|---|---|---|---|---|

外观阶段未完成时不能标记整屋重建完成。缺照片或取色不可靠时记录阻塞或明确推断，不把缺证据自动当作纯几何范围。

## 下一步
"""


def initialize(scene_path, output, runtime_path, roots=None, options=None,
               layout="spatiallm", objects="boxer", labels_file=None, exchange_selection=None):
    """Prepare new inputs and write runnable task bindings; existing outputs are never reused."""
    from .preparation import prepare

    scene_path, output = Path(scene_path).resolve(), Path(output).resolve()
    if output.exists():
        raise ContractError("Task output already exists; choose a new directory")
    raw = read_json(scene_path)
    if raw.get("kind") in ("reconstruction_description", "reconstruction_bundle"):
        from .exchange import import_description
        # Validate the runtime file before materializing the import.
        _runtime(runtime_path)
        import_description(scene_path, output / "input", roots, **(exchange_selection or {}))
        scene_path = output / "input/scene.json"
        roots = {}
        imported = read_json(scene_path)
        options = {**({"alignment": "preserve"} if imported["source_coordinates"]["up_axis"] == "+Z" else {}),
                   **(options or {})}
    scene = validate(read_json(scene_path))
    if scene["kind"] != "scene":
        raise ContractError("init-task requires a scan scene v1, not a model or legacy Blender bundle")
    roots = {key: Path(value).resolve() for key, value in (roots or {}).items()}
    roots.setdefault("bundle", scene_path.parent)
    validate_scene_bundle(scene, roots)
    runtime = _runtime(runtime_path)
    config = dict(options or {})
    geometry_id = config.get("geometry_id")
    geometry = next((g for g in scene["geometry"] if g["id"] == geometry_id), None) if geometry_id else next(iter(scene["geometry"]), None)
    if geometry_id and geometry is None:
        raise ContractError("Unknown geometry_id")
    already_work = geometry and geometry["coordinate_frame"] == "work"
    if not already_work and scene["source_coordinates"]["metres_per_unit"] is None:
        raise ContractError("Metric source scale is required; provide the source declaration")
    if not already_work and scene["source_coordinates"]["up_axis"] == "unknown":
        raise ContractError("Source up axis is required before preparation")

    job = output / "job"
    output.mkdir(parents=True, exist_ok=True)
    prepare(scene_path, job, roots, config)
    prepared_path = job / "prepare/prepared.json"
    prepared = read_json(prepared_path)
    evidence_roots = {name: str(path) for name, path in roots.items()}
    job_alias = _add_root(evidence_roots, "job", job)
    evidence = {"schema_version": 1, "kind": "evidence", "scene_id": scene["scene_id"],
                "prepared": {"root": job_alias, "path": "prepare/prepared.json", "sha256": sha256(prepared_path)}}
    if geometry and geometry["type"] == "mesh":
        asset = copy.deepcopy(geometry["asset"])
        asset["sha256"] = sha256(resolve_asset(asset, roots))
        evidence["scan"] = {
            "asset": asset,
            "metres_per_unit": 1 if already_work else scene["source_coordinates"]["metres_per_unit"],
            "T_work_from_source_m": [[int(i == j) for j in range(4)] for i in range(4)] if already_work else prepared["alignment"]}
    model_roots = dict(evidence_roots)
    task_alias = _add_root(model_roots, "task", output)
    runtime_file = output / "runtime.local.json"
    evidence_file = output / "evidence.json"
    atomic_json(runtime_file, runtime)
    atomic_json(evidence_file, evidence)
    calibrated = [f["id"] for f in prepared["frames"]]
    capabilities = {"colored_layout": bool(prepared["has_color"]), "calibrated_rgb": bool(calibrated),
                    "scan_rays": "scan" in evidence}
    missing = [name for name, needed in (
        ("spatiallm", capabilities["colored_layout"]), ("boxer", capabilities["calibrated_rgb"]),
        ("blender", True)) if needed and not runtime.get(name)]
    cli = [sys.executable, "-B", "-m", "indoor_recon"]
    evidence_flags = [item for name, path in evidence_roots.items() for item in ("--root", f"{name}={path}")]
    model_flags = [item for name, path in model_roots.items() for item in ("--root", f"{name}={path}")]
    from .perception import prediction_plan
    prediction = prediction_plan(prepared, runtime, layout, objects, labels_file)
    predict_command = [*cli, "predict", str(job), "--runtime", str(runtime_file),
                       "--layout", layout, "--objects", objects]
    if prediction["labels_file"]:
        predict_command += ["--labels-file", prediction["labels_file"]]
    startup = {"schema_version": 1, "kind": "task", "scene_id": scene["scene_id"], "tool_version": __version__,
               "source_scene": str(scene_path), "source_scene_sha256": sha256(scene_path),
               "python": sys.executable, "core_src": str(Path(__file__).resolve().parents[1]),
               "runtime": str(runtime_file), "job": str(job), "output_root": str(output),
               "evidence_config": str(evidence_file), "evidence_roots": evidence_roots,
               "model_roots": model_roots, "task_root_name": task_alias,
               "model_schema_version": 2, "available_frame_ids": calibrated,
               "capabilities": capabilities, "missing_runtime": missing,
               "prediction_preflight": prediction,
               "commands": {
                   "predict": predict_command if prediction["passed"] else None,
                   "predict_preflight": [*predict_command, "--preflight"],
                   "evidence_prefix": [*cli, "evidence", str(evidence_file), *evidence_flags],
                   "build_prefix": [*cli, "build", "--runtime", str(runtime_file), *model_flags],
                   "render_prefix": [*cli, "render", "--runtime", str(runtime_file)],
                   "residuals_prefix": [*cli, "residuals", "--startup", str(output / "startup.json")],
                   "check_geometry_prefix": [*cli, "check-geometry", "--startup", str(output / "startup.json")],
                   "check_materials_prefix": [*cli, "check-materials", *model_flags]}}
    atomic_json(output / "startup.json", startup)
    (output / "STATE.md").write_text(STATE_TEMPLATE.format(scene_id=scene["scene_id"]), encoding="utf-8")
    return {"passed": True, "status": "initialized", "scene_id": scene["scene_id"],
            "startup": str(output / "startup.json"), "frames": len(calibrated),
            "capabilities": capabilities, "missing_runtime": missing,
            "prediction_preflight": prediction,
            "summary": {"execution": "completed", "data": {"preparation": "passed"},
                        "quality": {"status": "not_evaluated"},
                        "next_steps": ["Run the generated predict command" if prediction["passed"] else "Resolve prediction_preflight errors; evidence and geometry remain usable"]}}
