# Contributing

Use Python 3.11 or 3.12 in a virtual environment. Install `.[geometry,dev]`,
run `python -B -m unittest discover -s tests -v`, then run
`python -B scripts/check_public_release.py`. Blender integration commands are
in [docs/testing.md](docs/testing.md).

Describe the concrete problem, resulting behavior and validation in a change
request. Keep unrelated edits out. Update the relevant contract, CLI or Blender
documentation when an interface changes. Use synthetic fixtures for regression
tests; never add private scans, camera poses, room layouts, credentials, local
runtime files or model weights.

New files intended for release must be added explicitly to
`release-files.json`. The release checker rejects missing/unlisted source files,
private identifiers, common credential patterns and broken local Markdown links.
It is a release aid, not a guarantee that arbitrary input is public.
Documentation images require individual visual/provenance review and an exact
checksum in the manifest's `reviewed_assets`; all other binaries are rejected.

By submitting original contributions you agree to license them under the
license of the component you modify: Apache-2.0 for core and documentation,
GPL-3.0-or-later for `blender/`. Preserve third-party notices and identify any
external code or data you introduce. Do not change the shared model contract's
license when it is copied into the add-on.
