# Python Project Template Contributor Notes

This repository is a copy-first Python project template. Keep the template small, self-contained, and free of product-specific behavior.

## Commands

Run every command from the repository root:

~~~bash
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
~~~

Verification order: ruff check -> ruff format --check -> basedpyright -> pytest. Before a release, also run uv build and inspect the wheel contents.

## Template architecture

| Path | Responsibility |
|---|---|
| src/python_template/__init__.py | Minimal public package namespace; add product modules here after instantiation. |
| src/python_template/py.typed | PEP 561 marker for typed consumers. |
| tests/test_package.py | Import smoke test that must remain fast and dependency-free. |
| pyproject.toml | Package metadata, Hatchling build, Ruff, BasedPyright, and pytest configuration. |
| docs/template-contract.md | Identity replacement and extension rules for copied projects. |

The template is intentionally static. Do not add a generator, shell hook, network fetch, or arbitrary template execution unless that becomes an explicit product requirement with its own threat model and tests.

## Constraints

- Python 3.14+ is the baseline unless the instantiated project deliberately lowers it in both pyproject.toml and .python-version.
- Keep the src layout and the explicit extraPaths = ["src"] setting unless the project adopts an equivalent, documented type-checker configuration.
- Keep runtime dependencies empty until the instantiated product actually needs one. Development tools belong in the dev dependency group.
- Keep Hatchling's wheel package selection explicit: packages = ["src/<import-package>"].
- Keep public modules typed under strict BasedPyright; do not suppress diagnostics with broad casts or ignore comments.
- Use Google-style docstrings and put from __future__ import annotations first in every source module after its docstring.
- Do not add parent-directory, file:, or link: dependencies to project configuration.
- Do not commit virtual environments, build output, caches, credentials, or generated lockfile changes unrelated to dependency updates.

## Instantiation workflow

1. Copy the repository into a new project or use it as a repository-template source.
2. Replace the distribution name and import package name independently.
3. Update metadata, license, documentation, and CI together.
4. Add the first real module and a focused test; keep __init__.py as a public boundary.
5. Run the complete verification order, then build and inspect the artifact.

## Style and ownership

- Keep feature code in cohesive modules instead of growing one catch-all file.
- Keep tests under tests/ and use --import-mode=importlib to exercise the packaged src layout.
- Update README, this file, and docs/template-contract.md when the template contract changes.
- A copied project may remove template-only guidance after its identity and module layout are finalized, but it must retain equivalent contributor and verification documentation.
