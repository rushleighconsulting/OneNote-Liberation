"""Asset detection and normalisation helpers.

The exporter receives some OneNote resources from Microsoft Graph with weak
or generic content types such as application/octet-stream. This module uses
file signatures (magic bytes) first, falling back to declared MIME types.
"""

from __future__ import annotations

import hashlib
import mimetypes
import pathlib
import shutil
import tempfile
from dataclasses import dataclass


@dataclass(frozen=True)
class AssetInfo:
    sha256: str
    asset_type: str
    mime_type: str
    extension: str


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_from_bytes(data: bytes, declared_mime_type: str = "") -> AssetInfo:
    declared = (declared_mime_type or "").split(";")[0].strip().lower()
    extension = ".bin"
    mime_type = declared or "application/octet-stream"
    asset_type = "unknown"

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = ".png"
        mime_type = "image/png"
        asset_type = "image"
    elif data.startswith(b"\xff\xd8\xff"):
        extension = ".jpg"
        mime_type = "image/jpeg"
        asset_type = "image"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        extension = ".gif"
        mime_type = "image/gif"
        asset_type = "image"
    elif len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        extension = ".webp"
        mime_type = "image/webp"
        asset_type = "image"
    elif data.startswith((b"II*\x00", b"MM\x00*")):
        extension = ".tiff"
        mime_type = "image/tiff"
        asset_type = "image"
    elif data.startswith(b"%PDF-"):
        extension = ".pdf"
        mime_type = "application/pdf"
        asset_type = "pdf"
    else:
        guessed = mimetypes.guess_extension(declared) if declared else None
        if guessed:
            extension = ".jpg" if guessed == ".jpe" else guessed
            if declared.startswith("image/"):
                asset_type = "image"
            elif declared == "application/pdf":
                asset_type = "pdf"
            elif declared.startswith("audio/"):
                asset_type = "audio"
            elif declared.startswith("video/"):
                asset_type = "video"
            elif declared in {
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.ms-powerpoint",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            }:
                asset_type = "office"
            else:
                asset_type = "file"

    return AssetInfo(
        sha256=sha256_hex(data),
        asset_type=asset_type,
        mime_type=mime_type,
        extension=extension,
    )


def detect_file(path: pathlib.Path, declared_mime_type: str = "") -> AssetInfo:
    return detect_from_bytes(path.read_bytes(), declared_mime_type=declared_mime_type)


def normalised_attachment_copy(path: pathlib.Path, temp_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Return a temp copy whose extension matches its detected file signature.

    Apple Notes uses the filename extension when showing attachments. If Graph
    exported a PNG as .bin, Notes will show a .bin unless we hand it a filename
    ending in .png.
    """
    info = detect_file(path)
    if path.suffix.lower() == info.extension:
        return path

    root = temp_dir or pathlib.Path(tempfile.mkdtemp(prefix="onenote_liberation_assets_"))
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{path.stem}{info.extension}"
    shutil.copy2(path, target)
    return target
