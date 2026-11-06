<a id="快速入门"></a>

# Quick start

[English](quickstart.md) | [简体中文](quickstart.zh-CN.md)

This example is a hand-authored synthetic room included in the repository. It contains no real building or photographs. Installing the core is enough to validate parameters; building and rendering also require Blender. Core 0.7.7, add-on 0.3.1 and skill 0.7.7 work together with model v2 parameters.

<a id="安装"></a>

## Install

Create and activate a Python 3.11 or 3.12 virtual environment from the repository root:

```bash
python -m venv .venv
python -m pip install .
restruct --help
restruct validate blender/examples/room.json
```

Activate the environment before the second command: `source .venv/bin/activate` on Linux/macOS, or `.venv/Scripts/Activate.ps1` in Windows PowerShell. Install this release candidate from source or its accompanying wheel; do not assume that a similarly named PyPI package is this project.

<a id="配置-blender"></a>

## Configure Blender

Install Blender 5.1.2, the currently validated version. Copy the [configuration example](../config/runtime.blender.example.json) to `runtime.local.json` at the repository root. Set absolute paths to the Blender executable and this repository's `blender/` directory. Windows JSON paths can use `/`. Keep this machine-specific configuration local and out of Git.

```json
{
  "blender": {
    "executable": "/absolute/path/to/blender",
    "addon_dir": "/absolute/path/to/restruct/blender"
  }
}
```

The CLI starts a separate Blender process and does not require installing the add-on globally. For the sidebar UI, package the add-on and install it through Blender's Install from Disk command:

```bash
python -B blender/package_addon.py dist/homespatial_model-0.3.1.zip
```

The ZIP includes the standard-library-only parameter module and complete license files. After installation, `addon_dir` can also point to the installed `homespatial_model/` directory.

<a id="创建编辑查看"></a>

## Create, edit and view

```bash
restruct build blender/examples/room.json --runtime runtime.local.json --out artifacts/demo/create
restruct build blender/examples/room.json --runtime runtime.local.json --requests blender/examples/room-edit.json --out artifacts/demo/edited
restruct render artifacts/demo/edited/model.blend --runtime runtime.local.json --view blender/examples/review.json --out artifacts/demo/render
restruct view artifacts/demo --open
```

Build outputs include `model.blend`, `model.glb`, a parameter copy, build reports and logs. The original parameters and previous build remain unchanged. Use a fresh output directory each time; choose another `demo` name when repeating the example. The second build updates the tabletop dimensions and should report revision 2. The render hides the ceiling and south wall to reveal the interior.

The default viewer URL is `http://127.0.0.1:8770/`; use `--port` if needed. The viewer supports category visibility, wall cutaways, height clipping, wireframe and a second-model overlay. Its libraries are bundled for offline use.

Working parameters use right-handed, Z-up coordinates in metres. Standard GLB exports are Y-up, with the coordinate transform recorded in metadata. Successful saving, reopening and export validate the tool workflow, not the geometric quality of arbitrary real scans.

<a id="使用自己的扫描"></a>

## Use your own scan

Install `python -m pip install ".[geometry]"`, then obtain and organize data using the [COLMAP / RTAB-Map input examples](input-data.md). For an existing independent description, use the [data handoff workflow](exchange-workflow.md). Run `restruct inspect` and preflight checks to confirm assets, units, axes and camera bindings before `restruct init-task`.

Perception models are optional. Configure their source, weights and separate environments yourself; see the [license notices](../THIRD_PARTY_NOTICES.md). Refine the final structure against evidence, using the included [reconstruction skill (Chinese)](../skills/reconstruct-structured-interiors/SKILL.md) if appropriate.
