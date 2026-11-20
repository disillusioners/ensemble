"""AC-D2-8 / AC-D2-9 — the D2 HOST-PROBE CONTRACT (self-service-promote Wave 4).

The subject
-----------
The host's keepalive/watchdog TIMER can fire inside the ceremony's
stop→verified window and bring the daemon up on the WRONG release. The
design's answer is "probe, don't assume" (design §2.3, "Host-probe
contract"): a bounded read of the host's timer census + the CLASSIFIED
daemon unit's state, and a PUBLISHED candidate predicate.

What this file pins
-------------------
* **AC-D2-9** — the candidate predicate, behaviourally, cell by cell:
  unit-resolution ⇒ candidate; unresolvable-ExecStart + family token ⇒
  unknown-candidate (TREATED as a watchdog); unrelated ⇒ not. Plus the two
  remaining published arms (the configured timer name; a keepalive/watchdog
  ExecStart path). The predicate is driven through the REAL shell function
  (``_watchdog_candidate_verdict`` in scripts/upgrade/lib.sh) — the contract
  is the behaviour, not a comment.
* **AC-D2-8** — the probe is asserted POSITIVELY: the ceremony READS the
  watchdog's state at runtime and JOURNALS it. The repo comment about
  ``scripts/watchdog-watcher.sh`` is LEFT INTACT (it is true of THAT
  artifact — the C-7 category error): the pin asserts the PROBE, never a
  rewritten comment.

Fences
------
* Every shell invocation is a bounded child (``bash -c`` with the lib
  sourced) with a scrubbed env (only PATH/HOME) — the ambient developer
  shell (which can leak a live POSTGRES_DB) cannot colour a result.
* No live state: the predicate is PURE (no I/O); the source-level pins
  only read the repo's own files.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_SH = REPO_ROOT / "scripts" / "upgrade" / "lib.sh"
PROMOTE_SH = REPO_ROOT / "scripts" / "upgrade" / "promote.sh"
ROLLBACK_SH = REPO_ROOT / "scripts" / "upgrade" / "rollback.sh"
RESTART_SH = REPO_ROOT / "scripts" / "upgrade" / "restart.sh"

# A FIXTURE unit name — never a live unit name (the predicate is pure, but
# the fixture keeps the failure message free of live host detail).
DAEMON_UNIT = "fixture-daemon.service"
KEEPALIVE_TIMER = "ensemble-keepalive.timer"
UNRELATED_TIMER = "fixture-unrelated.timer"


def _run_shell(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Source lib.sh in a bounded child with a scrubbed env."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"\n{script}'],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _verdict(
    timer: str,
    execstart: str,
    family: int = 0,
    daemon_unit: str = DAEMON_UNIT,
    configured: str = "",
) -> str:
    """The verdict the REAL shell predicate returns for one cell."""
    args = " ".join(
        shlex.quote(a) for a in (timer, execstart, str(family), daemon_unit, configured)
    )
    proc = _run_shell(f"_watchdog_candidate_verdict {args}")
    assert proc.returncode == 0, f"predicate call failed: rc={proc.returncode} {proc.stderr}"
    return proc.stdout.strip()


def _function_body(path: Path, name: str) -> str:
    """The body of one shell function (awk-extracted, house precedent)."""
    proc = subprocess.run(
        ["awk", f"/^{re_escape(name)}\\(\\)/,/^}}$/", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout


def re_escape(text: str) -> str:
    return text.replace(".", "\\.")


# ── AC-D2-9 — the candidate predicate (behavioural) ─────────────────────────


class TestCandidatePredicate:
    """The published predicate (design §2.3), cell by cell."""

    def test_unit_resolution_is_a_candidate(self):
        """The ExecStart references the RESOLVED daemon unit with a start verb."""
        verdict = _verdict(
            UNRELATED_TIMER,
            f"/usr/local/bin/ctl --verb=start {DAEMON_UNIT}",
        )
        assert verdict == "candidate", (
            "an ExecStart that starts the classified daemon unit IS a watchdog "
            f"candidate (got {verdict!r})"
        )

    def test_unresolvable_execstart_with_family_token_is_unknown_candidate(self):
        """An unreadable ExecStart on a family-token timer ⇒ unknown-candidate."""
        verdict = _verdict(KEEPALIVE_TIMER, "")
        assert verdict == "unknown-candidate", (
            "an unreadable ExecStart on a keepalive/watchdog timer must be an "
            f"unknown-candidate (TREATED as a watchdog — fail-closed), got {verdict!r}"
        )

    def test_unrelated_timer_is_not_a_candidate(self):
        """An unrelated timer is NEVER held."""
        verdict = _verdict(UNRELATED_TIMER, "/usr/local/bin/some-other-tool")
        assert verdict == "not", f"an unrelated timer must not be a candidate (got {verdict!r})"

    def test_unreadable_execstart_without_family_token_is_not_a_candidate(self):
        """The unknown-candidate arm needs the FAMILY token (keepalive|watchdog)."""
        verdict = _verdict(UNRELATED_TIMER, "")
        assert verdict == "not", (
            "an unreadable ExecStart on a NON-family timer is not a candidate "
            f"(got {verdict!r})"
        )

    def test_configured_timer_name_is_a_candidate(self):
        """The operator-declared timer name is a candidate regardless of its ExecStart."""
        verdict = _verdict(UNRELATED_TIMER, "/usr/local/bin/some-other-tool", 0, DAEMON_UNIT, UNRELATED_TIMER)
        assert verdict == "candidate", (
            f"the CONFIGURED timer name must be a candidate (got {verdict!r})"
        )

    def test_keepalive_execstart_path_is_a_candidate(self):
        """A keepalive/watchdog ExecStart path is a candidate."""
        verdict = _verdict(UNRELATED_TIMER, "/usr/local/bin/ensemble-keepalive.sh")
        assert verdict == "candidate", (
            f"a keepalive ExecStart path must be a candidate (got {verdict!r})"
        )

    def test_the_predicate_is_pure(self):
        """The predicate performs no I/O (it is a pure function of its inputs)."""
        body = _function_body(LIB_SH, "_watchdog_candidate_verdict")
        assert body, "lib.sh must define _watchdog_candidate_verdict (the D2 predicate)"
        for forbidden in ("systemctl", "curl", "lsof", "journal_"):
            assert forbidden not in body, (
                f"the candidate predicate must stay PURE — it references {forbidden!r}"
            )

    def test_the_predicate_never_hardcodes_a_unit_name(self):
        """The daemon unit is an INPUT (the CLASSIFIED one), never a literal."""
        body = _function_body(LIB_SH, "_watchdog_candidate_verdict")
        assert body, "lib.sh must define _watchdog_candidate_verdict (the D2 predicate)"
        assert "ensemble-main" not in body, (
            "the predicate must never hardcode a daemon unit name — the resolved "
            "(classified) unit is passed in"
        )


# ── AC-D2-8 — the probe is asserted POSITIVELY ─────────────────────────────


class TestProbeContract:
    """The ceremony READS the watchdog's state at runtime and journals it."""

    def test_lib_defines_the_probe(self):
        body = _function_body(LIB_SH, "_watchdog_probe")
        assert body, "lib.sh must define _watchdog_probe (the D2 host probe)"

    def test_the_probe_reads_the_classified_daemon_unit(self):
        body = _function_body(LIB_SH, "_watchdog_probe")
        assert "SUPERVISION_UNIT" in body, (
            "the probe must read the CLASSIFIED daemon unit (SUPERVISION_UNIT)"
        )
        assert "ensemble-main" not in body, (
            "the probe must never hardcode a daemon unit name"
        )

    def test_the_probe_reads_the_timer_census(self):
        body = _function_body(LIB_SH, "_watchdog_probe")
        assert "list-unit-files" in body and "--type=timer" in body, (
            "the probe must enumerate the host's timers (list-unit-files --type=timer)"
        )

    def test_the_probe_probes_is_failed(self):
        """The §2.3 🟡 blind pin: the pipeline never probed is-failed before."""
        body = _function_body(LIB_SH, "_watchdog_probe")
        assert "is-failed" in body, (
            "the probe must read is-failed (the blind pin the design names)"
        )

    def test_the_probe_reads_the_enable_state(self):
        """The F14 enable-state discriminator (UnitFileState / is-enabled)."""
        body = _function_body(LIB_SH, "_watchdog_probe")
        assert "UnitFileState" in body, (
            "the probe must read the unit's enable state (UnitFileState)"
        )

    def test_every_probe_read_is_bounded(self):
        """No UNBOUNDED systemctl call in the probe (the ≤20s total bound)."""
        body = _function_body(LIB_SH, "_watchdog_probe")
        for line in body.splitlines():
            if "_watchdog_probe_read" not in line:
                continue
            assert "$SYSTEMCTL_BIN" not in line or "_watchdog_probe_read" in line, (
                f"probe reads must ride the bounded reader: {line.strip()!r}"
            )
        assert "_run_bounded" in _function_body(LIB_SH, "_watchdog_probe_read"), (
            "the probe reader must be bounded (_run_bounded)"
        )

    def test_the_total_probe_budget_is_at_most_20s(self):
        text = LIB_SH.read_text(encoding="utf-8")
        assert 'WATCHDOG_PROBE_TOTAL_BUDGET_S:-}" 20' in text, (
            "the D2 probe's TOTAL budget must default to ≤20s (design §2.3)"
        )

    def test_promote_runs_the_probe_and_journals_it(self):
        text = PROMOTE_SH.read_text(encoding="utf-8")
        assert "_watchdog_probe" in text, "promote.sh must run the D2 host probe"
        assert "journal_history_append watchdog_probe" in text, (
            "promote.sh must JOURNAL the probe result (AC-D2-8: the ceremony "
            "reads the watchdog state at runtime and journals it)"
        )

    def test_an_unreadable_probe_halts_before_any_stop(self):
        """Fail-closed: the unusable probe ⇒ HALT (the existing halt + exit-78 class)."""
        text = PROMOTE_SH.read_text(encoding="utf-8")
        assert "watchdog_probe_unreadable" in text, (
            "the unreadable-probe refusal must carry the named reason"
        )
        probe_at = text.index("_watchdog_probe")
        halt_at = text.index("watchdog_probe_unreadable", probe_at)
        stop_at = text.index("stop_via_stop_script", probe_at)
        assert probe_at < halt_at < stop_at, (
            "the probe must run BEFORE the halt arm, and the halt arm must be "
            "in place BEFORE any stop"
        )
        halt_window = text[halt_at:stop_at]
        assert "exit 78" in halt_window, (
            "the unreadable-probe refusal must exit 78 (the existing halt class)"
        )

    def test_the_repo_watchdog_comment_is_left_intact(self):
        """C-7/F17: the comment about scripts/watchdog-watcher.sh is TRUE of
        that artifact — the wave probes the host, it does not rewrite the
        comment."""
        promote = PROMOTE_SH.read_text(encoding="utf-8")
        rollback = ROLLBACK_SH.read_text(encoding="utf-8")
        assert "watchdog-watcher is NOTIFY-ONLY" in promote, (
            "promote.sh's watchdog-watcher NOTIFY-ONLY comment must stay intact"
        )
        assert "watchdog-watcher is NOTIFY-ONLY" in rollback, (
            "rollback.sh's watchdog-watcher NOTIFY-ONLY comment must stay intact"
        )
        assert "it never restarts anything" in promote, (
            "the 'never restarts anything' clause (true of the repo artifact) "
            "must stay intact"
        )


# ── the sibling paths carry the version fast-fail (AC-D2-5, P-5) ───────────


class TestSiblingPathVersionFastFail:
    """P-5 (the blind pin): rollback.sh/restart.sh never set
    PROMOTE_EXPECTED_VERSION ⇒ the version fast-fail was INACTIVE there."""

    def test_rollback_sets_the_expected_version(self):
        text = ROLLBACK_SH.read_text(encoding="utf-8")
        assert "PROMOTE_EXPECTED_VERSION=" in text, (
            "rollback.sh must set PROMOTE_EXPECTED_VERSION (AC-D2-5)"
        )

    def test_restart_sets_the_expected_version(self):
        text = RESTART_SH.read_text(encoding="utf-8")
        assert "PROMOTE_EXPECTED_VERSION=" in text, (
            "restart.sh must set PROMOTE_EXPECTED_VERSION (AC-D2-5)"
        )
