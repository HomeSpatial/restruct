# SPDX-License-Identifier: Apache-2.0
"""Checkpoint writes shared by the CLI and standalone model workers (stdlib only)."""
import json
import os
from pathlib import Path
import time
from uuid import uuid4


def write_json(path, value, *, default=None, ensure_ascii=False):
    """Replace atomically; tolerate short Windows locks, never discard a failed write.

    Six replace attempts wait at most 1.55 seconds in total. Only Windows access,
    sharing and lock violations are retried; persistent errors still propagate.
    The old destination remains in place and the unique temporary file is retained
    on failure so an operator can inspect it without treating it as committed.
    """
    data = json.dumps(value, allow_nan=False, indent=2, default=default, ensure_ascii=ensure_ascii) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                os.replace(temp, path)
                return
            except OSError as exc:
                if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 5:
                    raise
                time.sleep(.05 * 2**attempt)
    except OSError as exc:
        exc.add_note(f"Checkpoint not committed; temporary file retained if created: {temp}")
        raise
