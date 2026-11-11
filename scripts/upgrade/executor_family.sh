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
#   resolves its stop script through TWO legs (lib.sh stop_via_stop_script):
#     (1) the SIBLING one level up of the CALLING script's dir —
#         lib.sh:4849 anchors BASH_SOURCE[1] (promote.sh's dir), so the leg
#         is <scripts_dir>/../stop-ensemble.sh;
#     (2) the $(pwd)/scripts/stop-ensemble.sh fallback (lib.sh:4852).
#   BOTH legs miss in an install layout ⇒ bash: No such file or directory ⇒
#   rc=127 ⇒ the promote aborts before the flip (B4). PR1 (edbf3c92f) closed
#   the DAEMON side of that class with a fail-closed arm preflight; THIS file
#   closes the SHIPPING side — the executor family now travels with the
#   release payload AND with every fresh install.
#
# PAYLOAD-RELATIVE PATHS (the canonical layout — ONE shape, two carriers):
#   <root>/scripts/upgrade/<family>   the executor scripts themselves
#   <root>/scripts/stop-ensemble.sh   the promote stop step's SIBLING
#   <root>/scripts/install-dir-guard.sh  the install-dir guard
#   where <root> is the staged release dir (releases/<ver>/) for the payload
#   and the install dir for a flat install. The sibling relation is what
#   matters: <scripts_dir>/../stop-ensemble.sh must resolve to a PRESENT and
#   EXECUTABLE file (PR1's arm gate: os.access(X_OK)).
#
# NO PLIST EVER (property, not accident): the family list below is an
# EXPLICIT enumeration — ensemble-prod.plist / ensemble-watchdog-watcher.plist
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
EOF
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
        *)
            printf 'usage: %s --install <src_root> <dst_root> | --paths | --stamp-coverage <git_root> <stamp>\n' "$0" >&2
            exit 2
            ;;
    esac
fi
