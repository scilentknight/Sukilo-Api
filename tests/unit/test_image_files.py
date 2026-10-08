"""Unit tests for image upload validation."""

import pytest
from fastapi import HTTPException

from app.utils.image_files import validate_image_count


def test_validate_image_count_accepts_one_to_five() -> None:
    validate_image_count(1)
    validate_image_count(5)


def test_validate_image_count_rejects_zero() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_image_count(0)
    assert exc_info.value.status_code == 400


def test_validate_image_count_rejects_more_than_five() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_image_count(6)
    assert exc_info.value.status_code == 400
