"""Prompt-migration pins for the mission-watch toolset reshape.

Toolset reshape (2026-09-19, ``feature/mission-watch-toolset``; design
§8/§9.9): the 9-file prompt migration MUST land in the same change as the
``watch_mission`` tool. These grep-style pins hold the migration in place:

* **No ``watch_job``/``watch_jobs`` tokens** in any migrated file
  (word-boundary match — ``unwatch_job``/``list_watched_jobs`` are KEPT
  tools and must not trip the pin).
* **Decision-rule text** ("wait on MISSIONS, not receipts" family) present
  in the ari/jober soul/rule/tools_note/workflow files and skill.md.
* **``watch_mission`` vocabulary** present in all 9 files.
* **skill.md parser block byte-identical** (design §6: the
  ``[JOB_EVENT]`` envelope contract is emitted by the untouched engine —
  the parser prose is pinned verbatim; only the EVENT SET changed).
* **Handle-tolerance notes** for ``unwatch_job`` (receipt OR mission_id).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

JOBER_FILES = [
    "agents/jober/tools_note.md",
    "agents/jober/rule.md",
    "agents/jober/workflow.md",
    "agents/jober/soul.md",
]
ARI_FILES = [
    "agents/ari/workflow.md",
    "agents/ari/rule.md",
    "agents/ari/soul.md",
    "agents/ari/tools_note.md",
]
SKILL_MD = "agents/_prompt_system/innate-skills/job-orchestration/skill.md"
ALL_NINE = JOBER_FILES + ARI_FILES + [SKILL_MD]

# Word-boundary token match: matches `watch_job` / `watch_jobs` but NOT
# `unwatch_job` (the preceding char class excludes word chars and `_`).
_WATCH_JOB_TOKEN = re.compile(r"(^|[^A-Za-z0-9_])watch_jobs?\b")

# The [JOB_EVENT] parser contract block in skill.md (design §6: the
# envelope is emitted by the UNTOUCHED engine, so this block stays
# byte-identical; only the surrounding prose/example set was re-scoped).
# Fixture pin — ANY edit inside this block must fail this test.
SKILL_MD_PARSER_BLOCK = """## Notification Format

When watching a job, notifications arrive as plain text with this structure:

**Completed job:**
```
[JOB_EVENT] Job b5536c60... completed ✓
  Agent: leader
  Result: (result text, may be multi-line)
```

**Failed job:**
```
[JOB_EVENT] Job b5536c60... failed ✗
  Agent: leader
  Error: (error text)
```

**Header:** `[JOB_EVENT] Job {job_id}... {status}` — the status word appears directly with a visual indicator (`completed ✓` or `failed ✗`). There is no "reached status" prefix.

**Source:** `internal_agent:job_event:{job_id}:{status}`
- Classified as `MessageType.AGENT`
- This distinguishes it from user messages

**Body:** Plain text lines:
- `Agent:` line is always present
- `Result:` line is present on completion (may be multi-line)
- `Error:` line is present only on failure (absent — not "Error: None" — when there is no error)
- There is no JSON block at the end of the message

---
"""


def _read(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text()


class TestNoWatchJobTokens:
    def test_all_nine_files_carry_zero_watch_job_tokens(self):
        for relpath in ALL_NINE:
            content = _read(relpath)
            hits = _WATCH_JOB_TOKEN.findall(content)
            assert not hits, (
                f"{relpath} still carries watch_job/watch_jobs token(s): "
                f"{_WATCH_JOB_TOKEN.search(content)!r} — the migration "
                f"replaces them with watch_mission"
            )

    def test_kept_tools_survive_in_jober_tools_note(self):
        """unwatch_job / list_watched_jobs are KEPT (resolver-extended) —
        the token pin must not have driven them out."""
        content = _read("agents/jober/tools_note.md")
        assert "### unwatch_job" in content
        assert "### list_watched_jobs" in content


class TestDecisionRulePresent:
    def test_missions_not_receipts_rule_in_soul_rule_skill_files(self):
        """The decision-rule text (design §8) lands in ari & jober
        soul/rule/tools_note/workflow + skill.md, adapted per file voice."""
        expectations = {
            SKILL_MD: "You wait on MISSIONS, not receipts",
            "agents/jober/tools_note.md": "You wait on MISSIONS, not receipts",
            "agents/jober/rule.md": "I wait on MISSIONS, not receipts",
            "agents/jober/soul.md": "I wait on MISSIONS, not receipts",
            "agents/ari/rule.md": "wait on MISSIONS, not receipts",
            "agents/ari/soul.md": "Waiting on the work",
            "agents/ari/tools_note.md": "wait on MISSIONS, not receipts",
            "agents/ari/workflow.md": "wait on MISSIONS, not receipts",
        }
        for relpath, needle in expectations.items():
            content = re.sub(r"\s+", " ", _read(relpath))
            assert needle in content, f"{relpath} missing decision-rule text: {needle!r}"

    # Spec-anchored WINDOW pin (tidier #8): the SPEC sentence is
    # "The FIRST [JOB_EVENT] after your watch is the signal; later
    # events on the same mission's other receipts are echoes — act
    # once." Match the FIRST→signal→echo chain inside a bounded window
    # (whitespace-collapsed) and require "act once" in the tail — a bare
    # "FIRST" or "echo" elsewhere must NOT satisfy the pin.
    _ECHO_CHAIN = re.compile(
        r"FIRST.{0,80}?signal.{0,140}?receipts.{0,60}?echo", re.DOTALL
    )
    _ECHO_SITES = (
        SKILL_MD,
        "agents/jober/tools_note.md",
        "agents/jober/rule.md",
        "agents/jober/workflow.md",
        "agents/jober/soul.md",
        "agents/ari/rule.md",
        "agents/ari/tools_note.md",
        "agents/ari/soul.md",
        "agents/ari/workflow.md",
    )

    def test_first_event_is_the_signal_rule_present(self):
        """The echo rule (act once) — multi-receipt fan-in produces N
        events for ONE mission terminal (design §3). Anchored to the SPEC
        phrasing in a char-budgeted window; every duplicate prose site is
        enumerated (#8 + #19)."""
        for relpath in self._ECHO_SITES:
            flat = re.sub(r"\s+", " ", _read(relpath))
            m = self._ECHO_CHAIN.search(flat)
            assert m, (
                f"{relpath}: no FIRST→signal→echo chain within budget — "
                f"the echo rule drifted from the SPEC sentence"
            )
            tail = flat[m.end():m.end() + 120]
            assert "act once" in tail.lower(), (
                f"{relpath}: echo rule missing the 'act once' disposition"
            )

    def test_echo_pin_rejects_trivially_satisfiable_text(self):
        """Survivor (negation) pin: 'FIRST'/'echo' occurrences OUTSIDE the
        spec-shaped chain must NOT satisfy the pin (tidier key pattern)."""
        trivial = (
            "FIRST you create a job. The signal strength is fine. "
            "An echo chamber is unrelated. act once."
        )
        assert not self._ECHO_CHAIN.search(re.sub(r"\s+", " ", trivial)), (
            "echo pin matched non-spec text — the window is too loose"
        )

    # Spec-anchored WINDOW pin (tidier #20): the re-watch rule is ABOUT
    # job_continue — the window must contain the job_continue subject,
    # the watch_mission verb, AND the non-auto-watched disposition.
    # The old `|not auto-watched` alternation was trivially satisfiable.
    _REWATCH_CHAIN = re.compile(
        r"job_continue.{0,240}?watch_mission.{0,160}?(not\s+auto-?watched|\bagain\b)",
        re.IGNORECASE | re.DOTALL,
    )
    _REWATCH_SITES = (
        SKILL_MD,
        "agents/jober/tools_note.md",
        "agents/jober/rule.md",
        "agents/jober/workflow.md",
        "agents/ari/rule.md",
    )

    def test_rewatch_after_job_continue_rule_present(self):
        for relpath in self._REWATCH_SITES:
            flat = re.sub(r"\s+", " ", _read(relpath))
            assert self._REWATCH_CHAIN.search(flat), (
                f"{relpath}: no job_continue→watch_mission→(not "
                f"auto-watched|again) chain — the re-watch rule drifted"
            )

    def test_rewatch_pin_rejects_out_of_context_text(self):
        """Survivor (negation) pin: 'not auto-watched' WITHOUT the
        job_continue subject must NOT satisfy the pin."""
        out_of_context = (
            "Spawned receipts are not auto-watched by anything. "
            "Nothing here mentions the continuation verb."
        )
        assert not self._REWATCH_CHAIN.search(re.sub(r"\s+", " ", out_of_context)), (
            "re-watch pin matched out-of-context text — the anchor is loose"
        )

    def test_revived_mission_needs_fresh_watch_rule_in_skill_md(self):
        content = _read(SKILL_MD)
        assert "revived mission needs a fresh `watch_mission`" in content
        assert "no epoch" in content

    def test_await_mission_timeout_snapshot_rule_present(self):
        """``await_mission`` timeout returns a SNAPSHOT, not an error —
        prompt note, not code (design §1)."""
        for relpath in (SKILL_MD, "agents/ari/rule.md", "agents/ari/tools_note.md"):
            content = _read(relpath)
            assert "SNAPSHOT, not an error" in content, relpath

    def test_watch_mission_vocab_in_all_nine(self):
        for relpath in ALL_NINE:
            content = _read(relpath)
            assert "watch_mission" in content, f"{relpath} missing watch_mission"


class TestHandleToleranceNotes:
    def test_unwatch_job_accepts_mission_id(self):
        for relpath in (
            "agents/jober/tools_note.md",
            "agents/jober/rule.md",
            "agents/ari/rule.md",
        ):
            content = _read(relpath)
            assert re.search(r"unwatch_job.{0,200}(mission_id|receipt job_id)", content, re.DOTALL), (
                f"{relpath} missing the unwatch_job handle-tolerance note"
            )

    def test_watch_mission_accepts_receipt_handle(self):
        content = _read(SKILL_MD)
        assert "accepts the receipt handle OR the mission_id" in content


class TestParserBlockByteIdentity:
    def test_skill_md_parser_block_byte_identical(self):
        content = _read(SKILL_MD)
        assert SKILL_MD_PARSER_BLOCK in content, (
            "skill.md [JOB_EVENT] parser block changed — the envelope is "
            "emitted by the UNTOUCHED engine (work_notifier.py format "
            "contract); this block must stay byte-identical"
        )


class TestMetaPins:
    def test_ari_jober_deny_watch_tools(self):
        for agent in ("ari", "jober"):
            meta = json.loads((REPO_ROOT / "agents" / agent / "meta.json").read_text())
            deny = meta["tools"].get("deny", [])
            assert "watch_job" in deny and "watch_jobs" in deny, agent

    def test_versions_bumped(self):
        for agent in ("ari", "jober"):
            meta = json.loads((REPO_ROOT / "agents" / agent / "meta.json").read_text())
            assert meta["version"] == "1.2.0", agent
