# Test Layout

The template starts with one dependency-free import smoke test. Replace or extend it as soon as the instantiated project gains behavior.

## Conventions

- Keep tests under tests/ and name files test_*.py.
- Test observable public behavior rather than private implementation details.
- Add focused fixtures only when several tests share the same setup.
- Keep tests runnable with uv run pytest from the repository root.
- Preserve importlib mode so tests do not accidentally import an uninstalled package from a sibling path.

After renaming the package, update tests/test_package.py and any new imports together.
