"""Store private seller document bytes behind an opaque key."""

import secrets
from typing import Protocol


class DocumentStorage(Protocol):
    """Store and open private seller files."""

    def put_private(self, content: bytes, media_type: str) -> str:
        """Store one private file and return its opaque key."""
        ...

    def open_private(self, storage_key: str) -> bytes:
        """Return the bytes stored for one opaque key."""
        ...


class MemoryDocumentStorage:
    """Keep private documents in process memory."""

    def __init__(self) -> None:
        """Initialize an empty document map."""
        self._objects: dict[str, tuple[bytes, str]] = {}

    def put_private(self, content: bytes, media_type: str) -> str:
        """Store one private file and return its opaque key.

        Args:
            content: File bytes.
            media_type: Declared media type.

        Returns:
            Opaque storage key.
        """
        key = secrets.token_urlsafe(32)
        self._objects[key] = (content, media_type)
        return key

    def open_private(self, storage_key: str) -> bytes:
        """Return the bytes stored for one opaque key.

        Args:
            storage_key: Key returned by `put_private`.

        Returns:
            Stored file bytes.

        Raises:
            KeyError: If the key is unknown.
        """
        try:
            return self._objects[storage_key][0]
        except KeyError as exc:
            raise KeyError("Document is unavailable.") from exc
