"""GS1 GTIN-14 and Digital Link helpers for product identification."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

DEMO_GS1_PREFIX = "02"
_DIGITS_RE = re.compile(r"\d+")
_AI01_RE = re.compile(r"(?:/01/|01[=/])(\d{8,14})")


def gs1_check_digit(body: str) -> str:
    """Return the GS1 Mod-10 check digit for a numeric body.

    Args:
        body: Digit string excluding the check digit.

    Returns:
        Single check-digit character.

    Raises:
        ValueError: If the body is empty or not numeric.
    """
    if not body or not body.isdigit():
        raise ValueError("GS1 body must be a non-empty digit string.")
    total = 0
    for index, char in enumerate(reversed(body)):
        factor = 3 if index % 2 == 0 else 1
        total += int(char) * factor
    return str((10 - (total % 10)) % 10)


def gtin_from_product_id(product_id: int) -> str:
    """Build a demo GTIN-14 from a catalog product id.

    Args:
        product_id: Positive PostgreSQL product primary key.

    Returns:
        Fourteen-digit GTIN with a valid check digit.

    Raises:
        ValueError: If the product id is not a positive integer.
    """
    if product_id < 1:
        raise ValueError("product_id must be a positive integer.")
    serial = str(product_id).zfill(11)
    if len(serial) > 11:
        raise ValueError("product_id is too large for a demo GTIN-14.")
    body = f"{DEMO_GS1_PREFIX}{serial}"
    return f"{body}{gs1_check_digit(body)}"


def assign_product_gtin(raw: str | None, product_id: int) -> str:
    """Return a vendor barcode or a demo GTIN for a product id.

    Args:
        raw: Optional UPC, EAN, GTIN, or Digital Link from the vendor.
        product_id: Database product primary key.

    Returns:
        Fourteen-digit GTIN-14.

    Raises:
        ValueError: If a provided barcode cannot be normalized.
    """
    if raw is None or not str(raw).strip():
        return gtin_from_product_id(product_id)
    return parse_scanned_code(raw)


def normalize_gtin(raw: str) -> str:
    """Normalize UPC-A, EAN-13, or GTIN-14 digits to GTIN-14.

    Args:
        raw: Barcode digits, optionally containing spaces.

    Returns:
        Fourteen-digit GTIN-14.

    Raises:
        ValueError: If the value cannot be normalized.
    """
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 12:
        digits = f"00{digits}"
    elif len(digits) == 13:
        digits = f"0{digits}"
    if len(digits) != 14:
        raise ValueError("Barcode must be a 12, 13, or 14 digit GTIN.")
    if gs1_check_digit(digits[:-1]) != digits[-1]:
        raise ValueError("GTIN check digit is invalid.")
    return digits


def build_digital_link(origin: str, gtin: str) -> str:
    """Build a GS1 Digital Link URI for AI 01 (GTIN).

    Args:
        origin: Absolute origin such as ``https://sukilo.example``.
        gtin: Fourteen-digit GTIN.

    Returns:
        Digital Link URL of the form ``{origin}/01/{gtin}``.
    """
    normalized_origin = origin.rstrip("/")
    return f"{normalized_origin}/01/{gtin}"


def parse_scanned_code(raw: str) -> str:
    """Extract a GTIN-14 from a Digital Link URL or raw barcode.

    Args:
        raw: Scanner output: URL or digit string.

    Returns:
        Normalized GTIN-14.

    Raises:
        ValueError: If no GTIN can be parsed.
    """
    text = unquote(raw.strip())
    if not text:
        raise ValueError("Scanned code is empty.")
    match = _AI01_RE.search(text)
    if match:
        return normalize_gtin(match.group(1))
    parsed = urlparse(text)
    if parsed.scheme and parsed.path:
        path_match = _AI01_RE.search(parsed.path)
        if path_match:
            return normalize_gtin(path_match.group(1))
    digits = "".join(_DIGITS_RE.findall(text))
    return normalize_gtin(digits)
