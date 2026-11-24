"""POK write-gate judge unit pack (Wave 2 Phase 7/8).

Legs: the CLOSED verdict / family vocabularies (the R-RET/R-PROV rider
added RULES, never tokens); the verdict PARSER (one code fence
tolerated, closed-set verdict + family, garbage → the D2 fail-closed
``pok_gate_unavailable`` — NEVER re-labelled ``pok_gate_reject``); the
five family CONTRACT templates (each names its alternative home + the
Emperor's trace phrase); the bounded ACTIVE-row digest (the ≤8000-char
ceiling binds, K is the observed sizing WITHIN it); the purpose-bound
ALIAS seam (``POK_GATE_MODEL`` — never the global model); the STUB mode
(rule-based, never a pass-through, prod-forced to llm); the Pattern-C
timeout knobs; the bounded verdict CACHE (ASK replays AS-ASK with a
FRESH gate_id; the ANSWERED outcome is never cached as ACCEPT; stub
and llm entries are segregated); the confidence → ASK rule; the R-RET
advisory lexical leg; the R-PROV protective clause; the pending-ASK
store and its six typed refusals.

NO LLM and NO live store: the judge is a pure function over injected
rows, and every exercise runs on synthetic fixtures.
"""

from __future__ import annotations

import time

import pytest

from daemon.services import pok_gate
from daemon.services.pok_gate import (
    ASK_CONFIDENCE_THRESHOLD,
    DEFAULT_MERGE_POLICY,
    FAMILY_TEMPLATES,
    GATE_FAMILIES,
    GATE_VERDICTS,
    ActiveRow,
    GateVerdict,
    PendingGateStore,
    POKGateUnavailable,
    VerdictCache,
    build_active_digest,
    build_judge_prompt,
    cache_key,
    classify_answer,
    family_home,
    family_template,
    judge_pok_write,
    normalize_reason,
    parse_verdict_payload,
    resolve_gate_model,
    resolve_gate_mode,
)
from daemon.services.pok_gate_timeout_resolver import (
    DEFAULT_GATE_TIMEOUT_S,
    GATE_MAX_RETRIES,
    GATE_WALL_CLOCK_CAP_S,
    MAX_GATE_TIMEOUT_S,
    MIN_GATE_TIMEOUT_S,
    get_gate_timeout_s,
    get_gate_wall_clock_cap_s,
    reset_gate_timeout_resolver_for_tests,
)

PROJECT = "p1"


def _row(slug: str, body: str = "an operating fact", **kwargs) -> ActiveRow:
    defaults = {
        "slug": slug,
        "body": body,
        "family": None,
        "pinned": False,
        "source_tier": "agent",
        "verify_count": 0,
        "last_verified": "2026-11-24T00:00:00+00:00",
    }
    defaults.update(kwargs)
    return ActiveRow(**defaults)


def _judge_payload(**overrides) -> str:
    import json

    payload = {
        "verdict": "ACCEPT",
        "family": "scope",
        "explanation": "the fact is operational how-to for this project",
        "alternative": None,
        "confidence": 0.9,
    }
    payload.update(overrides)
    return json.dumps(payload)


# ── the closed vocabularies ────────────────────────────────────────────────


class TestClosedVocabularies:
    def test_the_verdict_set_is_the_four_value_set(self) -> None:
        assert GATE_VERDICTS == ("ACCEPT", "MERGE", "ASK", "REJECT")

    def test_the_family_set_is_the_five_value_set(self) -> None:
        assert GATE_FAMILIES == (
            "boundary",
            "reason",
            "durability",
            "redundancy",
            "scope",
        )

    def test_the_rider_added_rules_not_tokens(self) -> None:
        """The R-RET / R-PROV rider grew the RULE set, never the token
        closed set (gate-rules-r6-r7-amendment §2.4)."""
        assert "R-RET" in pok_gate.GATE_RULES
        assert "R-PROV" in pok_gate.GATE_RULES
        assert len(GATE_VERDICTS) == 4
        assert len(GATE_FAMILIES) == 5
        assert not any("retriev" in v.lower() for v in GATE_VERDICTS)
        assert not any("retriev" in f.lower() for f in GATE_FAMILIES)

    def test_the_merge_policy_default_is_ask_with_proposal(self) -> None:
        """T7.6 — the F2 default is ASK_WITH_PROPOSAL."""
        assert DEFAULT_MERGE_POLICY == "ASK_WITH_PROPOSAL"
        assert pok_gate.MERGE_POLICIES == (
            "ASK_WITH_PROPOSAL",
            "AUTO",
            "ASK_NO_PROPOSAL",
        )


# ── the verdict parser (T7.3) ───────────────────────────────────────────────


class TestVerdictParser:
    def test_a_valid_payload_parses(self) -> None:
        parsed = parse_verdict_payload(_judge_payload())
        assert parsed["verdict"] == "ACCEPT"
        assert parsed["family"] == "scope"
        assert parsed["confidence"] == 0.9

    def test_one_code_fence_is_tolerated(self) -> None:
        parsed = parse_verdict_payload(
            "```json\n" + _judge_payload() + "\n```"
        )
        assert parsed["verdict"] == "ACCEPT"

    def test_garbage_is_unavailable_never_reject(self) -> None:
        """D2 — an unparsable answer fails closed; it is NEVER
        re-labelled ``pok_gate_reject``."""
        for raw in ("", "not json at all", "{", "[]", "null"):
            with pytest.raises(POKGateUnavailable) as excinfo:
                parse_verdict_payload(raw)
            assert excinfo.value.reason == "unparsable"
            assert excinfo.value.code == "pok_gate_unavailable"

    def test_an_unknown_verdict_is_unparsable(self) -> None:
        with pytest.raises(POKGateUnavailable):
            parse_verdict_payload(_judge_payload(verdict="MAYBE"))

    def test_an_unknown_family_is_unparsable(self) -> None:
        with pytest.raises(POKGateUnavailable):
            parse_verdict_payload(_judge_payload(family="vibes"))

    def test_the_confidence_is_clamped(self) -> None:
        parsed = parse_verdict_payload(_judge_payload(confidence=7.5))
        assert parsed["confidence"] == 1.0
        parsed = parse_verdict_payload(_judge_payload(confidence=-2))
        assert parsed["confidence"] == 0.0

    def test_a_credential_shaped_excerpt_is_redacted(self) -> None:
        """T8.18 — the parser scrubs credential-shaped content."""
        parsed = parse_verdict_payload(
            _judge_payload(explanation="the key is api_key=abcdefgh12345678")
        )
        assert "abcdefgh12345678" not in parsed["explanation"]
        assert "REDACTED" in parsed["explanation"]


# ── the family CONTRACT templates (T7.4 / phase11 T2) ─────────────────────


class TestFamilyContractTemplates:
    """T7.4 — five CONTRACT tests: each family names its alternative
    home (R5) and carries the Emperor's canonical trace phrase."""

    @pytest.mark.parametrize(
        "family,home,home_marker",
        [
            ("boundary", "rag", "experience()"),
            ("reason", "sharpen_reason", "sharpen the reason"),
            ("durability", "release_tree", "release tree"),
            ("redundancy", "supersede", "supersede"),
            ("scope", "other_project", "this project"),
        ],
    )
    def test_each_family_names_its_home_and_the_emperor_trace(
        self, family: str, home: str, home_marker: str
    ) -> None:
        template = family_template(family)
        assert home_marker in template, family
        assert pok_gate.EMPEROR_TRACE_PHRASE in template, family
        assert family_home(family) == home

    def test_every_family_has_a_template(self) -> None:
        assert set(FAMILY_TEMPLATES) == set(GATE_FAMILIES)


# ── the digest (T7.9) ──────────────────────────────────────────────────────


class TestDigest:
    def test_the_digest_is_within_the_ceiling(self) -> None:
        rows = [_row(f"row-{i:03d}", "x" * 240) for i in range(200)]
        digest = build_active_digest(
            candidate_slug="new-fact",
            candidate_body="a body about deploys",
            rows=rows,
            reason="the deploy invariant",
        )
        assert len(digest) <= pok_gate.POK_GATE_DIGEST_MAX_CHARS

    def test_the_boundary_clause_is_never_dropped(self) -> None:
        rows = [_row(f"row-{i:03d}", "x" * 240) for i in range(500)]
        digest = build_active_digest(
            candidate_slug="new-fact",
            candidate_body="a body about deploys",
            rows=rows,
            reason="the deploy invariant",
        )
        assert "INTRA-STORE" in digest
        assert "A RAG twin is" in digest

    def test_the_zero_overlap_fallback_is_family_first_not_slug_order(self) -> None:
        """The zero-overlap fallback is same-family-first, then
        most-recently-verified — NOT slug order."""
        rows = [
            _row("aaa-first", "unrelated words", family="other", last_verified="2026-11-01"),
            _row("zzz-last", "unrelated words", family="target", last_verified="2026-01-01"),
            _row("mmm-mid", "unrelated words", family="target", last_verified="2026-11-02"),
        ]
        digest = build_active_digest(
            candidate_slug="target-01-thing",
            candidate_body="completely different vocabulary here",
            rows=rows,
            reason="r",
        )
        # The same-family rows come first even though their slugs sort
        # after ``aaa-first``.
        assert digest.index("mmm-mid") < digest.index("aaa-first")
        assert digest.index("zzz-last") < digest.index("aaa-first")

    def test_the_row_bodies_are_one_line_slices(self) -> None:
        digest = build_active_digest(
            candidate_slug="s",
            candidate_body="b",
            rows=[_row("target-01", "y" * 300)],
            reason="r",
        )
        assert "y" * 80 in digest
        assert "y" * 81 not in digest


# ── the alias seam (T7.13) ─────────────────────────────────────────────────


class TestAliasSeam:
    def test_the_default_alias_is_the_literal_quick(self) -> None:
        assert resolve_gate_model({}) == "quick"
        assert pok_gate.POK_GATE_DEFAULT_MODEL == "quick"

    def test_the_env_seam_overrides_the_alias(self) -> None:
        assert resolve_gate_model({"POK_GATE_MODEL": "judge-1"}) == "judge-1"

    def test_the_alias_is_never_the_global_model(self) -> None:
        """The alias is a purpose-id — it is never resolved from the
        config's model fields (which ``set_title_model_fallback``
        rewrites to the GLOBAL model). The pin reads the CODE (an
        attribute access), not the prose."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(pok_gate))
        attributes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                attributes.add(node.attr)
        assert "model_quick" not in attributes
        assert "model" not in attributes
        # The env seam is the ONLY model source.
        assert pok_gate.POK_GATE_MODEL_ENV == "POK_GATE_MODEL"
        assert resolve_gate_model({"POK_GATE_MODEL": ""}) == "quick"


# ── the stub mode (T7.11) ──────────────────────────────────────────────────


class TestStubMode:
    def test_the_stub_is_rule_based_and_rejects_a_known_bad_fixture(self) -> None:
        verdict = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="s",
            body="",
            reason="r",
            rows=[],
            mode="stub",
            is_production=False,
        )
        assert verdict.verdict == "REJECT"
        assert verdict.family == "boundary"

    def test_the_stub_stamps_its_alias(self) -> None:
        verdict = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="s",
            body="a well formed operating fact",
            reason="the operational invariant",
            rows=[],
            mode="stub",
            is_production=False,
        )
        assert verdict.model_alias == "stub"

    def test_prod_forces_the_llm_mode(self) -> None:
        pok_gate.reset_gate_mode_warn_for_tests()
        assert resolve_gate_mode({"POK_GATE_MODE": "stub"}, is_production=True) == "llm"
        assert (
            resolve_gate_mode({"POK_GATE_MODE": "stub"}, is_production=False) == "stub"
        )
        pok_gate.reset_gate_mode_warn_for_tests()


# ── the timeout knobs (T7.10) ─────────────────────────────────────────────


class TestTimeoutResolver:
    def setup_method(self) -> None:
        reset_gate_timeout_resolver_for_tests()

    def teardown_method(self) -> None:
        reset_gate_timeout_resolver_for_tests()

    def test_the_defaults_are_the_overturned_ones(self) -> None:
        assert DEFAULT_GATE_TIMEOUT_S == 20.0
        assert GATE_WALL_CLOCK_CAP_S == 45.0
        assert GATE_MAX_RETRIES == 1

    def test_the_min_clamp_is_ten_seconds(self) -> None:
        import os

        os.environ["ENSEMBLE_POK_GATE_TIMEOUT_S"] = "3"
        reset_gate_timeout_resolver_for_tests()
        assert get_gate_timeout_s() == MIN_GATE_TIMEOUT_S
        del os.environ["ENSEMBLE_POK_GATE_TIMEOUT_S"]
        reset_gate_timeout_resolver_for_tests()

    def test_the_max_clamp_is_one_hundred_eighty(self) -> None:
        import os

        os.environ["ENSEMBLE_POK_GATE_TIMEOUT_S"] = "9999"
        reset_gate_timeout_resolver_for_tests()
        assert get_gate_timeout_s() == MAX_GATE_TIMEOUT_S
        del os.environ["ENSEMBLE_POK_GATE_TIMEOUT_S"]
        reset_gate_timeout_resolver_for_tests()

    def test_an_invalid_value_fails_open_to_the_default(self) -> None:
        import os

        os.environ["ENSEMBLE_POK_GATE_TIMEOUT_S"] = "abc"
        reset_gate_timeout_resolver_for_tests()
        assert get_gate_timeout_s() == DEFAULT_GATE_TIMEOUT_S
        del os.environ["ENSEMBLE_POK_GATE_TIMEOUT_S"]
        reset_gate_timeout_resolver_for_tests()

    def test_the_wall_clock_is_a_constant(self) -> None:
        assert get_gate_wall_clock_cap_s() == 45.0


# ── the verdict cache (T7.8 / T8.13) ───────────────────────────────────────


class TestVerdictCache:
    def test_the_key_uses_the_stripped_body_and_the_normalized_reason(self) -> None:
        a = cache_key(project_id=PROJECT, slug="s", body="  body  ", reason="Why  It")
        b = cache_key(project_id=PROJECT, slug="s", body="body", reason="why it")
        c = cache_key(project_id=PROJECT, slug="s", body="BODY", reason="why it")
        assert a == b
        assert a != c  # the body hash is over the STRIPPED string, not normalized

    def test_the_reason_normalization_lowercases_and_collapses(self) -> None:
        assert normalize_reason("  Why   IT  ") == "why it"

    def test_the_cache_is_bounded_and_lru(self) -> None:
        cache = VerdictCache(max_entries=2)
        for i in range(3):
            cache.put(
                cache_key(project_id=PROJECT, slug=f"s{i}", body="b", reason="r"),
                GateVerdict(verdict="ACCEPT", family="scope"),
            )
        assert len(cache) == 2

    def test_the_stub_and_llm_entries_are_segregated(self) -> None:
        stub_key = cache_key(
            project_id=PROJECT, slug="s", body="b", reason="r", mode="stub"
        )
        llm_key = cache_key(
            project_id=PROJECT, slug="s", body="b", reason="r", mode="llm"
        )
        assert stub_key != llm_key


# ── the judge (T7.1 / T7.7 / T7.15) ────────────────────────────────────────


def _judge(**overrides) -> dict:
    import json

    payload = {
        "verdict": "ACCEPT",
        "family": "scope",
        "explanation": "ok",
        "alternative": None,
        "confidence": 0.9,
    }
    payload.update(overrides)
    return payload


class TestJudge:
    def test_a_low_confidence_reject_becomes_an_ask(self) -> None:
        """T7.7 — confidence ≤ 2/3 → ASK, NEVER auto-reject."""
        verdict = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="s",
            body="a body",
            reason="the claim",
            rows=[],
            mode="llm",
            is_production=False,
            llm_call=lambda prompt: __import__("json").dumps(
                _judge(verdict="REJECT", family="boundary", confidence=0.5)
            ),
        )
        assert verdict.verdict == "ASK"
        assert verdict.question

    def test_a_confident_reject_stays_a_reject(self) -> None:
        verdict = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="s",
            body="a body",
            reason="the claim",
            rows=[],
            mode="llm",
            is_production=False,
            llm_call=lambda prompt: __import__("json").dumps(
                _judge(verdict="REJECT", family="boundary", confidence=0.95)
            ),
        )
        assert verdict.verdict == "REJECT"
        assert verdict.alternative  # the alternative home is named (R5)

    def test_a_merge_naming_an_unknown_target_becomes_an_ask(self) -> None:
        verdict = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="s",
            body="a body",
            reason="the claim",
            rows=[],
            mode="llm",
            is_production=False,
            llm_call=lambda prompt: __import__("json").dumps(
                _judge(verdict="MERGE", family="redundancy", target_slug="nope")
            ),
        )
        assert verdict.verdict == "ASK"

    def test_a_merge_naming_a_known_target_is_a_merge(self) -> None:
        verdict = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="s",
            body="a body",
            reason="the claim",
            rows=[_row("target-01", "the same fact")],
            mode="llm",
            is_production=False,
            llm_call=lambda prompt: __import__("json").dumps(
                _judge(
                    verdict="MERGE",
                    family="redundancy",
                    target_slug="target-01",
                    confidence=0.9,
                )
            ),
        )
        assert verdict.verdict == "MERGE"
        assert verdict.target_slug == "target-01"
        assert verdict.merge_kind == "no_delta"  # no merged body proposed

    def test_a_proposed_body_makes_the_merge_a_delta(self) -> None:
        verdict = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="s",
            body="a body",
            reason="the claim",
            rows=[_row("target-01", "the same fact")],
            mode="llm",
            is_production=False,
            llm_call=lambda prompt: __import__("json").dumps(
                _judge(
                    verdict="MERGE",
                    family="redundancy",
                    target_slug="target-01",
                    merged_body_proposal="the same fact plus nuance",
                )
            ),
        )
        assert verdict.verdict == "MERGE"
        assert verdict.merge_kind == "delta"
        assert verdict.proposed_body_sha256

    def test_a_cached_ask_replays_as_ask_with_a_fresh_gate_id(self) -> None:
        """T8.13 — the cached ASK re-asks with the SAME question but a
        FRESH gate_id."""
        cache = VerdictCache()
        calls = []

        def _llm(prompt: str) -> str:
            calls.append(prompt)
            return __import__("json").dumps(
                _judge(verdict="ASK", family="reason", question="is this right?")
            )

        first = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="s",
            body="a body",
            reason="the claim",
            rows=[],
            mode="llm",
            is_production=False,
            llm_call=_llm,
            cache=cache,
        )
        second = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="s",
            body="a body",
            reason="the claim",
            rows=[],
            mode="llm",
            is_production=False,
            llm_call=_llm,
            cache=cache,
        )
        assert first.verdict == "ASK"
        assert second.verdict == "ASK"
        assert second.question == first.question
        assert second.gate_id != first.gate_id
        assert second.cache_hit is True
        assert len(calls) == 1  # the replay did NOT call the LLM again

    def test_the_gate_writes_nothing(self) -> None:
        """T8.1 — the gate is a pure function: no store write."""
        from sqlalchemy import create_engine

        engine = create_engine("sqlite://")
        verdict = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="s",
            body="a body",
            reason="the claim",
            rows=[],
            mode="stub",
            is_production=False,
        )
        assert verdict.verdict
        # The engine was never touched — the gate does not even know it.
        assert engine is not None

    def test_an_unavailable_judge_is_never_a_reject(self) -> None:
        """D2 — a provider failure is ``pok_gate_unavailable``."""

        def _boom(prompt: str) -> str:
            raise RuntimeError("provider is down")

        with pytest.raises(POKGateUnavailable) as excinfo:
            judge_pok_write(
                project_id=PROJECT,
                project_name="proj",
                slug="s",
                body="a body",
                reason="the claim",
                rows=[],
                mode="llm",
                is_production=False,
                llm_call=_boom,
            )
        assert excinfo.value.code == "pok_gate_unavailable"
        assert excinfo.value.reason == "llm_error"

    def test_an_unparsable_answer_is_never_a_reject(self) -> None:
        with pytest.raises(POKGateUnavailable) as excinfo:
            judge_pok_write(
                project_id=PROJECT,
                project_name="proj",
                slug="s",
                body="a body",
                reason="the claim",
                rows=[],
                mode="llm",
                is_production=False,
                llm_call=lambda prompt: "total garbage",
            )
        assert excinfo.value.reason == "unparsable"


# ── the R-RET advisory leg (T7.15 / T8.15) ────────────────────────────────


class TestRetrievabilityLeg:
    def test_a_shared_vocabulary_does_not_flag(self) -> None:
        flagged, proposal = pok_gate._retrievability_flag(
            slug="deploy-01-pipeline",
            body="the deploy pipeline must run the smoke gate",
        )
        assert flagged is False
        assert proposal is None

    def test_a_disjoint_slug_flags_and_proposes(self) -> None:
        flagged, proposal = pok_gate._retrievability_flag(
            slug="zzz-99-placeholder",
            body="the deploy pipeline must run the smoke gate",
        )
        assert flagged is True
        assert proposal

    def test_the_new_write_asks_for_a_rename_in_enforce_mode(self) -> None:
        """T8.15 — a NEW-write retrievability failure is ASK-with-rename
        (NEVER auto-reject)."""
        verdict = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="zzz-99-placeholder",
            body="the deploy pipeline must run the smoke gate",
            reason="the deploy invariant",
            rows=[],
            mode="llm",
            is_production=False,
            shadow=False,
            llm_call=lambda prompt: __import__("json").dumps(_judge()),
        )
        assert verdict.verdict == "ASK"
        assert verdict.proposed_slug
        assert verdict.rule_applied == "R-RET"

    def test_in_shadow_the_retrievability_finding_is_advisory_only(self) -> None:
        verdict = judge_pok_write(
            project_id=PROJECT,
            project_name="proj",
            slug="zzz-99-placeholder",
            body="the deploy pipeline must run the smoke gate",
            reason="the deploy invariant",
            rows=[],
            mode="llm",
            is_production=False,
            shadow=True,
            llm_call=lambda prompt: __import__("json").dumps(_judge()),
        )
        assert verdict.verdict == "ACCEPT"
        assert verdict.advisory is True
        assert verdict.retrievability_flagged is True


# ── the prompt clauses (T7.12) ────────────────────────────────────────────


class TestPromptClauses:
    def _prompt(self, **overrides) -> str:
        kwargs = {
            "project_name": "proj",
            "slug": "s",
            "body": "b",
            "reason": "the claim",
            "digest": "DIGEST",
        }
        kwargs.update(overrides)
        return build_judge_prompt(**kwargs)

    def test_the_r1_beats_r4_clause_is_verbatim(self) -> None:
        assert "R1 beats R4" in self._prompt()
        assert "A RAG twin is NOT a rejection basis" in self._prompt()

    def test_the_r_prov_protective_clause_is_present(self) -> None:
        assert "R-PROV" in self._prompt()
        assert "EVIDENCE FOR an operational claim" in self._prompt()

    def test_the_r_ret_question_is_present(self) -> None:
        assert "R-RET" in self._prompt()
        assert "operative terms" in self._prompt()

    def test_the_judge_question_is_present(self) -> None:
        assert "What breaks in THIS repo if we drop this?" in self._prompt()

    def test_the_r1_question_never_asks_is_this_general(self) -> None:
        prompt = self._prompt()
        assert (
            "Does the body still teach the same lesson in a repo with no "
            "ensemble?" in prompt
        )
        # The forbidden phrasing appears ONLY inside the prohibition.
        assert "NEVER ask 'is this general?'" in prompt
        assert prompt.count("is this general") == 1

    def test_the_r3_clause_is_advisory_only(self) -> None:
        assert "ADVISORY FLAG ONLY" in self._prompt()
        assert "NEVER a rejection basis" in self._prompt()


# ── the pending-ASK store (T8.6 / T8.7) ───────────────────────────────────


def _pending(store: PendingGateStore, **overrides):
    from daemon.services.pok_gate import PendingGate

    defaults = {
        "gate_id": "g-1",
        "project_id": PROJECT,
        "owner_agent_id": "developer",
        "owner_instance_id": "inst-1",
        "slug": "s",
        "body": "b",
        "reason": "r",
        "domain": "other",
        "provenance": "observed",
        "audience": "operation",
        "source_tier": None,
        "question": "is this right?",
        "verdict": GateVerdict(verdict="ASK", family="reason"),
        "created_at": time.monotonic(),
        "expires_at": time.monotonic() + 1200,
    }
    defaults.update(overrides)
    return store.mint(PendingGate(**defaults))


class TestPendingGateStore:
    def test_the_default_ttl_is_twelve_hundred_seconds(self) -> None:
        assert pok_gate.DEFAULT_ASK_TTL_S == 1200.0
        assert pok_gate.MIN_ASK_TTL_S == 120.0
        assert pok_gate.MAX_ASK_TTL_S == 3600.0

    def test_an_unknown_gate_is_refused(self) -> None:
        store = PendingGateStore()
        pending, refusal = store.resolve(
            "nope", agent_id="developer", instance_id="inst-1", answer="yes"
        )
        assert pending is None
        assert refusal == "pok_gate_unknown"

    def test_an_expired_gate_is_refused(self) -> None:
        store = PendingGateStore()
        _pending(store, expires_at=time.monotonic() - 1)
        pending, refusal = store.resolve(
            "g-1", agent_id="developer", instance_id="inst-1", answer="yes"
        )
        assert pending is None
        assert refusal == "pok_gate_expired"

    def test_a_foreign_agent_is_refused(self) -> None:
        store = PendingGateStore()
        _pending(store)
        pending, refusal = store.resolve(
            "g-1", agent_id="someone-else", instance_id="inst-1", answer="yes"
        )
        assert pending is None
        assert refusal == "pok_gate_not_owner"

    def test_an_already_answered_gate_is_refused(self) -> None:
        store = PendingGateStore()
        _pending(store)
        store.consume("g-1")
        pending, refusal = store.resolve(
            "g-1", agent_id="developer", instance_id="inst-1", answer="yes"
        )
        assert pending is None
        assert refusal == "pok_gate_already_answered"

    def test_a_missing_answer_is_refused(self) -> None:
        store = PendingGateStore()
        _pending(store)
        pending, refusal = store.resolve(
            "g-1", agent_id="developer", instance_id="inst-1", answer="   "
        )
        assert pending is None
        assert refusal == "pok_gate_answer_required"

    def test_the_six_typed_refusals_are_the_approved_set(self) -> None:
        assert set(pok_gate.PENDING_ASK_REFUSALS) == {
            "pok_gate_unknown",
            "pok_gate_expired",
            "pok_gate_already_answered",
            "pok_gate_not_owner",
            "pok_gate_answer_required",
            "pok_gate_answer_unrecognized",
        }

    def test_the_answer_classifier_never_defaults_to_accept(self) -> None:
        assert classify_answer("yes, confirmed") == "confirm"
        assert classify_answer("no, declined") == "decline"
        assert classify_answer("maybe later") is None
        assert classify_answer("") is None
        assert classify_answer(None) is None
