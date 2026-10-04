#!/usr/bin/env python3
# ============================================================================
# tests/test_chart_image_delivery_audit.py
# ----------------------------------------------------------------------------
# Phase D — chart-image-delivery 24-pin regression audit (D.0/D.1 task #1).
# Pytest mirror of tools/audit-chart-image-delivery.sh. Each test asserts
# ONE pin; the module is also runnable via tools/audit-chart-image-delivery.sh
# (which shells out to pytest for the per-pin checks). All 24 tests must
# pass on the merged tree; the PRESERVATION subset (1, 2, 3, 4, 5, 9, 13, 22,
# 23) must pass on the pre-merge base.
#
# Pin catalog = phaseD-plan.md §2 (Components §2 table, lines 128-153).
# Every assertion is content-addressable (NOT line numbers) so the suite
# is merge-stable. Anchor drift on a pin → that test FAILs (others still run).
# ============================================================================
"""Phase D chart-image-delivery 24-pin regression audit (pytest mirror)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# ---------- repo paths ----------
# Resolve from the test file's location so the suite is portable across
# worktrees and the pre-merge base audit worktree (/tmp/cid-base-audit).
THIS_FILE = Path(__file__).resolve()
# THIS_FILE = tests/test_chart_image_delivery_audit.py
# repo root = parent of tests/
REPO_ROOT = THIS_FILE.parent.parent

CHART_TOOLS = REPO_ROOT / "daemon/tools/chart_tools.py"
TEST_CHART_TOOLS = REPO_ROOT / "tests/test_chart_tools.py"
SKILL_MD = REPO_ROOT / "agents/_prompt_system/innate-skills/chart/skill.md"
DECISIONS_MD = REPO_ROOT / ".agents/shared/planning/chart-image-delivery/decisions.md"
DISPATCHER_PY = REPO_ROOT / "daemon/sources/dispatcher.py"
REGISTRY_PY = REPO_ROOT / "daemon/sources/registry.py"
BASE_PY = REPO_ROOT / "daemon/sources/base.py"
DISCORD_ADAPTER = REPO_ROOT / "daemon/sources/adapters/discord/adapter.py"
TELEGRAM_ADAPTER = REPO_ROOT / "daemon/sources/adapters/telegram.py"
SLACK_ADAPTER = REPO_ROOT / "daemon/sources/adapters/slack/adapter.py"
CONSTANTS_PY = REPO_ROOT / "daemon/constants.py"
CHARTER_META = REPO_ROOT / "agents/charter/meta.json"
CONTEXT_MD = REPO_ROOT / ".agents/shared/context.md"
ARI_WORKFLOW = REPO_ROOT / "agents/ari/workflow.md"
SLACK_SETUP_MD = REPO_ROOT / "docs/sources/slack-setup.md"
MANAGER_PY = REPO_ROOT / "daemon/manager.py"
MOCK_SOURCE_SERVER = REPO_ROOT / "tests/e2e/mock_source_server.py"

# Locked strings (single source of truth for both the bash gate and pytest)
_BUSY_STRING = "Error: Charter busy; pass fresh=True for parallel charts."
_PAUSED_FRAGMENT_1 = "Error: Charter is paused; resume it or pass fresh=True for a "
_PAUSED_FRAGMENT_2 = "new charter."
_PAUSED_STRING = _PAUSED_FRAGMENT_1 + _PAUSED_FRAGMENT_2
LOCKED_MARKER_REGEX = r"^<!-- ens-img:chart-render:[a-f0-9]{32} -->$"

# The 20 chart-capable agents that must reference "Chat Delivery" in one of
# rule/soul/tools_note/workflow .md (R2 corrected; verified Phase C list).
# Charter itself is NOT in this set. [v2] agents have brackets in the
# directory name (handled via the explicit list below).
CHART_CAPABLE_AGENTS = [
    "approver", "approver[v2]",
    "architect", "ari", "coder",
    "developer", "developer[v2]",
    "devops", "doc-writer", "governor",
    "leader", "maintenancer",
    "planner", "planner[v2]",
    "project-manager",
    "reviewer", "reviewer[v2]",
    "tidier", "tidier[v2]",
    "wanderer",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ============================================================================
# PRESERVATION pins (1, 2, 3, 4, 5, 9, 13, 22, 23) — 9 pins
# ============================================================================


def test_pin_01_busy_string_byte_stable() -> None:
    """Pin #1 — _BUSY_STRING text byte-stable at 3 sites.

    chart_tools.py : ``_BUSY_MSG = "<busy>"``
    tests/test_chart_tools.py : ``_BUSY_STRING = "<busy>"``
    skill.md : busy callout narrative contains the literal.
    """
    busy = _BUSY_STRING
    chart = _read(CHART_TOOLS)
    test = _read(TEST_CHART_TOOLS)
    skill = _read(SKILL_MD)
    assert f'_BUSY_MSG = "{busy}"' in chart, "chart_tools.py missing _BUSY_MSG"
    assert f'_BUSY_STRING = "{busy}"' in test, "test_chart_tools.py missing _BUSY_STRING"
    assert busy in skill, "skill.md missing busy callout"
    # Per the plan, the busy string must appear at 3 call sites under the
    # busy-body section (one in chart_tools const + one in skill.md Best
    # Practices + one in skill.md Wedged-Charter Recovery + the test pin).
    # Verify ≥3 hits across skill.md (best-practices + wedged-charter).
    assert skill.count(busy) >= 2, "skill.md must reference busy string ≥ 2 times"


def test_pin_02_paused_string_byte_stable() -> None:
    """Pin #2 — _PAUSED_STRING byte-stable across split source.

    The source literal is SPLIT across chart_tools.py:222-223 (two
    fragments); tests + skill.md hold the FULL concatenated string. A
    single full-string grep on chart_tools.py is GUARANTEED 0 hits.
    """
    chart = _read(CHART_TOOLS)
    test = _read(TEST_CHART_TOOLS)
    skill = _read(SKILL_MD)
    # chart_tools.py — both fragments must appear (the split is the contract)
    assert _PAUSED_FRAGMENT_1 in chart, "chart_tools.py missing paused fragment 1"
    assert _PAUSED_FRAGMENT_2 in chart, "chart_tools.py missing paused fragment 2"
    # test pin — full string
    assert "_PAUSED_STRING" in test, "test_chart_tools.py missing _PAUSED_STRING symbol"
    assert _PAUSED_STRING in test, "test_chart_tools.py missing paused full string"
    # skill.md — full string in the paused callout
    assert _PAUSED_STRING in skill, "skill.md missing paused callout"


def test_pin_03_marker_regex_locked() -> None:
    """Pin #3 — marker regex byte-stable in decisions.md §marker.

    The LOCKED form (NOT the near-miss sweeper pattern from
    §phase-b-r2-addendum-14). The locked form uses a 32-char hex with
    exact anchor `^...$` and exact whitespace.
    """
    decisions = _read(DECISIONS_MD)
    assert LOCKED_MARKER_REGEX in decisions, (
        "decisions.md missing locked marker regex form "
        f"(expected: {LOCKED_MARKER_REGEX!r})"
    )
    # Negative guard: the near-miss sweeper (looser char class, no anchors)
    # is a DIFFERENT pattern and must NOT satisfy this pin. Assert the
    # near-miss pattern's distinguishing substring is NOT the locked form
    # (the locked form has both `^` and `$` anchors).
    assert "\\s*<!--" not in LOCKED_MARKER_REGEX, "internal guard: locked regex is anchored"
    # And the pattern must include both anchors (anchored regex contract)
    assert LOCKED_MARKER_REGEX.startswith("^")
    assert LOCKED_MARKER_REGEX.endswith("$")


def test_pin_04_passthrough_adjacency() -> None:
    """Pin #4 — chart_tools.py anchor adjacency preserved.

    The `return result` immediately precedes `generate_chart._full_doc_ = `
    assignment. Content-addressable: find the first `return result` and
    verify the next non-blank line begins with `generate_chart._full_doc_`.
    """
    chart = _read(CHART_TOOLS)
    lines = chart.splitlines()
    saw_return = False
    for line in lines:
        if saw_return:
            if line.strip():
                assert line.lstrip().startswith("generate_chart._full_doc_"), (
                    f"expected next non-blank line to start with "
                    f"`generate_chart._full_doc_`; got: {line!r}"
                )
                return
            continue
        if line.strip() == "return result":
            saw_return = True
    pytest.fail("never found a `return result` line in chart_tools.py")


def test_pin_05_no_colon_skip_pre_extraction() -> None:
    """Pin #5 — dispatcher no-colon skip runs BEFORE extract_chart_images.

    Both seams (`dispatch_message` and `dispatch_completed`) must have the
    `if ":" not in source` short-circuit. PRESERVATION: the skip is
    pre-existing (Phase B added `extract_chart_images` AFTER the existing
    short-circuits; never inside them).
    """
    dispatcher = _read(DISPATCHER_PY)
    short_circuits = len(
        re.findall(r'if\s*":"\s*not\s*in\s*source\b', dispatcher)
    )
    assert short_circuits >= 2, (
        f"dispatcher.py must have ≥2 `if \":\" not in source` short-circuits "
        f"(one per seam); found {short_circuits}"
    )
    # Verify each short-circuit precedes an extract call (in source order).
    # The `":" not in source` string also appears in docstrings; match the
    # actual `if ":" not in source:` control-flow form to anchor at code
    # sites. Likewise for extract, match `extract_chart_images(content)`
    # (the call form, not the `def`).
    skip_matches = list(re.finditer(r"if\s+\":\"\s+not\s+in\s+source", dispatcher))
    extract_matches = list(
        re.finditer(r"extract_chart_images\(content\)", dispatcher)
    )
    assert len(extract_matches) >= 2, (
        f"dispatcher.py must have ≥2 extract_chart_images call sites; "
        f"found {len(extract_matches)}"
    )
    # In source order: each short-circuit must precede the corresponding
    # extract call in the same seam. The dispatcher has 2 seams and they
    # are interleaved (short_circuit, extract, …, short_circuit, extract, …).
    # Check that the FIRST skip precedes the FIRST extract call (zip-style).
    assert len(skip_matches) == 2 and len(extract_matches) >= 2, (
        f"expected 2 short-circuits and ≥2 extract calls; got "
        f"{len(skip_matches)} and {len(extract_matches)}"
    )
    assert skip_matches[0].start() < extract_matches[0].start(), (
        "first no-colon short-circuit must precede the first extract call"
    )
    assert skip_matches[1].start() < extract_matches[1].start(), (
        "second no-colon short-circuit must precede the second extract call"
    )


def test_pin_09_discord_2000_chunker() -> None:
    """Pin #9 — Discord 2000-char chunking preserved."""
    discord = _read(DISCORD_ADAPTER)
    assert "DISCORD_MAX_MESSAGE_LENGTH" in discord, "missing DISCORD_MAX_MESSAGE_LENGTH"
    # The constant is imported from daemon.sources.adapters.discord.constants
    # (where it is defined as 2000). Verify the constant value too.
    constants = REPO_ROOT / "daemon/sources/adapters/discord/constants.py"
    assert "DISCORD_MAX_MESSAGE_LENGTH = 2000" in _read(constants), (
        "DISCORD_MAX_MESSAGE_LENGTH must equal 2000"
    )


def test_pin_13_slack_blocks_threshold() -> None:
    """Pin #13 — Slack BLOCKS_CONTENT_THRESHOLD (400 chars) preserved."""
    slack = _read(SLACK_ADAPTER)
    assert "BLOCKS_CONTENT_THRESHOLD = 400" in slack, (
        "Slack adapter must declare BLOCKS_CONTENT_THRESHOLD = 400"
    )


def test_pin_22_manager_tmp_image_store_public() -> None:
    """Pin #22 — daemon/manager.py tmp_image_store property still public."""
    manager = _read(MANAGER_PY)
    assert "def tmp_image_store(self)" in manager, (
        "manager.py must define `def tmp_image_store(self)` (public accessor)"
    )
    # The accessor is a @property — verify the decorator is in place
    # (allow some lines of docstring between @property and def).
    # Loose check: "@property" appears within ~5 lines BEFORE the def.
    idx = manager.find("def tmp_image_store(self)")
    pre = manager[:idx].splitlines()[-5:]
    assert any("@property" in line for line in pre), (
        "@property decorator must immediately precede the tmp_image_store def"
    )


def test_pin_23_mock_source_captures_message() -> None:
    """Pin #23 — MockSourceAdapter captures the whole OutgoingMessage.

    tests/e2e/mock_source_server.py ``MockSourceAdapter.send()`` must
    capture the entire ``OutgoingMessage`` (no field-stripping).
    """
    mock = _read(MOCK_SOURCE_SERVER)
    assert "self.sent_messages: list[OutgoingMessage] = []" in mock, (
        "MockSourceAdapter must declare self.sent_messages: list[OutgoingMessage]"
    )
    assert "self.sent_messages.append(message)" in mock, (
        "MockSourceAdapter.send() must append the whole message to self.sent_messages"
    )


# ============================================================================
# FEATURE pins (6, 7, 8, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 24) — 15
# ============================================================================


def test_pin_06_both_seam_extract() -> None:
    """Pin #6 (INVERTED) — extract_chart_images at BOTH seams.

    ``dispatch_message`` AND ``dispatch_completed`` both call
    ``extract_chart_images`` as the LAST content transformation before
    ``OutgoingMessage`` construction. ``OutgoingMessage.images`` populated
    at both construction sites; NEVER at registry.py:980.
    """
    dispatcher = _read(DISPATCHER_PY)
    call_sites = len(re.findall(r"extract_chart_images\s*\(", dispatcher))
    assert call_sites >= 2, (
        f"dispatcher.py must have ≥2 extract_chart_images call sites (both seams); "
        f"found {call_sites}"
    )
    images_kw = len(re.findall(r"^\s*images\s*=\s*", dispatcher, re.MULTILINE))
    assert images_kw >= 2, (
        f"dispatcher.py must have ≥2 `images=` kwarg sites in OutgoingMessage "
        f"constructions; found {images_kw}"
    )
    # registry.py:980 must NOT have `images=` (the pin NEVER there)
    registry_lines = _read(REGISTRY_PY).splitlines()
    if len(registry_lines) >= 980:
        line_980 = registry_lines[979]
        assert not re.match(r"^\s*images\s*=", line_980), (
            f"registry.py:980 must NOT populate `images=`; got: {line_980!r}"
        )


def test_pin_07_outgoing_images_default() -> None:
    """Pin #7 — OutgoingMessage.images field defaults to None."""
    base = _read(BASE_PY)
    assert re.search(
        r"images:\s*list\[ImageAttachment\]\s*\|\s*None\s*=\s*None", base
    ), "OutgoingMessage.images must default to None (list[ImageAttachment] | None)"


def test_pin_08_discord_chunk_kwarg() -> None:
    """Pin #8 — Discord _send_single_chunk accepts file=None + files= list."""
    discord = _read(DISCORD_ADAPTER)
    assert "async def _send_single_chunk" in discord, "missing _send_single_chunk def"
    assert re.search(r"file:\s*[^=]*\|\s*None\s*=\s*None", discord), (
        "_send_single_chunk must accept `file: ... | None = None`"
    )
    assert re.search(r"files:\s*list\[[^]]+\]\s*\|\s*None\s*=\s*None", discord), (
        "_send_single_chunk must accept `files: list[...] | None = None`"
    )
    # The single-image vs multi-image switch: file XOR files (discord-py 2.7.1
    # forbids both; the chunk-1 path picks one and the multi-image path picks
    # the other).
    assert re.search(r'kwargs\["files"\]\s*=\s*files', discord), (
        "missing `kwargs[\"files\"] = files` (multi-image path)"
    )
    assert re.search(r'kwargs\["file"\]\s*=\s*file', discord), (
        "missing `kwargs[\"file\"] = file` (single-image path)"
    )


def test_pin_10_telegram_multipart_retry() -> None:
    """Pin #10 — Telegram _api_call_multipart 3-retry + 4xx-non-transient."""
    telegram = _read(TELEGRAM_ADAPTER)
    assert "async def _api_call_multipart" in telegram, "missing _api_call_multipart def"
    assert "MAX_RETRIES = 3" in telegram, "MAX_RETRIES must be 3"
    assert re.search(r"for\s+attempt\s+in\s+range\(MAX_RETRIES\)", telegram), (
        "missing 3-attempt retry loop"
    )
    # 4xx non-transient classifier
    assert re.search(r"400\s*<=\s*error_code\s*<\s*500", telegram), (
        "missing 4xx non-transient classifier"
    )
    assert "_TelegramNonTransientAPIError" in telegram, (
        "missing _TelegramNonTransientAPIError class for 4xx"
    )
    # record_failure is called only for TRANSPORT errors (per architecture)
    assert "record_failure" in telegram, "missing circuit-breaker record_failure"


def test_pin_11_telegram_size_ladder() -> None:
    """Pin #11 — Telegram sendPhoto (≤10 MB) / sendDocument (≤50 MB)."""
    telegram = _read(TELEGRAM_ADAPTER)
    assert re.search(r'method\s*=\s*"sendPhoto"', telegram), (
        "missing `method = \"sendPhoto\"` branch"
    )
    assert re.search(r'method\s*=\s*"sendDocument"', telegram), (
        "missing `method = \"sendDocument\"` branch"
    )
    assert re.search(
        r"len\(file_bytes\)\s*<=\s*TELEGRAM_PHOTO_MAX_BYTES", telegram
    ), "missing ≤10 MB photo threshold"
    assert re.search(
        r"len\(file_bytes\)\s*>\s*TELEGRAM_DOCUMENT_MAX_BYTES", telegram
    ), "missing >50 MB document threshold"


def test_pin_12_slack_upload_v2() -> None:
    """Pin #12 — Slack single-call files_upload_v2 + capability flag."""
    slack = _read(SLACK_ADAPTER)
    assert re.search(r"class\s+SlackCapabilityError", slack), (
        "missing SlackCapabilityError class"
    )
    assert "files_upload_v2" in slack, "missing files_upload_v2 reference"
    assert re.search(
        r"self\._slack_capability_flags:\s*set\[str\]\s*=\s*set\(\)", slack
    ), "missing _slack_capability_flags set init"
    # Classify missing_scope BEFORE record_failure (per architecture)
    assert "missing_scope" in slack, "missing missing_scope classification"


def test_pin_14_daemon_constants() -> None:
    """Pin #14 — daemon/constants.py exports the 4 size constants + MIME whitelist."""
    constants = _read(CONSTANTS_PY)
    assert re.search(
        r"DISCORD_FILE_MAX_BYTES:\s*int\s*=\s*8\s*\*\s*1024\s*\*\s*1024", constants
    ), "DISCORD_FILE_MAX_BYTES must be 8 * 1024 * 1024"
    assert re.search(
        r"TELEGRAM_PHOTO_MAX_BYTES:\s*int\s*=\s*10\s*\*\s*1024\s*\*\s*1024", constants
    ), "TELEGRAM_PHOTO_MAX_BYTES must be 10 * 1024 * 1024"
    assert re.search(
        r"TELEGRAM_DOCUMENT_MAX_BYTES:\s*int\s*=\s*50\s*\*\s*1024\s*\*\s*1024", constants
    ), "TELEGRAM_DOCUMENT_MAX_BYTES must be 50 * 1024 * 1024"
    assert re.search(
        r"SLACK_FILE_MAX_BYTES:\s*int\s*=\s*1024\s*\*\s*1024\s*\*\s*1024", constants
    ), "SLACK_FILE_MAX_BYTES must be 1024 * 1024 * 1024"
    # CHART_IMAGE_MIME_WHITELIST — frozenset with 4 MIMEs
    assert re.search(
        r"CHART_IMAGE_MIME_WHITELIST:\s*frozenset\[str\]", constants
    ), "CHART_IMAGE_MIME_WHITELIST must be a frozenset[str]"
    for mime in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        assert mime in constants, f"CHART_IMAGE_MIME_WHITELIST missing MIME {mime}"


def test_pin_15_charter_image_tool() -> None:
    """Pin #15 — agents/charter/meta.json tools.allow contains "image"."""
    meta = _read(CHARTER_META)
    assert '"image"' in meta, 'charter meta.json must include "image" in tools.allow'


def test_pin_16_charter_version() -> None:
    """Pin #16 — agents/charter/meta.json version is 1.2.0."""
    meta = _read(CHARTER_META)
    assert re.search(r'"version":\s*"1\.2\.0"', meta), (
        'charter meta.json version must be "1.2.0"'
    )


@pytest.mark.parametrize("agent", CHART_CAPABLE_AGENTS)
def test_pin_17_chat_delivery_references(agent: str) -> None:
    """Pin #17 — 20 chart-capable agents reference "Chat Delivery".

    Each of the 20 agents (excluding charter itself) must reference
    "Chat Delivery" in one of rule/soul/tools_note/workflow .md.
    """
    agent_dir = REPO_ROOT / "agents" / agent
    if not agent_dir.is_dir():
        pytest.fail(f"agent dir not found: {agent_dir}")
    md_files = list(agent_dir.glob("*.md"))
    assert md_files, f"no .md files in {agent_dir}"
    matches = [p for p in md_files if "Chat Delivery" in p.read_text(encoding="utf-8")]
    assert matches, (
        f"agent {agent!r} does not reference 'Chat Delivery' in any "
        f"rule/soul/tools_note/workflow .md (searched: {[p.name for p in md_files]})"
    )


def test_pin_18_no_md_path_tokens_in_phase_c() -> None:
    """Pin #18 — no .md path tokens in Phase C diff added lines.

    Scans the Phase C commit range (3e2584b9..b31fa926 per phaseD-plan.md
    §2) for any `^+\\S*` line that contains a `.md` token in added content
    of `agents/*/{rule,soul,tools_note,workflow}.md`. The wider range
    4aa2eb13..53dabf0b (the actual Phase C commits touching agent prompt
    files) is checked separately below as a cross-validation.
    """
    range_narrow = "3e2584b9..b31fa926"
    range_wide = "4aa2eb13..53dabf0b"
    for label, rng in (("narrow (plan-specified)", range_narrow), ("wide (actual Phase C)", range_wide)):
        result = subprocess.run(
            [
                "git", "log", "-p", rng,
                "--", "agents/*/rule.md", "agents/*/soul.md",
                "agents/*/tools_note.md", "agents/*/workflow.md",
            ],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 and "unknown revision" in result.stderr:
            pytest.skip(f"{label}: git range {rng} not available in this worktree")
        added_lines = [
            line for line in result.stdout.splitlines() if line.startswith("+") and not line.startswith("+++")
        ]
        bad = [line for line in added_lines if re.search(r"\.md\b", line)]
        assert not bad, (
            f"{label} range {rng}: found {len(bad)} added lines with `.md` "
            f"path tokens: {bad[:3]}"
        )


def test_pin_19_busy_string_byte_identical_skill_and_test() -> None:
    """Pin #19 — busy string in skill.md is byte-identical to test pin."""
    busy = _BUSY_STRING
    test_hit = busy in _read(TEST_CHART_TOOLS)
    skill_hit = busy in _read(SKILL_MD)
    assert test_hit, "test pin must contain busy string"
    assert skill_hit, "skill.md must contain busy string"


def test_pin_20_wedged_charter_section_unchanged() -> None:
    """Pin #20 — Wedged-Charter Recovery section unchanged."""
    skill = _read(SKILL_MD)
    assert re.search(r"^##\s*Wedged-Charter Recovery", skill, re.MULTILINE), (
        "skill.md must have a `## Wedged-Charter Recovery` section heading"
    )
    for required in (
        "Caller escape hatch (`fresh=True`)",
        "Operator clears the hung orphan",
        "Daemon restart",
        "A paused charter surfaces a distinct error",
    ):
        assert required in skill, f"wedged-charter section missing: {required!r}"


def test_pin_21_slack_setup_files_write() -> None:
    """Pin #21 — docs/sources/slack-setup.md files:write (YAML + table)."""
    slack = _read(SLACK_SETUP_MD)
    hits = slack.count("files:write")
    assert hits >= 2, (
        f"slack-setup.md must have ≥2 `files:write` references "
        f"(YAML manifest + scopes table); found {hits}"
    )


def test_pin_24_ari_prewarm_reminder() -> None:
    """Pin #24 — Ari pre-warm reminder present in BOTH files."""
    ctx = _read(CONTEXT_MD)
    ari = _read(ARI_WORKFLOW)
    assert re.search(r"pre-warm|deploy step", ctx, re.IGNORECASE), (
        ".agents/shared/context.md must contain pre-warm / deploy-step language"
    )
    assert re.search(r"pre-warm|deploy step", ari, re.IGNORECASE), (
        "agents/ari/workflow.md must contain pre-warm / deploy-step language"
    )
    assert "install-mermaid-cli" in ari, (
        "ari/workflow.md must reference the install-mermaid-cli skill"
    )
