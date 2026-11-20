"""Push-doctrine content pins for the sonic family (sonic-push-doctrine).

Before this commission the sonic doctrine put release-lane git OUTSIDE the
loop: "The release lane lives outside the sonic loop" — coder commits, the
developer merges, and the actual push/stage cycle was an escalation at the
end ("ESCALATED at the push"). The sonic-push-doctrine commission moved the
release lane INSIDE the loop: sonic-developer owns the full end-of-task
cycle — merge the module branches, land the integration tip on
``origin/latest``, push the release tag, and stage the release — with a
LOAD-BEARING push order (tip first, then the tag, EXPLICIT refspecs, never
``git push --tags``) and a definition of done of MERGED + PUSHED + STAGED.

This file freezes BOTH directions of that change:

* **Presence anchors** — the new doctrine prose must stay verbatim in the
  merged tree (leader soul/rule/workflow/tools_note, developer
  rule/workflow/tools_note, all four coder files' hand-over sentence).
* **Boundary-intact anchors** — the leaves did NOT widen: coders still
  never push/merge/add -A/stash, the developer still never ``git add -A``
  / ``git stash`` and module branches are still never pushed by a coder,
  the tester is still no merge/push/tag/stash, the reviewer still never
  mutates files.
* **Whole-file ZERO pins** — the old doctrine stays dead: ZERO
  case-insensitive "outside the sonic loop" and "release-lane" in every
  sonic-leader prompt; ZERO "never a push" / "never push" in the
  developer's rule.md and tools_note.md (case-insensitive — the old
  phrasing was lowercase, and a capitalized re-entry is the same defect).

The pins read the WORKTREE copies (resolved relative to THIS file — never
an absolute main-checkout path) and match through a whitespace-normalized
``_contains`` identical in spirit to the shared pack's
``test/packs/sonic_agent_defs_checks.py::_contains`` (markdown re-wrap
safe).  Whole-file pins run through :func:`_doctrine_violations`, a PURE
checker over a ``{relpath: text}`` mapping — which is what makes the
mutation legs below real tests rather than comments.

Case note: the coder rule.md boundary phrase is pinned AS IT APPEARS in
the file — sentence-initial "Never ``git add -A``" — so the pin is exact
rather than case-folded.

Mutation legs (each plants ONE defect into an in-memory copy and proves
the checker REDs — no repo bytes are touched):

* M1  — reinsert "Release-lane git lives outside the sonic loop." into a
  leader file → RED (trips BOTH leader zero pins)
* M2  — delete the coder hand-over sentence → RED
* M3  — delete "PUSH ORDER IS LOAD-BEARING:" from the leader rule → RED
* M4  — weaken the coder bound (remove "never push, never merge") → RED
* M5  — weaken the developer bound (remove "never ``git add -A``") → RED
* M6  — add "never push" back to the developer rule → RED
* M7  — remove "no merge, no push, no tag" from the tester rule → RED
* M8  — remove the REMOTE-state clause from the leader tools_note → RED
* M9  — remove "runs WITHOUT ``--allow-stale-stage``" → RED
* M10 — remove the leader's ls-remote verification sentence → RED
* M11 — remove "NEVER mutate files" from the reviewer workflow → RED
* M12 — plant a bare "release-lane" in a leader workflow → RED
* M13 — remove the MERGED+PUSHED+STAGED definition of done → RED
* M14 — the clean tree → GREEN (non-vacuity control)

Deliberately NOT pinned here: the baseline row count (217 today) — counts
churn when agent dirs are added; the prompt-artifact SEAL freshness is the
A8 leg's job in the shared pack.  This file pins CONTENT, not cardinality.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every sonic prompt file this pin set addresses, keyed repo-relative.
_PROMPT_FILES: dict[str, Path] = {
    "agents/sonic-leader/soul.md": REPO_ROOT / "agents" / "sonic-leader" / "soul.md",
    "agents/sonic-leader/rule.md": REPO_ROOT / "agents" / "sonic-leader" / "rule.md",
    "agents/sonic-leader/workflow.md": REPO_ROOT / "agents" / "sonic-leader" / "workflow.md",
    "agents/sonic-leader/tools_note.md": REPO_ROOT / "agents" / "sonic-leader" / "tools_note.md",
    "agents/sonic-developer/rule.md": REPO_ROOT / "agents" / "sonic-developer" / "rule.md",
    "agents/sonic-developer/workflow.md": REPO_ROOT / "agents" / "sonic-developer" / "workflow.md",
    "agents/sonic-developer/tools_note.md": REPO_ROOT / "agents" / "sonic-developer" / "tools_note.md",
    "agents/sonic-coder/soul.md": REPO_ROOT / "agents" / "sonic-coder" / "soul.md",
    "agents/sonic-coder/rule.md": REPO_ROOT / "agents" / "sonic-coder" / "rule.md",
    "agents/sonic-coder/workflow.md": REPO_ROOT / "agents" / "sonic-coder" / "workflow.md",
    "agents/sonic-coder/tools_note.md": REPO_ROOT / "agents" / "sonic-coder" / "tools_note.md",
    "agents/sonic-tester/rule.md": REPO_ROOT / "agents" / "sonic-tester" / "rule.md",
    "agents/sonic-reviewer/workflow.md": REPO_ROOT / "agents" / "sonic-reviewer" / "workflow.md",
}

_LEADER_FILES = (
    "agents/sonic-leader/soul.md",
    "agents/sonic-leader/rule.md",
    "agents/sonic-leader/workflow.md",
    "agents/sonic-leader/tools_note.md",
)
_CODER_FILES = (
    "agents/sonic-coder/soul.md",
    "agents/sonic-coder/rule.md",
    "agents/sonic-coder/workflow.md",
    "agents/sonic-coder/tools_note.md",
)

# ---------------------------------------------------------------------------
# PRESENCE ANCHORS — the new doctrine, verbatim from the merged tree.
# ---------------------------------------------------------------------------

#: Leader soul.md — the "What I Do Not Do" commit bullet's release-lane tail.
_ANCHOR_LEADER_SOUL_RELEASE_LANE = (
    "The release lane is INSIDE the sonic loop now: the developer lands the "
    "integration tip on `origin/latest`, pushes the release tag, and stages "
    "the release."
)

#: Leader rule.md rule 7 — the release lane moved inside the sonic loop.
_ANCHOR_LEADER_RULE_RELEASE_LANE = (
    "The release lane is INSIDE the sonic loop: sonic-developer owns the "
    "full end-of-task cycle — merge the module branches, push the "
    "integration tip to `origin/latest`, push the release tag, and stage "
    "the release."
)

#: Leader rule.md rule 7 — the definition of done.
_ANCHOR_LEADER_RULE_DOD = (
    "The definition of done for a sonic commission is MERGED + PUSHED + STAGED"
)

#: Leader rule.md rule 8 — the load-bearing push order.
_ANCHOR_LEADER_RULE_PUSH_ORDER = (
    "PUSH ORDER IS LOAD-BEARING: push `origin latest` FIRST, then the tag, "
    "with EXPLICIT refspecs — `git push origin <integration-branch>:latest` "
    "then `git push origin <tag>` — and NEVER `git push --tags` (it would "
    "sweep unrelated local tags)."
)

#: Leader workflow.md §5 — the read-only PUSHED-state verification.
_ANCHOR_LEADER_WF_LS_REMOTE = (
    "I verify the PUSHED state read-only: `git ls-remote origin "
    "refs/heads/latest` and `git ls-remote --tags origin "
    "refs/tags/<version>` must BOTH show the integration tip and the "
    "annotated tag (with its `^{}` peeled ref) before I accept the run."
)

#: Leader tools_note.md bash row — REMOTE state is part of the read surface.
_ANCHOR_LEADER_TN_REMOTE_STATE = (
    "AND the REMOTE state (`git ls-remote --heads --tags origin`)"
)

#: Developer rule.md — the end-of-task cycle is the developer's, in full.
_ANCHOR_DEV_RULE_END_OF_TASK = (
    "The end-of-task cycle is MINE, in full: merge the module branches "
    "(`merge --no-ff`), land the integration tip on the latest base, push "
    "`origin latest`, push the release tag, and stage the release."
)

#: Developer rule.md — the definition of done, with the old escalation gone.
_ANCHOR_DEV_RULE_DOD = (
    "A sonic commission's definition of done is MERGED + PUSHED + STAGED "
    "— not ESCALATED at the push."
)

#: Developer rule.md — push discipline lead.
_ANCHOR_DEV_RULE_PUSH_ORDER = (
    "PUSH ORDER: push `origin latest` FIRST, then the tag, with EXPLICIT refspecs"
)

#: Developer rule.md — finalize: fetch, never force-push.
_ANCHOR_DEV_RULE_FINALIZE = (
    "FINALIZE: `git fetch origin latest` before the push; if `origin/latest` "
    "MOVED since the lane base, merge it into the integration branch and "
    "re-run the gates — NEVER force-push"
)

#: Developer rule.md — the pushed tag is the evidence.
_ANCHOR_DEV_RULE_TAG_EVIDENCE = (
    "The pushed tag is the evidence: `git ls-remote --tags origin "
    "refs/tags/<version>` must show BOTH the annotated object and its `^{}` "
    "peeled ref. A local-only tag is NOT a finished release."
)

#: Developer workflow.md §5.1 — the stage runs without the stale override.
_ANCHOR_DEV_WF_ALLOW_STALE = (
    "`stage.sh live` runs WITHOUT `--allow-stale-stage` once the tip and "
    "the tag are pushed"
)

#: Developer workflow.md §5.1 — isolated clean worktree at the tag.
_ANCHOR_DEV_WF_STAGE_WORKTREE = (
    "The stage happens from an isolated clean worktree detached at the tag, "
    "with `dist/` and `frontend/dist/` removed first"
)

#: Developer tools_note.md bash row — explicit refspecs, no tag sweep, no force.
_ANCHOR_DEV_TN_NO_TAG_SWEEP = "never `git push --tags`"
_ANCHOR_DEV_TN_NO_FORCE = "never force-push"

#: Coder hand-over sentence — ALL FOUR coder files must carry it.
_ANCHOR_CODER_HANDOVER = (
    "The push and the release stage are the developer's too — I hand over a "
    "clean branch tip, and the developer lands it."
)

# ---------------------------------------------------------------------------
# BOUNDARY-INTACT ANCHORS — the doctrine change must NOT have widened the
# leaves.  Each is pinned exactly as it appears in its file.
# ---------------------------------------------------------------------------

#: Coder rule.md — write-and-commit discipline (sentence-initial case is
#: the file's actual bytes; see module docstring case note).
_ANCHOR_CODER_RULE_BOUND = (
    "Never `git add -A`, never `git stash`, never push, never merge"
)
#: Coder workflow.md step 5 — the no-push/no-merge tail.
_ANCHOR_CODER_WF_BOUND = "no push, no merge"
#: Coder soul.md + tools_note.md — "never a push" survives.
_ANCHOR_CODER_SOUL_TN_BOUND = "never a push"
#: Developer workflow.md §5 — module branches are never pushed by a coder.
_ANCHOR_DEV_WF_CODER_BOUND = (
    "Module branches are NEVER pushed and never merged by a coder"
)
#: Developer rule.md + coder rule.md — add -A / stash stay banned.
_ANCHOR_ADD_A = "never `git add -A`"
_ANCHOR_NO_STASH = "never `git stash`"
#: Tester rule.md — git writes bounded (whitespace-normalized; wraps in-file).
_ANCHOR_TESTER_BOUND = "no merge, no push, no tag, no `git stash`"
#: Reviewer workflow.md — read-only bash.
_ANCHOR_REVIEWER_BOUND = "NEVER mutate files"

# ---------------------------------------------------------------------------
# ZERO PINS — the OLD doctrine must stay dead.
# ---------------------------------------------------------------------------

#: Leader files: ZERO case-insensitive hits, per file.
_OLD_OUTSIDE_LOOP = "outside the sonic loop"
_OLD_RELEASE_LANE = "release-lane"
#: Developer rule.md + tools_note.md: ZERO case-insensitive hits, per file.
_OLD_NEVER_A_PUSH = "never a push"
_OLD_NEVER_PUSH = "never push"


def _norm(text: str) -> str:
    """Collapse all whitespace runs to single spaces (markdown re-wrap safe)."""
    return " ".join(text.split())


def _contains(haystack: str, needle: str) -> bool:
    """Whitespace-normalized containment, in the shared pack's spirit."""
    return _norm(needle) in _norm(haystack)


def _read_all(texts: dict[str, str] | None = None) -> dict[str, str]:
    """The pinned prompt bodies, or the caller's in-memory stand-ins."""
    if texts is not None:
        return texts
    return {
        rel: path.read_text(encoding="utf-8")
        for rel, path in _PROMPT_FILES.items()
    }


def _doctrine_violations(texts: dict[str, str] | None = None) -> list[str]:
    """PURE checker: every push-doctrine pin violation, as messages.

    Returns ``[]`` on a clean tree.  Deliberately pure over a
    ``{relpath: text}`` mapping so the mutation legs can plant a defect
    into an in-memory copy and prove the checker catches it — an
    assertion that can never fail is not a pin.
    """
    bodies = _read_all(texts)
    problems: list[str] = []

    def miss(rel: str, anchor: str, what: str) -> None:
        if not _contains(bodies[rel], anchor):
            problems.append(f"{rel}: {what} anchor MISSING: {anchor[:60]!r}")

    # Presence anchors — the new doctrine.
    miss(
        "agents/sonic-leader/soul.md",
        _ANCHOR_LEADER_SOUL_RELEASE_LANE,
        "release-lane-inside-loop",
    )
    miss(
        "agents/sonic-leader/rule.md",
        _ANCHOR_LEADER_RULE_RELEASE_LANE,
        "release-lane-inside-loop",
    )
    miss("agents/sonic-leader/rule.md", _ANCHOR_LEADER_RULE_DOD, "definition-of-done")
    miss("agents/sonic-leader/rule.md", _ANCHOR_LEADER_RULE_PUSH_ORDER, "push-order")
    miss(
        "agents/sonic-leader/workflow.md",
        _ANCHOR_LEADER_WF_LS_REMOTE,
        "pushed-state-verification",
    )
    miss(
        "agents/sonic-leader/tools_note.md",
        _ANCHOR_LEADER_TN_REMOTE_STATE,
        "remote-state-read",
    )
    for rel, anchor, what in (
        ("agents/sonic-developer/rule.md", _ANCHOR_DEV_RULE_END_OF_TASK, "end-of-task-cycle"),
        ("agents/sonic-developer/rule.md", _ANCHOR_DEV_RULE_DOD, "definition-of-done"),
        ("agents/sonic-developer/rule.md", _ANCHOR_DEV_RULE_PUSH_ORDER, "push-order"),
        ("agents/sonic-developer/rule.md", _ANCHOR_DEV_RULE_FINALIZE, "finalize-fetch"),
        ("agents/sonic-developer/rule.md", _ANCHOR_DEV_RULE_TAG_EVIDENCE, "tag-evidence"),
        ("agents/sonic-developer/workflow.md", _ANCHOR_DEV_WF_ALLOW_STALE, "no-allow-stale-stage"),
        ("agents/sonic-developer/workflow.md", _ANCHOR_DEV_WF_STAGE_WORKTREE, "stage-worktree"),
        ("agents/sonic-developer/tools_note.md", _ANCHOR_DEV_TN_NO_TAG_SWEEP, "no-tag-sweep"),
        ("agents/sonic-developer/tools_note.md", _ANCHOR_DEV_TN_NO_FORCE, "no-force-push"),
    ):
        miss(rel, anchor, what)
    for rel in _CODER_FILES:
        miss(rel, _ANCHOR_CODER_HANDOVER, "coder-hand-over")

    # Boundary-intact anchors — the leaves did not widen.  coder/rule.md is
    # pinned as ONE exact-case sentence (its "Never `git add -A`" is
    # sentence-initial — see module docstring case note).
    miss("agents/sonic-coder/rule.md", _ANCHOR_CODER_RULE_BOUND, "coder-bound")
    miss("agents/sonic-coder/workflow.md", _ANCHOR_CODER_WF_BOUND, "coder-bound")
    miss("agents/sonic-coder/soul.md", _ANCHOR_CODER_SOUL_TN_BOUND, "coder-bound")
    miss("agents/sonic-coder/tools_note.md", _ANCHOR_CODER_SOUL_TN_BOUND, "coder-bound")
    miss("agents/sonic-developer/workflow.md", _ANCHOR_DEV_WF_CODER_BOUND, "coder-bound")
    miss("agents/sonic-developer/rule.md", _ANCHOR_ADD_A, "developer-bound")
    miss("agents/sonic-developer/rule.md", _ANCHOR_NO_STASH, "developer-bound")
    miss("agents/sonic-tester/rule.md", _ANCHOR_TESTER_BOUND, "tester-bound")
    miss("agents/sonic-reviewer/workflow.md", _ANCHOR_REVIEWER_BOUND, "reviewer-bound")

    # Whole-file ZERO pins — the old doctrine stays dead.
    for rel in _LEADER_FILES:
        for phrase in (_OLD_OUTSIDE_LOOP, _OLD_RELEASE_LANE):
            hits = re.findall(re.escape(phrase), bodies[rel], flags=re.IGNORECASE)
            if hits:
                problems.append(
                    f"{rel}: {len(hits)} case-insensitive {phrase!r} "
                    f"occurrence(s), expected 0 (the old doctrine must stay dead)"
                )
    for rel in ("agents/sonic-developer/rule.md", "agents/sonic-developer/tools_note.md"):
        for phrase in (_OLD_NEVER_A_PUSH, _OLD_NEVER_PUSH):
            hits = re.findall(re.escape(phrase), bodies[rel], flags=re.IGNORECASE)
            if hits:
                problems.append(
                    f"{rel}: {len(hits)} case-insensitive {phrase!r} "
                    f"occurrence(s), expected 0 (a coder-side push ban does "
                    f"not belong in the developer prompts anymore)"
                )

    return problems


class TestPushDoctrinePresenceAnchors:
    """The new doctrine prose must stay verbatim in the merged tree."""

    def test_leader_soul_release_lane_inside_loop(self):
        text = _PROMPT_FILES["agents/sonic-leader/soul.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_LEADER_SOUL_RELEASE_LANE)

    def test_leader_rule_release_lane_inside_loop(self):
        text = _PROMPT_FILES["agents/sonic-leader/rule.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_LEADER_RULE_RELEASE_LANE)

    def test_leader_rule_definition_of_done(self):
        text = _PROMPT_FILES["agents/sonic-leader/rule.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_LEADER_RULE_DOD)

    def test_leader_rule_push_order_load_bearing(self):
        text = _PROMPT_FILES["agents/sonic-leader/rule.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_LEADER_RULE_PUSH_ORDER)

    def test_leader_workflow_pushed_state_verification(self):
        text = _PROMPT_FILES["agents/sonic-leader/workflow.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_LEADER_WF_LS_REMOTE)

    def test_leader_tools_note_remote_state_in_read_surface(self):
        text = _PROMPT_FILES["agents/sonic-leader/tools_note.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_LEADER_TN_REMOTE_STATE)

    def test_developer_rule_end_of_task_cycle(self):
        text = _PROMPT_FILES["agents/sonic-developer/rule.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_DEV_RULE_END_OF_TASK)

    def test_developer_rule_definition_of_done_not_escalated(self):
        text = _PROMPT_FILES["agents/sonic-developer/rule.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_DEV_RULE_DOD)

    def test_developer_rule_push_order(self):
        text = _PROMPT_FILES["agents/sonic-developer/rule.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_DEV_RULE_PUSH_ORDER)

    def test_developer_rule_finalize_never_force_push(self):
        text = _PROMPT_FILES["agents/sonic-developer/rule.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_DEV_RULE_FINALIZE)

    def test_developer_rule_pushed_tag_evidence(self):
        text = _PROMPT_FILES["agents/sonic-developer/rule.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_DEV_RULE_TAG_EVIDENCE)

    def test_developer_workflow_stage_without_allow_stale(self):
        text = _PROMPT_FILES["agents/sonic-developer/workflow.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_DEV_WF_ALLOW_STALE)

    def test_developer_workflow_stage_from_isolated_worktree(self):
        text = _PROMPT_FILES["agents/sonic-developer/workflow.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_DEV_WF_STAGE_WORKTREE)

    def test_developer_tools_note_no_tag_sweep_no_force(self):
        text = _PROMPT_FILES["agents/sonic-developer/tools_note.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_DEV_TN_NO_TAG_SWEEP)
        assert _contains(text, _ANCHOR_DEV_TN_NO_FORCE)

    def test_all_four_coder_files_carry_the_hand_over_sentence(self):
        for rel in _CODER_FILES:
            text = _PROMPT_FILES[rel].read_text(encoding="utf-8")
            assert _contains(text, _ANCHOR_CODER_HANDOVER), (
                f"{rel} lost the coder hand-over sentence"
            )


class TestBoundaryIntact:
    """The doctrine change moved the release lane IN — it must not have
    widened the leaves OUT."""

    def test_coder_rule_still_never_push_never_merge(self):
        text = _PROMPT_FILES["agents/sonic-coder/rule.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_CODER_RULE_BOUND)

    def test_coder_workflow_still_no_push_no_merge(self):
        text = _PROMPT_FILES["agents/sonic-coder/workflow.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_CODER_WF_BOUND)

    def test_coder_soul_and_tools_note_still_never_a_push(self):
        for rel in ("agents/sonic-coder/soul.md", "agents/sonic-coder/tools_note.md"):
            text = _PROMPT_FILES[rel].read_text(encoding="utf-8")
            assert _contains(text, _ANCHOR_CODER_SOUL_TN_BOUND), f"{rel} weakened"

    def test_coder_rule_still_bans_add_a_and_stash(self):
        text = _PROMPT_FILES["agents/sonic-coder/rule.md"].read_text(encoding="utf-8")
        # exact-case: the file carries the phrase sentence-initially
        assert _contains(text, "Never `git add -A`")
        assert _contains(text, _ANCHOR_NO_STASH)

    def test_developer_workflow_module_branches_still_never_pushed_by_coder(self):
        text = _PROMPT_FILES["agents/sonic-developer/workflow.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_DEV_WF_CODER_BOUND)

    def test_developer_rule_still_bans_add_a_and_stash(self):
        text = _PROMPT_FILES["agents/sonic-developer/rule.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_ADD_A)
        assert _contains(text, _ANCHOR_NO_STASH)

    def test_tester_rule_still_no_merge_no_push_no_tag_no_stash(self):
        text = _PROMPT_FILES["agents/sonic-tester/rule.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_TESTER_BOUND)

    def test_reviewer_workflow_still_never_mutates_files(self):
        text = _PROMPT_FILES["agents/sonic-reviewer/workflow.md"].read_text(encoding="utf-8")
        assert _contains(text, _ANCHOR_REVIEWER_BOUND)


class TestOldDoctrineStaysDead:
    """Whole-file ZERO pins."""

    def test_checker_is_green_on_the_clean_tree(self):
        """Non-vacuity control: the real tree satisfies the whole checker."""
        assert _doctrine_violations() == []

    def test_no_leader_file_says_outside_the_sonic_loop(self):
        for rel in _LEADER_FILES:
            text = _PROMPT_FILES[rel].read_text(encoding="utf-8")
            hits = re.findall(re.escape(_OLD_OUTSIDE_LOOP), text, flags=re.IGNORECASE)
            assert hits == [], f"{rel} carries {len(hits)} old-doctrine hit(s)"

    def test_no_leader_file_says_release_lane_hyphenated(self):
        for rel in _LEADER_FILES:
            text = _PROMPT_FILES[rel].read_text(encoding="utf-8")
            hits = re.findall(re.escape(_OLD_RELEASE_LANE), text, flags=re.IGNORECASE)
            assert hits == [], f"{rel} carries {len(hits)} old-doctrine hit(s)"

    def test_developer_rule_and_tools_note_have_no_coder_push_ban(self):
        for rel in ("agents/sonic-developer/rule.md", "agents/sonic-developer/tools_note.md"):
            text = _PROMPT_FILES[rel].read_text(encoding="utf-8")
            for phrase in (_OLD_NEVER_A_PUSH, _OLD_NEVER_PUSH):
                hits = re.findall(re.escape(phrase), text, flags=re.IGNORECASE)
                assert hits == [], f"{rel} carries {len(hits)} {phrase!r} hit(s)"


class TestMutationLegs:
    """Each leg plants ONE defect into an IN-MEMORY copy and proves the
    checker REDs.  No repo bytes are touched."""

    @staticmethod
    def _clean() -> dict[str, str]:
        return _read_all()

    def test_M1_reinserting_outside_the_sonic_loop_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-leader/soul.md"] += (
            "\nRelease-lane git lives outside the sonic loop.\n"
        )
        problems = _doctrine_violations(texts)
        assert problems, "the reinserted old-doctrine sentence was NOT caught"
        assert any("agents/sonic-leader/soul.md" in prob for prob in problems), problems

    def test_M2_deleting_the_coder_hand_over_sentence_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-coder/workflow.md"] = texts[
            "agents/sonic-coder/workflow.md"
        ].replace(_ANCHOR_CODER_HANDOVER, "", 1)
        problems = _doctrine_violations(texts)
        assert problems, "the deleted hand-over sentence was NOT caught"
        assert any(
            "agents/sonic-coder/workflow.md" in prob and "coder-hand-over" in prob
            for prob in problems
        ), problems

    def test_M3_deleting_push_order_is_load_bearing_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-leader/rule.md"] = texts[
            "agents/sonic-leader/rule.md"
        ].replace("PUSH ORDER IS LOAD-BEARING:", "PUSH ORDER:", 1)
        problems = _doctrine_violations(texts)
        assert problems, "the weakened push-order anchor was NOT caught"
        assert any(
            "agents/sonic-leader/rule.md" in prob and "push-order" in prob
            for prob in problems
        ), problems

    def test_M4_weakening_the_coder_bound_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-coder/rule.md"] = texts[
            "agents/sonic-coder/rule.md"
        ].replace("never push, never merge", "", 1)
        problems = _doctrine_violations(texts)
        assert problems, "the weakened coder bound was NOT caught"
        assert any(
            "agents/sonic-coder/rule.md" in prob and "coder-bound" in prob
            for prob in problems
        ), problems

    def test_M5_weakening_the_developer_bound_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-developer/rule.md"] = texts[
            "agents/sonic-developer/rule.md"
        ].replace(_ANCHOR_ADD_A, "", 1)
        problems = _doctrine_violations(texts)
        assert problems, "the weakened developer bound was NOT caught"
        assert any(
            "agents/sonic-developer/rule.md" in prob and "developer-bound" in prob
            for prob in problems
        ), problems

    def test_M6_adding_never_push_back_to_developer_rule_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-developer/rule.md"] += "\nnever push\n"
        problems = _doctrine_violations(texts)
        assert problems, "the re-added coder push ban was NOT caught"
        assert any(
            "agents/sonic-developer/rule.md" in prob and "'never push'" in prob
            for prob in problems
        ), problems

    def test_M7_removing_the_tester_bound_goes_red(self):
        texts = self._clean()
        # the tester phrase wraps in-file — collapse whitespace in-memory
        texts["agents/sonic-tester/rule.md"] = re.sub(
            r"no\s+merge,\s+no\s+push,\s+no\s+tag,\s+no\s+`git\s+stash`",
            "",
            texts["agents/sonic-tester/rule.md"],
            count=1,
        )
        problems = _doctrine_violations(texts)
        assert problems, "the removed tester bound was NOT caught"
        assert any(
            "agents/sonic-tester/rule.md" in prob and "tester-bound" in prob
            for prob in problems
        ), problems

    def test_M8_removing_the_remote_state_clause_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-leader/tools_note.md"] = texts[
            "agents/sonic-leader/tools_note.md"
        ].replace(_ANCHOR_LEADER_TN_REMOTE_STATE, "", 1)
        problems = _doctrine_violations(texts)
        assert problems, "the removed REMOTE-state clause was NOT caught"
        assert any(
            "agents/sonic-leader/tools_note.md" in prob and "remote-state-read" in prob
            for prob in problems
        ), problems

    def test_M9_allowing_allow_stale_stage_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-developer/workflow.md"] = texts[
            "agents/sonic-developer/workflow.md"
        ].replace("runs WITHOUT `--allow-stale-stage`", "runs WITH `--allow-stale-stage`", 1)
        problems = _doctrine_violations(texts)
        assert problems, "the re-enabled --allow-stale-stage was NOT caught"
        assert any(
            "agents/sonic-developer/workflow.md" in prob and "no-allow-stale-stage" in prob
            for prob in problems
        ), problems

    def test_M10_removing_the_pushed_state_verification_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-leader/workflow.md"] = texts[
            "agents/sonic-leader/workflow.md"
        ].replace(_ANCHOR_LEADER_WF_LS_REMOTE, "", 1)
        problems = _doctrine_violations(texts)
        assert problems, "the removed ls-remote verification was NOT caught"
        assert any(
            "agents/sonic-leader/workflow.md" in prob
            and "pushed-state-verification" in prob
            for prob in problems
        ), problems

    def test_M11_letting_the_reviewer_mutate_files_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-reviewer/workflow.md"] = texts[
            "agents/sonic-reviewer/workflow.md"
        ].replace(_ANCHOR_REVIEWER_BOUND, "", 1)
        problems = _doctrine_violations(texts)
        assert problems, "the removed reviewer bound was NOT caught"
        assert any(
            "agents/sonic-reviewer/workflow.md" in prob and "reviewer-bound" in prob
            for prob in problems
        ), problems

    def test_M12_planting_release_lane_in_a_leader_workflow_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-leader/workflow.md"] += "\nrelease-lane boundaries below\n"
        problems = _doctrine_violations(texts)
        assert problems, "the planted 'release-lane' was NOT caught"
        assert any(
            "agents/sonic-leader/workflow.md" in prob and "'release-lane'" in prob
            for prob in problems
        ), problems

    def test_M13_deleting_the_definition_of_done_goes_red(self):
        texts = self._clean()
        texts["agents/sonic-leader/rule.md"] = texts[
            "agents/sonic-leader/rule.md"
        ].replace(_ANCHOR_LEADER_RULE_DOD, "The definition of done is TALKED ABOUT", 1)
        problems = _doctrine_violations(texts)
        assert problems, "the deleted definition of done was NOT caught"
        assert any(
            "agents/sonic-leader/rule.md" in prob and "definition-of-done" in prob
            for prob in problems
        ), problems

    def test_M14_clean_tree_stays_green(self):
        """The control leg — proves the REDs above are the planted defect,
        not a checker that always fires."""
        assert _doctrine_violations(self._clean()) == []
