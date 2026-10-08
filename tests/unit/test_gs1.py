"""Unit tests for GS1 GTIN and Digital Link helpers."""

import pytest

from app.utils.gs1 import (
    assign_product_gtin,
    build_digital_link,
    gtin_from_product_id,
    gs1_check_digit,
    normalize_gtin,
    parse_scanned_code,
)


def test_gs1_check_digit_for_known_gtin13() -> None:
    assert gs1_check_digit("400638133393") == "1"


def test_gtin_from_product_id_is_fourteen_digits_with_valid_check() -> None:
    gtin = gtin_from_product_id(1)
    assert len(gtin) == 14
    assert gtin.isdigit()
    assert gtin.startswith("02")
    assert gs1_check_digit(gtin[:-1]) == gtin[-1]


def test_gtin_from_product_id_is_unique_per_id() -> None:
    assert gtin_from_product_id(1) != gtin_from_product_id(2)


def test_normalize_ean13_and_upca_to_gtin14() -> None:
    assert normalize_gtin("4006381333931") == "04006381333931"
    assert normalize_gtin("0061414112345") == "00061414112345"


def test_build_and_parse_digital_link() -> None:
    gtin = gtin_from_product_id(94)
    link = build_digital_link("https://sukilo.example", gtin)
    assert link == f"https://sukilo.example/01/{gtin}"
    assert parse_scanned_code(link) == gtin


def test_parse_scanned_code_accepts_raw_digits() -> None:
    gtin = gtin_from_product_id(7)
    assert parse_scanned_code(gtin) == gtin
    assert parse_scanned_code(gtin[1:]) == gtin


def test_parse_scanned_code_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        parse_scanned_code("not-a-code")
    with pytest.raises(ValueError):
        parse_scanned_code("123")


def test_assign_product_gtin_uses_demo_when_vendor_omits_barcode() -> None:
    assert assign_product_gtin(None, 1) == gtin_from_product_id(1)
    assert assign_product_gtin("   ", 12) == gtin_from_product_id(12)


def test_assign_product_gtin_keeps_vendor_ean13() -> None:
    assert assign_product_gtin("4006381333931", 99) == "04006381333931"


def test_assign_product_gtin_rejects_invalid_vendor_code() -> None:
    with pytest.raises(ValueError):
        assign_product_gtin("12345", 1)
