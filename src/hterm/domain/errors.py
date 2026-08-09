"""Domain-level errors for the application."""

from typing import Self


class AppError(Exception):
    """Structured application error with optional hint."""

    def __init__(
        self,
        message: str,
        hint: str | None = None,
        exit_code: int = 1,
    ) -> None:
        self.message = message
        self.hint = hint
        self.exit_code = exit_code
        super().__init__(message)

    def __str__(self) -> str:
        result = self.message
        if self.hint:
            result += f"\n\nHint: {self.hint}"
        return result

    @classmethod
    def http_error(
        cls,
        message: str,
        url: str | None = None,
        hint: str | None = None,
    ) -> Self:
        """Create an HTTP-related error."""
        full_message = f"HTTP Error: {message}"
        if url:
            full_message = f"{full_message} (URL: {url})"
        return cls(message=full_message, hint=hint or "Check your network connection and the URL.")

    @classmethod
    def validation_error(
        cls,
        message: str,
        field: str | None = None,
        hint: str | None = None,
    ) -> Self:
        """Create a validation-related error."""
        full_message = f"Validation Error: {message}"
        if field:
            full_message = f"{full_message} (field: {field})"
        return cls(message=full_message, hint=hint or "Check your input values.")
