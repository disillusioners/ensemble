"""The declared A/B disposition manifest — corrected per-pair data.

The sweep posture is NO knob: diverts come from an EXPLICIT,
operator/leader-named disposition — never from sweep judgment. This
manifest carries the SIX declared pairs of the E-6 pending-wiring
table and, since the 2026-11-18 adjudication, the per-pair EVIDENCE
that says which of them actually authorise a divert.

The pins here are the SEED contract:

* exactly six pairs, in the leader's order;
* each pair names its ``skill_ab_tests`` PAIR-ROW id and the declared
  winner SKILL id (plus the group id and the pair's other member);
* every entry declares a machine-checkable ``status``;
* NO entry may assert a defect without a NAMED finding id (anomaly
  uuid and/or POK slug) — a bare "verified-defective" claim fails
  loudly;
* an entry marked "no finding" carries an explicit none/no-finding
  marker — never an omission;
* entries whose claim is NOT supported are NOT divert-authorized —
  the plain E-6 default applies and they must never be
  blanket-diverted;
* the original (pre-adjudication) claim is preserved per entry, with
  the correction date + note;
* the manifest is DATA — it mutates nothing, and no shipped daemon
  module imports it (the sanctioned resolution stays an explicit,
  operator-driven act with its own reason + receipt);
* import-time inert (no I/O at import).
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SOURCE = (
    REPO_ROOT / "daemon" / "services" / "skill_ab_disposition_manifest.py"
)
MANAGER_SOURCE = REPO_ROOT / "daemon" / "manager.py"

#: The six pairs in the leader's order: (pair-row id prefix, declared
#: winner skill id prefix).
EXPECTED_ROW_TO_WINNER = (
    ("1b8e4d80", "8bb22eb8"),
    ("8f8cee3a", "08e67030"),
    ("b77128c5", "76e3e1a7"),
    ("604550ac", "c595f7cf"),
    ("4ba4086d", "0757f082"),
    ("f3bc3a3e", "607425ef"),
)

#: The full per-pair truth (pair-row id prefix -> (group id prefix,
#: declared winner skill id prefix, status)). A drifted id FAILS
#: LOUDLY instead of silently mismatching.
EXPECTED_PAIR_TRUTH = {
    "1b8e4d80": ("58c601e0", "8bb22eb8", "no-finding"),
    "8f8cee3a": ("a617317b", "08e67030", "not-verified-defective"),
    "b77128c5": ("ee28042c", "76e3e1a7", "not-verified-defective"),
    "604550ac": ("8e9853e9", "c595f7cf", "verified-defective"),
    "4ba4086d": ("39a27f20", "0757f082", "verified-defective"),
    "f3bc3a3e": ("bddd6559", "607425ef", "verified-defective"),
}

#: The three pairs whose original "verified-defective" claim did NOT
#: hold — they must never be blanket-diverted.
UNSUPPORTED_ROW_IDS = ("1b8e4d80", "8f8cee3a", "b77128c5")

#: The three pairs whose defect claim IS supported by named evidence.
SUPPORTED_ROW_IDS = ("604550ac", "4ba4086d", "f3bc3a3e")

#: An anomaly evidence id: ``anomaly:`` + a uuid (or its 8-char
#: prefix).
_ANOMALY_EVIDENCE_RE = re.compile(
    r"^anomaly:[0-9a-f]{8}"
    r"(-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?$"
)
#: A POK evidence id: ``pok:`` + a lowercase kebab slug.
_POK_EVIDENCE_RE = re.compile(r"^pok:[a-z0-9][a-z0-9-]*$")

#: The literal former field name, built at runtime so this file never
#: contains the string it asserts is gone.
_RETIRED_FIELD_NAME = "defective" + "_skill_id_prefix"


# --------------------------------------------------------------------------
# (b) the static per-pair truth pin
# --------------------------------------------------------------------------
def test_manifest_carries_exactly_the_six_declared_pairs():
    from daemon.services.skill_ab_disposition_manifest import (
        DECLARED_AB_DISPOSITIONS,
    )

    assert len(DECLARED_AB_DISPOSITIONS) == 6
    observed = tuple(
        (d.ab_test_row_id_prefix, d.winner_skill_id_prefix)
        for d in DECLARED_AB_DISPOSITIONS
    )
    assert observed == EXPECTED_ROW_TO_WINNER


def test_row_ids_map_to_expected_group_winner_and_status():
    """The per-pair truth pin — a drifted id FAILS LOUDLY.

    Pure/static: no database is required, so this pin can never be
    skipped for environmental reasons.
    """
    from daemon.services.skill_ab_disposition_manifest import (
        DECLARED_AB_DISPOSITIONS,
    )

    observed = {
        d.ab_test_row_id_prefix: (
            d.ab_test_group_prefix,
            d.winner_skill_id_prefix,
            d.status,
        )
        for d in DECLARED_AB_DISPOSITIONS
    }
    assert observed == EXPECTED_PAIR_TRUTH, (
        "the manifest's per-pair ids/status drifted from the "
        "adjudicated truth — a pair-row id, group id, winner id or "
        "status was changed"
    )


def test_every_pair_carries_its_group_and_other_member():
    """Every pair names the group id (what the sanctioned resolver
    takes) and the pair's other member — the NEWER lineage
    generation, i.e. the member the plain E-6 default would crown."""
    from daemon.services.skill_ab_disposition_manifest import (
        DECLARED_AB_DISPOSITIONS,
    )

    for d in DECLARED_AB_DISPOSITIONS:
        assert d.ab_test_group_prefix.strip(), (
            f"{d.ab_test_row_id_prefix}: the group id is missing"
        )
        assert d.newer_member_skill_id_prefix.strip(), (
            f"{d.ab_test_row_id_prefix}: the other member is missing"
        )
        assert d.newer_member_skill_id_prefix != d.winner_skill_id_prefix, (
            f"{d.ab_test_row_id_prefix}: the pair's two members must "
            "differ"
        )


# --------------------------------------------------------------------------
# (a) no defect claim without a NAMED finding
# --------------------------------------------------------------------------
def test_no_entry_asserts_verified_defective_without_named_evidence():
    """The core data pin — a defect claim REQUIRES a named finding.

    A bare "verified-defective" claim (no evidence id) fails here
    loudly; so does an entry whose status is not one of the three
    declared statuses.
    """
    from daemon.services.skill_ab_disposition_manifest import (
        DECLARED_AB_DISPOSITIONS,
        NO_FINDING_MARKER,
        STATUS_NO_FINDING,
        STATUS_NOT_VERIFIED_DEFECTIVE,
        STATUS_VERIFIED_DEFECTIVE,
    )

    known_statuses = {
        STATUS_VERIFIED_DEFECTIVE,
        STATUS_NOT_VERIFIED_DEFECTIVE,
        STATUS_NO_FINDING,
    }
    for d in DECLARED_AB_DISPOSITIONS:
        assert d.status in known_statuses, (
            f"{d.ab_test_row_id_prefix}: the status {d.status!r} is "
            "not one of the declared machine-checkable statuses — a "
            "defect claim must be declared, never implied"
        )
        assert d.evidence, (
            f"{d.ab_test_row_id_prefix}: the evidence list is empty — "
            "a claim must name its finding"
        )
        if d.status == STATUS_VERIFIED_DEFECTIVE:
            named = d.named_evidence_ids
            assert named, (
                f"{d.ab_test_row_id_prefix}: a verified-defective claim "
                "carries NO named evidence id — this is the bare-claim "
                "failure the manifest exists to prevent"
            )
            assert NO_FINDING_MARKER not in d.evidence, (
                f"{d.ab_test_row_id_prefix}: a verified-defective "
                "claim must not carry the no-finding marker"
            )
        if d.status == STATUS_NO_FINDING:
            assert d.evidence == (NO_FINDING_MARKER,), (
                f"{d.ab_test_row_id_prefix}: a no-finding entry must "
                "carry the explicit no-finding marker, never an "
                "omission"
            )
        if d.status == STATUS_NOT_VERIFIED_DEFECTIVE:
            assert d.named_evidence_ids, (
                f"{d.ab_test_row_id_prefix}: an unsupported claim must "
                "still name what WAS found"
            )


def test_every_evidence_id_is_well_formed():
    """Each evidence id is an ``anomaly:<uuid>`` / ``pok:<slug>`` /
    explicit no-finding marker — never a free-text blob."""
    from daemon.services.skill_ab_disposition_manifest import (
        DECLARED_AB_DISPOSITIONS,
        NO_FINDING_MARKER,
        STATUS_NO_FINDING,
    )

    for d in DECLARED_AB_DISPOSITIONS:
        for evidence_id in d.evidence:
            if evidence_id == NO_FINDING_MARKER:
                assert d.status == STATUS_NO_FINDING, (
                    f"{d.ab_test_row_id_prefix}: the no-finding marker "
                    "is only legal on a no-finding entry"
                )
                continue
            assert (
                _ANOMALY_EVIDENCE_RE.match(evidence_id)
                or _POK_EVIDENCE_RE.match(evidence_id)
            ), (
                f"{d.ab_test_row_id_prefix}: evidence id "
                f"{evidence_id!r} is neither an anomaly uuid nor a POK "
                "slug — the evidence must be machine-checkable"
            )


def test_every_entry_carries_a_substantive_finding():
    """The finding says WHAT WAS FOUND — never a bare status token."""
    from daemon.services.skill_ab_disposition_manifest import (
        DECLARED_AB_DISPOSITIONS,
        STATUS_NO_FINDING,
        STATUS_NOT_VERIFIED_DEFECTIVE,
        STATUS_VERIFIED_DEFECTIVE,
    )

    for d in DECLARED_AB_DISPOSITIONS:
        finding = d.finding.strip()
        assert finding, (
            f"{d.ab_test_row_id_prefix}: the finding is empty"
        )
        assert finding not in {
            STATUS_VERIFIED_DEFECTIVE,
            STATUS_NOT_VERIFIED_DEFECTIVE,
            STATUS_NO_FINDING,
        }, (
            f"{d.ab_test_row_id_prefix}: the finding must name WHAT "
            "was found, not repeat the status token"
        )


def test_supported_and_unsupported_pairs_are_separated():
    """Only the SUPPORTED pairs are divert-authorized.

    This is the guard against the blanket-apply hazard: an operator
    (or a future auto-apply) must not be able to read the manifest
    and crown the wrong member of an unsupported pair.
    """
    from daemon.services.skill_ab_disposition_manifest import (
        DECLARED_AB_DISPOSITIONS,
        declared_divert_dispositions,
    )

    authorized = {
        d.ab_test_row_id_prefix for d in declared_divert_dispositions()
    }
    assert authorized == set(SUPPORTED_ROW_IDS), (
        "the divert-authorized set drifted — only the pairs whose "
        "defect claim is supported by named evidence may be diverted"
    )
    for d in DECLARED_AB_DISPOSITIONS:
        if d.ab_test_row_id_prefix in UNSUPPORTED_ROW_IDS:
            assert not d.divert_authorized, (
                f"{d.ab_test_row_id_prefix}: an unsupported claim must "
                "never be divert-authorized"
            )
            assert d.basis != "defective_arm", (
                f"{d.ab_test_row_id_prefix}: an unsupported pair carries "
                "the plain E-6 default basis, not the defect-divert "
                "basis"
            )
        else:
            assert d.divert_authorized, (
                f"{d.ab_test_row_id_prefix}: a supported claim with "
                "named evidence must be divert-authorized"
            )


def test_the_original_claim_is_preserved_per_entry():
    """The correction is RECORDED, not silently rewritten."""
    from daemon.services.skill_ab_disposition_manifest import (
        DECLARED_AB_DISPOSITIONS,
        ORIGINAL_GENERIC_CLAIM,
    )

    for d in DECLARED_AB_DISPOSITIONS:
        assert d.original_claim == ORIGINAL_GENERIC_CLAIM, (
            f"{d.ab_test_row_id_prefix}: the pre-adjudication claim was "
            "rewritten — the history must stay auditable"
        )
        assert d.original_basis == "defective_arm", (
            f"{d.ab_test_row_id_prefix}: the pre-adjudication basis was "
            "rewritten"
        )
        assert d.corrected_on == "2026-11-18", (
            f"{d.ab_test_row_id_prefix}: the correction date changed"
        )
        assert d.correction_note.strip(), (
            f"{d.ab_test_row_id_prefix}: the correction note is empty"
        )


def test_manifest_version_was_bumped_for_the_shape_change():
    from daemon.services.skill_ab_disposition_manifest import (
        MANIFEST_VERSION,
    )

    assert MANIFEST_VERSION == 2, (
        "the entry shape changed (status/evidence/finding/correction) — "
        "the manifest version must be bumped"
    )


# --------------------------------------------------------------------------
# the matcher
# --------------------------------------------------------------------------
def test_matcher_resolves_both_members():
    from daemon.services.skill_ab_disposition_manifest import (
        disposition_winner,
        match_ab_disposition,
    )

    # A SUPPORTED pair: the pair-row id and the winner skill id both
    # resolve, and the winner lookup returns the declared winner.
    row_id, winner = next(
        pair
        for pair in EXPECTED_ROW_TO_WINNER
        if pair[0] in SUPPORTED_ROW_IDS
    )
    assert match_ab_disposition(row_id) is not None
    assert match_ab_disposition(winner) is not None
    assert disposition_winner(row_id) == winner
    assert disposition_winner(winner) == winner
    # An UNSUPPORTED pair still resolves as DATA (the operator reads
    # it), but it is not a disposition — no winner is offered.
    unsupported_row, _unsupported_winner = next(
        pair
        for pair in EXPECTED_ROW_TO_WINNER
        if pair[0] in UNSUPPORTED_ROW_IDS
    )
    assert match_ab_disposition(unsupported_row) is not None
    assert disposition_winner(unsupported_row) is None
    # An unlisted id matches nothing (the operator then decides).
    assert match_ab_disposition("deadbeef") is None
    assert disposition_winner("deadbeef") is None


def test_matcher_resolves_the_group_and_the_other_member():
    from daemon.services.skill_ab_disposition_manifest import (
        match_ab_disposition,
    )

    for row_id, (group_id, winner_id, _status) in EXPECTED_PAIR_TRUTH.items():
        for identifier in (group_id, winner_id):
            entry = match_ab_disposition(identifier)
            assert entry is not None, (
                f"{identifier} does not resolve to its pair"
            )
            assert entry.ab_test_row_id_prefix == row_id
        entry = match_ab_disposition(
            match_ab_disposition(group_id).newer_member_skill_id_prefix
        )
        assert entry is not None
        assert entry.ab_test_row_id_prefix == row_id


def test_disposition_winner_refuses_unsupported_pairs():
    """A blanket read must not be able to crown the wrong member."""
    from daemon.services.skill_ab_disposition_manifest import (
        disposition_winner,
    )

    for row_id, (_group, winner_id, _status) in EXPECTED_PAIR_TRUTH.items():
        expected = winner_id if row_id in SUPPORTED_ROW_IDS else None
        assert disposition_winner(row_id) == expected, (
            f"{row_id}: the winner lookup must honour the "
            "divert-authorization gate"
        )


# --------------------------------------------------------------------------
# (c) the module stays DATA-ONLY
# --------------------------------------------------------------------------
def _daemon_importers_of_the_manifest():
    """Every daemon module that IMPORTS the manifest (AST-parsed)."""
    offenders = []
    manifest_module = "skill_ab_disposition_manifest"
    for path in sorted((REPO_ROOT / "daemon").rglob("*.py")):
        if path == MANIFEST_SOURCE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover — unreadable
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if manifest_module in alias.name:
                        offenders.append((path, alias.name))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if manifest_module in module:
                    offenders.append((path, module))
                for alias in node.names:
                    if manifest_module in alias.name:
                        offenders.append((path, f"{module}.{alias.name}"))
    return offenders


def test_no_daemon_module_imports_the_manifest():
    """The manifest stays DATA-ONLY.

    Nothing in ``daemon/`` may import it — the divert stays an
    explicit operator act. This is the guard against silent
    auto-apply creeping back in.
    """
    offenders = _daemon_importers_of_the_manifest()
    assert not offenders, (
        "daemon modules import the disposition manifest — the "
        "manifest must stay DATA-ONLY: "
        + ", ".join(f"{p.relative_to(REPO_ROOT)} ({m})" for p, m in offenders)
    )


def test_no_daemon_module_dynamically_imports_the_manifest():
    """A dynamic import (``importlib`` / ``__import__``) is an import
    too."""
    pattern = re.compile(
        r"(importlib\.import_module|__import__)\s*\(\s*"
        r"['\"]skill_ab_disposition_manifest['\"]"
    )
    offenders = []
    for path in sorted((REPO_ROOT / "daemon").rglob("*.py")):
        if path == MANIFEST_SOURCE:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(path)
    assert not offenders, (
        "a daemon module dynamically imports the disposition manifest: "
        + ", ".join(str(p.relative_to(REPO_ROOT)) for p in offenders)
    )


def test_no_shipped_path_auto_applies_the_dispositions():
    """The manifest is DATA: no shipped module may call the sanctioned
    resolver BECAUSE of a disposition row (the divert stays an
    explicit operator act)."""
    from daemon.services import skill_ab_disposition_manifest as manifest

    # The matcher is not wired into the sweep either.
    sweep_source = MANAGER_SOURCE.read_text(encoding="utf-8")
    assert "skill_ab_disposition_manifest" not in sweep_source
    assert manifest.MANIFEST_VERSION == 2


def test_the_manifest_never_calls_the_sanctioned_resolver():
    """The manifest is DATA — it never reaches the resolver.

    AST-scoped (not a textual grep): the module docstring legitimately
    NAMES the sanctioned resolver to state the current truth, so a
    raw-text pin would be prose-satisfiable in both directions. What
    must not exist is a CALL (or an import that could reach one).
    """
    tree = ast.parse(MANIFEST_SOURCE.read_text(encoding="utf-8"))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name:
                called.add(name)
    assert "resolve_ab_test_sanctioned" not in called, (
        "the manifest must not CALL the sanctioned resolver"
    )
    assert "resolve_pair" not in called, (
        "the manifest must not call a pair resolver"
    )
    imported = {
        (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(
        "skill_evolution_service" in name or "routers" in name
        for name in imported
    ), (
        "the manifest must not import the resolver/service layer — it "
        "is DATA"
    )


def test_the_sweep_still_passes_no_winner_and_no_defective_arm():
    """The sweep's deliberate design: NO winner, NO defective arm.

    "Defect-divert is NEVER sweep-judgment: the sweep only resolves
    pairs no one has flagged." If a winner/defective-arm ever rides
    the sweep call, the manifest's "no consumer" truth claim (and the
    module docstring) becomes false.
    """
    tree = ast.parse(MANAGER_SOURCE.read_text(encoding="utf-8"))
    sweep = None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_run_skill_ab_inactivity_sweep"
        ):
            sweep = node
            break
    assert sweep is not None, (
        "the sweep wrapper vanished from manager.py — this pin must be "
        "re-pointed deliberately"
    )
    forbidden = {"winner_id", "defective_arm_id"}
    used = set()
    for node in ast.walk(sweep):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg in forbidden:
                    used.add(keyword.arg)
    assert not used, (
        "the sweep must not supply a winner/defective arm — the "
        f"divert is NEVER sweep-judgment (found: {sorted(used)})"
    )


def test_manifest_is_import_time_inert():
    """Importing the manifest must not touch the filesystem — the
    house note on import-time side effects (the metrics registry
    precedent)."""
    source = MANIFEST_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("open(", "Path(", "os.environ", "sqlite"):
        assert forbidden not in source, (
            f"the manifest must stay import-time inert ({forbidden})"
        )


def test_the_retired_field_name_is_gone_from_the_code_surfaces():
    """The misnamed field was renamed — zero references remain."""
    surfaces = ("daemon", "tests", "scripts")
    offenders = []
    for surface in surfaces:
        root = REPO_ROOT / surface
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix not in {
                ".py", ".md", ".sh", ".yaml", ".yml", ".json",
            }:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if _RETIRED_FIELD_NAME in text:
                offenders.append(path)
    assert not offenders, (
        "the retired field name still appears in: "
        + ", ".join(str(p.relative_to(REPO_ROOT)) for p in offenders)
    )


# --------------------------------------------------------------------------
# optional: the same pins against a DEV database fixture
# --------------------------------------------------------------------------
@pytest.mark.skipif(
    not (
        os.environ.get("POSTGRES_DB") == "ensemble_dev"
        and os.environ.get("POSTGRES_USER")
        and os.environ.get("POSTGRES_PASSWORD")
        and os.environ.get("POSTGRES_HOST")
        and os.environ.get("POSTGRES_PORT")
    ),
    reason=(
        "dev-only fixture check — the dev fence is not fully exported "
        "(POSTGRES_DB must be ensemble_dev with user/password/host/"
        "port). The guard is explicit on purpose: a glob scrub is a "
        "silent no-op"
    ),
)
def test_row_ids_resolve_in_the_dev_database():
    """OPTIONAL resolution pin — only when a dev fixture exists.

    Read-only. The pair rows live in the live database; the dev
    database usually carries ZERO ``skill_ab_tests`` rows, in which
    case this pin skips loudly instead of pretending to verify.
    """
    from sqlmodel import Session, create_engine, select

    from daemon.repositories.skill.models import SkillABTest

    engine = create_engine(
        f"postgresql+psycopg://"
        f"{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}"
        f"/{os.environ['POSTGRES_DB']}"
    )
    with Session(engine) as session:
        rows = session.exec(select(SkillABTest)).all()
        if not rows:
            pytest.skip(
                "ensemble_dev carries no skill_ab_tests rows — the "
                "static per-pair truth pin is the binding one"
            )
        by_prefix = {}
        for row in rows:
            by_prefix.setdefault(row.id[:8], row)
        for row_id, (group_id, winner_id, _status) in (
            EXPECTED_PAIR_TRUTH.items()
        ):
            row = by_prefix.get(row_id)
            if row is None:
                pytest.skip(
                    f"pair row {row_id} is absent from ensemble_dev — "
                    "the static per-pair truth pin is the binding one"
                )
            assert row.ab_test_group[:8] == group_id
            assert (row.winner_skill_id or "")[:8] == winner_id
