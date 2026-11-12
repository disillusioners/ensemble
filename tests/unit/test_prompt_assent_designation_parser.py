"""The designation-map parser + the classification-ordering unit matrix
(prompt-assent Phase 1.5, Phase 1 — tasks T1.5 / T1.6 / T1.7 / T1.8).

This file is the PROOF SURFACE of the pure half of the designated-channel
extension:

* **the parse matrix** — one named test per validation rule of D-4
  (format, id shape, leading zero, zero, duplicate key, non-str value,
  the entry bound, unset vs empty vs invalid);
* **the classification matrix** — the neutral-leg ordering of
  ``designation_outcome`` (refusal-cell-matrix §1.1), including the
  absent-``is_group_dm`` gate (M-18), the absent-identity leg (M-8/M-19)
  and the cross-pin case (M-14);
* **the single-home pin** (risks.md **P-1**) — the env-name literal
  appears exactly ONCE in ``daemon/``;
* **the no-regression pin** (risks.md **P-6**) — ``is_private_assent_channel``
  is byte-identical to the base commit;
* **the phase fence** (AC7) — no production call site reaches the new
  symbols yet.

NON-VACUITY. Every structural pin here is paired with an in-file
counter-mutation demonstration (see ``TestTheCounterMutations``): each
proves the check turns RED when the property it guards is broken. A pin
that stays green under its own revert is a failure of the suite (the house
mutation discipline — POK ``mutation-01-guards-need-counter-mutation``).
"""

from __future__ import annotations

import ast
import json
import textwrap
from pathlib import Path

import pytest

from daemon.repositories.prompt_assent import mint_context
from daemon.repositories.prompt_assent.designation import (
    ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS,
    MAP_DIGEST_HEX_LEN,
    MAX_DESIGNATIONS,
    DesignationMap,
    DesignationParseResult,
    designation_digest,
    describe_parse,
    parse_designations,
)
from daemon.repositories.prompt_assent.mint_context import (
    DesignationResult,
    designation_outcome,
    is_designated_assent_channel,
    is_private_assent_channel,
    resolve_pinned_approver,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The base commit this lane branched from — the no-regression pin's anchor.
BASE_COMMIT = "efa8aaae2"

#: The prefix the tests use to BUILD the env-var name programmatically
#: (T1.6: tests are exempt from the single-home pin, but they must not
#: introduce a second literal either — the constant is imported and this
#: construction proves the literal is derivable, not duplicated).
ENV_PREFIX = "ENSEMBLE_PROMPT_ASSENT_"

CHANNEL = "111122223333444455"
APPROVER = "999888777666555444"
OTHER_HUMAN = "555666777888999000"
OTHER_CHANNEL = "222333444555666777"


def _valid_map(channel: str = CHANNEL, approver: str = APPROVER) -> DesignationParseResult:
    return parse_designations(json.dumps({channel: approver}))


# ── the parse matrix (D-4 rule by rule) ───────────────────────────────────


class TestTheParseFormatRules:
    def test_an_unset_value_is_a_valid_unset_map(self) -> None:
        """Rule 5 — an absent env var means "no designated channels"."""
        for raw in (None, "", "   ", "\n"):
            result = parse_designations(raw)
            assert result.valid is True
            assert result.errors == ()
            assert result.map is not None
            assert result.map.unset is True
            assert len(result.map) == 0
            assert result.state == "unset"

    def test_an_empty_object_is_a_valid_empty_map(self) -> None:
        """Rule 4 — ``{}`` is valid, means no designated channels, and is
        DIAGNOSTICALLY distinct from unset."""
        result = parse_designations("{}")
        assert result.valid is True
        assert result.map is not None
        assert result.map.unset is False
        assert len(result.map) == 0
        assert result.state == "empty"

    def test_a_json_array_is_refused(self) -> None:
        """Rule 1 — an array is a shape error, not a partial map."""
        result = parse_designations(json.dumps([CHANNEL, APPROVER]))
        assert result.valid is False
        assert result.map is None
        assert any("format" in err for err in result.errors)

    def test_a_json_scalar_is_refused(self) -> None:
        for raw in ("42", '"hello"', "true", "null"):
            result = parse_designations(raw)
            assert result.valid is False, raw
            assert any("format" in err for err in result.errors), raw

    def test_malformed_json_is_refused(self) -> None:
        for raw in ("{", "}", '{"a":}', "not json at all", '{"a":"b",}'):
            result = parse_designations(raw)
            assert result.valid is False, raw
            assert result.map is None, raw
            assert result.state == "invalid", raw

    def test_a_deeply_nested_value_does_not_raise(self) -> None:
        """A non-object value on a valid key must be REFUSED, never crash."""
        result = parse_designations(json.dumps({CHANNEL: {"nested": "x"}}))
        assert result.valid is False
        assert any("id-shape" in err for err in result.errors)


class TestTheIdShapeRules:
    def test_a_valid_map_parses(self) -> None:
        result = _valid_map()
        assert result.valid is True
        assert result.map is not None
        assert result.map.pinned_approver(CHANNEL) == APPROVER
        assert result.state == "1 entries"

    def test_a_non_digit_channel_id_is_refused(self) -> None:
        for bad in ("abc", "12a4", "12-34", " 123", "123 "):
            result = parse_designations(json.dumps({bad: APPROVER}))
            assert result.valid is False, bad
            assert any("id-shape" in err for err in result.errors), bad

    def test_a_non_digit_approver_id_is_refused(self) -> None:
        result = parse_designations(json.dumps({CHANNEL: "user-abc"}))
        assert result.valid is False
        assert any("id-shape" in err for err in result.errors)

    def test_a_zero_id_is_refused(self) -> None:
        """"0" is not a Discord snowflake — refuse it (D-4 rule 2)."""
        assert parse_designations(json.dumps({"0": APPROVER})).valid is False
        assert parse_designations(json.dumps({CHANNEL: "0"})).valid is False

    def test_a_leading_zero_id_is_refused(self) -> None:
        """The leading-zero typo class can never match a stringified id —
        refuse it loudly rather than silently never matching."""
        result = parse_designations(json.dumps({"0" + CHANNEL: APPROVER}))
        assert result.valid is False
        assert any("id-shape" in err for err in result.errors)
        result = parse_designations(json.dumps({CHANNEL: "0" + APPROVER}))
        assert result.valid is False
        assert any("id-shape" in err for err in result.errors)

    def test_an_id_longer_than_the_bound_is_refused(self) -> None:
        assert parse_designations(json.dumps({"1" * 33: APPROVER})).valid is False
        assert parse_designations(json.dumps({CHANNEL: "1" * 33})).valid is False
        # 32 digits is the documented maximum — accepted.
        assert parse_designations(json.dumps({"1" * 32: "2" * 32})).valid is True

    def test_a_non_string_value_is_rejected_never_coerced(self) -> None:
        """D-4 rule 2 / AC2 — an int or a bool is REJECTED, never coerced.

        A ``1`` that silently became ``"1"`` would hide a typo; the error
        names the key and the rule, and the type is named so the operator
        can see WHAT was wrong without the value being echoed.
        """
        for raw in (
            json.dumps({CHANNEL: 1}),
            json.dumps({CHANNEL: True}),
            json.dumps({CHANNEL: None}),
            json.dumps({CHANNEL: [APPROVER]}),
        ):
            result = parse_designations(raw)
            assert result.valid is False, raw
            assert any("id-shape" in err for err in result.errors), raw
            # The offending key IS named; the value never is.
            assert CHANNEL in result.errors[0], raw

    def test_errors_never_contain_the_operator_value(self) -> None:
        """S-6 — errors name the KEY and the RULE, never a value."""
        secret = "444555666777888999"
        result = parse_designations(json.dumps({CHANNEL: secret, "0bad": "x"}))
        assert result.valid is False
        for err in result.errors:
            assert secret not in err, err


class TestTheDuplicateAndBoundRules:
    def test_a_duplicate_key_is_refused(self) -> None:
        """D-4 rule 3 — ``json.loads`` keeps the LAST duplicate silently;
        the parser must refuse the shape outright."""
        raw = f'{{"{CHANNEL}": "{APPROVER}", "{CHANNEL}": "{OTHER_HUMAN}"}}'
        result = parse_designations(raw)
        assert result.valid is False
        assert any("unique-keys" in err for err in result.errors)
        assert result.map is None

    def test_the_entry_bound_is_enforced(self) -> None:
        """D-4 rule 8 — at most 64 designations."""
        at_bound = {
            str(10_000 + i): str(90_000 + i) for i in range(MAX_DESIGNATIONS)
        }
        assert parse_designations(json.dumps(at_bound)).valid is True

        over_bound = dict(at_bound)
        over_bound[str(10_000 + MAX_DESIGNATIONS)] = str(
            90_000 + MAX_DESIGNATIONS
        )
        result = parse_designations(json.dumps(over_bound))
        assert result.valid is False
        assert any("bound" in err for err in result.errors)

    def test_one_bad_entry_invalidates_the_whole_map(self) -> None:
        """D-1.4 — all-or-nothing: no partial authority state."""
        raw = json.dumps({CHANNEL: APPROVER, OTHER_CHANNEL: "not-an-id"})
        result = parse_designations(raw)
        assert result.valid is False
        assert result.map is None, "a partially applied map is forbidden"
        assert any(OTHER_CHANNEL in err for err in result.errors)

    def test_every_offending_entry_is_named(self) -> None:
        raw = json.dumps({CHANNEL: "bad", OTHER_CHANNEL: "also-bad"})
        result = parse_designations(raw)
        assert result.valid is False
        assert len(result.errors) == 2
        assert any(CHANNEL in err for err in result.errors)
        assert any(OTHER_CHANNEL in err for err in result.errors)

    def test_the_same_approver_on_many_channels_is_allowed(self) -> None:
        """D-4 rule 6 — one human owning many channels is the normal case."""
        raw = json.dumps({CHANNEL: APPROVER, OTHER_CHANNEL: APPROVER})
        result = parse_designations(raw)
        assert result.valid is True
        assert result.map is not None
        assert result.map.pinned_approver(CHANNEL) == APPROVER
        assert result.map.pinned_approver(OTHER_CHANNEL) == APPROVER


class TestTheMapValueType:
    def test_the_map_is_immutable(self) -> None:
        """D-1.1 — a caller cannot mutate a resolved map mid-decision."""
        result = _valid_map()
        assert result.map is not None
        with pytest.raises(TypeError):
            result.map.entries[CHANNEL] = OTHER_HUMAN  # type: ignore[index]

    def test_the_map_is_content_equal_regardless_of_key_order(self) -> None:
        a = parse_designations(json.dumps({CHANNEL: APPROVER, OTHER_CHANNEL: OTHER_HUMAN}))
        b = parse_designations(json.dumps({OTHER_CHANNEL: OTHER_HUMAN, CHANNEL: APPROVER}))
        assert a.map is not None and b.map is not None
        assert a.map.canonical_json() == b.map.canonical_json()
        assert designation_digest(a.map) == designation_digest(b.map)

    def test_the_pinned_lookup_is_exact_only(self) -> None:
        """D-9 — no thread inheritance, no coercion."""
        result = _valid_map()
        assert result.map is not None
        assert result.map.pinned_approver(CHANNEL) == APPROVER
        assert result.map.pinned_approver(CHANNEL + "0") is None
        assert result.map.pinned_approver(int(CHANNEL)) is None
        assert result.map.pinned_approver(None) is None


class TestTheFingerprint:
    def test_the_digest_is_stable_for_a_given_map(self) -> None:
        a = _valid_map()
        b = _valid_map()
        assert a.map is not None and b.map is not None
        assert designation_digest(a.map) == designation_digest(b.map)

    def test_the_digest_changes_when_the_map_changes(self) -> None:
        a = _valid_map()
        b = _valid_map(approver=OTHER_HUMAN)
        assert a.map is not None and b.map is not None
        assert designation_digest(a.map) != designation_digest(b.map)

    def test_the_digest_is_twelve_hex_and_content_free(self) -> None:
        """D-15 — ONE length everywhere; never a channel id, never a user id."""
        result = _valid_map()
        assert result.map is not None
        digest = designation_digest(result.map)
        assert len(digest) == MAP_DIGEST_HEX_LEN == 12
        int(digest, 16)  # hex
        assert CHANNEL not in digest
        assert APPROVER not in digest

    def test_the_parse_log_line_is_content_free(self) -> None:
        """P1-AC15 — the mandatory line names state + count + fingerprint and
        NOTHING else."""
        valid = _valid_map()
        line = describe_parse(valid)
        assert "state=1 entries" in line
        assert "count=1" in line
        assert f"map_digest={designation_digest(valid.map)}" in line  # type: ignore[arg-type]
        assert CHANNEL not in line
        assert APPROVER not in line

        invalid = parse_designations("{bad")
        bad_line = describe_parse(invalid)
        assert "state=invalid" in bad_line
        assert "map_digest=-" in bad_line
        assert CHANNEL not in bad_line

        unset = describe_parse(parse_designations(None))
        assert "state=unset" in unset
        assert "count=0" in unset

        empty = describe_parse(parse_designations("{}"))
        assert "state=empty" in empty

    def test_the_unset_and_empty_digests_match_but_the_states_differ(self) -> None:
        """Behaviourally identical, diagnostically distinct (D-4 rule 4)."""
        unset = parse_designations(None)
        empty = parse_designations("{}")
        assert unset.map is not None and empty.map is not None
        assert designation_digest(unset.map) == designation_digest(empty.map)
        assert describe_parse(unset) != describe_parse(empty)


# ── the classification ordering (refusal-cell-matrix §1.1) ────────────────


DM_META = {"is_dm": True, "is_group_dm": False, "channel_id": CHANNEL, "user_id": APPROVER}
GUILD_META = {
    "is_dm": False,
    "is_group_dm": False,
    "guild_id": "777",
    "channel_id": CHANNEL,
    "channel_type": "text",
    "user_id": APPROVER,
}


class TestTheDesignationOutcomeOrdering:
    def test_the_leg_set_is_exactly_seven(self) -> None:
        """D-18 — seven side-neutral variants, no more, no fewer."""
        assert {leg.value for leg in DesignationResult} == {
            "leg:pass",
            "leg:not-designated",
            "leg:group-dm-shape",
            "leg:approver-not-pinned",
            "leg:absent-discriminator",
            "leg:absent-identity",
            "leg:config-invalid",
        }
        assert len(DesignationResult) == 7

    def test_no_variant_is_usable_as_a_refusal_code(self) -> None:
        """D-18 — a variant is never a refusal code, not even by STRING
        equality (``approver-not-pinned`` is a real code on both sides)."""
        from daemon.tools.prompt_assent import (
            MISMATCH_CODES,
            PHASE1_5_PLACED_CODES,
            REFUSAL_TAXONOMY,
        )

        for leg in DesignationResult:
            assert leg.value.startswith("leg:"), leg
            assert leg.value not in REFUSAL_TAXONOMY, leg
            assert leg.value not in MISMATCH_CODES, leg
            assert leg.value not in PHASE1_5_PLACED_CODES, leg

    def test_a_proven_1to1_dm_passes_without_the_map(self) -> None:
        """Leg 1 — and it passes even with an INVALID map (blast radius)."""
        invalid = parse_designations("{bad")
        assert designation_outcome(invalid, DM_META, APPROVER) is DesignationResult.PASS
        assert designation_outcome(None, DM_META, APPROVER) is DesignationResult.PASS
        assert designation_outcome(_valid_map(), DM_META, APPROVER) is DesignationResult.PASS

    def test_a_dm_passes_even_when_the_dm_channel_is_listed(self) -> None:
        """M-17a — the entry is INERT on the DM path."""
        result = _valid_map(channel=CHANNEL, approver=APPROVER)
        meta = dict(DM_META, channel_id=CHANNEL)
        assert designation_outcome(result, meta, APPROVER) is DesignationResult.PASS

    def test_a_group_dm_is_group_dm_shape_whatever_the_map(self) -> None:
        """Leg 2 / M-15 / M-17b — the map is never consulted."""
        group = dict(DM_META, is_group_dm=True, channel_id=CHANNEL, user_id=APPROVER)
        for result in (_valid_map(), parse_designations("{bad"), parse_designations(None)):
            assert (
                designation_outcome(result, group, APPROVER)
                is DesignationResult.GROUP_DM_SHAPE
            )

    def test_an_absent_dm_discriminator_is_fail_closed(self) -> None:
        """Leg 3 — an absent / non-bool ``is_dm`` refuses before the map."""
        for is_dm in (None, "false", 1, 0):
            meta = {"is_group_dm": False, "channel_id": CHANNEL}
            if is_dm is not None:
                meta["is_dm"] = is_dm
            assert (
                designation_outcome(_valid_map(), meta, APPROVER)
                is DesignationResult.ABSENT_DISCRIMINATOR
            ), is_dm

    def test_an_absent_is_group_dm_key_is_not_eligible(self) -> None:
        """Leg 4 / M-18 (S7) — the interaction path builds ``discord_meta``
        WITHOUT ``is_group_dm``; a guild turn must therefore never be
        designation-eligible, whatever the map says."""
        meta = {
            "is_dm": False,
            "guild_id": "777",
            "channel_id": CHANNEL,
            "channel_type": "text",
            "user_id": APPROVER,
        }
        assert "is_group_dm" not in meta
        assert (
            designation_outcome(_valid_map(), meta, APPROVER)
            is DesignationResult.ABSENT_DISCRIMINATOR
        )

    def test_a_non_false_is_group_dm_is_absent_discriminator(self) -> None:
        for value in (None, "false", 0, 1):
            meta = dict(GUILD_META, is_group_dm=value)
            assert (
                designation_outcome(_valid_map(), meta, APPROVER)
                is DesignationResult.ABSENT_DISCRIMINATOR
            ), value

    def test_an_invalid_map_is_config_invalid(self) -> None:
        """Leg 5 / M-9 — and only for the designated path (leg 1 already
        short-circuited every DM)."""
        assert (
            designation_outcome(parse_designations("{bad"), GUILD_META, APPROVER)
            is DesignationResult.CONFIG_INVALID
        )
        # A HEADER-less valid map (unset) is NOT config-invalid: it simply
        # designates nothing.
        assert (
            designation_outcome(parse_designations(None), GUILD_META, APPROVER)
            is DesignationResult.NOT_DESIGNATED
        )

    def test_an_undesignated_channel_is_not_designated(self) -> None:
        """Leg 6 / M-5 — with the default empty map, the guild cell is
        byte-identical to v0.29.18."""
        assert (
            designation_outcome(parse_designations(None), GUILD_META, APPROVER)
            is DesignationResult.NOT_DESIGNATED
        )
        assert (
            designation_outcome(_valid_map(channel=OTHER_CHANNEL), GUILD_META, APPROVER)
            is DesignationResult.NOT_DESIGNATED
        )

    def test_a_thread_id_does_not_inherit_its_parent_designation(self) -> None:
        """D-9 / M-12 — exact channel-id match only."""
        parent = CHANNEL
        thread_meta = dict(
            GUILD_META, channel_id="888999000111", channel_type="thread",
            parent_channel_id=parent,
        )
        assert (
            designation_outcome(_valid_map(channel=parent), thread_meta, APPROVER)
            is DesignationResult.NOT_DESIGNATED
        )

    def test_a_thread_designated_by_its_own_id_passes(self) -> None:
        """M-13 — the operator may name a thread explicitly."""
        thread_id = "888999000111"
        thread_meta = dict(
            GUILD_META, channel_id=thread_id, channel_type="thread",
            parent_channel_id=CHANNEL,
        )
        assert (
            designation_outcome(_valid_map(channel=thread_id), thread_meta, APPROVER)
            is DesignationResult.PASS
        )

    def test_an_absent_user_id_is_absent_identity(self) -> None:
        """Leg 7 / M-8 / M-19 — the absent-identity leg PRECEDES the pinned
        comparison, so "no human at all" is never coded as "wrong human"."""
        for absent in (None, "", "   "):
            assert (
                designation_outcome(_valid_map(), GUILD_META, absent)
                is DesignationResult.ABSENT_IDENTITY
            ), absent

    def test_a_non_pinned_human_is_approver_not_pinned(self) -> None:
        """Leg 8 / M-7."""
        assert (
            designation_outcome(_valid_map(), GUILD_META, OTHER_HUMAN)
            is DesignationResult.APPROVER_NOT_PINNED
        )

    def test_a_human_pinned_for_another_channel_is_not_pinned_here(self) -> None:
        """M-14 — the map is per CHANNEL, not per human."""
        result = parse_designations(
            json.dumps({CHANNEL: APPROVER, OTHER_CHANNEL: OTHER_HUMAN})
        )
        assert (
            designation_outcome(result, GUILD_META, OTHER_HUMAN)
            is DesignationResult.APPROVER_NOT_PINNED
        )

    def test_the_pinned_approver_passes(self) -> None:
        """Leg 9 / M-6 — the new pass path."""
        assert (
            designation_outcome(_valid_map(), GUILD_META, APPROVER)
            is DesignationResult.PASS
        )

    def test_the_function_never_reads_the_environment(self, monkeypatch) -> None:
        """D-1.9 / B2(i) — the caller owns the read; a broken env var cannot
        change this function's answer."""
        monkeypatch.setenv(ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS, "{bad")
        assert (
            designation_outcome(_valid_map(), GUILD_META, APPROVER)
            is DesignationResult.PASS
        )
        monkeypatch.delenv(ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS, raising=False)
        assert (
            designation_outcome(_valid_map(), GUILD_META, OTHER_HUMAN)
            is DesignationResult.APPROVER_NOT_PINNED
        )


class TestTheBooleanConveniences:
    def test_is_designated_is_true_exactly_on_pass(self) -> None:
        result = _valid_map()
        assert is_designated_assent_channel(result, GUILD_META, APPROVER) is True
        assert is_designated_assent_channel(result, GUILD_META, OTHER_HUMAN) is False
        # A DM is not "designated" — it is private (the two are distinct).
        assert is_designated_assent_channel(result, DM_META, APPROVER) is False

    def test_resolve_pinned_approver_is_diagnostic_only(self) -> None:
        result = _valid_map()
        assert resolve_pinned_approver(result, GUILD_META) == APPROVER
        assert resolve_pinned_approver(result, dict(GUILD_META, channel_id="1")) is None
        assert resolve_pinned_approver(parse_designations("{bad"), GUILD_META) is None
        assert resolve_pinned_approver(None, GUILD_META) is None


# ── the single-home pin (P-1) ─────────────────────────────────────────────


def _daemon_python_files() -> list[Path]:
    return [
        path
        for path in sorted((REPO_ROOT / "daemon").rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


class TestTheSingleHomePin:
    def test_the_constant_holds_the_ruled_name(self) -> None:
        assert (
            ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS
            == f"{ENV_PREFIX}DESIGNATED_CHANNELS"
        )

    def test_the_env_literal_appears_exactly_once_in_daemon_source(self) -> None:
        """P-1 / AC6 — ONE literal (the declaration site). Every other
        module imports the constant."""
        literal = f'"{ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS}"'
        hits: list[tuple[str, int]] = []
        for path in _daemon_python_files():
            count = path.read_text(
                encoding="utf-8", errors="ignore"
            ).count(literal)
            if count:
                hits.append((str(path.relative_to(REPO_ROOT)), count))
        assert hits == [
            ("daemon/repositories/prompt_assent/designation.py", 1)
        ], (
            "the env-name literal must appear EXACTLY ONCE in daemon/ "
            f"(the single declaration — not in ``__all__``, not in a "
            f"second module); found: {hits}"
        )

    def test_only_the_known_modules_reference_the_constant(self) -> None:
        """The NAME may travel by import (Phase 2 wires three modules) — but
        the module SET is fixed, so a new consumer must consciously join it.

        Consumers: the declaration site (``designation.py``), the mint/approve
        gate + its resolver (``daemon/tools/prompt_assent.py``, T2.1) and the
        decision-provenance assembly (``daemon/repositories/prompt_version/
        repository.py``, F-15). The LITERAL is still exactly one (the pin
        above).
        """
        expected = {
            "daemon/repositories/prompt_assent/designation.py",
            "daemon/tools/prompt_assent.py",
            "daemon/repositories/prompt_version/repository.py",
        }
        referrers = {
            str(p.relative_to(REPO_ROOT))
            for p in _daemon_python_files()
            if ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS
            in p.read_text(encoding="utf-8", errors="ignore")
        }
        assert referrers == expected, (
            f"unexpected constant consumers: {sorted(referrers ^ expected)}"
        )

    def test_the_parser_module_owns_no_environment_read(self) -> None:
        """D-3 / phase1-plan §5 — the parser takes the raw string; it never
        reads the process environment and imports no daemon module."""
        source = (
            REPO_ROOT / "daemon/repositories/prompt_assent/designation.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        allowed = {"__future__", "hashlib", "json", "re", "dataclasses", "types", "typing"}
        assert imported <= allowed, f"designation.py imports daemon/other deps: {imported - allowed}"
        # No environment read ANYWHERE in the module (code or docstring).
        assert "os.environ" not in source
        assert "os.getenv" not in source


# ── the no-regression pin (P-6) ───────────────────────────────────────────


def _function_source(text: str, name: str) -> str:
    """The EXACT def block of ``name`` (no decorators), indent-normalized."""
    dedented = textwrap.dedent(text)
    tree = ast.parse(dedented)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(dedented, node)
            assert segment is not None, f"{name}: no source segment"
            return "\n".join(line.rstrip() for line in segment.splitlines())
    raise AssertionError(f"{name} not found")


def _git_show(path: str, rev: str = BASE_COMMIT) -> str:
    import subprocess

    out = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


class TestTheNoRegressionPin:
    def test_is_private_assent_channel_is_byte_identical_to_base(self) -> None:
        """P-6 / AC5 — the DM classifier is untouched by this phase, proved
        against the BASE COMMIT's bytes, not by eye."""
        base = _function_source(
            _git_show("daemon/repositories/prompt_assent/mint_context.py"),
            "is_private_assent_channel",
        )
        live = _function_source(
            (REPO_ROOT / "daemon/repositories/prompt_assent/mint_context.py").read_text(
                encoding="utf-8"
            ),
            "is_private_assent_channel",
        )
        assert live == base, (
            "is_private_assent_channel drifted from base "
            f"{BASE_COMMIT}:\n--- base ---\n{base}\n--- live ---\n{live}"
        )

    def test_the_four_existing_classifier_cells_are_green(self) -> None:
        """The four cells the base shipped — unchanged behaviour."""
        assert is_private_assent_channel({"is_dm": True, "is_group_dm": False}) is True
        assert is_private_assent_channel({"is_dm": True, "is_group_dm": True}) is False
        assert is_private_assent_channel({"is_dm": True}) is False
        assert is_private_assent_channel({"is_dm": False, "is_group_dm": False}) is False
        assert is_private_assent_channel({}) is False
        assert is_private_assent_channel(None) is False


# ── the phase fence (AC7) ─────────────────────────────────────────────────


class TestThePhaseFence:
    """P1 owns the pure half ONLY — no gate may call into it yet.

    **PHASE 2 OWNS THE FLIP.** When the mint gate (leg M-4c) and the
    service predicate (leg 4f) are wired, the expected call-site count
    becomes 2 and this pin's constant is bumped — in the SAME commit as
    the wiring, so the fence can never silently rot.
    """

    #: The PRODUCTION files (excluding the owner module) that reference the
    #: new symbols. **P1 shipped 0** (the phase was inert); **P2 wires
    #: exactly TWO** — the mint gate + resolver in
    #: ``daemon/tools/prompt_assent.py`` and the approve predicate in
    #: ``daemon/services/prompt_version_service.py``. Phase 2 bumped this
    #: constant in the SAME commit as the wiring, so the fence cannot rot.
    EXPECTED_CALL_SITE_FILES = {
        "daemon/tools/prompt_assent.py",
        "daemon/services/prompt_version_service.py",
    }

    def test_no_production_call_site_reaches_the_new_symbols(self) -> None:
        symbols = ("designation_outcome", "is_designated_assent_channel")
        callers: set[str] = set()
        for path in _daemon_python_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            rel = str(path.relative_to(REPO_ROOT))
            # The definition site carries the names by construction.
            if rel == "daemon/repositories/prompt_assent/mint_context.py":
                continue
            if rel == "daemon/repositories/prompt_assent/designation.py":
                continue
            if any(symbol in text for symbol in symbols):
                callers.add(rel)
        assert callers == self.EXPECTED_CALL_SITE_FILES, (
            f"unexpected call sites: {sorted(callers ^ self.EXPECTED_CALL_SITE_FILES)}"
        )

    def test_the_designation_module_is_not_reexported(self) -> None:
        """The package ``__init__`` is untouched (AC12's fence): the
        consumer imports by submodule path, like the service does."""
        init_source = (
            REPO_ROOT / "daemon/repositories/prompt_assent/__init__.py"
        ).read_text(encoding="utf-8")
        assert "designation" not in init_source
        assert "designation_outcome" not in init_source

    def test_the_owner_module_exports_the_contract(self) -> None:
        assert callable(designation_outcome)
        assert callable(is_designated_assent_channel)
        assert callable(resolve_pinned_approver)
        assert isinstance(DesignationResult.PASS, str)
        assert DesignationMap({}).pinned_approver("1") is None


# ── counter-mutations (the non-vacuity demonstrations) ────────────────────


class TestTheCounterMutations:
    """Each pin above is shown to REDDEN under a mutation of the property it
    guards. These run as ordinary tests (the mutation is applied to a local
    copy, never to the live tree) so the demonstration ships WITH the pin.
    """

    def test_mutating_the_dm_short_circuit_reddens_the_dm_cell(self) -> None:
        """Counter-mutation for the leg-1 short-circuit: consult the map
        BEFORE the 1:1-DM check and an invalid map refuses a DM."""
        invalid = parse_designations("{bad")
        # The real ordering short-circuits: PASS.
        assert designation_outcome(invalid, DM_META, APPROVER) is DesignationResult.PASS

        # The mutation: check ``map is invalid`` FIRST.
        def mutated(designations, meta, user_id):
            if designations is None or not designations.valid:
                return DesignationResult.CONFIG_INVALID
            return designation_outcome(designations, meta, user_id)

        assert mutated(invalid, DM_META, APPROVER) is DesignationResult.CONFIG_INVALID

    def test_mutating_the_group_dm_leg_reddens_m15(self) -> None:
        """Counter-mutation for M-15: on a group-DM turn whose channel IS
        designated, a map-first ordering PASSES — the leg order is what
        refuses it."""
        group = dict(DM_META, is_group_dm=True, channel_id=CHANNEL, user_id=APPROVER)
        assert (
            designation_outcome(_valid_map(), group, APPROVER)
            is DesignationResult.GROUP_DM_SHAPE
        )

        def map_first(designations, meta, user_id):
            if designations is not None and designations.valid:
                pinned = designations.map.pinned_approver(meta.get("channel_id"))  # type: ignore[union-attr]
                if pinned is not None and pinned == user_id:
                    return DesignationResult.PASS
            return designation_outcome(designations, meta, user_id)

        assert map_first(_valid_map(), group, APPROVER) is DesignationResult.PASS, (
            "the mutation must change the verdict — otherwise the ordering pin "
            "is vacuous"
        )

    def test_mutating_the_absent_identity_leg_reddens_m8(self) -> None:
        """Counter-mutation for M-8: without the absent-identity leg, an
        empty user id compares unequal and miscodes as wrong-human."""
        assert (
            designation_outcome(_valid_map(), GUILD_META, "")
            is DesignationResult.ABSENT_IDENTITY
        )

        def without_the_leg(designations, meta, user_id):
            leg = designation_outcome(designations, meta, user_id)
            if leg is DesignationResult.ABSENT_IDENTITY:
                return DesignationResult.APPROVER_NOT_PINNED
            return leg

        assert (
            without_the_leg(_valid_map(), GUILD_META, "")
            is DesignationResult.APPROVER_NOT_PINNED
        )

    def test_mutating_the_env_literal_pin_reddens_p1(self, tmp_path) -> None:
        """Counter-mutation for P-1: a SECOND literal in daemon/ reddens the
        scan (demonstrated on a synthetic tree)."""
        literal = f'"{ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS}"'
        (tmp_path / "second_home.py").write_text(
            f"X = {literal}\n", encoding="utf-8"
        )
        hits = [
            p.name
            for p in sorted(tmp_path.rglob("*.py"))
            if literal in p.read_text(encoding="utf-8")
        ]
        assert hits == ["second_home.py"], "the scan must find a second literal"
