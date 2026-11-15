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
