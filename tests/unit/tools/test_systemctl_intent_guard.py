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


# ---------------------------------------------------------------------------
# DENY leg 4 — the unit-stop WRAPPER ``scripts/stop-ensemble.sh``
# ---------------------------------------------------------------------------
# The ACTUAL kill mechanism of all three 2026-11-18 outages (D2 hunt): the
# agent ran ``bash scripts/stop-ensemble.sh``; its UNIT_MANAGED branch
# composes the unit from ``ENSEMBLE_SUPERVISION_RESULT`` and issues
# ``systemctl stop``/``kill`` ITSELF, so the command text carries neither a
# ``systemctl`` token nor a live-unit token. Every invocation shape below is
# refused by BASENAME EQUALITY, in any directory, with ANY args/env —
# ``DRY_RUN=1`` included (no agent-lane use of the wrapper is sanctioned;
# the supervision machinery invokes it directly, never via this lane).
#
# Counter-mutation M5a: neutralise ``_protected_wrapper_violation`` (plant
# ``return None`` at its top) ⇒ every pin here returns None and REDDENs.
# ---------------------------------------------------------------------------

_WRAPPER_SHAPES = [
    "bash scripts/stop-ensemble.sh",
    "sh /home/nea/ensemble-src/scripts/stop-ensemble.sh",
    "dash scripts/stop-ensemble.sh",
    "./scripts/stop-ensemble.sh",
    "stop-ensemble.sh",
    "/home/nea/agents-ensemble/scripts/stop-ensemble.sh",
    "env DRY_RUN=1 bash scripts/stop-ensemble.sh",
    "DRY_RUN=1 bash scripts/stop-ensemble.sh",
    "bash scripts/stop-ensemble.sh --force",
    "cd /home/nea/ensemble-src && bash scripts/stop-ensemble.sh",
    "sudo bash scripts/stop-ensemble.sh",
    "sudo -u root bash scripts/stop-ensemble.sh",
    "nohup bash scripts/stop-ensemble.sh &",
    "timeout 60 bash scripts/stop-ensemble.sh",
    "setsid bash scripts/stop-ensemble.sh",
    "bash -c 'scripts/stop-ensemble.sh'",
    "sh -c 'bash scripts/stop-ensemble.sh'",
    "bash <<'EOF'\nscripts/stop-ensemble.sh\nEOF",
    "cat scripts/stop-ensemble.sh | bash",
    "bash < scripts/stop-ensemble.sh",
    "source scripts/stop-ensemble.sh",
    ". scripts/stop-ensemble.sh",
    "find /tmp -name '*.sh' -exec ./scripts/stop-ensemble.sh {} \\;",
    "xargs bash scripts/stop-ensemble.sh",
    "eval 'bash scripts/stop-ensemble.sh'",
    "echo $(bash scripts/stop-ensemble.sh)",
    'W=scripts/stop-ensemble.sh; bash "$W"',
    'ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:"$U" bash scripts/stop-ensemble.sh',
]


@pytest.mark.parametrize("command", _WRAPPER_SHAPES)
def test_unit_stop_wrapper_refused_any_invocation(command):
    assert guard.systemctl_violation(command) == guard.WRAPPER_VIOLATION_TOKEN


def test_wrapper_table_exported_and_exact():
    """The deny is a basename EQUALITY on the exact wrapper — not a substring
    over the command text (that would re-create the over-block)."""
    assert guard.PROTECTED_WRAPPER_NAMES == ("stop-ensemble.sh",)
    assert guard.SUPERVISION_MARKER_VAR == "ENSEMBLE_SUPERVISION_RESULT"
    assert guard.SUPERVISION_MANAGED_PREFIX == "UNIT_MANAGED:"
    assert guard.systemctl_violation("bash scripts/stop-ensemble.sh.bak") is None


# ---------------------------------------------------------------------------
# DENY leg 5 — env-primed (``ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:*``)
# script invocation, i.e. a COPY of the wrapper under another name/path.
#
# Counter-mutation M5b: make ``_segment_has_supervision_marker`` return False
# unconditionally ⇒ every pin here returns None and REDDENs.
# ---------------------------------------------------------------------------

_PRIMED_SHAPES = [
    "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-main bash /tmp/other-stop.sh",
    "export ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-main; bash /tmp/other-stop.sh",
    'ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:"$U" bash ./scripts/copy-stop.sh',
    "env ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-main bash /tmp/x.sh",
    "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-main bash -c './stop-copy.sh'",
    'ENSEMBLE_SUPERVISION_RESULT="UNIT_MANAGED:ensemble-main" bash scripts/copy.sh',
]


@pytest.mark.parametrize("command", _PRIMED_SHAPES)
def test_supervision_primed_script_refused(command):
    assert guard.systemctl_violation(command) == guard.SUPERVISION_PRIMED_TOKEN


# ---------------------------------------------------------------------------
# DENY leg 6 — command-word resolution must not be hijacked by an
# UNRESOLVABLE leading assignment (``X="$1" systemctl ...`` used to make the
# whole segment escape analysis — the literal unit token behind it was
# missed). Counter-mutation: revert the assignment-shaped skip in
# ``_command_word_index`` ⇒ both pins return None.
# ---------------------------------------------------------------------------


def test_unresolved_assignment_prefix_does_not_hide_command():
    assert guard.systemctl_violation('X="$1" systemctl restart ensemble-main') == "ensemble-main"
    assert guard.systemctl_violation('UNIT=$(cat f) systemctl stop ensemble-daemon') == "ensemble-daemon"


def test_sudo_option_peel_resolves_command_word():
    """``sudo -n systemctl restart <unit>`` used to resolve its command word
    to ``-n`` and slip through. Counter-mutation: drop the sudo/doas
    option-peel arm in ``_command_word_index`` ⇒ returns None."""
    assert guard.systemctl_violation("sudo -n systemctl restart ensemble-main") == "ensemble-main"
    assert guard.systemctl_violation("sudo -u root systemctl stop ensemble-daemon") == "ensemble-daemon"


# ---------------------------------------------------------------------------
# ALLOW leg — the wrapper deny is SCOPED: reads, prose, and similar-but-
# different script names stay allowed.
#
# Counter-mutation: reimplement the wrapper deny as a raw substring over the
# command text (``if "stop-ensemble.sh" in text``) ⇒ the read/prose pins
# here refuse and REDDEN.
# ---------------------------------------------------------------------------

_WRAPPER_ALLOW = [
    "cat scripts/stop-ensemble.sh",
    'grep -n "stop-ensemble.sh" docs/ops.md',
    "git log --oneline -- scripts/stop-ensemble.sh",
    "bash scripts/stop-all.sh",
    "bash scripts/stop-ensemble.sh.bak",
    "bash scripts/stop-ensemble2.sh",
    "bash scripts/restart-ensemble-helper.sh",
    "ls scripts/ | grep stop",
    "echo 'bash scripts/stop-ensemble.sh is the operator stop path'",
    "cat > notes.md <<'EOF'\nrun bash scripts/stop-ensemble.sh to stop\nEOF",
    'grep -rn "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED" scripts/',
    "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-main echo ok",
    "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-main systemctl status ensemble-main",
    "bash tests/helpers/my-helper.sh --flag",
    "python scripts/backfill_snapshot_agent_space.py --dry",
]


@pytest.mark.parametrize("command", _WRAPPER_ALLOW)
def test_wrapper_deny_scoped_no_overblock(command):
    assert guard.systemctl_violation(command) is None


# ---------------------------------------------------------------------------
# SEAM — bash lane + proc lane both refuse the wrapper shapes
# ---------------------------------------------------------------------------


def test_bash_seam_refuses_wrapper_allows_read():
    assert _pkill_overlay_violation("bash scripts/stop-ensemble.sh") == guard.WRAPPER_VIOLATION_TOKEN
    assert _pkill_overlay_violation("bash scripts/stop-ensemble.sh --force") == guard.WRAPPER_VIOLATION_TOKEN
    assert _pkill_overlay_violation("cat scripts/stop-ensemble.sh") is None


async def test_proc_lane_refuses_wrapper_and_primed():
    pr = _proc_run()
    for command in (
        "bash scripts/stop-ensemble.sh",
        "./scripts/stop-ensemble.sh",
        "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-main bash /tmp/other-stop.sh",
    ):
        text = await pr.coroutine(command=command)
        assert text.startswith(
            "Error: refused by the rogue-containment pkill overlay guard"
        ), (command, text)


async def test_proc_lane_allows_wrapper_read_and_readonly():
    pr = _proc_run()
    for command in ("cat scripts/stop-ensemble.sh", "systemctl status ensemble-main"):
        text = await pr.coroutine(command=command, timeout=-1)
        assert "timeout must be >= 0" in text, (command, text)
        assert "refused by the rogue-containment" not in text, (command, text)


# ===========================================================================
# RUNNER / PIPE / SCHEDULER LAUNDERING (2026-11-18 reviewer round)
# ===========================================================================
# The pre-2026-11-18 TEXT guard refused any command text carrying
# ("systemctl" AND a mutating verb AND an ensemble-family token). The
# structural matcher replaced that text scan and LOST the
# literal-through-runner class: a payload handed to an executor is DATA to
# the producer and never reached the command-word analysis. The reviewer
# confirmed 12 such bypasses (all allowed by the merged guard, all refused
# by the old text guard).
#
# The fix keeps the old guard's conjunction verbatim as the SHAPE test and
# adds a DELIVERY test (the payload must actually reach an executor), plus
# runner awareness:
#   * PIPE  — producer | executor (stdin-reading shell / scheduler / xargs
#             / ssh) with the mutating-ensemble shape in the payload;
#   * RUNNER — su -c / runuser -c / ssh host '<cmd>' (command string),
#             systemd-run / watch (argv), xargs (operands from stdin ⇒ a
#             non-readonly tail with no operand or a {} placeholder FAILS
#             CLOSED);
#   * SCHEDULER — at / batch / crontab (they RUN what they are handed).
#
# Counter-mutations (each leg REDDENS its own family):
#   * M6a — neutralise the shape matcher ⇒ pipe/scheduler/executed-context
#     deny pins RED;
#   * M6b — neutralise the runner branches ⇒ runner deny pins RED;
#   * M6c — drop the xargs fail-closed operand rule ⇒ xargs deny pins RED;
#   * M6d — make the laundering rule refuse UNCONDITIONALLY (over-broad)
#     ⇒ the read-only-through-runner ALLOW pins RED.
# ===========================================================================


_PIPE_LAUNDER_SHAPES = [
    "echo 'systemctl restart ensemble-main' | bash",
    "printf 'systemctl restart ensemble-main' | sh",
    "cat <<EOF | bash\nsystemctl restart ensemble-main\nEOF",
    "echo 'systemctl stop ensemble-prod' | dash",
    "echo 'systemctl kill ensemble-main' | ssh host 'sh'",
    "echo 'systemctl restart ensemble-main' | bash -c 'cat'",
    # The segment that PRODUCES a pipe-fed payload is not always the
    # segment that CARRIES it: a subshell, a brace group and every
    # intermediate forwarding stage move the payload away from the
    # executor's neighbour, so the SHAPE test is applied to the WHOLE
    # stripped text of a command that pipes into an executor.
    "(echo 'systemctl restart ensemble-main') | bash",
    "{ echo 'systemctl restart ensemble-main'; } | bash",
    "echo 'systemctl restart ensemble-main' | cat | bash",
    "echo 'systemctl restart ensemble-main' | tee /tmp/x.log | bash",
    "echo 'systemctl restart ensemble-main' | grep ensemble-main | bash",
    "echo 'systemctl restart ensemble-main' | cat | tee /tmp/x.log | bash",
    # the executor need not be a shell
    "echo 'systemctl restart ensemble-main' | cat | at now",
]


@pytest.mark.parametrize("command", _PIPE_LAUNDER_SHAPES)
def test_pipe_into_executor_refused(command):
    """The payload is DATA to the producer and is EXECUTED by the consumer."""
    assert guard.systemctl_violation(command) is not None


_XARGS_STDIN_SHAPES = [
    "echo ensemble-main.service | xargs systemctl restart",
    "list-unit-files | grep ensemble-main | xargs -r systemctl restart",
    "echo ensemble-main | xargs -I{} systemctl restart {}",
    "echo ensemble-main | xargs systemctl stop",
]


@pytest.mark.parametrize("command", _XARGS_STDIN_SHAPES)
def test_xargs_stdin_operands_fail_closed(command):
    """xargs takes its operands from STDIN: the target is not statically
    known ⇒ a non-readonly systemctl tail FAILS CLOSED."""
    assert guard.systemctl_violation(command) == guard.UNRESOLVED_UNIT_TOKEN


_RUNNER_SHAPES = [
    "su -c 'systemctl restart ensemble-main'",
    "su root -c 'systemctl restart ensemble-main'",
    'runuser -u root -c "systemctl restart ensemble-main"',
    "systemd-run systemctl restart ensemble-main",
    "systemd-run --unit=foo systemctl restart ensemble-main",
    "watch systemctl restart ensemble-main",
    "watch -n 5 systemctl restart ensemble-main",
    "ssh localhost 'systemctl restart ensemble-main'",
    "ssh -p 2222 localhost 'systemctl restart ensemble-main'",
    "bash -c \"$(echo 'systemctl restart ensemble-main')\"",
    # A runner option that CONSUMES the next word (the systemd-run
    # timer options) must not hide the tail: the value is skipped and
    # the command behind it is analysed.
    'systemd-run --on-calendar "12:00" systemctl restart ensemble-main',
    "systemd-run --on-boot 60 systemctl restart ensemble-main",
    "systemd-run --on-startup 30 systemctl restart ensemble-main",
    "systemd-run --on-unit-active 30 systemctl restart ensemble-main",
    "systemd-run --on-unit-inactive 30 systemctl restart ensemble-main",
    "systemd-run --timer-property AccuracySec=1s systemctl restart ensemble-main",
    # the `=`-forms are one self-contained token and keep working
    "systemd-run --on-calendar=12:00 systemctl restart ensemble-main",
    # an option that takes NO value must not swallow the command
    "systemd-run --on-clock-change systemctl restart ensemble-main",
    "systemd-run --on-timezone-change systemctl restart ensemble-main",
    "systemd-run -P systemctl restart ensemble-main",
    "systemd-run -t systemctl restart ensemble-main",
    "watch -p systemctl restart ensemble-main",
    "watch -t systemctl restart ensemble-main",
    "watch -q 5 systemctl restart ensemble-main",
    # the timer-option value must not be able to swallow the command
    # that FOLLOWS it either (a second option after the value used to
    # anchor the tail analysis on the option, not on the command)
    'systemd-run --on-calendar "12:00" --unit foo systemctl restart ensemble-main',
    "systemd-run --timer-property AccuracySec=1s --unit foo systemctl restart ensemble-main",
    # the plain value options of the runner are consumed as well
    "systemd-run --description plain systemctl restart ensemble-main",
    "systemd-run --working-directory /tmp systemctl restart ensemble-main",
    "systemd-run --uid root systemctl restart ensemble-main",
]


@pytest.mark.parametrize("command", _RUNNER_SHAPES)
def test_runner_shapes_refused(command):
    """su/runuser carry a command string, systemd-run/watch an argv, ssh a
    remote command string — none of them is an escape."""
    assert guard.systemctl_violation(command) is not None


_SCHEDULER_SHAPES = [
    "echo 'systemctl restart ensemble-main' | at now",
    "echo 'systemctl restart ensemble-main' | batch",
    "echo '0 5 * * * systemctl restart ensemble-main' | crontab -",
]


@pytest.mark.parametrize("command", _SCHEDULER_SHAPES)
def test_scheduler_handoff_refused(command):
    """at/batch/crontab RUN what they are handed — the payload is a command
    (a crontab line carries the shape, not a bare command word)."""
    assert guard.systemctl_violation(command) is not None


def test_shape_matcher_matches_the_old_text_guard_conjunction():
    """The shape test is the OLD text guard's conjunction (its verb set and
    token set, same substring semantics), widened with the verbs the
    STRUCTURAL matcher refuses on its own — so a piped shape is never
    weaker than the same verb in command position.
    Counter-mutation: drop the verb arm ⇒ the read-only leg reddens."""
    assert guard._text_has_mutating_ensemble_shape(
        "systemctl restart ensemble-main"
    )
    assert guard._text_has_mutating_ensemble_shape(
        "0 5 * * * systemctl stop ensemble-prod.service"
    )
    # a NEW verb the structural matcher refuses on its own (unknown ⇒ fail
    # closed) is refused in the piped shape too
    assert guard._text_has_mutating_ensemble_shape(
        "systemctl freeze ensemble-main"
    )
    assert not guard._text_has_mutating_ensemble_shape(
        "systemctl status ensemble-main"
    )
    assert not guard._text_has_mutating_ensemble_shape(
        "systemctl restart nginx"
    )
    assert not guard._text_has_mutating_ensemble_shape(
        "grep restart ensemble-main docs"
    )


_LAUNDER_ALLOW = [
    # read-only through every runner must stay green
    "echo 'systemctl status ensemble-main' | bash",
    "watch systemctl status ensemble-main",
    "ssh localhost 'systemctl show ensemble-main'",
    "ssh -p 2222 localhost 'systemctl show ensemble-main'",
    "systemd-run systemctl status ensemble-main",
    "su -c 'systemctl status ensemble-main'",
    'runuser -u root -c "systemctl is-active ensemble-main"',
    "xargs systemctl status",
    "xargs echo hello",
    "ssh localhost",
    # a payload that only lands in data stays allowed
    "echo 'systemctl restart nginx' | bash",
    "echo 'systemctl restart ensemble-main' | grep -n restart",
    "printf '%s\\n' 'systemctl stop ensemble-prod' > /tmp/reminder.txt",
    "grep -n 'systemctl restart ensemble-main' docs/ops.md",
    "echo 'do not systemctl stop ensemble-main'",
    "cat > notes.md <<'EOF'\nrun systemctl restart ensemble-main to recover\nEOF",
    "journalctl -u ensemble-main -n 20 | grep -i enable",
    "systemctl status ensemble-main | grep ensemble",
    # read-only through a runner option that CONSUMES the next word
    'systemd-run --on-calendar "12:00" systemctl status ensemble-main',
    "systemd-run --on-calendar=12:00 systemctl status ensemble-main",
    "systemd-run --on-boot 60 systemctl status ensemble-main",
    "systemd-run --on-unit-inactive 30 systemctl status ensemble-main",
    "systemd-run --timer-property AccuracySec=1s systemctl status ensemble-main",
    "systemd-run --description plain systemctl status ensemble-main",
    "watch -n 5 systemctl status ensemble-main",
    "watch -q 5 systemctl status ensemble-main",
]


@pytest.mark.parametrize("command", _LAUNDER_ALLOW)
def test_laundering_deny_is_scoped(command):
    """The laundering deny must not swallow read-only inspection or prose."""
    assert guard.systemctl_violation(command) is None


def test_bash_seam_refuses_laundering_shapes():
    assert _pkill_overlay_violation(
        "echo 'systemctl restart ensemble-main' | bash"
    ) is not None
    assert _pkill_overlay_violation("su -c 'systemctl restart ensemble-main'") is not None
    assert _pkill_overlay_violation("ssh localhost 'systemctl restart ensemble-main'") is not None
    assert _pkill_overlay_violation("echo 'systemctl status ensemble-main' | bash") is None
    assert _pkill_overlay_violation("watch systemctl status ensemble-main") is None


async def test_proc_lane_refuses_laundering_shapes():
    pr = _proc_run()
    for command in (
        "echo 'systemctl restart ensemble-main' | bash",
        "su -c 'systemctl restart ensemble-main'",
        "echo 'systemctl restart ensemble-main' | at now",
    ):
        text = await pr.coroutine(command=command)
        assert text.startswith(
            "Error: refused by the rogue-containment pkill overlay guard"
        ), (command, text)


async def test_proc_lane_allows_readonly_through_runner():
    pr = _proc_run()
    for command in (
        "echo 'systemctl status ensemble-main' | bash",
        "ssh localhost 'systemctl show ensemble-main'",
    ):
        text = await pr.coroutine(command=command, timeout=-1)
        assert "timeout must be >= 0" in text, (command, text)
        assert "refused by the rogue-containment" not in text, (command, text)


# ===========================================================================
# wave-3 (2026-11-19) — the guard-inversion CLOSING fixes
#
#   * the pipe/executor laundering rule applies the SHAPE test to the WHOLE
#     stripped text: the segment that PRODUCES a pipe-fed payload is not
#     always the segment that CARRIES it (a subshell, a brace group, an
#     intermediate forwarding stage), so an adjacency-scoped test left real
#     laundering shapes ALLOWED;
#   * a runner option that CONSUMES the next word (the systemd-run timer
#     options) no longer hides the tail behind the option's value;
#   * a systemctl option that takes NO value no longer eats the verb.
#
# Counter-mutations:
#   * M6g — revert the whole-text pipe rule to adjacency-scoped payload
#     analysis ⇒ the pipe-shape deny pins RED;
#   * M6h — drop the runner option value-consumption ⇒ the runner-shape
#     deny pins RED.
# ===========================================================================

_SYSTEMCTL_OPTION_ANCHOR_SHAPES = [
    # ``--mkdir`` takes NO value (it is a flag): treating it as
    # value-consuming made the tail analysis anchor on the VALUE, so the
    # VERB was eaten and the unit became the verb. The command was
    # ALLOWED although ``systemctl --mkdir restart <unit>`` really does
    # restart the unit.
    "systemctl --mkdir restart ensemble-main",
    "systemctl --mkdir stop ensemble-main",
    # the value-consuming options still consume their value, so the verb
    # is found and the ensemble unit is refused
    "systemctl --host myhost restart ensemble-main",
    "systemctl --machine mycontainer restart ensemble-main",
    "systemctl --root /mnt restart ensemble-main",
    "systemctl --job-mode replace restart ensemble-main",
]


@pytest.mark.parametrize("command", _SYSTEMCTL_OPTION_ANCHOR_SHAPES)
def test_systemctl_option_anchor_shapes_refused(command):
    """A ``systemctl`` option must not be able to eat the verb."""
    assert guard.systemctl_violation(command) is not None


def test_property_name_colliding_with_a_mutating_verb():
    """The disclosed SHAPE residual — the EXECUTED context.

    A property name that COLLIDES with a mutating verb (``-p Restart``)
    is REFUSED once the command is EXECUTED: the shape test sees the
    verb. The DIRECT context stays ALLOWED — that over-block is the one
    this guard deliberately dropped, and it is pinned as allowed here so
    the two contexts cannot drift apart silently.
    """
    assert guard.systemctl_violation("systemctl show ensemble-main -p Restart") is None
    assert guard.systemctl_violation(
        "systemctl show ensemble-main --property=Restart"
    ) is None
    for command in (
        "ssh localhost 'systemctl show ensemble-main -p Restart'",
        "su -c 'systemctl show ensemble-main -p Restart'",
        'bash -c "systemctl show ensemble-main -p Restart"',
        "echo 'systemctl show ensemble-main -p Restart' | bash",
    ):
        assert guard.systemctl_violation(command) is not None, command
