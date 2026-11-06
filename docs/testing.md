<a id="测试与支持范围"></a>

# Testing and supported configurations

[English](testing.md) | [简体中文](testing.zh-CN.md)

The core declares Python 3.11+; the automated test matrix covers Windows/Linux with Python 3.11/3.12. The Blender add-on declares a minimum of 4.2, with **5.1.2** as the current real integration target. Other Python, Blender, macOS and GPU combinations are not considered validated.

<a id="cpu-测试"></a>

## CPU tests

```bash
python -m pip install ".[geometry,dev]"
python -B -m unittest discover -s tests -v
python -B scripts/check_public_release.py
python -m build
python -m twine check dist/*
python -B blender/package_addon.py dist/homespatial_model-0.3.1.zip
```

Tests use synthetic fixtures and need no private scans or weights. They cover contracts, coordinates, evidence queries, model checks, textures, exchange formats, task startup, package resources, skill synchronization and release boundaries. CPU CI also runs the CLI from an installed wheel outside the source tree.

<a id="真实-blender-集成"></a>

## Real Blender integration

Run these commands from the repository root, replacing `/path/to/blender` with your installed executable. In PowerShell, put `&` before a quoted executable path.

```bash
/path/to/blender --background --factory-startup --python-exit-code 1 --python blender/tests/test_parameter_scene.py -- --contract-src src --mode fixture --out artifacts/fixture
python -B blender/tests/verify_parameter_exports.py --output artifacts/fixture
/path/to/blender --background --factory-startup --python-exit-code 1 --python blender/tests/test_installed_parameter_addon.py -- --zip dist/homespatial_model-0.3.1.zip --blend artifacts/fixture/edited.blend --exclude src --out artifacts/installed
/path/to/blender --background --factory-startup --python-exit-code 1 --python blender/tests/test_review.py -- --contract-src src --blend artifacts/fixture/edited.blend --out artifacts/review
```

These checks cover creation/editing, saving/reopening, rebuilding, GLB coordinates and PBR, actual wall openings, offline export, installation-ZIP independence and temporary reviews preserving the original project. The Blender workflow is manually triggered and separately downloads version 5.1.2 with its official SHA-256 verification. CPU CI does not download Blender.

<a id="未覆盖的验收"></a>

## Acceptance work not covered

GPU inference, dataset reconstruction quality, full indoor coverage, navigation and collision accuracy require separate acceptance checks. The repository includes a [local ScanNet++ case record and screenshot](../cases/scannetpp-09c1414f1b/README.md), but does not distribute real input data or a general benchmark. Passing tests does not mean automatic reconstruction achieves the architectural accuracy a user needs.
