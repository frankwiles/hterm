"""Console output helpers using Rich."""

from rich.console import Console
from rich.panel import Panel

console = Console()


def ok(message: str) -> None:
    """Print a success message in green."""
    console.print(f"[bold green]✓[/bold green] {message}")


def warn(message: str) -> None:
    """Print a warning message in yellow."""
    console.print(f"[bold yellow]![/bold yellow] {message}")


def err(message: str) -> None:
    """Print an error message in red."""
    console.print(f"[bold red]✗[/bold red] {message}")


def info(message: str) -> None:
    """Print an info message in blue."""
    console.print(f"[bold blue]ℹ[/bold blue] {message}")


def header(title: str) -> None:
    """Print a styled header."""
    console.print()
    console.rule(f"[bold]{title}[/bold]")
    console.print()


def panel(content: str, title: str | None = None, style: str = "blue") -> None:
    """Print a styled panel."""
    console.print(Panel(content, title=title, border_style=style))
