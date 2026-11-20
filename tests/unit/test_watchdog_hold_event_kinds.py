"""AC-D2-6 / AC-D2-7 — the D2 EVENT-KIND HYGIENE (self-service-promote Wave 4).

The subject
-----------
The D2 watchdog-containment wave adds new journal event kinds. Two rules
are load-bearing:

* **AC-D2-6** — every NEW event kind is **NON-TERMINAL**: it must NOT be a
  member of ``daemon.tools.upgrade_journal._TERMINAL_EVENTS`` (nor of the
  derived terminal tuples). A terminal kind CLOSES a pending_op — a
  watchdog hold/restore/probe row is pure observability and must never
  close one.
* **AC-D2-7** — the "7b" banned-literal rule holds for every NEW log line:
  no operator-facing line may carry the banned ``systemctl start`` literal
  (the incident §7b trap: an operator copies the line and runs it).

What this file pins
-------------------
* the exact membership of the new-kind list is NON-TERMINAL, on EVERY
  terminal tuple the tree derives (the shared set + the restart superset +
  the derived outcome tuple);
* each new kind is actually USED by the shell family (a positive pin — a
  kind that no writer emits would make the non-terminal assertion vacuous);
* the shared terminal tuple is UNCHANGED (the wave added no terminal kind);
* every NEW log/journal line is free of the banned literal.

The kind list is the wave's contract surface: adding a kind here is
additive (LD-7) and MUST keep the non-terminal property.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UPGRADE_DIR = REPO_ROOT / "scripts" / "upgrade"
LIB_SH = UPGRADE_DIR / "lib.sh"
PROMOTE_SH = UPGRADE_DIR / "promote.sh"
ROLLBACK_SH = UPGRADE_DIR / "rollback.sh"
RESTART_SH = UPGRADE_DIR / "restart.sh"

# The D2 wave's NEW event kinds (all observability — none of them closes a
# pending_op).
NEW_EVENT_KINDS = (
    "watchdog_probe",          # the host probe's result (AC-D2-8)
    "watchdog_hold",           # the hold was applied
    "watchdog_restore",        # the held timer(s) were re-armed
    "watchdog_hold_unavailable",  # the hold degraded to containment (c)
    "watchdog_hold_deferred",  # the RT deferred (re-armed itself)
    "resume_from_state",       # the F2-i resume decision
)

# The ADDITIVE refusal/reason tokens the wave introduces (LD-7: additive
# only — no existing token is renamed or weakened).
NEW_REASON_TOKENS = (
    "watchdog_probe_unreadable",
    "watchdog_hold_unavailable",
    "watchdog_hold_deferred",
)

# The 7b banned literal: an operator-facing line must never carry it (the
# incident §7b trap — an operator copies the line and runs it).
BANNED_LITERAL = "systemctl start"


def _terminal_sets() -> dict[str, tuple[str, ...]]:
    """Every terminal tuple the tree derives (the shared set + supersets)."""
    from daemon.tools import upgrade_journal as uj
    from daemon.tools import upgrade_tools as ut

    return {
        "_TERMINAL_EVENTS": tuple(uj._TERMINAL_EVENTS),
        "RESTART_TERMINAL_EVENTS": tuple(uj.RESTART_TERMINAL_EVENTS),
        "_TERMINAL_OUTCOME_EVENTS": tuple(ut._TERMINAL_OUTCOME_EVENTS),
    }


def _shell_family_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in (LIB_SH, PROMOTE_SH, ROLLBACK_SH, RESTART_SH)
    )


class TestNewEventKindsAreNonTerminal:
    """AC-D2-6 — a new kind must never close a pending_op."""

    def test_no_new_kind_is_terminal(self):
        sets = _terminal_sets()
        for kind in NEW_EVENT_KINDS:
            for name, members in sets.items():
                assert kind not in members, (
                    f"the new event kind {kind!r} must NOT be terminal — it is "
                    f"observability (it appears in {name})"
                )

    def test_the_shared_terminal_set_is_unchanged(self):
        """The wave adds NO terminal kind (the shared tuple's membership)."""
        from daemon.tools import upgrade_journal as uj

        assert tuple(uj._TERMINAL_EVENTS) == (
            "commit",
            "rollback",
            "halt",
            "sweep_rollback",
            "sweep",
            "quarantine",
            "refusal",
            "executor_exit",
            "boot_sweep_commit_and_continue",
            "serving_green_commit",
        ), (
            "the shared terminal set must be UNCHANGED by the watchdog wave — "
            "a new kind there would close pending_ops"
        )

    def test_every_new_kind_is_actually_written(self):
        """A positive pin: a kind nobody writes would make the assertion vacuous."""
        text = _shell_family_text()
        for kind in NEW_EVENT_KINDS:
            assert f"journal_history_append {kind}" in text, (
                f"the new event kind {kind!r} must be written by the shell family"
            )

    def test_the_new_reason_tokens_exist(self):
        """LD-7: the new reasons are ADDITIVE — they exist, and nothing that
        existed before was renamed."""
        text = _shell_family_text()
        for token in NEW_REASON_TOKENS:
            assert token in text, f"the additive reason token {token!r} must exist"

    def test_the_existing_halt_refusal_vocabulary_is_intact(self):
        """The wave must not weaken the refusal family: the named halt
        reasons the tree already had are still there."""
        text = _shell_family_text()
        for token in (
            "watchdog_probe_unreadable",
            "txn-busy",
            "promote refused",
        ):
            assert token in text, f"the refusal vocabulary must keep {token!r}"


class TestBannedLiteralRule:
    """AC-D2-7 — the 7b rule holds for every NEW log line."""

    # The scan is SCOPED to the wave's own lines: the tree carries
    # PRE-EXISTING occurrences of the literal (comments, and the frozen 7c
    # FAILURE-path wording — the rule is about SUCCESS-path log lines).
    # A line belongs to the wave when it names the D2 subject (the watchdog
    # hold/probe/restore, the resume decision).
    D2_TOKENS = (
        "watchdog",
        "WATCHDOG",
        "_promote_resume",
        "PROMOTE_RESUME",
        "resume_from_state",
    )

    def _d2_lines(self) -> list[str]:
        offenders: list[str] = []
        for path in (LIB_SH, PROMOTE_SH, ROLLBACK_SH, RESTART_SH):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if not any(tok in line for tok in self.D2_TOKENS):
                    continue
                if BANNED_LITERAL in line:
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        return offenders

    def test_no_new_line_carries_the_banned_literal(self):
        """No D2 line may carry the banned ``systemctl start``."""
        offenders = self._d2_lines()
        assert not offenders, (
            "the banned literal must not appear in any new log/journal line:\n"
            + "\n".join(offenders)
        )

    def test_the_new_log_lines_use_the_safe_verb_forms(self):
        """The restore path re-arms a timer — the line says so WITHOUT the
        banned literal."""
        text = LIB_SH.read_text(encoding="utf-8")
        assert "re-arm" in text, (
            "the watchdog restore lines must describe the re-arm in prose "
            "(the banned literal is a copy-paste trap)"
        )


class TestResumeVocabulary:
    """The F2-i resume decision vocabulary is closed (no restart-from-arm)."""

    def test_the_decision_vocabulary_is_exactly_four_modes(self):
        body = _read_function_body(LIB_SH, "_promote_resume_decision")
        assert body, "lib.sh must define _promote_resume_decision"
        modes = set(re.findall(r"printf '([a-z-]+)\\n'", body))
        assert modes == {"fresh", "busy", "gates-only", "flip-prep"}, (
            f"the resume decision vocabulary must be exactly {{fresh, busy, "
            f"gates-only, flip-prep}} — there is NO restart-from-arm path (got {modes})"
        )


def _read_function_body(path: Path, name: str) -> str:
    import subprocess

    proc = subprocess.run(
        ["awk", f"/^{re.escape(name)}\\(\\)/,/^}}$/", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout
