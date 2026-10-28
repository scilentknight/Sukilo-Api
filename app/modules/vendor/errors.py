"""Report safe seller application failures."""


class VendorServiceError(Exception):
    """Report a seller workflow failure with an HTTP status."""

    def __init__(
        self,
        status_code: int,
        message: str,
        code: str | None = None,
    ) -> None:
        """Initialize a safe domain error.

        Args:
            status_code: HTTP status for the route boundary.
            message: PII-free diagnostic message.
            code: Stable check code, when the caller must branch on it.
        """
        self.status_code = status_code
        self.code = code
        super().__init__(message)
