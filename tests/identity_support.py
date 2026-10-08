"""The one development identity, for test harnesses (lane DI, 2026-10-08).

The server no longer reads a client-chosen profile from a header or the
query string: in a non-operational scope every request is the experimenter
``DevIdentityProvider.from_environment().authenticate()`` names. A harness
that bootstraps a workspace, binds a protocol and then opens a WebSocket has
to do all of it as that same principal, or the server's own identity will
not see what the test prepared.
"""

from __future__ import annotations

from voiney_lab.identity import DevIdentityProvider, Principal


def development_principal() -> Principal:
    """The principal the server resolves when no login is configured."""

    return DevIdentityProvider.from_environment().authenticate()
