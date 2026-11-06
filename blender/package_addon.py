# SPDX-License-Identifier: GPL-3.0-or-later
"""Package the Blender add-on with this checkout's standard-library model contract."""
import argparse
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def package(output, contract_source=None):
    root = Path(__file__).resolve().parent
    contract = Path(contract_source) if contract_source else root.parent / "src/indoor_recon/scene_model.py"
    contract = contract.resolve()
    if not contract.is_file():
        raise FileNotFoundError(f"Model contract is missing: {contract}")
    license_files = {
        "LICENSE": root / "LICENSE",
        "NOTICE": root / "NOTICE",
        "LICENSE-APACHE-2.0": root.parent / "LICENSE",
        "CORE-NOTICE": root.parent / "NOTICE",
    }
    for source in license_files.values():
        if not source.is_file():
            raise FileNotFoundError(f"Required license file is missing: {source}")
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, source in license_files.items():
            archive.write(source, "homespatial_model/" + name)
        for name in ("__init__.py", "api.py", "parameter_scene.py", "review.py"):
            archive.write(root / "homespatial_model" / name, "homespatial_model/" + name)
        archive.write(root / "README.md", "homespatial_model/README.md")
        archive.write(root / "run_agent.py", "homespatial_model/run_agent.py")
        archive.writestr("homespatial_model/_vendor/__init__.py", "")
        archive.write(contract, "homespatial_model/_vendor/scene_model.py")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--contract-source", type=Path, help="Override the bundled scene_model.py")
    args = parser.parse_args()
    print(package(args.output, args.contract_source))


if __name__ == "__main__":
    main()
