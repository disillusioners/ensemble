#!/bin/bash
# ============================================================================
# scripts/upgrade/executor_family.sh — the executor script family (D3)
# ============================================================================
# THE canonical list of the pipeline's own executor scripts, plus the
# primitives that PLACE them at the payload-relative paths the promote stop
# step resolves.
#
# WHY THIS FILE EXISTS (the v0.29.16 promote-halt class, 2026-11-11):
#   The install dir's scripts/ trees were OUT-OF-BAND copies — NO packaging
#   lane shipped them: the staged release payload had no scripts/ at all, and
#   `make install` shipped only scripts/watchdog-watcher.sh into
#   <install_dir>/scripts/ (Makefile:375-377). The promote stop step
#   resolves its stop script as the SIBLING one level up of the CALLING
#   script's dir (lib.sh stop_via_stop_script — the W4 single source of
#   truth: <scripts_dir>/../stop-ensemble.sh; the old $(pwd)/scripts
#   fallback is DELETED). A missing sibling aborts mid-stop ⇒ bash: No such
#   file or directory ⇒
#   rc=127 ⇒ the promote aborts before the flip (B4). PR1 (edbf3c92f) closed
#   the DAEMON side of that class with a fail-closed arm preflight; THIS file
#   closes the SHIPPING side — the executor family now travels with the
#   release payload AND with every fresh install.
#
# PAYLOAD-RELATIVE PATHS (the canonical layout — ONE shape, two carriers):
#   <root>/scripts/upgrade/<family>   the executor scripts themselves
#   <root>/scripts/stop-ensemble.sh   the promote stop step's SIBLING
#   <root>/scripts/install-dir-guard.sh  the install-dir guard
#   <root>/daemon/plugin_subsystem/promote_staleness.py  the plugin-staleness
#       predicate module (D3 payload-completeness slice — ENSEMBLE-32 Fix #3)
#   where <root> is the staged release dir (releases/<ver>/) for the payload
#   and the install dir for a flat install. The sibling relation is what
#   matters: <scripts_dir>/../stop-ensemble.sh must resolve to a PRESENT and
#   EXECUTABLE file (PR1's arm gate: os.access(X_OK)). The staleness module
#   is resolved by lib.sh's plugin-staleness gate as
#   <lib.sh-dir>/../../daemon/plugin_subsystem/promote_staleness.py — i.e.
#   TWO levels up from scripts/upgrade — so the payload must carry the
#   daemon/plugin_subsystem/ slice at the payload top level or a
#   release-staged promote refuses FAIL-CLOSED (plugin-staleness-unreadable:
#   incident docs/incidents/2026-11-14-macos-launchd-handback-halt.md §3.3 +
#   §7 00:49:01 recurrence, defect #3). The module is the MINIMAL slice by
#   construction: it imports ONLY stdlib + PyYAML — NO daemon imports — and
#   is invoked as a direct script (`"$py" "$pred" "$pdir"`), so no
#   daemon/__init__.py or plugin_subsystem/__init__.py travels with it
#   (daemon/__init__.py pulls daemon.util.* — staging it would be the
#   daemon grab-bag this slice exists to avoid).
#
# NO PLIST EVER (property, not accident): the family list below is an
# EXPLICIT enumeration — com.ensemble.prod.plist / com.ensemble.watchdog.plist
# are OPERATOR-side (make plist-install stages them into <install>/data/),
# never part of a release payload. A *.plist in a payload would put a
# launchd job definition into a web-servable/release artifact tree; the
# layout pin test (tests/test_executor_family_payload.sh) enforces the
# property over the whole staged tree.
#
# USAGE:
#   bash scripts/upgrade/executor_family.sh --install <src_root> <dst_root>
#       Copy the family from <src_root> into <dst_root> at the canonical
#       payload-relative paths (modes preserved). Fail-closed: a missing
#       source file aborts with the path named.
#   bash scripts/upgrade/executor_family.sh --paths
#       Print the canonical payload-relative path list (one per line).
#   bash scripts/upgrade/executor_family.sh --stamp-coverage <git_root> <stamp>
#       Print the family paths that do NOT exist at commit <stamp> (empty
#       output = the stamp covers the whole shipped set).
#
# SOURCED BY stage.sh (the payload assembler) — the payload copy, the
# manifest map/aggregate and the stamp-coverage gate all call the SAME
# functions the installer CLI calls, so the shipped set has exactly one
# definition.
# ============================================================================

set -u

# ── The canonical family list (payload-relative paths) ──────────────────────
# Every path is relative to a SOURCE ROOT that contains scripts/upgrade/ and
# scripts/stop-ensemble.sh + scripts/install-dir-guard.sh — the repo root,
# a staged release dir, or an install dir.
executor_family_paths() {
    cat <<'EOF'
scripts/upgrade/_build_frontend.sh
scripts/upgrade/adopt-unit.sh
scripts/upgrade/executor_family.sh
scripts/upgrade/journal_read.py
scripts/upgrade/journal_splice.py
scripts/upgrade/ledger_check.py
scripts/upgrade/lib.sh
scripts/upgrade/promote.sh
scripts/upgrade/restart.sh
scripts/upgrade/rollback.sh
scripts/upgrade/stage.sh
scripts/upgrade/status.sh
scripts/stop-ensemble.sh
scripts/install-dir-guard.sh
daemon/plugin_subsystem/promote_staleness.py
daemon/plugin_subsystem/config_compat_check.py
daemon/plugin_subsystem/config_compat_contract.py
EOF
}

# executor_family_daemon_slice_paths — the family members OUTSIDE scripts/
# (currently exactly the plugin-staleness predicate slice, ENSEMBLE-32
# Fix #3). Derived by filtering executor_family_paths so the canonical
# list stays the ONE definition of what ships; the scripts/ tree coverage
# (executor_family_manifest_lines over <payload>/scripts) plus this
# subset together cover the whole family.
executor_family_daemon_slice_paths() {
    executor_family_paths | grep -v '^scripts/'
}

# The paths whose EXEC BIT is load-bearing (PR1's arm gate tests
# os.access(X_OK) on the stop-script sibling; the guard is invoked as an
# executable by the Makefile lanes).
executor_family_exec_paths() {
    cat <<'EOF'
scripts/stop-ensemble.sh
scripts/install-dir-guard.sh
EOF
}

# executor_family_install <src_root> <dst_root>
#   Copy every family path from <src_root> to <dst_root>, preserving the
#   source mode (cp -p). FAIL-CLOSED: a missing/! -f source aborts with the
#   path named — a silently short family is the exact class this file
#   exists to close (the v0.29.16 halt was a MISSING file, not a wrong one).
#   Post-condition: the exec-bit-critical paths are executable at the
#   destination (a source that lost its exec bit is a packaging defect, not
#   a runtime condition to discover later).
executor_family_install() {
    local src_root="${1:-}" dst_root="${2:-}" rel src dst d
    if [ -z "$src_root" ] || [ -z "$dst_root" ]; then
        printf 'executor_family: usage: install <src_root> <dst_root>\n' >&2
        return 2
    fi
    if [ ! -d "$src_root" ]; then
        printf 'executor_family: source root %s does not exist\n' "$src_root" >&2
        return 1
    fi
    while IFS= read -r rel; do
        [ -n "$rel" ] || continue
        src="$src_root/$rel"
        if [ ! -f "$src" ]; then
            printf 'executor_family: MISSING source %s (the family list is authoritative — a missing member is a packaging defect, never a silent skip)\n' "$src" >&2
            return 1
        fi
        dst="$dst_root/$rel"
        d="$(dirname "$dst")"
        mkdir -p "$d" || { printf 'executor_family: cannot create %s\n' "$d" >&2; return 1; }
        cp -p "$src" "$dst" || { printf 'executor_family: copy FAILED %s -> %s\n' "$src" "$dst" >&2; return 1; }
    done <<EOF
$(executor_family_paths)
EOF
    # exec-bit post-condition on the load-bearing members
    while IFS= read -r rel; do
        [ -n "$rel" ] || continue
        dst="$dst_root/$rel"
        if [ ! -x "$dst" ]; then
            printf 'executor_family: %s landed WITHOUT the exec bit (source mode was not executable) — the promote arm gate requires an executable stop-script sibling\n' "$dst" >&2
            return 1
        fi
    done <<EOF
$(executor_family_exec_paths)
EOF
    return 0
}

# executor_family_manifest_lines <tree_root> [prefix]
#   "sha256  relpath" lines for every file under <tree_root>, sorted by
#   relpath — BYTE-COMPATIBLE with lib.sh's _tree_manifest <dir> "" (same
#   find|sed|sort order, same shasum -a 256 output, same two-space
#   separator), so the manifest aggregate computed from these lines equals
#   the one lib.sh's verifier would compute for the same tree.
executor_family_manifest_lines() {
    local dir="${1:-}" prefix="${2:-}" rel sha list
    if [ -z "$dir" ] || [ ! -d "$dir" ]; then
        printf 'executor_family_manifest_lines: %s is not a directory\n' "$dir" >&2
        return 1
    fi
    list="$(mktemp "${TMPDIR:-/tmp}/exec-family-manifest.XXXXXX")" || return 1
    ( cd "$dir" && find . -type f 2>/dev/null | sed 's|^\./||' | sort ) > "$list" || {
        rm -f "$list"; return 1;
    }
    while IFS= read -r rel; do
        [ -n "$rel" ] || continue
        sha="$(shasum -a 256 "$dir/$rel" 2>/dev/null | awk '{print $1}')"
        [ -n "$sha" ] || { rm -f "$list"; printf 'executor_family_manifest_lines: cannot hash %s\n' "$dir/$rel" >&2; return 1; }
        printf '%s  %s\n' "$sha" "$prefix$rel"
    done < "$list"
    rm -f "$list"
    return 0
}

# executor_family_tree_hash <lines>
#   The aggregate over manifest lines — IDENTICAL algorithm to lib.sh's
#   _tree_hash_of_lines (printf '%s\n' | shasum -a 256 | awk '{print $1}'),
#   so the manifest value and a lib.sh-side recomputation agree byte for byte.
executor_family_tree_hash() {
    printf '%s\n' "${1:-}" | shasum -a 256 | awk '{print $1}'
}

# executor_family_manifest_lines_for <root> <relpath>…
#   "sha256  relpath" lines for EXACTLY the named paths under <root>
#   (payload-relative), sorted by relpath — the same line shape + sort
#   discipline as executor_family_manifest_lines, but driven by an explicit
#   path list instead of a whole-tree walk. Used for the daemon/ slice,
#   whose members live OUTSIDE the scripts/ tree the whole-tree walker
#   covers. Fail-closed: a missing path aborts with the path named (a
#   silently short slice would re-open the defect-3 class this slice
#   closes — incident 2026-11-14 §3.3/§7).
executor_family_manifest_lines_for() {
    local root="${1:-}" rel sha
    [ -n "$root" ] || { printf 'executor_family_manifest_lines_for: usage: <root> <relpath>…\n' >&2; return 2; }
    [ -d "$root" ] || { printf 'executor_family_manifest_lines_for: %s is not a directory\n' "$root" >&2; return 1; }
    shift
    for rel in "$@"; do
        [ -n "$rel" ] || continue
        if [ ! -f "$root/$rel" ]; then
            printf 'executor_family_manifest_lines_for: MISSING %s (the slice list is authoritative — a missing member is a packaging defect, never a silent skip)\n' "$root/$rel" >&2
            return 1
        fi
        sha="$(shasum -a 256 "$root/$rel" 2>/dev/null | awk '{print $1}')"
        [ -n "$sha" ] || { printf 'executor_family_manifest_lines_for: cannot hash %s\n' "$root/$rel" >&2; return 1; }
        printf '%s  %s\n' "$sha" "$rel"
    done | sort -k2
}

# executor_family_staleness_entry_check <payload_root>
#   THE FAIL-CLOSED STAGING ASSERTION (ENSEMBLE-32 Fix #3, incident
#   docs/incidents/2026-11-14-macos-launchd-handback-halt.md §3.3 + §7
#   00:49:01 recurrence): a release payload whose promote would fail its
#   OWN plugin-staleness predicate must never be staged. The promote-time
#   gate (lib.sh promote_plugin_staleness_check) resolves the predicate
#   module as <lib.sh-dir>/../../daemon/plugin_subsystem/promote_staleness.py
#   and refuses FAIL-CLOSED (plugin-staleness-unreadable, exit 78) when the
#   module is absent — the v0.30.5 AND v0.30.9 release-staged promotes both
#   died exactly there, because the 14-file payload carried no daemon/
#   slice. stage.sh calls this BEFORE signing the manifest and refuses
#   (exit 78 class) on ANY failure — never a warning.
#
#   Three rungs, all fail-closed:
#     (1) RESOLUTION — the module exists at the exact payload-relative path
#         the promote-time resolution walks (<payload>/scripts/upgrade/
#         lib.sh must be present too: it is the anchor the two-levels-up
#         walk starts from).
#     (2) EVALUABILITY — an interpreter with PyYAML resolves (the same
#         ladder the promote-time gate runs: PLUGIN_STALENESS_PYTHON →
#         python3 → python). A payload that cannot be evaluated at stage
#         time would refuse its own gate at promote time — refuse here
#         with the PLUGIN_STALENESS_PYTHON remedy, never warn.
#     (3) EXECUTION — the staged module actually RUNS and answers fresh
#         (exit 0) on an empty plugin dir. This proves interpreter +
#         PyYAML + module integrity end-to-end inside the STAGED tree
#         (a corrupted/truncated module fails here, not at the promote).
#
#   Prints a one-line PASS summary on success; on failure prints the
#   refusal text (single line, greppable token plugin-staleness-payload)
#   and returns nonzero.
executor_family_staleness_entry_check() {
    local root="${1:-}" mod py cand scratch out rc
    [ -n "$root" ] || { printf 'plugin-staleness-payload: usage: entry-check <payload_root>\n' >&2; return 2; }
    # Rung 1 — resolution (the exact two-levels-up shape, anchored).
    if [ ! -f "$root/scripts/upgrade/lib.sh" ]; then
        printf 'plugin-staleness-payload: staged tree has no scripts/upgrade/lib.sh — the promote-time resolution anchor is absent; refusing'
        return 1
    fi
    mod="$root/daemon/plugin_subsystem/promote_staleness.py"
    if [ ! -f "$mod" ]; then
        printf 'plugin-staleness-payload: staged payload carries NO plugin-staleness predicate module at daemon/plugin_subsystem/promote_staleness.py — a release-staged promote would refuse FAIL-CLOSED (plugin-staleness-unreadable) exactly as the v0.30.5/v0.30.9 release-staged promotes did (incident 2026-11-14 §3.3/§7 defect #3); refusing'
        return 1
    fi
    # Rung 2 — evaluability (mirror lib.sh's interpreter ladder).
    py=""
    if [ -n "${PLUGIN_STALENESS_PYTHON:-}" ] && [ -x "${PLUGIN_STALENESS_PYTHON}" ]; then
        py="$PLUGIN_STALENESS_PYTHON"
    fi
    if [ -z "$py" ]; then
        for cand in "$(command -v python3 2>/dev/null || true)" \
                    "$(command -v python 2>/dev/null || true)"; do
            if [ -n "$cand" ] && [ -x "$cand" ] && "$cand" -c "import yaml" >/dev/null 2>&1; then
                py="$cand"
                break
            fi
        done
    fi
    if [ -z "$py" ]; then
        printf 'plugin-staleness-payload: no interpreter with PyYAML available to prove the staged predicate runs (tried PLUGIN_STALENESS_PYTHON, python3, python) — the promote-time gate would refuse FAIL-CLOSED on the same condition; set PLUGIN_STALENESS_PYTHON=<interpreter with PyYAML> on the staging host or install PyYAML; refusing'
        return 1
    fi
    # Rung 3 — execution inside the staged tree.
    scratch="$(mktemp -d "${TMPDIR:-/tmp}/exec-family-stale.XXXXXX")" || {
        printf 'plugin-staleness-payload: cannot create the entry-check scratch dir; refusing'
        return 1
    }
    out="$("$py" "$mod" "$scratch" 2>&1)"
    rc=$?
    rm -rf "$scratch" 2>/dev/null
    if [ "$rc" -ne 0 ]; then
        printf 'plugin-staleness-payload: the STAGED predicate module failed its entry check (rc=%s): %s — a payload whose own predicate cannot run refuses at promote; refusing' "$rc" "$(printf '%s' "$out" | tr '\n' ';' | tr -s ' ' | cut -c1-300)"
        return 1
    fi
    printf 'staleness predicate entry-check PASS (module=%s interpreter=%s verdict=%s)' \
        "${mod#"$root"/}" "$py" "$(printf '%s' "$out" | tail -n 1)"
    return 0
}

# executor_family_verify_stamp_coverage <git_root> <stamp>
#   Print the family paths that do NOT exist at commit <stamp> in
#   <git_root>. Empty output = the stamp COVERS the whole shipped set.
#   This is the truthfulness gate for PIPELINE_SCRIPTS_REV: the stamp is
#   the commit the payload's scripts claim to contain (D6-8/D6-10/D6-11 —
#   on a non-git pinned dir the stamp IS the whole provenance), so a family
#   member that did not exist at that commit makes the stamp a LIE about
#   what it vouches for. The remedy is the release obligation that already
#   exists: re-pin PIPELINE_SCRIPTS_REV + PIPELINE_SCRIPTS_FLOOR_COMMIT to
#   a merge commit that contains the shipped set.
executor_family_verify_stamp_coverage() {
    local git_root="${1:-}" stamp="${2:-}" rel missing=0
    if [ -z "$git_root" ] || [ -z "$stamp" ]; then
        printf 'executor_family_verify_stamp_coverage: usage: <git_root> <stamp>\n' >&2
        return 2
    fi
    while IFS= read -r rel; do
        [ -n "$rel" ] || continue
        if ! git -C "$git_root" cat-file -e "${stamp}:${rel}" 2>/dev/null; then
            printf '%s\n' "$rel"
            missing=1
        fi
    done <<EOF
$(executor_family_paths)
EOF
    return "$missing"
}

# ── D4 (self-service-promote Wave 5, §2.6): the STAGE-TIME scripts-pin
# advance — the release-lane WRITER. The arm-time READER lives daemon-side
# (daemon/tools/upgrade_tools.py scripts_pin_self_advance); both move the
# SAME shared pin worktree with a worktree-local `git checkout --detach`
# (NEVER a fetch — worktrees share the object store with the main repo, so
# the staged release's HEAD commit is present by construction).
#
# scripts_pin_from_env [install_dir]
#   Resolve the scripts pin for the stage lane: the ambient explicit
#   ENSEMBLE_UPGRADE_SCRIPTS_DIR first, then the INSTALL .env pin (the live
#   daemon's pin lives there — the stage lane runs outside the daemon env).
#   Prints the dir (possibly empty) — a DIRECTORY TEST is the caller's job.
scripts_pin_from_env() {
    local install_dir="${1:-}" raw
    raw="${ENSEMBLE_UPGRADE_SCRIPTS_DIR:-}"
    if [ -z "$raw" ] && [ -n "$install_dir" ] && [ -f "$install_dir/.env" ]; then
        raw="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}ENSEMBLE_UPGRADE_SCRIPTS_DIR[[:space:]]*=[[:space:]]*//p' "$install_dir/.env" 2>/dev/null | head -1)"
        raw="${raw%$'\r'}"
        case "$raw" in
            \"*\") raw="${raw#\"}"; raw="${raw%\"}" ;;
            \'*\') raw="${raw#\'}"; raw="${raw%\'}" ;;
        esac
    fi
    printf '%s\n' "$raw"
}

# _stage_marker_reclaimable <marker-path> <now> — is a blocking steal-won
# marker reclaimable? rc 0 = yes (its stealer is DEAD / its pid is
# impossible / it is ancient >3600s — the marker outlived its
# transition), rc 1 = no (a LIVE transition owns it; fail-closed
# block). Malformed content BLOCKS (a human clears it) — mirrors
# _advance_marker_reclaimable daemon-side.
_stage_marker_reclaimable() {
    local m_raw m_pid m_ts m_now
    m_now="$2"
    m_raw="$(cat "$1" 2>/dev/null || true)"
    set -- $m_raw
    [ $# -ge 2 ] || return 1
    m_pid="$1"; m_ts="$2"
    case "$m_pid$m_ts" in
        ''|*[!0-9]*) return 1 ;;
    esac
    case "$m_pid" in
        ????????*) return 0 ;;   # 8+ digits — impossible pid, no live stealer
    esac
    case "$m_ts" in
        ???????????*) return 1 ;;  # 11+ digits — future-nonsense timestamp
    esac
    [ "$m_pid" -gt 0 ] || return 0
    if _efa_pid_alive "$m_pid" && [ $(( m_now - m_ts )) -lt 3600 ]; then
        return 1
    fi
    return 0
}

# _stage_advance_lock_acquire <lock_dir_path> — the CYCLE-3 A2 TRUE-CAS
# acquisition of the advance lock (ONE namespace with the daemon-side
# _advance_lock_acquire; the shell lane cannot flock — Bash-3.2/BSD).
# rc 0 = this process HOLDS the lock; rc 1 = refused (the reason is
# printed). Fast path = the atomic mkdir. Held path = the
# liveness-gated verdict, then the link-claim CAS — SINGLE WINNER by
# construction under N concurrent stealers (the pre-A2 rename-aside
# measured 2–3 concurrent winners: each rename succeeded against
# whatever sat at the path, including another stealer's
# freshly-installed LIVE lock):
#   1. the verdict qualifies the holder (dead pid + BOTH clocks >300s);
#   2. EXCLUSIVE CLAIM: hard-link a claim file to <lock>.steal-won —
#      link(2) is EEXIST-atomic; exactly ONE stealer wins. A marker
#      naming a dead/impossible/ancient stealer is reclaimed once
#      (crash recovery); a live one blocks fail-closed;
#   3. RETIRE the stale dir (uncontested — every other stealer lost
#      the link) and RE-VERIFY it still answers to the verdict's owner
#      bytes — a REPLACED dir is restored best-effort and refused,
#      never stolen;
#   4. mkdir the freed path — EEXIST means a concurrent non-stealing
#      acquirer won it; this stealer loses and refuses.
_stage_advance_lock_acquire() {
    local lock_dir_path="$1"
    local now adv_owner adv_dir_age adv_hb_age
    local adv_marker adv_claim adv_src adv_retired adv_retired_owner adv_restored
    if mkdir "$lock_dir_path" 2>/dev/null; then
        return 0
    fi
    now="$(date +%s)"
    # ── the liveness-gated verdict (CYCLE-2 MAJOR-4, unchanged) ──────
    adv_owner="$(cat "$lock_dir_path/owner" 2>/dev/null || true)"
    case "$adv_owner" in
        ''|0)
            printf 'advance lock HELD (%s) with NO valid owner pid (%s) — the holder cannot be attributed; never stolen from (clear the lock by hand)' "$lock_dir_path" "$adv_owner"
            return 1
            ;;
        *[!0-9]*)
            printf 'advance lock HELD (%s) with a non-pid owner file (%s) — the holder cannot be attributed; never stolen from (clear the lock by hand)' "$lock_dir_path" "$adv_owner"
            return 1
            ;;
        ????????*)
            # cycle-3 A3: an out-of-range pid can never name a real
            # process (and kill -0 misreports huge pids) — unattributable.
            printf 'advance lock HELD (%s) with an out-of-range owner pid (%s — beyond the kernel pid_max ceiling) — the holder cannot be attributed; never stolen from (clear the lock by hand)' "$lock_dir_path" "$adv_owner"
            return 1
            ;;
    esac
    if [ "$adv_owner" -gt 4194304 ]; then
        printf 'advance lock HELD (%s) with an out-of-range owner pid (%s — beyond the kernel pid_max ceiling) — the holder cannot be attributed; never stolen from (clear the lock by hand)' "$lock_dir_path" "$adv_owner"
        return 1
    fi
    adv_dir_age=$(( now - $(_efa_stat_mtime "$lock_dir_path") ))
    if [ -f "$lock_dir_path/heartbeat" ]; then
        adv_hb_age=$(( now - $(_efa_stat_mtime "$lock_dir_path/heartbeat") ))
    else
        adv_hb_age="$adv_dir_age"
    fi
    if _efa_pid_alive "$adv_owner"; then
        printf 'advance lock HELD (%s) by a LIVE owner (pid %s; dir %ss, heartbeat %ss old) — never stolen from a live holder; retry' \
            "$lock_dir_path" "$adv_owner" "$adv_dir_age" "$adv_hb_age"
        return 1
    fi
    if [ "$adv_dir_age" -le 300 ] || [ "$adv_hb_age" -le 300 ]; then
        printf 'advance lock HELD (%s; holder pid %s gone but dir %ss, heartbeat %ss old — BOTH must exceed 300s) — not yet stale-breakable; retry' \
            "$lock_dir_path" "$adv_owner" "$adv_dir_age" "$adv_hb_age"
        return 1
    fi
    # ── the CYCLE-3 A2 CAS (single winner by construction) ───────────
    adv_marker="${lock_dir_path}.steal-won"
    adv_claim="${lock_dir_path}.steal-claim.${BASHPID:-$$}"
    if [ -f "$lock_dir_path/heartbeat" ]; then
        adv_src="$lock_dir_path/heartbeat"
    else
        adv_src="$lock_dir_path/owner"
    fi
    printf '%s %s\n' "${BASHPID:-$$}" "$now" > "$adv_claim" 2>/dev/null || {
        printf 'advance steal claim could not be staged (%s) — the steal never started' "$adv_claim"
        return 1
    }
    if ! ln "$adv_claim" "$adv_marker" 2>/dev/null; then
        if _stage_marker_reclaimable "$adv_marker" "$now"; then
            rm -f "$adv_marker" 2>/dev/null || true
        else
            rm -f "$adv_claim" 2>/dev/null || true
            printf 'advance steal marker HELD by a live transition (%s) — single-winner CAS; retry' "$adv_marker"
            return 1
        fi
        if ! ln "$adv_claim" "$adv_marker" 2>/dev/null; then
            rm -f "$adv_claim" 2>/dev/null || true
            printf 'advance steal claim LOST (marker %s) — single-winner CAS; retry' "$adv_marker"
            return 1
        fi
    fi
    rm -f "$adv_claim" 2>/dev/null || true
    adv_retired="${lock_dir_path}.retired.${BASHPID:-$$}"
    if ! mv "$lock_dir_path" "$adv_retired" 2>/dev/null; then
        rm -f "$adv_marker" 2>/dev/null || true
        printf 'advance steal retire FAILED (%s) — the lock path changed under the steal; retry' "$lock_dir_path"
        return 1
    fi
    adv_retired_owner="$(cat "$adv_retired/owner" 2>/dev/null || true)"
    if [ "$adv_retired_owner" != "$adv_owner" ]; then
        if mv "$adv_retired" "$lock_dir_path" 2>/dev/null; then
            adv_restored='the replacement is restored'
        else
            adv_restored="the replacement is parked at $adv_retired (clear by hand)"
        fi
        rm -f "$adv_marker" 2>/dev/null || true
        printf 'advance lock dir REPLACED after the verdict — %s; never stolen (fail-closed)' "$adv_restored"
        return 1
    fi
    if ! mkdir "$lock_dir_path" 2>/dev/null; then
        rm -rf "$adv_retired" 2>/dev/null || true
        rm -f "$adv_marker" 2>/dev/null || true
        printf 'advance steal lost the free path to a concurrent acquirer (%s) — single-winner CAS; retry' "$lock_dir_path"
        return 1
    fi
    printf '%s\n' "${BASHPID:-$$}" > "$lock_dir_path/owner" 2>/dev/null || true
    printf '%s\n' "$now" > "$lock_dir_path/heartbeat" 2>/dev/null || true
    rm -rf "$adv_retired" 2>/dev/null || true
    rm -f "$adv_marker" 2>/dev/null || true
    return 0
}

# stage_scripts_pin_advance <pin_scripts_upgrade_dir> <source_commit> <floor_commit> <staging_repo_root>
#   The guarded advance: `git -C <pin_root> checkout --detach T` after
#   verifying (1) T present locally, (2) floor ⊆ T, (3) the 17-path
#   executor-family stamp coverage at T EMPTY, (4) the pin is NOT the
#   staging tree itself, (5) the mkdir lock acquired. On SUCCESS prints
#   "T_old=<short> T_new=<full>" and returns 0 with pin HEAD == T. On
#   FAILURE prints the exact refusal reason and returns 1 — NEVER touches
#   the staging tree (the caller owns rm -rf "$STAGE_TMP"; exit 78).
#   Lock: a portable mkdir lock beside the worktree (lib.sh's header bans
#   flock(1) — "Bash 3.2 / BSD tools only"); same-install stage-vs-arm
#   races are already serialized by the install's pipeline lock — this
#   covers the cross-install case (two envs, one shared pin), MINOR-11.
# _efa_pid_alive <pid> — kill -0 liveness with EPERM=ALIVE semantics
# (self-contained twin of lib.sh's _pid_alive / upgrade_journal's
# _pid_alive: this file is sourced by stage.sh but ALSO runs standalone
# in CLI mode where lib.sh is NOT loaded — MAJOR-4's steal gate must not
# depend on the caller having sourced lib.sh). Exit 0 = alive (EPERM
# counts as ALIVE: a live foreign-user holder is never stealable);
# 1 = dead.
_efa_pid_alive() {
    local err
    err="$(LC_ALL=C kill -0 "$1" 2>&1)" && return 0
    case "$err" in
        *"not permitted"*) return 0 ;;
        *) return 1 ;;
    esac
}

# _efa_stat_mtime <path> — portable mtime epoch. BSD `stat -f %m` first,
# GNU `stat -c %Y` second, else 0. EXIT-STATUS fallback alone is NOT
# enough (found driving the MAJOR-4 proof on GNU): GNU `stat -f %m <path>`
# treats %m as an OPERAND — it exits 1 AND still dumps the real path's
# filesystem status to stdout, so a plain `a || b` chain yields a
# multi-line blob that explodes as unbound-variable inside $((…)). The
# output is therefore validated NUMERICALLY at every step.
_efa_stat_mtime() {
    local m
    m="$(stat -f %m "$1" 2>/dev/null || true)"
    case "$m" in
        ''|*[!0-9]*) m="$(stat -c %Y "$1" 2>/dev/null || true)" ;;
    esac
    case "$m" in
        ''|*[!0-9]*) echo 0 ;;
        *) printf '%s\n' "$m" ;;
    esac
}

# _adv_heartbeat_refresher <lock_dir> <holder_pid> — CYCLE-2 MAJOR-4
# (2026-11-20): the m4 namespace had NO heartbeat refresh, so a long
# LIVE checkout's lock crossed the 300s staleness line (dir mtime is set
# once at mkdir) and the python lane's mtime-only stale-break STOLE it —
# both lanes then moved the pin HEAD concurrently. This refresher keeps
# the heartbeat fresh across the checkout AND dies with its holder: the
# `kill -0 <holder_pid>` guard means a SIGKILLed holder's heartbeat
# freezes at its last stamp and the lock goes stale normally (a refresher
# that outlived its holder would keep a dead holder's lock alive forever).
_adv_heartbeat_refresher() {
    local dir="$1" holder="$2"
    while kill -0 "$holder" 2>/dev/null && [ -d "$dir" ]; do
        printf '%s\n' "$(date +%s)" > "$dir/heartbeat" 2>/dev/null || break
        sleep 30
    done
}

stage_scripts_pin_advance() {
    local pin_scripts="$1" t="$2" floor="$3" repo_root="$4"
    local pin_root t_old lock_dir_path cov now age
    local adv_holder adv_dirty adv_dirty_rc
    pin_root="$(cd "$pin_scripts/../.." 2>/dev/null && pwd -P)" || {
        printf 'the configured scripts pin %s has no worktree root' "$pin_scripts"
        return 1
    }
    if [ "$pin_root" = "$(cd "$repo_root" && pwd -P)" ]; then
        printf 'the configured scripts pin IS the staging tree itself (%s) — the release lane must not move its own HEAD mid-stage' "$pin_root"
        return 1
    fi
    t_old="$(git -C "$pin_root" rev-parse --short HEAD 2>/dev/null || echo '?')"
    if ! git -C "$pin_root" cat-file -e "${t}^{commit}" 2>/dev/null; then
        printf 'target %s is NOT present in the pin worktree object store (%s) — worktrees share objects, so this pin is a foreign clone; run by hand: git -C %s fetch origin && git -C %s checkout --detach %s' \
            "$t" "$pin_root" "$pin_root" "$pin_root" "$t"
        return 1
    fi
    if ! git -C "$pin_root" merge-base --is-ancestor "$floor" "$t" 2>/dev/null; then
        printf 'floor %s is NOT an ancestor of target %s in the pin worktree (the pin history diverges from the release line)' "$floor" "$t"
        return 1
    fi
    cov="$(executor_family_verify_stamp_coverage "$pin_root" "$t")"
    if [ -n "$cov" ]; then
        printf 'executor-family stamp coverage at target %s is NON-EMPTY:\n%s' "$t" "$cov"
        return 1
    fi
    lock_dir_path="${pin_root}.scripts-advance.lock.d"
    # CYCLE-3 A2 (2026-11-20): the acquisition is now the TRUE-CAS
    # protocol (_stage_advance_lock_acquire: verdict + link-claim +
    # retire + mkdir-CAS) — under N concurrent stealers EXACTLY ONE
    # wins, both lanes. The pre-A2 rename-aside measured 2–3 concurrent
    # winners (each rename succeeded against whatever sat at the path,
    # including another stealer's freshly-installed LIVE lock).
    if ! _stage_advance_lock_acquire "$lock_dir_path"; then
        # the helper printed the refusal (verdict or CAS loss)
        return 1
    fi
    now="$(date +%s)"
    adv_holder="${BASHPID:-$$}"
    printf '%s\n' "$adv_holder" > "$lock_dir_path/owner"
    printf '%s\n' "$now" > "$lock_dir_path/heartbeat"
    # MAJOR-4: keep the heartbeat fresh across the (possibly long)
    # checkout — the refresher dies with this holder (kill -0 $adv_holder guard).
    _adv_heartbeat_refresher "$lock_dir_path" "$adv_holder" &
    _adv_hb_pid=$!
    # release discipline mirrors lib.sh lock_release: rm -rf ONLY when this
    # process owns the lock dir (owner file == adv_holder); a foreign holder is
    # never touched. The refresher is stopped FIRST (a straggler write
    # after rm -rf would recreate the heartbeat file in a deleted dir's
    # place — and a surviving refresher must never outlive the hold).
    _adv_release() {
        if [ -n "${_adv_hb_pid:-}" ]; then
            kill "$_adv_hb_pid" 2>/dev/null
            wait "$_adv_hb_pid" 2>/dev/null
        fi
        if [ -d "$lock_dir_path" ] && [ "$(cat "$lock_dir_path/owner" 2>/dev/null)" = "$adv_holder" ]; then
            rm -rf "$lock_dir_path"
        fi
    }
    # CYCLE-3 E2 (2026-11-20): the stage-time twin of the arm-time
    # dirty guard (upgrade_tools.py review-fix d11, asymmetric gap
    # RR2): `checkout --detach` only ever moves a CLEAN, operator-owned
    # pin worktree (§2.6(b)); a dirty tree would fold local edits into
    # the advanced HEAD or lose them. Refuse (never move); fail-closed
    # when cleanliness is unprovable. The lock is released on refusal.
    adv_dirty="$(git -C "$pin_root" status --porcelain 2>/dev/null)"
    adv_dirty_rc=$?
    if [ "$adv_dirty_rc" -ne 0 ]; then
        _adv_release
        printf 'pin worktree cleanliness UNVERIFIABLE (git status rc=%s) — fail-closed per §2.6(b); the operator advances by hand: git -C %s checkout --detach %s' "$adv_dirty_rc" "$pin_root" "$t"
        return 1
    fi
    if [ -n "$adv_dirty" ]; then
        _adv_release
        printf 'the pin worktree is DIRTY (%s status entries) — the stage lane never moves the HEAD of an operator-owned tree with local changes (§2.6(b) fail-closed; clean or commit the tree by hand, then re-stage); the operator advances by hand: git -C %s checkout --detach %s' "$(printf '%s\n' "$adv_dirty" | wc -l)" "$pin_root" "$t"
        return 1
    fi
    if ! git -C "$pin_root" checkout --detach "$t" >/dev/null 2>&1; then
        _adv_release
        printf 'the worktree-local HEAD move FAILED (git checkout --detach %s in %s)' "$t" "$pin_root"
        return 1
    fi
    if [ "$(git -C "$pin_root" rev-parse HEAD 2>/dev/null)" != "$t" ]; then
        _adv_release
        printf 'checkout returned 0 but pin HEAD != target (%s)' "$t"
        return 1
    fi
    _adv_release
    printf 'T_old=%s T_new=%s' "$t_old" "$t"
    return 0
}

# ── CLI mode (only when executed, never when sourced) ───────────────────────
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    _cmd="${1:-}"
    shift || true
    case "$_cmd" in
        --install)
            executor_family_install "${1:-}" "${2:-}"
            exit $?
            ;;
        --paths)
            executor_family_paths
            exit 0
            ;;
        --stamp-coverage)
            executor_family_verify_stamp_coverage "${1:-}" "${2:-}"
            exit $?
            ;;
        --staleness-entry-check)
            executor_family_staleness_entry_check "${1:-}"
            exit $?
            ;;
        *)
            printf 'usage: %s --install <src_root> <dst_root> | --paths | --stamp-coverage <git_root> <stamp>\n' "$0" >&2
            exit 2
            ;;
    esac
fi
