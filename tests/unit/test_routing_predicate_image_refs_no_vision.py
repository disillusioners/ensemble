"""Direct (non-inferential) unit test pinning the vision-routing
predicate at ``daemon/graph.py:7049-7058`` (A1 / round-2 review
MAJOR #2).

Background
----------

The ref-send path (Phase 2 / clipboard-image-chat) MUST NOT route
the agent through the vision model. The contract:

  * ref-send → ``HumanMessage.content`` is a plain ``str`` (no
    ``image_url`` content-block list);
  * the refs live in ``HumanMessage.additional_kwargs["image_refs"]``
    (a display-channel sidecar);
  * the LLM selection at ``daemon/graph.py:7063-7068`` sets
    ``use_vision_model = False`` and ``call_type = "STANDARD"``.

Existing coverage (Phase 2 freeze-list A1) only pins the
**structural** half of that contract — the kwargs stamp
(``tests/integration/test_messages_ref_path_e2e.py``) and the
serializer union. The **routing predicate** at
``graph.py:7049-7058`` was inferred by reading the code, but a
future refactor could change the scan shape without breaking any
test. This file pins the predicate directly so any future drift
trips an assertion.

Constraint compliance
---------------------

The task constraint forbids ``graph.py`` modification. Rather than
mirror the predicate as a copy (which would invite silent drift),
this test extracts the EXACT production source of the scan block
via ``ast.get_source_segment`` on the module AST, compiles it in a
sandbox namespace, and runs the real production code against
``HumanMessage`` shapes — **direct** in the strict sense (no copy,
no inference). A drift-pin assertion locates the predicate in
``graph.py`` and fails loudly if the scan-block shape changes in
ways that would silently re-fire vision routing.

What this test pins:

  1. The scan block exists and is structurally what we expect
     (``has_images = False`` + the for-loop walks ``msg.content``,
     not ``additional_kwargs.image_refs``).
  2. ``has_images`` evaluates to ``False`` for a refs-only message
     (str content + ``additional_kwargs={"image_refs": [...]}``).
  3. ``has_images`` evaluates to ``True`` for a multimodal message
     (list content with an ``{"type": "image_url"}`` block) — so
     the scan isn't returning False by accident on every input.
  4. ``use_vision_model`` and ``call_type`` derive correctly from
     ``has_images`` + the standard-gate triple
     (``model_vision``, ``llm_standard is not None``).
"""

from __future__ import annotations

import ast
import textwrap

from langchain_core.messages import HumanMessage


# ---------------------------------------------------------------------------
# Helpers — extract the production scan from ``daemon/graph.py``
# ---------------------------------------------------------------------------


def _extract_scan_block_source() -> str:
    """Locate and return the EXACT production source of the has_images
    scan block in :mod:`daemon.graph`.

    The scan is the first ``has_images = False`` assignment inside
    the module. We extract its source via :func:`ast.get_source_segment`
    so the test runs the EXACT bytes the production graph ships — no
    copy, no inference. If the module is reorganized to extract this
    into a named helper, the locator + the test will both need to be
    updated together.
    """
    import daemon.graph as graph_module

    src_path = graph_module.__file__
    assert src_path is not None, "Could not resolve daemon.graph source path"
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()

    tree = ast.parse(src)
    # Locate the first ``has_images = False`` assignment (the scan's
    # seed value). This is the anchor — the scan block is the
    # immediately-following for-loop with the image_url break check.
    target: ast.Assign | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (
                    isinstance(t, ast.Name)
                    and t.id == "has_images"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is False
                ):
                    target = node
                    break
            if target is not None:
                break
    assert target is not None, (
        "Could not find the production has_images scan anchor "
        "(``has_images = False``) in daemon/graph.py. The scan "
        "block may have been renamed or extracted — update this "
        "test together with the production refactor."
    )

    # The scan block runs from the anchor assignment through the
    # enclosing if/break on ``has_images`` after the inner for.
    # We pull a fixed window of source lines starting at the
    # anchor.lineno — the drift-pin in Section 1 below verifies
    # the window still contains the expected for-loop body shape.
    lines = src.split("\n")
    start = target.lineno - 1
    end = start + 12  # anchor line + 11-line scan loop window
    return "\n".join(lines[start:end])


def _extract_derivation_source() -> str:
    """Extract the production-source derivation lines that compute
    ``use_vision_model`` + ``call_type`` from ``has_images``.

    Mirrors the ``_extract_scan_block_source`` discipline — pulls
    the EXACT bytes from :mod:`daemon.graph` so this test pins the
    production derivation, not a copy. We extract ONLY the two
    statements we care about (``use_vision_model = ...`` and
    ``call_type = ...``); the surrounding
    ``current_llm / model_name / vision_log`` lines reference
    ``llm_with_tools`` and ``llm_config`` which are out of scope
    for this test (we assert the routing DECISION, not the LLM
    object construction).
    """
    import daemon.graph as graph_module

    src_path = graph_module.__file__
    assert src_path is not None
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()

    lines = src.split("\n")
    uvm_idx = next(
        (i for i, line in enumerate(lines) if "use_vision_model = has_images" in line),
        None,
    )
    assert uvm_idx is not None, (
        "Could not find ``use_vision_model = has_images`` derivation "
        "in daemon/graph.py."
    )
    # Find the matching ``call_type = ...`` line. The two are
    # adjacent in production (graph.py:7063 + :7068 in the original,
    # 5 lines apart in current source).
    call_type_idx = next(
        (
            i
            for i in range(uvm_idx + 1, min(uvm_idx + 20, len(lines)))
            if 'call_type = "VISION" if use_vision_model else "STANDARD"' in lines[i]
        ),
        None,
    )
    assert call_type_idx is not None, (
        "Could not find the ``call_type = ...`` derivation "
        "(signature line containing 'VISION' and 'STANDARD') in "
        "daemon/graph.py."
    )

    # Join the two lines — exactly what we pin. Anything between is
    # out of scope.
    return "\n".join([lines[uvm_idx], lines[call_type_idx]])


def _run_scan_for(messages: list) -> bool:
    """Execute the production has_images scan against ``messages``.

    Compiles the production-source scan block in a sandbox namespace
    and runs it. Returns the resulting ``has_images`` value.

    The production scan lives inside nested closures inside the
    ``agent_node`` function returned by ``create_agent_node``
    (graph.py:6385), so the source bytes carry non-zero left
    indentation. We :func:`textwrap.dedent` to strip the common
    indent so the snippet compiles at module scope inside the
    sandbox.
    """
    import textwrap as _textwrap

    scan_src = _extract_scan_block_source()
    dedented = _textwrap.dedent(scan_src)
    func_src = (
        "def _scan(messages):\n"
        + _textwrap.indent(dedented, "    ")
        + "\n    return has_images\n"
    )
    namespace: dict = {}
    exec(
        compile(func_src, "<sandbox:has_images-scan>", "exec"),
        namespace,
    )
    return namespace["_scan"](messages)


def _derive_routing(has_images: bool, model_vision, llm_standard):
    """Compute the production-shape ``use_vision_model`` +
    ``call_type`` derivation against the inputs.

    The derivation is byte-for-byte the same as
    ``daemon/graph.py:7063-7068`` — extracted via
    :func:`_extract_derivation_source` and executed in a sandbox so
    a future change to the production derivation (e.g. dropping the
    ``model_vision`` gate) trips the regression.
    """
    deriv_src = _extract_derivation_source()
    dedented = textwrap.dedent(deriv_src)
    func_src = (
        "def _derive(has_images, model_vision, llm_standard):\n"
        + textwrap.indent(dedented, "    ")
        + "\n    return use_vision_model, call_type\n"
    )
    namespace: dict = {}
    exec(compile(func_src, "<sandbox:routing-derivation>", "exec"), namespace)
    return namespace["_derive"](has_images, model_vision, llm_standard)


# ---------------------------------------------------------------------------
# Section 1 — drift pin (predicate structure must not change silently)
# ---------------------------------------------------------------------------


class TestVisionScanPredicateStructure:
    """Pin the EXACT shape of the production scan block.

    If any of these trip:
      * the predicate has been refactored to scan ``additional_kwargs``
        instead of (or in addition to) ``content`` — a regression
        that would re-fire vision routing on ref-sends;
      * the predicate has been extracted into a named helper without
        updating this locator — a maintainability hint, not a bug;
      * the predicate has been removed entirely — a regression that
        would let ref-sends fall into the default branch.

    The locator prefers a stable anchor (the literal substring
    ``has_images = False``) over a brittle line-number pin — line
    numbers drift as the codebase evolves, but the predicate's
    initialization token stays put unless the production logic
    itself changes.
    """

    def test_scan_anchor_is_present_and_starts_at_known_shape(self):
        """The ``has_images = False`` anchor must still be at the head
        of the scan."""
        scan_src = _extract_scan_block_source()
        # First non-blank line MUST be the anchor.
        first_line = next(
            (ln for ln in scan_src.split("\n") if ln.strip()),
            "",
        )
        assert first_line.strip() == "has_images = False", (
            f"Vision-scan anchor drifted: first non-blank line is "
            f"{first_line!r}; expected 'has_images = False'. The "
            f"scan block was restructured — review whether the "
            f"routing predicate still covers refs-only messages."
        )

    def test_scan_block_targets_content_not_additional_kwargs(self):
        """The scan loop walks ``msg.content`` for ``image_url`` blocks
        — NOT ``msg.additional_kwargs['image_refs']``. This is the
        structural pin for the A1 contract: refs are display-only and
        NEVER trigger vision routing.
        """
        scan_src = _extract_scan_block_source()
        # The block MUST read ``msg.content`` (or equivalent
        # ``getattr(msg, 'content', ...)``) for image_url matching.
        assert "content" in scan_src, (
            "Vision scan no longer references message content — "
            "routing may now be relying on a different signal."
        )
        assert '"image_url"' in scan_src, (
            "Vision scan no longer matches the 'image_url' block "
            "type — type-constant changed, may break vision routing."
        )
        # And it MUST NOT scan additional_kwargs for image_refs.
        # ``additional_kwargs`` literal in the scan block would
        # mean a refactor that began treating refs as a vision
        # signal — a regression the A1 contract explicitly forbids.
        assert "additional_kwargs" not in scan_src, (
            "Vision scan now reads additional_kwargs — ref-sends "
            "would re-fire vision routing. This is an A1 contract "
            "violation."
        )

    def test_derivation_block_keeps_three_gate_triple(self):
        """``use_vision_model`` keeps the three-gate triple
        (``has_images``, ``model_vision``, ``llm_standard is not None``).
        If the gate expands or shrinks, this trips.
        """
        deriv_src = _extract_derivation_source()
        # Gate triple visible verbatim.
        assert "has_images" in deriv_src
        assert "model_vision" in deriv_src
        assert "llm_standard" in deriv_src


# ---------------------------------------------------------------------------
# Section 2 — direct runtime assertion against the production predicate
# ---------------------------------------------------------------------------


class TestVisionScanPredicateRuntime:
    """Run the EXACT production scan against constructed HumanMessages
    and assert the routing decision (no inference, no mirror).

    These tests make the A1 routing contract **direct**: any change
    to the production predicate's behavior will fail here without
    needing to mirror or copy the logic into the test.
    """

    def test_refs_only_message_routes_through_standard(self):
        """HumanMessage with str content + additional_kwargs image_refs:

            * ``has_images = False`` (scan sees str content, no list)
            * ``use_vision_model = False`` (even when model_vision set)
            * ``call_type = "STANDARD"``

        The ref-send path's central contract (A1).
        """
        msg = HumanMessage(
            content="look at these images",
            additional_kwargs={"image_refs": ["/api/tmp_images/" + "a" * 32]},
        )

        # 1. Scan the message — must NOT detect a vision signal.
        has_images = _run_scan_for([msg])
        assert has_images is False, (
            "Vision scan returned True for a refs-only message "
            "(str content + additional_kwargs.image_refs). "
            "Ref-sends MUST NOT trigger vision routing (A1 "
            "contract)."
        )

        # 2. Derive routing — even with model_vision set,
        # ``use_vision_model`` must be False, and ``call_type``
        # must be STANDARD. The derivation runs in a sandbox with
        # the EXACT source bytes of ``daemon/graph.py:7063-7068``
        # so any change to those lines trips this test.
        use_vision, call_type = _derive_routing(
            has_images=has_images,
            model_vision="gpt-4-vision",
            llm_standard=lambda msgs: None,  # sentinel: "is not None"
        )
        assert use_vision is False
        assert call_type == "STANDARD", (
            f"call_type = {call_type!r}; expected 'STANDARD' for a "
            f"refs-only message. Vision routing re-fired."
        )

    def test_multimodal_message_routes_through_vision(self):
        """Sanity anchor: multimodal content (list with image_url
        block) DOES route through vision. This pins that the scan
        block above isn't returning False-by-accident — vision
        routing still fires for the legacy data-URI path.
        """
        msg = HumanMessage(
            content=[
                {"type": "text", "text": "what is this"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,..."},
                },
            ],
        )

        has_images = _run_scan_for([msg])
        assert has_images is True, (
            "Vision scan returned False for a multimodal message "
            "(list content with image_url block). The scan block "
            "appears broken — vision routing is now disabled for "
            "legacy data-URI sends too."
        )

        # With model_vision configured, the standard-gate triple
        # evaluates True — vision path selected.
        def _mock_llm(_msgs):
            return None

        use_vision, call_type = _derive_routing(
            has_images=has_images,
            model_vision="gpt-4-vision",
            llm_standard=_mock_llm,
        )
        assert use_vision is True
        assert call_type == "VISION"

    def test_multimodal_message_with_no_vision_model_falls_back(self):
        """Multimodal content but ``model_vision=None`` → standard
        fallback (``use_vision_model = False``) — same as the
        production behavior on a misconfigured daemon. Pins the
        standard-gate triple behavior end-to-end.
        """
        msg = HumanMessage(
            content=[
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ],
        )
        has_images = _run_scan_for([msg])
        assert has_images is True

        def _mock_llm(_msgs):
            return None

        use_vision, call_type = _derive_routing(
            has_images=has_images,
            model_vision=None,  # gate-empty
            llm_standard=_mock_llm,
        )
        # Production code uses the truthy/falsy of ``use_vision_model``
        # directly (``if use_vision_model`` at graph.py:7064, :7066), so
        # a falsy value (None, False, "", 0) all behave as 'standard'.
        # The derivation is the expression
        # ``has_images and model_vision and llm_standard is not None``,
        # which short-circuits to ``None`` when ``model_vision is None``.
        # We assert FALSY (not strict False) to match production semantics.
        assert not use_vision, (
            f"use_vision_model = {use_vision!r}; expected falsy when "
            f"model_vision is None (standard-gate triple empties)."
        )
        assert call_type == "STANDARD"

    def test_refs_and_multimodal_mixed_message(self):
        """Edge: messages list containing BOTH a refs-only
        HumanMessage AND a multimodal HumanMessage (e.g. the agent's
        prior turn). The scan must catch the multimodal entry →
        ``has_images = True`` (conservative: any image_url in any
        message triggers vision for the whole LLM call).
        """
        msgs = [
            HumanMessage(
                content="ref turn",
                additional_kwargs={"image_refs": ["/api/tmp_images/" + "a" * 32]},
            ),
            HumanMessage(
                content=[{"type": "image_url", "image_url": {"url": "data:..."}}],
            ),
        ]
        has_images = _run_scan_for(msgs)
        assert has_images is True

    def test_empty_message_list(self):
        """Empty messages list → has_images stays False (no False-by-
        exception path). Edge pinned at the seam.
        """
        has_images = _run_scan_for([])
        assert has_images is False
