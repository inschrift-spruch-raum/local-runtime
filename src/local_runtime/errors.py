"""Errors raised at the generic routing and lifecycle seams."""

from __future__ import annotations


class MultiMcpError(Exception):
    """Base class for expected runtime failures."""


class SessionNotFoundError(MultiMcpError):
    """The requested session does not exist."""


class SessionExpiredError(MultiMcpError):
    """The requested session was previously expired."""


class RoutingError(MultiMcpError):
    """A request could not be routed to a session."""


class LifecycleError(MultiMcpError):
    """An adapter could not start or stop its runtime."""
