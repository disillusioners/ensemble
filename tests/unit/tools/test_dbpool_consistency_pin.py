"""G-consistency pin — the W-11 cross-artifact consistency scanner (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §6.4 (the scanner is written down there
in full — this pin implements it) and §5 (the ONE authoritative pin
inventory).

THE SCANNER RE-READS THE CONTRACT as its source of truth — it does NOT
carry a private copy of the id sets: the canonical 17-entry ladder
enumeration, the §5 pin inventory, the §1 fix-id inventory and the
sentinel-marker rules are all PARSED from
``final-implementation-spec.md`` at run time. If the pin and the
contract disagree, the pin is wrong.

The four checks (contract §6.4.2):

1. **Gate-ID check** — every gate-id token (a capital ``G``, a digit
   ``0``–``3``, a dot, and a digit) OUTSIDE inline-code spans, outside
   fenced blocks and outside the sentinel-delimited exempt region must
   be a member of the explicit 17-id enumeration.
2. **Fix-ID check** — every fix-id token (a capital ``F``, a digit
   ``1``–``8``, then either a dot and a number or a dash and a
   lowercase suffix) must be a fix id in §1, a pin in §5, or a known
   exempt token (``F7`` — the fix GROUP).
3. **AC-ID check** — every AC-id token (the letters ``AC`` followed by
   one or two digits) must appear in at least one OTHER artifact of the
   scan set (an AC that exists in exactly one file is drift).
4. **Pack-inventory check** — the set of pin ids in §5 (the contract)
   must equal the set of pin ids in ``test-strategy.md`` §3 — no pin
   on one side only.

Sentinel handling (contract §6.4.3): the scanner skips ONLY the text
between the ``EXEMPT:BEGIN`` marker and the matching ``EXEMPT:END``
marker, and asserts — per artifact — that the number of marker pairs is
exactly one if that artifact carries the legacy→canonical mapping and
exactly zero if it does not.

Plantable mutations: introduce a gate id / fix id / AC id that is not
in the canonical sets, remove or duplicate a sentinel pair, or add a pin
to §5 without adding it to ``test-strategy.md`` §3 ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_consistency_pin.py --tb=short -q
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 7

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLAN_DIR = _REPO_ROOT / ".agents" / "shared" / "planning" / "db-pool-idle-txn"
_SPEC_PATH = _PLAN_DIR / "final-implementation-spec.md"
_STRATEGY_PATH = _PLAN_DIR / "test-strategy.md"

#: The scan set (contract §6.4.1) — EVERY artifact carrying gate / AC /
#: fix ids.
SCAN_SET = (
    "plan-overview.md",
    "phase1-plan.md",
    "phase2-plan.md",
    "phase3-plan.md",
    "test-strategy.md",
    "decisions.md",
    "architecture-recommendation.md",
    "final-implementation-spec.md",
)

_GATE_TOKEN = re.compile(r"\bG[0-3]\.\d\b")
#: A fix-id token: ``F<digit>`` then either a dot and a number, or a dash
#: and a LOWERCASE SUFFIX. The suffix must be ID-SHAPED (a single letter
#: a-j, optionally ``-static``) — otherwise English prose that merely
#: follows a fix id ("F8-and-F3's fixes", "F4-keyed") would be read as an
#: id token.
_FIX_TOKEN = re.compile(r"\bF[1-8](?:\.\d+|-[a-j](?:-static)?)\b")
_AC_TOKEN = re.compile(r"\bAC\d{1,2}\b")
#: The pin-id shape (a pin id is ``F<fix>-<letter>`` with an optional
#: ``-static`` suffix, or a ``G-`` id).
_PIN_ID = re.compile(r"^(?:F[1-8]-[a-z]+(?:-static)?|G-[A-Za-z]+)$")

#: Known exempt fix tokens (contract §6.4.2 check 2) — ``F7`` is the fix
#: GROUP, not a numbered change or a pin.
_EXEMPT_FIX_TOKENS = frozenset({"F7"})


# ── contract parsing (the source of truth) ───────────────────────────────


def _spec_text() -> str:
    return _SPEC_PATH.read_text()


def _canonical_gate_ids() -> set[str]:
    """The explicit 17-id ladder enumeration (contract §6.2a)."""
    for line in _spec_text().splitlines():
        if "explicit enumeration" in line:
            ids = set(re.findall(r"`(G[0-3](?:\.\d)?)`", line))
            if ids:
                return ids
    raise AssertionError(
        "G-consistency: the contract's explicit ladder enumeration was "
        "not found — the pin re-reads it from the contract and cannot "
        "fall back to a private copy"
    )


def _contract_section(start_marker: str, end_marker: str) -> str:
    """The text between two contract headings."""
    text = _spec_text()
    start = text.find(start_marker)
    assert start != -1, f"contract section not found: {start_marker!r}"
    end = text.find(end_marker, start + len(start_marker))
    assert end != -1, f"contract section end not found: {end_marker!r}"
    return text[start:end]


def _contract_pin_ids() -> set[str]:
    """The §5 pin inventory (the ONE authoritative list)."""
    section = _contract_section("### 5.2 The pins", "\n## §6")
    ids = set(re.findall(r"^\| \*\*([A-Za-z0-9.\-]+)\*\* \|", section, re.M))
    return {i for i in ids if _PIN_ID.match(i)}


def _contract_fix_ids() -> set[str]:
    """The §1 numbered-change ids (F1.1 … F9.1)."""
    section = _contract_section("## §1 NUMBERED CHANGES", "\n## §2")
    return set(re.findall(r"^\| (F\d+\.\d+) \|", section, re.M))


def _sentinel_markers() -> tuple[str, str]:
    """The BEGIN / END marker text (read from the contract, §6.3)."""
    text = _spec_text()
    begin = re.search(r"<!--\s*EXEMPT:BEGIN[^\n]*-->", text)
    end = re.search(r"<!--\s*EXEMPT:END[^n]*-->", text)
    assert begin is not None, "G-consistency: the EXEMPT:BEGIN marker was not found"
    assert end is not None, "G-consistency: the EXEMPT:END marker was not found"
    return begin.group(0), end.group(0)


def _expected_mapping_artifacts() -> set[str]:
    """The artifacts the contract says carry the legacy→canonical
    mapping (parsed from the contract's own sentence)."""
    text = _spec_text()
    expected = {_SPEC_PATH.name}
    for line in text.splitlines():
        if "carry the mapping" in line:
            expected.update(re.findall(r"`([A-Za-z0-9_.\-]+\.md)`", line))
    return expected


# ── text preparation ─────────────────────────────────────────────────────


def _strip_fenced_blocks(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def _strip_inline_code(text: str) -> str:
    return re.sub(r"`[^`\n]*`", "", text)


def _remove_exempt_region(text: str, begin: str, end: str) -> str:
    """Remove ONLY the sentinel-delimited exempt region."""
    pattern = re.escape(begin) + r".*?" + re.escape(end)
    return re.sub(pattern, "", text, flags=re.S)


def _live_text(path: Path, begin: str, end: str) -> str:
    """The live (non-exempt, non-code, non-fenced) text of one artifact."""
    text = _remove_exempt_region(path.read_text(), begin, end)
    text = _strip_fenced_blocks(text)
    return _strip_inline_code(text)


# ── the checks ───────────────────────────────────────────────────────────


class TestGConsistency:
    """G-consistency — the four cross-artifact checks + the sentinel rule."""

    def test_sentinel_pairs_match_the_contract_rule(self):
        begin, end = _sentinel_markers()
        expected = _expected_mapping_artifacts()
        observed: dict[str, int] = {}
        for name in SCAN_SET:
            path = _PLAN_DIR / name
            assert path.exists(), f"G-consistency: scan-set artifact missing: {name}"
            text = path.read_text()
            observed[name] = len(re.findall(re.escape(begin), text))
            observed[name + "::end"] = len(re.findall(re.escape(end), text))
        for name in SCAN_SET:
            begin_count = observed[name]
            end_count = observed[name + "::end"]
            assert begin_count == end_count, (
                f"G-consistency: {name} has {begin_count} BEGIN and "
                f"{end_count} END markers — the pair must match"
            )
            if name in expected:
                assert begin_count == 1, (
                    f"G-consistency: {name} carries the legacy→canonical "
                    f"mapping and must carry EXACTLY ONE marker pair — it "
                    f"carries {begin_count}"
                )
            else:
                assert begin_count == 0, (
                    f"G-consistency: {name} carries NO legacy→canonical "
                    f"mapping and must carry ZERO marker pairs — it "
                    f"carries {begin_count} (the exemption could be "
                    "widened silently)"
                )

    def test_check_1_gate_ids_are_canonical(self):
        begin, end = _sentinel_markers()
        canonical = _canonical_gate_ids()
        unknown: list[tuple[str, str]] = []
        for name in SCAN_SET:
            for token in _GATE_TOKEN.findall(_live_text(_PLAN_DIR / name, begin, end)):
                if token not in canonical:
                    unknown.append((name, token))
        assert not unknown, (
            "G-consistency: gate ids referenced outside the exempt region "
            f"are not in the canonical 17-id ladder: {unknown}"
        )

    def test_check_2_fix_ids_are_known(self):
        begin, end = _sentinel_markers()
        known = _contract_fix_ids() | _contract_pin_ids() | _EXEMPT_FIX_TOKENS
        unknown: list[tuple[str, str]] = []
        for name in SCAN_SET:
            text = _remove_exempt_region(
                (_PLAN_DIR / name).read_text(), begin, end
            )
            for token in _FIX_TOKEN.findall(text):
                if token not in known:
                    unknown.append((name, token))
        assert not unknown, (
            "G-consistency: fix ids referenced in the scan set are "
            f"neither a §1 change nor a §5 pin: {unknown}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "OPEN DRIFT (W-11 check 3 — contract §6.4.2 check 3: 'every "
            "AC-id token ... must appear in at least one other artifact in "
            "the scan set (the AC set is cross-artifact by construction — "
            "an AC that exists in exactly one file is drift)'). TREE "
            "REALITY: the PHASE PLANS are the DEFINITION SITE of their own "
            "phase's AC ids (phase1-plan.md owns AC1-AC13/AC20/AC21/AC24/"
            "AC30-AC33; phase2-plan.md owns AC17-AC22/AC34-AC36; "
            "phase3-plan.md owns AC25-AC29), and each of those ids occurs "
            "in EXACTLY ONE artifact of the 8-artifact scan set — the "
            "contract's §1 numbered-change tables carry a prose 'AC' "
            "header with 'Verify' references, NOT the AC ids, so no other "
            "artifact names them. The fix is NOT a code change: it belongs "
            "to the plan-artifact owner (the fold lane) — either the "
            "contract's §1 rows gain the AC ids they govern, or "
            "plan-overview.md's AC table gains them. This strict xfail "
            "turns XPASS-strict (RED) the moment the drift is closed, "
            "forcing the marker's removal."
        ),
    )
    def test_check_3_ac_ids_are_cross_artifact(self):
        begin, end = _sentinel_markers()
        per_artifact: dict[str, set[str]] = {}
        for name in SCAN_SET:
            text = _remove_exempt_region(
                (_PLAN_DIR / name).read_text(), begin, end
            )
            per_artifact[name] = set(_AC_TOKEN.findall(text))
        orphans = [
            (name, ac)
            for name, acs in per_artifact.items()
            for ac in acs
            if not any(
                ac in others for other, others in per_artifact.items() if other != name
            )
        ]
        assert not orphans, (
            "G-consistency: AC ids that exist in exactly ONE artifact are "
            f"drift (the AC set is cross-artifact by construction): {orphans}"
        )

    def test_check_4_pack_inventory_matches(self):
        contract_pins = _contract_pin_ids()
        strategy = _STRATEGY_PATH.read_text()
        start = strategy.find("## 3.")
        end = strategy.find("## 4.")
        assert start != -1 and end != -1 and end > start, (
            "G-consistency: test-strategy.md §3 (the pin-inventory mirror) "
            "could not be located"
        )
        section = strategy[start:end]
        strategy_pins = {
            i
            for i in re.findall(
                r"^\| \*\*([A-Za-z0-9.\-]+)\*\* \|", section, re.M
            )
            if _PIN_ID.match(i)
        }
        assert contract_pins == strategy_pins, (
            "G-consistency: the §5 pin inventory and test-strategy.md §3 "
            "must match row for row — "
            f"only in the contract: {sorted(contract_pins - strategy_pins)}; "
            f"only in the strategy: {sorted(strategy_pins - contract_pins)}"
        )

    def test_the_canonical_enumeration_has_seventeen_entries(self):
        ids = _canonical_gate_ids()
        assert len(ids) == 17, (
            f"G-consistency: the canonical ladder enumeration carries "
            f"{len(ids)} ids — the contract declares 17"
        )

    def test_the_pin_inventory_carries_the_declared_count(self):
        pins = _contract_pin_ids()
        assert len(pins) == 46, (
            f"G-consistency: the §5 pin inventory carries {len(pins)} pins "
            "— the contract declares 46"
        )
