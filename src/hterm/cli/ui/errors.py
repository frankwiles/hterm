"""Error rendering for the CLI."""

from rich.console import Console
from rich.panel import Panel

from hterm.domain.errors import AppError

console = Console()


def render_error(error: AppError) -> None:
    """Render an AppError with a styled panel."""
    content = f"[bold red]{error.message}[/bold red]"
    if error.hint:
        content += f"\n\n[dim]💡 Hint: {error.hint}[/dim]"

    console.print()
    console.print(Panel(content, title="Error", border_style="red"))
    console.print()


def render_exception(exc: Exception) -> None:
    """Render any exception as an error."""
    if isinstance(exc, AppError):
        render_error(exc)
    else:
        # Convert unknown exceptions to AppError
        error = AppError(
            message=str(exc),
            hint="An unexpected error occurred. Try again or check your configuration.",
        )
        render_error(error)


def handle_error(exc: Exception) -> int:
    """Handle an exception and return the appropriate exit code."""
    render_exception(exc)
    if isinstance(exc, AppError):
        return exc.exit_code
    return 1
