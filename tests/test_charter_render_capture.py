"""Tests for Phase A of chart-image-delivery.

18 test cases (per phaseA-plan.md Components §6 / Test Strategy):
   1. test_charter_workflow_persists_png_on_success          (UNIT — de-stubbed)
   2. test_charter_workflow_no_marker_on_image_save_failure  (UNIT — de-stubbed)
   3. test_charter_workflow_no_marker_on_render_failure      (@integration harness)
   4. test_charter_workflow_no_marker_on_retry_exhausted     (@integration harness)
   5. test_charter_workflow_marker_is_byte_stable            (@integration harness)
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
  16. test_npx_y_retired_from_workflow                  (WIDENED: all of agents/charter/**)
  17. test_store_capacity_precheck_skips_persist_above_80_percent
  18. test_pin_audit_greps_pass

Phase A review-fix additions (NEEDS-FIX pass):
  - De-stubbed the 5 contract-critical cases so they RUN (not skip):
    marker emit (#1), marker suppress on image_save failure (#2),
    malformed-hex 31/33 chars + uppercase + non-string ids,
    JSONDecodeError / non-dict JSON, and store-full persist skip —
    all executed against the ACTUAL Step 6a/6b/6c code blocks extracted
    from workflow.md (the prompt code IS the contract).
  - MARKER_RE is now wired (was defined-never-used).
  - Lock-staleness crash recovery (lib) + verify-toolchain contract
    (lib, with a fake mmdc) + content pins for the anchored marker,
    sandboxed-default launch, lock-held-across-install, and the landed
    Chat Delivery section in the chart skill.

Harness note: tests #3-#5 exercise charter's LLM-driven workflow; per
R4 (reviewer iter-002), they remain `@pytest.mark.integration` cases
that require a live charter instance.
"""

from __future__ import annotations

import base64
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
# WIRED: the de-stubbed Step 6c contract tests assert against this regex.
MARKER_RE = re.compile(r"^<!-- ens-img:chart-render:([a-f0-9]{32}) -->$")

# --- Busy / paused pin strings (byte-stable) ----------------------------
_BUSY_STRING = "Error: Charter busy; pass fresh=True for parallel charts."
_PAUSED_STRING = "Error: Charter is paused; resume it or pass fresh=True for a new charter."


# =========================================================================
# Tests #1-#2 — marker emit/suppress contract (DE-STUBBED → unit tests)
# =========================================================================
#
# The Step 6c block in workflow.md IS the emit/suppress contract. These
# tests extract that exact block and execute it against stubbed
# image_save results — no live charter needed, no drift between the
# prompt code and the tested behavior.


def _extract_workflow_python(heading: str) -> str:
    """Extract the first ```python fenced block after a workflow.md
    heading (content-addressable — not line numbers)."""
    body = WORKFLOW_MD.read_text()
    idx = body.index(heading)
    m = re.search(r"```python\n(.*?)```", body[idx:], re.DOTALL)
    assert m, f"no ```python fence found after {heading!r}"
    return m.group(1)


def _run_step6c(save_result: str):
    """Exec workflow.md's Step 6c block with a stubbed save_result.
    Returns (image_id, marker) — the emit/suppress contract under test."""
    ns: dict = {"save_result": save_result}
    exec(compile(_extract_workflow_python("### Step 6c"), "<workflow-6c>", "exec"), ns)
    return ns["image_id"], ns["marker"]


def test_charter_workflow_persists_png_on_success():
    """(de-stubbed) Valid image_save JSON → marker in the LOCKED form.

    Per arch-rec §3 amendment #19: marker is emitted ONLY after a valid
    image_save result; MARKER_RE is the byte-exact Phase B pin.
    """
    good = json.dumps({"image_id": "a" * 32, "content_type": "image/png"})
    image_id, marker = _run_step6c(good)
    assert image_id == "a" * 32
    assert MARKER_RE.match(marker), f"marker not LOCKED form: {marker!r}"
    assert marker == f"<!-- ens-img:chart-render:{'a' * 32} -->"


def test_charter_workflow_no_marker_on_image_save_failure():
    """(de-stubbed) image_save returns 'Error: ...' → Mermaid-only, NO marker.

    Per arch-rec §3 amendment #19: an error response short-circuits to
    text-only delivery.
    """
    image_id, marker = _run_step6c("Error: store not initialized")
    assert image_id is None
    assert marker == ""


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps({"image_id": "a" * 31}),   # 31 hex — too short
        json.dumps({"image_id": "b" * 33}),   # 33 hex — too long
        json.dumps({"image_id": "C" * 32}),   # uppercase — not [a-f0-9]
        json.dumps({"image_id": ""}),         # empty
        json.dumps({"image_id": "g" * 32}),   # non-hex
        json.dumps({"image_id": 12345}),      # non-string id
        json.dumps({"image_id": None}),       # null id
        '{"image_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',  # JSONDecodeError
        "42",                                 # valid JSON, not an object
        "[1, 2]",                             # valid JSON, not an object
        "null",                               # valid JSON, not an object
    ],
)
def test_marker_suppressed_on_malformed_save_payloads(payload):
    """(de-stubbed) Malformed ids (31/33 chars, uppercase, non-string),
    unparseable JSON, and non-dict JSON all suppress the marker."""
    image_id, marker = _run_step6c(payload)
    assert image_id is None, f"malformed payload accepted: {payload!r}"
    assert marker == ""


@pytest.mark.parametrize(
    "bad_id", ["a" * 31, "b" * 33, "C" * 32, "", "g" * 32]
)
def test_marker_re_regex_rejects_malformed_ids(bad_id):
    """MARKER_RE itself (the locked Phase B pin) rejects the malformed
    shapes — the regex and the code must stay in agreement."""
    assert MARKER_RE.match(f"<!-- ens-img:chart-render:{bad_id} -->") is None


def test_marker_suppressed_on_store_full():
    """(de-stubbed) The store-full sentinel short-circuits to text-only."""
    image_id, marker = _run_step6c("image_store_full")
    assert image_id is None
    assert marker == ""


# =========================================================================
# Tests #3-#5 — charter LLM-driven workflow (HARNESS / @integration)
# =========================================================================

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
# Test #16 — npx -y RETIRED from ALL of agents/charter/** (WIDENED)
# =========================================================================

def test_npx_y_retired_from_workflow():
    """agents/charter/** must contain ZERO matches of `npx -y`.

    Widened per the Phase A review: under context compression a rule.md
    cardinal outranks workflow.md, so the retirement must hold file-wide
    — rule.md, workflow.md, install skill, lib, meta. Even prohibitive
    prose must avoid the literal token: a zero-tolerance ban is only
    auditable while `grep -r 'npx -y' agents/charter/` finds nothing.
    """
    offenders = []
    for p in sorted(CHARTER_DIR.rglob("*")):
        if p.is_file() and "npx -y" in p.read_text(errors="replace"):
            offenders.append(str(p.relative_to(REPO_ROOT)))
    assert offenders == [], (
        "`npx -y` must be retired file-wide (arch-rec §3 amendment #16); "
        f"found in: {offenders}"
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


def _run_step6ab(rows, tmp_path):
    """Exec workflow.md's Step 6a + 6b blocks with stubbed image_list /
    image_save. `rows` is a list of size dicts or an error string.
    Returns (namespace, saved_calls)."""
    code_a = _extract_workflow_python("### Step 6a")
    code_b = _extract_workflow_python("### Step 6b")
    saved: list = []

    def fake_image_list(**_kwargs):
        if isinstance(rows, str):
            return rows
        return json.dumps(rows)

    def fake_image_save(**kwargs):
        saved.append(kwargs)
        return json.dumps({"image_id": "b" * 32})

    png = tmp_path / "out.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    ns = {
        "image_list": fake_image_list,
        "image_save": fake_image_save,
        "TMPPNG_PATH": str(png),
    }
    exec(compile(code_a, "<workflow-6a>", "exec"), ns)
    exec(compile(code_b, "<workflow-6b>", "exec"), ns)
    return ns, saved


def test_store_full_skips_image_save_call(tmp_path):
    """(de-stubbed, exec) Footprint > 80% of the 1 GiB cap → image_save
    is NOT called and the store-full sentinel short-circuits to
    text-only delivery."""
    over_cap = [{"size_bytes": 900_000_000}]  # > 0.8 * 2**30
    ns, saved = _run_step6ab(over_cap, tmp_path)
    assert saved == [], "image_save must be skipped when the store is full"
    assert ns["save_result"] == "image_store_full"


def test_store_precheck_allows_persist_under_cap(tmp_path):
    """(de-stubbed, exec) Footprint well under the cap → persist proceeds."""
    ns, saved = _run_step6ab([{"size_bytes": 100_000_000}], tmp_path)
    assert len(saved) == 1
    assert json.loads(ns["save_result"])["image_id"] == "b" * 32


def test_store_precheck_fails_open_on_store_error(tmp_path):
    """(de-stubbed, exec) Store-not-initialized → fail-open (persist
    proceeds); text delivery is never blocked by a pre-check error."""
    ns, saved = _run_step6ab("Error: store not initialized", tmp_path)
    assert len(saved) == 1


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


# =========================================================================
# Phase A review-fix tests — lock lifecycle, verify contract, content pins
# =========================================================================

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _run_lib(script: str, home: Path) -> subprocess.CompletedProcess:
    """Run a bash snippet with the lib sourced, a private HOME, and a
    clean probe environment (no inherited stale-window override)."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("CHARTER_LOCK_STALE_SECS", None)
    env["CHARTER_PUPPETEER_CONFIG"] = str(home / "unset-config.json")
    return subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True
    )


def _make_fake_mmdc(tmp_path: Path, mode: str):
    """A fake mmdc that records its argv + puppeteer config and emits
    evidence artifacts. Modes: ok | fail | sandbox-first."""
    calls = tmp_path / "calls.log"
    png = tmp_path / "template.png"
    png.write_bytes(PNG_1X1)
    script = f"""#!/usr/bin/env bash
out=""; cfgfile=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  if [ "$prev" = "--puppeteerConfigFile" ]; then cfgfile="$a"; fi
  prev="$a"
done
printf 'ARGS: %s\\n' "$*" >> '{calls}'
printf 'CFG: %s\\n' "$(cat "$cfgfile" 2>/dev/null)" >> '{calls}'
case "{mode}" in
  fail)
    exit 3 ;;
  sandbox-first)
    if grep -q 'no-sandbox' "$cfgfile" 2>/dev/null; then
      case "$out" in
        *.svg) printf 'svg-content' > "$out" ;;
        *) cat '{png}' > "$out" ;;
      esac
    else
      echo "No usable sandbox! Falling back is not automatic." >&2
      exit 1
    fi ;;
  *)
    case "$out" in
      *.svg) printf 'svg-content' > "$out" ;;
      *) cat '{png}' > "$out" ;;
    esac ;;
esac
"""
    mmdc = tmp_path / "mmdc"
    mmdc.write_text(script)
    mmdc.chmod(0o755)
    return mmdc, calls


def _make_verify_cfg(tmp_path: Path, mmdc: Path) -> Path:
    chrome = tmp_path / "chrome"
    chrome.write_text("#!/bin/sh\nexit 0\n")
    chrome.chmod(0o755)
    cfg = tmp_path / "verify-cfg.json"
    cfg.write_text(json.dumps({
        "mermaidCli": 12,
        "mmdcPath": str(mmdc),
        "puppeteerConfig": {"executablePath": str(chrome), "args": []},
    }))
    return cfg


def test_lock_staleness_clears_abandoned_lock(tmp_path):
    """Crash recovery (review ride-along): a lock file left behind by a
    crashed install must NOT wedge every future probe into rc=2. A lock
    older than the staleness window is treated as abandoned — the probe
    reports not-held and removes it."""
    lockdir = tmp_path / ".cache" / "charter"
    lockdir.mkdir(parents=True)
    lock = lockdir / "mermaid-install.lock"
    lock.touch()

    # Fresh lock → held → rc=2
    r = _run_lib(f". '{INSTALL_LIB}'; charter_readiness_probe; echo rc=$?", tmp_path)
    assert "rc=2" in r.stdout, f"fresh lock should be held: {r.stdout!r}"

    # Backdate beyond the default 1800s window → stale → not held (rc=1,
    # config missing) AND the abandoned lock file is removed.
    subprocess.run(
        ["bash", "-c", f"touch -d '40 minutes ago' '{lock}'"], check=True
    )
    r = _run_lib(f". '{INSTALL_LIB}'; charter_readiness_probe; echo rc=$?", tmp_path)
    assert "rc=1" in r.stdout, f"stale lock must not read as held: {r.stdout!r}"
    assert not lock.exists(), "stale lock must be removed by the probe"


def test_verify_toolchain_passes_on_real_evidence(tmp_path):
    """charter_verify_toolchain renders a minimal diagram under the
    render contract and passes on real SVG+PNG evidence (fake mmdc)."""
    mmdc, calls = _make_fake_mmdc(tmp_path, "ok")
    cfg = _make_verify_cfg(tmp_path, mmdc)
    r = _run_lib(
        f". '{INSTALL_LIB}'; charter_verify_toolchain '{mmdc}' '{cfg}'; echo vrc=$?",
        tmp_path,
    )
    assert "vrc=0" in r.stdout, f"verify should pass: {r.stdout} {r.stderr}"
    log = calls.read_text()
    # Render contract: security pin present, both artifacts requested
    assert '"securityLevel":"strict","htmlLabels":false' in log
    assert log.count("ARGS: ") == 2  # one svg + one png render


def test_verify_toolchain_fails_without_evidence(tmp_path):
    """A render-side failure (fake mmdc exits non-zero, no artifacts)
    must fail the verify — evidence, not claims."""
    mmdc, _ = _make_fake_mmdc(tmp_path, "fail")
    cfg = _make_verify_cfg(tmp_path, mmdc)
    r = _run_lib(
        f". '{INSTALL_LIB}'; charter_verify_toolchain '{mmdc}' '{cfg}'; echo vrc=$?",
        tmp_path,
    )
    assert "vrc=1" in r.stdout, f"verify must fail without evidence: {r.stdout}"


def test_verify_toolchain_sandbox_fallback_is_logged_single_retry(tmp_path):
    """Sandbox policy (arch-rec §3 amendment #15): sandboxed launch is
    the default; --no-sandbox is applied ONLY as the logged single
    fallback after a sandbox launch failure."""
    mmdc, calls = _make_fake_mmdc(tmp_path, "sandbox-first")
    cfg = _make_verify_cfg(tmp_path, mmdc)
    r = _run_lib(
        f". '{INSTALL_LIB}'; charter_verify_toolchain '{mmdc}' '{cfg}'; echo vrc=$?",
        tmp_path,
    )
    assert "vrc=0" in r.stdout, f"fallback should recover: {r.stdout} {r.stderr}"
    assert "WARN: sandboxed launch failed" in r.stdout, "fallback must be logged"
    log = calls.read_text()
    assert "--no-sandbox" in log, "fallback attempt must use --no-sandbox"
    assert log.count("ARGS: ") == 4, "exactly one retry per render (svg+png)"


def test_install_config_staged_until_verify_passes():
    """Blocker-4 contract: the probe-visible config is written ONLY after
    verify passes. The skill must stage to a temp path, verify against
    the staged file, then promote — a broken partial install can never
    satisfy the probe's static checks."""
    body = INSTALL_SKILL_MD.read_text()
    assert "TMPCFG_STAGE" in body, "install must stage the config to a temp path"
    # Order within the stage → verify → promote sequence (anchored at the
    # Step 4 heading so the Step 0 fast-path mention doesn't confuse it).
    stage_idx = body.index("### Step 4")
    verify_idx = body.index("charter_verify_toolchain", stage_idx)
    promote_idx = body.index('mv "$TMPCFG_STAGE"', verify_idx)
    assert stage_idx < verify_idx < promote_idx, (
        "install order must be: stage config → verify staged → promote"
    )
    # Verify runs against the STAGED file, not the probe path
    assert 'charter_verify_toolchain "$MMDC_PATH" "$TMPCFG_STAGE"' in body
    # Wired idempotency fast path (was documented but unwired)
    assert "verify-only fast path" in body
    assert "probe warm" in body


def test_cold_path_holds_lock_and_caps_self_heal():
    """Ride-along pins: lock held ACROSS the install (rc=2 reachable
    during real installs), amendment #18 self-heal cap wired, the dead
    triple-probe removed, and the else branch wired to the skip path."""
    body = WORKFLOW_MD.read_text()
    assert ". agents/charter/skills-template/install-mermaid-cli.lib.sh" in body, (
        "lib must be sourced repo-root-relative (phaseA-plan prescribed form)"
    )
    assert "\n. ./skills-template/" not in body, "cwd-relative source form must stay retired"
    assert "install_self_heal_capped" in body, "self-heal cap must be wired"
    assert "charter_read_install_session_state" in body, "cap counter must be read"
    assert "cold_misses=$(charter_readiness_probe" not in body, (
        "dead triple-probe must stay removed"
    )
    assert body.count("⚠️ Validation skipped, text-only, no marker.") >= 2, (
        "else branch + re-probe gate must both wire the skip path"
    )


def test_marker_emit_is_anchored_in_prose():
    """Ride-along pin: the marker line is ANCHORED — column 0, own line,
    no surrounding whitespace — in the emitter surface prose."""
    body = WORKFLOW_MD.read_text()
    assert "ANCHORED" in body
    assert "no leading or trailing whitespace" in body
    rule_body = (CHARTER_DIR / "rule.md").read_text()
    assert "no leading or trailing whitespace" in rule_body, (
        "the rule.md cardinal must pin the anchoring too (context-compression guard)"
    )


def test_sandboxed_default_and_never_root():
    """Ride-along pin (arch-rec §3 amendment #15): sandboxed default,
    --no-sandbox only as the logged single fallback, never-root guard."""
    body = WORKFLOW_MD.read_text()
    assert '"args": []' in body, "default puppeteer config must start sandboxed"
    assert "retrying once WITH --no-sandbox" in body, "fallback must be logged"
    assert "rendering as root is unsupported" in body, "never-root guard"
    skill_body = INSTALL_SKILL_MD.read_text()
    assert '"args": []' in skill_body, "staged install config starts sandboxed too"


def test_returned_block_is_sanitized_content():
    """Ride-along pin: the returned Mermaid block must be the SANITIZED
    content (byte-identical to what mmdc rendered), not the raw draft."""
    body = WORKFLOW_MD.read_text()
    assert "Byte-identity rule" in body
    assert "SANITIZED" in body
    assert "byte-identical to what" in body


def test_chart_skill_chat_delivery_section_landed():
    """Blocker-3 guard: the canonical Chat Delivery section exists in the
    chart skill — the target all 20 per-agent cross-references point at.
    Without it every 'See Chat Delivery in the chart skill' dangles."""
    body = CHART_SKILL_MD.read_text()
    assert "## Chat Delivery" in body
    assert "never hand-write" in body, "never-hand-write-on-chat rule"
    assert "When in doubt about the source, prefer `generate_chart()`" in body, (
        "uncertainty default (over-deliver-when-uncertain)"
    )
    assert "chat source wins" in body, "Self-generate vs. Delegate table row"
    assert "Do NOT strip the trailing marker" in body, "marker preserve rule"
    # Pin-audit invariants must survive the insertion
    assert body.count(_BUSY_STRING) >= 2
    assert _PAUSED_STRING in body
    assert "## Wedged-Charter Recovery" in body


# =========================================================================
# chart-render-opt-in tests — conditional gate (user directive 2026-10-05)
# =========================================================================
#
# The Step 5 gate is CONDITIONAL on the RENDER_IMAGE directive. These
# tests pin the opt-in contract:
#   * workflow.md carries the conditional gate (test-pinnable form,
#     exec-able) that returns False when the directive is absent/false.
#   * workflow.md carries the render_image=True form that returns True.
#   * The chart skill's signature table documents the opt-in.
#   * chart_tools.py declares the flag with a False default.
#   * The directive in chart_tools.py's dispatch message is byte-stable
#     (RENDER_IMAGE: true|false on its own line, lowercase).
#
# Preservation pins:
#   * The marker regex (LOCKED form) is untouched.
#   * Step 6a/6b/6c contract tests still pass under render_image=True.
#   * The always-render contract is GONE — markers are opt-in only.

GATE_HEADING = "### Conditional gate — test-pinnable form"


def _gate_function():
    """Exec workflow.md's conditional-gate python block; returns the
    should_render_dispatch_message function under test."""
    body = WORKFLOW_MD.read_text()
    idx = body.index(GATE_HEADING)
    m = re.search(r"```python\n(.*?)```", body[idx:], re.DOTALL)
    assert m, f"no ```python fence found after {GATE_HEADING!r}"
    ns: dict = {}
    exec(compile(m.group(1), "<gate>", "exec"), ns)
    return ns["should_render_dispatch_message"]


def test_render_image_gate_contract_default_is_false():
    """The gate function treats the ABSENT directive as False."""
    should_render = _gate_function()
    # No directive → False (text-only delivery)
    msg = "Create a flowchart diagram.\n\nDescription: x\nProject: p\n"
    assert should_render(msg) is False, (
        "absent RENDER_IMAGE directive MUST default to False (validated Mermaid only)"
    )


def test_render_image_gate_contract_false_is_false():
    """The gate function treats RENDER_IMAGE: false as False."""
    should_render = _gate_function()
    msg = (
        "Create a flowchart diagram.\n\nDescription: x\n"
        "RENDER_IMAGE: false\n"
    )
    assert should_render(msg) is False


def test_render_image_gate_contract_true_is_true():
    """The gate function treats RENDER_IMAGE: true as True."""
    should_render = _gate_function()
    msg = (
        "Create a flowchart diagram.\n\nDescription: x\n"
        "RENDER_IMAGE: true\n"
    )
    assert should_render(msg) is True


def test_render_image_gate_contract_handles_project_line():
    """The gate function still matches when Project: line comes after."""
    should_render = _gate_function()
    msg = (
        "Create a flowchart diagram.\n\nDescription: x\n"
        "RENDER_IMAGE: true\n"
        "Project: proj-1\n"
    )
    assert should_render(msg) is True


@pytest.mark.parametrize(
    "malformed",
    [
        "RENDER_IMAGE: True",      # capital T — not the contract form
        "RENDER_IMAGE: TRUE",      # caps
        "RENDER_IMAGE: yes",
        "RENDER_IMAGE: 1",
        "RENDER_IMAGE:",           # missing value
        "render_image: true",      # lowercase key
        "RENDER_IMAGE: truex",     # typo'd value
        "RENDER_IMAGE: false true",  # two values
    ],
)
def test_render_image_gate_contract_malformed_is_false(malformed):
    """Any directive form other than `true`/`false` (lowercase) → False.

    The directive is machine-emitted by chart_tools.py; a malformed
    value is a bug, not a render trigger. Fail-closed (False) is the
    safe default."""
    should_render = _gate_function()
    msg = f"Create a flowchart.\n\nDescription: x\n{malformed}\n"
    assert should_render(msg) is False, (
        f"malformed directive {malformed!r} must fail-closed to False"
    )


def test_render_image_directive_in_chart_tools_message_is_byte_stable():
    """chart_tools.py's dispatch message carries the exact directive form.

    Pins the test-pinnable contract: ``RENDER_IMAGE: <lowercase-bool>``
    is emitted on its own line in the dispatch message, so the charter
    gate regex can match it deterministically.
    """
    body = DAEMON_CHART_TOOLS.read_text()
    # The f-string form the tool emits — byte-exact.
    assert 'f"RENDER_IMAGE: {str(render_image).lower()}\\n"' in body, (
        "chart_tools.py must emit RENDER_IMAGE: <lowercase-bool> on its own line "
        "in the dispatch message (f-string form pinned)"
    )


def test_render_image_flag_declared_with_false_default():
    """generate_chart's render_image parameter defaults to False."""
    body = DAEMON_CHART_TOOLS.read_text()
    # Parameter signature: render_image: bool = False
    assert re.search(
        r"render_image:\s*bool\s*=\s*False", body
    ), "render_image must be declared with default False in generate_chart"


def test_workflow_md_step5_is_conditional_not_always():
    """workflow.md Step 5 is CONDITIONAL on RENDER_IMAGE (opt-in contract).

    Replaces the always-render contract: the step's heading carries the
    CONDITIONAL marker, and the prose states that the render pipeline is
    skipped when the directive is false or absent."""
    body = WORKFLOW_MD.read_text()
    assert "## Step 5: Validate + Render + Persist (CONDITIONAL on RENDER_IMAGE)" in body, (
        "Step 5 heading must carry the CONDITIONAL-on-RENDER_IMAGE marker"
    )
    assert "this entire step" in body, "Step 5 must state the conditional scope"
    assert "RENDER_IMAGE: true" in body, "Step 5 must reference the true directive"
    assert "RENDER_IMAGE:\nfalse" in body.replace("\n", "\n").replace(
        "RENDER_IMAGE:\nfalse", "RENDER_IMAGE: false"
    ) or "RENDER_IMAGE: false" in body, (
        "Step 5 must reference the false directive (or absence) as the skip path"
    )
    assert re.search(
        r"[Nn]o render, no PNG, no\s+tmp_images write, no marker", body
    ), (
        "Step 5 must pin the skip contract (no render, no PNG, no tmp_images, no marker)"
    )


def test_workflow_md_step6_is_conditional_not_always():
    """workflow.md Step 6 (persist + marker) is CONDITIONAL on RENDER_IMAGE."""
    body = WORKFLOW_MD.read_text()
    assert "## Step 6: Persist + Return (CONDITIONAL on RENDER_IMAGE)" in body, (
        "Step 6 heading must carry the CONDITIONAL-on-RENDER_IMAGE marker"
    )
    assert "RENDER_IMAGE: true" in body, (
        "Step 6 must gate persist + marker emission on the true directive"
    )


def test_workflow_md_summary_pin_conditional():
    """The Summary block reflects the conditional contract."""
    body = WORKFLOW_MD.read_text()
    assert "Step 5: CONDITIONAL on RENDER_IMAGE directive" in body, (
        "Summary must reflect the conditional contract"
    )


def test_chart_skill_signature_includes_render_image():
    """The chart skill's signature table documents the render_image flag."""
    body = CHART_SKILL_MD.read_text()
    assert "`render_image`" in body, "signature table must include render_image"
    assert "no (default `false`)" in body, "render_image must be documented as opt-in"
    assert "600s" in body and "1200s" in body, (
        "render_image must document the differential timeout (600s vs 1200s)"
    )


def test_chart_skill_has_opt_in_render_section():
    """The chart skill has a dedicated render_image section documenting
    the opt-in contract."""
    body = CHART_SKILL_MD.read_text()
    assert "## render_image: opt-in rendering" in body, (
        "chart skill must carry the dedicated render_image opt-in section"
    )
    # The table with the differential timeout contract
    assert "| `False` (default) | 600s | NO | NO | NO |" in body, (
        "render_image=False row (default) must pin 600s + no PNG + no marker"
    )
    assert "| `True` | 1200s | YES | YES |" in body, (
        "render_image=True row must pin 1200s + render + persist + marker"
    )


def test_chart_tools_timeout_constants_match_d5_spec():
    """d5_timeout (user addendum 2026-10-05): differential timeout wiring."""
    body = DAEMON_CHART_TOOLS.read_text()
    # Module-level constants
    assert re.search(r"_RENDER_TIMEOUT_S\s*=\s*1200\.0", body), (
        "chart_tools.py must pin _RENDER_TIMEOUT_S = 1200.0 (render_image=True)"
    )
    assert re.search(r"_DEFAULT_TIMEOUT_S\s*=\s*600\.0", body), (
        "chart_tools.py must pin _DEFAULT_TIMEOUT_S = 600.0 (render_image=False default)"
    )
    # Dynamic selection in the tool body
    assert re.search(
        r"timeout_s\s*=\s*_RENDER_TIMEOUT_S\s+if\s+render_image\s+else\s+_DEFAULT_TIMEOUT_S", body
    ), (
        "generate_chart must select the timeout dynamically from render_image"
    )
    # Reuse-path default also respects the constant (no hardcoded 600.0)
    assert "timeout: float = _DEFAULT_TIMEOUT_S" in body, (
        "_reuse_charter's default timeout must use the module constant"
    )
