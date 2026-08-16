#!/bin/bash
# ============================================================================
# tests/test_deploy.sh — tests for scripts/deploy.sh (Auto-Restart Phase 1)
# ============================================================================
# Portable plain-bash tests: no bats, no new dev-dependency. Self-asserting;
# exits nonzero on any failure.
#
#   bash tests/test_deploy.sh
#
# Scope: --dry-run output parsing on both targets (dirs/ports/env paths/DB
# names), the live guard (ENSEMBLE_DEPLOY_LIVE), the missing-env-source
# refusal (exit 78, temp-dir fixture), and the .env.prod.demo generator
# (idempotency, PORT + POSTGRES_DB overridden, other keys preserved —
# secrets REDACTED in all test output). Nothing is ever deployed, built,
# stopped, or started by this suite: every non-dry-run case runs against
# throwaway fixtures with --no-start and a fake REPO_ROOT where possible.
# ============================================================================
# Fixture strategy: deploy.sh derives REPO_ROOT from its own path, so the
# suite builds a THROWAWAY copy of the script under $TMP/deploy-repo with
# stub dist/, agents/, config.yaml, launcher.sh, frontend/dist — letting us
# exercise the real staging code against $TMP targets (HOME overridden).
# ============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY="$REPO_ROOT/scripts/deploy.sh"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }

_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name" "$expected" "$actual"; fi
}

assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}

assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "must NOT contain '$needle'" "$haystack" ;;
        *) _pass ;;
    esac
}

# Redact obvious secret values before anything is echoed into a failure
# message (the fixtures use KEY=value lines on purpose).
redact() {
    printf '%s\n' "$1" | sed -E 's/(KEY|TOKEN|SECRET|PASSWORD|DSN|API_KEY)=.+/\1=<REDACTED>/I'
}

section() { printf '\n== %s ==\n' "$1"; }

# ─── fixture: throwaway repo + fake HOME ────────────────────────────────────
# Normalize the way deploy.sh does (REPO_ROOT via `cd && pwd`): macOS
# TMPDIR carries a trailing slash (→ double slash), and the script's own
# normalization would otherwise mismatch the raw fixture path in needles.
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/deploy-test.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
FAKE_REPO="$FIXTURE/repo"
FAKE_HOME="$FIXTURE/home"
mkdir -p "$FAKE_REPO/scripts" "$FAKE_REPO/agents" \
         "$FAKE_REPO/frontend/dist/frontend/browser" \
         "$FAKE_REPO/dist" "$FAKE_HOME"

cp "$DEPLOY" "$FAKE_REPO/scripts/deploy.sh"
chmod +x "$FAKE_REPO/scripts/deploy.sh"
printf '#!/bin/bash\n# stub launcher\n' > "$FAKE_REPO/launcher.sh"
chmod +x "$FAKE_REPO/launcher.sh"
printf 'stub-agents\n' > "$FAKE_REPO/agents/agent.md"
printf 'port: ${PORT:-8088}\n' > "$FAKE_REPO/config.yaml"
printf 'stub-index\n' > "$FAKE_REPO/frontend/dist/frontend/browser/index.html"
printf 'STUB_BINARY\n' > "$FAKE_REPO/dist/ensemble-prod"
chmod +x "$FAKE_REPO/dist/ensemble-prod"
cp "$REPO_ROOT/scripts/stop-ensemble.sh" "$FAKE_REPO/scripts/stop-ensemble.sh"

# .env.prod for the generator tests: two override keys + preserved keys
# (secret-shaped values are FAKE — but redacted in output regardless).
cat > "$FAKE_REPO/.env.prod" <<'EOF'
OPENAI_API_KEY=fake-key-for-tests-not-real
OPENAI_BASE_URL=https://llm.example.test/v1
HOST=127.0.0.1
PORT=9797
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=ensemble_prod
POSTGRES_USER=ensemble
POSTGRES_PASSWORD=fake-password-for-tests
EOF

# ─── 1. syntax + help gates ─────────────────────────────────────────────────
section "syntax gates"
if bash -n "$DEPLOY" 2>/dev/null; then _pass; else _fail "deploy.sh passes bash -n"; fi
if bash "$DEPLOY" --help >/dev/null 2>&1; then _pass; else _fail "--help exits 0"; fi
HELP_OUT="$(bash "$DEPLOY" --help 2>&1)"
assert_contains "--help mentions demo/live" "demo|live" "$HELP_OUT"
assert_contains "--help mentions ENSEMBLE_DEPLOY_LIVE" "ENSEMBLE_DEPLOY_LIVE" "$HELP_OUT"

# ─── 2. dry-run on demo: plan lines, no side effects ────────────────────────
section "dry-run demo"
DRY_DEMO="$(HOME="$FAKE_HOME" bash "$FAKE_REPO/scripts/deploy.sh" demo --dry-run 2>&1)"
DRY_DEMO_RC=$?
assert_eq "demo dry-run rc" "0" "$DRY_DEMO_RC"
assert_contains "demo dry-run: install dir" "$FAKE_HOME/agents-ensemble-demo" "$DRY_DEMO"
assert_contains "demo dry-run: port 7979" "port=7979" "$DRY_DEMO"
assert_contains "demo dry-run: db ensemble_demo" "db=ensemble_demo" "$DRY_DEMO"
assert_contains "demo dry-run: env source .env.prod.demo" ".env.prod.demo" "$DRY_DEMO"
assert_contains "demo dry-run: livez on 7979" "localhost:7979/livez" "$DRY_DEMO"
assert_contains "demo dry-run: readyz on 7979" "localhost:7979/readyz" "$DRY_DEMO"
assert_contains "demo dry-run: generator announced" "generating" "$DRY_DEMO"
if [ -e "$FAKE_REPO/.env.prod.demo" ]; then
    _fail "demo dry-run generated NO .env.prod.demo (side-effect leak)"
else
    _pass
fi
if [ -e "$FAKE_HOME/agents-ensemble-demo" ]; then
    _fail "demo dry-run created no install dir (side-effect leak)"
else
    _pass
fi
# The demo generator in dry-run must NOT leak the redirect into the file —
# pinned by the checks above (file absent). Also: no secrets in the log.
assert_not_contains "demo dry-run: no raw secrets" "fake-password-for-tests" "$DRY_DEMO"

# ─── 3. dry-run on live: guard + config table ───────────────────────────────
section "dry-run live guard"
GUARD_OUT="$(HOME="$FAKE_HOME" bash "$FAKE_REPO/scripts/deploy.sh" live --dry-run 2>&1)"
GUARD_RC=$?
assert_eq "live without ack: exit 78" "78" "$GUARD_RC"
assert_contains "live guard: refusal message" "REFUSING to deploy to live" "$GUARD_OUT"
assert_contains "live guard: tells how to override" "ENSEMBLE_DEPLOY_LIVE=1" "$GUARD_OUT"

mkdir -p "$FAKE_HOME/agents-ensemble"   # live dir must exist
LIVE_DRY="$(HOME="$FAKE_HOME" ENSEMBLE_DEPLOY_LIVE=1 bash "$FAKE_REPO/scripts/deploy.sh" live --dry-run 2>&1)"
LIVE_DRY_RC=$?
assert_eq "live with ack: dry-run rc 0" "0" "$LIVE_DRY_RC"
assert_contains "live dry-run: install dir" "$FAKE_HOME/agents-ensemble " "$LIVE_DRY"
assert_contains "live dry-run: port 9797" "port=9797" "$LIVE_DRY"
assert_contains "live dry-run: db ensemble_prod" "db=ensemble_prod" "$LIVE_DRY"
assert_contains "live dry-run: env source .env.prod" "env source: $FAKE_REPO/.env.prod" "$LIVE_DRY"
assert_contains "live dry-run: livez on 9797" "localhost:9797/livez" "$LIVE_DRY"

# ─── 4. missing env source → exit 78 (fixture without .env.prod) ────────────
section "missing env source refusal"
NOENV_REPO="$FIXTURE/noenv-repo"
cp -R "$FAKE_REPO" "$NOENV_REPO"
rm -f "$NOENV_REPO/.env.prod"
NOENV_OUT="$(HOME="$FAKE_HOME" ENSEMBLE_DEPLOY_LIVE=1 bash "$NOENV_REPO/scripts/deploy.sh" live --dry-run 2>&1)"
NOENV_RC=$?
assert_eq "live, no .env.prod: exit 78" "78" "$NOENV_RC"
assert_contains "live, no .env.prod: refusal says refusing" "refusing" "$NOENV_OUT"
assert_contains "live, no .env.prod: names the missing file" "$NOENV_REPO/.env.prod" "$NOENV_OUT"
# demo with no .env.prod AND no .env.prod.demo → generator cannot run → 78
NOENV_DEMO_OUT="$(HOME="$FAKE_HOME" bash "$NOENV_REPO/scripts/deploy.sh" demo --dry-run 2>&1)"
NOENV_DEMO_RC=$?
assert_eq "demo, no sources at all: exit 78" "$NOENV_DEMO_RC" "78"

# ─── 5. TARGET env override ─────────────────────────────────────────────────
section "TARGET env override"
ENV_TGT_OUT="$(HOME="$FAKE_HOME" TARGET=live ENSEMBLE_DEPLOY_LIVE=1 bash "$FAKE_REPO/scripts/deploy.sh" --dry-run 2>&1)"
ENV_TGT_RC=$?
assert_eq "TARGET=live env override rc" "0" "$ENV_TGT_RC"
assert_contains "TARGET=live override picks live dir" "$FAKE_HOME/agents-ensemble " "$ENV_TGT_OUT"
assert_contains "TARGET=live override port" "port=9797" "$ENV_TGT_OUT"

# ─── 6. .env.prod.demo generator: run for real against the fixture ──────────
section ".env.prod.demo generator (real run, --no-start)"
GEN_OUT="$(HOME="$FAKE_HOME" bash "$FAKE_REPO/scripts/deploy.sh" demo --no-start --skip-build 2>&1)"
GEN_RC=$?
assert_eq "demo real stage (no-start) rc" "0" "$GEN_RC"

# 6a. file generated next to .env.prod
[ -f "$FAKE_REPO/.env.prod.demo" ] && _pass || _fail ".env.prod.demo generated"

# 6b. PORT + POSTGRES_DB overridden; others preserved
GEN_PORT="$(sed -n 's/^PORT=//p' "$FAKE_REPO/.env.prod.demo" | head -1)"
GEN_DB="$(sed -n 's/^POSTGRES_DB=//p' "$FAKE_REPO/.env.prod.demo" | head -1)"
assert_eq "generated PORT overridden" "7979" "$GEN_PORT"
assert_eq "generated POSTGRES_DB overridden" "ensemble_demo" "$GEN_DB"
GEN_KEY_COUNT="$(grep -c '^OPENAI_API_KEY=' "$FAKE_REPO/.env.prod.demo")"
assert_eq "generated keeps OPENAI_API_KEY line" "1" "$GEN_KEY_COUNT"
GEN_HOST="$(sed -n 's/^HOST=//p' "$FAKE_REPO/.env.prod.demo" | head -1)"
assert_eq "generated keeps HOST" "127.0.0.1" "$GEN_HOST"
GEN_LINE_COUNT="$(grep -c '=' "$FAKE_REPO/.env.prod.demo")"
assert_eq "generated preserves line count" "$(grep -c '=' "$FAKE_REPO/.env.prod")" "$GEN_LINE_COUNT"

# 6c. staged into the install dir as .env
if [ -f "$FAKE_HOME/agents-ensemble-demo/.env" ]; then _pass; else _fail ".env staged into demo install dir"; fi
STAGED_PORT="$(sed -n 's/^PORT=//p' "$FAKE_HOME/agents-ensemble-demo/.env" | head -1)"
assert_eq "staged .env has demo PORT" "7979" "$STAGED_PORT"

# 6d. payload staged: binary, agents, config, launcher, frontend
[ -f "$FAKE_HOME/agents-ensemble-demo/ensemble-prod" ] && _pass || _fail "binary staged"
[ -f "$FAKE_HOME/agents-ensemble-demo/agents/agent.md" ] && _pass || _fail "agents staged"
[ -f "$FAKE_HOME/agents-ensemble-demo/config.yaml" ] && _pass || _fail "config.yaml staged"
[ -f "$FAKE_HOME/agents-ensemble-demo/launcher.sh" ] && _pass || _fail "launcher.sh staged"
[ -f "$FAKE_HOME/agents-ensemble-demo/frontend/dist/frontend/browser/index.html" ] && _pass \
    || _fail "frontend staged"

# 6e. idempotency: re-run stages cleanly, does not duplicate or drift
GEN_OUT2="$(HOME="$FAKE_HOME" bash "$FAKE_REPO/scripts/deploy.sh" demo --no-start --skip-build 2>&1)"
GEN_RC2=$?
assert_eq "second deploy run rc" "0" "$GEN_RC2"
assert_not_contains "second run: no regeneration (file exists)" "generating" "$GEN_OUT2"
GEN_PORT2="$(sed -n 's/^PORT=//p' "$FAKE_HOME/agents-ensemble-demo/.env" | head -1)"
assert_eq "second run: staged .env still 7979" "7979" "$GEN_PORT2"
AGENTS_COUNT="$(ls "$FAKE_HOME/agents-ensemble-demo/agents" | wc -l | tr -d ' ')"
assert_eq "second run: agents not duplicated" "1" "$AGENTS_COUNT"

# 6f. a PRE-EXISTING .env.prod.demo is never overwritten by the generator
printf 'PORT=1234\nPOSTGRES_DB=custom\nMARKER=preserved\n' > "$FAKE_REPO/.env.prod.demo"
GEN_OUT3="$(HOME="$FAKE_HOME" bash "$FAKE_REPO/scripts/deploy.sh" demo --no-start --skip-build 2>&1)"
assert_not_contains "pre-existing env not regenerated" "generating" "$GEN_OUT3"
CUSTOM_PORT="$(sed -n 's/^PORT=//p' "$FAKE_HOME/agents-ensemble-demo/.env" | head -1)"
assert_eq "custom pre-existing env is staged verbatim" "1234" "$CUSTOM_PORT"

# ─── 7. exit-code shape for unknown target ──────────────────────────────────
section "unknown target"
BOGUS_OUT="$(bash "$DEPLOY" bogus 2>&1)"
BOGUS_RC=$?
assert_eq "unknown target exit 78" "78" "$BOGUS_RC"
assert_contains "unknown target message" "unknown flag or target" "$BOGUS_OUT"

# ─── cleanup ────────────────────────────────────────────────────────────────
rm -rf "$FIXTURE"

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n========================================\n'
printf 'deploy tests: %d passed, %d failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed:%s\n' "$(redact "$FAILED_TESTS")"
    exit 1
fi
printf 'ALL PASS\n'
exit 0
