# Source Layout

This directory is the source root for the project template. The package directory is named python_template; the distribution name is python-template.

## Ownership

- src/python_template/__init__.py is the public package boundary. Keep it small and use explicit re-exports when the package grows.
- Add cohesive feature modules under src/python_template/<feature>.py or src/python_template/<feature>/; do not copy product-specific modules from another project.
- Keep implementation details private with a leading underscore when they are not part of the public contract.
- Keep type annotations complete under strict BasedPyright.
- Keep py.typed in the import package so downstream type checkers know inline annotations are supported.

## After copying

Rename this directory to the chosen Python import name and update the wheel packages setting in pyproject.toml. The distribution name and import name may differ, but every import and package path must agree.
