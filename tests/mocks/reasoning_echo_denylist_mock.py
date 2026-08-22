"""Real-behavior mock verification: reasoning-echo DENYLIST semantics.

Drives the REAL ``ThinkingChatOpenAI`` (daemon/graph.py, branch
``feature/reasoning-echo-denylist``, commits 28ea76a9 + 018800b8) fully
in-process — no HTTP, no ports, no daemon start. Payload construction via
``_get_request_payload`` is pure dict building; the class under test is
NEVER stubbed (inert api_key/base_url kwargs only, network never fires).

Env -> LLMConfig -> ClassVar wiring replicates daemon/__main__.py:30-32
exactly: ``LLMConfig`` (pydantic-settings, env_prefix="OPENAI_", NoDecode +
field_validator CSV parsing) -> ``ThinkingChatOpenAI.reasoning_echo_disabled_models``.

Six scenarios (S1-S6) per the task spec. Each scenario saves/restores:
  - the two echo env vars (OPENAI_REASONING_ECHO_DISABLED_MODELS,
    OPENAI_REASONING_ECHO_MODELS), with set-but-empty distinguishable
    from absent;
  - the ClassVar list and the deprecation-warning module flag.

Dual-layer timeout: signal.alarm(180) inner + `timeout 200` outer guard.
Never modifies production code or the 4 existing pytest files. A scenario
FAIL caused by a genuine production bug is REPORTED, not fixed.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

# Ensure repo root is on the path so the daemon package is importable when
# the script is invoked from any CWD.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

import daemon.config as config_module  # noqa: E402
from daemon.config import LLMConfig, warn_deprecated_reasoning_echo_env  # noqa: E402
from daemon.graph import ThinkingChatOpenAI  # noqa: E402

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("reasoning_echo_denylist_mock")

ENV_NEW = "OPENAI_REASONING_ECHO_DISABLED_MODELS"  # denylist (effective)
ENV_OLD = "OPENAI_REASONING_ECHO_MODELS"           # allowlist (dead, warns)

_ABSENT = object()  # sentinel: env var must NOT exist


# ---------------------------------------------------------------------------
# Self-timeout (script-level guard, inner layer)
# ---------------------------------------------------------------------------
HARD_TIMEOUT_SECONDS = 180


def _timeout_handler(_signum: int, _frame: Any) -> None:
    print("RESULT: TIMEOUT (script exceeded 180s hard cap)", flush=True)
    sys.exit(124)


signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(HARD_TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# Scenario plumbing
# ---------------------------------------------------------------------------
@dataclass
class ScenarioResult:
    name: str
    passed: bool
    note: str = ""
    details: dict = field(default_factory=dict)


@contextmanager
def echo_env(new: Any = _ABSENT, old: Any = _ABSENT):
    """Set the two echo env vars for the duration of the block.

    ``_ABSENT`` deletes the var; any str (including ``""``) sets it —
    set-but-empty is distinguishable from unset.
    """
    wanted = ((ENV_NEW, new), (ENV_OLD, old))
    saved = {k: os.environ.get(k, _ABSENT) for k, _ in wanted}
    try:
        for key, val in wanted:
            if val is _ABSENT:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        yield
    finally:
        for key, val in saved.items():
            if val is _ABSENT:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


@contextmanager
def class_state():
    """Snapshot/restore the ClassVar list + deprecation module flag."""
    saved_list = list(ThinkingChatOpenAI.reasoning_echo_disabled_models)
    saved_flag = config_module._reasoning_echo_deprecation_warned
    try:
        yield
    finally:
        ThinkingChatOpenAI.reasoning_echo_disabled_models = saved_list
        config_module._reasoning_echo_deprecation_warned = saved_flag


def wire_classvar_from_env() -> list[str]:
    """Replicate daemon/__main__.py:30-32 startup wiring exactly."""
    cfg = LLMConfig(_env_file=None)
    ThinkingChatOpenAI.reasoning_echo_disabled_models = list(
        cfg.reasoning_echo_disabled_models or []
    )
    return list(ThinkingChatOpenAI.reasoning_echo_disabled_models)


def build_payload(model: str, messages: list) -> dict:
    """Build the outgoing request payload via the REAL class.

    Payload construction is pure — no request fires. base_url points at a
    closed local port purely as an inert marker of that guarantee.
    """
    llm = ThinkingChatOpenAI(
        model=model,
        api_key="test-key",
        base_url="http://127.0.0.1:1",
    )
    return llm._get_request_payload(messages)


def observed_echo(payload: dict, assistant_index: int = 0) -> dict:
    """Evidence of reasoning_content presence in one assistant payload msg."""
    assistants = [m for m in payload.get("messages", []) if m.get("role") == "assistant"]
    msg = assistants[assistant_index]
    present = msg.get("reasoning_content") is not None
    return {
        "payload_includes_reasoning_content": present,
        "observed_value": msg.get("reasoning_content"),
        "key_in_dict": "reasoning_content" in msg,
    }


def thinking_msg(content: str, reasoning: str) -> AIMessage:
    """Plain (non-tool-call) assistant turn carrying reasoning_content."""
    return AIMessage(
        content=content,
        additional_kwargs={"reasoning_content": reasoning},
    )


def _fail(name: str, note: str, **details: Any) -> ScenarioResult:
    return ScenarioResult(name=name, passed=False, note=note, details=details)


def _ok(name: str, note: str, **details: Any) -> ScenarioResult:
    return ScenarioResult(name=name, passed=True, note=note, details=details)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def scenario_s1_default_all_models_echo() -> ScenarioResult:
    """S1: no echo env vars -> even gpt-4o (previously non-echoing) echoes."""
    name = "S1 DEFAULT (no env) — gpt-4o echoes"
    with echo_env(new=_ABSENT, old=_ABSENT), class_state():
        wired = wire_classvar_from_env()
        payload = build_payload("gpt-4o", [thinking_msg("ans", "S1-thinking")])
        ev = observed_echo(payload)
        try:
            assert wired == [], f"classvar should be [] (no env), got {wired!r}"
            assert ev["payload_includes_reasoning_content"] is True, (
                f"gpt-4o payload must INCLUDE reasoning_content under default "
                f"env; observed={ev}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), env_new="(absent)", env_old="(absent)",
                         classvar=wired, gpt_4o=ev)
        return _ok(name, "gpt-4o payload includes reasoning_content",
                   env_new="(absent)", env_old="(absent)", classvar=wired, gpt_4o=ev)


def scenario_s2_denylist_spared_others() -> ScenarioResult:
    """S2: deny gpt-4o -> gpt-4o excluded, deepseek-chat still echoes."""
    name = "S2 DENYLIST SPARES OTHERS"
    with echo_env(new="gpt-4o", old=_ABSENT), class_state():
        wired = wire_classvar_from_env()
        p_gpt = build_payload("gpt-4o", [thinking_msg("a", "S2-gpt")])
        p_ds = build_payload("deepseek-chat", [thinking_msg("b", "S2-ds")])
        ev_gpt, ev_ds = observed_echo(p_gpt), observed_echo(p_ds)
        try:
            assert wired == ["gpt-4o"], f"classvar should be ['gpt-4o'], got {wired!r}"
            assert ev_gpt["payload_includes_reasoning_content"] is False, (
                f"gpt-4o payload must EXCLUDE reasoning_content; observed={ev_gpt}"
            )
            assert ev_ds["payload_includes_reasoning_content"] is True, (
                f"deepseek-chat payload must still INCLUDE reasoning_content; "
                f"observed={ev_ds}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), env_new="gpt-4o", classvar=wired,
                         gpt_4o=ev_gpt, deepseek_chat=ev_ds)
        return _ok(name, "gpt-4o excluded; deepseek-chat still echoes",
                   env_new="gpt-4o", classvar=wired, gpt_4o=ev_gpt, deepseek_chat=ev_ds)


def scenario_s3_case_insensitive() -> ScenarioResult:
    """S3: denylist entry 'GPT-4O' (uppercase) still disables gpt-4o."""
    name = "S3 CASE-INSENSITIVE (GPT-4O disables gpt-4o)"
    with echo_env(new="GPT-4O", old=_ABSENT), class_state():
        wired = wire_classvar_from_env()
        payload = build_payload("gpt-4o", [thinking_msg("ans", "S3-thinking")])
        ev = observed_echo(payload)
        try:
            assert wired == ["GPT-4O"], f"classvar should be ['GPT-4O'], got {wired!r}"
            assert ev["payload_includes_reasoning_content"] is False, (
                f"uppercase 'GPT-4O' entry must still disable gpt-4o; observed={ev}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), env_new="GPT-4O", classvar=wired, gpt_4o=ev)
        return _ok(name, "uppercase entry matches gpt-4o case-insensitively",
                   env_new="GPT-4O", classvar=wired, gpt_4o=ev)


def scenario_s4_empty_string() -> ScenarioResult:
    """S4: set-but-empty env -> parses to [] -> gpt-4o still echoes.

    Guards against a ``[""]`` poison entry (empty string substring-matches
    every model name, which would disable echo everywhere).
    """
    name = "S4 EMPTY STRING env (no [\"\"] poison)"
    with echo_env(new="", old=_ABSENT), class_state():
        wired = wire_classvar_from_env()
        payload = build_payload("gpt-4o", [thinking_msg("ans", "S4-thinking")])
        ev = observed_echo(payload)
        try:
            assert wired == [], (
                f"set-but-empty env must parse to [], got {wired!r} "
                f"(a [''] entry would substring-match every model)"
            )
            assert ev["payload_includes_reasoning_content"] is True, (
                f"gpt-4o must still echo with empty denylist; observed={ev}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), env_new="(set to empty string)",
                         classvar=wired, gpt_4o=ev)
        return _ok(name, "empty env -> [] -> gpt-4o echoes (no poison entry)",
                   env_new="(set to empty string)", classvar=wired, gpt_4o=ev)


class _CaptureHandler(logging.Handler):
    """Collects WARNING+ log records for deprecation-warning counting."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _count_deprecation_warnings(handler: _CaptureHandler) -> int:
    return sum(
        1
        for r in handler.records
        if r.levelno >= logging.WARNING and ENV_OLD in r.getMessage()
    )


def scenario_s5_deprecation() -> ScenarioResult:
    """S5: old env set -> exactly ONE warning per load (dedup via module
    flag), and behavior unchanged (old key gates nothing).

    Also records (as observation, not a failure) the actual dedup
    granularity: the once-per-process flag is consumed even by a call made
    while the env var is ABSENT, so a later call with the env var set stays
    silent.
    """
    name = "S5 DEPRECATION (old env warns once, gates nothing)"
    with echo_env(new=_ABSENT, old="deepseek"), class_state():
        # -- behavioral half: old key must not gate anything ---------------
        wired = wire_classvar_from_env()
        payload = build_payload("gpt-4o", [thinking_msg("ans", "S5-thinking")])
        ev = observed_echo(payload)

        # -- warning half: 3 calls -> exactly 1 warning ---------------------
        cfg_logger = logging.getLogger("daemon.config")
        handler = _CaptureHandler()
        cfg_logger.addHandler(handler)
        try:
            config_module._reasoning_echo_deprecation_warned = False  # fresh "load"
            warn_deprecated_reasoning_echo_env()
            warn_deprecated_reasoning_echo_env()
            warn_deprecated_reasoning_echo_env()
        finally:
            cfg_logger.removeHandler(handler)
        warning_count = _count_deprecation_warnings(handler)

        # -- observation: dedup granularity (flag consumed even when env
        #    absent at first call). NOT spec'd — recorded, never fatal. ----
        handler2 = _CaptureHandler()
        cfg_logger.addHandler(handler2)
        try:
            os.environ.pop(ENV_OLD, None)
            config_module._reasoning_echo_deprecation_warned = False
            warn_deprecated_reasoning_echo_env()          # env absent: silent
            os.environ[ENV_OLD] = "deepseek"
            warn_deprecated_reasoning_echo_env()          # env now set: still silent?
        finally:
            cfg_logger.removeHandler(handler2)
        gran_obs = {
            "sequence": "reset flag -> call(env absent) -> call(env set)",
            "warnings_observed": _count_deprecation_warnings(handler2),
            "interpretation": (
                "module flag is consumed even when env var is absent at the "
                "first call, so a later call with env set stays silent "
                "(per-process budget, not per-env-state)"
            ),
        }

        try:
            assert wired == [], f"old key must not feed the denylist; classvar={wired!r}"
            assert ev["payload_includes_reasoning_content"] is True, (
                f"old key must not gate echo; observed={ev}"
            )
            assert warning_count == 1, (
                f"expected exactly 1 deprecation warning across 3 calls "
                f"(helper should dedup), observed {warning_count}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), env_old="deepseek", env_new="(absent)",
                         classvar=wired, gpt_4o=ev, warnings_from_3_calls=warning_count,
                         dedup_granularity_observation=gran_obs)
        return _ok(
            name,
            "3 loads -> exactly 1 warning; gpt-4o still echoes (old key inert)",
            env_old="deepseek", env_new="(absent)", classvar=wired, gpt_4o=ev,
            warnings_from_3_calls=warning_count, dedup_granularity_observation=gran_obs,
        )


def scenario_s6_presence_gate() -> ScenarioResult:
    """S6: presence gate — plain non-tool-call turn WITH reasoning echoes
    (default env, gpt-4o); assistant WITHOUT reasoning never echoes
    (any model — probed with deepseek-chat, echo-eligible)."""
    name = "S6 PRESENCE GATE"
    with echo_env(new=_ABSENT, old=_ABSENT), class_state():
        wired = wire_classvar_from_env()
        # Part A: plain (non-tool-call) assistant turn WITH reasoning.
        msgs_a = [HumanMessage(content="hi"), thinking_msg("plain answer", "S6-thinking")]
        ev_a = observed_echo(build_payload("gpt-4o", msgs_a))
        # Part B: assistant WITHOUT reasoning_content — echo-eligible model.
        msgs_b = [HumanMessage(content="hi"), AIMessage(content="no thinking here")]
        ev_b = observed_echo(build_payload("deepseek-chat", msgs_b))
        try:
            assert wired == [], f"classvar should be [], got {wired!r}"
            assert ev_a["payload_includes_reasoning_content"] is True, (
                f"plain non-tool-call turn WITH reasoning must echo; observed={ev_a}"
            )
            assert ev_b["payload_includes_reasoning_content"] is False, (
                f"assistant WITHOUT reasoning must never echo; observed={ev_b}"
            )
        except AssertionError as exc:
            return _fail(name, str(exc), classvar=wired, with_reasoning_gpt_4o=ev_a,
                         without_reasoning_deepseek_chat=ev_b)
        return _ok(name, "presence gate intact both directions",
                   classvar=wired, with_reasoning_gpt_4o=ev_a,
                   without_reasoning_deepseek_chat=ev_b)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
SCENARIOS = [
    scenario_s1_default_all_models_echo,
    scenario_s2_denylist_spared_others,
    scenario_s3_case_insensitive,
    scenario_s4_empty_string,
    scenario_s5_deprecation,
    scenario_s6_presence_gate,
]


def _fmt_details(details: dict) -> str:
    out = []
    for k, v in details.items():
        out.append(f"    {k}: {v}")
    return "\n".join(out)


def main() -> int:
    results: list[ScenarioResult] = []
    for fn in SCENARIOS:
        try:
            r = fn()
        except Exception:
            tb = traceback.format_exc()
            r = ScenarioResult(name=fn.__name__, passed=False,
                               note=f"exception: {tb}", details={})
        results.append(r)

    print()
    print("=== Real-Behavior Mock Test: Reasoning-Echo Denylist Semantics ===")
    print()
    for r in results:
        verdict = "PASS" if r.passed else "FAIL"
        print(f"Scenario {r.name}: {verdict}")
        if r.note:
            print(f"  - {r.note}")
        if r.details:
            print(_fmt_details(r.details))
        print()
    overall = "PASS" if all(r.passed for r in results) else "FAIL"
    print(f"RESULT: {overall}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    started = time.monotonic()
    try:
        exit_code = main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        print("RESULT: FAIL (uncaught exception)", flush=True)
        exit_code = 1
    finally:
        elapsed = time.monotonic() - started
        print(f"Actual runtime: {elapsed:.2f} s", flush=True)
        signal.alarm(0)  # clean exit — cancel the self-timeout
    sys.exit(exit_code)
