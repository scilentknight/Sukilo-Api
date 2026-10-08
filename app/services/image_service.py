"""Persist uploaded product images to local disk."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile


@dataclass(frozen=True)
class StoredImage:
    """Filesystem location and public URL for a saved upload."""

    path: Path
    url: str


class ImageService:
    """Write product images under the configured upload directory."""

    def __init__(self, upload_dir: Path) -> None:
        """Store the root directory used for uploads.

        Args:
            upload_dir: Base path where product images are written.
        """
        self._upload_dir = upload_dir

    def ensure_upload_root(self) -> None:
        """Create the upload directory tree when it is missing."""
        self._upload_dir.mkdir(parents=True, exist_ok=True)

    def save_product_image(self, product_id: int, upload: UploadFile) -> StoredImage:
        """Save one product image and return its path and URL.

        Args:
            product_id: Owning product identifier.
            upload: Uploaded file from multipart form data.

        Returns:
            Saved file location and URL served under ``/uploads``.
        """
        suffix = _extension_for_content_type(upload.content_type)
        filename = f"{uuid.uuid4().hex}{suffix}"
        relative = Path("products") / str(product_id) / filename
        destination = self._upload_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        upload.file.seek(0)
        destination.write_bytes(upload.file.read())
        return StoredImage(path=destination, url=f"/uploads/{relative.as_posix()}")

    def copy_product_image_from_path(self, product_id: int, source_path: Path) -> StoredImage:
        """Copy an existing file into the product upload tree.

        Args:
            product_id: Owning product identifier.
            source_path: Source image on disk.

        Returns:
            Saved file location and URL served under ``/uploads``.
        """
        suffix = source_path.suffix.lower() if source_path.suffix else ".jpg"
        filename = f"{uuid.uuid4().hex}{suffix}"
        relative = Path("products") / str(product_id) / filename
        destination = self._upload_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source_path.read_bytes())
        return StoredImage(path=destination, url=f"/uploads/{relative.as_posix()}")


def _extension_for_content_type(content_type: str | None) -> str:
    """Map a MIME type to a file extension.

    Args:
        content_type: Uploaded file MIME type.

    Returns:
        File extension including the leading dot.
    """
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    if content_type and content_type in mapping:
        return mapping[content_type]
    return ".jpg"
