#!/usr/bin/env python3
"""Memoir Store v1 — the REAL plant/restore mutation matrix (W-3, council).

The phase-7 driver this file REPLACES only ORCHESTRATED packs — it planted
nothing, so a PASS was green with every guard deleted (the council's W-3:
a vacuous matrix). Every leg below plants a real mutation INDEPENDENTLY on
the production source, runs the intended guard pack, requires RED with the
INTENDED failing node, restores BYTE-IDENTICALLY, and requires GREEN.

Leg table (label -> guard -> what the mutation breaks):
  1  k-slice-removed              -> the C-1 pin (12 rows / k=5 => len==5)
  2  admission-after-insert       -> the D2.15/W3 source-order pin
  3  stall-warning-removed        -> the W-2 stall WARNING pin
  4  v1-recall-map-entry-deleted  -> the V-1 inverted empty-query pin
  5  knob-garbage-repaired        -> the knob truth-table garbage row
  6  non-ari-redirect-widened     -> the inner_soul non-ari BYTE-IDENTITY
                                     (script pack, runs the pack's own
                                     harness against the widened boundary)
  7  keepsake-template-drifted    -> the briefing pack's exact-byte pin
                                     (proves the W-1 single-sourcing: the
                                     briefing follows the repository
                                     constant)

Restore discipline (NO ``git checkout --``), from the house template
(``test/packs/metrics_read_mutation_matrix_test.py``):

  * each mutation is restored from an IN-MEMORY BYTE COPY taken
    immediately before the plant;
  * before the first leg a pre-run manifest (HEAD sha + the sha256 of
    every file the matrix will touch) is recorded to
    ``LOG_DIR/pre_run_manifest.json``, and each in-flight mutation's
    pre-plant bytes are journalled to ``LOG_DIR/recovery/<label>.blob``
    with ``LOG_DIR/inflight.json`` — a run killed mid-mutation leaves the
    tree DIRTY but DETECTABLE and ATTRIBUTABLE: the next invocation
    STARTS with an explicit clean-tree check and REFUSES to run dirty
    (critical note 7e92b12c), ``--verify`` re-hashes the manifest, and
    ``--recover`` rewrites the journaled bytes back;
  * ``git status --porcelain`` is captured BEFORE and AFTER every leg; a
    leg whose restore leaves any diff FAILS LOUD;
  * after the LAST restore the whole manifest is re-verified — a
    post-run mismatch is a loud FAIL, never a silent pass.

DETERMINISM (the porcelain root cause — ITEM 7 of the council cycle): the
digest hashes STABLE CONTENT ONLY — per-leg rc, status, the INTENDED red
marker, and pytest NODE COUNTS. No timings, no wall-clock, no summaries
that carry ``in X.XXs``. The digest file is REWRITTEN ONLY WHEN ITS
CONTENT CHANGES (or under ``--record``), so a re-run leaves the committed
digest byte-identical and the tree clean. Run it twice and ``diff`` the
digest file: byte-identical or the matrix has a determinism bug.

The working tree MUST be clean when the matrix starts (the pre-run
manifest shas are recorded against a committed tree, so a dirty tree is
not attributable to the matrix alone) — the script refuses to run dirty.

FENCE (the lane standard): every pack invocation runs under the inline
``POSTGRES_*`` / ``ENSEMBLE_SELF_ENV=dev`` fence, built per-variable BY
NAME — the ``env -u POSTGRES_*`` glob form is a SILENT NO-OP (bash does
not glob env names) and is never used here.

Invocation (from the repo root):

    .venv/bin/python test/packs/memoir_mutation_matrix_test.py
    .venv/bin/python test/packs/memoir_mutation_matrix_test.py --verify
    .venv/bin/python test/packs/memoir_mutation_matrix_test.py --recover
    .venv/bin/python test/packs/memoir_mutation_matrix_test.py --record
    .venv/bin/python test/packs/memoir_mutation_matrix_test.py --selftest
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = Path(os.environ.get("MEMOIR_MUTATION_LOG_DIR", "/tmp/memoir-mutation"))
RECOVERY_DIR = LOG_DIR / "recovery"
PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python")
DIGEST_PATH = (
    REPO_ROOT / ".agents" / "shared" / "planning" / "ari-memoir-store"
    / "mutation-matrix-digest.json"
)

SUBPROCESS_TIMEOUT_S = 900

RETRIEVAL_PACK = "tests/unit/test_memoir_retrieval_unit.py"
ADMISSION_ORDER_PACK = "tests/unit/test_memoir_write_purity_unit.py"
WORKER_PACK = "tests/unit/test_memoir_embedding_worker_unit.py"
TOOLS_PACK = "tests/unit/test_memoir_tools_unit.py"
KNOB_PACK = "tests/unit/test_memoir_knob_truth_table.py"
REDIRECT_SCRIPT_PACK = "test/packs/memoir_inner_soul_redirect_test.sh"
BRIEFING_PACK = "tests/unit/test_memoir_briefing_unit.py"


def _fenced_env() -> dict:
    """The lane fence, per-variable BY NAME (never the no-op glob form)."""
    env = dict(os.environ)
    for key in (
        "POSTGRES_USER",
        "POSTGRES_DB",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
        "PORT",
        "ENSEMBLE_SELF_ENV",
        "DAEMON_PORT",
    ):
        env.pop(key, None)
    env.update(
        {
            "POSTGRES_DB": "ensemble_dev_scratch",
            "POSTGRES_USER": "scratch",
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "5432",
            "ENSEMBLE_SELF_ENV": "dev",
        }
    )
    return env


#: The mutation table. Every entry is
#: ``(label, rel_path, find, replace, guard_kind, guard_path, red_marker,
#:    expected_occurrences)``.
#:
#: ``guard_kind`` is ``"pytest"`` or ``"script"``. ``red_marker`` is a
#: substring that MUST appear in the RED run's output — it proves the
#: mutation was caught by the INTENDED assertion, not by an unrelated
#: crash. ``expected_occurrences`` defaults to 1; leg 6 widens BOTH ari
#: branches (the single and the compound arm), mirroring the pack's own
#: M3 leg.
MUTATIONS: list[tuple[str, str, str, str, str, str, str, int]] = [
    (
        "k-slice-removed",
        "daemon/services/memoir_retrieval.py",
        "            candidates = candidates[:count]",
        "            pass  # MUTATION: the k-slice was removed",
        "pytest",
        RETRIEVAL_PACK,
        "test_k_caps_the_shipped_episode_count_on_a_larger_corpus",
        1,
    ),
    (
        "admission-after-insert",
        "daemon/repositories/memoir/repository.py",
        """                self._admit_pin(
                    session,
                    agent_id,
                    candidate_id=new_id,
                    candidate_tokens=candidate_tokens,
                )
            session.execute(
                _MEMOIR_INSERT_STATEMENT,
                {
                    "id": new_id,
                    "agent_id": agent_id,
                    "title": clean_title,
                    "body": clean_body,
                    "created_at": written_at,
                    "event_at": occurred_at,
                    "importance": clean_importance,
                    "stars": list(clean_stars) if clean_stars is not None else None,
                    "is_keepsake": bool(is_keepsake),
                    "pinned_by": pinned_by if is_keepsake else None,
                    "pinned_reason": pinned_reason if is_keepsake else None,
                    "pinned_at": written_at if is_keepsake else None,
                },
            )
            session.commit()""",
        """            session.execute(
                _MEMOIR_INSERT_STATEMENT,
                {
                    "id": new_id,
                    "agent_id": agent_id,
                    "title": clean_title,
                    "body": clean_body,
                    "created_at": written_at,
                    "event_at": occurred_at,
                    "importance": clean_importance,
                    "stars": list(clean_stars) if clean_stars is not None else None,
                    "is_keepsake": bool(is_keepsake),
                    "pinned_by": pinned_by if is_keepsake else None,
                    "pinned_reason": pinned_reason if is_keepsake else None,
                    "pinned_at": written_at if is_keepsake else None,
                },
            )
            session.commit()
            if is_keepsake:  # MUTATION: the admission now FOLLOWS the INSERT
                candidate_tokens = _estimate_tokens(
                    render_keepsake_line(
                        title=clean_title,
                        pinned_by=pinned_by,
                        pclass=KEEPSAKE_ADMISSION_PCLASS,
                        pinned_reason=pinned_reason,
                    )
                )
                self._admit_pin(
                    session,
                    agent_id,
                    candidate_id=new_id,
                    candidate_tokens=candidate_tokens,
                )""",
        "pytest",
        ADMISSION_ORDER_PACK,
        "test_the_admission_reads_precede_the_insert_in_the_source",
        1,
    ),
    (
        "stall-warning-removed",
        "daemon/services/memoir_embedding_worker.py",
        "                self._stall_streak >= MEMOIR_EMBED_STALL_WARN_TICKS",
        "                self._stall_streak >= 10**9  # MUTATION: the stall warning was removed",
        "pytest",
        WORKER_PACK,
        "test_a_starved_window_warns_after_n_stalled_ticks",
        1,
    ),
    (
        "v1-recall-map-entry-deleted",
        "daemon/tools/memoir.py",
        '    "query_required": MEMOIR_CODE_QUERY_REQUIRED,',
        "    # MUTATION: the V-1 map entry was deleted",
        "pytest",
        TOOLS_PACK,
        "test_a_blank_recall_query_refuses_with_query_required",
        1,
    ),
    (
        "knob-garbage-repaired",
        "daemon/tools/memoir_knob.py",
        """            state = _state(
                enabled=False,
                resolution=resolution,
                source="env",
                rejected_value=rejected,""",
        """            state = _state(
                enabled=True,  # MUTATION: garbage repaired to the default
                resolution=resolution,
                source="env",
                rejected_value=rejected,""",
        "pytest",
        KNOB_PACK,
        "test_indeterminate_value_fails_closed",
        1,
    ),
    (
        "non-ari-redirect-widened",
        "daemon/tools/inner_soul.py",
        'if agent_id == "ari":',
        'if agent_id != "__nobody__":  # MUTATION: the boundary widened',
        "script",
        REDIRECT_SCRIPT_PACK,
        "non-ari-byte-identity",
        2,
    ),
    (
        "keepsake-template-drifted",
        "daemon/repositories/memoir/models.py",
        'KEEPSAKE_LINE_TEMPLATE = (\n    "- [keepsake] {title} (pinned_by={pinned_by}; "\n    "provenance={pclass}) {pinned_reason}"\n)',
        'KEEPSAKE_LINE_TEMPLATE = (\n    "- [keepsake] {title} (pinned_by={pinned_by}; "\n    "provenance={pclass}X) {pinned_reason}"  # MUTATION: a template byte drifted\n)',
        "pytest",
        BRIEFING_PACK,
        "test_keepsake_line_template_exact_bytes",
        1,
    ),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, check=False
    )
    return out.stdout.strip()


def _porcelain() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def _touched_files() -> list[str]:
    seen: list[str] = []
    for entry in MUTATIONS:
        rel = entry[1]
        if rel not in seen:
            seen.append(rel)
    return seen


def _record_manifest() -> dict:
    manifest = {
        "head_sha": _git("rev-parse", "HEAD"),
        "files": {rel: _sha256(REPO_ROOT / rel) for rel in _touched_files()},
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "pre_run_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def _verify_manifest(manifest: dict) -> list[str]:
    mismatches: list[str] = []
    for rel, sha in manifest["files"].items():
        path = REPO_ROOT / rel
        if not path.exists():
            mismatches.append(f"{rel}: MISSING")
            continue
        if _sha256(path) != sha:
            mismatches.append(f"{rel}: sha mismatch")
    return mismatches


def _node_counts(output: str) -> dict:
    """STABLE node counts only — never the timing that follows them."""
    passed = re.findall(r"(\d+) passed", output)
    failed = re.findall(r"(\d+) failed", output)
    return {
        "passed": int(passed[-1]) if passed else None,
        "failed": int(failed[-1]) if failed else None,
    }


def _run_pack(guard_kind: str, guard_path: str) -> tuple[int, str]:
    if guard_kind == "script":
        cmd = ["bash", str(REPO_ROOT / guard_path)]
    else:
        cmd = [
            PYTHON,
            "-m",
            "pytest",
            guard_path,
            "-q",
            "--tb=line",
            "-p",
            "no:randomly",
            "--timeout=300",
        ]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=_fenced_env(),
        timeout=SUBPROCESS_TIMEOUT_S,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _journal(label: str, rel: str, blob: bytes) -> None:
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    (RECOVERY_DIR / f"{label}.blob").write_bytes(blob)
    (LOG_DIR / "inflight.json").write_text(
        json.dumps({"label": label, "path": rel}, indent=2), encoding="utf-8"
    )


def _unjournal(label: str) -> None:
    (RECOVERY_DIR / f"{label}.blob").unlink(missing_ok=True)
    inflight = LOG_DIR / "inflight.json"
    if inflight.exists():
        try:
            data = json.loads(inflight.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("label") == label:
            inflight.unlink(missing_ok=True)


def _canonical_legs(legs: list[dict]) -> bytes:
    return json.dumps(legs, sort_keys=True, indent=2).encode("utf-8")


def _write_digest_if_changed(legs: list[dict], force: bool) -> str:
    """Write ONLY when the stable content changes (or under --record)."""
    digest = hashlib.sha256(_canonical_legs(legs)).hexdigest()
    payload = {"digest": digest, "legs": legs}
    new_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if not force and DIGEST_PATH.exists():
        try:
            if DIGEST_PATH.read_text(encoding="utf-8") == new_text:
                print(f"DIGEST: unchanged ({digest[:16]}…) — not rewritten")
                return digest
        except OSError:
            pass
    DIGEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIGEST_PATH.write_text(new_text, encoding="utf-8")
    print(f"DIGEST: recorded {digest} -> {DIGEST_PATH.relative_to(REPO_ROOT)}")
    return digest


def run_matrix() -> int:
    dirty = _porcelain()
    if dirty:
        # The explicit clean-tree check runs FIRST on EVERY invocation —
        # a run killed by a crash/SIGKILL leaves the tree dirty and the
        # next run refuses here (critical note 7e92b12c).
        print("RESULT: FAIL (DIRTY TREE) — the matrix refuses to run:")
        for line in dirty:
            print(f"  {line}")
        inflight = LOG_DIR / "inflight.json"
        if inflight.exists():
            print(f"  in-flight mutation journal: {inflight.read_text(encoding='utf-8').strip()}")
        print("  (recover with --recover, or restore the tree; a dirty tree is not attributable)")
        return 1

    manifest = _record_manifest()
    print(f"pre-run manifest: {manifest['head_sha']} over {len(manifest['files'])} files")

    failures = 0
    legs: list[dict] = []
    for label, rel, find, replace, guard_kind, guard_path, marker, expected in MUTATIONS:
        path = REPO_ROOT / rel
        original = path.read_bytes()
        text = original.decode("utf-8")
        leg: dict = {
            "label": label,
            "guard": guard_path,
            "guard_kind": guard_kind,
            "red_marker": marker,
            "status": "OK",
            "restore_byte_identical": True,
            "porcelain_clean_after": True,
            "red_rc": None,
            "green_rc": None,
            "red_node_counts": None,
            "green_node_counts": None,
        }
        count = text.count(find)
        if count != expected:
            print(
                f"{label}: FAIL (the find-string occurs {count}x, expected "
                f"{expected}) — a leg that cannot plant is a MISSING LEG"
            )
            leg["status"] = "FAIL (plant)"
            failures += 1
            legs.append(leg)
            continue

        before_porcelain = _porcelain()
        _journal(label, rel, original)
        try:
            path.write_text(text.replace(find, replace), encoding="utf-8")
            rc, out = _run_pack(guard_kind, guard_path)
            leg["red_rc"] = rc
            leg["red_node_counts"] = _node_counts(out) if guard_kind == "pytest" else None
            if rc == 0:
                print(f"{label}: FAIL — the mutation did NOT turn the guard RED")
                leg["status"] = "FAIL (not red)"
                failures += 1
            elif marker not in out:
                print(
                    f"{label}: FAIL — the guard went RED but the INTENDED "
                    f"assertion ({marker!r}) is not in the output"
                )
                leg["status"] = "FAIL (wrong red)"
                failures += 1
            else:
                red_node = ""
                if guard_kind == "pytest":
                    match = re.search(
                        rf"FAILED {re.escape(guard_path)}::(?:\w+::)?{re.escape(marker)}",
                        out,
                    )
                    red_node = match.group(0) if match else ""
                leg["red_node"] = red_node
                print(f"{label}: OK (RED via {marker})")

            path.write_bytes(original)
            if path.read_bytes() != original:
                print(f"{label}: FAIL — the restore is NOT byte-identical")
                leg["restore_byte_identical"] = False
                leg["status"] = "FAIL (restore)"
                failures += 1
            rc2, out2 = _run_pack(guard_kind, guard_path)
            leg["green_rc"] = rc2
            leg["green_node_counts"] = _node_counts(out2) if guard_kind == "pytest" else None
            if rc2 != 0:
                print(f"{label}: FAIL — the restore did NOT turn the guard GREEN")
                leg["status"] = "FAIL (not green after restore)"
                failures += 1
            after_porcelain = _porcelain()
            if after_porcelain != before_porcelain:
                print(f"{label}: FAIL — the restore left porcelain residue:")
                for line in after_porcelain:
                    print(f"    {line}")
                leg["porcelain_clean_after"] = False
                leg["status"] = "FAIL (porcelain residue)"
                failures += 1
            if leg["status"] == "OK":
                print(
                    f"{label}: restored byte-identically, guard GREEN, "
                    "porcelain clean"
                )
        except subprocess.TimeoutExpired:
            print(f"{label}: FAIL (the guard run exceeded {SUBPROCESS_TIMEOUT_S}s)")
            path.write_bytes(original)
            leg["status"] = "FAIL (timeout)"
            failures += 1
        finally:
            path.write_bytes(original)
            _unjournal(label)
        legs.append(leg)

    mismatches = _verify_manifest(manifest)
    if mismatches:
        print("RESULT: FAIL (post-run manifest mismatch)")
        for line in mismatches:
            print(f"  {line}")
        failures += 1
    else:
        print("post-run manifest: every touched file is byte-identical")

    _write_digest_if_changed(legs, force="--record" in sys.argv)

    if failures:
        print(f"RESULT: FAIL ({failures} leg(s) failed)")
        return 1
    print(
        f"RESULT: PASS ({len(MUTATIONS)} RED/GREEN legs, every restore "
        "byte-identical, porcelain clean)"
    )
    return 0


def verify() -> int:
    manifest_path = LOG_DIR / "pre_run_manifest.json"
    if not manifest_path.exists():
        print("no pre-run manifest — nothing to verify")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches = _verify_manifest(manifest)
    inflight = LOG_DIR / "inflight.json"
    if inflight.exists():
        print(f"IN-FLIGHT mutation: {inflight.read_text(encoding='utf-8').strip()}")
    if mismatches:
        print("RESULT: FAIL (dirty tree)")
        for line in mismatches:
            print(f"  {line}")
        return 1
    print("RESULT: CLEAN (every manifest file is byte-identical)")
    return 0


def recover() -> int:
    inflight = LOG_DIR / "inflight.json"
    if not inflight.exists():
        print("no in-flight mutation — nothing to recover")
        return 0
    data = json.loads(inflight.read_text(encoding="utf-8"))
    label = data.get("label")
    rel = data.get("path")
    blob = RECOVERY_DIR / f"{label}.blob"
    if not blob.exists():
        print(f"RESULT: FAIL (no recovery blob for {label})")
        return 1
    shutil.copyfile(blob, REPO_ROOT / rel)
    _unjournal(label)
    print(f"RESULT: RECOVERED {rel} from the journaled bytes of {label}")
    return 0


def selftest() -> int:
    """Exercise the restore/interruption pins — on a MEMOIR file (this
    matrix must never touch a metrics surface).

    The manifest records the ORIGINAL sha BEFORE the plant, so
    ``verify()`` must DETECT the planted byte (exit 1) and then confirm
    the restored byte (exit 0)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    probe = REPO_ROOT / "daemon" / "tools" / "memoir.py"
    original = probe.read_bytes()
    original_sha = _sha256(probe)
    try:
        probe.write_bytes(b"# MUTATION: the selftest byte\n")
        (LOG_DIR / "pre_run_manifest.json").write_text(
            json.dumps(
                {
                    "head_sha": "selftest",
                    "files": {"daemon/tools/memoir.py": original_sha},
                }
            ),
            encoding="utf-8",
        )
        if verify() != 1:
            print("RESULT: FAIL (the verifier did not detect the planted byte)")
            return 1
        probe.write_bytes(original)
        if verify() != 0:
            print("RESULT: FAIL (the verifier did not confirm the restored byte)")
            return 1
    finally:
        probe.write_bytes(original)
        (LOG_DIR / "pre_run_manifest.json").unlink(missing_ok=True)
    if _porcelain():
        print("RESULT: FAIL (the selftest left porcelain residue)")
        return 1
    print("RESULT: PASS (the restore/interruption pins hold)")
    return 0


def main(argv: list[str]) -> int:
    if "--verify" in argv:
        return verify()
    if "--recover" in argv:
        return recover()
    if "--selftest" in argv:
        return selftest()
    return run_matrix()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
