"""Allow-list SEMANTICS of the tool-usage digest reader — spec v4 §3.9.3.

Twelve nodes (39-47, 58, 59, 60) bound to the READER that already landed in
``daemon/services/tool_usage_digest.py`` and to the artifact it reads
(``<code tree>/.agents/shared/planning/tool-usage-metrics-p1/allowlists.json``,
schema 2).  Nothing here re-implements the reader: every assertion goes
through the reader's own API (``allowlist_source`` / ``allowlists`` /
``effective_agent_tools`` / ``grant_verdict``), and the ONE runtime the
equivalence proof compares against is the house resolver
``daemon.tools.instance.resolve_tool_filter``.

THE FOUR LOAD-BEARING FACTS THIS SUITE PINS
--------------------------------------------
1. THE CATEGORY-KEY UNIVERSE is ``categories ∪ runtime_only_categories``
   (§3.6.2a).  ``mcp`` and ``plane`` are CATEGORIES with no static members:
   an allow entry naming one is never a literal grant of a tool, and the
   members the runtime sweeps into them are call-time state -> ``unknown``.
2. THE FLAT EFFECTIVE-GRANT MAP IS THE VERDICT AUTHORITY (§3.6.3 step 1):
   membership in the published flat map IS a runtime grant.  The step order
   is load-bearing — step 1 is tested BEFORE the degraded-artifact gate
   (step 2) and BEFORE the agent-coverage gate (step 3); moving either gate
   ahead of step 1 turns pinned verdicts into ``unknown``.
3. THE MUTATION-WIRING RULE (§3.9.3, normative): a mutant replaces the
   READER's grant function AT THE SAME ENTRY POINT the verdict path
   consults (``effective_agent_tools``), installed by ``monkeypatch.setattr``
   from a MODULE-LEVEL fixture, keeps the 3-tuple signature
   ``(granted, reasons, granted_categories)``, and the mutant leg RE-RUNS
   the SHARED assertion body of the real probe(s) and asserts that body
   FAILS.  A mutant under which the real probes stay green is VACUOUS and
   fails this suite's gate.
4. THE FIXTURE-MAP (A3): the fixture's tool-category map is the artifact's
   category map PLUS the two runtime-only keys mapped to EMPTY lists.  The
   runtime's map carries ``mcp``/``plane`` as REGISTERED keys, so
   ``item in tool_categories`` is TRUE for them and the runtime EXPANDS them
   (the ``mcp_`` sweep and the ``plane_`` prefix sweep) instead of
   literalising them.  A fixture that omits those two keys takes the
   literal branch for the 26 agents that allow ``"mcp"``, and the whole
   equivalence proof becomes a false red.

HERMETIC: the artifact is read from the CODE TREE that owns this file (never
from a HOME-derived install path), no database is contacted, nothing is
written anywhere, and the only imports are the module under test plus the
house resolver the equivalence proof is required to call.
"""

from __future__ import annotations

import pytest

from daemon.services import tool_usage_digest as tud

#: The FIXED fixture universe's extra names (§3.9.3 node 44).  The universe
#: itself is "the artifact's category map + a fixed ``all_tool_names`` set";
#: these are the call-time names the runtime's sweeps need in order to be
#: exercised at all.  ``mcp_rpc_*`` and ``plane_sync_project`` are OWNED by a
#: registered category, so the sweeps must NOT claim them.
FIXTURE_EXTRA_NAMES = frozenset(
    {
        "mcp_github_create_issue",  # unowned mcp_-shaped -> swept into "mcp"
        "mcp_rpc_command",  # owned by "mcp_rpc" (privileged) -> NOT swept
        "mcp_rpc_connect",  # owned by "mcp_rpc" (privileged) -> NOT swept
        "mcp_rpc_list_machine",  # owned by "mcp_rpc" (privileged) -> NOT swept
        "plane_sync_project",  # owned by "plane_sync" -> NOT swept into "plane"
        "plane_list_projects",  # unowned plane_-shaped -> swept into "plane"
        "plane_list_work_items",  # unowned plane_-shaped -> swept into "plane"
    }
)


# ---------------------------------------------------------------------------
# Shared helpers (the shared assertion BODIES are re-run by the mutant legs)
# ---------------------------------------------------------------------------


def _is_mcp_shaped(name: str) -> bool:
    """The RUNTIME'S EXACT MCP-shape predicate (``daemon/tools/instance.py:522``).

    ``name.startswith("mcp_") and "_" in name[4:]`` — AT LEAST ONE underscore
    after the prefix.  The runtime's own comment says "at least 2 underscores"
    (it counts the prefix's own underscore); the CODE is the authority (A2).
    A stricter predicate would make a runtime-swept name such as ``mcp_x_y``
    fall through to a confident "no" — the cardinal defect.
    """
    return name.startswith("mcp_") and "_" in name[4:]


def _agent_view(src: tud.AllowSource, agent_id: str) -> dict:
    """The reader's per-agent view (``allow`` / ``deny`` / ``innate``)."""
    info = src.agents[agent_id]
    return {
        "allow": list(info.get("allow", [])),
        "deny": list(info.get("deny", [])),
        "innate": list(info.get("innate", [])),
    }


def _flat_grant_map(src: tud.AllowSource) -> dict[str, set[str]]:
    """The reader's FLAT EFFECTIVE-GRANT MAP — the VERDICT AUTHORITY.

    Mirrors ``tud.allowlists()`` but takes an explicit source, so synthetic
    sources work too.  It calls ``tud.effective_agent_tools`` — the SAME
    entry point the verdict path consults — so a mutant installed there is
    wired into the map the probes read.
    """
    out: dict[str, set[str]] = {}
    for agent_id in src.agents:
        granted, _reasons, _cats = tud.effective_agent_tools(
            _agent_view(src, agent_id), src
        )
        out[str(agent_id)] = set(granted)
    return out


def _fixture_category_map(src: tud.AllowSource) -> dict[str, list[str]]:
    """A3-FIXTURE-MAP: the artifact's category map PLUS the runtime-only
    keys mapped to EMPTY lists (the runtime registers them, then sweeps them)."""
    tool_categories = {key: list(tools) for key, tools in src.categories.items()}
    for key in src.runtime_only_categories:
        tool_categories[key] = []
    return tool_categories


def _source_with_agent(
    src: tud.AllowSource,
    agent_id: str,
    allow: list[str],
    deny: list[str] | None = None,
    innate: list[str] | None = None,
) -> tud.AllowSource:
    """A copy of the real source with ONE extra agent (the corpus cannot
    supply every shape the reader must handle)."""
    agents = {key: dict(value) for key, value in src.agents.items()}
    agents[agent_id] = {
        "allow": list(allow),
        "deny": list(deny or []),
        "innate": list(innate or []),
    }
    return tud.AllowSource(
        state=src.state,
        schema_version=src.schema_version,
        agents=agents,
        categories={key: list(tools) for key, tools in src.categories.items()},
        runtime_only_categories=list(src.runtime_only_categories),
        privileged_categories=list(src.privileged_categories),
        dynamic_prefixes=list(src.dynamic_prefixes),
        innate_skill_categories=dict(src.innate_skill_categories),
        known_tool_names=set(src.known_tool_names),
        unresolved=dict(src.unresolved),
        provenance=dict(src.provenance),
    )


def _non_privileged_universe(src: tud.AllowSource) -> set[str]:
    """The default universe an inherit-all agent receives: every
    NON-PRIVILEGED category's tools (``instance.py:587-591``)."""
    privileged = set(src.privileged_categories)
    universe: set[str] = set()
    for category, tools in src.categories.items():
        if category in privileged:
            continue
        universe.update(tools)
    return universe


def _assert_primary_probe(src: tud.AllowSource, granted_by_agent: dict) -> None:
    """THE ASSERTION BODY of node 39 — re-runnable under a mutant.

    ``explorer`` allows the category ``rag`` and denies ``rag_query`` (and
    two siblings).  The verdict is ``"no"`` AND the reason names BOTH the
    category grant and the deny override.  The verdict alone cannot
    discriminate, so the two DATA legs are asserted with it: the allowed
    category really does contribute its non-denied members (the category
    grant is real), and the denied member really is absent from the flat
    effective-grant map (the deny override is real).
    """
    agent_id = "explorer"
    tool = "rag_query"
    granted = granted_by_agent[agent_id]

    denied = set(src.agents[agent_id].get("deny", []))
    category_members = set(src.categories["rag"]) - denied
    assert category_members <= granted, (
        f"the allowed category 'rag' must contribute its non-denied members to "
        f"the flat grant map; missing={sorted(category_members - granted)[:5]}"
    )
    assert tool not in granted, (
        f"{tool!r} is denied for {agent_id!r} — deny wins, so it must not be "
        f"in the flat effective-grant map"
    )

    verdict, reason = tud.grant_verdict(tool, agent_id, granted, src)
    assert verdict == "no", f"expected verdict 'no', got {verdict!r} (reason {reason!r})"
    assert "category rag allowed" in reason, (
        f"the reason must name the CATEGORY GRANT; got {reason!r}"
    )
    assert "deny wins" in reason.lower(), (
        f"the reason must name the DENY OVERRIDE; got {reason!r}"
    )
    assert tool in reason, f"the reason must name the deny entry; got {reason!r}"


def _assert_secondary_probe(src: tud.AllowSource, granted_by_agent: dict) -> None:
    """THE ASSERTION BODY of node 40 — re-runnable under a mutant.

    ``devops`` allows the category ``filesystem``, which owns ``edit_file``.
    The flat effective-grant map is the runtime grant (§3.6.3 step 1) and the
    attribution names the granting category.
    """
    agent_id = "devops"
    tool = "edit_file"
    granted = granted_by_agent[agent_id]

    assert tool in granted, (
        f"{tool!r} is owned by the allowed category 'filesystem' and MUST be in "
        f"the flat effective-grant map (membership IS a runtime grant)"
    )

    verdict, reason = tud.grant_verdict(tool, agent_id, granted, src)
    assert verdict == "yes", f"expected verdict 'yes', got {verdict!r} (reason {reason!r})"
    assert reason == "category:filesystem", f"unexpected attribution: {reason!r}"


# ---------------------------------------------------------------------------
# The mutants (installed AT the reader's entry point — never re-implemented
# inside a test; each keeps the 3-tuple signature of §3.6.1)
# ---------------------------------------------------------------------------


def _mutant_category_only(agent, src):
    """MUTANT: allow expansion WITHOUT deny subtraction."""
    allow = tud.effective_allow(agent, src)
    granted: set[str] = set()
    reasons: dict[str, str] = {}
    granted_categories: set[str] = set()
    if not allow:
        granted = {
            tool
            for category, tools in (src.categories or {}).items()
            if category not in (src.privileged_categories or [])
            for tool in (tools or [])
        }
        reasons = {tool: "inherit-all (empty allow; non-privileged default universe)" for tool in granted}
        granted_categories = {
            category
            for category in src.category_keys
            if category not in (src.privileged_categories or [])
        }
    else:
        for entry in allow:
            name = str(entry)
            if name in src.category_keys:
                granted_categories.add(name)
                for tool in (src.categories.get(name) or []):
                    granted.add(tool)
                    reasons.setdefault(tool, f"category:{name}")
            else:
                granted.add(name)
                reasons.setdefault(name, "literal")
    # THE MUTATION: deny is never subtracted.
    return granted, reasons, granted_categories


def _mutant_exact_match(agent, src):
    """MUTANT: literal matching — NO category expansion on the allow side.

    The deny side is kept byte-for-byte the reader's own semantics, so the
    mutation is the allow-side expansion and nothing else.
    """
    allow = tud.effective_allow(agent, src)
    deny = agent.get("deny") if isinstance(agent, dict) else getattr(agent, "deny", None)
    granted: set[str] = set()
    reasons: dict[str, str] = {}
    granted_categories: set[str] = set()
    if not allow:
        granted = {
            tool
            for category, tools in (src.categories or {}).items()
            if category not in (src.privileged_categories or [])
            for tool in (tools or [])
        }
        reasons = {tool: "inherit-all (empty allow; non-privileged default universe)" for tool in granted}
        granted_categories = {
            category
            for category in src.category_keys
            if category not in (src.privileged_categories or [])
        }
    else:
        for entry in allow:
            name = str(entry)
            # THE MUTATION: the name ITSELF is granted; no category is expanded
            # and no category key is recorded as granted.
            granted.add(name)
            reasons.setdefault(name, "literal")
    denied: set[str] = set()
    for entry in (deny or []):
        name = str(entry)
        if name in src.category_keys:
            denied |= set(src.categories.get(name) or [])
        else:
            denied.add(name)
    granted -= denied
    for tool in denied:
        reasons.pop(tool, None)
    return granted, reasons, granted_categories


@pytest.fixture
def category_only_mutant(monkeypatch: pytest.MonkeyPatch):
    """MODULE-LEVEL fixture: installs the CATEGORY-ONLY mutant AT the
    reader's grant function — the same entry point the verdict path consults."""
    monkeypatch.setattr(tud, "effective_agent_tools", _mutant_category_only)
    return _mutant_category_only


@pytest.fixture
def exact_match_mutant(monkeypatch: pytest.MonkeyPatch):
    """MODULE-LEVEL fixture: installs the EXACT-MATCH mutant AT the reader's
    grant function — the same entry point the verdict path consults."""
    monkeypatch.setattr(tud, "effective_agent_tools", _mutant_exact_match)
    return _mutant_exact_match


# ---------------------------------------------------------------------------
# The twelve nodes
# ---------------------------------------------------------------------------


def test_the_primary_probe_explorer_rag_query_is_denied_and_names_the_deny_wins_path():
    """Node 39 — the PRIMARY probe, against the REAL artifact.

    ``explorer`` allows the category ``rag`` and denies ``rag_query``: the
    verdict is ``"no"`` and the reason names BOTH the category grant and the
    deny override.  No monkeypatch of the artifact, the taxonomy, or the
    readers: this is the real thing.
    """
    src = tud.allowlist_source()
    assert src.state == "ok"

    granted_by_agent = _flat_grant_map(src)
    # The helper is faithful to the reader's PUBLISHED flat map.
    published = tud.allowlists()
    assert set(published["explorer"]) == granted_by_agent["explorer"]

    _assert_primary_probe(src, granted_by_agent)


def test_the_secondary_probe_devops_edit_file_is_granted_via_the_filesystem_category():
    """Node 40 — the SECONDARY probe, against the REAL artifact.

    ``devops`` allows the category ``filesystem``, which owns ``edit_file``:
    the verdict is ``"yes"`` with the attribution ``category:filesystem``.
    """
    src = tud.allowlist_source()
    assert src.state == "ok"

    granted_by_agent = _flat_grant_map(src)
    assert set(tud.allowlists()["devops"]) == granted_by_agent["devops"]

    _assert_secondary_probe(src, granted_by_agent)


def test_the_board_probe_bash_devops_is_vacuous(request: pytest.FixtureRequest):
    """Node 41 — the board's stored acceptance probe is VACUOUS.

    ``bash`` is BOTH a category key and a tool name, so the exact-match
    mutant and the correct implementation return the SAME verdict for
    ``devops`` + ``bash``: the stored probe cannot discriminate and is
    therefore not used as a probe.
    """
    src = tud.allowlist_source()
    assert src.state == "ok"

    # The premise: "bash" is a category key AND a member of that category.
    assert "bash" in src.category_keys
    assert "bash" in set(src.categories["bash"])

    correct_map = _flat_grant_map(src)
    correct = tud.grant_verdict("bash", "devops", correct_map["devops"], src)

    # Install the exact-match mutant AT the reader's entry point, then read
    # the same pair again through the reader.
    request.getfixturevalue("exact_match_mutant")
    mutated_map = _flat_grant_map(src)
    mutated = tud.grant_verdict("bash", "devops", mutated_map["devops"], src)

    assert mutated[0] == correct[0], (
        f"the board probe must be vacuous: correct={correct!r} mutant={mutated!r}"
    )
    assert mutated == correct, (
        f"the board probe must be vacuous: correct={correct!r} mutant={mutated!r}"
    )


def test_the_mutant_category_only_expansion_fails_the_primary_probe(
    category_only_mutant,
):
    """Node 42 — the CATEGORY-ONLY mutant (no deny subtraction) makes the
    REAL primary probe go RED.

    The mutant is installed AT the reader's entry point; the real probe's
    assertion body is re-run through the SHARED helper and MUST fail.  A
    mutant under which the real probe still passes is VACUOUS and fails here.
    """
    src = tud.allowlist_source()
    assert src.state == "ok"

    granted_by_agent = _flat_grant_map(src)
    # The mutant really is wired in AT the reader's entry point.
    assert tud.effective_agent_tools is _mutant_category_only
    assert "rag_query" in granted_by_agent["explorer"], (
        "the category-only mutant must keep the denied tool in the grant map"
    )

    # The mutant flips the REAL probe's verdict (spec node 42): without the
    # deny subtraction the denied tool survives the expansion.
    mutant_verdict, mutant_reason = tud.grant_verdict(
        "rag_query", "explorer", granted_by_agent["explorer"], src
    )
    assert mutant_verdict == "yes", (
        f"the category-only mutant must flip the primary probe's verdict; "
        f"got {mutant_verdict!r} (reason {mutant_reason!r})"
    )

    with pytest.raises(AssertionError) as excinfo:
        _assert_primary_probe(src, granted_by_agent)

    message = str(excinfo.value)
    assert "rag_query" in message, (
        f"the primary probe must fail on the denied pair under this mutant; got {message!r}"
    )


def test_the_mutant_exact_match_expansion_fails_the_probes(exact_match_mutant):
    """Node 43 — the EXACT-MATCH mutant (literal matching, no category
    expansion) makes BOTH real probes go RED.

    The mutant is installed AT the reader's entry point; the real probes'
    assertion bodies are re-run through the SHARED helpers and MUST fail.  A
    mutant under which the real probes still pass is VACUOUS and fails here.
    """
    src = tud.allowlist_source()
    assert src.state == "ok"

    granted_by_agent = _flat_grant_map(src)
    # The mutant really is wired in AT the reader's entry point.
    assert tud.effective_agent_tools is _mutant_exact_match
    assert "edit_file" not in granted_by_agent["devops"], (
        "the exact-match mutant must not expand the 'filesystem' category"
    )
    assert "rag_query" not in granted_by_agent["explorer"], (
        "the exact-match mutant grants only literal names"
    )

    # NOTE (spec §3.9.3 node 43 vs the landed reader): the exact-match mutant
    # does NOT flip the VERDICT of either probe — the verdict function's
    # owner branch re-derives "yes" from the SOURCE's category map.  What it
    # does change is the reader's PUBLISHED effective grant set, which
    # §3.6.3 step 1 defines as the runtime grant ("membership in
    # allowlists()[agent_id] IS a runtime grant").  The shared probe bodies
    # therefore assert that map WITH the verdict, and that is the leg the
    # mutant turns red.

    with pytest.raises(AssertionError) as primary_exc:
        _assert_primary_probe(src, granted_by_agent)
    with pytest.raises(AssertionError) as secondary_exc:
        _assert_secondary_probe(src, granted_by_agent)

    primary_message = str(primary_exc.value)
    secondary_message = str(secondary_exc.value)
    assert "must contribute its non-denied members" in primary_message, (
        f"the primary probe must fail on the CATEGORY CONTRIBUTION under this "
        f"mutant; got {primary_message!r}"
    )
    assert "MUST be in the flat effective-grant map" in secondary_message, (
        f"the secondary probe must fail on the FLAT GRANT MAP under this mutant; "
        f"got {secondary_message!r}"
    )


def test_effective_grants_are_equivalent_to_resolve_tool_filter_for_every_agent():
    """Node 44 — THE EQUIVALENCE PROOF.

    For every agent in the artifact, the digest's effective grant set equals
    the house resolver's ``resolve_tool_filter`` result on the SAME fixture
    universe.  The runtime's ``allow`` argument is produced by the REAL
    ``expand_allow_for_innate_skills`` — never by the digest's own
    ``effective_allow`` — and the two are asserted EQUAL, so the innate half
    is never compared against itself.

    Runtime-class names (the ``mcp_``/``plane_`` sweeps) are call-time state
    the static map cannot know: they are excluded from the comparison by
    construction and asserted ``unknown`` separately.
    """
    from daemon.tools.instance import expand_allow_for_innate_skills, resolve_tool_filter

    src = tud.allowlist_source()
    assert src.state == "ok"
    assert len(src.agents) == 55

    # --- the FIXED fixture universe (A3-FIXTURE-MAP) ---------------------
    tool_categories = _fixture_category_map(src)
    assert set(tool_categories) == src.category_keys
    assert tool_categories["mcp"] == [] and tool_categories["plane"] == []

    fixture_names = {tool for tools in src.categories.values() for tool in tools}
    fixture_names |= set(FIXTURE_EXTRA_NAMES)

    owned = set(src.owned_category)
    runtime_class = {
        name
        for name in fixture_names
        if (_is_mcp_shaped(name) or src.is_dynamic_prefix_shaped(name))
        and name not in owned
    }
    # The reader's MCP-shape predicate IS the runtime's exact predicate (A2) —
    # a stricter one (">= 2 underscores after the prefix") would reclassify
    # ``mcp_github_create_issue`` as a plain, decidable name.
    for name in fixture_names:
        assert src.is_mcp_shaped(name) == _is_mcp_shaped(name), (
            f"the reader's MCP-shape predicate differs from the runtime's for {name!r}"
        )
    non_privileged_universe = _non_privileged_universe(src)

    # --- the 55 real agents ---------------------------------------------
    for agent_id in sorted(src.agents):
        view = _agent_view(src, agent_id)

        runtime_allow = expand_allow_for_innate_skills(list(view["allow"]), list(view["innate"]))
        digest_allow = tud.effective_allow(dict(view), src)
        assert runtime_allow == digest_allow, (
            f"{agent_id}: the runtime's innate expansion and the digest's must agree "
            f"(runtime={runtime_allow!r} digest={digest_allow!r})"
        )

        runtime_set = resolve_tool_filter(
            allow=list(runtime_allow),
            deny=list(view["deny"]),
            tool_categories=tool_categories,
            all_tool_names=fixture_names,
        )
        digest_set, _reasons, _cats = tud.effective_agent_tools(dict(view), src)
        digest_set = set(digest_set)

        if runtime_set is None:
            # allow AND deny both empty -> the runtime's "all tools" signal.
            assert digest_set == non_privileged_universe, (
                f"{agent_id}: the inherit-all agent must receive exactly the "
                f"non-privileged universe"
            )
            continue

        assert (set(runtime_set) - runtime_class) == (digest_set - runtime_class), (
            f"{agent_id}: digest and runtime grants diverge; "
            f"runtime-only={sorted((set(runtime_set) - runtime_class) - (digest_set - runtime_class))[:5]} "
            f"digest-only={sorted((digest_set - runtime_class) - (set(runtime_set) - runtime_class))[:5]}"
        )

    # --- the three SYNTHETIC agents the corpus cannot supply -----------
    # (a) allow=[] deny=[] -> the runtime returns None -> the full
    #     non-privileged universe.
    view = {"allow": [], "deny": [], "innate": []}
    runtime_allow = expand_allow_for_innate_skills([], [])
    digest_allow = tud.effective_allow(dict(view), src)
    assert runtime_allow == digest_allow == []
    runtime_set = resolve_tool_filter(
        allow=list(runtime_allow), deny=[], tool_categories=tool_categories, all_tool_names=fixture_names
    )
    digest_set, _reasons, _cats = tud.effective_agent_tools(dict(view), src)
    digest_set = set(digest_set)
    assert runtime_set is None, "the empty-allow + empty-deny case must return None"
    assert digest_set == non_privileged_universe
    # The size of the default universe is DERIVED from the artifact's own
    # category map (§8.2 — "COMPUTED, not hand-copied"); at this artifact it
    # measures 201 names.  The cross-implementation check is leg (b) below.
    assert len(digest_set) == len(non_privileged_universe)

    # (b) allow=[] deny=['bash'] -> the runtime takes the EMPTY-ALLOW branch
    #     REGARDLESS of the deny (instance.py:581) and subtracts the deny
    #     afterwards (instance.py:601-609).
    view = {"allow": [], "deny": ["bash"], "innate": []}
    runtime_allow = expand_allow_for_innate_skills([], [])
    digest_allow = tud.effective_allow(dict(view), src)
    assert runtime_allow == digest_allow == []
    runtime_set = resolve_tool_filter(
        allow=list(runtime_allow), deny=["bash"], tool_categories=tool_categories, all_tool_names=fixture_names
    )
    digest_set, _reasons, _cats = tud.effective_agent_tools(dict(view), src)
    digest_set = set(digest_set)
    assert runtime_set is not None, "a non-empty deny must not take the None branch"
    assert (set(runtime_set) - runtime_class) == (digest_set - runtime_class)
    assert digest_set, "the empty-allow + non-empty-deny case must NOT be empty"
    assert "bash" not in digest_set, "the deny must win"
    assert digest_set == non_privileged_universe - {"bash"}

    # (c) allow=[] innate=['todo'] deny=[] -> the innate expansion runs
    #     FIRST, so the allow list is NOT empty and the default-universe
    #     branch is NOT taken.  This pins the ORDER in both implementations.
    view = {"allow": [], "deny": [], "innate": ["todo"]}
    runtime_allow = expand_allow_for_innate_skills([], ["todo"])
    digest_allow = tud.effective_allow(dict(view), src)
    assert runtime_allow == digest_allow == ["todo"]
    runtime_set = resolve_tool_filter(
        allow=list(runtime_allow), deny=[], tool_categories=tool_categories, all_tool_names=fixture_names
    )
    digest_set, _reasons, _cats = tud.effective_agent_tools(dict(view), src)
    digest_set = set(digest_set)
    assert runtime_set is not None, "the innate expansion must prevent the None branch"
    assert (set(runtime_set) - runtime_class) == (digest_set - runtime_class)
    assert digest_set == set(src.categories["todo"]), (
        "the innate expansion must grant exactly the 'todo' category"
    )
    assert digest_set != non_privileged_universe, "the agent must NOT be inherit-all"

    # --- THE LOAD-BEARING ORDER (§3.6.3 steps 1-3) ----------------------
    # The flat map is consulted BEFORE the degraded-artifact gate (step 2)
    # and BEFORE the agent-coverage gate (step 3).
    uncovered = "an-agent-the-artifact-does-not-cover"
    verdict, reason = tud.grant_verdict("edit_file", uncovered, {"edit_file"}, src)
    assert verdict == "yes", (
        f"a 'yes' from the flat map must stay a 'yes' for an uncovered agent; got {verdict!r} ({reason!r})"
    )
    verdict, reason = tud.grant_verdict("edit_file", uncovered, set(), src)
    assert verdict == "unknown", (
        f"without the flat-map grant the agent-coverage gate must decide; got {verdict!r} ({reason!r})"
    )

    degraded = tud.AllowSource(
        state="absent",
        schema_version=2,
        agents={},
        categories={},
        runtime_only_categories=[],
        privileged_categories=[],
        dynamic_prefixes=[],
        innate_skill_categories={},
        known_tool_names=set(),
        unresolved={"allow": [], "deny": []},
        provenance={},
    )
    verdict, reason = tud.grant_verdict("edit_file", "any-agent", {"edit_file"}, degraded)
    assert verdict == "yes", (
        f"a 'yes' from the flat map must stay a 'yes' for a degraded artifact; got {verdict!r} ({reason!r})"
    )


def test_the_ownership_guards_withhold_mcp_rpc_from_an_mcp_only_agent():
    """Node 45 — the ``mcp_rpc`` ownership guard.

    An agent whose allow list contains ``"mcp"`` but not ``"mcp_rpc"`` does
    NOT receive ``mcp_rpc_command``: the verdict is ``"no"`` and the reason
    names the owning category ``mcp_rpc`` (a privileged category).
    """
    src = tud.allowlist_source()
    assert src.state == "ok"

    agent_id = "explorer"
    assert "mcp" in src.agents[agent_id]["allow"]
    assert "mcp_rpc" not in src.agents[agent_id]["allow"]
    assert "mcp_rpc" in src.privileged_categories, "mcp_rpc must be a privileged category"
    assert src.owned_category["mcp_rpc_command"] == "mcp_rpc"

    granted_by_agent = _flat_grant_map(src)
    assert "mcp_rpc_command" not in granted_by_agent[agent_id], (
        "the mcp sweep must not hand the privileged mcp_rpc surface to an "
        "agent that allows only 'mcp'"
    )

    verdict, reason = tud.grant_verdict("mcp_rpc_command", agent_id, granted_by_agent[agent_id], src)
    assert verdict == "no", f"expected verdict 'no', got {verdict!r} (reason {reason!r})"
    assert "mcp_rpc" in reason, f"the reason must name the owning category; got {reason!r}"

    # CONTROL: an agent that explicitly allows the privileged category does
    # receive it — the guard withholds, it does not blanket-ban.
    privileged_agent = "ari"
    assert "mcp_rpc" in src.agents[privileged_agent]["allow"]
    assert "mcp_rpc_command" in granted_by_agent[privileged_agent]
    verdict, reason = tud.grant_verdict(
        "mcp_rpc_command", privileged_agent, granted_by_agent[privileged_agent], src
    )
    assert verdict == "yes", f"expected verdict 'yes', got {verdict!r} (reason {reason!r})"

    # THE MCP-SHAPE PREDICATE IS THE RUNTIME'S EXACT FORM (A2): a name with
    # exactly ONE underscore after the "mcp_" prefix (``mcp_x_y``) IS swept
    # by the runtime, so the reader must render it "unknown" — a stricter
    # predicate would fall through to a confident "no" (a false NO).
    swept_name = "mcp_x_y"
    assert _is_mcp_shaped(swept_name) is True, "the runtime's predicate must accept 'mcp_x_y'"
    assert src.is_mcp_shaped(swept_name) is True, (
        "the reader must use the runtime's predicate, not a stricter one"
    )
    assert swept_name not in granted_by_agent[agent_id]
    verdict, reason = tud.grant_verdict(swept_name, agent_id, granted_by_agent[agent_id], src)
    assert verdict == "unknown", f"expected verdict 'unknown', got {verdict!r} (reason {reason!r})"
    assert "MCP" in reason, f"the reason must name the MCP sweep; got {reason!r}"


def test_the_plane_ownership_guard_excludes_plane_sync_from_the_plane_sweep():
    """Node 46 — the ``plane_sync`` ownership guard.

    ``plane_sync_project`` is owned by ``plane_sync`` and is never swept into
    the ``plane`` category — neither by the runtime nor by the digest.
    """
    from daemon.tools.instance import resolve_tool_filter

    src = tud.allowlist_source()
    assert src.state == "ok"

    assert src.owned_category["plane_sync_project"] == "plane_sync"
    assert "plane_sync_project" in set(src.categories["plane_sync"])

    # --- the RUNTIME's sweep must not claim it --------------------------
    tool_categories = _fixture_category_map(src)
    fixture_names = {tool for tools in src.categories.values() for tool in tools}
    fixture_names |= set(FIXTURE_EXTRA_NAMES)
    swept = resolve_tool_filter(
        allow=["plane"], deny=[], tool_categories=tool_categories, all_tool_names=fixture_names
    )
    assert swept is not None
    assert "plane_sync_project" not in swept, (
        "the plane_ prefix sweep must not claim a name another category owns"
    )
    assert "plane_list_work_items" in swept, (
        "the plane_ prefix sweep must still claim the genuinely unowned names"
    )

    # --- the DIGEST agrees -----------------------------------------------
    agent_id = "plane-sweeper"
    synthetic = _source_with_agent(src, agent_id, allow=["plane"])
    granted_by_agent = _flat_grant_map(synthetic)
    assert "plane_sync_project" not in granted_by_agent[agent_id]

    verdict, reason = tud.grant_verdict(
        "plane_sync_project", agent_id, granted_by_agent[agent_id], synthetic
    )
    assert verdict == "no", f"expected verdict 'no', got {verdict!r} (reason {reason!r})"
    assert "plane_sync" in reason, f"the reason must name the owning category; got {reason!r}"

    # CONTROL: an agent that explicitly allows the owning category does
    # receive it.
    assert "plane_sync" in src.agents["worker"]["allow"]
    granted = _flat_grant_map(src)
    assert "plane_sync_project" in granted["worker"]
    verdict, reason = tud.grant_verdict("plane_sync_project", "worker", granted["worker"], src)
    assert verdict == "yes", f"expected verdict 'yes', got {verdict!r} (reason {reason!r})"


def test_the_innate_skill_expansion_is_modelled():
    """Node 47 — the innate-skill expansion is modelled.

    An agent whose innate skills grant a category that is NOT in its allow
    list receives that category's tools (``worker`` -> ``todo``), and an
    agent with an empty allow list inherits every non-privileged category
    (and NOT the privileged ones).
    """
    src = tud.allowlist_source()
    assert src.state == "ok"

    # --- the innate expansion -------------------------------------------
    agent_id = "worker"
    assert "todo" not in src.agents[agent_id]["allow"]
    assert "todo" in src.agents[agent_id]["innate"]
    assert src.innate_skill_categories["todo"] == ["todo"]

    expanded = tud.effective_allow(_agent_view(src, agent_id), src)
    assert "todo" in expanded, f"the innate skill must expand the category; got {expanded!r}"

    granted = _flat_grant_map(src)
    todo_tools = set(src.categories["todo"])
    assert todo_tools <= granted[agent_id], (
        f"the innate-granted category's tools must be granted; missing={sorted(todo_tools - granted[agent_id])[:5]}"
    )
    assert "todo_list_create" in granted[agent_id]

    # W2 — the innate grant carries its ATTRIBUTION through the published
    # flat map. `AllowSource.grant_reason` alone re-derives from `allow`
    # only and would return "no reason available" for this third of agents.
    g, reasons, _cats = tud.effective_agent_tools(_agent_view(src, agent_id), src)
    assert reasons.get("todo_list_create") == "category:todo"
    published = tud.allowlists()
    published_reasons = getattr(published, "reasons", None) or {}
    assert published_reasons.get(agent_id, {}).get("todo_list_create") == "category:todo"
    verdict, reason = tud.grant_verdict(
        "todo_list_create",
        agent_id,
        set(published[agent_id]),
        src,
        reasons=published_reasons.get(agent_id, {}),
    )
    assert verdict == "yes"
    assert reason == "category:todo"
    # The reason is DEGRAABLE: a synthetic flat map with no attribution
    # keeps the SAME verdict and only loses the "category:todo" string.
    degraded = tud.grant_verdict("todo_list_create", agent_id, set(published[agent_id]), src)
    assert degraded[0] == verdict
    assert degraded[1] == "no reason available"

    # --- the inherit-all agent ------------------------------------------
    inherit_agent = "watcher"
    assert src.agents[inherit_agent]["allow"] == []
    assert src.inherits_all(inherit_agent) is True

    universe = _non_privileged_universe(src)
    assert universe <= granted[inherit_agent], (
        f"the inherit-all agent must receive every non-privileged category's tools; "
        f"missing={sorted(universe - granted[inherit_agent])[:5]}"
    )
    privileged_names = {
        tool
        for category, tools in src.categories.items()
        if category in set(src.privileged_categories)
        for tool in tools
    }
    assert privileged_names, "the artifact must carry privileged categories"
    assert not (privileged_names & granted[inherit_agent]), (
        f"privileged tools must never join the default universe; "
        f"leaked={sorted(privileged_names & granted[inherit_agent])[:5]}"
    )


def test_an_inherit_all_agent_is_granted_the_statically_owned_non_privileged_tools():
    """Node 58 — the INHERIT-ALL VERDICT for a statically-owned,
    NON-PRIVILEGED tool.

    ``watcher`` is the corpus's only inherit-all agent (its allow list is
    empty).  For ``edit_file`` (owned by the non-privileged category
    ``filesystem``) the VERDICT is ``"yes"`` and the reason names the
    inherit-all default universe.  The assertion is on the VERDICT.
    """
    src = tud.allowlist_source()
    assert src.state == "ok"

    agent_id = "watcher"
    assert src.agents[agent_id]["allow"] == []
    assert src.inherits_all(agent_id) is True

    tool = "edit_file"
    assert src.owned_category[tool] == "filesystem"
    assert src.owned_category[tool] not in set(src.privileged_categories)

    granted_by_agent = _flat_grant_map(src)
    verdict, reason = tud.grant_verdict(tool, agent_id, granted_by_agent[agent_id], src)
    assert verdict == "yes", f"expected verdict 'yes', got {verdict!r} (reason {reason!r})"
    assert "inherit-all" in reason, f"the reason must name the inherit-all universe; got {reason!r}"
    assert "default universe" in reason, f"the reason must name the default universe; got {reason!r}"


def test_an_inherit_all_agent_is_not_granted_the_privileged_category_tools():
    """Node 59 — the INHERIT-ALL VERDICT for a PRIVILEGED-category tool.

    For the same inherit-all agent (``watcher``) and a tool owned by a
    privileged category (``memoir_recall`` -> ``memoir``) the VERDICT is
    ``"no"``: privileged categories never join the default universe.  The
    assertion is on the VERDICT.
    """
    src = tud.allowlist_source()
    assert src.state == "ok"

    agent_id = "watcher"
    tool = "memoir_recall"
    assert src.owned_category[tool] == "memoir"
    assert src.owned_category[tool] in set(src.privileged_categories)

    granted_by_agent = _flat_grant_map(src)
    assert tool not in granted_by_agent[agent_id]

    verdict, reason = tud.grant_verdict(tool, agent_id, granted_by_agent[agent_id], src)
    assert verdict == "no", f"expected verdict 'no', got {verdict!r} (reason {reason!r})"
    assert "memoir" in reason, f"the reason must name the privileged category; got {reason!r}"


def test_an_agent_allowing_plane_renders_an_unowned_plane_name_unknown():
    """Node 60 — the ``plane`` runtime-only category.

    An agent whose allow list names the category ``"plane"`` (a
    ``runtime_only_categories`` member, NOT a ``categories`` key) and a
    ``plane_*`` name it does NOT literally allow renders ``"unknown"`` — the
    swept member set is call-time state, so a confident ``"no"`` would be a
    false NO.
    """
    src = tud.allowlist_source()
    assert src.state == "ok"

    agent_id = "plane-sweeper"
    synthetic = _source_with_agent(src, agent_id, allow=["plane"])

    # The reader's category-key universe is the UNION (§3.6.2a).
    assert "plane" in synthetic.category_keys
    assert "plane" not in synthetic.categories
    assert "plane" in synthetic.runtime_only_categories
    assert synthetic.category_granted(agent_id, "plane") is True, (
        "a runtime-only category key must count as granted"
    )

    tool = "plane_list_work_items"
    assert tool not in synthetic.agents[agent_id]["allow"], "the name is not literally allowed"

    granted_by_agent = _flat_grant_map(synthetic)
    assert tool not in granted_by_agent[agent_id], (
        "a runtime-only category contributes no static member names"
    )

    verdict, reason = tud.grant_verdict(tool, agent_id, granted_by_agent[agent_id], synthetic)
    assert verdict == "unknown", f"expected verdict 'unknown', got {verdict!r} (reason {reason!r})"
    assert verdict != "no", "a swept plane_ name must never render a confident 'no'"
    assert "plane_" in reason, f"the reason must name the plane_ sweep; got {reason!r}"


# ---------------------------------------------------------------------------
# R3/V2 — the tester's FULL exact-match model as a third module-level mutant
# ---------------------------------------------------------------------------


@pytest.fixture
def full_exact_match_mutant(monkeypatch: pytest.MonkeyPatch):
    """The FULL exact-match model: literal-only expansion AND no category
    grants in the SOURCE (so the ownership guard cannot re-derive a "yes").

    The existing ``exact_match_mutant`` changes only the expansion; the
    verdict path's owner branch still re-derives grants from
    ``AllowSource.granted_categories``. This mutant patches BOTH seams at
    their module-level entry points, which is the model the tester flagged.
    """
    monkeypatch.setattr(tud, "effective_agent_tools", _mutant_exact_match)
    monkeypatch.setattr(
        tud.AllowSource,
        "granted_categories",
        property(lambda self: {str(a): set() for a in (self.agents or {})}),
    )
    return _mutant_exact_match


def test_the_full_exact_match_mutant_flips_the_rendered_verdicts(
    full_exact_match_mutant,
):
    """R3/V2 — under the full model the RENDERED verdicts flip to "no":
    explorer×rag_get_graph by the ownership guard, devops×edit_file too."""
    src = tud.allowlist_source()
    assert src.state == "ok"
    assert tud.effective_agent_tools is _mutant_exact_match
    assert all(not cats for cats in src.granted_categories.values())

    granted = _flat_grant_map(src)
    assert "rag_get_graph" not in granted["explorer"]
    assert "edit_file" not in granted["devops"]

    pairs = [("explorer", "rag_get_graph"), ("devops", "edit_file")]
    agents_out = []
    for agent_id, tool in pairs:
        verdict, reason = tud.grant_verdict(tool, agent_id, granted[agent_id], src)
        assert verdict == "no", f"{agent_id}×{tool} rendered {verdict!r} ({reason!r})"
        assert "ownership guard" in reason
        agents_out.append(
            {
                "agent_id": agent_id,
                "calls_24h": 0,
                "calls_7d": 1,
                "allow_listed": False,
                "allow_listed_detail": {"verdict": verdict, "reason": reason},
            }
        )

    data = {
        "run_date": "2026-11-21",
        "last24_day": "2026-11-20",
        "week_days": [f"2026-11-{d}" for d in range(14, 21)],
        "projects": [
            {
                "project_id": "p-r3",
                "tools": [
                    {
                        "tool_name": tool,
                        "weight": 230,
                        "calls_24h": 0,
                        "calls_7d": 1,
                        "score": 230,
                        "allow_listed_any": False,
                        "allowed_agents": [],
                        "agents": [agents_out[i]],
                        "allow_listed_cell": "no",
                    }
                    for i, (_agent, tool) in enumerate(pairs)
                ],
            }
        ],
        "zero_near_zero": [],
        "allowlist_state": "ok",
        "allowlist_schema_version": 2,
        "unresolved": {"allow": [], "deny": []},
    }
    md = tud.render_markdown(data, "FIXED")
    assert (
        "- explorer: 24h=0 7d=1 (not allow-listed — owned by category rag - "
        "not swept into the allowed category (ownership guard))"
    ) in md
    assert (
        "- devops: 24h=0 7d=1 (not allow-listed — owned by category filesystem - "
        "not swept into the allowed category (ownership guard))"
    ) in md


def test_grant_verdict_yes_is_equivalent_to_membership_in_the_published_map():
    """R3/V2 — the real-artifact invariant: a "yes" verdict is exactly a
    name in `allowlists()[agent]` (deny excepted). This converts the
    map-leg detection into verdict-level discrimination in-tree."""
    src = tud.allowlist_source()
    assert src.state == "ok"
    published = tud.allowlists()
    reasons = getattr(published, "reasons", None) or {}

    # A bounded but load-bearing sample: every agent with a deny list, the
    # innate-grant agent, the inherit-all agent, and the first 8 by id.
    sample = {
        "ari", "devops", "explorer", "maintenancer", "worker", "watcher",
        *sorted(published)[:8],
    }
    checked = 0
    for agent_id in sorted(sample & set(published)):
        tools = published[agent_id]
        granted = set(tools)
        for tool in sorted(set(src.known_tool_names) | granted):
            verdict, _reason = tud.grant_verdict(
                tool, agent_id, granted, src, reasons=reasons.get(agent_id, {})
            )
            checked += 1
            if verdict == "yes":
                assert tool in granted, (
                    f"{agent_id}×{tool}: a 'yes' verdict must be a published grant"
                )
            elif tool in granted:
                # The only legitimate exception is the deny gate (deny wins).
                assert src.deny_entry_for(agent_id, tool) is not None, (
                    f"{agent_id}×{tool}: a published grant must render 'yes' "
                    "unless a deny entry removes it"
                )
    assert checked > 1000, f"vacuity guard: only {checked} pairs checked"
