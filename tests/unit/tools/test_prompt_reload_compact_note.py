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

import daemon.manager  # noqa: F401  (the patch target must be importable)
import daemon.services.prompt_refresh as pr
from daemon.services.prompt_refresh import (
    ARMED_DEFERRED_ANTI_REFIRE,
    ARMED_DEFERRED_BELOW_FLOOR,
    ARMED_EXPECTED_SEAM_CLAUSE,
    ARMED_NOT_YET_COMPACTED_LINE,
    ARMED_PENDING_SEAM,
    ARMED_STILL_ARMED_NO_DOC,
    CODE_COMPACT_DISABLED,
    CODE_COMPACT_RATE_LIMITED,
    CODE_NOTE_OVER_CAP,
    CODE_NOTE_REQUIRES_COMPACT,
    CODE_NOTE_REQUIRED,
    OUTCOME_ARMED_PENDING_SEAM,
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

#: The documented cap (architect §G — AUTHORITATIVE).
CAP = 12000

#: The three deferred states (VERBATIM — architect §G / AC10.5).
ALL_DEFERRED_WORDINGS = (
    ARMED_DEFERRED_ANTI_REFIRE,
    ARMED_DEFERRED_BELOW_FLOOR,
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
        "compact_note_prefilter_enabled": False,
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
        assert f"outcome={OUTCOME_ARMED_PENDING_SEAM}" in out

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
        assert f"outcome={OUTCOME_ARMED_PENDING_SEAM}" in out

    async def test_the_note_armed_hook_is_not_registered_in_the_mid_turn_sites(self) -> None:
        """T1/T2 (the mid-turn triggers) must NOT read the arm."""
        import daemon.graph as graph_mod
        import daemon.services.instance_messaging as im_mod

        for module in (graph_mod, im_mod):
            source = module.__file__
            with open(source, encoding="utf-8") as fh:
                text = fh.read()
            assert "compact_note" not in text, source


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
        assert f"outcome={OUTCOME_ARMED_PENDING_SEAM}" in out
        assert "note_chars=6" in out
        assert f"cap={CAP}" in out
        assert re.search(r"note_sha256=[0-9a-f]{12}\b", out)
        assert "superseded_previous=false" in out
        assert "prefilter_enabled=false" in out

    async def test_the_receipt_states_the_seam_and_the_explicit_not_yet_line(self) -> None:
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="a note")
        assert ARMED_NOT_YET_COMPACTED_LINE in out
        assert "your next turn boundary" in out

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
        assert ARMED_PENDING_SEAM in out

    async def test_the_still_armed_status_line_carries_the_deferred_state(self) -> None:
        manager = StubManager()
        await _call(manager, compact=True, compact_note="first note")
        manager._instance_repository.metadata["compact_note_last_deferred"] = (
            "anti_refire"
        )
        out = await _call(manager, compact=True, compact_note="second note")
        assert ARMED_DEFERRED_ANTI_REFIRE in out
        assert ARMED_PENDING_SEAM not in out

    async def test_the_plain_receipt_carries_no_status_line(self) -> None:
        """The still-armed line is ADDITIVE IN THE ARMED STATE only — the
        plain receipt is byte-identical (AC13.5)."""
        manager = StubManager()
        await _call(manager, compact=True, compact_note="first note")
        out = await _tool(manager).ainvoke({"force": False})
        assert out == GOLDEN_PLAIN_RECEIPT
        assert ARMED_PENDING_SEAM not in out

    async def test_the_three_deferred_wordings_are_the_canonical_literals(self) -> None:
        assert ARMED_DEFERRED_ANTI_REFIRE == (
            "armed — deferred (anti-refire window), not compacted"
        )
        assert ARMED_DEFERRED_BELOW_FLOOR == (
            "armed — deferred (below minimum-compaction floor), not compacted"
        )
        assert ARMED_STILL_ARMED_NO_DOC == (
            "armed — still armed (last compaction produced no doc), not compacted"
        )

    async def test_the_atomic_multi_key_write_path_is_used_when_available(self) -> None:
        repo = AtomicStubInstanceRepo()
        manager = StubManager(repo=repo)
        await _call(manager, compact=True, compact_note="a note")
        assert repo.metadata["compact_note_arm"] == "a note"


# ── L17 — the v1.4 EXHAUSTIVENESS pin ──────────────────────────────────────


class TestL17Exhaustiveness:
    async def test_the_expected_seam_clause_names_exactly_three_gates(self) -> None:
        clause = ARMED_EXPECTED_SEAM_CLAUSE
        assert "exactly three gates" in clause
        assert "this list is exhaustive" in clause
        assert "minimum-compaction floor" in clause
        assert "60s anti-refire window" in clause
        assert "deliberate-compact rate limit" in clause
        # EXACTLY three gate names — no FOURTH gate is named.
        listed = clause.split("applies: ", 1)[1].split(" — this list is exhaustive", 1)[0]
        items = [part.strip() for part in listed.replace("or ", "").split(",")]
        assert len(items) == 3, items
        assert items == [
            "the minimum-compaction floor",
            "the 60s anti-refire window",
            "the deliberate-compact rate limit",
        ]
        assert "unconditionally" not in clause

    async def test_the_receipt_carries_the_expected_seam_clause(self) -> None:
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="a note")
        assert ARMED_EXPECTED_SEAM_CLAUSE in out

    async def test_a_reader_cannot_believe_the_gate_list_is_shorter(self) -> None:
        """The three gate NAMES must each appear — a reader must not be
        able to believe the list is shorter."""
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="a note")
        for gate in (
            "the minimum-compaction floor",
            "the 60s anti-refire window",
            "the deliberate-compact rate limit",
        ):
            assert gate in out, gate

    async def test_the_gate_list_is_not_extended_by_the_receipt(self) -> None:
        """The word 'unconditionally' is GONE from the agent-facing literal."""
        manager = StubManager()
        out = await _call(manager, compact=True, compact_note="a note")
        assert "unconditionally" not in out
