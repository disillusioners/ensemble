"""The end-ack's DECLINE detector — ONE helper, TWO callers.

DAEMON-FREE BY CONSTRUCTION
---------------------------
This module imports NOTHING from ``daemon.*``.  The interop lane's
findings must be attributable to the WIRE and not to the centre's
internals (``transcript.py`` states the same rule), so the decline rule
is transcribed here from the SPEC — it is NOT imported from
``daemon.services.mcps_gateway.service``.

WHY A SHARED HELPER
-------------------
The decline check was copy-pasted into the two gates that assert an
acknowledged end (``p0_gate.py`` and ``test_real_pair.py``).  Two copies
means two shapes, and the shapes had already drifted once: one copy
tested ``"ended" in body and not body["ended"]`` and the other tested
``body.get("ended") is False`` — so an ack whose ``ended`` member was
``0`` or ``""`` (falsy, but not ``False``) was a DECLINE in one gate and
an ACCEPTANCE in the other.  One helper, one shape, two callers.

THE RULE (SPEC §6.9.7 — PRESENT AND FALSY)
-------------------------------------------
A decline is a DICT that CARRIES an ``ended`` member whose value is
FALSY:

* a dict whose ``ended`` is truthy is an ACCEPTANCE;
* a dict with NO ``ended`` member at all is an ACCEPTANCE too — the
  shipped v0.1 client answers ``session.end`` with ``{"session_id"}``
  and no ``ended`` member (the member is simply absent), so the
  acknowledged-end rows must stay GREEN against it;
* anything that is not a dict (``None`` included) is an ACCEPTANCE.

This is the SAME shape as the reference centre's own predicate
(``McpGatewayService._ack_declines``) and as the conformance kit's
independent transcription (``tests/conformance/conformance_peer.py``) —
three implementations of one rule, deliberately written apart but
deliberately the same rule.
"""

from __future__ import annotations

from typing import Any


def ack_declines(body: Any) -> bool:
    """Did the machine DECLINE the end? (SPEC §6.9.7)

    ``body`` is the machine's end-ack RESULT body (``None`` when the
    frame carried none).  ``None`` is handled EXPLICITLY: the check is a
    membership test on a mapping, and a ``None`` body must never raise
    a ``TypeError`` in the middle of a gate's evidence collection.
    """
    if body is None:
        return False
    if not isinstance(body, dict):
        return False
    if "ended" not in body:
        return False
    return not body["ended"]


__all__ = ["ack_declines"]
