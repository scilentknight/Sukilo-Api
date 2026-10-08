"""Remove SQLAlchemy wheel C extensions from the active environment.

Windows Application Control (WDAC / Smart App Control) can block
``*.pyd`` files under ``site-packages``. SQLAlchemy ships matching
``_cy.py`` modules; without the extensions, imports use pure Python.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _sqlalchemy_site_packages() -> Path:
    """Return the installed SQLAlchemy package directory.

    Returns:
        Path to the ``sqlalchemy`` package in ``site-packages``.

    Raises:
        ImportError: If SQLAlchemy is not installed.
    """
    import sqlalchemy

    return Path(sqlalchemy.__file__).resolve().parent


def remove_sqlalchemy_cextensions() -> int:
    """Delete blocked ``.pyd`` files next to SQLAlchemy ``_cy`` modules.

    Returns:
        Number of extension files removed.
    """
    package_root = _sqlalchemy_site_packages()
    removed = 0
    for extension_path in package_root.rglob("*.pyd"):
        extension_path.unlink()
        removed += 1
    return removed


def main() -> None:
    """Print how many SQLAlchemy C extensions were removed."""
    count = remove_sqlalchemy_cextensions()
    print(f"Removed {count} SQLAlchemy C extension file(s). Using pure-Python modules.")


if __name__ == "__main__":
    main()
    sys.exit(0)
