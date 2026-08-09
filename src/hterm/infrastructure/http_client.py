"""HTTP client wrapper with error handling."""

from typing import Any

import httpx

from hterm.cli.config import Config
from hterm.domain.errors import AppError


class HttpClient:
    """Synchronous HTTP client with built-in error handling."""

    def __init__(self, config: Config) -> None:
        """Initialize HTTP client with configuration.

        Args:
            config: Application configuration containing timeout and user agent settings.
        """
        self._timeout = config.http_timeout
        self._user_agent = config.http_user_agent
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
            )
        return self._client

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request and return JSON response.

        Args:
            url: The URL to request
            params: Optional query parameters

        Returns:
            Parsed JSON response

        Raises:
            AppError: If the request fails or response is invalid
        """
        try:
            client = self._get_client()
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise AppError.http_error(
                message=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                url=url,
                hint="The server returned an error. Check your request parameters.",
            ) from e
        except httpx.RequestError as e:
            raise AppError.http_error(
                message=str(e),
                url=url,
            ) from e
        except ValueError as e:
            raise AppError(
                message=f"Invalid JSON response: {e}",
                hint="The server returned invalid JSON.",
            ) from e

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """Make a GET request and return the response.

        Args:
            url: The URL to request
            params: Optional query parameters

        Returns:
            HTTP response object

        Raises:
            AppError: If the request fails
        """
        try:
            client = self._get_client()
            response = client.get(url, params=params)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            raise AppError.http_error(
                message=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                url=url,
            ) from e
        except httpx.RequestError as e:
            raise AppError.http_error(
                message=str(e),
                url=url,
            ) from e
