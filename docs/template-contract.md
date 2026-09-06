# Template Contract

This document defines what a project copied from python-template may rely on and what must be replaced.

## Stable baseline

A fresh instance provides:

- a Python src layout;
- Hatchling wheel builds with explicit package selection;
- a strict BasedPyright configuration;
- Ruff lint and format checks;
- pytest with importlib collection;
- a PEP 561 py.typed marker;
- no runtime dependencies and no network- or shell-based generation step.

The empty package namespace is intentional. The template does not promise a domain API.

## Required identity replacements

| Template identity | Replace in an instance |
|---|---|
| python-template in project.name | Distribution/project name |
| src/python_template | Python import package directory |
| from python_template ... | All source and test imports |
| Template description/authors | Product metadata |
| Template README and license holder | Product documentation and legal identity |

Treat distribution and import names as separate values. Normalize only the import name to a valid Python identifier.

## Extension rules

1. Add runtime dependencies only when production code imports them.
2. Add a test for every new public behavior.
3. Keep build, test, and documentation inputs within the project root.
4. Update pyproject.toml, README, contributor notes, and CI together when commands or supported Python versions change.
5. Inspect wheel and sdist contents before publishing.

## Non-goals

The template does not execute user-provided templates, install hooks, or remote repositories. It is intentionally a static baseline that can be copied, forked, or used through a hosting service's repository-template feature.
