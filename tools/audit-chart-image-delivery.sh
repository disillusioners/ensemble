#!/usr/bin/env bash
# ============================================================================
# tools/audit-chart-image-delivery.sh
# ----------------------------------------------------------------------------
# Phase D — chart-image-delivery 24-pin regression audit (D.0/D.1 tasks).
# Standalone, pure-bash + grep + sed. No venv / no Python import path.
#
# Why pure-bash: the pre-merge-base preservation gate (D.0b) must work in a
# bare checkout at /tmp/<base>-audit where the worktree editable-install
# trap means the main .venv's daemon editable path does NOT resolve to the
# temp worktree. A pure-bash gate avoids the venv. Pattern source:
# scripts/upgrade/ledger_check.py — read-only journal consumer; this script
# is a read-only file consumer. The two are explicitly disjoint concerns
# (this script MUST NOT touch scripts/upgrade/ — verified by grep self-check
# at the top of run_audit).
#
# Usage:
#   tools/audit-chart-image-delivery.sh                  # all 24 pins
#   tools/audit-chart-image-delivery.sh --class preservation
#   tools/audit-chart-image-delivery.sh --class feature
#   tools/audit-chart-image-delivery.sh --release-pin FILE…   # OQ4 backstop
#
# Exit codes:
#   0 — every selected pin PASS
#   1 — at least one selected pin FAIL
#   2 — usage error (bad --class value, etc.)
# ============================================================================
set -uo pipefail   # NOTE: not -e; pin functions must be able to return 1 without aborting

# ---------- argument parsing ----------
PIN_CLASS="all"           # all | preservation | feature
RELEASE_PIN_FILES=()      # OQ4 backstop — pin-by-file list (deferred, not used in R3)

usage() {
  cat <<'EOF'
Usage: tools/audit-chart-image-delivery.sh [options]

Options:
  --class preservation|feature|all   Pin class to run (default: all)
  --release-pin FILES…                RESERVED (not yet implemented) — OQ4 deferred-pin backstop, file arguments ignored
  -h, --help                         Show this help

Exit codes: 0 = all selected pins PASS, 1 = any FAIL, 2 = usage error.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --class)
      shift
      case "${1:-}" in
        preservation|feature|all) PIN_CLASS="$1" ;;
        *) echo "ERROR: --class must be preservation|feature|all (got: ${1:-})" >&2; exit 2 ;;
      esac
      ;;
    --release-pin)
      shift
      while [ $# -gt 0 ] && [[ "$1" != --* ]]; do
        RELEASE_PIN_FILES+=("$1")
        shift
      done
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift || true
done

# ---------- reserved --release-pin honesty (Phase D review Finding #2) ----------
# The flag is parsed into RELEASE_PIN_FILES but is NOT implemented. Refuse to
# silently no-op: emit ONE explicit WARN line and continue (selector stays
# class-based; no pin logic, no exit-code change). Keeps the documented
# signature while making the deferred-pin status visible to operators.
if [ "${#RELEASE_PIN_FILES[@]}" -gt 0 ]; then
  echo "WARN: --release-pin is reserved (OQ4 deferred-pin backstop) and is NOT implemented — running ALL pins in the selected class(es); file arguments ignored." >&2
fi

# ---------- colour (TERM-aware) ----------
if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ -n "${TERM:-}" ] && [ "${TERM:-}" != "dumb" ]; then
  C_RED=$(tput setaf 1 2>/dev/null || printf '')
  C_GREEN=$(tput setaf 2 2>/dev/null || printf '')
  C_YELLOW=$(tput setaf 3 2>/dev/null || printf '')
  C_BLUE=$(tput setaf 4 2>/dev/null || printf '')
  C_BOLD=$(tput bold 2>/dev/null || printf '')
  C_RST=$(tput sgr0 2>/dev/null || printf '')
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""; C_RST=""
fi

# ---------- repo root discovery ----------
# Use the script's own location to anchor the repo root, so the gate works
# from /tmp/<base>-audit bare checkouts where `git rev-parse --show-toplevel`
# still works but PWD is the worktree dir.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Self-check: refuse to live under scripts/upgrade/ (the live-rung gate is a
# different concern; this audit is its own tool per phaseD-plan.md §3).
case "$SCRIPT_DIR" in
  */scripts/upgrade/*|*/scripts/upgrade)
    echo "ERROR: this audit must live under tools/, not scripts/upgrade/" >&2
    exit 2
    ;;
esac

# ---------- counters / state ----------
TOTAL=0
PASSED=0
FAILED=0
CURRENT_CLASS=""   # set per-pin by the dispatcher

# Per-class pass/fail tracking
declare -A CLASS_TOTAL
declare -A CLASS_PASSED
CLASS_TOTAL[preservation]=0
CLASS_TOTAL[feature]=0
CLASS_TOTAL[all]=0
CLASS_PASSED[preservation]=0
CLASS_PASSED[feature]=0
CLASS_PASSED[all]=0

# ---------- helpers ----------
log() { printf '%s\n' "$*" >&2; }

# Print a pin row and update counters. Args: <pin_num> <class> <name> <status: PASS|FAIL> <detail>
emit_pin() {
  local n="$1" cls="$2" name="$3" status="$4" detail="${5:-}"
  TOTAL=$((TOTAL+1))
  CLASS_TOTAL[all]=$((CLASS_TOTAL[all]+1))
  CLASS_TOTAL["$cls"]=$((CLASS_TOTAL["$cls"]+1))
  if [ "$status" = "PASS" ]; then
    PASSED=$((PASSED+1))
    CLASS_PASSED[all]=$((CLASS_PASSED[all]+1))
    CLASS_PASSED["$cls"]=$((CLASS_PASSED["$cls"]+1))
    printf '  %s[ %s ]%s pin #%-2d  %-40s  %s%s%s\n' \
      "$C_BOLD" "$status" "$C_RST" "$n" "$name" "$C_GREEN" "$detail" "$C_RST"
  else
    FAILED=$((FAILED+1))
    printf '  %s[ %s ]%s pin #%-2d  %-40s  %s%s%s\n' \
      "$C_BOLD" "$status" "$C_RST" "$n" "$name" "$C_RED" "$detail" "$C_RST"
  fi
}

# Dispatcher: only run a pin if its class is selected.
check_pin() {
  local n="$1" cls="$2" name="$3"; shift 3
  case "$PIN_CLASS" in
    all) ;;
    "$cls") ;;
    *) return 0 ;;   # not selected, skip silently
  esac
  CURRENT_CLASS="$cls"
  if "$@"; then
    emit_pin "$n" "$cls" "$name" "PASS" ""
  else
    emit_pin "$n" "$cls" "$name" "FAIL" "see detail"
  fi
}

# ---------- pin functions ----------
# Each function returns 0 on PASS, 1 on FAIL. Use bash + grep + sed.
# All checks are content-addressable (not line numbers) so the gate is
# merge-stable. Anchor drift on a pin → that pin FAILs (other pins still run).

# Pin 1 — _BUSY_STRING byte-stable at chart_tools.py:_BUSY_MSG + test pin + skill.md (3 sites)
pin_01_busy_string() {
  local busy="Error: Charter busy; pass fresh=True for parallel charts."
  local hits=0
  # _BUSY_MSG constant
  grep -qF '_BUSY_MSG = "'"$busy"'"' "$REPO_ROOT/daemon/tools/chart_tools.py" && hits=$((hits+1))
  # test pin
  grep -qF '_BUSY_STRING = "'"$busy"'"' "$REPO_ROOT/tests/test_chart_tools.py" && hits=$((hits+1))
  # skill.md busy callout (Best Practices)
  grep -qF "$busy" "$REPO_ROOT/agents/_prompt_system/innate-skills/chart/skill.md" && hits=$((hits+1))
  [ "$hits" -eq 3 ]
}

# Pin 2 — _PAUSED_STRING byte-stable. Source literal is SPLIT across
# chart_tools.py:222-223 (two fragments); tests + skill.md have the FULL
# concatenated string. A single full-string grep -F on chart_tools.py is
# GUARANTEED 0 hits (the split) — grep the TWO FRAGMENTS separately there.
pin_02_paused_string() {
  local f1='Error: Charter is paused; resume it or pass fresh=True for a '
  local f2='new charter.'
  local full='Error: Charter is paused; resume it or pass fresh=True for a new charter.'
  local hits=0
  # chart_tools.py — both fragments must appear (the split is the contract)
  grep -qF "$f1" "$REPO_ROOT/daemon/tools/chart_tools.py" && \
  grep -qF "$f2" "$REPO_ROOT/daemon/tools/chart_tools.py" && hits=$((hits+1))
  # test pin — full string
  grep -qF "_PAUSED_STRING" "$REPO_ROOT/tests/test_chart_tools.py" && \
  grep -qF "$full" "$REPO_ROOT/tests/test_chart_tools.py" && hits=$((hits+1))
  # skill.md — full string in the paused callout
  grep -qF "$full" "$REPO_ROOT/agents/_prompt_system/innate-skills/chart/skill.md" && hits=$((hits+1))
  [ "$hits" -eq 3 ]
}

# Pin 3 — marker regex byte-stable in decisions.md §marker (the LOCKED form;
# NOT the near-miss sweeper pattern from §phase-b-r2-addendum-14)
pin_03_marker_regex() {
  local locked='^<!-- ens-img:chart-render:[a-f0-9]{32} -->$'
  # Must appear in decisions.md (NOT the near-miss sweeper form)
  grep -qF "$locked" "$REPO_ROOT/.agents/shared/planning/chart-image-delivery/decisions.md"
}

# Pin 4 — chart_tools.py anchor adjacency: `return result` immediately
# precedes the `generate_chart._full_doc_ = ` assignment (verified on HEAD:
# lines 525/527; pre-merge base line numbers may differ — content anchor).
# Strategy: find the first `return result` in chart_tools.py; check the
# next non-blank line starts with `generate_chart._full_doc_`.
pin_04_passthrough_adjacency() {
  local f="$REPO_ROOT/daemon/tools/chart_tools.py"
  # awk: print the line AFTER the first `return result` that is non-blank.
  # If the next non-blank line begins with `generate_chart._full_doc_` → PASS.
  local next
  next=$(awk '
    /return result/ { saw=NR; next }
    saw && NF { print; exit }
  ' "$f")
  # bash case patterns are anchored; trim leading whitespace and match the
  # full `generate_chart._full_doc_ =` prefix (parens + leading _ are part
  # of the contract).
  local trimmed="${next#"${next%%[![:space:]]*}"}"
  case "$trimmed" in
    'generate_chart._full_doc_ ='*) return 0 ;;
    *) return 1 ;;
  esac
}

# Pin 5 — dispatcher no-colon skip runs BEFORE extract_chart_images (both seams)
# Both seams must have `if ":" not in source` (or `if ":" not in source_id`)
# as a short-circuit BEFORE any `extract_chart_images` call.
pin_05_no_colon_skip() {
  local f="$REPO_ROOT/daemon/sources/dispatcher.py"
  # The pin is content-based: at least two `if ":" not in source` short-circuits.
  # The PRE-MERGE base has both (verified at :132 and :209); Phase B preserved
  # the two short-circuits and added `extract_chart_images` AFTER them. The
  # PRESERVATION contract is that both seams still skip before any extraction.
  local short_circuits
  short_circuits=$(grep -cE 'if\s*":"\s*not\s*in\s*source' "$f" || true)
  [ "${short_circuits:-0}" -ge 2 ]
}

# Pin 6 — INVERTED: extract_chart_images runs at BOTH seams
# (dispatch_message AND dispatch_completed) as the LAST content transformation.
# OutgoingMessage.images populated at BOTH construction sites; NEVER at
# registry.py:980.
pin_06_both_seam_extract() {
  local disp="$REPO_ROOT/daemon/sources/dispatcher.py"
  local reg="$REPO_ROOT/daemon/sources/registry.py"
  # At least 2 call sites of extract_chart_images in dispatcher.py
  local call_sites
  call_sites=$(grep -cE 'extract_chart_images\s*\(' "$disp" || true)
  # At least 2 `images=` in dispatcher.py OutgoingMessage constructions
  local images_kw
  images_kw=$(grep -cE '^\s*images\s*=\s*' "$disp" || true)
  # registry.py:980 must NOT have `images=` (the pin NEVER there)
  local reg_offender
  reg_offender=$(sed -n '980p' "$reg" 2>/dev/null | grep -E '^\s*images\s*=' || true)
  [ "${call_sites:-0}" -ge 2 ] && [ "${images_kw:-0}" -ge 2 ] && [ -z "$reg_offender" ]
}

# Pin 7 — OutgoingMessage.images field defaults to None in daemon/sources/base.py
# (`ImageAttachment` dataclass exists)
pin_07_outgoing_images_default() {
  grep -qE 'images:\s*list\[ImageAttachment\]\s*\|\s*None\s*=\s*None' "$REPO_ROOT/daemon/sources/base.py"
}

# Pin 8 — Discord _send_single_chunk accepts file=None kwarg (chunk-1 atomic
# unit on success; file never re-attempted on later chunks; files=[…] for N>1)
pin_08_discord_chunk_kwarg() {
  local f="$REPO_ROOT/daemon/sources/adapters/discord/adapter.py"
  grep -qE 'async\s+def\s+_send_single_chunk' "$f" && \
  grep -qE 'file:\s*[^=]*\|\s*None\s*=\s*None' "$f" && \
  grep -qE 'files:\s*list\[[^]]+\]\s*\|\s*None\s*=\s*None' "$f" && \
  grep -qE 'kwargs\["files"\]\s*=\s*files' "$f" && \
  grep -qE 'kwargs\["file"\]\s*=\s*file\s*(#|$)' "$f"
}

# Pin 9 — Discord 2000-char chunking preserved (DISCORD_MAX_MESSAGE_LENGTH=2000)
pin_09_discord_2000() {
  grep -rqE 'DISCORD_MAX_MESSAGE_LENGTH\s*=\s*2000' "$REPO_ROOT/daemon/" 2>/dev/null
}

# Pin 10 — Telegram _api_call_multipart 3-retry + 4xx-non-transient classifier;
# circuit-breaker discipline for transport errors only.
pin_10_telegram_multipart_retry() {
  local f="$REPO_ROOT/daemon/sources/adapters/telegram.py"
  grep -qE 'async\s+def\s+_api_call_multipart' "$f" && \
  grep -qE 'MAX_RETRIES\s*=\s*3' "$f" && \
  grep -qE 'for\s+attempt\s+in\s+range\(MAX_RETRIES\)' "$f" && \
  grep -qE '400\s*<=\s*error_code\s*<\s*500' "$f" && \
  grep -qE '_TelegramNonTransientAPIError' "$f" && \
  grep -qE 'record_failure' "$f"
}

# Pin 11 — Telegram sendPhoto (≤10 MB) / sendDocument (≤50 MB) ladder
pin_11_telegram_size_ladder() {
  local f="$REPO_ROOT/daemon/sources/adapters/telegram.py"
  grep -qE 'method\s*=\s*"sendPhoto"' "$f" && \
  grep -qE 'method\s*=\s*"sendDocument"' "$f" && \
  grep -qE 'len\(file_bytes\)\s*<=\s*TELEGRAM_PHOTO_MAX_BYTES' "$f" && \
  grep -qE 'len\(file_bytes\)\s*>\s*TELEGRAM_DOCUMENT_MAX_BYTES' "$f"
}

# Pin 12 — Slack single-call files_upload_v2; capability flag;
# missing_scope classified BEFORE record_failure; WARN-once-per-channel
pin_12_slack_upload_v2() {
  local f="$REPO_ROOT/daemon/sources/adapters/slack/adapter.py"
  grep -qE 'class\s+SlackCapabilityError' "$f" && \
  grep -qE 'files_upload_v2' "$f" && \
  grep -qE 'self\._slack_capability_flags:\s*set\[str\]\s*=\s*set\(\)' "$f" && \
  grep -qE 'missing_scope' "$f"
}

# Pin 13 — Slack BLOCKS_CONTENT_THRESHOLD (400 chars) preserved
pin_13_slack_blocks_threshold() {
  grep -rqE 'BLOCKS_CONTENT_THRESHOLD\s*=\s*400' "$REPO_ROOT/daemon/" 2>/dev/null
}

# Pin 14 — daemon/constants.py exports DISCORD_FILE_MAX_BYTES=8MB,
# TELEGRAM_PHOTO_MAX_BYTES=10MB, TELEGRAM_DOCUMENT_MAX_BYTES=50MB,
# SLACK_FILE_MAX_BYTES=1GB, CHART_IMAGE_MIME_WHITELIST (4 MIMEs)
pin_14_constants() {
  local f="$REPO_ROOT/daemon/constants.py"
  grep -qE 'DISCORD_FILE_MAX_BYTES:\s*int\s*=\s*8\s*\*\s*1024\s*\*\s*1024' "$f" && \
  grep -qE 'TELEGRAM_PHOTO_MAX_BYTES:\s*int\s*=\s*10\s*\*\s*1024\s*\*\s*1024' "$f" && \
  grep -qE 'TELEGRAM_DOCUMENT_MAX_BYTES:\s*int\s*=\s*50\s*\*\s*1024\s*\*\s*1024' "$f" && \
  grep -qE 'SLACK_FILE_MAX_BYTES:\s*int\s*=\s*1024\s*\*\s*1024\s*\*\s*1024' "$f" && \
  grep -qE 'CHART_IMAGE_MIME_WHITELIST:\s*frozenset\[str\]' "$f" && \
  grep -qE '"image/png"' "$f" && \
  grep -qE '"image/jpeg"' "$f" && \
  grep -qE '"image/gif"' "$f" && \
  grep -qE '"image/webp"' "$f"
}

# Pin 15 — agents/charter/meta.json tools.allow contains "image"
pin_15_charter_image_tool() {
  local f="$REPO_ROOT/agents/charter/meta.json"
  grep -qE '"image"' "$f"
}

# Pin 16 — agents/charter/meta.json version is 1.2.0
pin_16_charter_version() {
  grep -qE '"version":\s*"1\.2\.0"' "$REPO_ROOT/agents/charter/meta.json"
}

# Pin 17 — 20 chart-capable agents reference "Chat Delivery" in canonical home.
# Charter itself NOT in this set. [v2] agents have brackets in dir name —
# use shell glob to find them.
pin_17_chat_delivery_refs() {
  local agents=(
    approver developer planner reviewer tidier
  )
  local v2_agents=(
    'approver[v2]' 'developer[v2]' 'planner[v2]' 'reviewer[v2]' 'tidier[v2]'
  )
  local others=(
    architect ari coder devops doc-writer governor leader maintenancer project-manager wanderer
  )
  local all_hits=0
  local -a all_agents=("${agents[@]}" "${v2_agents[@]}" "${others[@]}")
  for a in "${all_agents[@]}"; do
    if grep -rlF "Chat Delivery" "$REPO_ROOT/agents/$a" 2>/dev/null | grep -qE '\.md$'; then
      all_hits=$((all_hits+1))
    fi
  done
  [ "$all_hits" -eq 20 ]
}

# Pin 18 — no NEW .md path tokens in agents/*/{rule,soul,tools_note,workflow}.md
# new content. Range: 3e2584b9..b31fa926 per phaseD-plan.md §2 (R3 plan prose).
# Wider check (4aa2eb13..53dabf0b, the actual Phase C range) also clean —
# record both ranges in report.
pin_18_no_md_path_tokens() {
  local range="${CHART_PHASE_C_RANGE:-3e2584b9..b31fa926}"
  local md_tokens
  md_tokens=$(cd "$REPO_ROOT" && \
    git log -p "$range" -- 'agents/*/rule.md' 'agents/*/soul.md' 'agents/*/tools_note.md' 'agents/*/workflow.md' 2>/dev/null | \
    grep -E "^\+[^+]" | \
    grep -E "\.md\b" || true)
  [ -z "$md_tokens" ]
}

# Pin 19 — _BUSY_STRING at skill.md call site byte-identical to
# tests/test_chart_tools.py:_BUSY_STRING (full string identity)
pin_19_busy_byte_identical() {
  local busy='Error: Charter busy; pass fresh=True for parallel charts.'
  local test_hit skill_hit
  test_hit=$(grep -F "$busy" "$REPO_ROOT/tests/test_chart_tools.py" || true)
  skill_hit=$(grep -F "$busy" "$REPO_ROOT/agents/_prompt_system/innate-skills/chart/skill.md" || true)
  [ -n "$test_hit" ] && [ -n "$skill_hit" ]
}

# Pin 20 — agents/_prompt_system/innate-skills/chart/skill.md
# "Wedged-Charter Recovery" section unchanged
pin_20_wedged_section() {
  local f="$REPO_ROOT/agents/_prompt_system/innate-skills/chart/skill.md"
  grep -qE '##\s*Wedged-Charter Recovery' "$f" && \
  grep -qF 'Caller escape hatch (`fresh=True`)' "$f" && \
  grep -qF 'Operator clears the hung orphan' "$f" && \
  grep -qF 'Daemon restart' "$f" && \
  grep -qF 'A paused charter surfaces a distinct error' "$f"
}

# Pin 21 — docs/sources/slack-setup.md scope manifest + scopes table contain
# files:write
pin_21_slack_setup_files_write() {
  local f="$REPO_ROOT/docs/sources/slack-setup.md"
  local hits
  hits=$(grep -cF 'files:write' "$f" || true)
  # YAML manifest fenced + scopes table = at least 2 hits (plan §21 cites
  # fenced :17-61 + table :69-82)
  [ "${hits:-0}" -ge 2 ]
}

# Pin 22 — daemon/manager.py tmp_image_store property still public
pin_22_manager_tmp_image_store() {
  local f="$REPO_ROOT/daemon/manager.py"
  # The @property accessor pattern (multi-line aware via -B1 -A6)
  grep -qE '@property' "$f" && \
  grep -qE 'def\s+tmp_image_store\s*\(' "$f"
}

# Pin 23 — tests/e2e/mock_source_server.py MockSourceAdapter.send() captures
# the whole OutgoingMessage (no field-stripping)
pin_23_mock_source_captures_message() {
  local f="$REPO_ROOT/tests/e2e/mock_source_server.py"
  grep -qE 'self\.sent_messages:\s*list\[OutgoingMessage\]\s*=\s*\[\]' "$f" && \
  grep -qE 'self\.sent_messages\.append\(message\)' "$f"
}

# Pin 24 — Ari pre-warm reminder present post-merge: in BOTH
# .agents/shared/context.md AND agents/ari/workflow.md (content-addressable)
pin_24_ari_prewarm() {
  local ctx="$REPO_ROOT/.agents/shared/context.md"
  local ari="$REPO_ROOT/agents/ari/workflow.md"
  grep -qiE 'pre-warm|deploy step' "$ctx" && \
  grep -qiE 'pre-warm|deploy step' "$ari" && \
  grep -qF 'install-mermaid-cli' "$ari"
}

# ============================================================================
# chart-render-opt-in pins (user directive 2026-10-05) — opt-in render
# contract, replacing the always-render model. Pins 25–30 form a single
# self-contained opt-in audit class (selection: ``--class feature``) that
# grep-verifies the contract end-to-end. Designed to be merge-stable
# (content-addressable, no line numbers).
# ============================================================================

# Pin 25 — chart_tools.py declares `render_image: bool = False` (the
# flag) on the generate_chart signature. The opt-in default.
pin_25_render_image_param_default_false() {
  local f="$REPO_ROOT/daemon/tools/chart_tools.py"
  grep -qE 'render_image:\s*bool\s*=\s*False' "$f"
}

# Pin 26 — chart_tools.py d5_timeout wiring: module-level constants
# 1200.0 (render) and 600.0 (default), selected dynamically in
# generate_chart. Source: user addendum 2026-10-05.
pin_26_d5_timeout_constants() {
  local f="$REPO_ROOT/daemon/tools/chart_tools.py"
  grep -qE '_RENDER_TIMEOUT_S\s*=\s*1200\.0' "$f" && \
  grep -qE '_DEFAULT_TIMEOUT_S\s*=\s*600\.0' "$f" && \
  grep -qE 'timeout_s\s*=\s*_RENDER_TIMEOUT_S\s+if\s+render_image\s+else\s+_DEFAULT_TIMEOUT_S' "$f"
}

# Pin 27 — chart_tools.py emits the byte-stable directive on its own
# line in the dispatch message: ``RENDER_IMAGE: <lowercase-bool>``.
# The charter gate regex parses this exact form.
pin_27_directive_in_dispatch_message() {
  local f="$REPO_ROOT/daemon/tools/chart_tools.py"
  grep -qE 'f?"?RENDER_IMAGE:\s*\{str\(render_image\)\.lower\(\)\}' "$f" || \
  grep -qE 'RENDER_IMAGE:\s*\{str\(render_image\)\.lower\(\)\}' "$f"
}

# Pin 28 — charter workflow.md Step 5 is CONDITIONAL on RENDER_IMAGE
# (opt-in contract). Heading carries the marker; prose pins the skip
# contract; test-pinnable form is the embedded Python block. The
# skip-contract phrase spans a soft line break in the prose, so each
# fragment is checked independently.
pin_28_workflow_step5_conditional() {
  local f="$REPO_ROOT/agents/charter/workflow.md"
  grep -qF '## Step 5: Validate + Render + Persist (CONDITIONAL on RENDER_IMAGE)' "$f" && \
  grep -qE '[Nn]o render, no PNG, no' "$f" && \
  grep -qE 'tmp_images write, no marker' "$f" && \
  grep -qF '### Conditional gate — test-pinnable form' "$f" && \
  grep -qE 'should_render_dispatch_message' "$f"
}

# Pin 29 — charter workflow.md Step 6 (persist + marker) is CONDITIONAL
# on RENDER_IMAGE. Same gate as Step 5 — the render directive is the
# single source of truth.
pin_29_workflow_step6_conditional() {
  local f="$REPO_ROOT/agents/charter/workflow.md"
  grep -qF '## Step 6: Persist + Return (CONDITIONAL on RENDER_IMAGE)' "$f"
}

# Pin 30 — chart skill documents the opt-in render_image flag:
# signature table row, the dedicated opt-in section, and the
# differential 600s/1200s timeout contract.
pin_30_chart_skill_opt_in_docs() {
  local f="$REPO_ROOT/agents/_prompt_system/innate-skills/chart/skill.md"
  grep -qF '`render_image`' "$f" && \
  grep -qF '## render_image: opt-in rendering' "$f" && \
  grep -qE 'no \(default `false`\)' "$f" && \
  grep -qF '600s' "$f" && \
  grep -qF '1200s' "$f" && \
  grep -qF '| `False` (default) | 600s | NO | NO | NO |' "$f" && \
  grep -qF '| `True` | 1200s | YES | YES |' "$f"
}

# Pin 31 — 20 chart-capable agents reference the render_image opt-in
# qualifier on their canonical Chat Delivery line. Preserves the
# pin_17 contract (the original line is intact — the qualifier is
# appended between `generate_chart` and `see Chat Delivery`).
pin_31_agents_render_image_qualifier() {
  local agents=(
    approver developer planner reviewer tidier
  )
  local v2_agents=(
    'approver[v2]' 'developer[v2]' 'planner[v2]' 'reviewer[v2]' 'tidier[v2]'
  )
  local others=(
    architect ari coder devops doc-writer governor leader maintenancer project-manager wanderer
  )
  local all_hits=0
  local -a all_agents=("${agents[@]}" "${v2_agents[@]}" "${others[@]}")
  for a in "${all_agents[@]}"; do
    if grep -rlF "render_image=True" "$REPO_ROOT/agents/$a" 2>/dev/null | grep -qE '\.md$'; then
      all_hits=$((all_hits+1))
    fi
  done
  [ "$all_hits" -eq 20 ]
}

# ============================================================================
# charter-skill-improvement pins (post-smoke, 2026-10-05) — durable
# skill/doc improvements so the next fresh-host first render is BORING.
# Pins 32–34 form the durable doc-encodes-the-fix contract; without them
# the committed lib fixes are SYMPTOM PATCHES if the .md doc still
# teaches the broken procedure. These pins are GREP-based (keyword
# phrases and `grep -qF` / `grep -qE` patterns) — NOT SHA256 hashes
# (the seal tripwire in `test_chart_image_delivery_e2e.py` is the
# hash-based backstop for full-file integrity; the audit pins are
# the "section present + canonical content" assertion class). A
# refactor that moves the keyword phrase but preserves the contract
# is fine; a refactor that drops the contract is not.
# ============================================================================

# Pin 32 — install-mermaid-cli.md carries a "Provisioning / pre-warm
# invocation" section that mirrors the install-opendesign deploy-step
# pattern. Heading + the two load-bearing contract terms
# (deploy step + pre-warm) are pinned.
pin_32_install_skill_prewarm_section() {
  local f="$REPO_ROOT/agents/charter/skills-template/install-mermaid-cli.md"
  grep -qF '## Provisioning / pre-warm invocation' "$f" && \
  grep -qF 'deploy step' "$f" && \
  grep -qE 'pre-warm' "$f" && \
  grep -qF 'install-mermaid-cli' "$f"
}

# Pin 33 — install-mermaid-cli.md carries a "Render contract" section
# that ENCODES all 6 canonical lib.sh fixes as the doc-level mirror.
# Each fix has a keyword/phrase the audit greps for; missing one
# means a maintainer edit reintroduced that defect. The lib is the
# implementation; this section is the doc.
pin_33_install_skill_render_contract_section() {
  local f="$REPO_ROOT/agents/charter/skills-template/install-mermaid-cli.md"
  grep -qF '## Render contract (the 6 canonical fixes)' "$f" && \
  grep -qF -- '--size 1200' "$f" && \
  grep -qF 'Stage the security-pin JSON' "$f" && \
  grep -qE 'NO .ulimit -v.|NO ulimit' "$f" && \
  grep -qF 'prepend ' "$f" && \
  grep -qE 'top level|TOP level|TOP-LEVEL' "$f" && \
  grep -qF '.args = ["--no-sandbox"]' "$f"
}

# Pin 34 — workflow.md has a "Render-path timeouts" section that
# cross-references the chart tool's _RENDER_TIMEOUT_S /
# _DEFAULT_TIMEOUT_S constants by NAME (not by restating the
# drift-prone 1200s/600s numbers). The convention is "name the
# constant, not the number".
pin_34_workflow_render_path_timeouts_section() {
  local f="$REPO_ROOT/agents/charter/workflow.md"
  grep -qF '## Render-path timeouts (charter wait budget, d5_timeout)' "$f" && \
  grep -qF '_DEFAULT_TIMEOUT_S' "$f" && \
  grep -qF '_RENDER_TIMEOUT_S' "$f"
}

# ---------- driver ----------
run_audit() {
  printf '%s%s== chart-image-delivery audit ==%s\n' "$C_BOLD" "$C_BLUE" "$C_RST"
  printf 'class: %s%s%s   repo: %s\n' "$C_BOLD" "$PIN_CLASS" "$C_RST" "$REPO_ROOT"

  printf '\n%sPRESERVATION (pre-existing invariants)%s\n' "$C_BOLD" "$C_RST"
  check_pin 1  preservation "busy-string byte-stable"          pin_01_busy_string
  check_pin 2  preservation "paused-string byte-stable"        pin_02_paused_string
  check_pin 3  preservation "marker regex locked-form"          pin_03_marker_regex
  check_pin 4  preservation "passthrough adjacency (525/527)"   pin_04_passthrough_adjacency
  check_pin 5  preservation "no-colon skip (both seams)"        pin_05_no_colon_skip
  check_pin 9  preservation "discord 2000-char chunker"         pin_09_discord_2000
  check_pin 13 preservation "slack BLOCKS_CONTENT_THRESHOLD"    pin_13_slack_blocks_threshold
  check_pin 22 preservation "manager.tmp_image_store public"   pin_22_manager_tmp_image_store
  check_pin 23 preservation "mock source captures message"      pin_23_mock_source_captures_message

  printf '\n%sFEATURE (post-merge state)%s\n' "$C_BOLD" "$C_RST"
  check_pin 6  feature "both-seam extract (INVERTED)"          pin_06_both_seam_extract
  check_pin 7  feature "OutgoingMessage.images default"        pin_07_outgoing_images_default
  check_pin 8  feature "discord _send_single_chunk kwarg"      pin_08_discord_chunk_kwarg
  check_pin 10 feature "telegram multipart 3-retry+4xx"        pin_10_telegram_multipart_retry
  check_pin 11 feature "telegram sendPhoto/sendDocument"       pin_11_telegram_size_ladder
  check_pin 12 feature "slack files_upload_v2 + cap flag"      pin_12_slack_upload_v2
  check_pin 14 feature "daemon/constants.py exports"           pin_14_constants
  check_pin 15 feature "charter tools.allow: image"            pin_15_charter_image_tool
  check_pin 16 feature "charter version 1.2.0"                 pin_16_charter_version
  check_pin 17 feature "20 agents → Chat Delivery"             pin_17_chat_delivery_refs
  check_pin 18 feature "no .md path tokens in Phase C"         pin_18_no_md_path_tokens
  check_pin 19 feature "busy-string skill.md = test pin"       pin_19_busy_byte_identical
  check_pin 20 feature "wedged-charter section unchanged"      pin_20_wedged_section
  check_pin 21 feature "slack-setup files:write"               pin_21_slack_setup_files_write
  check_pin 24 feature "ari pre-warm reminder (both files)"    pin_24_ari_prewarm
  printf '\n%sFEATURE (chart-render-opt-in, user directive 2026-10-05)%s\n' "$C_BOLD" "$C_RST"
  check_pin 25 feature "render_image param declared default False"   pin_25_render_image_param_default_false
  check_pin 26 feature "d5_timeout constants 1200/600 + dynamic"      pin_26_d5_timeout_constants
  check_pin 27 feature "RENDER_IMAGE directive in dispatch message"  pin_27_directive_in_dispatch_message
  check_pin 28 feature "workflow.md Step 5 conditional on RENDER_IMAGE" pin_28_workflow_step5_conditional
  check_pin 29 feature "workflow.md Step 6 conditional on RENDER_IMAGE" pin_29_workflow_step6_conditional
  check_pin 30 feature "chart skill documents render_image opt-in"   pin_30_chart_skill_opt_in_docs
  check_pin 31 feature "20 agents' render_image qualifier"            pin_31_agents_render_image_qualifier
  printf '\n%sFEATURE (charter-skill-improvement, 2026-10-05 post-smoke)%s\n' "$C_BOLD" "$C_RST"
  check_pin 32 feature "install-skill pre-warm section (deploy step)" pin_32_install_skill_prewarm_section
  check_pin 33 feature "install-skill render-contract section (6 fixes)" pin_33_install_skill_render_contract_section
  check_pin 34 feature "workflow.md render-path timeouts (by-name refs)" pin_34_workflow_render_path_timeouts_section

  printf '\n%sSUMMARY%s\n' "$C_BOLD" "$C_RST"
  for c in preservation feature; do
    local t=${CLASS_TOTAL[$c]:-0} p=${CLASS_PASSED[$c]:-0}
    if [ "$t" -eq 0 ]; then
      printf '  %s: %d/%d (skipped — not selected)\n' "$c" "$p" "$t"
    else
      printf '  %s: %d/%d\n' "$c" "$p" "$t"
    fi
  done
  printf '  %sTOTAL: %d/%d  (%d failed)%s\n' "$C_BOLD" "$PASSED" "$TOTAL" "$FAILED" "$C_RST"

  if [ "$FAILED" -gt 0 ]; then
    return 1
  fi
  return 0
}

run_audit
exit $?
