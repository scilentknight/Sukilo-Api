"""Helpers for validating and decoding uploaded image files."""

from __future__ import annotations

import io

from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

MAX_VENDOR_IMAGES = 5
MIN_VENDOR_IMAGES = 1


def validate_image_count(image_count: int) -> None:
    """Ensure the vendor upload contains between one and five images.

    Args:
        image_count: Number of files submitted in the multipart form.

    Raises:
        HTTPException: If the count is outside the allowed range.
    """
    if image_count < MIN_VENDOR_IMAGES or image_count > MAX_VENDOR_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submit between 1 and 5 product images.",
        )


def read_rgb_image(upload: UploadFile, max_bytes: int) -> Image.Image:
    """Read an upload into a PIL RGB image after size and type checks.

    Args:
        upload: Multipart image upload.
        max_bytes: Maximum allowed file size in bytes.

    Returns:
        Decoded RGB image.

    Raises:
        HTTPException: If validation or decoding fails.
    """
    if not upload.content_type or not upload.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Each upload must be an image file.",
        )
    upload.file.seek(0)
    raw_bytes = upload.file.read()
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded image is empty.",
        )
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image exceeds the maximum upload size.",
        )
    try:
        return Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    except UnidentifiedImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to decode image.",
        ) from exc
