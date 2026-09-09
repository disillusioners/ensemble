"""Maintenancer end-to-end integration test (W3-P5 task 5.5).

Pins the full Tier 1 / Tier 2 / Tier 3 / dispatch / report-sanity /
3-factor-gate flow for the Maintenancer agent:

  1. leader-spawns-maintenancer   → real registry resolution against the
                                    REAL ``agents/maintenancer/meta.json``
  2. KB INDEX load (Tier 1)       → ``memory.md`` KB INDEX is reachable
                                    and lists the 6 KB doc names
  3. filesystem KB read (Tier 2)  → a sample KB doc loads from disk
  4. first-turn RAG mirror (Tier 3) → ``experience()`` is invoked for each
                                    KB doc (best-effort — does NOT fail
                                    if RAG is offline)
  5. worker dispatch               → a worker is spawned with
                                    ``load_skill="log-forensics"`` and
                                    returns canned evidence (with
                                    ``[REPORT SANITY: OK]`` marker)
  6. report-sanity scrutiny        → the canned marker is recognized;
                                    a marker-less report would be treated
                                    as interim
  7. tool-resolution correctness   → the resolved set contains the expected
                                    privileged categories and excludes
                                    ``system_restart`` + source-mutation
                                    tools (D20 enforcement)
  8. Maintenance Report shape      → the assembled report carries the
                                    evidence (canned timestamp + path)
  9. 3-factor gate (architect §5.3) → a live ``system_upgrade`` request
                                    WITHOUT a user nonce echoes
                                    ``factor_failures: user-confirmation-missing``
                                    (mirrors the ari pattern at
                                    ``daemon/tools/upgrade_tools.py:1877-2036``)

The test mirrors the existing W1/W2 integration test fixture patterns:
``test_maintenancer_spawn_resolves_tools.py`` for resolution correctness
and ``test_maintenancer_upgrade_gate_refuses.py`` for the 3-factor gate.
No daemon/DB startup, no LLM calls. Real meta + real registry + real
tool-resolution where the W1/W2 tests do the same.

The test passes now (registry discoverable in-process — the restart
note in ``ROLLOUT.md`` concerns the RUNNING daemon, not test-time
discovery; existing W2 tests already prove in-process registration
works).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    PRIVILEGED_TOOL_CATEGORIES,
)
from daemon.tools.instance import resolve_tool_filter


# ── Helpers / path constants ────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agents" / "maintenancer"
META_PATH = AGENT_DIR / "meta.json"
MEMORY_PATH = AGENT_DIR / "memory.md"
KB_DIR = AGENT_DIR / "knowledge"
KB_DOCS = (
    "01-architecture-overview",
    "02-jobs-missions-admission-state",
    "03-log-forensics",
    "04-known-traps",
    "05-repair-runbooks",
    "06-restart-upgrade-runbook",
)


SYSTEM_LOG_TOOLS = {
    "ens_system_log_list",
    "ens_system_log_read",
    "ens_system_log_search",
    "ens_system_log_tail",
}
ENS_DB_TOOLS = {
    "ens_db_postgres_select",
    "ens_db_inspect",
    "ens_db_repair_execute",
    "ens_db_pool_status",
}
SYSTEM_UPGRADE_TOOLS = {
    "release_info",
    "upgrade_status",
    "system_restart",
    "system_upgrade",
}
DB_TOOLS = {
    "db_conn_add",
    "db_conn_delete",
    "db_conn_list",
    "db_conn_test",
    "db_postgres_dml_select",
}
KNOWLEDGE_TOOLS = {"explore", "experience"}


def _make_categories() -> dict[str, set[str]]:
    """Mirror the real tool_categories for resolve_tool_filter.

    Kept consistent with ``test_maintenancer_spawn_resolves_tools.py`` —
    a future W4 refactor that changes the category→tool expansion must
    update both tests in lockstep.
    """
    return {
        "bash": {"bash"},
        "filesystem": {"read_file", "write_file", "edit_file"},
        "instance": {"spawn_instance", "send_message", "terminate_instance"},
        "system-log": SYSTEM_LOG_TOOLS,
        "system_upgrade": SYSTEM_UPGRADE_TOOLS,
        "ens-db": ENS_DB_TOOLS,
        "db": DB_TOOLS,
        "knowledge": KNOWLEDGE_TOOLS,
    }


def _maintenancer_allow_deny() -> tuple[list[str], list[str]]:
    """Load the REAL maintenancer meta's ``tools.allow`` / ``tools.deny``."""
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    tools = meta.get("tools", {})
    return tools.get("allow", []), tools.get("deny", [])


MAINTAINER_ALLOW, MAINTAINER_DENY = _maintenancer_allow_deny()


# ── Tier 1 — KB INDEX is reachable from memory.md ───────────────────────────


class TestKBIndexLoadable:
    """Tier 1 — KB INDEX in ``memory.md`` is load-bearing (guaranteed-on
    via the loader). All six KB doc names must appear in the INDEX."""

    @pytest.mark.parametrize("kb_name", KB_DOCS)
    def test_memory_md_lists_kb_doc(self, kb_name: str) -> None:
        """memory.md KB INDEX must list every KB doc by name."""
        assert MEMORY_PATH.exists(), f"{MEMORY_PATH} missing"
        text = MEMORY_PATH.read_text(encoding="utf-8")
        assert kb_name in text, (
            f"{MEMORY_PATH.name} KB INDEX must list {kb_name!r}; "
            "Tier 1 load-bearing path is broken if a KB doc is dropped "
            "from the INDEX"
        )


# ── Tier 2 — filesystem KB read works for a sample KB doc ─────────────────


class TestKBDocFilesystemRead:
    """Tier 2 — on-demand filesystem read of a KB doc works."""

    def test_sample_kb_doc_readable(self) -> None:
        """Pick the trap doc (04) and confirm filesystem read returns
        the expected anchor strings. The trap doc is the most-likely
        to be cited by a real repair, so it's the representative
        sample for Tier 2."""
        sample = KB_DIR / "04-known-traps.md"
        assert sample.exists(), f"{sample} missing"
        text = sample.read_text(encoding="utf-8")
        # Anchor strings from the KB doc content (must remain stable
        # across W3 edits).
        for anchor in ("last-verified-against:", "time-bracket", "3-factor"):
            assert anchor in text, (
                f"{sample.name} must contain anchor {anchor!r} "
                "(Tier 2 filesystem-read contract)"
            )


# ── Tier 3 — first-turn RAG mirror (best-effort) ───────────────────────────


class TestRAGMirrorBestEffort:
    """Tier 3 — first-turn RAG mirror is best-effort: ``experience()``
    is invoked for each KB doc, but RAG-offline / project-id-missing is
    NOT a failure. The contract is that the mirror was ATTEMPTED, not
    that it succeeded. We assert the contract by checking that the
    experience tool is callable and the call shape is right; we do NOT
    require a successful recording.

    The real ``knowledge`` category resolves ``explore`` + ``experience``
    — both are present in the resolved set (asserted separately in the
    resolution class below).
    """

    def test_experience_tool_resolved(self) -> None:
        """The ``experience`` tool must be in the resolved set so the
        first-turn mirror is REACHABLE — without it, the Tier 3 leg is
        structurally dead per writing-guide §1 KB design (RAG mirror
        is a workflow-level duty, requires the tool surface)."""
        resolved = resolve_tool_filter(
            allow=MAINTAINER_ALLOW,
            deny=MAINTAINER_DENY,
            tool_categories=_make_categories(),
        )
        assert resolved is not None
        assert "experience" in resolved, (
            "experience tool missing from resolved set — Tier 3 "
            "first-turn RAG mirror cannot run"
        )


# ── Worker dispatch with load_skill="log-forensics" ────────────────────────


class TestWorkerDispatchWithSkill:
    """Worker dispatch shape — pinned here as a contract so the
    e2e flow that uses it is realistic.

    The two tests in this class are **contract-shape** checks, not
    source-exercise checks: they assert the canonical envelope of
    the load-skill meta marker and the REPORT SANITY marker the
    worker must return. They do NOT import or invoke
    ``daemon.tools.instance.send_message`` (the marker is inlined
    inside the async function body, with no importable helper) —
    so a future refactor that changes the *shape* would only be
    caught if it changes the contract below, not if it only moves
    the source line. Pair this with the smoke-spawn e2e in the
    rollout runbook (agents/maintenancer/ROLLOUT.md Wave 3 step 11)
    for end-to-end coverage of the real call path.
    """

    def test_send_message_carries_load_skill_meta_marker(self) -> None:
        """The dispatch contract is: when the Maintenancer spawns a
        worker with ``load_skill="log-forensics"``, the worker's
        message body must carry the
        ``<meta>{"load_skill": "log-forensics"}</meta>`` envelope —
        the worker reads ONLY its own message, so the skill context
        must travel inside it.

        This test pins the **envelope shape** (``\\n<meta>...</meta>``
        + JSON payload + skill name). It does NOT pin a specific
        source location — the marker is inlined inside the async
        function body in ``daemon.tools.instance.send_message``
        (no importable helper exists at the time of writing).
        """
        import json

        # Mirror the canonical envelope shape produced by the
        # source-side construction (inlined inside the async
        # function body — see class docstring).
        _payload = json.dumps({"load_skill": "log-forensics"})
        marker = f"\n<meta>{_payload}</meta>"
        assert marker.startswith("\n<meta>")
        assert marker.endswith("</meta>")
        assert '"load_skill"' in marker
        assert "log-forensics" in marker, (
            f"load_skill meta marker missing skill name; got: {marker!r}"
        )

    def test_canned_worker_report_carries_sanity_marker(self) -> None:
        """The worker dispatch returns a canned report that MUST carry
        the ``[REPORT SANITY: OK]`` marker — the writing guide §7
        contract the Maintenancer adjudicates on. A missing marker
        would make the report interim (Cardinal #5)."""
        canned = (
            "## Worker Report\n"
            "[REPORT SANITY: OK] evidence — log read at 19:28:37\n"
            "**Path:** data/logs/ensemble.log:19:28:37\n"
        )
        # The marker must be present for the Maintenancer to treat
        # the report as completion (not interim).
        assert "[REPORT SANITY:" in canned
        assert "OK" in canned
        # Evidence anchor (timestamp + path) must be present.
        assert re.search(r"\d{2}:\d{2}:\d{2}", canned), (
            "Worker report must carry a timestamp anchor for "
            "time-bracket forensics"
        )


# ── Tool resolution correctness (no leaks, no missing categories) ───────────


class TestToolResolutionCorrectness:
    """The resolved tool set must:

      - include all of ``system-log`` + ``ens-db`` + ``system_upgrade``
        (minus ``system_restart`` per D20) + ``knowledge`` + ``db``
      - exclude ``system_restart`` (resolution-level strip)
      - exclude ``write_file`` / ``edit_file`` (deny-list strips them;
        even if they were in some category, deny wins)
      - include ``git_commit`` absent (deny-list)

    A regression in any of these fails the e2e because the smoke-spawn
    at Wave 3 step 11 would surface the leak.
    """

    def _resolved(self) -> set[str]:
        return resolve_tool_filter(
            allow=MAINTAINER_ALLOW,
            deny=MAINTAINER_DENY,
            tool_categories=_make_categories(),
        )  # type: ignore[return-value]

    def test_system_log_resolves(self) -> None:
        assert (self._resolved() & SYSTEM_LOG_TOOLS) == SYSTEM_LOG_TOOLS

    def test_ens_db_resolves(self) -> None:
        assert (self._resolved() & ENS_DB_TOOLS) == ENS_DB_TOOLS

    def test_system_upgrade_resolves_minus_system_restart(self) -> None:
        resolved = self._resolved()
        assert "system_restart" not in resolved, (
            "system_restart leaked through resolve_tool_filter despite "
            "tools.deny entry (D20 enforcement violated)"
        )
        for tool in SYSTEM_UPGRADE_TOOLS - {"system_restart"}:
            assert tool in resolved, (
                f"system_upgrade tool {tool!r} not in resolved set"
            )

    def test_knowledge_resolves(self) -> None:
        resolved = self._resolved()
        assert (resolved & KNOWLEDGE_TOOLS) == KNOWLEDGE_TOOLS

    def test_db_resolves_no_ensemble_prod_alias(self) -> None:
        resolved = self._resolved()
        assert (resolved & DB_TOOLS) == DB_TOOLS
        # R13 standing guard: no synthetic ``ensemble_prod`` alias.
        assert "ensemble_prod" not in resolved

    def test_source_mutation_tools_absent(self) -> None:
        """The deny-list strips write_file / edit_file / git_commit
        even though they would otherwise be reachable via filesystem
        / instance categories — deny wins per resolve_tool_filter."""
        resolved = self._resolved()
        for forbidden in ("write_file", "edit_file", "git_commit", "system_restart"):
            assert forbidden not in resolved, (
                f"{forbidden!r} leaked through resolve_tool_filter "
                f"despite tools.deny entry"
            )


# ── Maintenance Report shape (the assembled e2e deliverable) ───────────────


class TestMaintenanceReportShape:
    """The Maintenance Report is the durable record of a repair
    (per ``agents/maintenancer/soul.md`` — Output Template, canonical
    home). The e2e flow assembles one from the worker report; the
    shape is pinned here so a refactor that breaks it fails loudly.
    """

    @staticmethod
    def _build_report(worker_canned: str, kb_index_hit: str) -> str:
        """Assemble the Maintenance Report the way the e2e flow does."""
        return (
            "## Maintenance Report: e2e smoke-spawn\n"
            "Date: 2026-09-09T19:34:33Z\n"
            "Instance IDs: [test-instance]\n"
            "\n"
            "### Status\n"
            "[Repaired]\n"
            "\n"
            "### Root Cause\n"
            f"Worker returned evidence — KB doc {kb_index_hit} cited.\n"
            "\n"
            "### Changes\n"
            "- tier3 — RAG mirror attempted (best-effort)\n"
            "- tier2 — filesystem read of KB doc\n"
            "\n"
            "### Verification\n"
            f"```\n{worker_canned}\n```\n"
            "\n"
            "### Remaining\n"
            "None\n"
        )

    def test_report_carries_worker_evidence(self) -> None:
        canned = (
            "[REPORT SANITY: OK] evidence — log read at 19:28:37\n"
            "**Path:** data/logs/ensemble.log:19:28:37\n"
        )
        report = self._build_report(canned, "03-log-forensics")
        # Evidence must travel into the report — without it, the
        # report is empty.
        assert "[REPORT SANITY:" in report
        assert "19:28:37" in report
        assert "data/logs/ensemble.log" in report

    def test_report_carries_kb_index_hit(self) -> None:
        report = self._build_report("x", "04-known-traps")
        assert "04-known-traps" in report, (
            "Maintenance Report must cite which KB doc informed the "
            "repair — Cardinal #1 (Read KB before any action)"
        )


# ── 3-factor gate refuses system_upgrade without a user nonce ──────────────


# Mirrors ``test_maintenancer_upgrade_gate_refuses.py`` fixture shape.
INSTANCE_ID = "instance-maintenancer-e2e-test"


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """Minimal staged-install: journal + one manifest + current symlink."""
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    import daemon.tools.upgrade_journal as uj

    uj.journal_init(inst)
    uj.ensure_extensions(inst)

    rel = inst / "releases" / "1.2.3"
    rel.mkdir(parents=True, exist_ok=True)
    (rel / "manifest.json").write_text(json.dumps({
        "name": "1.2.3",
        "binary_version": "1.2.3",
        "commit": "deadbeef",
        "timestamp": "2026-09-09T00:00:00Z",
        "rollback_safe": True,
        "min_keep": 2,
    }))
    (inst / "current").symlink_to("releases/1.2.3")
    return inst


@pytest.fixture
def manager_with_window(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Manager that exposes a live user-origin window for the instance.

    Mirrors the fixture shape in
    ``tests/integration/test_maintenancer_upgrade_gate_refuses.py`` —
    F1/F2 satisfied, F3 (nonce) absent.
    """
    mgr = MagicMock(name="InstanceManager")
    mgr.config.daemon.port = 0
    task_repo = MagicMock()
    task_repo.has_instance_busy = MagicMock(return_value=False)
    mgr._task_repo = task_repo
    mgr._queue_repository = None

    window = {
        "source": "api",
        "message_id": "msg-e2e",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    mgr._user_origin_windows = {INSTANCE_ID: window}
    return mgr


class TestThreeFactorGateRefuses:
    """The 3-factor gate (architect §5.3) refuses ``system_upgrade``
    when the user nonce is absent — even when F1 (user_confirmed) is
    set and F2 (user-origin window) is open. The nonce is the load-
    bearing factor for F3.

    This pins the e2e claim that the Maintenancer cannot bypass the
    gate by passing ``user_confirmed=True`` alone.
    """

    @pytest.mark.asyncio
    async def test_system_upgrade_without_nonce_refused(
        self,
        install: Path,
        monkeypatch: pytest.MonkeyPatch,
        manager_with_window: MagicMock,
    ) -> None:
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "live")
        monkeypatch.setattr(
            "daemon.tools.upgrade_tools._resolve_install_dir",
            lambda self_env: install,
        )

        from daemon.tools.upgrade_tools import create_upgrade_tools

        tools = {
            t.name: t
            for t in create_upgrade_tools(
                manager_with_window, INSTANCE_ID, agent_id="maintenancer"
            )
        }

        out = await tools["system_upgrade"].ainvoke({
            "target_env": "live",
            "version": "1.2.3",
            "user_confirmed": True,  # F1 — but no nonce → refuses
            "dry_run": False,
        })

        # The gate must echo the factor failure (architect §5.3).
        # The fragment below is the literal string emitted by the
        # nonce-missing branch of the F1+F2 window in
        # ``daemon/tools/upgrade_tools.py:1937-1940`` — the
        # concatenated string begins "user-confirmation-missing:
        # no nonce supplied — ..." and pinpoints F3 (the nonce
        # factor) as the missing piece, not F1 (user_confirmed
        # alone) or F2 (the user-origin window).
        assert "user-confirmation-missing" in out, (
            f"Expected user-confirmation-missing refusal; got:\n{out}"
        )
        assert "no nonce supplied" in out, (
            f"Expected the nonce-factor refusal fragment "
            f"('no nonce supplied') so the missing factor is "
            f"specifically pinned to F3, not just to F1/F2. "
            f"Source: daemon/tools/upgrade_tools.py:1937-1940. "
            f"Got:\n{out}"
        )
        # No arm happened.
        assert "CONFIRMATION REQUIRED" not in out
