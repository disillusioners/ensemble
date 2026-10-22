"""Neutral home for the user-origin PROVENANCE implementation (nest-ops D-14).

Why this module exists
----------------------
The provenance reader (``_restart_provenance`` and friends) was born
inside the LIVE upgrade lane (``daemon/tools/upgrade_tools.py``). The
``instance_ops`` category needs the SAME authorization semantics — a
second copy would be a silent-divergence risk, and a private
cross-lane import would couple a new module to a name the live lane
may rename.

D-14 therefore MOVES the implementation HERE, under PUBLIC names, and
leaves a delegating alias block in ``upgrade_tools`` so every consumed
name keeps resolving from its historical home:

============================  =========================================
Moved name (private)          Public name (this module)
============================  =========================================
``_restart_provenance``       :func:`restart_provenance`
``_lookup_provenance_field``  :func:`lookup_provenance_field`
``_capture_user_source``      :func:`capture_user_source`
``_capture_provenance_class`` :func:`capture_provenance_class`
``_ProvenanceVerdict``        :class:`ProvenanceVerdict`
``_EMPTY_PROVENANCE``         :data:`EMPTY_PROVENANCE`
============================  =========================================

The implementation is byte-preserved: the semantics, the fail-closed
paths, the refusal diagnostics and the verdict shape are UNCHANGED. The
move is a RELOCATION, never a redesign.

What the verdict means
----------------------
``ProvenanceVerdict.ok`` is True only when the acting instance carries
a LIVE user-origin window:

* ``pclass == "direct"`` — the window was minted on THIS instance by a
  user-origin turn (``_user_origin_windows``).
* ``pclass == "inherited"`` — the window was inherited from a caller
  through an ``internal_agent:<caller>`` delegation
  (``_inherited_provenance_windows``).

The windows are IN-MEMORY on the manager and are wiped on daemon
restart, so a restart between "the user spoke" and "the agent acts"
makes the gate REFUSE. That is fail-closed by design — it is not
durability.

Known bounds (stated, not implied)
----------------------------------
Requiring ``pclass == "inherited"`` on a protected target CLOSES the
direct-window-on-the-acting-instance forgery class, but it does NOT
close the DELEGATED forge: the inherited mint never inspects the
root's own provenance. See ``decisions.md`` D-18 and critical note
``f4cf9c4f`` (DF-4) for the root-fix lane. This module is a
defense-in-depth layer, NOT a proof of user intent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .upgrade_journal import parse_iso_utc

__all__ = [
    "EMPTY_PROVENANCE",
    "ProvenanceVerdict",
    "capture_provenance_class",
    "capture_user_source",
    "lookup_provenance_field",
    "restart_provenance",
]


# Shared provenance-capture plumbing (hygiene fold): both capture helpers
# read the SAME two manager stores with the same direct→inherited
# preference; the lookup helper centralizes the field read, and the
# empty dict is the single spelling of "no live provenance" (used by the
# capture helper's failure path AND the 1c narrowing's promote-branch).
EMPTY_PROVENANCE: dict[str, Any] = {
    "provenance_class": "",
    "root_source": None,
    "root_message_id": None,
    "depth": 0,
    "stamped_at": None,
    "expires_at": None,
}


def lookup_provenance_field(
    manager: "Any",
    instance_id: str,
    direct_key: str,
    inherited_key: str,
) -> Any:
    """Read ``direct_key`` from the instance's DIRECT user-origin window
    when present, else ``inherited_key`` from the inherited store.
    Returns ``None`` when neither store has a live-shaped entry for the
    instance. Defensive — never raises."""
    try:
        windows = getattr(manager, "_user_origin_windows", None)
        if isinstance(windows, dict):
            entry = windows.get(instance_id)
            if isinstance(entry, dict) and entry.get(direct_key) is not None:
                return entry.get(direct_key)
        inherited = getattr(manager, "_inherited_provenance_windows", None)
        if isinstance(inherited, dict):
            ientry = inherited.get(instance_id)
            if isinstance(ientry, dict) and ientry.get(inherited_key) is not None:
                return ientry.get(inherited_key)
        return None
    except Exception:  # defensive — capture is best-effort
        return None


def capture_user_source(manager: "Any", instance_id: str) -> str:
    """Read the recorded ``source`` from the manager's per-instance
    user-origin window. Returns ``""`` on absence (non-user-origin arm;
    Phase 2 falls back to ``"api"`` for routing). Defensive — never raises.

    agent-restart C-2 (§2.4 arm-time capture — extended to BOTH stores):
    when no DIRECT window is live, the inherited store's ROOT source is
    captured instead (the wake record + restart_audits render the root
    provenance; the restart-kind wake never re-stamps from it — A-2).
    """
    source = lookup_provenance_field(
        manager, instance_id, "source", "root_source"
    )
    return source if isinstance(source, str) and source else ""


def capture_provenance_class(
    manager: "Any", instance_id: str
) -> dict[str, Any]:  # values: provenance_class is Literal["direct","inherited",""]
    """agent-restart C-2: capture the provenance class + root fields for
    the wake record / audit row. Reads BOTH manager stores (direct
    first). Returns a dict with ``provenance_class`` (``""`` when no
    live provenance), ``root_source``, ``root_message_id``, ``depth``,
    ``stamped_at``, ``expires_at``. Defensive — never raises."""
    try:
        verdict = restart_provenance(manager, instance_id)
        if not verdict.ok:
            return dict(EMPTY_PROVENANCE)
        return {
            "provenance_class": verdict.pclass,
            "root_source": verdict.root_source,
            "root_message_id": verdict.root_message_id,
            "depth": verdict.depth,
            "stamped_at": verdict.stamped_at,
            "expires_at": verdict.expires_at,
        }
    except Exception:  # defensive — capture is best-effort
        return dict(EMPTY_PROVENANCE)


@dataclass
class ProvenanceVerdict:
    """agent-restart §2.4 gate predicate result (live lane)."""

    ok: bool
    pclass: str                 # "direct" | "inherited" | ""
    root_source: str | None = None
    root_message_id: str | None = None
    depth: int = 0
    stamped_at: str | None = None
    expires_at: str | None = None
    detail: str = ""            # refusal diagnostic (W1: names observed source)


def restart_provenance(
    manager: "Any", instance_id: str
) -> ProvenanceVerdict:
    """THE restart provenance gate (§2.4, env-scoped LIVE-only).

    Reads BOTH manager stores through this helper — the DIRECT window
    (``_user_origin_windows``) first, then the INHERITED store
    (``_inherited_provenance_windows``, mechanism B). The upgrade
    factor-2 gate keeps its explicit direct-store access and NEVER
    calls this helper (§2.6 decision-layer isolation, T0a — directional
    rule).

    Fail-closed everywhere: absent/expired/unparseable entries never
    pass; a refusal names the OBSERVED source value (W1 hardening —
    ``_user_origin_last_stamp`` carries the observation even on clear).
    """
    now_dt = datetime.now(tz=timezone.utc)
    refus_detail = "no live user-origin provenance on this turn"
    windows = getattr(manager, "_user_origin_windows", None)
    entry = windows.get(instance_id) if isinstance(windows, dict) else None
    if isinstance(entry, dict):
        exp = parse_iso_utc(entry.get("expires_at"))
        if exp is not None and now_dt <= exp:
            return ProvenanceVerdict(
                ok=True,
                pclass="direct",
                root_source=(
                    str(entry.get("source"))
                    if isinstance(entry.get("source"), str)
                    else None
                ),
                root_message_id=(
                    str(entry.get("message_id"))
                    if isinstance(entry.get("message_id"), str)
                    else None
                ),
                depth=0,
                stamped_at=(
                    str(entry.get("stamped_at"))
                    if isinstance(entry.get("stamped_at"), str)
                    else None
                ),
                expires_at=(
                    str(entry.get("expires_at"))
                    if isinstance(entry.get("expires_at"), str)
                    else None
                ),
                detail="direct window live",
            )
        refus_detail = (
            f"direct window expired at {entry.get('expires_at')}"
        )
    inherited = getattr(manager, "_inherited_provenance_windows", None)
    ientry = inherited.get(instance_id) if isinstance(inherited, dict) else None
    if isinstance(ientry, dict):
        exp = parse_iso_utc(ientry.get("expires_at"))
        if exp is not None and now_dt <= exp:
            return ProvenanceVerdict(
                ok=True,
                pclass="inherited",
                root_source=(
                    str(ientry.get("root_source"))
                    if isinstance(ientry.get("root_source"), str)
                    else None
                ),
                root_message_id=(
                    str(ientry.get("root_message_id"))
                    if isinstance(ientry.get("root_message_id"), str)
                    else None
                ),
                depth=int(ientry.get("depth") or 0),
                stamped_at=(
                    str(ientry.get("root_stamped_at"))
                    if isinstance(ientry.get("root_stamped_at"), str)
                    else None
                ),
                expires_at=(
                    str(ientry.get("expires_at"))
                    if isinstance(ientry.get("expires_at"), str)
                    else None
                ),
                detail="inherited window live (internal_agent chain)",
            )
        refus_detail = (
            f"inherited window expired at {ientry.get('expires_at')}"
        )
    # W1: name the observed source of this turn when known.
    last_stamps = getattr(manager, "_user_origin_last_stamp", None)
    observed = (
        last_stamps.get(instance_id) if isinstance(last_stamps, dict) else None
    )
    observed_src = (
        observed.get("source")
        if isinstance(observed, dict)
        else None
    )
    if isinstance(observed_src, str) and observed_src:
        refus_detail += (
            f" — observed source {observed_src!r} classified "
            f"{(observed or {}).get('detail', '?')!r}"
        )
    return ProvenanceVerdict(ok=False, pclass="", detail=refus_detail)
