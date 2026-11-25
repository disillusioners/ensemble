"""Fence + behavior pins for the daemon/graph.py eager-f-string debug guards.

Module M4 (cpu-l1-cheap-overhead): every ``logger.debug(f...)`` call site in
``daemon/graph.py`` must sit behind ``if logger.isEnabledFor(logging.DEBUG):``
so the f-string — and its str()/len()/slice work — is never built when DEBUG
is off (the daemon runs at INFO in production; the hot site ran on EVERY LLM
invocation).

Three pin families:
  (a) FENCE: zero unguarded f-string debug sites (AST-verified), and the
      guarded family is exactly the pinned canonical set — message bytes
      unchanged (mutation check: unguard or reword one site → RED).
  (b) BEHAVIOR: at DEBUG the guarded blocks still emit the exact same
      formatted message (driven through the smallest enclosing callable).
  (c) LEVEL-OFF: at INFO the guard short-circuits — logger.debug is never
      invoked at all (spy logger), so nothing is formatted.
"""

import ast
import logging
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.messages.ai import AIMessageChunk

GRAPH_PATH = Path(__file__).resolve().parents[2] / "daemon" / "graph.py"

# Canonical family: the exact source text of every guarded logger.debug(f...)
# call in daemon/graph.py at commit 362fa4d11+guards (module M4). A site that
# is reworded, re-levelled, added, or removed fails the set-equality fence —
# update this list ONLY with an explicit review of the new site's cost.
# Single-line sites are pinned byte-exact; multi-line sites whitespace-
# normalized (re-indentation allowed, wording/placeholders not).
CANONICAL_EXACT = {
    'logger.debug(f"[LLM] Could not extract reasoning_content in _create_chat_result: {e}")',
    'logger.debug(f"[LLM] Extracted reasoning from metadata: {str(reasoning)[:100]}...")',
    'logger.debug(f"[LLM] Could not extract reasoning_content: {e}")',
    'logger.debug(f"[LLM] Could not get original messages for reasoning_content injection: {e}")',
    'logger.debug(f"[LLM] Injected reasoning_content for assistant message {assistant_idx}")',
    'logger.debug(f"[LLM] Stream extracted reasoning_content: {str(reasoning_content)[:50]}...")',
    'logger.debug(f"[Graph] Thinking-only response, continuing...")',
    "logger.debug(f'[LLM][{instance_short}] Invoking LLM ({call_type}) with {len(full_messages)} messages (model={model_name}, transient_attempts={transient}, timeout_attempts={timeout}{vision_log})')",
    "logger.debug(f'[LLM] Context length exceeded, attempting reactive compaction for {len(messages)} messages')",
    "logger.debug(f'[LLM] Reactive compaction complete: {result.messages_before} -> {result.messages_after} messages, {result.tokens_saved} tokens saved ({result.compaction_type})')",
    "logger.debug(f'[LLM] Tool call: {tool_names[0]} — {tc_args_str}..., tools: {tool_names}')",
    "logger.debug(f'[LLM] Response: {response.content[:80]}...')",
}
CANONICAL_MULTILINE = {
    'logger.debug( f"LLM configured with {transient_attempts} transient retries, " f"{timeout_attempts} timeout retries" )',
    'logger.debug( f"[ContextSlot] B1 re-append: {len(ephemeral_context_msgs)} " f"ephemeral context message(s) re-injected after " f"loop-breaker repair for {instance_short}" )',
    'logger.debug( f"[ContextSlot] instance_repository.get({instance_id[:8]}...) " f"failed during project_injected check: {exc}" )',
    'logger.debug( f"[ContextSlot] instance_repository.get({instance_id[:8]}...) " f"failed during project_id resolution: {exc}" )',
    'logger.debug( f"[ContextSlot] Injected {len(ephemeral_context_msgs)} " f"ephemeral context message(s) for {instance_short} " f"before LLM call" )',
    'logger.debug( f"[watchover_check] turn counter write failed for " f"{instance_id[:8]}...: {type(turn_err).__name__}: {turn_err}" )',
    'logger.debug( f"[LLM] Extracted reasoning_content from raw response: " f"{str(reasoning)[:100]}..." )',
    "logger.debug( f'[LLM] Reactive compaction: re-appended ' f'{len(injected_msgs)} injected message(s) for ' f'{instance_short}' )",
    "logger.debug( f'[LLM] Reactive compaction: re-appended ' f'{len(ephemeral_context_msgs)} ephemeral context ' f'message(s) for {instance_short}' )",
}


# ─────────────────────────────────────────────────────────────────────────────
# AST helpers
# ─────────────────────────────────────────────────────────────────────────────


def _is_module_logger_debug_fstring(node: ast.Call) -> bool:
    """True for logger.debug(<f-string>) where logger is the module global."""
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "debug"):
        return False
    if not (isinstance(func.value, ast.Name) and func.value.id == "logger"):
        return False
    if not node.args:
        return False
    return isinstance(node.args[0], ast.JoinedStr)


def _is_debug_enabled_guard(test: ast.expr) -> bool:
    """True for logger.isEnabledFor(logging.DEBUG)."""
    if not (isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute)
            and test.func.attr == "isEnabledFor"):
        return False
    if not (isinstance(test.func.value, ast.Name) and test.func.value.id == "logger"):
        return False
    if len(test.args) != 1:
        return False
    arg = test.args[0]
    return (isinstance(arg, ast.Attribute) and arg.attr == "DEBUG"
            and isinstance(arg.value, ast.Name) and arg.value.id == "logging")


def _walk_with_parents(tree: ast.AST):
    """Yield (node, parent, grandparent) triples via a parent-tracking walk."""
    stack = [(tree, None, None)]
    while stack:
        node, parent, gp = stack.pop()
        yield node, parent, gp
        for child in ast.iter_child_nodes(node):
            stack.append((child, node, parent))


def _statement_parent(call_parent, call_gp):
    """Unwrap the ast.Expr statement wrapper around a bare expression call."""
    return call_gp if isinstance(call_parent, ast.Expr) else call_parent


def _collect_fstring_debug_sites(source: str):
    """Return {call_node: statement-parent node} for every module-logger f-string debug call."""
    tree = ast.parse(source)
    sites = {}
    for node, parent, gp in _walk_with_parents(tree):
        if isinstance(node, ast.Call) and _is_module_logger_debug_fstring(node):
            sites[node] = _statement_parent(parent, gp)
    return sites, tree


# ─────────────────────────────────────────────────────────────────────────────
# (a) FENCE
# ─────────────────────────────────────────────────────────────────────────────


class TestDebugFstringGuardFence:
    def test_zero_unguarded_fstring_debug_sites(self):
        source = GRAPH_PATH.read_text(encoding="utf-8")
        sites, _tree = _collect_fstring_debug_sites(source)
        assert sites, "f-string debug family vanished from daemon/graph.py — pins are stale"
        unguarded = [
            ast.get_source_segment(source, node)
            for node, parent in sites.items()
            if not (isinstance(parent, ast.If) and _is_debug_enabled_guard(parent.test))
        ]
        assert not unguarded, (
            f"{len(unguarded)} unguarded logger.debug(f...) site(s) in daemon/graph.py "
            f"(f-string built before the level check): {unguarded}"
        )

    def test_guarded_family_matches_canonical_set(self):
        source = GRAPH_PATH.read_text(encoding="utf-8")
        sites, _tree = _collect_fstring_debug_sites(source)
        guarded = [node for node in sites
                   if isinstance(sites[node], ast.If) and _is_debug_enabled_guard(sites[node].test)]
        segments = [ast.get_source_segment(source, node) for node in guarded]
        exact = {s for s in segments if "\n" not in s}
        multiline = {" ".join(s.split()) for s in segments if "\n" in s}
        assert exact == CANONICAL_EXACT, (
            "Single-line guarded debug-call family drifted. "
            f"unexpected={exact - CANONICAL_EXACT} missing={CANONICAL_EXACT - exact}"
        )
        assert multiline == CANONICAL_MULTILINE, (
            "Multi-line guarded debug-call family drifted. "
            f"unexpected={multiline - CANONICAL_MULTILINE} "
            f"missing={CANONICAL_MULTILINE - multiline}"
        )

    def test_plain_string_debug_sites_untouched_by_fence(self):
        """Non-f-string debug calls (no formatting cost) must stay OUTSIDE the fence."""
        source = GRAPH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        plain = 0
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "debug"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"):
                continue
            if node.args and not isinstance(node.args[0], ast.JoinedStr):
                plain += 1
        assert plain >= 1, (
            "expected at least one plain-string logger.debug site (e.g. '[LLM] Response: empty'); "
            "the fence only covers f-string sites"
        )


# ─────────────────────────────────────────────────────────────────────────────
# (b) BEHAVIOR — DEBUG on: identical formatted message still emitted
# ─────────────────────────────────────────────────────────────────────────────


class TestDebugEmissionUnchanged:
    def test_should_continue_thinking_only_emits_same_message(self, caplog):
        """:4382 — smallest enclosing callable of the [Graph] thinking-only site."""
        from daemon import graph

        state = {"messages": [
            AIMessage(content="", additional_kwargs={"reasoning_content": "thinking..."}),
        ]}
        with caplog.at_level(logging.DEBUG, logger="daemon.graph"):
            route = graph.should_continue(state)
        assert route == "agent"
        emitted = [r for r in caplog.records
                   if r.levelno == logging.DEBUG
                   and "[Graph] Thinking-only response" in r.getMessage()]
        assert len(emitted) == 1, "thinking-only debug line missing at DEBUG level"
        assert emitted[0].getMessage() == "[Graph] Thinking-only response, continuing..."

    def test_convert_delta_emits_same_reasoning_message(self, caplog):
        """:3862 — smallest enclosing callable of the stream-reasoning site."""
        from daemon.graph import ThinkingChatOpenAI

        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")
        reasoning = "r" * 60  # sliced to 50 inside the f-string
        with caplog.at_level(logging.DEBUG, logger="daemon.graph"):
            chunk = llm._convert_delta_to_message_chunk(
                {"content": "x", "reasoning_content": reasoning}, AIMessageChunk
            )
        assert chunk.additional_kwargs["reasoning_content"] == reasoning
        expected = f"[LLM] Stream extracted reasoning_content: {'r' * 50}..."
        emitted = [r.getMessage() for r in caplog.records
                   if r.levelno == logging.DEBUG
                   and "Stream extracted reasoning_content" in r.getMessage()]
        assert emitted == [expected], "stream-reasoning debug message changed"


# ─────────────────────────────────────────────────────────────────────────────
# (c) LEVEL-OFF — INFO: guard short-circuits, logger.debug never invoked
# ─────────────────────────────────────────────────────────────────────────────


class _SpyLogger:
    """Records whether .debug is reached; isEnabledFor mirrors a real level.

    A real logger configured at level L answers isEnabledFor(requested)
    True iff requested >= L — so a logger at INFO has isEnabledFor(DEBUG)
    False, which is exactly the short-circuit the guards rely on.
    """

    def __init__(self, level: int):
        self._level = level
        self.debug_calls: list = []

    def isEnabledFor(self, level: int) -> bool:
        return level >= self._level

    def debug(self, msg, *args, **kwargs):
        self.debug_calls.append(msg)


class TestDebugGuardShortCircuit:
    def test_should_continue_never_formats_at_info(self, monkeypatch):
        from daemon import graph

        spy = _SpyLogger(logging.INFO)
        monkeypatch.setattr(graph, "logger", spy)
        state = {"messages": [
            AIMessage(content="", additional_kwargs={"reasoning_content": "thinking..."}),
        ]}
        assert graph.should_continue(state) == "agent"
        assert spy.debug_calls == [], "logger.debug reached at INFO — guard does not short-circuit"

    def test_convert_delta_never_formats_at_info(self, monkeypatch):
        from daemon import graph
        from daemon.graph import ThinkingChatOpenAI

        spy = _SpyLogger(logging.INFO)
        monkeypatch.setattr(graph, "logger", spy)
        llm = ThinkingChatOpenAI(model="test-model", api_key="test-key")
        llm._convert_delta_to_message_chunk(
            {"content": "x", "reasoning_content": "reasoning"}, AIMessageChunk
        )
        assert spy.debug_calls == [], "logger.debug reached at INFO — guard does not short-circuit"

    def test_real_logger_at_info_emits_nothing(self, caplog):
        """Belt-and-braces on the real module logger (no spy): INFO swallows the site.

        caplog.at_level sets the daemon.graph logger to INFO for the block,
        so the real isEnabledFor(DEBUG) guard evaluates False — no global
        level mutation left behind.
        """
        from daemon import graph

        with caplog.at_level(logging.INFO, logger="daemon.graph"):
            graph.should_continue({"messages": [
                AIMessage(content="", additional_kwargs={"reasoning_content": "t"}),
            ]})
        assert not any("Thinking-only response" in r.getMessage() for r in caplog.records)
