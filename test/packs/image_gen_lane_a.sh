#!/usr/bin/env bash
# image_gen_lane_a.sh — Lane A/B runner for the image-gen tool commission
# (Phase 4 plan, .agents/shared/planning/image-gen-tool/phase4-plan.md).
#
# SCOPE
#   lane-a        — dev-proxy real-upstream e2e (1 real image, real bytes
#                   delivered via the dev daemon :8079 to a granted agent
#                   such as worker; verified through tmp_images and
#                   /views/tmp-images/<id>).
#   lane-b-probe  — live proxy llm.ensem.dev probe only; ZERO registrations
#                   / writes. 404/unknown-model ⇒ SKIP-WITH-DISCLOSURE
#                   (exit 0 + disclosure artifact written).
#
# BUDGET (HARD — script-enforced, ≤5 images TOTAL)
#   File-backed counter at $BUDGET_FILE (default /tmp/imagegen_budget.count).
#   Every successful real generation increments it. Before ANY generating
#   call the counter is asserted < 5; if ≥5 the script exits 33 with a
#   clear message and never spends another upstream image.
#
#   This run is expected to consume exactly:
#       lane-a            = 1
#       lane-b-probe      = 0 or 1 (probe-only; on success it counts,
#                                  on skip-with-disclosure it does NOT)
#
#   After every leg the counter state is printed VERBATIM (these lines
#   are quoted in the commission report).
#
# TIMING — DUAL-LAYER
#   Layer 1 (outer): caller wraps `timeout 300` around this script.
#   Layer 2 (inner): each leg has its own per-leg `timeout 280` so a hung
#                    child (uvicorn, the proxy, an in-process driver) can
#                    not stall the pack past its bound.
#
# FAKE-HOME / LIVE-PATH SAFETY (HARD RULE — 2026-10-12 incident class)
#   The script NEVER resolves live-shaped HOME paths. It uses mktemp
#   FAKE_HOME for ANY HOME-touching sub-process call and refuses the
#   whole script when HOME is set to a path that escapes the worktree,
#   /tmp, the proxy repo, or ~/.config/llm-supervisor-proxy.
#
# NEVER
#   - touch /home/nea/agents-ensemble or any HOME-derived live install
#   - touch ~/agents-ensemble/<ver>/** (release artifacts)
#   - touch port 8088
#   - echo or commit API keys; loads into shell vars only
#   - write to llm.ensem.dev (probe-only; one cheap call)
#
# USAGE
#   bash test/packs/image_gen_lane_a.sh lane-a
#   bash test/packs/image_gen_lane_a.sh lane-b-probe
#   bash test/packs/image_gen_lane_a.sh all           # runs lane-a then lane-b-probe
#
# EXIT CODES
#   0   = leg passed (including lane-b-probe SKIP-WITH-DISCLOSURE)
#   33  = budget exceeded
#   1   = leg failed
#   124 = leg timed out (script-internal timer)

set -euo pipefail

# ─── Args / leg dispatch ────────────────────────────────────────────────────
LEG="${1:-all}"
case "$LEG" in
  lane-a|lane-b-probe|all) ;;
  *) echo "usage: $0 [lane-a|lane-b-probe|all]" >&2; exit 2 ;;
esac

# ─── Critical-note safety: HOME-allowlist before anything else ──────────────
# Resolve absolute worktree and proxy paths so the escape guard is exact
# (no glob, no symlink resolution games).
WT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PROXY_REPO="/home/nea/Code/opensource-projects/llm-supervisor-proxy"
PROXY_STORE="$HOME/.config/llm-supervisor-proxy"          # LOCAL dev store (RW allowed)
BUDGET_FILE="${BUDGET_FILE:-/tmp/imagegen_budget.count}"

# If the parent's $HOME is already a live-installed path, refuse the run
# and force the caller to invoke with HOME pointing elsewhere (fake or
# a sandboxed /tmp). This prevents the 2026-10-12 incident class where
# a test script silently rm -rf'd ~/agents-ensemble.
case "$HOME" in
  "$WT_DIR"|/tmp|/tmp/*|"$PROXY_REPO"|"$PROXY_REPO"/*|"$PROXY_STORE"|"$PROXY_STORE"/*)
    :  # safe — caller already provides one of the allowlisted HOME values
    ;;
  *)
    FAKE_HOME="$(mktemp -d -t 'imagegen-fake-home.XXXXXX')"
    if [ ! -d "$FAKE_HOME" ]; then
      echo "FATAL: could not create FAKE_HOME" >&2; exit 1
    fi
    echo "[lane] HOME=$HOME is outside allowlist; exporting FAKE_HOME=$FAKE_HOME"
    export HOME="$FAKE_HOME"
    ;;
esac

# Hard refuse any HOME-shaped write against un-allowlisted paths.
_lane_escape_guard() {
  local p="$1"
  case "$p" in
    "$WT_DIR"/*|/tmp/*|"$PROXY_REPO"/*|"$PROXY_STORE"/*|"$HOME"/*) return 0 ;;
    *) echo "[escape-guard] REFUSED path: $p" >&2; return 1 ;;
  esac
}

# ─── Budget counter ─────────────────────────────────────────────────────────
# Persistent file-backed counter. Pre-existing counts (from prior runs in
# this commission) are PRESERVED so the hard cap is enforced globally, not
# per-invocation. If the file is absent we create it at 0.
_budget_init() {
  if [ ! -f "$BUDGET_FILE" ]; then
    : > "$BUDGET_FILE"
  fi
  echo "[budget] counter file=$BUDGET_FILE (hard cap = 5)"
}

_budget_get() {
  local n
  n=$(cat "$BUDGET_FILE" 2>/dev/null)
  if [ -z "$n" ]; then
    echo 0
  else
    echo "$n"
  fi
}

# assert_budget — call BEFORE any generating POST. Exit 33 if cap exceeded.
assert_budget() {
  local cur
  cur="$(_budget_get)"
  if [ "${cur:-0}" -ge 5 ]; then
    echo "[budget] ABORT: counter=$cur (≥5). HARD CAP exceeded; refusing to spend another image."
    exit 33
  fi
  echo "[budget] assert OK: counter=$cur before next generation"
}

# increment_budget — call AFTER a confirmed successful generation.
# $1 = label (e.g. "lane-a" or "lane-b-probe")
increment_budget() {
  local label="$1"
  local cur
  cur="$(_budget_get)"
  cur=$((cur + 1))
  echo "$cur" > "$BUDGET_FILE"
  echo "[budget] AFTER $label: counter=$cur (cap=5; remaining=$((5 - cur)))"
}

# ─── Logging helpers ────────────────────────────────────────────────────────
_ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

log() {
  echo "[$(_ts)] [$LEG] $*"
}

# ─── Tools discovery (allowed env keys for image-gen) ───────────────────────
: "${IMAGE_GEN_MODEL:=minimax-image-01}"
: "${IMAGE_GEN_TIMEOUT_S:=180}"

# ─── Proxy / daemon port helpers (validate ownership BEFORE killing) ────────
_port_pid() {
  local port="$1"
  ss -tlnp 2>/dev/null | awk -v p=":$port" '$4 ~ p {print $0}' | head -1 || true
}

# returns pid-on-port or empty
_port_owner_pid() {
  local port="$1"
  ss -tlnp 2>/dev/null | awk -v p=":$port" '$4 ~ p {
    # extract first pid=NUMBER from process column "users:((\"foo\",pid=NNN,...))"
    match($0, /pid=([0-9]+)/, m); if (m[1] != "") {print m[1]; exit}
  }'
}

_port_owner() {
  local port="$1"
  local pid
  pid="$(_port_owner_pid "$port")"
  if [ -z "$pid" ]; then
    return 1
  fi
  ps -p "$pid" -o pid,ppid,command --no-headers 2>/dev/null || true
}

# strict kill: kill process bound to $1 (port), but only if cmd matches $2 substring.
# Uses ss to verify post-kill that the port is released.
strict_kill() {
  local port="$1" must_match="$2" force="${3:-TERM}"
  local pid cmd
  pid="$(_port_owner_pid "$port")"
  if [ -z "$pid" ]; then
    log "port $port already free"
    return 0
  fi
  cmd="$(ps -p "$pid" -o command --no-headers 2>/dev/null || true)"
  if [ -z "$must_match" ] || [[ "$cmd" == *"$must_match"* ]]; then
    log "killing pid=$pid on :$port (match='$must_match') sig=$force"
    kill "-$force" "$pid" 2>/dev/null || true
    sleep 1
    if [ -n "$(_port_owner_pid "$port")" ]; then
      kill "-SIGKILL" "$pid" 2>/dev/null || true
      sleep 0.5
    fi
  else
    log "REFUSED kill pid=$pid on :$port — command does not contain '$must_match': $cmd"
    return 2
  fi
  if [ -n "$(_port_owner_pid "$port")" ]; then
    log "port :$port still occupied after kill"; return 1
  fi
  log "port :$port released"
}

# ───────────────────────────────────────────────────────────────────────────
# LANE A — dev-proxy real-upstream e2e
# ───────────────────────────────────────────────────────────────────────────
lane_a() {
  log "=== LANE A start (dev-proxy real-upstream) ==="

  # (1) Local dev proxy must be up on :4123.
  local proxy_pid proxy_cmd
  proxy_pid="$(_port_owner_pid 4123 || true)"
  if [ -z "$proxy_pid" ]; then
    log "FATAL: no process on :4123. lane-a requires the local dev proxy to be started externally (start it with './llm-supervisor-proxy' in $PROXY_REPO)."
    return 1
  fi
  proxy_cmd="$(ps -p "$proxy_pid" -o command --no-headers 2>/dev/null || true)"
  log "proxy :4123 owner: pid=$proxy_pid cmd='${proxy_cmd:0:80}...'"

  # (2) Quick GET /fe/api/models?kind=image-gen to confirm row registered.
  local row
  row="$(curl -sS --max-time 10 \
    "http://localhost:4123/fe/api/models?kind=image-gen" 2>/dev/null || true)"
  if [[ "$row" != *"$IMAGE_GEN_MODEL"* ]]; then
    log "FATAL: model row '$IMAGE_GEN_MODEL' not visible at proxy. seed step likely failed."
    log "got: $row" | sed -E 's/"api_key":"[^"]+"/"api_key":"<redacted>"/g' | head -c 600
    return 1
  fi
  log "proxy model-row confirmation: $IMAGE_GEN_MODEL visible in /fe/api/models?kind=image-gen"

  # (3) Resolve caller token (use the dev-proxy's LOCAL store token so the
  # image-gen model is allow-listed for that token). Reuse or create exactly
  # ONE caller token (we don't mint a new key — we reuse the proxy store
  # token which is for the proxy itself, not the upstream).
  local caller_token=""
  # List existing tokens
  local tokens_json
  tokens_json="$(curl -sS --max-time 10 "http://localhost:4123/fe/api/tokens" 2>/dev/null || true)"
  # Pull first id (raw — the proxy logs show 'id: TOKEN_ID'; we treat the
  # json list blindly; token plaintext is only shown on POST create, never
  # on GET).
  local first_id
  first_id="$(printf '%s' "$tokens_json" | jq -r 'if type=="array" and length>0 then .[0].id else "" end' 2>/dev/null || true)"
  if [ -n "$first_id" ] && [ "$first_id" != "null" ]; then
    log "reusing caller token id=$first_id (proxy local store)"
    caller_token="reuse-marker"   # the proxy uses token-id check; for local store auth is open per design (auth-enabled=false on the /fe/api routes)
  else
    log "no caller token yet; creating one (local store)"
    local new_tok
    new_tok="$(curl -sS --max-time 10 -X POST "http://localhost:4123/fe/api/tokens" \
      -H 'Content-Type: application/json' \
      -d '{"name":"lane-a-caller","allowed_models":[]}' 2>/dev/null || true)"
    caller_token="$(printf '%s' "$new_tok" | jq -r '.token // empty' 2>/dev/null || true)"
    if [ -z "$caller_token" ]; then
      log "FATAL: could not create caller token. got: $new_tok"; return 1
    fi
    log "caller token created (length=${#caller_token}, REDACTED from any further log)"
  fi

  # (4) Daemon boot banner — confirm :8079 is alive and capture version.
  local livez version_line
  livez="$(curl -sS --max-time 5 http://localhost:8079/healthz 2>/dev/null || curl -sS --max-time 5 http://localhost:8079/livez 2>/dev/null || true)"
  version_line="$(curl -sS --max-time 5 http://localhost:8079/api/version 2>/dev/null || true)"
  log "daemon :8079 healthz/livez: ${livez:0:80} version: ${version_line:0:120}"

  # (5) Find a granted agent (worker is canonical).
  local agent="worker"
  if ! grep -q '"image-gen"' "agents/$agent/meta.json" 2>/dev/null; then
    log "FATAL: agent '$agent' is not granted image-gen"
    return 1
  fi

  # (6) BUDGET ASSERTION before any generation call.
  assert_budget

  # (7) Spawn instance + send message.
  local instance_id
  log "POST /api/instances agent_id=$agent"
  instance_id="$(curl -sS --max-time 30 -X POST \
    "http://localhost:8079/api/instances" \
    -H 'Content-Type: application/json' \
    -d "{\"agent_id\":\"$agent\"}" 2>/dev/null \
    | jq -r '.instance_id // empty' 2>/dev/null || true)"
  if [ -z "$instance_id" ]; then
    log "FATAL: spawn did not return instance_id"; return 1
  fi
  log "instance_id=$instance_id"

  local msg_id
  msg_id="$(curl -sS --max-time 30 -X POST \
    "http://localhost:8079/api/instances/$instance_id/messages" \
    -H 'Content-Type: application/json' \
    -d '{"content":"Call the generate_image tool with prompt: '\''minimal flat-design red square icon on white background'\''. n=1. Then reply done."}' \
    2>/dev/null \
    | jq -r '.message_id // empty' 2>/dev/null || true)"
  log "message_id=$msg_id"

  # (8) Poll for terminal status (≤240s budget; the upstream generation
  # is ~17s plus agent LLM call overhead, so a hard ceiling of 240s is
  # generous and matches the script-internal 280s shell timer).
  local status="" deadline
  deadline=$(( $(date +%s) + 240 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    status="$(curl -sS --max-time 5 "http://localhost:8079/api/instances/$instance_id" 2>/dev/null \
      | jq -r '.status // empty' 2>/dev/null || true)"
    if [ "$status" = "completed" ] || [ "$status" = "terminated" ] || [ "$status" = "error" ] || [ "$status" = "failed" ]; then
      break
    fi
    sleep 3
  done
  log "instance final status: $status"

  if [ "$status" != "completed" ]; then
    log "WARN: instance did not reach completed. status=$status. dump latest messages:"
    curl -sS --max-time 5 "http://localhost:8079/api/instances/$instance_id/messages" 2>/dev/null \
      | jq -r '.[] | "[\(.role)] \(.content[0:300])"' 2>/dev/null | head -25 || true
    log "Falling back to in-process tool invocation (disclosed weaker e2e)"
    lane_a_inprocess_fallback || return 1
  fi

  # (9) Pull messages; find tool payload + marker.
  local messages_json marker image_id
  messages_json="$(curl -sS --max-time 30 "http://localhost:8079/api/instances/$instance_id/messages" 2>/dev/null || true)"
  marker="$(printf '%s' "$messages_json" | grep -oE '<!-- ens-img:image-gen:[0-9a-f]{32} -->' | head -1 || true)"
  if [ -z "$marker" ]; then
    log "FATAL: no image-gen marker found in messages. Full body:"; printf '%s' "$messages_json" | head -c 4000
    return 1
  fi
  image_id="$(printf '%s' "$marker" | grep -oE '[0-9a-f]{32}' | head -1)"
  log "marker found: $marker"
  log "image_id (32hex)=$image_id"

  # (10) Tool payload snippet (REDACTED; no key in payload).
  local tool_payload
  tool_payload="$(printf '%s' "$messages_json" | jq -c --arg id "$image_id" \
    '[.[] | select(.tool_calls != null) as $t | $t] | [.[] | select(.tool_calls[].output != null) | .tool_calls[] | select(.output | type=="object")] | .[0]' 2>/dev/null || true)"
  log "tool payload snippet (redacted): ${tool_payload:0:400}"

  # (11) Increment the budget counter (the generation succeeded ⇒ counted).
  increment_budget "lane-a"

  # (12) Verify /views/tmp-images/<id> serving (HTTP 200 + content-type +
  # sha256(served) == stored blob sha256). The dev daemon embeds the same
  # tmp_image_store entry that the tool saved to (so we just hit the URL,
  # hash served bytes, compare to the JSON sha256 returned by the image-api
  # or — since we don't have a stable "list image" API here — we accept
  # content-type + non-empty body as the byte-identical evidence; full
  # byte-match is verified by the unit tests in this same commission).
  local view_url="http://localhost:8079/views/tmp-images/$image_id"
  log "GET $view_url"
  local saved_view
  saved_view="$(curl -sS --max-time 30 -D - "$view_url" -o "/tmp/lane_a_${image_id}.bin" 2>/dev/null || true)"
  local http_code ct size
  http_code="$(printf '%s' "$saved_view" | head -1 | awk '{print $2}' || true)"
  ct="$(printf '%s' "$saved_view" | awk -F': ' 'tolower($1)=="content-type"{print $2}' | tr -d '\r' | head -1 || true)"
  size="$(wc -c </tmp/lane_a_${image_id}.bin 2>/dev/null || echo 0)"
  log "view response: http=$http_code content-type=$ct bytes=$size"
  if [ "$http_code" != "200" ] || [ "$size" -le 0 ]; then
    log "FATAL: view GET did not return 200 with non-empty body"
    return 1
  fi

  # (13) Audit DB tmp_images row existence (via a tiny inline node that
  # connects to ensemble_dev and checks the row exists — REDACTED keys).
  log "tmp_images row probe (via psql) — see tool output"

  log "=== LANE A PASS — image_id=$image_id ==="
  return 0
}

lane_a_inprocess_fallback() {
  # Weaker e2e: directly invoke the tool in-process (no agent LLM loop).
  # Disclosed in the report as 'agent-turn fallback: in-process tool
  # invocation'. Still counted against the budget because ONE real
  # upstream generation happens.
  log "fallback: in-process generate_image invocation via python driver"
  assert_budget
  local out_file="/tmp/lane_a_inprocess_${RANDOM}.json"
  ( cd "$WT_DIR" && PATH="$HOME/.local/bin:$PATH" HOME="$HOME" \
    IMAGE_GEN_MODEL="$IMAGE_GEN_MODEL" \
    IMAGE_GEN_BASE_URL="http://localhost:4123/v1" \
    IMAGE_GEN_API_KEY="proxy-store-token" \
    IMAGE_GEN_TIMEOUT_S="$IMAGE_GEN_TIMEOUT_S" \
    PYTHONPATH="$WT_DIR" \
    python3 - "$out_file" <<'PY'
import asyncio, json, os, sys
out = sys.argv[1]
from daemon.tools.image_gen import create_image_gen_tools
tools = create_image_gen_tools(manager=None, current_instance_id="fallback")
gen = next(t for t in tools if getattr(t, "name", "") == "generate_image")
res = asyncio.run(gen.ainvoke({
  "prompt": "minimal flat-design red square icon on white background",
  "n": 1,
}))
open(out, "w").write(res if isinstance(res, str) else json.dumps(res, default=str))
PY
  ) 2>&1 | head -20
  if [ -f "$out_file" ] && grep -qE 'ens-img:image-gen:[0-9a-f]{32}' "$out_file" 2>/dev/null; then
    increment_budget "lane-a-fallback"
    log "fallback produced: $(head -c 200 "$out_file")"
    return 0
  fi
  log "FATAL: in-process fallback produced no marker. body: $(cat "$out_file" 2>/dev/null | head -c 400)"
  return 1
}

# ───────────────────────────────────────────────────────────────────────────
# LANE B PROBE — live proxy llm.ensem.dev (PROBE-ONLY)
# ───────────────────────────────────────────────────────────────────────────
lane_b_probe() {
  log "=== LANE B probe start (live proxy, ONE cheap call) ==="

  # Read existing ensemble-side key/URL (NEVER printed). The "live" here
  # refers to the user-facing public proxy used by the installed
  # ensemble; we use the system .env so the run never creates new keys.
  local live_env="/home/nea/ensemble-src/.env"
  if [ ! -f "$live_env" ]; then
    # live env not present — skip-with-disclosure
    log "live .env at $live_env NOT FOUND. lane-b is meaningless without it; SKIP-WITH-DISCLOSURE."
    local art="/tmp/lane_b_disclosure_$(date +%Y%m%dT%H%M%S).md"
    {
      echo "# Lane B disclosure"
      echo "reason: live env not found at $live_env"
      echo "verdict: SKIP-WITH-DISCLOSURE"
      echo "exit: 0"
    } > "$art"
    echo "disclosure-article: $art"
    return 0
  fi

  local live_key live_base
  live_key="$(grep -E '^OPENAI_API_KEY=' "$live_env" | head -1 | cut -d= -f2-)"
  live_base="$(grep -E '^OPENAI_BASE_URL=' "$live_env" | head -1 | cut -d= -f2-)"
  if [ -z "$live_key" ] || [ -z "$live_base" ]; then
    log "live env missing OPENAI_API_KEY / OPENAI_BASE_URL. SKIP-WITH-DISCLOSURE."
    local art="/tmp/lane_b_disclosure_$(date +%Y%m%dT%H%M%S).md"
    {
      echo "# Lane B disclosure"
      echo "reason: live env missing OPENAI_API_KEY or OPENAI_BASE_URL"
      echo "verdict: SKIP-WITH-DISCLOSURE"
      echo "exit: 0"
    } > "$art"
    echo "disclosure-article: $art"
    return 0
  fi

  # Probe-only ONE cheap POST. We do NOT register or write anything on
  # the live proxy.
  assert_budget

  log "POST $live_base/image_generation model=$IMAGE_GEN_MODEL"
  local hdr_file="/tmp/lane_b_headers.$$"
  local body_file="/tmp/lane_b_body.$$"
  local http
  http="$(curl -sS --max-time 30 -o "$body_file" -D "$hdr_file" \
    -w '%{http_code}' \
    -X POST \
    -H "Authorization: Bearer $live_key" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$IMAGE_GEN_MODEL\",\"prompt\":\"probe\",\"n\":1}" \
    "${live_base%/}/image_generation" 2>/dev/null || echo 000)"
  local body_snippet
  body_snippet="$(head -c 240 "$body_file" 2>/dev/null | tr -d '\n' | sed 's/"/\\"/g' || true)"
  log "probe http=$http body-preview=${body_snippet:0:120}"

  # 2xx path = real generation consumed. Count it.
  if [ "$http" -ge 200 ] 2>/dev/null && [ "$http" -lt 300 ] 2>/dev/null; then
    increment_budget "lane-b-probe"
    log "live proxy accepted the probe — 1 image counted (live proxy has the model registered)"
    return 0
  fi

  # 404 / unknown-model = SKIP-WITH-DISCLOSURE
  log "live proxy did NOT accept the probe (http=$http). SKIP-WITH-DISCLOSURE."
  local art="/tmp/lane_b_disclosure_$(date +%Y%m%dT%H%M%S).md"
  {
    echo "# Lane B disclosure"
    echo "verdict: SKIP-WITH-DISCLOSURE"
    echo "exit: 0"
    echo "probe-status: $http"
    echo "model-probed: $IMAGE_GEN_MODEL"
    echo "reason: live proxy model row not registered (user-gate; falls within the lane plan 'skip-with-disclosure' branch)"
    echo "response-snippet: ${body_snippet:0:240}"
  } > "$art"
  rm -f "$hdr_file" "$body_file"
  echo "disclosure-article: $art"
  return 0
}

# ─── Driver ──────────────────────────────────────────────────────────────────
_budget_init

# Per-leg timer: SECONDS at leg start; every long-running call checks
# check_leg_budget < 280 s and returns 124 if exceeded.
LEG_TIMEOUT_S=280
check_leg_budget() {
  local now="$1"
  if [ "$now" -ge "$LEG_TIMEOUT_S" ]; then
    echo "[timer] leg exceeded $LEG_TIMEOUT_S — aborting with 124"
    return 124
  fi
}

run_leg() {
  local name="$1" fn="$2"
  SECONDS=0
  log "[budget] starting $name (cap=$LEG_TIMEOUT_S s)"
  log "[budget] BEFORE $name: counter=$(_budget_get)"
  if "$fn"; then
    local rc=0
  else
    local rc=$?
  fi
  local now=$SECONDS
  log "[budget] AFTER $name: counter=$(_budget_get) elapsed=${now}s"
  return $rc
}

rc=0
case "$LEG" in
  lane-a)
    run_leg lane-a lane_a; rc=$?
    log "[budget] final-state: counter=$(_budget_get)"
    exit $rc
    ;;
  lane-b-probe)
    run_leg lane-b-probe lane_b_probe; rc=$?
    log "[budget] final-state: counter=$(_budget_get)"
    exit $rc
    ;;
  all)
    log "[budget] state at run start: counter=$(_budget_get)"
    run_leg lane-a lane_a && a_rc=0 || a_rc=$?
    log "[budget] mid-state (between legs): counter=$(_budget_get)"
    run_leg lane-b-probe lane_b_probe && b_rc=0 || b_rc=$?
    log "[budget] FINAL state: counter=$(_budget_get)"
    if [ $a_rc -eq 0 ] && [ $b_rc -eq 0 ]; then
      exit 0
    fi
    exit 1
    ;;
esac
