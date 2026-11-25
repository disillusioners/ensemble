"""PIN — the journal DEFERRAL kinds stay OUTSIDE the daemon's terminal set.

The contract (the standing POK contract ``journal-defer-kind-must-be-nonterminal``):

    "A journal event kind in ``_TERMINAL_EVENTS`` makes ``reconcile_pending_op``
     close the pending promote op and release the arm. Transient deferral kinds
     must stay outside it; ``sweep_deferred`` is the in-repo precedent."

Background: the hygiene commit ``5f866fd41`` (A1) renamed the journal kind
``halt`` -> ``gate_defer`` at the two ``_gate_consume`` sites in
``scripts/upgrade/lib.sh``.  A ``gate_defer`` row is a TRANSIENT deferral
("attribution deferred — transient; NOT a failure verdict; no rollback, no
quarantine") — it must NEVER close a pending promote op and must NEVER
release the arm.

What this file pins:

* **the CLASSIFICATION** — the literal ``gate_defer`` is ABSENT from
  ``daemon.tools.upgrade_journal._TERMINAL_EVENTS``, with two non-vacuity
  controls: (a) the collection under test is the REAL, POPULATED terminal
  set (its cardinality and its known members are pinned), so the pin cannot
  pass against an empty or wrong collection; (b) the LEGAL PRECEDENT
  ``sweep_deferred`` IS PRESENT in the same collection's DEFINITION (the
  ``_TERMINAL_EVENTS`` block, ``daemon/tools/upgrade_journal.py:2572`` — it
  names ``sweep_deferred`` as the in-repo precedent for a deferral kind that
  stays OUTSIDE the set) while itself staying OUTSIDE the set.
* **the BEHAVIOUR** — a ``gate_defer`` row in a journal that carries a pending
  promote op does NOT close that op: ``reconcile_pending_op`` returns
  ``None``, the ``pending_op`` survives, and the journal is left
  byte-identical (no closure row, no arm release).
* **the DISCRIMINATING CONTROL** — the SAME fixture with a TERMINAL row
  (``commit``) DOES close the op.  Without this leg the behaviour leg could
  pass vacuously (a torn/absent fixture would also leave the op pending).

MUTATION PROOF (non-vacuity, both directions):

    The subject module is resolved through ``UPGRADE_JOURNAL_MODULE_PATH``:
    unset ⇒ the PRODUCTION ``daemon.tools.upgrade_journal``; set ⇒ the
    module at that path.  The pack
    ``tests/packs/journal_defer_kind_nonterminal_unit_test.sh`` copies the
    production module into a scratch dir, inserts ``"gate_defer"`` into the
    ``_TERMINAL_EVENTS`` tuple IN THE COPY and re-runs this file against the
    copy: the pin goes RED.  The production module is NEVER mutated.

FIXTURE RULES (house): every fixture is SYNTHETIC and lives under
``tmp_path`` — the live install (``~/agents-ensemble``) is never touched, and
the journal fixture is GENERATED, never copied from a live install.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The kind under test (the A1 hygiene rename: ``halt`` -> ``gate_defer``).
DEFER_KIND = "gate_defer"
# The in-repo LEGAL PRECEDENT named by the contract (a deferral kind that
# stays OUTSIDE the terminal set).
LEGAL_PRECEDENT_KIND = "sweep_deferred"
# The env seam the mutation proof drives (unset ⇒ the production module).
MODULE_PATH_ENV = "UPGRADE_JOURNAL_MODULE_PATH"


# ── the subject module (production by default; overridable for the proof) ──


def _uj():
    """Return the module under test.

    Production by default.  ``UPGRADE_JOURNAL_MODULE_PATH`` (set ⇒ the
    module at that path) exists so the MUTATION PROOF can run this very file
    against a scratch COPY of the module with ``gate_defer`` added — the
    production file is never touched.
    """
    override = os.environ.get(MODULE_PATH_ENV, "").strip()
    if not override:
        from daemon.tools import upgrade_journal

        return upgrade_journal
    path = Path(override)
    assert path.is_file(), f"{MODULE_PATH_ENV} does not point at a file: {path}"
    spec = importlib.util.spec_from_file_location("upgrade_journal_under_test", str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _terminal_events_source(module) -> str:
    """The SOURCE of the ``_TERMINAL_EVENTS`` definition block.

    The block carries the daemon's own comment that names ``sweep_deferred``
    as the legal precedent (a deferral kind that must stay OUTSIDE the set).
    """
    src = inspect.getsource(module)
    marker = "_TERMINAL_EVENTS: tuple[str, ...] = ("
    start = src.index(marker)
    end = src.index("\n)", start) + 2
    return src[start:end]


# ── fixtures ────────────────────────────────────────────────────────────────


def _write_pending_journal(install: Path, event: str) -> Path:
    """A synthetic journal carrying a pending PROMOTE op + one history row.

    ``in_flight`` is ``None`` (no live txn), the op is armed 60 s ago and
    expires in an hour (so the reconcile's EXPIRY branch — a genuine orphan
    with no executor evidence — never fires and the leg measures the
    TERMINAL-CLASSIFICATION branch only).
    """
    releases = install / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    payload = {
        "current": "v0.0.1-test",
        "previous": "v0.0.1-test",
        "in_flight": None,
        "pending_op": {
            "run_id": "run-pin-defer-kind",
            "kind": "promote",
            "env": "dev",
            "target": "v0.0.2-test",
            "armed_at": (now - timedelta(seconds=60)).strftime(stamp),
            "expires_at": (now + timedelta(hours=1)).strftime(stamp),
            "owner_pid": 0,
            "owner_kind": "tool-arm",
        },
        "history": [
            {
                "ts": now.strftime(stamp),
                "event": event,
                "detail": f"pin fixture row ({event})",
            }
        ],
    }
    state = releases / "state.json"
    state.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return state


def _journal(install: Path) -> dict:
    return json.loads(
        (install / "releases" / "state.json").read_text(encoding="utf-8")
    )


# ── pin 1: the CLASSIFICATION ───────────────────────────────────────────────


def test_gate_defer_kind_is_absent_from_the_terminal_event_set() -> None:
    """The A1 hygiene rename (``halt`` -> ``gate_defer``) stays NON-terminal.

    A ``gate_defer`` row is a TRANSIENT deferral; if the kind entered
    ``_TERMINAL_EVENTS`` the daemon's ``reconcile_pending_op`` would close
    the pending promote op and release the arm on a deferral — the exact
    defect the standing contract forbids.
    """
    module = _uj()
    terminal = module._TERMINAL_EVENTS

    # THE PIN — the literal kind is ABSENT from the terminal set.
    assert DEFER_KIND not in terminal, (
        f"{DEFER_KIND!r} is a TRANSIENT deferral kind — it must stay OUTSIDE "
        f"_TERMINAL_EVENTS (it would close the pending promote op)"
    )

    # NON-VACUITY (A) — the collection under test is the REAL, POPULATED
    # terminal set: the pin cannot pass against an empty or wrong collection.
    assert len(terminal) == 10, f"terminal set cardinality moved: {terminal}"
    assert "commit" in terminal
    assert "sweep_rollback" in terminal
    assert "serving_green_commit" in terminal

    # NON-VACUITY (B) — the LEGAL PRECEDENT: ``sweep_deferred`` IS PRESENT in
    # the SAME collection's definition (the block names it as the in-repo
    # precedent for a deferral kind that stays outside the set) and it itself
    # stays OUTSIDE the set, exactly like ``gate_defer`` must.
    block = _terminal_events_source(module)
    assert LEGAL_PRECEDENT_KIND in block, (
        "the _TERMINAL_EVENTS definition must keep naming "
        f"{LEGAL_PRECEDENT_KIND!r} as the legal precedent for a deferral kind"
    )
    assert LEGAL_PRECEDENT_KIND not in terminal


# ── pin 2: the BEHAVIOUR — a deferral row closes nothing ───────────────────


def test_gate_defer_row_does_not_close_the_pending_promote_op(tmp_path: Path) -> None:
    """A ``gate_defer`` row must not close the pending op nor release the arm.

    Driven through the PRODUCTION ``reconcile_pending_op`` — the reader the
    contract names.  The journal is left BYTE-IDENTICAL (read-first
    discipline: nothing is written when no closure happens).
    """
    module = _uj()
    install = tmp_path / "install"
    state = _write_pending_journal(install, DEFER_KIND)
    before = state.read_bytes()

    note = module.reconcile_pending_op(install)

    assert note is None, f"a deferral row closed the pending op: {note}"
    after = state.read_bytes()
    assert after == before, "the deferral path must not write to the journal"
    data = _journal(install)
    assert data.get("pending_op"), "the pending op must survive a deferral row"
    events = [str(e.get("event")) for e in data.get("history", [])]
    assert "sweep" not in events, "no closure row may be journaled"


# ── pin 3: the DISCRIMINATING CONTROL — the fixture is not vacuous ────────


def test_a_terminal_row_does_close_the_pending_promote_op(tmp_path: Path) -> None:
    """The control leg: the SAME fixture with a TERMINAL row DOES close.

    Without this leg the behaviour leg could pass vacuously (an absent or
    torn fixture would also leave the pending op in place).
    """
    module = _uj()
    install = tmp_path / "install"
    _write_pending_journal(install, "commit")

    note = module.reconcile_pending_op(install)

    assert note is not None and "commit" in note, (
        f"the control leg must close the op on a terminal row, got {note!r}"
    )
    data = _journal(install)
    assert not data.get("pending_op"), "the terminal row must clear the pending op"
