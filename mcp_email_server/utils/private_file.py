from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def write_private_text(path: Path, text: str) -> None:
    """Atomically write text to path with owner-only (0600) permissions.

    The content goes to a temporary file in the same directory, which mkstemp creates
    as 0600, and is then renamed over the target. Other local users never see the
    content at a wider mode, and a crash mid-write leaves the old file intact.

    Symlinks are followed, so a cache linked into another config directory stays shared
    instead of being replaced by a regular file.
    """
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
