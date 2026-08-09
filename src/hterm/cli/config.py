"""CLI configuration model."""

from pydantic import BaseModel, Field


class Config(BaseModel):
    """Application configuration passed through CLI context.

    Values can be set via CLI options or environment variables.
    """

    http_timeout: float = Field(
        default=30.0,
        description="HTTP request timeout in seconds",
    )
    http_user_agent: str = Field(
        default="hterm/0.1.0",
        description="User-Agent header for HTTP requests",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug mode",
    )
