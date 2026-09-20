"""Terminal-write census pin (engine phase, Shape (b) §4).

Gates the event-driven completion contract
(``watch-notification-reliability/architecture-recommendation.md``,
"Engine Phase — Shape (b) Re-Cut"):

* every structurally-silent terminal write in ``daemon/`` must either be
  **hooked** (a ``notify_watchers`` call near the site, recorded as
  ``hooked_at``) or **exempt** (a reason carried as the on-call
  breadcrumb — e.g. the site-3 D2 carve-out);
* every ``hooked`` entry's ``hooked_at`` must point at a real
  ``notify_watchers`` call within ±5 lines — the fixture cannot rot
  silently;
* a NEW unlisted terminal-write shape fails RED with the three
  remediations (hook it / classify hooked with ``hooked_at`` / classify
  exempt with reason).

Mechanism: AST walk over ``daemon/`` collecting terminal-write shapes —
direct/to_thread-wrapped calls to the known terminal-write methods, plus
bulk-SQL blocks (``.values(...)`` / ``text(...)``) that write a terminal
``admission_state`` adjacent to a ``terminal_reason`` literal — and a
regex supplement for ``terminal_reason='<literal>'`` writer literals
inside those SQL blocks. The walker is the source of truth; the
``TERMINAL_WRITE_CENSUS`` fixture is the allow-list.

Fast by contract: pure AST + regex, no DB, no daemon, < 2s.

## How to update the census (the three paths)

(a) NEW SITE discovered by the walker (RED with the remediation message):
    hook it at the service/processor caller (pattern exemplar
    ``job_recovery_service._fail_orphaned_job``), then add one
    ``CensusEntry`` — ``file`` (repo-relative), ``site`` (the walker
    shape id: method name or ``bulk_sql:<reason-literal>``), ``anchor``
    (substring of the enclosing function name), ``classification``
    (``hooked``/``exempt``), ``hooked_at`` (``<file>:<line>`` of the
    ``notify_watchers`` call), and ``exempt_kind``/``reason`` when
    exempt.

(b) STALE ``hooked_at`` (``test_hooked_entries_point_at_live_notify_calls``
    fails with "hooked_at entries no longer point at a notify_watchers
    call" — this WILL recur when code above a hook shifts; precedent:
    1081→1087 in the site-1 fix round): re-locate the notify with
    ``grep -n "notify_watchers" <the entry's file>`` (or
    ``grep -rn "notify_watchers" daemon/services/`` for the neighborhood),
    then update that entry's ``hooked_at`` line number. No other field
    changes.

(c) HOOK REMOVED: flip the entry's ``classification`` to ``exempt``,
    set ``exempt_kind`` + a ``reason`` breadcrumb, and clear
    ``hooked_at``. If the site disappears entirely, delete the entry —
    the walker is the source of truth; the fixture only allow-lists.

## Exempt-kind taxonomy
``D2`` — one-time cutover carve-out (site 3; misrepresenting token).
``non-terminal`` — shape matched a method name but the transition is
not a terminal write (queued→active, processing→paused).
``dormant`` — boundary writer with zero in-tree callers; the notify
contract attaches at callers when first wired.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

DAEMON_ROOT = Path(__file__).resolve().parents[2] / "daemon"

# Terminal-write method surface (the checklist + the canonical boundary
# writers). A CALL (direct or asyncio.to_thread-wrapped argument) to any
# of these is a terminal-write shape.
TERMINAL_WRITE_METHODS = frozenset({
    "finalize_mirror_job_at_completion",
    "reconcile_terminal_message_mirrors",
    "reap_legacy_mirror_zombies",
    "batch_cancel_queued",
    "force_finalize_orphan",
    "atomic_transition",
})

TERMINAL_ADMISSION_TOKENS = ("'done'", '"done"', "'dead'", '"dead"', "DONE", "DEAD")


@dataclass(frozen=True)
class DiscoveredSite:
    file: str      # repo-relative posix path, e.g. daemon/services/...
    line: int      # 1-based lineno of the shape
    shape: str     # canonical shape id (method name or "bulk_sql")
    function: str  # enclosing function name


@dataclass
class CensusEntry:
    file: str
    site: str
    anchor: str          # substring matched inside the enclosing function name to pin the site
    classification: str  # "hooked" | "exempt"
    hooked_at: str = ""  # "<file>:<line>" of the notify_watchers call
    reason: str = ""
    exempt_kind: str = ""  # "D2" | "non-terminal" | "dormant" (exempt entries only)


def _enclosing_function_map(tree: ast.AST) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append((node.lineno, node.end_lineno or node.lineno, node.name))
    return spans


def _enclosing(spans: list[tuple[int, int, str]], lineno: int) -> str:
    candidates = [(lo, hi, name) for lo, hi, name in spans if lo <= lineno <= hi]
    if not candidates:
        return "<module>"
    return min(candidates, key=lambda t: t[1] - t[0])[2]


def discover_terminal_write_sites(root: Path = DAEMON_ROOT) -> list[DiscoveredSite]:
    """AST walk over ``daemon/`` collecting terminal-write shapes.

    Shapes:

    * **M1 (method call)** — a direct ``Call`` whose func attr is a
      known terminal-write method, OR a method reference passed as a
      call argument (the ``asyncio.to_thread(repo.method, ...)`` wrap).
    * **S3 (bulk-SQL write)** — a ``.values(...)`` / ``text(...)`` call
      whose source span contains BOTH an ``admission_state`` terminal
      write (``'done'`` / ``'dead'`` / ``DONE`` / ``DEAD``) and a
      ``terminal_reason`` literal — the structurally-silent writer
      shape. The regex supplement runs inside these spans only, so
      reason *strings* (sweep details, filter maps) never match.
    """
    discovered: list[DiscoveredSite] = []
    seen: set[tuple[str, int, str]] = set()
    repo_root = root.parent
    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(repo_root).as_posix()
        src = py.read_text()
        try:
            tree = ast.parse(src)
        except SyntaxError:  # pragma: no cover — daemon/ is importable
            continue
        spans = _enclosing_function_map(tree)
        lines = src.splitlines()
        for node in ast.walk(tree):
            # M1 — direct call to a terminal-write method.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in TERMINAL_WRITE_METHODS:
                    key = (rel, node.lineno, node.func.attr)
                    if key not in seen:
                        seen.add(key)
                        discovered.append(DiscoveredSite(
                            rel, node.lineno, node.func.attr,
                            _enclosing(spans, node.lineno),
                        ))
            # M1b — method reference passed as an argument
            # (asyncio.to_thread(repo.method, ...) wrap).
            if isinstance(node, ast.Call):
                for arg in node.args:
                    if isinstance(arg, ast.Attribute) and arg.attr in TERMINAL_WRITE_METHODS:
                        key = (rel, arg.lineno, arg.attr)
                        if key not in seen:
                            seen.add(key)
                            discovered.append(DiscoveredSite(
                                rel, arg.lineno, arg.attr,
                                _enclosing(spans, arg.lineno),
                            ))
            # S3 — bulk-SQL terminal write: .values(...) / text(...)
            # span carrying both an admission terminal write and a
            # terminal_reason literal.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"values", "text"}:
                    lo = node.lineno
                    hi = node.end_lineno or node.lineno
                    span = "\n".join(lines[lo - 1:hi])
                    has_terminal_admission = any(
                        tok in span for tok in TERMINAL_ADMISSION_TOKENS
                    )
                    has_reason = re.search(
                        r"terminal_reason\s*[:=]\s*['\"](\w+)['\"]", span
                    )
                    has_admission_col = "admission_state" in span
                    if has_terminal_admission and has_reason and has_admission_col:
                        token = has_reason.group(1)
                        key = (rel, lo, f"bulk_sql:{token}")
                        if key not in seen:
                            seen.add(key)
                            discovered.append(DiscoveredSite(
                                rel, lo, f"bulk_sql:{token}",
                                _enclosing(spans, lo),
                            ))
    return discovered


def _read(rel_path: str) -> str:
    return (DAEMON_ROOT.parent / rel_path).read_text()


def _notify_near(rel_path: str, line: int, window: int = 5) -> bool:
    """True when a ``notify_watchers`` call exists within ±``window``
    lines of ``line`` in ``rel_path`` (hooked_at anti-rot check)."""
    lines = _read(rel_path).splitlines()
    lo = max(0, line - 1 - window)
    hi = min(len(lines), line + window)
    return any("notify_watchers" in l for l in lines[lo:hi])


# ── Documented exemptions INVISIBLE to the walker (notes, not entries —
# an entry the walker never discovers is vacuous and rots silently;
# council fix round 2026-09-20):
#
# * reconcile_turn_mirror CASE (daemon/repositories/task/repository.py
#   ~:1281-1325 — checklist site 7) — EXEMPT. ⚠️ CAVEAT: this exemption
#   rests on the same-work_id-handle ARGUMENT + CAS-equivalence, NOT on
#   a pin: the CASE follows an ALREADY-terminal Task on the SAME
#   work_id handle whose delivery is owned by the canonical
#   task-terminal notify (task_processor on_success per-kind
#   'settled'/'completed'; complete/fail/cancel all notify). In-method
#   hooking is blocked by the §3 facade-only + repo-purity constraints
#   (task repository has no JobQueueService reach); a caller-side hook
#   would be a guaranteed CAS-noop (noise). The CASE's terminal_reason
#   is a bound param (not a literal), so shape S3 cannot see it — do
#   NOT loosen S3 to match (reason-string false positives would flood
#   the census). Re-open if a different-handle consumer ever appears.
#
# * reap_legacy_mirror_zombies DEFINITION (daemon/repositories/
#   job_queue/repository.py — checklist site 3) — invisible to shape M1
#   (fires on ast.Call only). The discovered forms are classified
#   exempt above: the WRITE (bulk_sql:orphan_retired) and the CALLER
#   (job_recovery_service.reconcile_drift_states), both carrying the
#   D2 carve-out reason.

# ── The fixture: the allow-list. Walker is the source of truth. ──────────
# One entry per discovered site. `anchor` is a substring of the
# enclosing function name (refactor-tolerant site pin). `hooked_at` is
# `<file>:<line>` of the notify_watchers call that covers the site —
# caller-level notify per the §3 placement contract, so repo-side write
# entries point at their caller's notify line.
TERMINAL_WRITE_CENSUS: list[CensusEntry] = [
    # ── Checklist site 1 — Fix-B inline mirror finalize ──
    CensusEntry(
        file="daemon/services/task_processor.py", site="finalize_mirror_job_at_completion",
        anchor="on_success", classification="hooked",
        hooked_at="daemon/services/task_processor.py:1087",
        reason="checklist site 1; post-commit mirror notify 'settled' (per-kind mirror token)",
    ),
    CensusEntry(
        file="daemon/repositories/job_queue/repository.py", site="bulk_sql:completed",
        anchor="finalize_mirror_job_at_completion", classification="hooked",
        hooked_at="daemon/services/task_processor.py:1087",
        reason="write side of checklist site 1; §3 placement = caller-level notify",
    ),
    # ── Checklist site 2 — F-1 terminal message mirrors ──
    CensusEntry(
        file="daemon/services/job_recovery_service.py", site="reconcile_terminal_message_mirrors",
        anchor="_reconcile_terminal_message_mirrors", classification="hooked",
        hooked_at="daemon/services/job_recovery_service.py:894",
        reason="checklist site 2; per-row post-commit notify 'settled'",
    ),
    CensusEntry(
        file="daemon/repositories/job_queue/repository.py", site="bulk_sql:completed",
        anchor="reconcile_terminal_message_mirrors", classification="hooked",
        hooked_at="daemon/services/job_recovery_service.py:894",
        reason="write side of checklist site 2; §3 placement = caller-level notify",
    ),
    # ── Checklist site 3 — legacy zombie reap (EXEMPT) ──
    CensusEntry(
        file="daemon/repositories/job_queue/repository.py", site="bulk_sql:orphan_retired",
        anchor="reap_legacy_mirror_zombies", classification="exempt",
        exempt_kind="D2",
        reason="write side of checklist site 3 — same D2 carve-out "
               "('orphan_retired' ∉ _TERMINAL_STATUSES); docstring carries the carve-out.",
    ),
    CensusEntry(
        file="daemon/services/job_recovery_service.py", site="reap_legacy_mirror_zombies",
        anchor="reconcile_drift_states", classification="exempt",
        exempt_kind="D2",
        reason="D2 carve-out: caller of the checklist site 3 exempt write — no notify.",
    ),
    # ── Checklist site 4 — batch_cancel_queued (special shape) ──
    CensusEntry(
        file="daemon/services/job_queue_service.py", site="batch_cancel_queued",
        anchor="cleanup_non_terminal_jobs", classification="hooked",
        hooked_at="daemon/services/job_queue_service.py:1296",
        reason="checklist site 4; pre-SELECT → atomic UPDATE → re-SELECT → notify 'cancelled' "
               "(race guard; RETURNING rejected)",
    ),
    CensusEntry(
        file="daemon/repositories/job_queue/repository.py", site="bulk_sql:cancelled",
        anchor="batch_cancel_queued", classification="hooked",
        hooked_at="daemon/services/job_queue_service.py:1296",
        reason="write side of checklist site 4; §3 placement = caller-level notify",
    ),
    # ── Checklist site 5 — force_finalize_orphan ──
    CensusEntry(
        file="daemon/services/job_queue_service.py", site="force_finalize_orphan",
        anchor="cleanup_non_terminal_jobs", classification="hooked",
        hooked_at="daemon/services/job_queue_service.py:1362",
        reason="checklist site 5; post-commit notify with the terminal_reason-arg token ('cancelled')",
    ),
    # ── Checklist site 6 — f1-DEAD pattern finalize ──
    CensusEntry(
        file="daemon/services/job_recovery_service.py", site="atomic_transition",
        anchor="_pattern_f_finalize_dead", classification="hooked",
        hooked_at="daemon/services/job_recovery_service.py:3886",
        reason="checklist site 6; in-function post-transition notify 'dead_letter'",
    ),
    # ── Canonical boundary writers (notify-wired before this phase) ──
    CensusEntry(
        file="daemon/services/job_queue_service.py", site="atomic_transition",
        anchor="_finalize_terminal", classification="hooked",
        hooked_at="daemon/services/job_recovery_service.py:766",
        reason="canonical terminal boundary; notify contract lives at callers "
               "(pattern exemplar _fail_orphaned_job:766)",
    ),
    CensusEntry(
        file="daemon/services/job_queue_service.py", site="atomic_transition",
        anchor="_finalize_terminal_sync", classification="hooked",
        hooked_at="daemon/services/job_recovery_service.py:766",
        reason="sync variant of the canonical boundary; same caller-side notify contract",
    ),
    CensusEntry(
        file="daemon/repositories/job_queue/repository.py", site="atomic_transition",
        anchor="complete_job", classification="hooked",
        hooked_at="daemon/services/job_queue_service.py:3967",
        reason="canonical boundary write; notify wired in the owning service method",
    ),
    CensusEntry(
        file="daemon/repositories/job_queue/repository.py", site="atomic_transition",
        anchor="fail_job", classification="hooked",
        hooked_at="daemon/services/job_retry_engine.py:467",
        reason="canonical boundary write; notify wired at the retry-engine fail path",
    ),
    CensusEntry(
        file="daemon/repositories/job_queue/repository.py", site="bulk_sql:cancelled",
        anchor="cancel_job", classification="hooked",
        hooked_at="daemon/services/job_queue_service.py:1175",
        reason="single-job cancel write; notify wired in the owning service method",
    ),
    CensusEntry(
        file="daemon/services/job_feedback_observer.py", site="atomic_transition",
        anchor="_finalize_job", classification="hooked",
        hooked_at="daemon/services/job_feedback_observer.py:1989",
        reason="observer finalize; its own fan-out notify is the delivery "
               "(§5: redundant belt, CAS-deduped) — observer stays byte-identical",
    ),
    CensusEntry(
        file="daemon/services/job_recovery_service.py", site="atomic_transition",
        anchor="_fail_orphaned_job", classification="hooked",
        hooked_at="daemon/services/job_recovery_service.py:766",
        reason="the §2 pattern exemplar — notify wired in-function",
    ),
    CensusEntry(
        file="daemon/services/job_recovery_service.py", site="atomic_transition",
        anchor="_pattern_f_finalize_done", classification="hooked",
        hooked_at="daemon/services/job_recovery_service.py:4074",
        reason="f2-DONE finalize; watcher fire/cancel notify wired in-function",
    ),
    CensusEntry(
        file="daemon/services/job_recovery_service.py", site="atomic_transition",
        anchor="_pattern_f_finalize_failed_terminal", classification="hooked",
        hooked_at="daemon/services/job_recovery_service.py:4217",
        reason="f-failed-terminal finalize; notify wired in-function",
    ),
    # ── Non-terminal transitions (shape-matched, not terminal writes) ──
    CensusEntry(
        file="daemon/repositories/job_queue/repository.py", site="atomic_transition",
        anchor="start_job_atomic", classification="exempt",
        exempt_kind="non-terminal",
        reason="non-terminal:  (queued→active) — matched on method name only",
    ),
    CensusEntry(
        file="daemon/services/job_queue_service.py", site="atomic_transition",
        anchor="start_job", classification="exempt",
        exempt_kind="non-terminal",
        reason="non-terminal:  (queued→active dispatch) — matched on method name only",
    ),
    CensusEntry(
        file="daemon/services/worker_pool.py", site="atomic_transition",
        anchor="_activate_message_jobitem_async_coro", classification="exempt",
        exempt_kind="non-terminal",
        reason="non-terminal: queued→active message activation — matched on method name only",
    ),
    CensusEntry(
        file="daemon/services/job_recovery_service.py", site="atomic_transition",
        anchor="recover_on_startup", classification="exempt",
        exempt_kind="non-terminal",
        reason="non-terminal: processing→paused — matched on method name only",
    ),
    CensusEntry(
        file="daemon/repositories/job_queue/repository.py", site="atomic_transition",
        anchor="terminate_job", classification="exempt",
        exempt_kind="dormant",
        reason="dormant: boundary writer — zero in-tree callers at census time; "
               "notify contract attaches at callers when first wired",
    ),
]


def _entry_for(site: DiscoveredSite) -> CensusEntry | None:
    """Match a discovered site to its fixture entry.

    File semantics: the fixture path matches on an EXACT name OR a
    path-suffix (``site.file.endswith("/" + entry.file)``) so a module
    move/rename keeps its classification visible to the walker (an
    exact-``==`` match would silently drop renamed files — no RED, no
    warning — defeating walker-as-source-of-truth). ``site`` matches
    the walker shape id; ``anchor`` is a substring of the enclosing
    function name.
    """
    for entry in TERMINAL_WRITE_CENSUS:
        file_match = (
            site.file == entry.file
            or site.file.endswith("/" + entry.file)
        )
        if (
            file_match
            and entry.site == site.shape
            and entry.anchor in site.function
        ):
            return entry
    return None


def _assert_sites_classified(sites: list[DiscoveredSite]) -> None:
    """The census assertion — shared by the green gate and the RED
    simulation so the failure message cannot drift from the gate."""
    missing = [site for site in sites if _entry_for(site) is None]
    assert not missing, (
        "UNLISTED TERMINAL-WRITE SITE(S) discovered in daemon/ — "
        "the notify contract (Shape (b) §3) requires one of:\n"
        "  1. HOOK it: fire `await job_queue_service.notify_watchers("
        "job_id, <canonical token>)` post-commit at the service/"
        "processor caller (pattern exemplar: "
        "job_recovery_service._fail_orphaned_job);\n"
        "  2. CLASSIFY it hooked: add a TERMINAL_WRITE_CENSUS entry "
        "with classification='hooked' and hooked_at=<file>:<line> of "
        "the notify call;\n"
        "  3. CLASSIFY it exempt: add an entry with "
        "classification='exempt' and the reason as the on-call "
        "breadcrumb.\n"
        + "\n".join(
            f"  - {s.file}:{s.line} shape={s.shape} "
            f"function={s.function}"
            for s in missing
        )
    )


class TestTerminalWriteCensus:
    def test_every_terminal_write_site_is_classified(self):
        """Walker is the source of truth; the fixture is the allow-list.

        A discovered site absent from ``TERMINAL_WRITE_CENSUS`` fails
        with its file:line + shape and the THREE remediations.
        """
        _assert_sites_classified(discover_terminal_write_sites())

    def test_unlisted_site_fails_red_with_remediations(self, tmp_path):
        """RED-path simulation (design §7.3): a file carrying an
        unlisted terminal-write shape is DISCOVERED by the walker and
        matches NO fixture entry — i.e. the classification test fails
        RED with the remediation message. Uses a tmp root — never
        writes into daemon/."""
        (tmp_path / "daemon").mkdir()
        (tmp_path / "daemon" / "future_writer.py").write_text(
            "class FutureRepo:\n"
            "    def finalize_thing(self):\n"
            "        with Session(engine) as session:\n"
            "            stmt = (\n"
            "                update(JobItem)\n"
            "                .values(\n"
            "                    admission_state='done',\n"
            "                    terminal_reason='completed',\n"
            "                )\n"
            "            )\n"
        )
        from tests.job_queue.test_terminal_write_census import (
            discover_terminal_write_sites as _d,
            _assert_sites_classified,
        )
        sites = _d(tmp_path / "daemon")
        synthetic = [
            s for s in sites if s.file.endswith("future_writer.py")
        ]
        assert synthetic and synthetic[0].shape.startswith("bulk_sql:"), (
            "walker failed to discover the synthetic terminal write"
        )
        # Drive the REAL classification assertion — it must go RED and
        # the raised message must name the site and the THREE
        # remediations.
        with pytest.raises(AssertionError) as excinfo:
            _assert_sites_classified(sites)
        message = str(excinfo.value)
        for needle in (
            "UNLISTED TERMINAL-WRITE SITE",
            "1. HOOK it",
            "2. CLASSIFY it hooked",
            "3. CLASSIFY it exempt",
            "future_writer.py",
        ):
            assert needle in message, f"remediation message missing {needle!r}"

    def test_hooked_entries_point_at_live_notify_calls(self):
        """Anti-rot: every ``hooked`` entry's ``hooked_at`` must point at
        a real ``notify_watchers`` call within ±5 lines."""
        stale = []
        for entry in TERMINAL_WRITE_CENSUS:
            if entry.classification != "hooked":
                continue
            assert ":" in entry.hooked_at, (
                f"hooked entry {entry.file} {entry.site} ({entry.anchor}) "
                f"is missing hooked_at"
            )
            path, _, line = entry.hooked_at.rpartition(":")
            if not _notify_near(path, int(line)):
                stale.append(entry.hooked_at)
        assert not stale, (
            f"hooked_at entries no longer point at a notify_watchers "
            f"call (±5 lines) — the hook moved; update the census "
            f"fixture: {stale}"
        )
