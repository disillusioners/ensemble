#!/usr/bin/env python3
"""project-skill-loader — mutation matrix (Phase-4 code-review fix cycle).

THE ARTIFACT behind the fix-cycle mutation claim. Enumerates every leg in
code: plant the defect -> the discriminating pin must go RED -> restore ->
the same pin must go GREEN (file sha verified identical to the original).

Nothing here is inferred or narrated: the TSV line per leg is the evidence.

Usage (from the lane worktree):

    .venv/bin/python tests/packs/psl_fix_cycle_mutation_matrix.py \
        [--legs W1-corpus,P4-M4] [--out /tmp/psl-matrix.tsv]

Exit 0 only when EVERY selected leg is RED-with-defect AND GREEN-after-
restore. Any leg that fails to discriminate (GREEN with the defect
planted) is reported NON-DISCRIMINATING and fails the run — a pin that
cannot fail is worse than no pin.

Env fence: the child pytest runs with the POSTGRES_* variables scrubbed
PER-VARIABLE (``env -u POSTGRES_*`` is a silent no-op — see the project
critical notes), so a leg can never touch the live DB.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(WT, ".venv", "bin", "python")

SCRUB = [
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "DATABASE_URL",
    "ENSEMBLE_SELF_ENV",
    "PG_TEST_DB",
]


@dataclass
class Leg:
    id: str
    path: str
    old: str
    new: str
    node: str
    note: str = ""
    # ``belt=True`` marks a defence-in-depth leg that is EXPECTED to be
    # non-discriminating (a second layer whose sibling already refuses).
    # Recorded for completeness; not counted as a failure.
    belt: bool = False


LEGS: list[Leg] = [
    # ------------------------------------------------------------------
    # W1 — corpus project-binding TOCTOU (security-grade)
    # ------------------------------------------------------------------
    Leg(
        id="W1-corpus",
        path="daemon/services/project_skill_source/service.py",
        old="if index is None or index.project_id != project_id:",
        new="if index is None:",
        node=(
            "tests/unit/test_project_skill_source_index.py::"
            "TestCorpusProjectBinding::"
            "test_corpus_empty_when_index_rebound_to_other_project"
        ),
        note="project re-check dropped from corpus_for",
    ),
    Leg(
        id="W1-lookup",
        path="daemon/services/project_skill_source/service.py",
        old="""                if index.project_id != project_id:
                    # W1 sibling (same TOCTOU window as ``corpus_for``):""",
        new="""                if False:
                    # W1 sibling (same TOCTOU window as ``corpus_for``):""",
        node=(
            "tests/unit/test_project_skill_source_index.py::"
            "TestCorpusProjectBinding::"
            "test_lookup_explicit_miss_when_index_rebound"
        ),
        note="post-gate re-fetch project re-check dropped",
    ),
    # ------------------------------------------------------------------
    # W2 — sync DB adapter on the event loop
    # ------------------------------------------------------------------
    Leg(
        id="W2",
        path="daemon/services/skill_search_service.py",
        old="""            embeddings = await asyncio.to_thread(
                    self._file_source.file_embeddings_for, skill_id
                )""",
        new="            embeddings = self._file_source.file_embeddings_for(skill_id)",
        node=(
            "tests/unit/test_project_skill_source_phase4_wiring.py::"
            "TestStage2DualSourceAdapter::"
            "test_file_embeddings_bridged_off_the_loop_thread"
        ),
        note="to_thread bridge reverted to a direct call",
    ),
    # ------------------------------------------------------------------
    # W3 — gc_sweep has no production caller
    # ------------------------------------------------------------------
    Leg(
        id="W3",
        path="daemon/manager.py",
        old="""        if self._project_skill_source is not None:
            self._maintenance_service.register(
                "project_skill_gc",
                PROJECT_SKILL_GC_INTERVAL_HOURS,
                self._run_projectskill_gc,
            )""",
        new="        # MUTANT (W3): gc job registration removed",
        node=(
            "tests/unit/test_project_skill_source_phase4_wiring.py::"
            "TestGcMaintenanceWiring::test_gcjob_registered"
        ),
        note="maintenance registration removed",
    ),
    # ------------------------------------------------------------------
    # W4 — unconditional `source: db` suffix breaks OFF byte-identity
    # ------------------------------------------------------------------
    Leg(
        id="W4",
        path="daemon/tools/skill_tools.py",
        old='                db_suffix = " — source: db" if file_source is not None else ""',
        new='                db_suffix = " — source: db"',
        node=(
            "tests/tools/test_skill_tools_pf_surface.py::"
            "TestSkillListFiles::test_dbrows_tag_only_when_file_source_wired"
        ),
        note="unconditional suffix (pre-fix shape)",
    ),
    # ------------------------------------------------------------------
    # W5 — unvalidated `pf-<hex16>` feedback write (works knob-OFF)
    # ------------------------------------------------------------------
    Leg(
        id="W5",
        path="daemon/tools/skill_tools.py",
        old="""            if known_row is None or (
                getattr(known_row, "project_id", None) != pid
            ):""",
        new="""            if False:  # MUTANT (W5): id validation removed""",
        node=(
            "tests/tools/test_skill_tools_pf_surface.py::"
            "TestSkillFeedbackPf::test_pseudo_id_refused_when_not_indexed"
        ),
        note=(
            "pf-id validation removed. NOTE: the knob-OFF guard is NOT the "
            "discriminating layer for the OFF pin — with no file source "
            "``_resolve_pf_scope`` already returns (None, []) and refuses. "
            "The OFF guard is belt-and-suspenders on top of that; the "
            "load-bearing half of W5 is the index/project validation, "
            "which is the leg below."
        ),
    ),
    Leg(
        id="W5-off-guard",
        path="daemon/tools/skill_tools.py",
        old="            if file_source is None or not pid or not roots:",
        new="            if not pid or not roots:",
        node=(
            "tests/tools/test_skill_tools_pf_surface.py::"
            "TestSkillFeedbackPf::test_pseudo_id_refused_under_knob_off"
        ),
        belt=True,
        note=(
            "DEFENCE-IN-DEPTH leg — EXPECTED non-discriminating in the "
            "OFF shape (the scope resolver already fails closed). Recorded "
            "so the belt is visible, not silently omitted."
        ),
    ),
    # ------------------------------------------------------------------
    # W6 — body-FILE containment (revert the whole read-time fix: both
    # layers, since each alone suffices — that is the point of the belt)
    # ------------------------------------------------------------------
    Leg(
        id="W6",
        path="daemon/services/project_skill_source/service.py",
        old="""            skill_dir_real = os.path.realpath(
                os.path.join(
                    root_real, ".agents", meta.namespace, "skills", meta.name
                )
            )
            body_fail, body_real = _validate_file_inside_skill_dir(
                skill_dir_real, meta.body_path, "body"
            )
            if body_fail:
                raise ProjectSkillBodyUnavailable(meta.address, body_fail)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(body_real or body_path, flags)""",
        new="""            flags = os.O_RDONLY
            fd = os.open(body_path, flags)""",
        node=(
            "tests/unit/test_project_skill_source_index.py::"
            "TestReadTimeContainmentSwap::"
            "test_body_file_only_symlink_refused_at_read"
        ),
        note="body-file re-check + O_NOFOLLOW reverted (plain open)",
    ),
    # ------------------------------------------------------------------
    # Pre-existing tabulated legs (re-proven here; PACKS mutation table)
    # ------------------------------------------------------------------
    Leg(
        id="P4-M1",
        path="daemon/manager.py",
        old="file_source=self._project_skill_source,",
        new="file_source=None,",
        node=(
            "tests/unit/test_project_skill_source_phase4_wiring.py::"
            "TestWiringSites::test_site_1_search_ctor_threads_file_source"
        ),
        note="site-1 kwarg dropped (single occurrence mutated -> 2-site count goes 2->1)",
    ),
    Leg(
        id="P4-M3",
        path="daemon/services/skill_search_service.py",
        old="""        if self._config is None:
            result = self._degraded_select(reranked, max_results)
            result["degraded"] = "file_only\"""",
        new="""        if False and self._config is None:
            result = self._degraded_select(reranked, max_results)
            result["degraded"] = "file_only\"""",
        node=(
            "tests/unit/test_project_skill_source_phase4_wiring.py::"
            "TestM10FileOnly::test_stage3_skipped_no_llm_call"
        ),
        note="stage-3 deterministic skip disabled",
    ),
    Leg(
        id="P4-M4",
        path="daemon/api.py",
        old="skill_sources=_read_skill_sources_snapshot(manager),",
        new="skill_sources=None,",
        node=(
            "tests/unit/test_project_skill_source_phase4_readiness.py::"
            "TestApiSeamWiring::test_refresh_call_threads_skill_sources"
        ),
        note="api site-6 param dropped",
    ),
    Leg(
        id="P2-M4",
        path="daemon/services/skill_search_service.py",
        old="        return self._tag_result_sources(result, candidates)",
        new="        return result  # MUTANT: tagging seam disabled",
        node=(
            "tests/services/test_skill_search_fusion.py::"
            "TestSourceTagging::test_db_entries_tagged_when_fusion_active"
        ),
        note="source-tagging seam disabled",
    ),
    Leg(
        id="P3-M3",
        path="daemon/tools/skill_tools.py",
        old="""                instance_id=current_instance_id,
            )
            return json.dumps(result, default=_json_default, indent=2)""",
        new="""            )
            return json.dumps(result, default=_json_default, indent=2)""",
        node=(
            "tests/tools/test_skill_tools_pf_surface.py::"
            "TestActivation::test_search_threads_instance_id"
        ),
        note="ACTIVATION instance_id threading dropped",
    ),
]


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _run(node: str) -> int:
    env = os.environ.copy()
    for key in SCRUB:
        env.pop(key, None)
    cmd = [
        PY,
        "-m",
        "pytest",
        node,
        "-q",
        "--tb=no",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:randomly",
        "--timeout=120",
    ]
    return subprocess.run(
        cmd, cwd=WT, env=env, capture_output=True, text=True
    ).returncode


def _plant(leg: Leg) -> None:
    full = os.path.join(WT, leg.path)
    with open(full, encoding="utf-8") as fh:
        src = fh.read()
    hits = src.count(leg.old)
    if hits == 0:
        raise SystemExit(f"[{leg.id}] anchor NOT FOUND in {leg.path}")
    # P4-M1 mutates exactly ONE of the two identical site lines: that is
    # the defect (the remaining occurrence is what the 2-site count sees).
    mutated = src.replace(leg.old, leg.new, 1)
    if mutated == src:
        raise SystemExit(f"[{leg.id}] mutation was a NO-OP")
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(mutated)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--legs", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    wanted = {s.strip() for s in args.legs.split(",") if s.strip()}
    legs = [l for l in LEGS if not wanted or l.id in wanted]
    unknown = wanted - {l.id for l in LEGS}
    if unknown:
        raise SystemExit(f"unknown leg id(s): {sorted(unknown)}")

    rows: list[str] = ["leg\tfile\tnode\tplanted_sha\twith_defect\trestored_sha\trestored"]
    failures = 0
    for leg in legs:
        full = os.path.join(WT, leg.path)
        orig_sha = _sha(full)
        try:
            _plant(leg)
            planted_sha = _sha(full)
            red_rc = _run(leg.node)
        finally:
            restored = subprocess.run(
                ["git", "checkout", "--", leg.path], cwd=WT
            ).returncode
        restored_sha = _sha(full)
        green_rc = _run(leg.node)

        discriminating = red_rc != 0
        restore_ok = restored == 0 and restored_sha == orig_sha
        green_ok = green_rc == 0
        if leg.belt:
            verdict = "BELT (expected non-discriminating)" if (
                not discriminating and restore_ok and green_ok
            ) else "BELT-UNEXPECTED"
            if verdict != "BELT (expected non-discriminating)":
                failures += 1
        else:
            verdict = (
                "RED->GREEN"
                if discriminating and restore_ok and green_ok
                else "NON-DISCRIMINATING"
                if not discriminating
                else "RESTORE/GREEN-FAIL"
            )
            if verdict != "RED->GREEN":
                failures += 1
        rows.append(
            "\t".join(
                [
                    leg.id,
                    leg.path,
                    leg.node.rsplit("::", 1)[-1],
                    planted_sha[:12],
                    f"rc={red_rc}",
                    restored_sha[:12],
                    f"rc={green_rc} :: {verdict}",
                ]
            )
        )
        print(f"[{leg.id}] {verdict}  (with-defect rc={red_rc}, restored rc={green_rc})", flush=True)

    text = "\n".join(rows) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print()
    print(text)
    ok = len(legs) - failures
    belts = sum(1 for leg in legs if leg.belt)
    print(
        f"SUMMARY: {ok}/{len(legs)} legs accounted "
        f"({len(legs) - belts} discriminating legs RED->GREEN"
        + (f", {belts} belt leg(s) as expected)" if belts else ")")
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
