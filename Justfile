# hterm - Development tasks
# Use `just --list` to see available commands

# Default recipe - show help
default:
    just --list

# Install/sync dependencies
sync:
    uv sync

# Run the CLI with arguments
run *ARGS:
    uv run hterm {{ARGS}}

# Run tests
test:
    uv run pytest

# Format code with ruff
fmt:
    uv run ruff format .

# Lint code with ruff
lint:
    uv run ruff check .

# Type check with ty
typecheck:
    uv run ty check

# Run all checks (lint, format check, typecheck, test)
check: lint typecheck test
    uv run ruff format . --check

# Build the package
build:
    uv build

# Install the tool 
install: 
    uv tool install --force . 

raycast-install:
    cd raycast 
    npm install 
    echo "Ctrl-C to close this and it will stay installed"
    npm run dev 

# Clean build artifacts
clean:
    rm -rf dist/ .pytest_cache/ .ruff_cache/
    find . -type d -name "__pycache__" -exec rm -rf {} +

# Install pre-commit hooks
pre-commit:
    uv run pre-commit install

# Run pre-commit on all files
pre-commit-all:
    uv run pre-commit run --all-files

# Run the CLI via uvx from a git repo (for testing distribution)
uvx REPO *ARGS:
    uvx --from git+{{REPO}} hterm {{ARGS}}


