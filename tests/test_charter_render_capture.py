"""Tests for Phase A of chart-image-delivery.

18 test cases (per phaseA-plan.md Components §6 / Test Strategy):
  1. test_charter_workflow_persists_png_on_success
  2. test_charter_workflow_no_marker_on_image_save_failure
  3. test_charter_workflow_no_marker_on_render_failure
  4. test_charter_workflow_no_marker_on_retry_exhausted
  5. test_charter_workflow_marker_is_byte_stable
  6. test_charter_meta_json_includes_image_category
  7. test_install_skill_idempotent_on_warm_cache
  8. test_install_skill_handles_missing_nvm        (@pytest.mark.slow)
  9. test_readiness_probe_warm_returns_zero
 10. test_readiness_probe_cold_returns_one_and_invokes_install
 11. test_readiness_probe_install_in_progress_returns_two
 12. test_hybrid_executor_prewarm_renders_warm_first_try
 13. test_render_security_pins_strict_html_false
 14. test_render_pre_sanitizer_strips_init_and_frontmatter
 15. test_render_ulimit_and_timeout_wrap_invocation
 16. test_npx_y_retired_from_workflow
 17. test_store_capacity_precheck_skips_persist_above_80_percent
 18. test_pin_audit_greps_pass

Harness note: tests #1-#5 exercise charter's LLM-driven workflow; per
R4 (reviewer iter-002), they are `@pytest.mark.integration` cases that
require a live charter instance, NOT pure unit tests.

All other tests (#6-#18) are pure unit tests:
  - #6, #16: file content greps
  - #7: install-skill idempotency (config-file based, no live install)
  - #8: opt-in @pytest.mark.slow — ~200MB download path; not run by
    default per plan
  - #9-#12: charter_readiness_probe (4-signal contract) + lock
  - #13-#15: bash block inspection
  - #17: store-capacity pre-check logic (pure python)
  - #18: pin-audit content-grep test
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


# --- Path constants -----------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
CHARTER_DIR = REPO_ROOT / "agents" / "charter"
CHARTER_SKILLS = CHARTER_DIR / "skills-template"
INSTALL_LIB = CHARTER_SKILLS / "install-mermaid-cli.lib.sh"
INSTALL_SKILL_MD = CHARTER_SKILLS / "install-mermaid-cli.md"
WORKFLOW_MD = CHARTER_DIR / "workflow.md"
META_JSON = CHARTER_DIR / "meta.json"
CHART_SKILL_MD = (
    REPO_ROOT / "agents" / "_prompt_system" / "innate-skills" / "chart" / "skill.md"
)
DAEMON_CHART_TOOLS = REPO_ROOT / "daemon" / "tools" / "chart_tools.py"
TEST_CHART_TOOLS = REPO_ROOT / "tests" / "test_chart_tools.py"

# --- Locked marker regex (decisions.md §marker, byte-stable) -------------
MARKER_RE = re.compile(r"^<!-- ens-img:chart-render:([a-f0-9]{32}) -->$")

# --- Busy / paused pin strings (byte-stable) ----------------------------
_BUSY_STRING = "Error: Charter busy; pass fresh=True for parallel charts."
_PAUSED_STRING = "Error: Charter is paused; resume it or pass fresh=True for a new charter."


# =========================================================================
# Tests #1-#5 — charter LLM-driven workflow (HARNESS / @integration)
# =========================================================================

@pytest.mark.integration
def test_charter_workflow_persists_png_on_success():
    """Stub image_save → fixed image_id; assert marker matches LOCKED form.

    Per arch-rec §3 amendment #19: marker is emitted ONLY after a valid
    image_save result. Harness case — requires a live charter spawn.
    """
    # Placeholder — harness case. Implementation requires a live charter
    # instance (LLM-driven). Marked integration; not run by default.
    pytest.skip("HARNESS — requires live charter spawn; see plan T8/T9/T10")


@pytest.mark.integration
def test_charter_workflow_no_marker_on_image_save_failure():
    """image_save returns 'Error: ...'; assert Mermaid present, NO marker.

    Per arch-rec §3 amendment #19: error response short-circuits to
    text-only delivery. Harness case.
    """
    pytest.skip("HARNESS — requires live charter spawn; see plan T8/T9/T10")


@pytest.mark.integration
def test_charter_workflow_no_marker_on_render_failure():
    """mmdc exits non-zero; assert Mermaid present (validation OK),
    no marker.
    """
    pytest.skip("HARNESS — requires live charter spawn; see plan T8/T9/T10")


@pytest.mark.integration
def test_charter_workflow_no_marker_on_retry_exhausted():
    """All 3 syntax retries fail; assert text + warning, NO marker.

    Per arch-rec §3 amendment #19: syntax retry budget preserved; render
    side never retries.
    """
    pytest.skip("HARNESS — requires live charter spawn; see plan T8/T9/T10")


@pytest.mark.integration
def test_charter_workflow_marker_is_byte_stable():
    """Render multiple charts; assert marker regex byte-identical each
    (no LLM variance).
    """
    pytest.skip("HARNESS — requires live charter spawn; see plan T8/T9/T10")


# =========================================================================
# Test #6 — meta.json has image category
# =========================================================================

def test_charter_meta_json_includes_image_category():
    """charter meta.json tools.allow includes 'image'; version is 1.2.0."""
    data = json.loads(META_JSON.read_text())
    assert "image" in data["tools"]["allow"], (
        f"tools.allow missing 'image' (got {data['tools']['allow']})"
    )
    assert data["version"] == "1.2.0", (
        f"version should be 1.2.0 (got {data['version']})"
    )


# =========================================================================
# Test #7 — install skill idempotent on warm cache
# =========================================================================

def test_install_skill_idempotent_on_warm_cache():
    """Second invocation on a warm host is a verify-only fast path.

    The install skill (install-mermaid-cli.md) body must explicitly
    state idempotency. This is a content-grep on the skill file —
    verifies the idempotency contract is documented (the actual
    idempotency is verified by the verify-step in install-mermaid-cli.md).
    """
    body = INSTALL_SKILL_MD.read_text()
    assert "idempotent" in body.lower() or "Idempotent" in body, (
        "install-mermaid-cli.md must document idempotency"
    )
    # Must reference the config file the 4-signal probe reads
    assert "charter-mermaid-puppeteer.json" in body, (
        "install-mermaid-cli.md must reference the install config file"
    )


# =========================================================================
# Test #8 — install skill handles missing nvm (slow, opt-in)
# =========================================================================

@pytest.mark.slow
def test_install_skill_handles_missing_nvm():
    """Invocation on a host with no ~/.nvm runs the bootstrap.

    Marked @pytest.mark.slow because the install downloads ~200MB
    (nvm+Node+chromium). Per plan: NOT run per-PR; opt-in CI lane.
    """
    pytest.skip("SLOW — ~200MB download; opt-in CI lane only")


# =========================================================================
# Test #9 — readiness probe warm returns zero (NEW, arch-rec #17)
# =========================================================================

def test_readiness_probe_warm_returns_zero(tmp_path):
    """All 4 signals present in config; probe returns 0; exports vars."""
    config = tmp_path / "charter-mermaid-puppeteer.json"
    config.write_text(json.dumps({
        "mermaidCli": 12,
        "mmdcPath": "/bin/echo",   # absolute + executable in test env
        "puppeteerConfig": {"executablePath": "/bin/echo"},
    }))
    env = os.environ.copy()
    env["CHARTER_PUPPETEER_CONFIG"] = str(config)
    # Use a private HOME so the lock check is clean
    env["HOME"] = str(tmp_path)

    result = subprocess.run(
        ["bash", "-c",
         f"set -e; . '{INSTALL_LIB}'; "
         f"charter_readiness_probe; "
         f"echo MMDC_BIN=$MMDC_BIN; "
         f"echo PUPPETEER_EXECUTABLE_PATH=$PUPPETEER_EXECUTABLE_PATH"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"probe should be warm; rc={result.returncode} stdout={result.stdout} "
        f"stderr={result.stderr}"
    )
    assert "MMDC_BIN=/bin/echo" in result.stdout
    assert "PUPPETEER_EXECUTABLE_PATH=/bin/echo" in result.stdout


# =========================================================================
# Test #10 — readiness probe cold returns 1 (config missing)
# =========================================================================

def test_readiness_probe_cold_returns_one_and_invokes_install(tmp_path):
    """No config file; probe returns 1 (caller invokes install skill)."""
    env = os.environ.copy()
    env["CHARTER_PUPPETEER_CONFIG"] = str(tmp_path / "missing.json")
    env["HOME"] = str(tmp_path)

    result = subprocess.run(
        ["bash", "-c",
         f". '{INSTALL_LIB}'; "
         f"charter_readiness_probe; "
         f"echo rc=$?"],
        env=env, capture_output=True, text=True,
    )
    # The bash wrapper's returncode is the LAST command's exit (echo=0).
    # The actual probe return code is captured in stdout ("rc=N").
    assert "rc=1" in result.stdout, (
        f"probe should be cold; bash_rc={result.returncode} stdout={result.stdout!r}"
    )


# =========================================================================
# Test #11 — readiness probe install-in-progress returns 2
# =========================================================================

def test_readiness_probe_install_in_progress_returns_two(tmp_path):
    """Lock file present; probe returns 2 (install-in-progress-other)."""
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    # Create the lock file
    (tmp_path / ".cache" / "charter").mkdir(parents=True)
    (tmp_path / ".cache" / "charter" / "mermaid-install.lock").touch()

    result = subprocess.run(
        ["bash", "-c",
         f". '{INSTALL_LIB}'; "
         f"charter_readiness_probe; "
         f"echo rc=$?"],
        env=env, capture_output=True, text=True,
    )
    # The bash wrapper's returncode is the LAST command's exit (echo=0).
    # The actual probe return code is captured in stdout ("rc=N").
    assert "rc=2" in result.stdout, (
        f"probe should report install-in-progress; bash_rc={result.returncode} "
        f"stdout={result.stdout!r}"
    )


# =========================================================================
# Test #12 — hybrid executor pre-warm renders warm first try
# =========================================================================

def test_hybrid_executor_prewarm_renders_warm_first_try(tmp_path):
    """Simulate ari deploy-step pre-warm: write config file, probe returns 0
    on first call (no self-heal needed). This is the same test as #9 with
    different framing — assert the rc=0 first call (no flock contention,
    no cold-miss bump).
    """
    config = tmp_path / "charter-mermaid-puppeteer.json"
    config.write_text(json.dumps({
        "mermaidCli": 12,
        "mmdcPath": "/bin/echo",
        "puppeteerConfig": {"executablePath": "/bin/echo"},
    }))
    env = os.environ.copy()
    env["CHARTER_PUPPETEER_CONFIG"] = str(config)
    env["HOME"] = str(tmp_path)

    # Two consecutive probe calls — both should be warm.
    result = subprocess.run(
        ["bash", "-c",
         f"set -e; . '{INSTALL_LIB}'; "
         f"charter_readiness_probe; rc1=$?; "
         f"charter_readiness_probe; rc2=$?; "
         f"echo rc1=$rc1 rc2=$rc2"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "rc1=0 rc2=0" in result.stdout, (
        f"both probes should be warm; got {result.stdout}"
    )
    # No cold-miss session state should be written
    session_state = tmp_path / ".cache" / "charter" / "mermaid-session-state.json"
    assert not session_state.exists(), (
        "warm path should NOT write the session state counter"
    )


# =========================================================================
# Test #13 — render security pins (strict + htmlLabels:false)
# =========================================================================

def test_render_security_pins_strict_html_false():
    """workflow.md must include -c '{"securityLevel":"strict","htmlLabels":false}'."""
    body = WORKFLOW_MD.read_text()
    # The exact string the mmdc invocation must carry
    assert '"securityLevel":"strict","htmlLabels":false}' in body, (
        "workflow.md must include the security pin in the mmdc invocation"
    )


# =========================================================================
# Test #14 — pre-render sanitizer strips init + frontmatter
# =========================================================================

def test_render_pre_sanitizer_strips_init_and_frontmatter():
    """workflow.md must include a sed pass that strips %%{init}%% blocks
    and frontmatter securityLevel: keys before mmdc invocation.
    """
    body = WORKFLOW_MD.read_text()
    # The sanitizer block is identifiable by the directive name
    assert r"%%\{init\}%%" in body, (
        "workflow.md must include the sed sanitizer for %%{init}%% directives"
    )
    assert "securityLevel:" in body, (
        "workflow.md must include the sed sanitizer for frontmatter securityLevel:"
    )
    # And the actual sed block
    assert re.search(r"sed\s+-E.*securityLevel", body, re.DOTALL), (
        "sed block must reference both directives"
    )


# =========================================================================
# Test #15 — render wrapped in ( ulimit -v ...; timeout 60 ... )
# =========================================================================

def test_render_ulimit_and_timeout_wrap_invocation():
    """workflow.md mmdc invocation must be wrapped in
    ( ulimit -v 2097152; timeout 60 ... ) — memory + wall-clock bounds.
    """
    body = WORKFLOW_MD.read_text()
    # The wrapper pattern
    assert re.search(r"\( ulimit -v 2097152;\s*timeout 60", body), (
        "workflow.md mmdc invocation must be wrapped in ( ulimit -v 2097152; timeout 60 ... )"
    )


# =========================================================================
# Test #16 — npx -y @mermaid-js RETIRED from workflow.md
# =========================================================================

def test_npx_y_retired_from_workflow():
    """workflow.md must contain ZERO matches of `npx -y @mermaid-js`.

    Per arch-rec §3 amendment #16: npx -y is retired; the install
    skill provides absolute-path mmdc instead.
    """
    body = WORKFLOW_MD.read_text()
    # Content-grep (not line numbers — Phase C may insert content above).
    assert "npx -y @mermaid-js" not in body, (
        "workflow.md must not invoke `npx -y @mermaid-js/...` (retired per arch-rec #16)"
    )


# =========================================================================
# Test #17 — store-capacity pre-check skips persist above 80% (🟢 optional)
# =========================================================================

def test_store_capacity_precheck_skips_persist_above_80_percent():
    """The 🟢 optional store-capacity pre-check must be implemented in
    workflow.md: `image_list(feature='chart-render')` sum > 80% of cap
    → skip persist, deliver text-only with `image_store_full` log.
    """
    body = WORKFLOW_MD.read_text()
    # The pre-check function must exist
    assert "image_store_chart_render_usage_under_threshold" in body, (
        "workflow.md must implement the store-capacity pre-check helper"
    )
    # The skip-when-full path
    assert "image_store_full" in body, (
        "workflow.md must log `image_store_full` when the pre-check trips"
    )
    # The 80% threshold (defines the cap math)
    assert re.search(r"0\.8\s*\*\s*cap", body) or "0.8 * cap" in body or "0.8 \\* cap" in body, (
        "workflow.md pre-check must use the 80% threshold"
    )


# =========================================================================
# Test #18 — pin-audit greps pass (replaces R1 line-number audit)
# =========================================================================

def test_pin_audit_greps_pass():
    """Content-addressable grep audit for the busy/paused pins and the
    Wedged-Charter Recovery heading. Replaces the R1 line-number audit
    that false-fails on Phase C inserts.
    """
    # _BUSY_STRING: chart_tools.py:55, tests/test_chart_tools.py:296,
    # chart/skill.md (2 sites)
    busy_in_daemon = _BUSY_STRING in DAEMON_CHART_TOOLS.read_text()
    busy_in_tests = _BUSY_STRING in TEST_CHART_TOOLS.read_text()
    chart_body = CHART_SKILL_MD.read_text()
    busy_in_skill = chart_body.count(_BUSY_STRING) >= 2
    assert busy_in_daemon, f"_BUSY_STRING not in {DAEMON_CHART_TOOLS}"
    assert busy_in_tests, f"_BUSY_STRING not in {TEST_CHART_TOOLS}"
    assert busy_in_skill, (
        f"_BUSY_STRING count in chart/skill.md < 2 (got {chart_body.count(_BUSY_STRING)})"
    )

    # _PAUSED_STRING: chart_tools.py:222 (⚠ SPLIT across two lines as
    # adjacent Python string literals; per plan: normalize whitespace
    # before search). test_chart_tools.py:298, chart/skill.md.
    daemon_text = _normalize_for_split_concat(DAEMON_CHART_TOOLS.read_text())
    paused_in_daemon = _PAUSED_STRING in daemon_text
    paused_in_tests = _PAUSED_STRING in TEST_CHART_TOOLS.read_text()
    paused_in_skill = _PAUSED_STRING in chart_body
    assert paused_in_daemon, (
        f"_PAUSED_STRING not in {DAEMON_CHART_TOOLS} (split-across-lines form)"
    )
    assert paused_in_tests, f"_PAUSED_STRING not in {TEST_CHART_TOOLS}"
    assert paused_in_skill, f"_PAUSED_STRING not in {CHART_SKILL_MD}"

    # Wedged-Charter Recovery heading
    assert "## Wedged-Charter Recovery" in chart_body, (
        f"`## Wedged-Charter Recovery` heading missing in {CHART_SKILL_MD}"
    )


def _normalize_for_split_concat(text: str) -> str:
    """Collapse Python adjacent-string-literal concatenation so a
    `\\n            \\"new charter.\\"`-style split matches the
    full single-line string. The plan's "⚠ split across two lines"
    warning notes this is intentional at chart_tools.py ~222-223.
    """
    # Replace "<whitespace>"-newline-<indentation>" with no space
    import re as _re
    return _re.sub(r'"\s*\n\s*"', "", text)
