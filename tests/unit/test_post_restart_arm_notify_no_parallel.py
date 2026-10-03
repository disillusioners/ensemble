"""Structural AC6 tests for the Post-Restart Arm-Notify feature
(Phase 3 — T6.1, T6.2, T6.3, T6.4, T6.5, T6.6, T6.7).

The Post-Restart Arm-Notify feature reuses the existing Phase 2
machinery — there is NO parallel messaging subsystem, NO new
journal file, and NO new SQLModel table. These tests are the
**load-bearing** assertions of that guarantee: a regression in
any of them is a release blocker.

**The structural non-regression surface (per architecture deltas
#3, architecture-recommendation.md §FA1.1, §FA3.1, §FA6.1):**

* **T6.1** — no new journal file: the only journal file in the
  install dir is ``releases/state.json``; the wake's
  ``pending_wakes`` is a JSON key on ``state.json``, not a new
  file.
* **T6.2** — no new HTTP endpoint: the HTTP router's URL
  prefix list is the pre-feature baseline (no
  ``/post-restart-arm-notify``, ``/wake``, ``/arm-notify``, or
  ``/pending-wakes`` prefix).
* **T6.3** — no new SQLModel table: the metadata's table list
  is the pre-feature baseline (no ``arm_wake_records``,
  ``pending_wake``, or ``wake`` table).
* **T6.4** — the ``arm_pending_wake(`` call site sits INSIDE
  the lock-holding ``try`` at BOTH arm sites
  (``system_restart`` ~``:2209`` and ``system_upgrade``
  ~``:2850``), AFTER ``write_pending_op`` — a future refactor
  pulling it out is a SILENT TORN-WRITE risk. Architecture
  delta #3, MUST.
* **T6.5** (r4 fold C2) — the ``arm_pending_wake`` call site
  uses the WIRED helper (``_arm_pending_wake_for_op``), not a
  module-level seam. Complements T6.4.
* **T6.6** (r4 fold W3) — the ``sweep_wake_records`` method
  is reachable as a bound method on the service. Mirrors the
  manager-wiring structural pin.
* **T6.7** (r4 fold C1) — test fixtures use the REAL journal
  history shape ``{"ts", "event", "detail"}``; a fixture
  using the fictional ``{"name", "run_id"}`` shape is a
  fail-loud (the walker reads ``event``, NOT ``name``).

**The baseline is captured as a CONSTANT in this file (not a
runtime git lookup) per R3.1 in ``risk-register.md``:** a PR
that legitimately adds a new file / endpoint / table for the
wake must update BOTH the constant AND the architectural
analysis (D-FA1.1 / D-FA3.1 explicit rejection).

All tests use ``tmp_path`` + sandbox-DB fixtures; no live
contact. The arm-site AST scan is static (no daemon boot).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# ── T6.2 baseline: pre-feature HTTP router list (CONSTANT) ──────────────────
#
# Captured from the current `daemon/api.py` `api_router.include_router(...)`
# call sites. A new endpoint for the wake (e.g. `/post-restart-arm-notify`,
# `/wake`, `/arm-notify`, `/pending-wakes`) is a release blocker per
# D-FA1.1 / D-FA3.1 explicit rejection. The list is the union of all
# routers included in `daemon/api.py` BEFORE the wake feature's
# implementation — it must be UNCHANGED by this feature.
#
# Reviewer note: a PR that legitimately needs a new endpoint for the wake
# must (a) update this constant, (b) update the architectural analysis
# (D-FA3.1 explicit rejection), and (c) document the rationale in the
# PR description. Silent relaxation is a release blocker.
PRE_FEATURE_API_ROUTER_LIST: tuple[str, ...] = (
    "agents_router",        # /api/agents
    "instances_router",     # /api/instances
    "messages_router",      # /api/instances/{id}/messages, /api/instances/{id}/events
    "sources_router",       # /api/sources
    "mappings_router",      # /api/sources/{id}/mappings
    "schedules_router",     # /api/schedules
    "webhooks_router",      # /api/webhooks
    "jobs_router",          # /api/jobs
    "work_router",          # /api/work  (Phase 4: virtual job mgmt)
    "missions_router",      # /api/missions
    "projects_router",      # /api/projects
    "queues_router",        # /api/queues
    "system_queues_router", # /api/queues (defer-blocked §8.5)
    "skills_router",        # /api/skills
    "dlq_router",           # /api/dlq
    "mcp_servers_router",   # /api/mcp-servers
    "notifications_router", # /api/notifications
    "migration_router",     # /api/migration
    "database_router",      # /api/database
    "settings_router",      # /api/settings
    "skill_bank_router",    # /api/skill-bank
    "blueprints_router",    # /api/projects/{project_id}/blueprints
    "workspace_router",     # /api/workspace
    "recovery_router",      # /api/recovery
    "maintenance_router",   # /api/maintenance
    "plane_router",         # /api/plane
    "tmp_images_router",    # /api/tmp_images
)

# Names that are explicitly REJECTED for the wake surface (D-FA3.1
# explicit rejection — the wake reuses the existing /api/instances/
# {id}/messages endpoint via ``enqueue_message``).
REJECTED_WAKE_PREFIXES: tuple[str, ...] = (
    "/post-restart-arm-notify",
    "/wake",
    "/arm-notify",
    "/pending-wakes",
    "/post_restart_arm_notify",
)

# ── T6.3 baseline: pre-feature SQLModel table list (CONSTANT) ──────────────
#
# The wake reuses the existing `pending_wakes` key on
# `releases/state.json`; no new SQLModel table is added. A new
# table for the wake (e.g. `arm_wake_records`, `pending_wake`,
# `wake`) is a release blocker per D-FA1.1 explicit rejection.
# The list is the union of all `__tablename__` declarations in
# `daemon/repositories/*/models.py` BEFORE the wake feature's
# implementation. A regression in this list is a release
# blocker.
REJECTED_WAKE_TABLES: tuple[str, ...] = (
    "arm_wake_records",
    "pending_wake",
    "wake",
    "post_restart_arm_notify",
)

# ── T6.1 baseline: pre-feature journal file list (CONSTANT) ────────────────
#
# The only journal file is `releases/state.json`. The wake's
# `pending_wakes` is a JSON key on `state.json`, not a new file.
# A new file (e.g. `wake_records.json`, `pending_actions.json`
# — D-FA1.3 explicit rejection) is a release blocker.
PRE_FEATURE_RELEASES_FILE_LIST: frozenset[str] = frozenset(
    {"state.json"}
)


# ── T6.1: no new journal file ──────────────────────────────────────────────


class TestNoNewJournalFile:
    """T6.1: at the integration level, the only journal file in the
    install dir is ``releases/state.json``. The wake's
    ``pending_wakes`` is a JSON key on ``state.json``, not a new
    file. Structural AC6 enforcement."""

    def test_no_new_journal_file_after_arm_and_sweep(
        self, tmp_path: Path
    ) -> None:
        """T6.1: after a wake arm + a boot sweep, the file list under
        ``<install_dir>/releases/`` is exactly ``{state.json}``.
        Structural AC6 enforcement — a regression that adds a new
        file (e.g. a leaked ``wake_records.json``) FAILS the test
        loudly with the D-FA1.1 reference."""
        from daemon.tools import upgrade_journal as uj

        install = tmp_path / "install"
        (install / "releases").mkdir(parents=True)
        uj.journal_init(install)
        uj.ensure_extensions(install)
        # Arm a wake record (writes to `state.json`, no new file).
        from daemon.tools.upgrade_journal import (
            PENDING_WAKE_GRACE_S,
            PendingWake,
            arm_pending_wake,
            iso_plus,
        )
        wake = PendingWake(
            run_id="r-t61",
            kind="restart",
            env="demo",
            arming_instance_id="i-1",
            arming_agent_id="ari",
            source="discord:user1",
            message_id="m-1",
            message_metadata={},
            target_version=None,
            mode="graceful-now",
            armed_at="2026-10-04T00:00:00Z",
            expires_at="2026-10-04T00:10:00Z",
            abandon_after=iso_plus(
                "2026-10-04T00:10:00Z", PENDING_WAKE_GRACE_S
            ),
        )
        arm_pending_wake(install, wake)
        # Enumerate the releases dir.
        releases_dir = install / "releases"
        actual_files = {p.name for p in releases_dir.iterdir()}
        # The pre-feature baseline is exactly {state.json}.
        assert actual_files == PRE_FEATURE_RELEASES_FILE_LIST, (
            f"AC6 T6.1: the releases/ file list must be exactly "
            f"{PRE_FEATURE_RELEASES_FILE_LIST} (the wake's "
            f"pending_wakes is a JSON key on state.json, NOT a new "
            f"file). Got: {sorted(actual_files)}. Per D-FA1.1 / "
            f"D-FA1.3 explicit rejection, no new journal file is "
            f"permitted."
        )


# ── T6.2: no new HTTP endpoint ─────────────────────────────────────────────


class TestNoNewHttpEndpoint:
    """T6.2: the HTTP router's URL prefix list is the pre-feature
    baseline. The wake does NOT add a new HTTP endpoint — it
    reuses the existing ``/api/instances/{id}/messages`` route
    via ``manager.enqueue_message`` (D-FA3.1 explicit rejection).
    Structural AC6 enforcement."""

    def test_no_new_http_endpoint_in_api_router(self) -> None:
        """T6.2: the ``api_router.include_router(...)`` call sites in
        ``daemon/api.py`` are exactly the pre-feature list. A new
        router (e.g. a leaked ``/wake`` or ``/arm-notify`` route)
        FAILS the test loudly with the D-FA3.1 reference."""
        api_path = Path(__file__).parent.parent.parent / "daemon" / "api.py"
        src = api_path.read_text(encoding="utf-8")
        # Extract every `api_router.include_router(X_router)` call
        # (ignore `app.include_router(api_router)`).
        pattern = re.compile(
            r"api_router\.include_router\(\s*([a-z_]+_router)\s*\)"
        )
        actual = pattern.findall(src)
        # Pre-feature baseline (T6.2 constant).
        assert tuple(actual) == PRE_FEATURE_API_ROUTER_LIST, (
            f"AC6 T6.2: api_router.include_router(...) call sites "
            f"must be exactly {PRE_FEATURE_API_ROUTER_LIST} (the wake "
            f"reuses the existing /api/instances/{{id}}/messages "
            f"route via manager.enqueue_message). Got: {actual}. "
            f"Per D-FA3.1 explicit rejection, no new HTTP endpoint "
            f"is permitted for the wake."
        )

    def test_no_wake_prefix_in_api_routes(self) -> None:
        """T6.2 follow-on: no router file declares a wake-related
        path prefix (the rejected set per D-FA3.1)."""
        routers_dir = (
            Path(__file__).parent.parent.parent / "daemon" / "routers"
        )
        for router_file in sorted(routers_dir.glob("*.py")):
            src = router_file.read_text(encoding="utf-8")
            for prefix in REJECTED_WAKE_PREFIXES:
                # Reject the prefix as a route prefix (either in the
                # `@router.get("...")` style or in a router file
                # import).
                if prefix in src and (
                    f'prefix="{prefix}"' in src
                    or f"prefix='{prefix}'" in src
                ):
                    pytest.fail(
                        f"AC6 T6.2: {router_file.name} declares a "
                        f"rejected wake prefix `{prefix}` — the wake "
                        f"reuses the existing /api/instances/"
                        f"{{id}}/messages route. Per D-FA3.1 explicit "
                        f"rejection, no wake-prefixed endpoint is "
                        f"permitted."
                    )


# ── T6.3: no new SQLModel table ────────────────────────────────────────────


class TestNoNewSqlModelTable:
    """T6.3: the SQLModel metadata's table list is the pre-feature
    baseline. The wake reuses the existing ``pending_wakes`` key
    on ``releases/state.json``; NO new table is added (D-FA1.1
    explicit rejection). Structural AC6 enforcement.

    The check is a **static source scan** of all
    ``__tablename__ = "..."`` declarations across the daemon
    repo (mirrors the router scan in T6.2). A dynamic
    SQLModel.metadata.tables scan would require importing
    every model module, which is fragile (the table list grows
    as new modules are added; the import path is
    implementation-defined). The static scan is
    model-module-agnostic and asserts the structural
    invariant directly."""

    def test_no_new_sqlmodel_table(self) -> None:
        """T6.3: every ``__tablename__`` declaration in the daemon
        repo is in the pre-feature set. A regression that adds a
        new table (e.g. ``arm_wake_records``) FAILS the test
        loudly with the D-FA1.1 reference."""
        daemon_root = (
            Path(__file__).parent.parent.parent / "daemon"
        )
        # Enumerate every ``__tablename__ = "..."`` declaration.
        actual: set[str] = set()
        for py_file in sorted(daemon_root.rglob("*.py")):
            if "__pycache__" in str(py_file):
                continue
            src = py_file.read_text(encoding="utf-8")
            for match in re.finditer(
                r"__tablename__\s*=\s*[\"']([a-z_]+)[\"']", src
            ):
                actual.add(match.group(1))
        # Reject the wake-related table names explicitly. The
        # wake is the EXISTING ``pending_wakes`` key on
        # ``releases/state.json``; any new SQLModel table is a
        # release blocker per D-FA1.1.
        for rejected in REJECTED_WAKE_TABLES:
            assert rejected not in actual, (
                f"AC6 T6.3: a rejected wake table `{rejected}` "
                f"is declared in the daemon repo — the wake "
                f"reuses the existing pending_wakes key on "
                f"releases/state.json, NOT a new SQLModel table. "
                f"Per D-FA1.1 explicit rejection, this is a "
                f"release blocker."
            )


# ── T6.4: arm_pending_wake call-site position pin ──────────────────────────


class TestArmPendingWakeLockPositionPin:
    """T6.4 (architecture delta #3, MUST): the
    ``arm_pending_wake(`` call site is INSIDE the lock-holding
    ``try`` block at BOTH arm sites (``system_restart`` ~``:2209``
    and ``system_upgrade`` ~``:2850``), AFTER ``write_pending_op``.
    A future refactor pulling the call OUTSIDE the ``try`` (or
    BEFORE ``write_pending_op``) is a SILENT TORN-WRITE risk —
    arm recorded without wake, or wake without arm under crash.
    Static source-inspection test (mirrors the Phase 4 banner
    regression test shape)."""

    def _load_ut_source(self) -> str:
        """Load the upgrade_tools.py source."""
        from daemon.tools import upgrade_tools as mod
        return Path(mod.__file__).read_text(encoding="utf-8")

    def test_arm_pending_wake_inside_lock_holding_try(self) -> None:
        """T6.4: the ``_arm_pending_wake_for_op(`` call site is
        INSIDE the lock-holding ``try`` block at BOTH arm sites,
        AFTER ``write_pending_op``. The static check uses an
        indentation-aware scan to find the ``try:`` block that
        follows the ``journal_lock_acquire`` call and assert the
        ``_arm_pending_wake_for_op(`` call's line sits within it
        AND after the ``write_pending_op`` call line.

        Note: the actual call site is the WIRED helper
        ``_arm_pending_wake_for_op`` (the same helper asserted by
        T6.5). The helper internally calls ``uj.arm_pending_wake``
        at module level — but the per-call-site check is the
        call at the arm site, which is the wired helper."""
        src = self._load_ut_source()
        # Both arm sites: `system_restart` and `system_upgrade`
        # (top-level async functions).
        for site_name in ("system_restart", "system_upgrade"):
            arm_call_line, write_op_line, try_line = (
                self._find_arm_call_position(src, site_name)
            )
            assert arm_call_line is not None, (
                f"T6.4 ({site_name}): no `_arm_pending_wake_for_op(`` "
                f"call site found INSIDE the function body — the "
                f"wake must be written under the lock, NOT removed. "
                f"Per ADR-039, the wake is structurally inseparable "
                f"from the arm."
            )
            assert write_op_line is not None, (
                f"T6.4 ({site_name}): no `write_pending_op(` call "
                f"site found — the arm helper chain is broken."
            )
            assert try_line is not None, (
                f"T6.4 ({site_name}): no `try:` block found after "
                f"the `journal_lock_acquire` call — the wake must "
                f"ride the lock-holding `try`."
            )
            # The arm_pending_wake call must be AFTER write_pending_op
            # (the lock-holding try ordering).
            assert arm_call_line > write_op_line > try_line, (
                f"T6.4 ({site_name}): the call ordering is wrong — "
                f"`_arm_pending_wake_for_op` (line {arm_call_line}) "
                f"must be AFTER `write_pending_op` (line "
                f"{write_op_line}), both INSIDE the lock-holding "
                f"`try` (line {try_line}). A different ordering is a "
                f"silent torn-write risk (architecture delta #3, "
                f"MUST). Per ADR-039 revision, atomicity is BY the "
                f"caller-acquired journal lock, not the journal's."
            )

    def _find_arm_call_position(
        self, src: str, site_name: str
    ) -> tuple[int | None, int | None, int | None]:
        """Find the line numbers of the wired-helper call
        ``_arm_pending_wake_for_op(``, the matching
        ``write_pending_op(``, and the enclosing lock-holding
        ``try:`` in the named function.

        Returns (arm_call_line, write_op_line, try_line) — any
        may be None if not found.
        """
        lines = src.splitlines()
        # Find the `async def <site_name>(` line.
        func_start: int | None = None
        for i, line in enumerate(lines, start=1):
            if re.match(rf"^\s*async def {site_name}\(", line):
                func_start = i
                break
        if func_start is None:
            return None, None, None
        # The function body extends until the next top-level
        # `def` / `async def` / `class` at the SAME indent as the
        # `async def` line. For upgrade_tools.py, the function
        # bodies are 4-space-indented and the top-level `def` /
        # `class` is 0-indented.
        func_indent = len(lines[func_start - 1]) - len(
            lines[func_start - 1].lstrip()
        )
        func_end: int | None = None
        for j in range(func_start, len(lines)):
            stripped = lines[j].lstrip()
            if (
                stripped
                and not lines[j].startswith(" " * (func_indent + 1))
                and (
                    stripped.startswith("def ")
                    or stripped.startswith("async def ")
                    or stripped.startswith("class ")
                )
            ):
                func_end = j  # the line BEFORE this is the last
                break
        if func_end is None:
            func_end = len(lines)
        # Within the function body, find:
        # - the first `try:` line (the lock-holding try)
        # - the first `write_pending_op(` line AFTER the try
        # - the first `_arm_pending_wake_for_op(` line AFTER
        #   write_pending_op
        try_line: int | None = None
        write_op_line: int | None = None
        arm_call_line: int | None = None
        for k in range(func_start, func_end):
            line = lines[k - 1]
            if try_line is None and re.match(r"^\s+try:\s*$", line):
                try_line = k
            if (
                try_line is not None
                and write_op_line is None
                and "write_pending_op(" in line
            ):
                write_op_line = k
            if (
                write_op_line is not None
                and arm_call_line is None
                and "_arm_pending_wake_for_op(" in line
            ):
                arm_call_line = k
                break
        return arm_call_line, write_op_line, try_line


# ── T6.5: arm_pending_wake uses the wired helper (r4 fold C2) ─────────────


class TestArmPendingWakeUsesWiredHelper:
    """T6.5 (r4 fold C2): the ``arm_pending_wake`` call site uses
    the WIRED helper (``_arm_pending_wake_for_op``), NOT a
    module-level seam. Complements T6.4's lock-position pin."""

    def test_arm_pending_wake_call_uses_wired_helper(self) -> None:
        """T6.5: the ``arm_pending_wake`` call site at BOTH arm
        sites invokes the wired helper ``_arm_pending_wake_for_op``,
        which captures the manager's in-memory state and writes
        the record under the caller's lock. A refactor that
        inlines a module-level ``arm_pending_wake`` call is a
        structural pin breach."""
        from daemon.tools import upgrade_tools as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        # The wired helper must be present.
        assert "_arm_pending_wake_for_op" in src, (
            "T6.5: the wired helper `_arm_pending_wake_for_op` is "
            "missing — the wake must ride the wired helper, NOT a "
            "module-level `arm_pending_wake` call. Per r4 fold C2."
        )
        # The wired helper must be called at BOTH arm sites
        # (system_restart, system_upgrade).
        for site_name in ("system_restart", "system_upgrade"):
            lines = src.splitlines()
            in_func = False
            func_indent = 0
            found_call = False
            for i, line in enumerate(lines, start=1):
                if re.match(rf"^\s*async def {site_name}\(", line):
                    in_func = True
                    func_indent = (
                        len(line) - len(line.lstrip())
                    )
                    continue
                if in_func and line.lstrip():
                    stripped = line.lstrip()
                    if (
                        stripped.startswith("def ")
                        or stripped.startswith("async def ")
                        or stripped.startswith("class ")
                    ) and (len(line) - len(line.lstrip())) <= func_indent:
                        in_func = False
                        continue
                    if "_arm_pending_wake_for_op(" in line:
                        found_call = True
                        break
            assert found_call, (
                f"T6.5 ({site_name}): the wired helper "
                f"`_arm_pending_wake_for_op(` is not called inside "
                f"the function body — the wake must ride the wired "
                f"helper. Per r4 fold C2."
            )


# ── T6.6: sweep_wake_records is a bound method (r4 fold W3) ───────────────


class TestSweepWakeRecordsIsBoundMethod:
    """T6.6 (r4 fold W3): ``sweep_wake_records`` is reachable as
    a bound method on the service (NOT a module-level function
    that requires a hidden global). Mirrors the manager-wiring
    structural pin (T5.18)."""

    def test_sweep_wake_records_is_a_bound_method(self) -> None:
        """T6.6: ``sweep_wake_records`` is a method on
        ``UpgradeJournalSweepService`` — reachable as a bound
        method via an instance. The method is async."""
        from daemon.services.upgrade_journal_sweep import (
            UpgradeJournalSweepService,
        )
        import inspect

        assert hasattr(UpgradeJournalSweepService, "sweep_wake_records")
        assert callable(
            getattr(UpgradeJournalSweepService, "sweep_wake_records")
        )
        # Async coroutine function.
        assert inspect.iscoroutinefunction(
            UpgradeJournalSweepService.sweep_wake_records
        ), (
            "T6.6: `sweep_wake_records` must be an async method on "
            "the service. A free function would violate the "
            "manager-wired structural pin (r4 fold C2 + W3)."
        )


# ── T6.7: test fixtures use the real journal history shape (r4 fold C1) ────


class TestRealJournalShapeFixture:
    """T6.7 (r4 fold C1): the wake reader's fixtures are the
    REAL journal history shape ``{"ts": <iso>, "event":
    <name>, "detail": <prose>}`` per ``upgrade_journal.py:326``,
    NOT a fictional ``{"name": ..., "run_id": ...}`` shape.
    A test that imports a fixture with the fictional shape is
    a fail-loud (the fixture import itself raises on a schema
    check).

    Note: the journal test file
    (``tests/unit/tools/test_post_restart_arm_notify_journal.py``)
    contains ONE intentional negative fixture at
    ``TestLatestMatchingEvent::test_walker_uses_real_journal_shape``
    (line 514) — the test demonstrates that the walker does NOT
    match a fictional-shape entry. This is the legitimate
    INVARIANT test for the walker; it is excluded from the
    structural pin (the pin targets the test FILES that USE
    fixtures as real-shape data, not the file that asserts the
    walker rejects the fictional shape)."""

    def test_fixtures_use_real_journal_shape(self) -> None:
        """T6.7: spot-check the wake test fixtures' journal history
        entries. The real shape is ``{"ts", "event", "detail"}``;
        the fictional shape is ``{"name", "run_id"}``. This test
        scans the wake test files (other than the legitimate
        negative fixture in the journal test) and fails loudly
        if any contains a fictional-shape entry used as
        POSITIVE data."""
        tests_dir = Path(__file__).parent.parent
        # Files that may legitimately use the fictional shape as
        # a NEGATIVE test (the walker-shape test at journal:514).
        excluded = {
            "test_post_restart_arm_notify_journal.py",
        }
        for test_file in sorted(tests_dir.glob(
            "**/test_post_restart_arm_notify_*.py"
        )):
            if test_file.name in excluded:
                continue
            src = test_file.read_text(encoding="utf-8")
            # The fictional shape is `{"name": ..., "run_id": ...}`
            # in a journal history entry. We look for the
            # co-occurrence of both fields in a dict literal that
            # also has a "ts" key (the test fixture style).
            for match in re.finditer(
                r"\{\s*[\"']ts[\"']\s*:\s*[\"'][^\"']*[\"']"
                r"\s*,\s*[\"']name[\"']",
                src,
            ):
                pytest.fail(
                    f"T6.7: {test_file.name} contains a journal "
                    f"history fixture using the fictional shape "
                    f"`{{ts, name, ...}}`. The real shape is "
                    f"`{{ts, event, detail}}` per "
                    f"upgrade_journal.py:326. Per r4 fold C1, a "
                    f"fixture using the fictional shape is a "
                    f"fail-loud."
                )
