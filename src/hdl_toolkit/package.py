"""Zip .dat files (plus ClobFiles/ and BlobFiles/ attachments) for upload."""

from __future__ import annotations

import base64
import io
import os
import zipfile

ATTACHMENT_DIRS = ("ClobFiles", "BlobFiles")


def build_zip(dat_files: list[str], attachments_root: str | None = None) -> bytes:
    """Return a ZIP archive, in memory, ready for HDL.

    ``.dat`` files go at the root of the archive. When ``attachments_root``
    contains ``ClobFiles/`` or ``BlobFiles/`` folders, their contents are
    added under the same folder names, which is where HDL looks for them.
    """
    if not dat_files:
        raise ValueError("at least one .dat file is required")

    buffer = io.BytesIO()
    names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in dat_files:
            name = os.path.basename(path)
            if not name.lower().endswith(".dat"):
                raise ValueError(f"{path}: HDL data files must use the .dat extension")
            if name in names:
                raise ValueError(f"duplicate file name in archive: {name}")
            names.add(name)
            zf.write(path, arcname=name)

        if attachments_root:
            for folder in ATTACHMENT_DIRS:
                base = os.path.join(attachments_root, folder)
                for root, _dirs, files in os.walk(base):
                    for file in sorted(files):
                        full = os.path.join(root, file)
                        rel = os.path.relpath(full, attachments_root)
                        zf.write(full, arcname=rel.replace(os.sep, "/"))
    return buffer.getvalue()


def to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
