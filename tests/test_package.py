"""Smoke tests for the empty template package."""

from __future__ import annotations

import python_template


def test_package_imports() -> None:
    """Verify that the template package has a stable, empty public surface."""
    assert python_template.__all__ == []
