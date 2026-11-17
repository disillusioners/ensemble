"""Compact-note lane — the TOOL surface (``prompt_reload(compact,
compact_note)``).

The lane-owned, tool-level legs of the ``prompt_reload_compact_note_unit``
pack (plan §10.4):

* **L1** — param validation + the byte-identical ``prompt_reload()`` receipt
  (the golden is CAPTURED FROM THE PRE-CHANGE CODE — see
  ``GOLDEN_PLAIN_RECEIPT`` below — never hand-written).
* **L2** — the cap boundary (12,000 accepted / 12,001 refused, never
  truncated, nothing armed).
* **L3** — BOTH refusal families: all FIVE new codes reachable, each on its
  exact input, each in the house REFUSED envelope, each with ZERO carrier
  side effect and NO "compacted" claim.
* **L4** — seam safety: NO ``compact_state`` call during the tool call (the
  compaction happens at the next turn boundary, never mid-turn).
* **L7** — content-free logging: a sentinel planted in the note appears in
  ZERO log lines.
* **L12** — the ARMED receipt + the still-armed status line + the supersede
  disclosure + the never-"compacted" negative grep.
* **L17** — the v1.4 EXHAUSTIVENESS PIN: the expected-seam clause names
  EXACTLY three gates and states that the list is exhaustive.

Non-vacuity: the golden is captured from the base tree (see the module
docstring of the capture recipe in the lane's report); the negative grep is
a NEGATION-AWARE scan (the receipt's mandated line is "CONTEXT NOT YET
COMPACTED" and the deferred wordings are "… not compacted" — an affirmative
claim is what must be absent, and that is what is asserted).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import daemon.services.prompt_refresh as pr
from daemon.services.prompt_refresh import (
    ARMED_PENDING_APPEND,
    ARMED_STILL_ARMED_NO_DOC,
    CODE_COMPACT_DISABLED,
    CODE_COMPACT_RATE_LIMITED,
    CODE_INTERNAL_ERROR,
    CODE_NOTE_OVER_CAP,
    CODE_NOTE_REQUIRES_COMPACT,
    CODE_NOTE_REQUIRED,
    OUTCOME_ARMED,
    OUTCOME_ARMED_PARTIAL_WRITE,
)
from daemon.tools.prompt_reload import create_prompt_reload_tools

# ── The captured golden (PRE-CHANGE code) ─────────────────────────────────────
#
# Captured by running the BASE worktree
# (``/home/nea/ensemble-src-wt-prompt-reload-compact-base`` @ 858a85e31) with
# the frozen ``FROZEN_RESULT`` below and dumping ``repr()`` of the tool's
# return. The HEAD (post-change) run produces the IDENTICAL bytes — the
# byte-identity proof of the default path.
#
# The fixture is a FROZEN ``PromptRefreshResult`` (not the live agent dir),
# so the golden is deterministic and does not drift with the repo's agent
# files.
GOLDEN_PLAIN_RECEIPT = (
    "prompt_reload reloaded — Reloaded: prompt files changed — "
    "recomposed and the cached graph was popped.\n"
    "release: v0.29.0 (current_matches_running=true, daemon_version=0.29.0)\n"
    "manifest: ok(6 checked, 0 unmanifested-skipped)\n"
    "db_fingerprint: deadbeef (mcp_tool_names_source=empty, was_cached=true)\n"
    "manager_instances: 1->0\n"
    "persona diff: +2/-1 lines, 100→140 bytes\n"
    "headings added: ## New Section\n"
    "files: 1 changed of 2 in the loader read set\n"
    "  soul.md: sha256 aaa… → bbb…\n"
    "  db row: tools_note (CORE, selected=v3) — "
    + pr.S10_A_TOOLS_NOTE_DEAD
    + "\n"
    + pr.S10_C_DISPLAY_DIVERGENCE
    + "\n"
    + pr.S10_D_NEXT_TURN
    + "\n"
    + pr.S10_E_PROVIDER_CACHE
    + "\n"
    "[PROMPT-RELOADED outcome=reloaded next_turn=true]"
)

#: A frozen tier-b result — the shape the golden was captured against.
FROZEN_RESULT = pr.PromptRefreshResult(
    outcome=pr.OUTCOME_RELOADED,
    code=None,
    message="Reloaded: prompt files changed — recomposed and the cached graph was popped.",
    diff={
        "persona_diff": "exact",
        "lines_added": 2,
        "lines_removed": 1,
        "bytes_before": 100,
        "bytes_after": 140,
        "headings_added": ["## New Section"],
        "headings_removed": [],
        "files": [
            {"path": "soul.md", "sha_before": "aaa…", "sha_after": "bbb…", "changed": True},
            {"path": "rule.md", "sha_before": "ccc…", "sha_after": "ccc…", "changed": False},
        ],
    },
    provenance={
        "agent_id": "ari",
        "running_release": "v0.29.0",
        "current_matches_running": "true",
        "divergence_line": "",
        "daemon_version": "0.29.0",
        "manifest": "ok(6 checked, 0 unmanifested-skipped)",
        "db_fingerprint": "deadbeef",
        "mcp_tool_names_source": "empty",
        "was_cached": True,
        "manager_instances": "1->0",
        "db_rows": [
            {
                "artifact": "tools_note",
                "class": "CORE",
                "selected": "v3",
                "honesty": pr.S10_A_TOOLS_NOTE_DEAD,
            },
        ],
    },
)

#: The re-runnable golden digest of ``GOLDEN_PLAIN_RECEIPT`` (A2 — the
#: golden is a BYTE artifact, and this digest makes the artifact's
#: identity checkable without re-reading the string by eye). Measured on
#: the captured base-tree value; a change to the golden REDDENS this pin.
GOLDEN_PLAIN_RECEIPT_SHA256 = (
    "0592b4a0fe960b2894763a45f2eb6f80f1bfa42430f15a41648893ae907dd183"
)

#: The documented cap (architect §G — AUTHORITATIVE).
CAP = 12000

#: The ONE producible deferred state (v1.5 vocabulary sweep — the prior
#: anti-refire / below-floor literals were UNPRODUCIBLE and are deleted).
ALL_DEFERRED_WORDINGS = (
    ARMED_STILL_ARMED_NO_DOC,
)


class StubInstanceRepo:
    """An in-memory ``instances.metadata`` JSONB carrier stub.

    Exposes ONLY ``get`` / ``set_metadata`` / ``delete_metadata`` — the
    atomic helpers the architect's carrier ruling names. The generic
    ``update`` method is deliberately ABSENT (it REJECTS the
    ``instance_metadata`` kwarg by design in the real repository).
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {
            "iid-1": {"agent_id": "ari", "instance_metadata": {}}
        }
        self.set_metadata_calls: list[tuple[str, str, object]] = []
        self.delete_metadata_calls: list[tuple[str, str]] = []

    def _meta(self, instance_id: str) -> dict:
        return self.rows.setdefault(
            instance_id, {"agent_id": "ari", "instance_metadata": {}}
        )["instance_metadata"]

    def get(self, instance_id: str):
        row = self.rows.get(instance_id)
        if row is None:
            return None
        return SimpleNamespace(
            agent_id=row["agent_id"], instance_metadata=row["instance_metadata"]
        )

    def set_metadata(self, instance_id: str, key: str, value):
        self.set_metadata_calls.append((instance_id, key, value))
        self._meta(instance_id)[key] = value
        return self.get(instance_id)

    def delete_metadata(self, instance_id: str, key: str):
        self.delete_metadata_calls.append((instance_id, key))
        self._meta(instance_id).pop(key, None)
        return self.get(instance_id)

    @property
    def metadata(self) -> dict:
        return self.rows["iid-1"]["instance_metadata"]


class AtomicStubInstanceRepo(StubInstanceRepo):
    """The same stub, but exposing the atomic multi-key helper."""

    def set_metadata_many(self, instance_id: str, updates: dict):
        for key, value in updates.items():
            self._meta(instance_id)[key] = value
        return self.get(instance_id)


def _compaction_config(**overrides):
    base = {
        "enabled": True,
        "compact_note_max_chars": CAP,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class StubManager:
    """A stub manager with a config + a JSONB carrier."""

    def __init__(self, *, compaction=None, repo=None) -> None:
        self.instances = {"iid-1": (object(), "/nonexistent")}
        self._instance_repository = repo if repo is not None else StubInstanceRepo()
        self.config = SimpleNamespace(
            compaction=compaction if compaction is not None else _compaction_config()
        )
        self.refresh_calls: list[tuple[str, bool]] = []

    async def refresh_instance_prompt(self, instance_id: str, *, force: bool = False):
        self.refresh_calls.append((instance_id, force))
        return FROZEN_RESULT


def _tool(manager: StubManager, agent_id: str = "ari"):
    return create_prompt_reload_tools(
        manager, current_instance_id="iid-1", agent_id=agent_id
    )[0]


async def _call(manager: StubManager, *, compact: bool = False, compact_note: str = ""):
    payload = {"force": False}
    if compact:
        payload["compact"] = True
    if compact_note:
        payload["compact_note"] = compact_note
    return await _tool(manager).ainvoke(payload)


def _code_of(output: str) -> str:
    assert output.startswith("Error: prompt_reload "), output
    return output.split("reason=", 1)[1].split(":", 1)[0]


def _affirmative_compacted_claims(text: str) -> list[str]:
    """Occurrences of "compacted" that are NOT negations.

    The receipt's mandated line is "CONTEXT NOT YET COMPACTED" and the
    deferred wordings are "… not compacted" — both are NEGATIONS and both
    are required. What must be absent is an AFFIRMATIVE claim that the
    compaction ran ("compacted", "compaction complete", "context
    compacted", "summary rendered").
    """
    hits: list[str] = []
    lowered = text.lower()
    idx = 0
    while True:
        i = lowered.find("compacted", idx)
        if i < 0:
            break
        window = lowered[max(0, i - 16) : i]
        if "not" not in window:
            hits.append(text[max(0, i - 16) : i + 9])
        idx = i + 1
    return hits


@pytest.fixture(autouse=True)
def _clear_baseline(monkeypatch):
    pr._BASELINE_MAP.clear()
    monkeypatch.delenv("ENSEMBLE_PROMPT_RELOAD_ENABLED", raising=False)
    yield
    pr._BASELINE_MAP.clear()


# ── L1 — param validation + the byte-identical plain receipt ────────────────


class TestL1ParamValidationAndGolden:
    def test_args_schema_has_exactly_three_fields_with_the_right_defaults(self) -> None:
        from daemon.tools.prompt_reload import create_prompt_reload_tools as _c

        tool = _c(StubManager(), current_instance_id="iid-1", agent_id="ari")[0]
        schema = tool.args_schema
        assert schema is not None
        fields = schema.model_fields
        assert set(fields) == {"force", "compact", "compact_note"}
        assert fields["force"].default is False
        assert fields["compact"].default is False
        assert fields["compact_note"].default == ""

    async def test_plain_call_is_byte_identical_to_the_pre_change_golden(self) -> None:
        manager = StubManager()
        out = await _tool(manager).ainvoke({"force": False})
        assert out == GOLDEN_PLAIN_RECEIPT

    async def test_the_golden_artifact_digest_is_stable(self) -> None:
        """A2 — the golden is a re-runnable artifact: its sha256 is
        pinned, so a silent edit of the captured receipt REDDENS here."""
        import hashlib

        assert (
            hashlib.sha256(GOLDEN_PLAIN_RECEIPT.encode()).hexdigest()
            == GOLDEN_PLAIN_RECEIPT_SHA256
        )

    async def test_plain_call_touches_the_prompt_refresh_path_unchanged(self) -> None:
        manager = StubManager()
        await _tool(manager).ainvoke({"force": False})
        assert manager.refresh_calls == [("iid-1", False)]

    async def test_compact_false_with_a_blank_note_is_byte_identical(self) -> None:
        manager = StubManager()
        out = await _tool(manager).ainvoke(
            {"force": False, "compact": False, "compact_note": "   \n\t  "}
        )
        assert out == GOLDEN_PLAIN_RECEIPT


# ── L2 — the cap boundary ───────────────────────────────────────────────────


class TestL2CapBoundary:
    async def test_exactly_the_cap_is_accepted(self) -> None:
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="x" * CAP)
        assert f"note_chars={CAP}" in out
        assert f"cap={CAP}" in out
        assert manager._instance_repository.metadata["compact_note_arm"] == "x" * CAP

    async def test_one_over_the_cap_is_refused_with_chars_and_cap(self) -> None:
        manager = StubManager()
        note = "x" * (CAP + 1)
        out = await _call(manager, compact=True, compact_note=note)
        assert _code_of(out) == CODE_NOTE_OVER_CAP
        assert f"{CAP + 1}" in out
        assert str(CAP) in out
        # NOTHING armed, and the note was NEVER truncated.
        assert "compact_note_arm" not in manager._instance_repository.metadata

    async def test_the_cap_is_the_architects_value(self) -> None:
        from daemon.config import CompactionConfig

        assert CompactionConfig().compact_note_max_chars == CAP

    async def test_the_cap_is_co_pinned_against_the_global_token_cap(self) -> None:
        """Drift guard — the two caps are DISTINCT and must never be
        confused (architect §G)."""
        from daemon.compaction import GLOBAL_OVERVIEW_TOKEN_CAP

        assert CAP != GLOBAL_OVERVIEW_TOKEN_CAP
        assert GLOBAL_OVERVIEW_TOKEN_CAP < CAP


# ── L3 — both refusal families ──────────────────────────────────────────────


class TestL3RefusalFamilies:
    async def test_note_required_when_absent(self) -> None:
        manager = StubManager()
        out = await _call(manager, compact=True)
        assert _code_of(out) == CODE_NOTE_REQUIRED
        assert out.startswith("Error: prompt_reload REFUSED — reason=")
        assert "compact_note_arm" not in manager._instance_repository.metadata

    async def test_note_required_when_whitespace_only(self) -> None:
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="   \n\t  ")
        assert _code_of(out) == CODE_NOTE_REQUIRED
        assert "compact_note_arm" not in manager._instance_repository.metadata

    async def test_absent_and_whitespace_only_share_one_code(self) -> None:
        manager = StubManager()
        absent = await _call(manager, compact=True)
        blank = await _call(manager, compact=True, compact_note=" \t ")
        assert _code_of(absent) == _code_of(blank) == CODE_NOTE_REQUIRED

    async def test_no_second_empty_code_exists(self) -> None:
        assert "prompt_reload_compact_note_empty" not in pr.REFUSAL_CODES
        assert "prompt_reload_compact_note_required" not in pr.REFUSAL_CODES
        assert CODE_NOTE_REQUIRED in pr.REFUSAL_CODES

    async def test_note_requires_compact(self) -> None:
        manager = StubManager()
        out = await _call(manager, compact=False, compact_note="a note")
        assert _code_of(out) == CODE_NOTE_REQUIRES_COMPACT
        assert "compact_note_arm" not in manager._instance_repository.metadata

    async def test_compact_disabled(self) -> None:
        manager = StubManager(compaction=_compaction_config(enabled=False))
        out = await _call(manager, compact=True, compact_note="a note")
        assert _code_of(out) == CODE_COMPACT_DISABLED
        assert "compact_note_arm" not in manager._instance_repository.metadata

    async def test_compact_rate_limited_echoes_the_remaining_seconds(self) -> None:
        manager = StubManager()
        manager._instance_repository.metadata["compact_note_last_applied_at"] = (
            datetime.now(timezone.utc).isoformat()
        )
        out = await _call(manager, compact=True, compact_note="a note")
        assert _code_of(out) == CODE_COMPACT_RATE_LIMITED
        assert "s remaining" in out
        assert "compact_note_arm" not in manager._instance_repository.metadata

    async def test_the_rate_limit_is_a_hardcoded_module_constant(self) -> None:
        from daemon.constants import DELIBERATE_COMPACT_RATE_LIMIT_S

        assert DELIBERATE_COMPACT_RATE_LIMIT_S == 900
        # NOT a config knob.
        from daemon.config import CompactionConfig

        assert "rate_limit" not in " ".join(CompactionConfig.model_fields)

    async def test_the_rate_limit_window_has_expired_by_then(self) -> None:
        manager = StubManager()
        manager._instance_repository.metadata["compact_note_last_applied_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1000)
        ).isoformat()
        out = await _call(manager, compact=True, compact_note="a note")
        assert f"outcome={OUTCOME_ARMED}" in out

    async def test_every_refusal_carries_no_compacted_claim(self) -> None:
        manager = StubManager(compaction=_compaction_config(enabled=False))
        cases = [
            (True, ""),
            (True, "  "),
            (True, "x" * (CAP + 1)),
            (False, "a note"),
            (True, "a note"),
        ]
        for compact, note in cases:
            out = await _call(manager, compact=compact, compact_note=note)
            assert out.startswith("Error: prompt_reload REFUSED — reason=")
            assert _affirmative_compacted_claims(out) == [], out
            for phrase in ("compaction complete", "context compacted", "summary rendered"):
                assert phrase not in out.lower()

    async def test_the_refusals_are_refused_not_failed(self) -> None:
        manager = StubManager()
        out = await _call(manager, compact=True)
        assert "REFUSED" in out
        assert "FAILED" not in out

    async def test_the_already_armed_code_is_retired(self) -> None:
        assert "prompt_reload_compact_note_already_armed" not in pr.REFUSAL_CODES


# ── The note carrier (plan §9 Phase 2, tasks 2.1-2.6) ───────────────────────


class TestCarrier:
    """The arm carrier: ``instances.metadata`` JSONB — NO migration, NO
    DDL, NO schema change (architect §D / ES-3)."""

    async def test_the_arm_lives_in_the_instance_metadata_column(self) -> None:
        manager = StubManager()
        await _call(manager, compact=True, compact_note="a note")
        md = manager._instance_repository.metadata
        assert md["compact_note_arm"] == "a note"
        assert "compact_note_armed_at" in md
        assert "compact_note_sha256_12" in md

    async def test_the_generic_update_path_is_never_used(self) -> None:
        """The generic repository ``update`` REJECTS the
        ``instance_metadata`` kwarg by design — the arm goes through the
        atomic helpers only."""
        manager = StubManager()
        await _call(manager, compact=True, compact_note="a note")
        assert not hasattr(manager._instance_repository, "update")

    async def test_no_cross_instance_leakage(self) -> None:
        """The note is scoped to the instance that authored it."""
        repo = StubInstanceRepo()
        repo.rows["iid-2"] = {"agent_id": "ari", "instance_metadata": {}}
        manager = StubManager(repo=repo)
        await _call(manager, compact=True, compact_note="instance one only")
        other = repo.rows["iid-2"]["instance_metadata"]
        assert "compact_note_arm" not in other
        state = pr.read_compact_note_state(repo, "iid-2")
        assert state.armed is False
        assert state.note == ""

    async def test_the_consumption_clears_the_arm_and_stamps_the_clock(self) -> None:
        repo = StubInstanceRepo()
        manager = StubManager(repo=repo)
        await _call(manager, compact=True, compact_note="a note")
        sha = pr.compact_note_sha256_12("a note")
        assert pr.clear_compact_note_arm(repo, "iid-1", sha256_12=sha)
        md = repo.metadata
        assert "compact_note_arm" not in md
        assert md["compact_note_last_sha256_12"] == sha
        assert md["compact_note_last_applied_at"]
        assert md["compact_note_last_deferred"] == pr.DEFERRED_STATE_NONE

    async def test_the_deferred_marker_is_content_free(self) -> None:
        repo = StubInstanceRepo()
        assert pr.mark_compact_note_deferred(repo, "iid-1", pr.DEFERRED_STATE_NO_DOC)
        assert repo.metadata["compact_note_last_deferred"] == "no_doc"

    async def test_an_unknown_deferred_state_is_refused(self) -> None:
        repo = StubInstanceRepo()
        assert pr.mark_compact_note_deferred(repo, "iid-1", "not-a-state") is False
        assert "compact_note_last_deferred" not in repo.metadata

    async def test_the_arm_read_never_raises(self) -> None:
        class RaisingRepo:
            def get(self, instance_id):
                raise RuntimeError("db down")

        state = pr.read_compact_note_state(RaisingRepo(), "iid-1")
        assert state.armed is False

    async def test_a_missing_repository_yields_no_arm(self) -> None:
        state = pr.read_compact_note_state(None, "iid-1")
        assert state.armed is False


# ── L4 — seam safety (no mid-turn compaction) ──────────────────────────────

class TestL4SeamSafety:
    async def test_no_compact_state_call_during_the_tool_call(self) -> None:
        import daemon.compaction as compaction_mod

        calls: list[object] = []

        def _boom(*args, **kwargs):
            calls.append(args)
            raise AssertionError("compact_state must NOT be called by the tool")

        with patch.object(compaction_mod.ContextCompactor, "compact_state", _boom):
            manager = StubManager()
            out = await _call(manager, compact=True, compact_note="a note")
        assert calls == []
        assert f"outcome={OUTCOME_ARMED}" in out

    async def test_the_graph_seam_carries_the_append_and_swap(self) -> None:
        """v1.5 (Amendment 2): the graph DOES carry the append (tools
        node) and the swap (agent node). The auto-path mid-turn sites
        (precall 95% hook, CLE reactive handler) remain arm-blind."""
        import daemon.graph as graph_mod

        with open(graph_mod.__file__, encoding="utf-8") as fh:
            text = fh.read()
        assert "compaction_request" in text
        assert "build_compact_note_request" in text
        assert "make_remove_all_sentinel" in text


# ── L7 — content-free logging ───────────────────────────────────────────────


class TestL7ContentFreeLogging:
    async def test_the_note_body_appears_in_zero_log_lines(self, caplog) -> None:
        sentinel = "SENTINEL-CANARY-9f3a-note-body-must-never-be-logged"
        manager = StubManager()
        with caplog.at_level(logging.DEBUG):
            await _call(manager, compact=True, compact_note=f"prefix {sentinel} suffix")
        assert sentinel not in caplog.text

    async def test_the_info_line_carries_length_and_digest_only(self, caplog) -> None:
        import hashlib

        note = "a compact note body"
        manager = StubManager()
        with caplog.at_level(logging.INFO):
            await _call(manager, compact=True, compact_note=note)
        lines = [
            r.message
            for r in caplog.records
            if r.message.startswith(pr.COMPACT_NOTE_INFO_PREFIX)
        ]
        assert len(lines) == 1, lines
        line = lines[0]
        digest = hashlib.sha256(note.encode("utf-8")).hexdigest()[:12]
        assert f"note_chars={len(note)}" in line
        assert f"note_sha256={digest}" in line
        assert note not in line

    async def test_a_refusal_emits_exactly_one_content_free_info_line(self, caplog) -> None:
        manager = StubManager()
        with caplog.at_level(logging.INFO):
            await _call(manager, compact=True)
        lines = [
            r.message
            for r in caplog.records
            if r.message.startswith(pr.COMPACT_NOTE_INFO_PREFIX)
        ]
        assert len(lines) == 1, lines
        assert f"outcome=refused" in lines[0]
        assert f"code={CODE_NOTE_REQUIRED}" in lines[0]


# ── L12 — the ARMED receipt + status line + supersede disclosure ────────────


class TestL12ArmedReceipt:
    async def test_the_receipt_carries_the_frozen_outcome_token_and_fields(self) -> None:
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="a note")
        assert f"outcome={OUTCOME_ARMED}" in out
        assert "note_chars=6" in out
        assert f"cap={CAP}" in out
        assert re.search(r"note_sha256=[0-9a-f]{12}\b", out)
        assert "superseded_previous=false" in out

    async def test_the_receipt_states_the_explicit_not_yet_line(self) -> None:
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="a note")
        assert "CONTEXT NOT YET COMPACTED" in out
        assert "a REQUEST your model will execute on its next invoke" in out

    async def test_the_receipt_never_affirmatively_claims_a_compaction(self) -> None:
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="a note")
        assert _affirmative_compacted_claims(out) == [], out
        for phrase in ("compaction complete", "context compacted", "summary rendered"):
            assert phrase not in out.lower()

    async def test_the_receipt_never_carries_the_note_body(self) -> None:
        manager = StubManager()
        note = "THE-NOTE-BODY-MUST-NOT-APPEAR"
        out = await _call(manager, compact=True, compact_note=note)
        assert note not in out

    async def test_the_arm_is_written_to_the_carrier(self) -> None:
        manager = StubManager()
        note = "a note"
        await _call(manager, compact=True, compact_note=note)
        md = manager._instance_repository.metadata
        assert md["compact_note_arm"] == note
        assert md["compact_note_sha256_12"] == pr.compact_note_sha256_12(note)
        assert md["compact_note_armed_at"]

    async def test_a_second_note_replaces_with_disclosure(self) -> None:
        manager = StubManager()
        first = "first note"
        await _call(manager, compact=True, compact_note=first)
        out = await _call(manager, compact=True, compact_note="second note")
        assert "superseded_previous=true" in out
        assert f"previous_note_sha256={pr.compact_note_sha256_12(first)}" in out
        # REPLACED — not refused, and the prior note's TEXT is not disclosed.
        assert "REFUSED" not in out
        assert first not in out
        assert manager._instance_repository.metadata["compact_note_arm"] == "second note"

    async def test_the_still_armed_status_line_rides_the_next_receipt(self) -> None:
        manager = StubManager()
        await _call(manager, compact=True, compact_note="first note")
        out = await _call(manager, compact=True, compact_note="second note")
        assert ARMED_PENDING_APPEND in out

    async def test_the_still_armed_status_line_carries_the_deferred_state(self) -> None:
        manager = StubManager()
        await _call(manager, compact=True, compact_note="first note")
        manager._instance_repository.metadata["compact_note_last_deferred"] = (
            pr.DEFERRED_STATE_NO_DOC
        )
        out = await _call(manager, compact=True, compact_note="second note")
        assert ARMED_STILL_ARMED_NO_DOC in out
        assert ARMED_PENDING_APPEND not in out

    async def test_the_plain_receipt_carries_no_status_line(self) -> None:
        """The still-armed line is ADDITIVE IN THE ARMED STATE only — the
        plain receipt is byte-identical (AC13.5)."""
        manager = StubManager()
        await _call(manager, compact=True, compact_note="first note")
        out = await _tool(manager).ainvoke({"force": False})
        assert out == GOLDEN_PLAIN_RECEIPT
        assert ARMED_PENDING_APPEND not in out

    async def test_the_one_deferred_wording_is_the_canonical_literal(self) -> None:
        """v1.5 vocabulary sweep: the anti-refire / below-floor deferred
        literals were UNPRODUCIBLE (their gates are removed) — they are
        deleted, and ONE deferred vocabulary remains."""
        assert ARMED_STILL_ARMED_NO_DOC == (
            "armed — still armed (the last append produced no doc), not compacted"
        )
        assert pr.DEFERRED_STATE_TEXT == {
            pr.DEFERRED_STATE_NO_DOC: ARMED_STILL_ARMED_NO_DOC,
            pr.DEFERRED_STATE_EMPTY_REPLY: pr.ARMED_DEFERRED_EMPTY_REPLY,
            pr.DEFERRED_STATE_TOOL_CALLS_REPLY: pr.ARMED_DEFERRED_TOOL_CALLS_REPLY,
            pr.DEFERRED_STATE_REFUSED_REPLY: pr.ARMED_DEFERRED_REFUSED_REPLY,
            pr.DEFERRED_STATE_APPEND_FAILED: pr.ARMED_DEFERRED_APPEND_FAILED,
            pr.DEFERRED_STATE_READ_FAILED: pr.ARMED_DEFERRED_READ_FAILED,
            pr.DEFERRED_STATE_CHANNEL_REBUILD_FAILED: (
                pr.ARMED_DEFERRED_CHANNEL_REBUILD_FAILED
            ),
        }
        assert not hasattr(pr, "ARMED_DEFERRED_ANTI_REFIRE")
        assert not hasattr(pr, "ARMED_DEFERRED_BELOW_FLOOR")
        assert not hasattr(pr, "ARMED_EXPECTED_SEAM_CLAUSE")
        assert not hasattr(pr, "ARMED_SEAM_STATEMENT")
        assert not hasattr(pr, "ARMED_NOT_YET_COMPACTED_LINE")

    async def test_the_atomic_multi_key_write_path_is_used_when_available(self) -> None:
        repo = AtomicStubInstanceRepo()
        manager = StubManager(repo=repo)
        await _call(manager, compact=True, compact_note="a note")
        assert repo.metadata["compact_note_arm"] == "a note"


# ── L17 — the v1.4 EXHAUSTIVENESS pin ──────────────────────────────────────


class TestL17Exhaustiveness:
    async def test_the_receipt_carries_the_append_path_fields(self) -> None:
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="a note")
        assert "path=append" in out
        assert "selection=the model made the final selection" in out
        assert "outcome=armed" in out

    async def test_the_receipt_carries_no_three_gate_claim(self) -> None:
        """Under v1.5 the note path is ONE path (append); there is NO
        seam transition and NO 'three gates' (the floor predicate + the
        threshold bypass are removed)."""
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="a note")
        assert "three gates" not in out
        assert "armed_pending_seam" not in out
        assert "unconditionally" not in out

    async def test_the_gate_list_is_not_extended_by_the_receipt(self) -> None:
        """The word 'unconditionally' is GONE from the agent-facing literal."""
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="a note")
        assert "unconditionally" not in out


# ── Phase C — the READ-AFTER-WRITE pin (⭐ the highest-value pin) ────────────


class TestReadAfterWrite:
    """The tools-node wrapper reads ``read_compact_note_state`` IMMEDIATELY
    after the tool writes it, inside the SAME node. Whether the repository
    returns the FRESH row or a STALE cache is UNVERIFIED and, if stale,
    the append reads an unarmed state and silently does nothing (the
    receipt would say so, but nothing would work). This pin settles it.
    """

    def test_the_arm_is_readable_immediately_after_write(self) -> None:
        repo = StubInstanceRepo()
        note = "THE-NOTE-READ-AFTER-WRITE"
        pr.arm_compact_note(repo, "iid-1", note)
        # Read within the same node (the same repository instance).
        state = pr.read_compact_note_state(repo, "iid-1")
        assert state.armed is True
        assert state.note == note
        assert state.sha256_12 == pr.compact_note_sha256_12(note)

    def test_the_pin_reddens_if_the_read_is_served_stale(self) -> None:
        """Mutation proof — a stale-cache repository makes the pin FAIL.

        This is the mutation leg: if the repository serves a stale row,
        the read returns an unarmed state and the pin reddens. The pin
        has teeth: a stale cache would make the append path silently
        inert, and this test would go RED.
        """

        class StaleRepo(StubInstanceRepo):
            def get(self, instance_id):
                # A stale cache: the write landed but the read returns
                # the OLD row (no arm).
                return SimpleNamespace(agent_id="ari", instance_metadata={})

        repo = StaleRepo()
        note = "THE-NOTE"
        # The arm write lands (the repo's set_metadata works).
        pr.arm_compact_note(repo, "iid-1", note)
        # But the read is served stale.
        state = pr.read_compact_note_state(repo, "iid-1")
        assert not (state.armed and state.note == note)

    def test_the_read_after_write_holds_for_the_sha_prefix_too(self) -> None:
        repo = StubInstanceRepo()
        note = "another note body"
        pr.arm_compact_note(repo, "iid-1", note)
        state = pr.read_compact_note_state(repo, "iid-1")
        assert state.sha256_12 == pr.compact_note_sha256_12(note)
        # The write's sha prefix is the same one the receipt carries.
        assert len(state.sha256_12) == 12


# ── H6 — the arm write's TRUTH (a None write means NOTHING was written) ────


class NoneWriteRepo(StubInstanceRepo):
    """A carrier whose write helper reports "the instance does not
    exist" — ``None`` — the house contract's failure signal."""

    def set_metadata(self, instance_id: str, key: str, value):
        self.set_metadata_calls.append((instance_id, key, value))
        return None


class TestH6ArmWriteTruth:
    async def test_a_none_write_never_renders_the_armed_receipt(self) -> None:
        """The write helpers return ``Instance | None``; ``None`` means
        NOTHING was written. The tool must take the FAILED branch — never
        render the ARMED receipt over an empty carrier."""
        manager = StubManager(repo=NoneWriteRepo())
        out = await _call(manager, compact=True, compact_note="a note")
        assert CODE_INTERNAL_ERROR in out
        assert "outcome=armed" not in out
        assert "ARMED" not in out

    async def test_a_none_write_leaves_the_carrier_unarmed(self) -> None:
        repo = NoneWriteRepo()
        manager = StubManager(repo=repo)
        await _call(manager, compact=True, compact_note="a note")
        state = pr.read_compact_note_state(repo, "iid-1")
        assert state.armed is False
        assert state.note == ""


# ── N4 — the arm-write VARIANT MATRIX (three variants, three outcomes) ────


class AppliesButReportsNoneRepo(StubInstanceRepo):
    """A carrier that APPLIES the key but reports ``None`` (a partial
    write) — and has NO atomic multi-key helper, so the per-key fallback
    runs. The write state is therefore UNVERIFIED by the return value."""

    def set_metadata(self, instance_id: str, key: str, value):
        self.set_metadata_calls.append((instance_id, key, value))
        self._meta(instance_id)[key] = value
        return None  # the write landed, the RETURN does not say so


class TestN4ArmWriteVariantMatrix:
    async def test_full_write_arms_cleanly(self) -> None:
        """FULL WRITE ⇒ the ARMED receipt, the arm present."""
        repo = StubInstanceRepo()
        manager = StubManager(repo=repo)
        out = await _call(manager, compact=True, compact_note="a note")
        assert f"outcome={OUTCOME_ARMED}" in out
        assert "PARTIAL WRITE" not in out
        assert pr.read_compact_note_state(repo, "iid-1").armed is True

    async def test_a_none_write_is_the_failed_envelope(self) -> None:
        """NONE-WRITE (the write did not land) ⇒ the FAILED envelope and
        the honest 'nothing was armed'."""
        repo = NoneWriteRepo()
        manager = StubManager(repo=repo)
        out = await _call(manager, compact=True, compact_note="a note")
        assert CODE_INTERNAL_ERROR in out
        assert "nothing was armed" in out
        assert "outcome=armed" not in out
        assert pr.read_compact_note_state(repo, "iid-1").armed is False

    async def test_a_partial_write_reports_the_partial_write_outcome(self) -> None:
        """PARTIAL WRITE (the carrier HOLDS the arm although the write
        reported failure) ⇒ a DISTINCT outcome — NEVER 'nothing was
        armed' over a carrier that holds the arm."""
        repo = AppliesButReportsNoneRepo()
        manager = StubManager(repo=repo)
        out = await _call(manager, compact=True, compact_note="a note")
        assert "nothing was armed" not in out
        assert f"outcome={OUTCOME_ARMED_PARTIAL_WRITE}" in out
        assert "PARTIAL WRITE" in out
        # The arm IS there — the receipt says so.
        assert pr.read_compact_note_state(repo, "iid-1").armed is True


# ── 🔴 5 — the five refusal MESSAGES are pinned (the agent's only
# remediation guidance — the codes were pinned, the WORDS were not) ────────


class TestRefusalMessageTemplates:
    def test_the_five_refusal_message_templates_are_pinned(self) -> None:
        """The TEMPLATE constants are pinned verbatim (the rendered
        dynamic strings carry live values and are not pinned here)."""
        from daemon.tools.prompt_reload import (
            _COMPACT_NOTE_MSG_COMPACT_DISABLED,
            _COMPACT_NOTE_MSG_COMPACT_RATE_LIMITED,
            _COMPACT_NOTE_MSG_NOTE_OVER_CAP,
            _COMPACT_NOTE_MSG_NOTE_REQUIRED,
            _COMPACT_NOTE_MSG_NOTE_REQUIRES_COMPACT,
        )

        assert _COMPACT_NOTE_MSG_NOTE_REQUIRES_COMPACT == (
            "a compact_note was supplied with compact=false — the note is only "
            "meaningful together with compact=true; re-issue the call with "
            "compact=true. Nothing was armed."
        )
        assert _COMPACT_NOTE_MSG_NOTE_REQUIRED == (
            "a compact note is REQUIRED when compact=true — the note is the "
            "point, not an option; supply compact_note with non-empty text. "
            "Nothing was armed."
        )
        assert _COMPACT_NOTE_MSG_NOTE_OVER_CAP == (
            "the compact note is {note_chars} characters, over the "
            "{cap}-character cap — the note is NEVER truncated; shorten it and "
            "re-arm. Nothing was armed."
        )
        assert _COMPACT_NOTE_MSG_COMPACT_DISABLED == (
            "compaction is DISABLED in this deployment — arming a note that can "
            "never land would strand it; enable compaction (CompactionConfig.enabled) "
            "and re-arm. Nothing was armed."
        )
        assert _COMPACT_NOTE_MSG_COMPACT_RATE_LIMITED == (
            "the deliberate-compact rate limit is in force — {remaining}s remaining "
            "before another note-guided compaction may be armed (the limit is "
            "{limit}s). Nothing was armed; try again after the window."
        )
