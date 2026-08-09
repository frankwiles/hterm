"""Stable application errors shared by the CLI and orchestration layers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class HtermError(Exception):
    """An expected failure that can be rendered for humans or as JSON."""

    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)
    exit_code: int = 1

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.details}


class ConfigurationError(HtermError):
    """Configuration could not be read or validated."""

    def __init__(
        self, message: str, *, code: str = "configuration_error", **details: Any
    ):
        super().__init__(code, message, details)


class ExternalCommandError(HtermError):
    """An external executable failed or could not be invoked."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "external_command_failed",
        **details: Any,
    ):
        super().__init__(code, message, details)
