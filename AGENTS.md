# local-runtime Agent Guide

## Mission

Maintain `local-runtime` as a small, product-neutral Python runtime for local endpoint discovery, routing, leases, lifecycle ownership, and GUI/headless adapters. Keep host behavior at adapter boundaries; the library owns generic coordination only.

## Working Rules

- Use the globally installed `uv` for Python environments and dependencies.
- Treat `pyproject.toml` as the source of truth for metadata, dependencies, packaging, and tool configuration.
- Keep runtime dependencies empty and development tools in the `dev` group.
- Target Python 3.14 or newer, strict BasedPyright, the `src` layout, and the explicit Hatchling package `src/local_runtime`.
- Keep public modules typed, cohesive, and documented with Google-style docstrings. Put `from __future__ import annotations` after each module docstring.
- Put host SDKs, product handlers, installers, file formats, databases, and worker entrypoints in consuming projects.
- Keep generated environments, build output, caches, credentials, and unrelated lockfile changes out of Git.

## Change Loop

1. Read `docs/runtime-contract.md` before changing public registry, transport, routing, lifecycle, or adapter behavior.
2. Locate the owning module and make one cohesive change.
3. Add a focused contract test for each changed behavior and failure path under `tests/`.
4. Run this sequence from the repository root:

   ```text
   uv sync --group dev
   uv run ruff check .
   uv run ruff format --check .
   uv run basedpyright
   uv run pytest
   ```

   On Windows, if `uv` cannot write its environment or cache, rerun the same command with the required elevation. Preserve the project configuration.

5. Before release, run `uv build`, inspect the sdist and wheel contents, then run `git diff --check`.
6. Report completion only when the changed behavior, focused tests, verification sequence, and final diff all pass.

## Repository Map

- `src/local_runtime/`: generic registry, transport, routing, lifecycle, and adapter modules.
- `src/local_runtime/py.typed`: typed-consumer marker.
- `tests/`: contract tests using `--import-mode=importlib`.
- `docs/runtime-contract.md`: public lifecycle and adapter contract.

Keep distribution name `local-runtime`, import name `local_runtime`, author, repository URL, license, version, and Python requirement synchronized in `pyproject.toml`.