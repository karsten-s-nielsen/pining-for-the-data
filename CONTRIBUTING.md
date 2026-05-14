# Contributing to pining-for-the-data

Thank you for your interest in contributing! This document covers the development workflow and standards.

## Development Setup

```bash
git clone https://github.com/karsten-s-nielsen/pining-for-the-data.git
cd pining-for-the-data
uv sync --extra dev
uv run pre-commit install
```

## Coding Standards

- **Python 3.12+** -- use modern syntax (type unions with `|`, etc.)
- **Ruff** for linting and formatting (line length 120)
- **Pyright** for type checking (basic mode)
- **pytest** for testing

All three must pass before submitting a PR:

```bash
uv run ruff check src/ scripts/ && uv run ruff format --check src/ scripts/
uv run pyright src/
uv run pytest
```

## Commit Conventions

- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`
- Keep commits focused -- one logical change per commit

## Pull Request Process

1. Fork the repo and create a feature branch from `main`
2. Make your changes with tests
3. Ensure all checks pass (ruff, pyright, pytest)
4. Fill out the PR template
5. A maintainer will review your PR

## What to Contribute

- Bug fixes (with regression tests)
- Documentation improvements
- New format handlers (see `src/formats/` for the pattern)
- Test coverage improvements

## What Not to Contribute (without discussion first)

- New CLI tools or API endpoints (open an issue first)
- Large refactors (open an issue first)
- Changes to the de-identification engine (reserved for private data use cases)

## Questions?

Open a [GitHub issue](https://github.com/karsten-s-nielsen/pining-for-the-data/issues) for questions or feature proposals.
