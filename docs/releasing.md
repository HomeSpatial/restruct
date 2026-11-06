<a id="生成公开发行候选"></a>

# Build a public release candidate

[English](releasing.md) | [简体中文](releasing.zh-CN.md)

Distribute the core wheel/sdist, Blender add-on ZIP and complete source ZIP separately. The current version is a source candidate; packaging does not automatically upload to PyPI, create remote repositories or publish images.

<a id="边界"></a>

## Boundaries

`release-files.json` is a file-by-file allowlist for complete source releases. The packager copies only listed files, excluding `.git`, ignored directories, weights, runtime outputs and unlisted files. Cleaning a working tree does not make its private development history public. For an initial public repository, initialize Git in a new directory from the generated source ZIP instead of pushing the original development history.

Explicitly update the allowlist when adding public files, then run checks. Do not update it by scanning and automatically including every file in a directory.

Documentation screenshots each need a `reviewed_assets` entry containing their path, SHA-256, provenance and an attribution document also listed for release. The checker permits only those exact PNG/JPEG bytes. Replacing a screenshot requires another review and a new digest. This record grants no dataset rights; raw data and models remain local under the case policy.

```bash
python -B scripts/check_public_release.py
python -B scripts/build_release.py --out /new/path/release
```

Outputs include:

- `homespatial_restruct-*.whl` and the core `.tar.gz`: Apache-2.0 core and MIT three.js.
- `homespatial_model-*.zip`: complete GPL-3.0-or-later add-on source and notices for the Apache shared module.
- `homespatial-restruct-*-source.zip`: complete allowlisted source that can run tests and packaging independently.
- `SOURCE_MANIFEST.json` and `SHA256SUMS`: digests of source and distribution files.

Builds run in a separate temporary directory and validate wheel/sdist contents and license metadata. The output directory must not exist. Partial failed outputs remain for diagnosis; completed candidates are not overwritten.

<a id="发布前在目标主机完成"></a>

## Before publishing on the target host

1. For an initial publication, create a repository without private history from the source ZIP and review files and license notices.
2. Enable CI and private vulnerability reporting on the public host, and actually run the CPU and Blender workflows.
3. The project is Restruct and the target repository is `HomeSpatial/restruct`; package metadata already uses this address. After the first push, verify the project, documentation and issue links.
4. Add permitted public-dataset demonstrations individually under the [sample policy](../cases/README.md), recording real quality limitations.

Release packages contain neither model weights nor a complete GPU container. Optional backends retain their own licensing; see the [third-party notices](../THIRD_PARTY_NOTICES.md).
