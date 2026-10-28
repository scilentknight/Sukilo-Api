"""Report safe seller application failures."""


class VendorServiceError(Exception):
    """Report a seller workflow failure with an HTTP status."""

    def __init__(self, status_code: int, message: str) -> None:
        """Initialize a safe domain error.

        Args:
            status_code: HTTP status for the route boundary.
            message: PII-free diagnostic message.
        """
        self.status_code = status_code
        super().__init__(message)
