"""Intent-match pins for the shared systemctl/kill guard (2026-11-18 outage).

WHAT THESE PINS DEFEND
======================
The pre-2026-11-18 guard classified ``systemctl`` verbs correctly but tested
the live-unit family with a plain SUBSTRING match over the raw command text.
That inverted the intent match in BOTH directions:

* UNDER-BLOCK — composed / variable-indirected unit tokens passed clean;
  three live outages came through that hole.
* OVER-BLOCK — non-executing prose (heredoc doc-writes, ``printf``, ``grep``
  patterns) and read-only inspection whose *property name* is a mutating-verb
  word (``systemctl show <unit> -p Restart``) were refused.

Every pin family below names the counter-mutation that reddens it. All pins
are PURE (no spawn); the module-local recorder at the top is a structural
tripwire for the two seam pins that call the real tool coroutines — under any
mutant, including refusal-gate-bypassing ones, a spawn is captured and turned
into a RED pin with a zero-exec proof instead of a live kill. NOTHING is ever
executed.
"""

import asyncio
import importlib
import inspect

import pytest

from daemon.tools import _systemctl_guard as guard
from daemon.tools.bash import _pkill_overlay_violation
from daemon.tools.proc_tools import create_proc_tools


# ---------------------------------------------------------------------------
# STRUCTURAL spawn-seam neuter — record-instead-of-spawn (house pattern)
# ---------------------------------------------------------------------------


class _RecordedSpawnStub:
    """Inert stand-in for ``asyncio.subprocess.Process`` — never a process."""

    def __init__(self, seq: int) -> None:
        self.pid = 2_000_000 + seq
        self.returncode = 0
        self.stdout = None
        self.stderr = None

    async def wait(self) -> int:
        return self.returncode


@pytest.fixture(autouse=True)
def _spawn_seam_recorder(monkeypatch):
    """Record-instead-of-spawn for every pin in this file."""
    spawns: list = []
    seq = 0

    async def _record_exec(*args, **kwargs):
        nonlocal seq
        seq += 1
        spawns.append({"argv": list(args), "cwd": kwargs.get("cwd")})
        return _RecordedSpawnStub(seq)

    async def _record_shell(command, *args, **kwargs):
        nonlocal seq
        seq += 1
        spawns.append({"argv": [command], "cwd": kwargs.get("cwd")})
        return _RecordedSpawnStub(seq)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _record_exec, raising=True)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _record_shell, raising=True)
    yield
    assert not spawns, (
        "STRUCTURAL SPAWN GUARD tripped (test_systemctl_intent_guard): the "
        f"recorder captured {len(spawns)} spawn attempt(s) that the refusal "
        f"gate failed to stop: {spawns!r}. NOTHING was executed — the "
        "recorder returned an inert stub — but the guard under test is "
        "bypassed and this pin family must REDDEN under such a mutant."
    )


# ---------------------------------------------------------------------------
# DENY leg — composition / indirection resolution
# ---------------------------------------------------------------------------
# Counter-mutations that redden this family:
#   * reverting to the substring match (no assignment resolution) ⇒ every
#     composed shape below returns None;
#   * dropping the var/exp arms of _resolve_parts ⇒ same;
#   * dropping the family check ⇒ the literal leg returns None.
# ---------------------------------------------------------------------------


def test_literal_ensemble_unit_still_refused():
    assert guard.systemctl_violation("systemctl restart ensemble-main") == "ensemble-main"
    assert guard.systemctl_violation("systemctl --user stop ensemble-demo.service") == "ensemble-demo"
    assert guard.systemctl_violation("sudo systemctl start ensemble-prod.service") == "ensemble-prod"
    assert guard.systemctl_violation("systemctl restart agents-ensemble") == "agents-ensemble"


def test_same_string_assignment_resolved():
    """The three outage shapes: variable-indirected unit operands."""
    assert guard.systemctl_violation(
        'UNIT=ensemble-main.service; systemctl restart "$UNIT"'
    ) == "ensemble-main"
    assert guard.systemctl_violation(
        "S=ensemble; systemctl restart ${S}-main"
    ) == "ensemble-main"
    assert guard.systemctl_violation(
        'systemctl restart "$(echo ensemble-main)"'
    ) == "ensemble-main"


def test_quoted_composition_resolved():
    assert guard.systemctl_violation('systemctl restart ensemble-"main"') == "ensemble-main"
    assert guard.systemctl_violation('systemctl restart "ensemble"-"main"') == "ensemble-main"
    # The brief's short-stem composition shape: a composed operand carrying our
    # stem is not "clearly non-ensemble", so it is treated as ours.
    assert guard.systemctl_violation('systemctl stop ENS-"main"') == "ens-main"


def test_assignment_before_command_on_one_line():
    assert guard.systemctl_violation(
        'UNIT=ensemble-daemon.service systemctl restart "$UNIT"'
    ) == "ensemble-daemon"


def test_unresolvable_operand_fails_closed():
    """Contract 1: an operand not statically resolvable to a clearly
    non-ensemble unit is REFUSED (fail-closed), never allowed."""
    assert guard.systemctl_violation('systemctl restart "$UNIT"') == guard.UNRESOLVED_UNIT_TOKEN
    assert guard.systemctl_violation('systemctl restart "$(cat /tmp/unit.txt)"') != None  # noqa: E711
    assert guard.systemctl_violation("systemctl restart 'ensemble-*'") != None  # noqa: E711
    assert guard.systemctl_violation('systemctl restart "$(readlink -f unit)"') != None  # noqa: E711


def test_unknown_verb_fails_closed():
    """Only the read-only allowlist is allowed; an unknown verb against an
    ensemble unit is refused (a future mutating verb cannot slip through)."""
    assert guard.systemctl_violation("systemctl freeze ensemble-main") != None  # noqa: E711
    assert guard.systemctl_violation("systemctl preset ensemble-main") != None  # noqa: E711


def test_shell_keyword_prefixed_command_refused():
    """``if systemctl restart <unit>; then`` — the keyword is not the command
    word (counter-mutation: drop the _SHELL_KEYWORDS peel ⇒ returns None)."""
    assert guard.systemctl_violation(
        "if systemctl restart ensemble-main; then echo done; fi"
    ) == "ensemble-main"
    assert guard.systemctl_violation(
        "while :; do systemctl stop ensemble-daemon; done"
    ) == "ensemble-daemon"


def test_ansi_c_quoted_operand_fails_closed():
    r"""ANSI-C quoting (``$'\x65nsemble-main'``) hides the literal from any
    static resolver ⇒ FAIL CLOSED (counter-mutation: treat ``$'...'`` as a
    plain literal ⇒ the composed-but-objectively-literal operand resolves
    to a non-family token and is ALLOWED)."""
    assert guard.systemctl_violation(r"systemctl restart $'\x65nsemble-main'") == guard.UNRESOLVED_UNIT_TOKEN


def test_runner_laundered_shapes_refused():
    """Quoting the kill shape through a runner is not an escape."""
    assert guard.systemctl_violation("bash -c 'systemctl restart ensemble-main'") == "ensemble-main"
    assert guard.systemctl_violation("bash <<'EOF'\nsystemctl restart ensemble-main\nEOF") == "ensemble-main"
    assert guard.systemctl_violation("echo $(systemctl restart ensemble-main)") == "ensemble-main"
    assert guard.systemctl_violation("xargs systemctl restart ensemble-main") == "ensemble-main"
    assert guard.systemctl_violation("eval 'systemctl restart ensemble-main'") == "ensemble-main"


# ---------------------------------------------------------------------------
# ALLOW leg — read-only inspection must pass end-to-end
# ---------------------------------------------------------------------------
# Counter-mutations that redden this family:
#   * restoring the text scan for the systemctl leg (verb words matched
#     anywhere in the text) ⇒ ``-p Restart`` and the prose cases refuse;
#   * dropping SYSTEMCTL_READONLY ⇒ all read-only pins refuse;
#   * making the deny unconditional (refuse every systemctl) ⇒ they refuse.
# ---------------------------------------------------------------------------

_READONLY_COMMANDS = [
    "systemctl status ensemble-main",
    "systemctl status ensemble-main --no-pager",
    "systemctl show ensemble-main",
    "systemctl is-active ensemble-main --no-pager",
    "systemctl is-enabled ensemble-main",
    "systemctl is-failed ensemble-main",
    "systemctl cat ensemble-main | head -5",
    "systemctl list-units | grep ensemble",
    "systemctl list-unit-files --type=service | grep ensemble",
    "systemctl show-environment",
    "systemctl get-default",
    # Property names that ARE mutating-verb words — the original over-block.
    "systemctl show ensemble-main -p Restart",
    "systemctl show ensemble-main --property=Restart",
    "systemctl status ensemble-main -n 20 | grep -i restart",
    "journalctl -u ensemble-main -n 20 | grep -i enable",
]


@pytest.mark.parametrize("command", _READONLY_COMMANDS)
def test_readonly_allowed(command):
    assert guard.systemctl_violation(command) is None


def test_non_ensemble_mutation_allowed():
    """The deny is scoped to the ensemble's own units."""
    assert guard.systemctl_violation("systemctl restart nginx") is None
    assert guard.systemctl_violation("systemctl --user restart postgresql@16-main") is None
    assert guard.systemctl_violation("systemctl daemon-reload") is None


def test_non_executing_text_allowed():
    """Doc-writes / prose / search patterns are DATA, not kill intent."""
    assert guard.systemctl_violation(
        "cat > notes.md <<'EOF'\nrun systemctl restart ensemble-main to recover\nEOF"
    ) is None
    assert guard.systemctl_violation(
        "printf '%s\\n' 'systemctl stop ensemble-prod' > /tmp/reminder.txt"
    ) is None
    assert guard.systemctl_violation(
        "grep -n 'systemctl restart ensemble-main' docs/ops.md"
    ) is None
    assert guard.systemctl_violation("echo 'do not systemctl stop ensemble-main'") is None


# ---------------------------------------------------------------------------
# Kill legs — UNCHANGED text-scoped semantics (defence in depth)
# ---------------------------------------------------------------------------


def test_kill_legs_intact():
    assert guard.kill_leg_violation("pkill -f ensemble-prod") == "ensemble-prod"
    assert guard.kill_leg_violation("pkill -f 'ensemble-main'") == "ensemble-main"
    assert guard.kill_leg_violation("killall ensemble-daemon") == "ensemble-daemon"
    assert guard.kill_leg_violation("fuser -k 9797/tcp") == "9797"
    assert guard.kill_leg_violation("kill -9 $(pgrep -f ensemble-prod)") == "ensemble-prod"
    assert guard.kill_leg_violation("pkill -f my-scratch-daemon") is None
    assert guard.kill_leg_violation("echo skill management") is None


# ---------------------------------------------------------------------------
# SEAM — bash lane delegates to the shared module; fence stays ambient-only
# ---------------------------------------------------------------------------


def test_bash_seam_delegates_to_shared_module():
    bash_mod = importlib.import_module("daemon.tools.bash")
    src = inspect.getsource(bash_mod._pkill_overlay_violation)
    assert "_systemctl_guard_overlay_violation" in src
    assert "_PKILL_OVERLAY_LIVE_NAME_PATTERNS == ()" not in src


def test_shared_module_is_pure_no_env_reads():
    """The fence read lives at the bash seam ONLY (shared module = pure)."""
    src = inspect.getsource(guard)
    assert "os.environ" not in src


def test_bash_seam_both_directions():
    assert _pkill_overlay_violation('S=ensemble; systemctl restart ${S}-main') == "ensemble-main"
    assert _pkill_overlay_violation("systemctl status ensemble-main") is None


def test_bash_seam_fence_ambient_only(monkeypatch):
    monkeypatch.delenv("ENSEMBLE_FENCED_TEST_CONTEXT", raising=False)
    overlay = {"ENSEMBLE_FENCED_TEST_CONTEXT": "1"}
    assert _pkill_overlay_violation('systemctl stop "$UNRESOLVED_UNIT"', env=overlay) is not None
    monkeypatch.setenv("ENSEMBLE_FENCED_TEST_CONTEXT", "1")
    assert _pkill_overlay_violation('systemctl stop "$UNRESOLVED_UNIT"') is None


# ---------------------------------------------------------------------------
# SEAM — proc lane inherits the whole model through the delegation
# ---------------------------------------------------------------------------


def _proc_run():
    return next(t for t in create_proc_tools("scig-pin-instance") if t.name == "proc_run")


async def test_proc_lane_refuses_composed_shapes():
    pr = _proc_run()
    for command in (
        'UNIT=ensemble-main.service; systemctl restart "$UNIT"',
        'S=ensemble; systemctl restart ${S}-main',
        'systemctl restart "$(echo ensemble-main)"',
        "systemctl restart ensemble-main",
    ):
        text = await pr.coroutine(command=command)
        assert text.startswith(
            "Error: refused by the rogue-containment pkill overlay guard"
        ), (command, text)


async def test_proc_lane_allows_readonly():
    """Read-only must reach the timeout validation (i.e. pass the guard)."""
    pr = _proc_run()
    for command in (
        "systemctl status ensemble-main",
        "systemctl show ensemble-main -p Restart",
        "systemctl list-units | grep ensemble",
    ):
        text = await pr.coroutine(command=command, timeout=-1)
        assert "timeout must be >= 0" in text, (command, text)
        assert "refused by the rogue-containment" not in text, (command, text)


async def test_proc_lane_delegates_to_bash_wrapper():
    """Structural: proc_run still imports the bash wrapper at call time."""
    proc_mod = importlib.import_module("daemon.tools.proc_tools")
    assert (
        "from daemon.tools.bash import _pkill_overlay_violation"
        in inspect.getsource(proc_mod.create_proc_tools)
    )
