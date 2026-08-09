# hterm

Raycast extension for launching common herdr workspaces

## Requirements

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) for dependency management
- [just](https://github.com/casey/just) for running tasks (optional but recommended)

## Installation

### Using uv (recommended)

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/hterm.git
cd hterm

# Install dependencies
uv sync

# Run the CLI
uv run hterm --help
```

### Using uvx (one-off)

```bash
# Run directly from git
uvx --from git+https://github.com/YOUR_USERNAME/hterm hterm --help
```

## Usage

```bash
# Show help
hterm --help

# Show version
hterm --version


# Run hello command
hterm hello world
hterm hello name YourName

# Fetch JSON from an API
hterm api get json https://api.github.com

```

## Development

This project uses `just` for common tasks:

```bash
# Show available commands
just

# Install dependencies
just sync

# Run the CLI
just run --help

# Run tests
just test

# Format code
just fmt

# Lint code
just lint

# Type check
just typecheck

# Run all checks
just check

# Build package
just build

# Install pre-commit hooks
just pre-commit
```

## Project Structure

```
src/hterm/
├── __init__.py           # Package init
├── __main__.py           # Module entry point
├── cli/
│   ├── app.py            # Main CLI app with autodiscovery
│   ├── config.py         # Configuration model (CLI options + env vars)
│   ├── ui/
│   │   ├── console.py    # Output helpers (ok, warn, err, info)
│   │   └── errors.py     # Error rendering
│   └── commands/         # Auto-discovered commands
│       ├── hello.py
│       └── api/
│           └── get.py
├── domain/
│   └── errors.py         # AppError and domain errors
└── infrastructure/
    └── http_client.py    # HTTP client wrapper
```

## Adding New Commands


1. Create a new file in `cli/commands/` or a subdirectory
2. Define `app = typer.Typer()` in the module
3. Add commands with `@app.command()` decorator

Example:

```python
# cli/commands/mycommand.py
import typer
from hterm.cli.ui.console import ok

app = typer.Typer()

@app.command()
def greet(name: str) -> None:
    """Greet someone."""
    ok(f"Hello, {name}!")
```

This automatically becomes available as `hterm mycommand greet`.


## License

MIT
