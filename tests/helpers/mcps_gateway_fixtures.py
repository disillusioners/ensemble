"""The remote-hands gateway's capability-map FIXTURES — ONE copy.

WHAT THIS OWNS
--------------
The two capability maps a machine advertises at its ``hello`` (§5.3):

* :data:`CAPS_V01` — the historical seven keys.  A v0.1 machine has
  never heard of ``client_terminate`` and advertises exactly this map.
* :data:`CAPS_V02` — the v0.1 map plus ``client_terminate``, the ONE
  difference the v0.2 generation adds (§5.3 / §6.10).

WHY ONE MODULE
---------------
The maps were copy-pasted into three unit files (the service, the
session-expiry and the termination suites) and the copies had already
drifted: one of them spelled the v0.2 key as the bare literal
``"client_terminate"`` while its siblings used the protocol constant
(:data:`daemon.services.mcps_gateway.protocol.CAP_CLIENT_TERMINATE`).
A capability name is a WIRE token — one spelling, one home.

THE CONSTANT, NOT THE LITERAL
-----------------------------
The v0.2 key is taken from the protocol module, so a rename of the
wire token can never leave a stale literal behind in a fixture.  (The
kit under ``tests/conformance/`` keeps its own literal on purpose: it
imports NOTHING from ``daemon.*`` — its independence is the point.)
"""

from __future__ import annotations

from daemon.services.mcps_gateway.protocol import CAP_CLIENT_TERMINATE

#: The capability map a v0.1 machine advertises — the historical seven
#: keys.  A machine that never heard of ``client_terminate`` advertises
#: exactly this map.
CAPS_V01: dict[str, bool] = {
    "session": True,
    "tools": True,
    "notifications": False,
    "heartbeat": True,
    "screenshot_delta": False,
    "multi_session": False,
    "client_cert": False,
}

#: The capability map a v0.2 machine advertises — the v0.1 map plus
#: ``client_terminate``: the machine tells the centre it can terminate a
#: session from its own side.  Exactly the v0.1 map plus the one key.
CAPS_V02: dict[str, bool] = {**CAPS_V01, CAP_CLIENT_TERMINATE: True}

__all__ = ["CAPS_V01", "CAPS_V02"]
