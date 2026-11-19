"""The D5 rollback-target chain — the PYTHON resolver (Wave 3).

The 5-rung resolution R0 > R1 > R2 > R3 > R4 from §2.5 of
``.agents/shared/planning/self-service-promote/architecture-recommendation.md``
(the BINDING table). THREE implementations exist by necessity — lib.sh
(``_rollback_target_resolve``, the ceremony scripts), launcher.sh
(``_js_rollback_target_resolve``, the staged-standalone sweep twin), and
this module (the daemon closer's ``to=`` payload + the parity harness) —
and AC-D5-12's parity pack pins all three resolve IDENTICALLY over
``tests/fixtures/rollback_target_chain_cases.tsv``.

Rung semantics (identical in all three):

* VALID (a G0 candidate — capture / displaced): non-empty string ∧ ≠ txn
  target ∧ ≠ live symlink target ∧ ``releases/<v>`` exists.
* VALID_to (the R1 ``to=`` — F5): non-empty ∧ ≠ target ∧ ``releases/<v>``
  exists. A ``to=`` failing VALID is treated as ABSENT — never a
  candidate, never a halt.
* R0 capture: absent (null / empty — the flip-prep persist writes null
  when there is no symlink) ⇒ fall through; present ∧ VALID ⇒ candidate
  ``captured``; present ∧ INVALID ⇒ HALT ``capture_invalid`` — NEVER fall
  through (F3).
* R1 to=: VALID ∧ a conflicting VALID R0 ⇒ HALT ``rollback_target_ambiguous``;
  VALID ∧ no R0 ⇒ candidate ``requested``; absent/none/failing-VALID ⇒ fall.
* R2 current: null ⇒ fall; == target (or otherwise not a valid candidate)
  ⇒ fall; else candidate ``current``.
* R3 displaced: null ⇒ fall; present ∧ ≠ a VALID R2 ⇒ HALT
  ``rollback_target_ambiguous``; else VALID ⇒ candidate ``displaced``
  (R2 == R3 keeps the R2 candidate, source=current).
* R4 previous (G1): null / == target / otherwise-invalid ⇒ HALT
  ``no_previous_release`` (the callers map it to the EXISTING "journal
  has no previous release" halt, VERBATIM); VALID ⇒ candidate ``previous``.
* Resolution SHORT-CIRCUITS at the highest VALID rung (clarification (i)):
  a VALID R0 (or R1) resolves OVER any stale-field disagreement between
  the lower rungs — those are never evaluated once a higher rung
  resolves.

CLI (the parity harness entry): pass the parsed inputs, get the same
machine line the shell resolvers print — ``resolve <ver> <source>`` or
``halt <reason>``; exit 0 on resolve, 1 on halt.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

HALT_CAPTURE_INVALID = "capture_invalid"
HALT_AMBIGUOUS = "rollback_target_ambiguous"
HALT_NO_PREVIOUS = "no_previous_release"

_SOURCE_CAPTURED = "captured"
_SOURCE_REQUESTED = "requested"
_SOURCE_CURRENT = "current"
_SOURCE_DISPLACED = "displaced"
_SOURCE_PREVIOUS = "previous"


@dataclass(frozen=True)
class Resolution:
    """One chain verdict: a resolve (value + source rung) or a halt."""

    kind: str  # "resolve" | "halt"
    value: str | None = None
    source: str | None = None
    reason: str | None = None

    def render(self) -> str:
        if self.kind == "resolve":
            return f"resolve {self.value} {self.source}"
        return f"halt {self.reason}"


def _clean(value: object) -> str:
    """Normalize a rung input: JSON null / empty / non-string ⇒ '' (ABSENT)."""
    if value is None:
        return ""
    if not isinstance(value, str):
        # the R0 predicate's first conjunct: the capture must be a JSON
        # STRING. A non-string is not an absent field — but it cannot be a
        # candidate either; the shell seam (field extraction) cannot
        # distinguish, so parity treats it as absent (documented).
        return ""
    if value in ("", "null", "none"):
        return ""
    return value


def _valid(releases: Path, ver: str, target: str, live: str) -> bool:
    """The G0-candidate VALID rule."""
    if not ver:
        return False
    if ver == target:
        return False
    if ver == live:
        return False
    return (releases / ver).is_dir()


def _valid_to(releases: Path, ver: str, target: str) -> bool:
    """The R1 to= VALID rule (F5 — no live-link conjunct)."""
    if not ver:
        return False
    if ver == target:
        return False
    return (releases / ver).is_dir()


def resolve(
    releases_dir: Path | str,
    target: str,
    live_link: str | None,
    capture: str | None,
    r1_to: str | None,
    r2_current: str | None,
    r3_displaced: str | None,
    r4_previous: str | None,
) -> Resolution:
    """Resolve the rollback target per the §2.5 chain table."""
    releases = Path(releases_dir)
    live = _clean(live_link)
    cap = _clean(capture)
    r1to = _clean(r1_to)
    if r1to == "none":
        r1to = ""
    r2 = _clean(r2_current)
    r3 = _clean(r3_displaced)
    r4 = _clean(r4_previous)

    cand = ""
    src = ""

    # ── R0: the filesystem capture ──
    if cap:
        if _valid(releases, cap, target, live):
            cand, src = cap, _SOURCE_CAPTURED
        else:
            return Resolution("halt", reason=HALT_CAPTURE_INVALID)

    # ── R1: the rollback_requested row's to= ──
    if r1to:
        if _valid_to(releases, r1to, target):
            if cand and r1to != cand:
                return Resolution("halt", reason=HALT_AMBIGUOUS)
            if not cand:
                cand, src = r1to, _SOURCE_REQUESTED
        # a to= failing VALID is treated as ABSENT (F5)

    if cand:
        return Resolution("resolve", value=cand, source=src)

    # ── R2: journal.current at resolution time ──
    r2_cand = ""
    if r2:
        if r2 != target and r2 != live and (releases / r2).is_dir():
            r2_cand = r2
            cand, src = r2, _SOURCE_CURRENT

    # ── R3: in_flight.displaced_version (legacy G0 stamp) ──
    if r3:
        if r2_cand and r3 != r2_cand:
            return Resolution("halt", reason=HALT_AMBIGUOUS)
        if not cand and _valid(releases, r3, target, live):
            cand, src = r3, _SOURCE_DISPLACED

    # ── R4: journal.previous (G1 — the last rung) ──
    if not cand:
        if not r4:
            return Resolution("halt", reason=HALT_NO_PREVIOUS)
        if r4 != target and r4 != live and (releases / r4).is_dir():
            cand, src = r4, _SOURCE_PREVIOUS
        else:
            return Resolution("halt", reason=HALT_NO_PREVIOUS)

    return Resolution("resolve", value=cand, source=src)


def resolve_from_journal(
    install_dir: Path | str,
    journal: dict,
) -> Resolution:
    """Resolve using the journal's own fields (the closer's ``to=`` path).

    The daemon resolves ONLY from the capture (§2.5 daemon payload): when
    the capture is absent or invalid the answer for the ``to=`` payload is
    ``to none to_source none`` — the daemon never invents a destination
    and never rolls back by itself (upgrade_journal_sweep.py's closer).
    The live-link conjunct reads the REAL symlink (a capture equal to
    where the symlink already points — e.g. a manual rollback already
    happened — is INVALID in the closer's frame too); an unreadable
    symlink degrades to the expected post-flip frame (live == target).
    """
    import os

    install = Path(install_dir)
    in_flight = journal.get("in_flight")
    if not isinstance(in_flight, dict):
        return Resolution("halt", reason=HALT_NO_PREVIOUS)
    target = _clean(in_flight.get("target"))
    if not target:
        return Resolution("halt", reason=HALT_NO_PREVIOUS)
    try:
        live = os.readlink(install / "current").rsplit("/", 1)[-1]
    except OSError:
        live = target
    capture = _clean(in_flight.get("captured_previous"))
    if capture and _valid(install / "releases", capture, target, live):
        return Resolution("resolve", value=capture, source=_SOURCE_CAPTURED)
    return Resolution("halt", reason=HALT_NO_PREVIOUS)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="The D5 rollback-target chain resolver (parity harness)"
    )
    parser.add_argument("--releases-dir", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--live-link",
        default="",
        help="the live symlink target NAME at resolution time (e.g. the flipped version)",
    )
    parser.add_argument(
        "--capture", default="", help="in_flight.captured_previous ('' = absent)"
    )
    parser.add_argument("--to", default="", help="the rollback_requested to= ('' = absent)")
    parser.add_argument("--current", default="", help="journal.current ('' = null)")
    parser.add_argument(
        "--displaced", default="", help="in_flight.displaced_version ('' = null)"
    )
    parser.add_argument("--previous", default="", help="journal.previous ('' = null)")
    parser.add_argument(
        "--json",
        dest="json_spec",
        default="",
        help='alternative: a JSON object {"live_link":...,"capture":...,"to":...,"current":...,"displaced":...,"previous":...}',
    )
    args = parser.parse_args(argv)

    if args.json_spec:
        spec = json.loads(args.json_spec)
    else:
        spec = {
            "live_link": args.live_link,
            "capture": args.capture,
            "to": args.to,
            "current": args.current,
            "displaced": args.displaced,
            "previous": args.previous,
        }

    res = resolve(
        args.releases_dir,
        args.target,
        spec.get("live_link"),
        spec.get("capture"),
        spec.get("to"),
        spec.get("current"),
        spec.get("displaced"),
        spec.get("previous"),
    )
    print(res.render())
    return 0 if res.kind == "resolve" else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
