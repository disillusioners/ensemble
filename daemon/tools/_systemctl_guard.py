"""Shared, pure intent guard for agent-issued ``systemctl`` / kill shapes.

WHY THIS MODULE EXISTS (2026-11-18 triple outage)
-------------------------------------------------
``daemon/tools/bash.py`` classified ``systemctl`` correctly at the *verb*
level but matched the live-unit family with a plain SUBSTRING test over the
raw command text. Two consequences, both observed in production:

* **UNDER-BLOCK (caused three live outages today).** A variable-indirected or
  composed unit token never produces the literal substring, so
  ``UNIT=ensemble-main.service; systemctl restart "$UNIT"`` and
  ``S=ensemble; systemctl restart ${S}-main`` sailed through the guard.
  The only reason the first shape was ever refused is that the *assignment
  text* happened to contain ``ensemble-main`` — an accident, not a control.
* **OVER-BLOCK.** Because the verb scan ran over the entire command text (not
  over execution positions), non-executing prose was refused: a heredoc that
  merely *documents* ``systemctl restart ensemble-main``, a ``grep`` for that
  string, a ``printf`` of it. Read-only inspection also tripped the same
  text scan whenever a *property name* was a mutating-verb word
  (``systemctl show ensemble-main -p Restart`` => refused).

FIX SHAPE
---------
Intent is decided over **execution positions**, not over raw text:

1. The command string is lexed into simple-command segments. Heredoc bodies
   are DATA unless the heredoc is the stdin of a shell interpreter. Single
   quotes are data. Double quotes are data *except* ``$(...)`` / backticks,
   which execute and are recursed into.
2. Same-string variable assignments (``NAME=value``) are collected and
   resolved statically, including quoted composition (``"$A"-main``),
   ``${A}-main`` and trivial substitutions (``$(echo ensemble-main)``).
3. For a ``systemctl`` segment: read-only verbs are ALLOWED against any unit
   **unless a mutating verb appears LATER in the same command, followed by
   an ensemble unit or by an operand that cannot be resolved statically** —
   a read-only verb does not end the analysis, because a mis-parsed option
   arity can leave the option's VALUE in the verb slot with the real
   command still in the tail (``systemctl -P cat stop <unit>``, and the
   option-free spelling ``systemctl cat stop <unit>``). The unit operand of
   the read-only verb itself stays allowed. Any other verb (mutating, or
   unknown => fail-closed) whose unit operand resolves into the ensemble's
   own unit family is REFUSED. An operand that cannot be resolved
   statically (indirected from the environment, globbed, produced by a
   non-trivial substitution) is REFUSED (fail-closed).
4. ``pkill`` / ``killall`` / ``fuser`` / ``kill`` / ``xargs kill`` keep the
   original text-scoped semantics unchanged (defence in depth; a quoted
   target such as ``pkill -f 'ensemble-prod'`` must stay refused).
5. Wrappers (``sudo``, ``env``, ``nohup``, ...), ``bash -c``/``sh -c``,
   ``eval`` and ``xargs <cmd>`` are peeled/recursed so the shapes cannot be
   laundered through a runner.
6. The repo's own unit-stop WRAPPER (``scripts/stop-ensemble.sh``) is REFUSED
   in any execution position, however invoked (see below). The wrapper hides
   the destructive verb entirely: its ``UNIT_MANAGED`` branch runs
   ``systemctl stop "$SU_UNIT"`` with the unit composed from
   ``ENSEMBLE_SUPERVISION_RESULT``, so the agent's command text
   (``bash scripts/stop-ensemble.sh``) carries neither a ``systemctl`` token
   nor a live-unit token and the structural matcher above never sees it.

CONTRACT NOTES (the rules above as the code actually enforces them)
------------------------------------------------------------------
* TAIL SCAN (rule 3) is POSITIONAL: a mutating verb followed by an ensemble
  unit, or by an unresolvable / globbed operand. ``systemctl help <unit>
  stop`` is therefore ALLOWED while ``systemctl cat stop <unit>`` is
  REFUSED — the unit must FOLLOW the mutating verb. That asymmetry is
  disclosed, not a defect: dropping the positional requirement would
  blanket-scan every remaining token and re-open the property-name
  over-block.
* FAIL-CLOSED ON UNKNOWN OPTIONS, PER TOOL. The argv runners
  (``systemd-run``, ``watch``), ``xargs`` and ``ssh`` carry an explicit
  BOOLEAN set beside their VALUE set (see the OPTION GRAMMARS map); an
  option in NEITHER is one the tool does not have, its arity is unknown,
  and the command is REFUSED. ``systemctl``, ``env``, ``sudo``/``doas``
  and the four wrapper-arg tools skip an unknown option one word: for
  ``systemctl`` the class is closed by the tail scan plus the operand scan;
  for the wrappers it is the residual disclosed in PACKS.
* EXECUTED-CONTEXT SHAPE OVER-BLOCK. A command string a nested interpreter
  will RUN (``bash -c``, ``eval``, ``su -c``, ``ssh '<remote>'``,
  ``env -S``, a shell's or scheduler's heredoc stdin) is refused whenever
  its TEXT carries the mutating-ensemble shape — even when part of that
  text is only data to an inner ``echo``. That is the old text guard's
  strength, kept deliberately for executed contexts.
* BARE ``ssh host`` IS AN EXECUTOR. With no remote command ssh starts a
  remote login shell and FORWARDS the client's stdin, so a piped payload
  is delivered.
* SHELL ATTACHED ``-c`` NARROWING (disclosed). ``bash -c'payload'`` (no
  space) is ALLOWED: no shell installed on this host executes the attached
  form — bash/sh/zsh reject it (fish/ksh/mksh are not installed and are
  unverifiable). The OLD text guard refused it by text scan. It is not a
  bypass here; it is disclosed rather than closed. ``su``/``runuser`` DO
  accept the attached form and are analysed.
* TWO CLASSES THE OLD GUARD REFUSED ARE DELIBERATELY ALLOWED: the PROSE
  over-block (a heredoc, ``grep`` or ``printf`` that merely documents a
  mutating command, with no pipe into an executor) and the DIRECT-context
  property-name over-block (``systemctl show <unit> -p Restart``).

UNIT-STOP WRAPPER (2026-11-18 follow-up — the ACTUAL kill mechanism)
--------------------------------------------------------------------
The three outages were not raw ``systemctl`` calls: the agent ran
``bash scripts/stop-ensemble.sh``, whose unit-owned branch composes the unit
from ``ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:<unit>`` and issues
``systemctl stop``/``systemctl kill`` itself (``/tmp/.ensemble-stop-sc.*``
sentinel mtimes match the three journald stop events sub-second). Two rules:

* **Wrapper execution is refused by basename** wherever the segment EXECUTES
  it: direct command word (``./scripts/stop-ensemble.sh``, an absolute path, a
  bare ``stop-ensemble.sh`` resolved via PATH), as the script argument of a
  shell interpreter (``bash|sh|dash <path>``, including after ``env``/``sudo``
  /``nohup``/``timeout`` prefixes), via ``source``/``.``, ``find -exec``, and
  ``bash < <path>`` / ``cat <path> | bash``. ANY arguments and ANY environment
  are refused — ``DRY_RUN=1`` included: no agent-lane use of this wrapper is
  sanctioned (the supervision/upgrade machinery invokes it directly, never
  through the bash/proc tool lane).
* **Env-primed scripts are refused generally**: a segment that BOTH invokes a
  script path AND carries (or inherits, prefix/``export``/``env``) an
  ``ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:*`` assignment is refused, even
  for a copy of the wrapper under another name.

Read-only inspection of the file stays allowed (``cat``/``grep``/``ls`` with
no shell execution). Similar-but-different script names (``stop-all.sh``,
``stop-ensemble.sh.bak``) stay allowed — the deny is a basename equality on
the exact wrapper, not a substring.

RUNNER / PIPE / SCHEDULER LAUNDERING (2026-11-18 reviewer round)
------------------------------------------------------------------
The pre-2026-11-18 TEXT guard refused any command text that carried
("systemctl" AND a mutating verb AND an ensemble-family token). The
structural matcher replaced that text scan and, in doing so, LOST the
literal-through-runner class: a payload handed to an executor is DATA to
the producer and never reached the command-word analysis. The reviewer
confirmed 12 such bypasses (allowed by the merged guard, refused by the old
text guard).

The coverage is restored WITHOUT re-introducing the prose over-block, by
splitting the old conjunction into two halves:

* **SHAPE** — the payload text carries ("systemctl" AND a mutating verb AND
  an ensemble-family token). This is the OLD guard's conjunction (same token
  set, same substring semantics), with its verb set WIDENED by the verbs the
  structural matcher refuses on its own (unknown verbs fail closed against
  an ensemble unit) — so a piped shape is never weaker than the same verb in
  command position.
* **DELIVERY** — the payload actually REACHES an executor:
  * a PIPE into a stdin-reading shell, a scheduler (``at``/``batch``/
    ``crontab`` — they RUN what they are handed), ``xargs`` (stdin becomes
    the argument vector) or ``ssh`` (stdin is forwarded to the remote
    command);
  * a nested COMMAND STRING (``bash -c``, ``eval``, a shell's heredoc-stdin)
    whose text is itself the executed command.

A payload that only lands in a file, a variable or a grep pattern is DATA
and stays allowed — the prose/doc-write pins depend on that.

Runner awareness was added in the same round, because the runner is where
the payload stops being visible:

* ``su -c '<cmd>'`` / ``runuser -u root -c '<cmd>'`` / ``ssh host '<cmd>'``
  carry a COMMAND STRING (recursed into);
* ``systemd-run <cmd> ...`` / ``watch <cmd> ...`` carry an ARGV (analysed
  as a command segment);
* ``xargs <cmd>`` takes its OPERANDS FROM STDIN — a non-readonly systemctl
  tail with no operand, or a ``{}`` placeholder, has an unknown target and
  FAILS CLOSED.

RESIDUAL (stated, not hidden): a payload the runner reads at RUN time from
a FILE (``bash -c "$(cat script.sh)"``) is invisible to static analysis —
the guard sees the substitution, never the file's content.

Everything here is a PURE function: no I/O, no process state, no environment
reads. The ambient-only fence (``ENSEMBLE_FENCED_TEST_CONTEXT``) is read by
the callers (``daemon/tools/bash.py``; ``proc_run`` reaches the same wrapper
through its existing delegation) so the fence channel stays exactly where it
was (fix-round 2 / B6 — the agent-settable tool-call env overlay is ignored).
"""

from __future__ import annotations

import re
from typing import Sequence

__all__ = [
    "LAUNDERED_TOKEN",
    "LIVE_NAME_PATTERNS",
    "MEANINGFUL_PATH_PATTERNS",
    "PROTECTED_WRAPPER_NAMES",
    "SUPERVISION_MARKER_VAR",
    "SUPERVISION_MANAGED_PREFIX",
    "SUPERVISION_PRIMED_TOKEN",
    "SYSTEMCTL_MUTATING",
    "SYSTEMCTL_READONLY",
    "UNRESOLVED_UNIT_TOKEN",
    "WRAPPER_VIOLATION_TOKEN",
    "is_kill_like",
    "kill_leg_violation",
    "overlay_violation",
    "systemctl_violation",
]

#: The ensemble's own unit / install-root name family (unchanged from the
#: original overlay table). The ``ens`` prefix joins it for composed operand
#: shapes (``ENS-"main"``): a unit token composed of our short stem is not
#: "clearly non-ensemble", so it is treated as ours (refuse).
LIVE_NAME_PATTERNS: tuple[str, ...] = (
    "ensemble-prod",
    "ensemble-daemon",
    "ensemble-main",
    "ensemble-demo",
    "agents-ensemble",
)

#: Port tokens stay whole-word (``19797`` must not match).
MEANINGFUL_PATH_PATTERNS: tuple[tuple[str, str], ...] = (
    ("9797", r"\b9797\b"),
    ("9798", r"\b9798\b"),
    ("7979", r"\b7979\b"),
)

#: Read-only ``systemctl`` verbs — ALLOWED against any unit, including the
#: ensemble's own (the outage-fixing operator needs this).
SYSTEMCTL_READONLY: frozenset[str] = frozenset({
    "status",
    "show",
    "is-active",
    "is-enabled",
    "is-failed",
    "is-system-running",
    "cat",
    "list-units",
    "list-unit-files",
    "list-sockets",
    "list-timers",
    "list-jobs",
    "list-machines",
    "list-dependencies",
    "list-automounts",
    "list-paths",
    "get-default",
    "show-environment",
    "help",
})

#: Mutating ``systemctl`` verbs (the contract's set, unchanged).
SYSTEMCTL_MUTATING: frozenset[str] = frozenset({
    "stop",
    "restart",
    "kill",
    "disable",
    "mask",
    "reload",
    "daemon-reload",
    "enable",
    "start",
    "try-restart",
    "isolate",
})

#: The FULL mutating-verb set — ONE source of truth. Every verb below is a
#: real mutating ``systemctl`` verb on this host (verified against
#: ``systemctl --help``, systemd 255, 2026-11-19). ``add``/``remove`` are
#: kept from the pre-inversion text guard so the shape test is never weaker
#: than it was. The structural tail scan uses this set directly; the shape
#: REGEX is DERIVED from it at import so the two can never diverge again —
#: they diverged by six verbs once and a piped shape was weaker than the
#: direct form, which is the one contract this module must never break.
_SYSTEMCTL_TAIL_MUTATING: frozenset[str] = SYSTEMCTL_MUTATING | frozenset({
    "clean", "freeze", "thaw", "preset", "preset-all", "revert", "link",
    "edit", "import", "set-property", "unset-property", "add", "remove",
    "reload-or-restart", "try-reload-or-restart", "reenable",
    # verified on this host's systemd 255 (2026-11-19)
    "unmask", "reset-failed", "daemon-reexec", "set-environment",
    "unset-environment", "add-wants", "add-requires",
})

#: Returned when an operand/verb could not be resolved statically. Any
#: non-None return from this module means REFUSE; this token makes the
#: fail-closed branch legible in refusals and tests.
UNRESOLVED_UNIT_TOKEN = "unresolved-unit"

#: The agent-lane-refused unit-stop wrapper. The supervision/upgrade
#: machinery (``scripts/upgrade/lib.sh`` ``stop_via_stop_script``) invokes
#: this script DIRECTLY; an agent that runs it through the bash/proc tool
#: lane is performing a unit stop. Matched by BASENAME EQUALITY — copies in
#: any directory are the same risk (the outage ran it from the repo root);
#: similar-but-different names stay allowed.
PROTECTED_WRAPPER_NAMES: tuple[str, ...] = ("stop-ensemble.sh",)

#: Refusal token for rule 1 (wrapper execution).
WRAPPER_VIOLATION_TOKEN = "unit-stop-wrapper"

#: The supervision classification env var that drives the wrapper's
#: unit-owned ``systemctl stop`` branch (frozen in ``scripts/upgrade/lib.sh``).
SUPERVISION_MARKER_VAR = "ENSEMBLE_SUPERVISION_RESULT"

#: The VALUE prefix that selects the unit-owned branch.
SUPERVISION_MANAGED_PREFIX = "UNIT_MANAGED:"

#: Refusal token for rule 2 (env-primed script invocation).
SUPERVISION_PRIMED_TOKEN = "supervision-primed-script"

# ===========================================================================
# OPTION GRAMMARS — one analyser, one table per tool
# ===========================================================================
# MAP (every option table in this module, its tool, and its failure mode):
#   _OPTIONS_WITH_VALUE + _SYSTEMCTL_FLAG_OPTIONS   systemctl   skip-unknown*
#   _SYSTEMD_RUN_VALUE_OPTIONS + _FLAGS             systemd-run FAIL-CLOSED
#   _WATCH_VALUE_OPTIONS + _FLAGS                   watch       FAIL-CLOSED
#   _XARGS_VALUE_OPTIONS + _FLAGS + attached        xargs       FAIL-CLOSED
#   _ENV_VALUE_OPTIONS + _FLAGS                     env         skip-unknown*
#   _SUDO_VALUE_OPTIONS + _FLAGS                    sudo/doas   skip-unknown*
#   _WRAPPER_ARG_GRAMMARS (timeout/nice/ionice/stdbuf)          skip-unknown*
#   _SSH_VALUE_OPTIONS + _FLAGS                     ssh         FAIL-CLOSED
#   _ARGV_RUNNER_VALUE_PREFIXES (+ _VALUE_FLAGS)    systemd-run timer options
# (* skip-unknown = an option in NEITHER set is skipped one word; the class
#   is closed elsewhere — for systemctl by the non-terminal read-only parse
#   plus the operand scan; for the wrappers by the residual disclosed in
#   PACKS. FAIL-CLOSED = the command is REFUSED, the arity being unknown.)
# Every table is verified against its tool's own ``--help`` ON THIS HOST and
# records the tool version and the date in its own comment.


def _short_letters(options: frozenset[str]) -> frozenset[str]:
    """The single-letter shorts of an option set (``-H`` becomes ``H``)."""
    return frozenset(
        o[1] for o in options
        if o.startswith("-") and not o.startswith("--") and len(o) == 2
    )


def _option_token(
    text: str,
    value_options: frozenset[str],
    flag_options: frozenset[str],
    attached_shorts: frozenset[str] = frozenset(),
    long_prefixes: tuple[str, ...] = (),
    long_prefix_flags: frozenset[str] = frozenset(),
) -> tuple[bool, bool, str | None]:
    """Normalise ONE option token of the tool whose tables are given.

    Returns ``(known, consumes_next, attached_value)``.

    * a LONG option: known when its name (up to any ``=``) is in either
      table; it consumes the next word only when it is a VALUE option in
      its space form (``--opt value``) — the ``--opt=value`` form is a
      single self-contained token;
    * a SHORT BUNDLE: ``-aP`` is ``-a`` followed by the value-taking ``-P``,
      so the bundle consumes the NEXT word; ``-S<value>``, ``-n1`` and
      ``-I{}`` carry the value INSIDE the token (a value-taking letter that
      is not last takes the rest of the token as its value);
    * an ATTACHED-ONLY short (``xargs -i/-l/-e``) never consumes the next
      word: its optional value rides inside the token;
    * a lone ``-`` (env's stdin marker) consumes nothing;
    * any letter in NEITHER table is UNKNOWN — the caller fails closed
      where that tool's scanner is fail-closed.
    """
    if not text.startswith("-"):
        return False, False, None
    if text.startswith("--"):
        name = text.split("=", 1)[0]
        if name in value_options:
            return True, "=" not in text, None
        if name in flag_options:
            return True, False, None
        if long_prefixes and name.startswith(long_prefixes):
            return True, ("=" not in text and name not in long_prefix_flags), None
        return False, False, None
    body = text[1:]
    if not body:
        return True, False, None
    value_shorts = _short_letters(value_options)
    flag_shorts = _short_letters(flag_options)
    for i, ch in enumerate(body):
        rest = body[i + 1:]
        if ch in value_shorts:
            if rest:
                return True, False, rest
            return True, True, None
        if ch in flag_shorts:
            if rest:
                continue
            return True, False, None
        if ch in attached_shorts:
            if rest:
                return True, False, rest
            return True, False, None
        return False, False, None


#: ``systemctl`` options that consume the following token as a value, so the
#: option's argument is not mistaken for the verb (``systemctl -t service
#: restart <unit>``).
_OPTIONS_WITH_VALUE: frozenset[str] = frozenset({
    "-H", "--host", "-M", "--machine", "-t", "--type", "--state",
    "-p", "--property", "-P", "-n", "--lines", "-o", "--output", "-s", "--signal",
    "--kill-whom", "--kill-value", "--root", "--image", "--job-mode",
    "--what", "--drop-in", "--message", "--preset-mode", "--when",
    "--timestamp", "--reboot-argument", "--check-inhibitors",
    # v255 value options that were missing from the table: with them absent
    # the parser lands on the option's VALUE and the real verb behind it is
    # never analysed (``systemctl -P cat stop <unit>``).
    "--image-policy", "--boot-loader-menu", "--boot-loader-entry",
    # ``--legend=BOOL`` takes a MANDATORY value on this host (the help shows
    # no ``[...]``): without it ``systemctl --legend yes status <unit>`` was
    # refused — a read-only command.
    "--legend",
})

#: The BOOLEAN (value-less) ``systemctl`` options, verified against
#: ``systemctl --help`` on this host (systemd 255, 2026-11-19). Back-pointer:
#: see the OPTION GRAMMARS map above — systemctl is a SKIP-UNKNOWN scanner.
_SYSTEMCTL_FLAG_OPTIONS: frozenset[str] = frozenset({
    "-a", "--all", "-f", "--force", "-h", "--help", "-i", "-l", "--full",
    "-q", "--quiet", "-r", "--recursive", "-T", "--show-transaction",
    "--dry-run", "--failed", "--firmware-setup", "--global", "--marked",
    "--mkdir", "--no-ask-password", "--no-block", "--no-pager", "--no-reload",
    "--no-wall", "--no-warn", "--now", "--plain", "--read-only", "--reverse",
    "--runtime", "--show-types", "--system", "--user", "--value", "--version",
    "--wait", "--with-dependencies",
})

#: Shell reserved words that can precede a command on the same line
#: (``if systemctl restart <unit>; then``). Skipped like wrappers so the real
#: command word is analysed.
_SHELL_KEYWORDS: frozenset[str] = frozenset({
    "if", "then", "else", "elif", "while", "until", "do", "done", "fi",
    "case", "esac", "in", "select", "time", "{", "}", "!", ":",
})

#: Prefix words that run another command; peeled before the command word.
_WRAPPER_WORDS: frozenset[str] = frozenset({
    "sudo", "doas", "env", "command", "exec", "nohup", "setsid", "stdbuf",
    "nice", "ionice", "time", "builtin", "busybox", "chronic", "unbuffer",
})

#: Wrapper words that consume one non-option argument (a duration / priority).
_WRAPPER_ARG_WORDS: frozenset[str] = frozenset({"timeout", "nice", "ionice", "stdbuf"})

#: Shell interpreters whose ``-c`` payload — and whose heredoc stdin — is
#: executed as commands rather than kept as data.
_SHELLS: frozenset[str] = frozenset({"bash", "sh", "zsh", "dash", "ksh", "ash", "fish", "mksh"})

#: Runners whose ``-c``/tail payload is a COMMAND STRING (interpreted by a
#: shell): ``su -c '<cmd>'``, ``runuser -u root -c '<cmd>'``.
_COMMAND_STRING_RUNNERS: frozenset[str] = frozenset({"su", "runuser"})

#: Runners whose tail (after their own options) is an ARGV — a command plus
#: its arguments: ``systemd-run <cmd> ...``, ``watch <cmd> ...``.
_ARGV_RUNNERS: frozenset[str] = frozenset({"systemd-run", "watch"})

#: ``xargs`` options that consume the following word as a value (the long
#: forms of the ones the table already carried, plus the mandatory-value
#: long options). The OPTIONAL-value long options (``--replace[=R]``,
#: ``--eof[=END]``) are deliberately absent: their ``=``-form is one token
#: and their space form does not take the next word.
_XARGS_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a",
    "--arg-file", "--delimiter", "--max-lines", "--max-args", "--max-procs",
    "--max-chars", "--process-slot-var",
})

#: The BOOLEAN (value-less) options of ``xargs``, verified against
#: ``xargs --help`` on this host (2026-11-19). The optional-value long
#: options (``--eof[=END]``, ``--replace[=R]``) and the attached ``-l`` are
#: here: their ``=``-form is one token and their space form takes nothing.
_XARGS_FLAG_OPTIONS: frozenset[str] = frozenset({
    "-0", "--null", "--eof", "--replace", "-o", "--open-tty",
    "-p", "--interactive", "-r", "--no-run-if-empty", "-t", "--verbose",
    "-x", "--exit", "--show-limits", "--help", "--version",
})

#: ``xargs`` shorts whose OPTIONAL value rides inside the token (GNU xargs
#: ``-i/--replace[=R]``, ``-l[MAX-LINES]``, ``-e/--eof[=END]``): ``-i{}``
#: and ``-l3`` take no following word.
_XARGS_ATTACHED_SHORTS: frozenset[str] = frozenset({"e", "i", "l"})

#: Schedulers that EXECUTE a command handed to them on stdin (``at now``,
#: ``batch``, ``crontab -``): the piped payload is a command that will run.
_SCHEDULERS: frozenset[str] = frozenset({"at", "batch", "crontab"})

#: ``ssh`` options that consume the following word as a value (so the value
#: is never mistaken for the host name).
_SSH_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l",
    "-m", "-O", "-o", "-p", "-Q", "-R", "-S", "-W", "-w",
})

#: Options of ``systemd-run`` that consume the following word as a value.
#: Verified against ``systemd-run --help`` on this host (2026-11-19): every
#: option listed there with a VALUE — ``--host=``, ``--machine=``,
#: ``--unit=``, ``--property=``, ``--description=``, ``--slice=``,
#: ``--expand-environment=``, ``--service-type=``, ``--uid=``, ``--gid=``,
#: ``--nice=``, ``--working-directory=``, ``--setenv=``,
#: ``--path-property=``, ``--socket-property=``. The TIMER options
#: (``--on-active=``, ``--on-boot=``, ``--on-startup=``,
#: ``--on-unit-active=``, ``--on-unit-inactive=``, ``--on-calendar=``,
#: ``--timer-property=``) are matched by PREFIX — see
#: ``_ARGV_RUNNER_VALUE_PREFIXES``.
#:
#: An option that takes a value and is treated as a FLAG makes the tail
#: analysis anchor on the VALUE: the real command (which follows it) is
#: then never analysed, and a dangerous tail slips through.
_SYSTEMD_RUN_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-H", "--host", "-M", "--machine", "-u", "--unit", "-p", "--property",
    "--description", "--slice", "--expand-environment", "--service-type",
    "--uid", "--gid", "--nice", "--working-directory", "-E", "--setenv",
    "--path-property", "--socket-property",
})

#: ``env`` options that consume the following word as a value (GNU coreutils
#: ``env --help``, 2026-11-19). ONE table for both scanning sites (the
#: command-word peel and the split-string scanner). The ``--*-signal[=SIG]``
#: family takes an OPTIONAL value (the ``=`` form only) so it consumes
#: nothing and sits in the flag table. Back-pointer: env is a SKIP-UNKNOWN
#: scanner — see the OPTION GRAMMARS map.
_ENV_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-u", "--unset", "-C", "--chdir", "-S", "--split-string",
})
_ENV_FLAG_OPTIONS: frozenset[str] = frozenset({
    "-i", "--ignore-environment", "-0", "--null", "-v", "--debug",
    "--help", "--version", "--list-signal-handling", "--block-signal",
    "--default-signal", "--ignore-signal",
})

#: ``sudo``/``doas`` options that consume the following word as a value
#: (``sudo --help``, sudo 1.9.x, 2026-11-19). ``-r``/``--role`` and
#: ``-t``/``--type`` were MISSING: ``sudo --role <value> systemctl restart
#: <unit>`` anchored the command word on the VALUE and NOPASSWD made the
#: bypass directly exploitable. ``--preserve-env`` takes a value only in its
#: ``=list`` form (one token). Back-pointer: SKIP-UNKNOWN scanner.
_SUDO_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
    "-C", "--close-from", "-T", "--command-timeout", "-R", "--chroot",
    "-D", "--chdir", "-U", "--other-user", "-r", "--role", "-t", "--type",
})
_SUDO_FLAG_OPTIONS: frozenset[str] = frozenset({
    "-A", "--askpass", "-b", "--background", "-B", "--bell",
    "-E", "--preserve-env", "-e", "--edit", "-H", "--set-home", "-i",
    "--login", "-K", "--remove-timestamp", "-k", "--reset-timestamp",
    "-l", "--list", "-n", "--non-interactive", "-P", "--preserve-groups",
    "-S", "--stdin", "-s", "--shell", "-V", "--version", "--help",
})

#: The four ``_WRAPPER_ARG_WORDS`` tools have FOUR different grammars — one
#: table each (``timeout``/``nice``/``ionice``/``stdbuf`` ``--help``, GNU
#: coreutils + util-linux, 2026-11-19). ``stdbuf -e <MODE>`` was the bypass:
#: the shared tuple knew ``-i``/``-o`` but not ``-e``. Back-pointer:
#: SKIP-UNKNOWN scanners.
_TIMEOUT_VALUE_OPTIONS: frozenset[str] = frozenset({"-k", "--kill-after", "-s", "--signal"})
_TIMEOUT_FLAG_OPTIONS: frozenset[str] = frozenset({"--preserve-status", "--foreground", "-v", "--verbose", "--help", "--version"})
_NICE_VALUE_OPTIONS: frozenset[str] = frozenset({"-n", "--adjustment"})
_NICE_FLAG_OPTIONS: frozenset[str] = frozenset({"--help", "--version"})
_IONICE_VALUE_OPTIONS: frozenset[str] = frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid", "-u", "--uid"})
_IONICE_FLAG_OPTIONS: frozenset[str] = frozenset({"-t", "--ignore", "-h", "--help", "-V", "--version"})
_STDBUF_VALUE_OPTIONS: frozenset[str] = frozenset({"-i", "--input", "-o", "--output", "-e", "--error"})
_STDBUF_FLAG_OPTIONS: frozenset[str] = frozenset({"--help", "--version"})
_WRAPPER_ARG_GRAMMARS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "timeout": (_TIMEOUT_VALUE_OPTIONS, _TIMEOUT_FLAG_OPTIONS),
    "nice": (_NICE_VALUE_OPTIONS, _NICE_FLAG_OPTIONS),
    "ionice": (_IONICE_VALUE_OPTIONS, _IONICE_FLAG_OPTIONS),
    "stdbuf": (_STDBUF_VALUE_OPTIONS, _STDBUF_FLAG_OPTIONS),
}

#: ``ssh`` options that consume the following word as a value, and ssh's
#: BOOLEAN options (OpenSSH ``ssh --help`` usage line, 2026-11-19: the
#: boolean bundle is ``[-46AaCfGgKkMNnqsTtVvXxYy]``). ``-B`` and ``-P`` were
#: missing from the value table. Back-pointer: ssh is a FAIL-CLOSED scanner.
_SSH_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-B", "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l",
    "-m", "-O", "-o", "-P", "-p", "-Q", "-R", "-S", "-W", "-w",
})
_SSH_FLAG_OPTIONS: frozenset[str] = frozenset({
    "-4", "-6", "-A", "-a", "-C", "-f", "-G", "-g", "-K", "-k", "-M", "-N",
    "-n", "-q", "-s", "-T", "-t", "-V", "-v", "-X", "-x", "-Y", "-y",
})

#: Options of util-linux ``watch`` that consume the following word as a
#: value (``watch --help`` on this host, 2026-11-19): ``-n/--interval``
#: and ``-q/--equexit``. ``watch -t`` is ``--no-title`` and ``watch -p`` is
#: ``--precise`` on this host — both are FLAGS, so neither may be treated
#: as value-consuming (that is why the two runners do NOT share a set).
_WATCH_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-n", "--interval", "-q", "--equexit",
})

#: The BOOLEAN (value-less) options of ``systemd-run``, verified against
#: ``systemd-run --help`` on this host (2026-11-19). An option that is in
#: NEITHER set is one the tool does not have: its arity is unknown, so the
#: tail analysis cannot anchor ⇒ FAIL CLOSED.
_SYSTEMD_RUN_FLAG_OPTIONS: frozenset[str] = frozenset({
    "-h", "--help", "--version", "--no-ask-password", "--user", "--scope",
    "--slice-inherit", "--no-block", "-r", "--remain-after-exit", "--wait",
    "--send-sighup", "-d", "--same-dir", "-t", "--pty", "-P", "--pipe",
    "-q", "--quiet", "-G", "--collect", "-S", "--shell",
})

#: The BOOLEAN (value-less) options of util-linux ``watch``, verified
#: against ``watch --help`` on this host (2026-11-19).
_WATCH_FLAG_OPTIONS: frozenset[str] = frozenset({
    "-b", "--beep", "-c", "--color", "-C", "--no-color", "-d", "--differences",
    "-e", "--errexit", "-g", "--chgexit", "-p", "--precise", "-r", "--no-rerun",
    "-t", "--no-title", "-w", "--no-wrap", "-x", "--exec", "-h", "--help",
    "-v", "--version",
})

#: Prefixes of the ``systemd-run`` options that consume the following word
#: as a value (the timer options). ``--on-*`` covers ``--on-active``,
#: ``--on-boot``, ``--on-startup``, ``--on-unit-active``,
#: ``--on-unit-inactive`` and ``--on-calendar``; ``--timer-*`` covers
#: ``--timer-property``.
_ARGV_RUNNER_VALUE_PREFIXES: tuple[str, ...] = ("--on-", "--timer-")

#: The ``systemd-run`` options in the prefixes above that take NO value
#: (they are FLAGS). A prefix rule must not swallow the word after them:
#: the tail analysis would anchor one word late and a dangerous tail
#: would slip through.
_ARGV_RUNNER_VALUE_FLAGS: frozenset[str] = frozenset({
    "--on-timezone-change", "--on-clock-change",
})


def _argv_runner_option_grammar(
    base: str,
) -> tuple[frozenset[str], frozenset[str], tuple[str, ...], frozenset[str]]:
    """The (value, flag, prefix, prefix-flag) tables of this argv runner."""
    if base == "watch":
        return _WATCH_VALUE_OPTIONS, _WATCH_FLAG_OPTIONS, (), frozenset()
    return (
        _SYSTEMD_RUN_VALUE_OPTIONS, _SYSTEMD_RUN_FLAG_OPTIONS,
        _ARGV_RUNNER_VALUE_PREFIXES, _ARGV_RUNNER_VALUE_FLAGS,
    )


def _argv_runner_option_is_known(text: str, base: str) -> bool:
    """Is this an option the runner actually HAS?

    An option in NEITHER table has an unknown arity: the tail analysis
    could anchor on its value and miss the command behind it ⇒ the caller
    fails closed.
    """
    value, flag, prefixes, prefix_flags = _argv_runner_option_grammar(base)
    return _option_token(
        text, value, flag, long_prefixes=prefixes, long_prefix_flags=prefix_flags
    )[0]


def _argv_runner_option_consumes_value(text: str, base: str) -> bool:
    """Does this argv-runner option consume the NEXT word as its value?

    ``--opt=value`` and the attached/bundled short forms (``-Hhost``,
    ``-n1``) are self-contained: they never consume the following word.
    """
    value, flag, prefixes, prefix_flags = _argv_runner_option_grammar(base)
    return _option_token(
        text, value, flag, long_prefixes=prefixes, long_prefix_flags=prefix_flags
    )[1]


#: DERIVED from ``_SYSTEMCTL_TAIL_MUTATING`` — do not hand-edit. The
#: boundaries use lookarounds instead of ``\b`` so that ``preset`` and
#: ``preset-all`` stay DISTINCT verbs (``\b`` matched ``preset`` inside
#: ``preset-all`` by accident; the derivation must not keep the accident).
_VERB_BOUNDARY_BEFORE = r"(?<![a-z0-9-])"
_VERB_BOUNDARY_AFTER = r"(?![a-z0-9-])"
_MUTATING_VERB_RE = re.compile(
    _VERB_BOUNDARY_BEFORE
    + "(?:"
    + "|".join(
        re.escape(verb)
        for verb in sorted(_SYSTEMCTL_TAIL_MUTATING, key=len, reverse=True)
    )
    + ")"
    + _VERB_BOUNDARY_AFTER
)

#: The ensemble family token set of the pre-2026-11-18 TEXT guard (substring
#: semantics, exactly as that guard matched it — the shape must never be
#: WEAKER than the guard it replaces).
_ENSEMBLE_TOKEN_RE = re.compile(
    r"(?:ensemble-prod|ensemble-daemon|ensemble-main|ensemble-demo|agents-ensemble)"
)

#: Refusal token for the laundering family (payload handed to an executor).
LAUNDERED_TOKEN = "laundered-payload"

_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
_UNIT_FAMILY_RE = re.compile(r"^(?:ensemble|ens|agents[-_]?ensemble)(?:[-@.:]|$)")
_GLOB_CHARS = ("*", "?", "[")

_UNIT_SUFFIXES: tuple[str, ...] = (
    ".service", ".socket", ".timer", ".target", ".mount", ".path", ".slice",
    ".device", ".automount", ".swap", ".scope",
)

_MAX_DEPTH = 6

_LIT = "lit"
_EXP = "exp"
_VAR = "var"

# A word is a list of (kind, value) parts; a segment is a list of words.
_Part = tuple[str, str]
_Word = list[_Part]
_Segment = list[_Word]


# ---------------------------------------------------------------------------
# kill legs — UNCHANGED text-scoped semantics
# ---------------------------------------------------------------------------


def _as_text(command) -> str:
    if isinstance(command, (list, tuple)):
        return " ".join(str(c) for c in command)
    return str(command)


def is_kill_like(cmd_lower: str) -> bool:
    """Text-scoped kill-shape detection (the original overlay legs).

    ``systemctl`` is deliberately NOT decided here — structure decides it.
    """
    if re.search(r"\bpkill\b", cmd_lower):
        return True
    if re.search(r"\bkillall\b", cmd_lower):
        return True
    if re.search(r"\bfuser\b", cmd_lower):
        return True
    # "kill" as a command word (not inside another word, not "skill").
    if re.search(r"(^|[\s;&|(`])\s*kill\b", cmd_lower):
        return True
    if "xargs kill" in cmd_lower or re.search(r"\bxargs\s+kill\b", cmd_lower):
        return True
    return False


def kill_leg_violation(command) -> str | None:
    """Return the matched live pattern for a kill-shaped command, else None.

    Text-scoped on purpose: a quoted target (``pkill -f 'ensemble-prod'``) is
    still kill intent, so quote-stripping analysis would be a regression.
    """
    lower = _as_text(command).lower()
    if not is_kill_like(lower):
        return None
    for name in LIVE_NAME_PATTERNS:
        if name in lower:
            return name
    for port, pattern in MEANINGFUL_PATH_PATTERNS:
        if re.search(pattern, lower):
            return port
    return None


# ---------------------------------------------------------------------------
# lexer
# ---------------------------------------------------------------------------


def _read_balanced_paren(text: str, i: int) -> tuple[str, int]:
    """``text[i]`` is just past ``$(``; return (inner, index past ``)``)."""
    depth = 1
    out: list[str] = []
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if ch == "'":
            j = text.find("'", i + 1)
            if j < 0:
                out.append(text[i:])
                i = n
                break
            out.append(text[i:j + 1])
            i = j + 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                j += 1
            out.append(text[i:j + 1])
            i = min(j + 1, n)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return "".join(out), i + 1
        out.append(ch)
        i += 1
    return "".join(out), n


def _read_dollar(text: str, i: int) -> tuple[_Part, int]:
    """``text[i] == '$'``; return (part, next index)."""
    n = len(text)
    if i + 1 < n and text[i + 1] in "'\"":
        # ANSI-C quoting (``$'\\x65nsemble-main'``) / locale quoting
        # (``$"..."``) — escapes make the value unresolvable statically;
        # emit an unresolvable var so the operand FAILS CLOSED.
        return (_VAR, ""), i + 1
    if i + 1 < n and text[i + 1] == "(":
        inner, j = _read_balanced_paren(text, i + 2)
        return (_EXP, inner), j
    if i + 1 < n and text[i + 1] == "{":
        j = text.find("}", i + 2)
        if j < 0:
            return (_VAR, text[i + 1:]), n
        return (_VAR, text[i + 2:j]), j + 1
    m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[i + 1:])
    if m:
        return (_VAR, m.group(0)), i + 1 + m.end()
    return (_LIT, "$"), i + 1


def _read_backtick(text: str, i: int) -> tuple[_Part, int]:
    j = text.find("`", i + 1)
    if j < 0:
        return (_EXP, text[i + 1:]), len(text)
    return (_EXP, text[i + 1:j]), j + 1


def _merge_literals(parts: _Word) -> _Word:
    """Collapse adjacent literal parts (the lexer emits per-character lits)."""
    merged: _Word = []
    for kind, value in parts:
        if kind == _LIT and merged and merged[-1][0] == _LIT:
            merged[-1] = (_LIT, merged[-1][1] + value)
        else:
            merged.append((kind, value))
    return merged


def _scan_segments(text: str) -> tuple[list[_Segment], list[str], bool]:
    """Lex a command string into simple-command segments of words.

    Returns ``(segments, separators, saw_pipe)`` where ``separators[i]`` is
    the operator that TERMINATED segment ``i`` ("" for the last one). The
    pipeline rule needs the distinction between a PIPE ("|") and a plain
    sequence (";", "&", newline): only a pipe hands the previous stage's
    OUTPUT to the next stage.

    ``saw_pipe`` is True when the text carries a PIPE operator OUTSIDE
    quotes. The separator list cannot answer that question on its own: a
    segment that ends on an EMPTY word DROPS its separator (the operator
    between the two halves of a subshell is swallowed that way), so the
    pipe presence is tracked by the lexer while it scans.
    """
    segments: list[_Segment] = []
    seps: list[str] = []
    words: _Segment = []
    cur: _Word = []
    started = False
    saw_pipe = False
    i = 0
    n = len(text)

    def end_word() -> None:
        nonlocal cur, started
        if started:
            words.append(_merge_literals(cur))
            cur = []
            started = False

    def end_segment(sep: str = "") -> None:
        nonlocal words
        end_word()
        if words:
            segments.append(words)
            # The separator that TERMINATED this segment ("" for the last).
            # The pipeline rule needs this to tell a PIPE ("|") from a
            # sequence (";", "&", newline).
            seps.append(sep)
            words = []

    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            cur.append((_LIT, text[i + 1]))
            started = True
            i += 2
            continue
        if ch == "'":
            j = text.find("'", i + 1)
            if j < 0:
                cur.append((_LIT, text[i + 1:]))
                i = n
            else:
                cur.append((_LIT, text[i + 1:j]))
                i = j + 1
            started = True
            continue
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                c2 = text[i]
                if c2 == "\\" and i + 1 < n:
                    cur.append((_LIT, text[i + 1]))
                    i += 2
                    continue
                if c2 == "$":
                    part, i = _read_dollar(text, i)
                    cur.append(part)
                    continue
                if c2 == "`":
                    part, i = _read_backtick(text, i)
                    cur.append(part)
                    continue
                cur.append((_LIT, c2))
                i += 1
            i = i + 1 if i < n else n
            started = True
            continue
        if ch == "$":
            part, i = _read_dollar(text, i)
            cur.append(part)
            started = True
            continue
        if ch == "`":
            part, i = _read_backtick(text, i)
            cur.append(part)
            started = True
            continue
        if ch == "#" and not started:
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch in " \t\r":
            end_word()
            i += 1
            continue
        if ch == "\n" or ch in ";&|()":
            if ch == "|":
                saw_pipe = True
            end_segment(ch)
            i += 1
            continue
        cur.append((_LIT, ch))
        started = True
        i += 1
    end_segment()
    return segments, seps, saw_pipe


def _scan(text: str) -> list[_Segment]:
    """Lex a command string into simple-command segments of words."""
    return _scan_segments(text)[0]


# ---------------------------------------------------------------------------
# heredocs — bodies are data unless they are a shell interpreter's stdin
# ---------------------------------------------------------------------------


def _heredoc_operators(text: str) -> list[tuple[int, int, bool, str]]:
    """Find ``<<``/``<<-`` operators in CODE positions (quote-aware).

    Returns (op_start, op_end, strip_tabs, delimiter) tuples.
    """
    ops: list[tuple[int, int, bool, str]] = []
    i = 0
    n = len(text)
    in_single = in_double = False
    while i < n:
        ch = text[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == "<" and text.startswith("<<", i) and not text.startswith("<<<", i):
            j = i + 2
            strip_tabs = False
            if j < n and text[j] == "-":
                strip_tabs = True
                j += 1
            while j < n and text[j] in " \t":
                j += 1
            quote = ""
            if j < n and text[j] in "'\"":
                quote = text[j]
                j += 1
            k = j
            while k < n and (text[k].isalnum() or text[k] in "_-."):
                k += 1
            delim = text[j:k]
            if quote and k < n and text[k] == quote:
                k += 1
            if delim:
                ops.append((i, k, strip_tabs, delim))
                i = k
                continue
        i += 1
    return ops


def _line_start(text: str, pos: int) -> int:
    nl = text.rfind("\n", 0, pos)
    return 0 if nl < 0 else nl + 1


def _line_end(text: str, pos: int) -> int:
    nl = text.find("\n", pos)
    return len(text) if nl < 0 else nl


def _is_shell_stdin_line(line_text: str, env: dict[str, str]) -> bool:
    """Is the heredoc attached to a reader that EXECUTES its stdin?

    A shell interpreter runs the body as commands, and the SCHEDULERS
    (``at`` / ``batch`` / ``crontab``) run it too: ``crontab -`` installs
    the body as a crontab read from stdin, and ``at`` / ``batch`` run the
    lines they are handed.
    """
    segments = _scan(line_text)
    if not segments:
        return False
    words = segments[0]
    k = _command_word_index(words, env)
    if k is None:
        return False
    text, ok = _resolve_word(words[k], env, 0)
    if not ok:
        return False
    base = text.rsplit("/", 1)[-1].lower()
    return base in _SHELLS or base in _SCHEDULERS


def _strip_heredocs(
    text: str, env: dict[str, str]
) -> tuple[str, list[str], list[tuple[str, str]]]:
    """Remove heredoc bodies from ``text``.

    Returns ``(code_only, executed_bodies, data_bodies)``:
    * ``executed_bodies`` — bodies fed to a shell interpreter's stdin (they
      ARE commands);
    * ``data_bodies`` — ``(delimiter, body)`` pairs fed to some other
      command (they are DATA — but a producer whose stdout is piped into
      an executor HANDS THAT DATA OVER, which the pipeline rule accounts
      for).
    """
    ops = _heredoc_operators(text)
    if not ops:
        return text, [], []

    removed: list[tuple[int, int]] = []
    executed: list[str] = []
    data: list[tuple[str, str]] = []
    cursor = 0
    i = 0
    while i < len(ops):
        op_start, op_end, strip_tabs, delim = ops[i]
        line_start = _line_start(text, op_start)
        line_end = _line_end(text, op_end)
        line_ops = [o for o in ops if _line_start(text, o[0]) == line_start]
        is_shell = _is_shell_stdin_line(text[line_start:line_ops[0][0]], env)
        body_cursor = line_end + 1
        for idx, (o_start, o_end, o_tabs, o_delim) in enumerate(line_ops):
            if o_start < cursor:
                continue
            if idx == 0:
                body_cursor = _line_end(text, o_end) + 1
            body_start = body_cursor
            scan = body_start
            body_end = len(text)
            term_end = len(text)
            while scan <= len(text):
                le = _line_end(text, scan)
                line = text[scan:le]
                candidate = line.lstrip("\t") if o_tabs else line
                if candidate.rstrip("\r") == o_delim:
                    body_end = scan
                    term_end = min(len(text), le + 1)
                    break
                if le >= len(text):
                    break
                scan = le + 1
            body = text[body_start:body_end]
            if is_shell:
                executed.append(body)
            else:
                data.append((o_delim, body))
            removed.append((body_start, term_end))
            body_cursor = term_end
            cursor = term_end
        # skip the remaining ops on this line (already consumed as bodies)
        while i < len(ops) and _line_start(text, ops[i][0]) == line_start:
            i += 1

    out: list[str] = []
    pos = 0
    for start, end in sorted(removed):
        if start < pos:
            start = pos
        if start > pos:
            out.append(text[pos:start])
        pos = min(end, len(text))
    out.append(text[pos:])
    return "".join(out), executed, data


# ---------------------------------------------------------------------------
# static same-string variable / substitution resolution
# ---------------------------------------------------------------------------


def _resolve_dollar_expr(expr: str, env: dict[str, str]) -> tuple[str, bool]:
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-=+])(.*))?$", expr, re.S)
    if not m:
        # $1, $@, $?, $_ , #{...} etc — not statically resolvable.
        return "", False
    name, op, default = m.group(1), m.group(2), m.group(3)
    if name in env:
        return env[name], True
    if op in (":-", "-", "=", ":=") and default is not None:
        if "$" in default or "`" in default:
            return "", False
        return default, True
    return "", False


def _resolve_substitution(inner: str, env: dict[str, str], depth: int) -> tuple[str, bool]:
    """Resolve a trivial ``$(echo <literal>)`` / ``$(printf %s <literal>)``."""
    if depth > _MAX_DEPTH:
        return "", False
    code, executed, _data = _strip_heredocs(inner, env)
    if executed:
        return "", False
    segments = _scan(code)
    if len(segments) != 1:
        return "", False
    words = segments[0]
    k = _command_word_index(words, env)
    if k is None:
        return "", False
    cmd, ok = _resolve_word(words[k], env, depth + 1)
    if not ok:
        return "", False
    cmd = cmd.rsplit("/", 1)[-1].lower()
    args: list[str] = []
    for w in words[k + 1:]:
        text, ok_arg = _resolve_word(w, env, depth + 1)
        if not ok_arg or any(g in text for g in _GLOB_CHARS):
            return "", False
        args.append(text)
    if cmd == "echo":
        args = [a for a in args if not (a.startswith("-") and set(a[1:]) <= {"n", "e", "E"})]
        return " ".join(args), True
    if cmd == "printf" and args:
        fmt = args[0]
        if "%" not in fmt:
            return " ".join(args), True
        if fmt.strip() in ("%s", "%s\n"):
            return " ".join(args[1:]), True
    return "", False


def _resolve_parts(parts: Sequence[_Part], env: dict[str, str], depth: int) -> tuple[str, bool]:
    out: list[str] = []
    for kind, value in parts:
        if kind == _LIT:
            out.append(value)
        elif kind == _VAR:
            text, ok = _resolve_dollar_expr(value, env)
            if not ok:
                return "", False
            out.append(text)
        elif kind == _EXP:
            text, ok = _resolve_substitution(value, env, depth + 1)
            if not ok:
                return "", False
            out.append(text)
        else:  # pragma: no cover - defensive
            return "", False
    return "".join(out), True


def _resolve_word(word: _Word, env: dict[str, str], depth: int = 0) -> tuple[str, bool]:
    return _resolve_parts(word, env, depth)


def _collect_assignments(segments: Sequence[_Segment]) -> dict[str, str]:
    """Collect ``NAME=value`` prefixes (same-string, static) and resolve them."""
    raw: dict[str, _Word] = {}
    for words in segments:
        k = 0
        while k < len(words):
            head_text, head_ok = _resolve_word(words[k], {}, 0)
            if head_ok and head_text in ("export", "declare", "local", "readonly", "typeset"):
                k += 1
                continue
            break
        while k < len(words):
            head = words[k]
            if not head or head[0][0] != _LIT:
                break
            m = _ASSIGN_RE.match(head[0][1])
            if not m:
                break
            name = m.group(1)
            rest: _Word = [(_LIT, head[0][1][m.end():])] + list(head[1:])
            raw[name] = rest
            k += 1

    env: dict[str, str] = {}
    for _ in range(3):
        for name, parts in raw.items():
            text, ok = _resolve_parts(parts, env, 0)
            if ok:
                env[name] = text
    return env


# ---------------------------------------------------------------------------
# systemctl intent analysis
# ---------------------------------------------------------------------------


def _unit_base(unit: str) -> str:
    name = unit.strip().strip("\"'").lower()
    if "@" in name:
        name = name.split("@", 1)[0]
    for suffix in _UNIT_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _unit_is_ensemble(unit: str) -> bool:
    return bool(_UNIT_FAMILY_RE.match(_unit_base(unit)))


def _command_word_index(words: _Segment, env: dict[str, str]) -> int | None:
    """Index of the command word after peeling wrapper prefixes."""
    k = 0
    n = len(words)
    while k < n:
        text, ok = _resolve_word(words[k], env, 0)
        if not ok:
            # An unresolvable LEADING ASSIGNMENT (``X="$1" systemctl ...``)
            # is still an assignment, not the command word — skip it, or
            # the whole segment escapes analysis (the literal
            # ``systemctl restart ensemble-main`` after it would be missed).
            probe = _word_literal_prefix(words[k])
            if probe and _ASSIGN_RE.match(probe):
                k += 1
                continue
            return k
        # Leading ``NAME=value`` prefixes are environment assignments, not the
        # command word (``UNIT=x systemctl restart "$UNIT"``).
        if _ASSIGN_RE.match(text):
            k += 1
            continue
        base = text.rsplit("/", 1)[-1].lower()
        if base in _SHELL_KEYWORDS:
            k += 1
            continue
        if base not in _WRAPPER_WORDS and base not in _WRAPPER_ARG_WORDS:
            return k
        k += 1
        # peel the wrapper's own option / value arguments
        if base in _WRAPPER_ARG_WORDS:
            # one grammar per tool — the four wrappers do not share one
            value_options, flag_options = _WRAPPER_ARG_GRAMMARS[base]
            while k < n:
                arg, arg_ok = _resolve_word(words[k], env, 0)
                if arg_ok and arg.startswith("-"):
                    _known, consumes, _attached = _option_token(
                        arg, value_options, flag_options
                    )
                    k += 2 if consumes else 1
                    continue


                if arg_ok and re.fullmatch(r"[0-9]+(\.[0-9]+)?[smhd]?", arg):
                    k += 1
                    continue
                break
        elif base in ("sudo", "doas"):
            # ``sudo -u root <cmd>`` — without peeling the options the
            # command word resolves to ``-u`` and BOTH the systemctl and
            # the wrapper checks miss the shape entirely.
            while k < n:
                arg, arg_ok = _resolve_word(words[k], env, 0)
                if not arg_ok:
                    break
                if arg.startswith("-"):
                    _known, consumes, _attached = _option_token(
                        arg, _SUDO_VALUE_OPTIONS, _SUDO_FLAG_OPTIONS
                    )
                    k += 2 if consumes else 1
                    continue
                break
        if base == "env":
            while k < n:
                arg, arg_ok = _resolve_word(words[k], env, 0)
                if not arg_ok:
                    break
                if arg.startswith("-"):
                    _known, consumes, _attached = _option_token(
                        arg, _ENV_VALUE_OPTIONS, _ENV_FLAG_OPTIONS
                    )
                    k += 2 if consumes else 1
                    continue
                if _ASSIGN_RE.match(arg):
                    k += 1
                    continue
                break
    return None


# ---------------------------------------------------------------------------
# unit-stop wrapper — basename execution refusal + supervision-primed scripts
# ---------------------------------------------------------------------------


def _basename(text: str) -> str:
    """Basename of a single-token path; "" when the text is not one token."""
    text = text.strip().strip("\"'")
    if not text or any(ch.isspace() for ch in text):
        return ""
    return text.rsplit("/", 1)[-1].lower()


def _word_literal_prefix(word: _Word) -> str:
    """The literal characters of a word (variables/substitutions dropped).

    ``ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:"$U"`` yields the prefix up to
    the variable, which is enough to recognise the assignment SHAPE.
    """
    return "".join(value for kind, value in word if kind == _LIT)


def _shell_script_arg(words: _Segment, k: int, env: dict[str, str]) -> _Word | None:
    """The word a shell interpreter would execute as a script file, if any.

    ``bash -c '<payload>'`` returns None (the payload is a command STRING, and
    it is recursed into separately). ``bash < <file>`` returns ``<file>`` —
    stdin redirection executes the file. An unresolvable candidate is returned
    verbatim so the caller can still check its literal prefix.
    """
    value_opts = ("-o", "+o", "-O", "+O", "--rcfile", "--init-file")
    j = k + 1
    n = len(words)
    while j < n:
        text, ok = _resolve_word(words[j], env, 0)
        if not ok:
            return words[j]
        if text in ("-c", "-lc", "-ic", "--command"):
            return None
        if text == "<":
            return words[j + 1] if j + 1 < n else None
        if text.startswith("-") or text.startswith("+"):
            j += 2 if text in value_opts else 1
            continue
        return words[j]
    return None


def _script_invocation(words: _Segment, env: dict[str, str]) -> bool:
    """Does this segment invoke a script file (rule 2's script-path half)?"""
    k = _command_word_index(words, env)
    if k is None or k >= len(words):
        return False
    cmd_text, cmd_ok = _resolve_word(words[k], env, 0)
    if not cmd_ok:
        return False
    base = _basename(cmd_text)
    if base in _SHELLS or base in ("source", "."):
        script = _shell_script_arg(words, k, env)
        if script is not None:
            return True
        # ``bash -c '<payload>'``: the payload is a command STRING — it
        # invokes a script when its own (first) command word is one.
        for i in range(k + 1, len(words) - 1):
            flag, flag_ok = _resolve_word(words[i], env, 0)
            if flag_ok and flag in ("-c", "-lc", "-ic", "--command"):
                payload, payload_ok = _resolve_word(words[i + 1], env, 0)
                if payload_ok:
                    return _text_invokes_script(payload, env)
        return False
    if not base:
        return False
    if base.endswith(".sh") or base.endswith(".bash"):
        return True
    return "/" in cmd_text and not cmd_text.startswith("-")


def _text_invokes_script(text: str, env: dict[str, str]) -> bool:
    """Does a command STRING invoke a script file as its command word?"""
    for words in _scan(text):
        k = _command_word_index(words, env)
        if k is None or k >= len(words):
            continue
        cmd_text, ok = _resolve_word(words[k], env, 0)
        if not ok:
            return True
        base = _basename(cmd_text)
        if base.endswith(".sh") or base.endswith(".bash"):
            return True
        if "/" in cmd_text and not cmd_text.startswith("-"):
            return True
    return False


def _shell_reads_stdin(words: _Segment, env: dict[str, str]) -> bool:
    """A shell interpreter fed on stdin (no script argument, or ``< file``).

    A ``-c`` payload is a command STRING (recursed into separately), so it
    does NOT make the shell a stdin reader.
    """
    k = _command_word_index(words, env)
    if k is None or k >= len(words):
        return False
    text, ok = _resolve_word(words[k], env, 0)
    if not ok or _basename(text) not in _SHELLS:
        return False
    stdin_redirect = False
    saw_script = False
    j = k + 1
    n = len(words)
    while j < n:
        t, t_ok = _resolve_word(words[j], env, 0)
        if not t_ok:
            return False
        if t in ("-c", "-lc", "-ic", "--command"):
            return False
        if t == "<":
            stdin_redirect = True
            j += 1
            continue
        if t in (">", ">>"):
            j += 1
            continue
        if t.startswith("-") or t.startswith("+"):
            j += 1
            continue
        saw_script = True
        j += 1
    return stdin_redirect or not saw_script


def _protected_wrapper_violation(words: _Segment, env: dict[str, str]) -> str | None:
    """Rule 1: does this segment EXECUTE the unit-stop wrapper?"""
    k = _command_word_index(words, env)
    if k is None or k >= len(words):
        return None
    cmd_text, cmd_ok = _resolve_word(words[k], env, 0)
    if cmd_ok and _basename(cmd_text) in PROTECTED_WRAPPER_NAMES:
        return WRAPPER_VIOLATION_TOKEN

    # ``find ... -exec <wrapper> ...`` / ``-execdir`` / interactive forms.
    for j, word in enumerate(words):
        text, ok = _resolve_word(word, env, 0)
        if not ok or text not in ("-exec", "-execdir", "-ok", "-okdir"):
            continue
        if j + 1 < len(words):
            nxt, nxt_ok = _resolve_word(words[j + 1], env, 0)
            if nxt_ok and _basename(nxt) in PROTECTED_WRAPPER_NAMES:
                return WRAPPER_VIOLATION_TOKEN

    if cmd_ok and (_basename(cmd_text) in _SHELLS or _basename(cmd_text) in ("source", ".")):
        script = _shell_script_arg(words, k, env)
        if script is not None:
            text, ok = _resolve_word(script, env, 0)
            probe = text if ok else _word_literal_prefix(script)
            if _basename(probe) in PROTECTED_WRAPPER_NAMES:
                return WRAPPER_VIOLATION_TOKEN
    return None


def _segment_has_supervision_marker(words: _Segment, env: dict[str, str]) -> bool:
    """Does this segment ASSIGN ``ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:*``?

    Recognised in every assignment position: a leading ``NAME=value`` prefix,
    after ``export``/``declare``/..., and as an ``env`` argument. A resolved
    value must start with the ``UNIT_MANAGED:`` prefix; when the word does not
    resolve statically, its LITERAL prefix is checked, so
    ``ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:"$U"`` is still recognised.
    """
    k = _command_word_index(words, env)
    if k is None:
        return False
    candidates: list[_Word] = list(words[:k])
    if k < len(words):
        head, head_ok = _resolve_word(words[k], env, 0)
        if head_ok and _basename(head) in (
            "export", "declare", "local", "readonly", "typeset",
        ):
            candidates.extend(words[k + 1:])
    needle = SUPERVISION_MARKER_VAR + "="
    for word in candidates:
        text, ok = _resolve_word(word, env, 0)
        probe = text if ok else _word_literal_prefix(word)
        if not probe.startswith(needle):
            continue
        if probe[len(needle):].startswith(SUPERVISION_MANAGED_PREFIX):
            return True
    return False


def _has_protected_word(segments: Sequence[_Segment], env: dict[str, str]) -> bool:
    """Any single-token word in code position that IS the wrapper basename?"""
    for words in segments:
        for word in words:
            text, ok = _resolve_word(word, env, 0)
            probe = text if ok else _word_literal_prefix(word)
            if probe and _basename(probe) in PROTECTED_WRAPPER_NAMES:
                return True
    return False


def _pipeline_wrapper_violation(
    segments: Sequence[_Segment], env: dict[str, str]
) -> str | None:
    """``cat <wrapper> | bash`` / ``bash < <wrapper>`` cross-segment shape."""
    if not _has_protected_word(segments, env):
        return None
    for words in segments:
        if _shell_reads_stdin(words, env):
            return WRAPPER_VIOLATION_TOKEN
    return None


# ---------------------------------------------------------------------------
# runner / pipe / scheduler laundering (2026-11-18 reviewer round)
# ---------------------------------------------------------------------------
# The pre-2026-11-18 TEXT guard refused any command text that carried
# ("systemctl" AND a mutating verb AND an ensemble-family token). The
# structural matcher replaced that text scan, and in doing so it LOST the
# literal-through-runner class: a payload handed to an executor
# (``echo 'systemctl restart ensemble-main' | bash``, ``su -c '...'``,
# ``ssh host '...'``, ``systemd-run ...``, ``at now``) is DATA to the
# producer and never reached the command-word analysis.
#
# The rule below restores that coverage WITHOUT re-introducing the prose
# over-block:
#
# * SHAPE — the payload text carries ("systemctl" AND a mutating verb AND
#   an ensemble-family token). This is the OLD guard's conjunction, kept
#   verbatim (same verb set, same token set, same substring semantics), so
#   coverage is never weaker than the guard being replaced.
# * DELIVERY — the payload is actually HANDED to an executor: a pipe into a
#   stdin-reading shell / scheduler / xargs, or a nested command string
#   (``bash -c``, ``eval``, a shell's heredoc-stdin) whose expansions feed
#   the executed text. A payload that only lands in a file, a variable or a
#   grep pattern is DATA and stays allowed (the prose pins depend on it).
# * RUNNERS — ``su -c`` / ``runuser -c`` / ``ssh host '<cmd>'`` carry a
#   COMMAND STRING (recursed into); ``systemd-run`` / ``watch`` carry an
#   ARGV (analysed as a command segment); ``xargs`` takes its operands from
#   STDIN, so a non-readonly systemctl tail with no operand (or a ``{}``
#   placeholder) FAILS CLOSED.


def _text_has_mutating_ensemble_shape(text: str) -> bool:
    """The old text guard's conjunction, reproduced (and slightly widened).

    ("systemctl" token) AND (a mutating verb) AND (an ensemble-family
    token) — anywhere in the same text. The verb set is the old guard's
    set PLUS the verbs the structural matcher refuses on its own (unknown
    verbs fail closed against an ensemble unit), so the piped shape is never
    weaker than the same verb in command position.
    """
    lower = text.lower()
    if "systemctl" not in lower:
        return False
    if not _MUTATING_VERB_RE.search(lower):
        return False
    return bool(_ENSEMBLE_TOKEN_RE.search(lower))


def _join_command_words(
    value: str, words: _Segment, start: int, env: dict[str, str]
) -> str:
    """The command string a split option carries: its value plus the rest."""
    parts = [value] if value else []
    for word in words[start:]:
        text, ok = _resolve_word(word, env, 0)
        parts.append(text if ok else _word_literal_prefix(word))
    return " ".join(p for p in parts if p)


def _env_split_string_payload(
    words: _Segment, env: dict[str, str]
) -> tuple[str, str | None]:
    """The COMMAND STRING carried by ``env -S`` / ``env --split-string``.

    ``env -S 'systemctl restart <unit>'`` SPLITS the string and RUNS it, so
    the string is an EXECUTED command — not an opaque value. The effective
    command string is the option's own value plus the words that follow it
    (the unquoted ``--split-string=cmd arg ...`` form carries only the
    first word of the command line). Bundled forms are normalised by the
    shared analyser: ``-iS`` takes the NEXT word, ``-iS<value>`` carries
    the value inside the token.

    Returns ``("none", None)`` when the segment is not an ``env -S``
    invocation, ``("payload", text)`` when it is, and ``("unresolved",
    None)`` when the split operand cannot be resolved statically — the
    caller FAILS CLOSED on that.
    """
    n = len(words)
    # ``env`` may sit behind shell keywords and wrappers (``if env -S …``,
    # ``sudo env -S …``, ``timeout 5 env -S …``): walk up to the command
    # word the peeler resolves to and take the first ``env`` on the way.
    limit = _command_word_index(words, env)
    limit = n - 1 if limit is None else limit
    start = None
    for j in range(min(limit, n - 1) + 1):
        text, ok = _resolve_word(words[j], env, 0)
        if ok and _basename(text) == "env":
            start = j
            break
    if start is None:
        return "none", None
    j = start + 1
    while j < n:
        text, ok = _resolve_word(words[j], env, 0)
        if not ok:
            return "none", None
        if _ASSIGN_RE.match(text):
            j += 1
            continue
        if text.startswith("--split-string="):
            return "payload", _join_command_words(
                text[len("--split-string="):], words, j + 1, env
            )
        if text == "--split-string":
            if j + 1 >= n:
                return "none", None
            value, value_ok = _resolve_word(words[j + 1], env, 0)
            if not value_ok:
                return "unresolved", None
            return "payload", _join_command_words(value, words, j + 2, env)
        if text.startswith("-") and not text.startswith("--") and "S" in text[1:]:
            known, consumes, attached = _option_token(
                text, _ENV_VALUE_OPTIONS, _ENV_FLAG_OPTIONS
            )
            if not known:
                return "none", None
            if attached is not None:
                return "payload", _join_command_words(
                    attached, words, j + 1, env
                )
            if consumes:
                if j + 1 >= n:
                    return "none", None
                value, value_ok = _resolve_word(words[j + 1], env, 0)
                if not value_ok:
                    return "unresolved", None
                return "payload", _join_command_words(value, words, j + 2, env)
            # ``-S`` at the very end of a bundle with no value behind it
            return "none", None
        if text.startswith("-"):
            known, consumes, attached = _option_token(
                text, _ENV_VALUE_OPTIONS, _ENV_FLAG_OPTIONS
            )
            if not known:
                return "none", None        # env is a skip-unknown scanner
            j += 2 if (consumes or attached is not None) else 1
            continue
        break
    return "none", None


def _shell_has_c_payload(words: _Segment, env: dict[str, str]) -> bool:
    """Does this shell segment carry a ``-c '<command string>'`` payload?"""
    k = _command_word_index(words, env)
    if k is None or k >= len(words):
        return False
    text, ok = _resolve_word(words[k], env, 0)
    if not ok or _basename(text) not in _SHELLS:
        return False
    for i in range(k + 1, len(words)):
        flag, flag_ok = _resolve_word(words[i], env, 0)
        if flag_ok and flag in ("-c", "-lc", "-ic", "--command"):
            return True
    return False


def _ssh_remote_words(
    words: _Segment, k: int, env: dict[str, str]
) -> list[_Word] | None:
    """The words of the REMOTE command of an ``ssh`` segment (may be empty).

    ``None`` means an option ``ssh`` does not have was seen — the caller
    fails closed (the remote command cannot be located).

    ``ssh [options] host [command...]`` — the command (if any) is a COMMAND
    STRING run on the far side, and stdin is forwarded to it.
    """
    j = k + 1
    n = len(words)
    while j < n:
        text, ok = _resolve_word(words[j], env, 0)
        if not ok:
            return []
        if text.startswith("-"):
            known, consumes, _attached = _option_token(
                text, _SSH_VALUE_OPTIONS, _SSH_FLAG_OPTIONS
            )
            if not known:
                # an option ssh does NOT have: its arity is unknown, so the
                # remote command cannot be located ⇒ the caller FAILS CLOSED
                return None
            j += 2 if consumes else 1
            continue
        break
    return list(words[j + 1:])


def _executor_kind(words: _Segment, env: dict[str, str]) -> str | None:
    """What kind of EXECUTOR is this segment? None when it consumes nothing.

    * ``shell`` — a shell fed on stdin (``bash``, ``sh`` with no script);
    * ``shell-c`` — a shell whose ``-c`` payload is a command string;
    * ``scheduler`` — ``at`` / ``batch`` / ``crontab`` (they RUN what they
      are handed);
    * ``xargs`` — stdin becomes the argument vector;
    * ``eval`` — its argument is evaluated as commands;
    * ``ssh`` — stdin is forwarded to the REMOTE command.
    """
    k = _command_word_index(words, env)
    if k is None or k >= len(words):
        return None
    cmd, ok = _resolve_word(words[k], env, 0)
    if not ok:
        return None
    base = _basename(cmd)
    if base in _SHELLS:
        if _shell_reads_stdin(words, env):
            return "shell"
        if _shell_has_c_payload(words, env):
            return "shell-c"
        return None
    if base in _SCHEDULERS:
        return "scheduler"
    if base == "xargs":
        return "xargs"
    if base == "eval":
        return "eval"
    if base == "ssh":
        # ``ssh host`` with NO remote command starts a remote login shell
        # and FORWARDS the client's stdin — the payload IS delivered, so the
        # segment is an executor either way.
        return "ssh"
    return None


def _owned_bodies(
    words: _Segment, data_bodies: Sequence[tuple[str, str]]
) -> list[str]:
    """The heredoc bodies whose ``<<DELIM`` operator sits in this segment."""
    probes: list[str] = []
    for word in words:
        text, ok = _resolve_word(word, {}, 0)
        probes.append(text if ok else _word_literal_prefix(word))
    owned: list[str] = []
    for delim, body in data_bodies:
        for probe in probes:
            if not probe.startswith("<<"):
                continue
            tail = probe[2:].lstrip("-").strip("'\"")
            if tail == delim:
                owned.append(body)
                break
    return owned


def _segment_payload_text(
    words: _Segment,
    env: dict[str, str],
    data_bodies: Sequence[tuple[str, str]],
) -> str | None:
    """The text this segment PRODUCES on stdout (its output payload)."""
    parts: list[str] = []
    for word in words:
        text, ok = _resolve_word(word, env, 0)
        parts.append(text if ok else _word_literal_prefix(word))
    for body in _owned_bodies(words, data_bodies):
        parts.append(body)
    if not parts:
        return None
    return " ".join(parts)


def _pipeline_laundering_violation(
    segments: Sequence[_Segment],
    seps: Sequence[str],
    env: dict[str, str],
    data_bodies: Sequence[tuple[str, str]],
    code: str,
    saw_pipe: bool,
) -> str | None:
    """A payload with the mutating-ensemble SHAPE handed to an EXECUTOR.

    Two independent tests; together they are never weaker than the OLD
    text guard, which refused ANY command text carrying the shape:

    * WHOLE TEXT — the command's ENTIRE stripped code carries the shape
      AND the command pipes into an EXECUTOR. The adjacency test alone is
      NOT enough: the segment that PRODUCES a pipe-fed payload is not
      always the segment that CARRIES it. A subshell ``( ... )``, a brace
      group ``{ ... ; }`` and every intermediate forwarding stage
      (``| cat |``, ``| tee FILE |``, ``| grep ... |``) move the payload
      away from the segment that sits next to the executor, so an
      adjacency-scoped test lets real laundering shapes through.
    * ADJACENCY — the segment next to the executor produces the payload
      ITSELF. Its words are RESOLVED, so a payload that only exists after
      an environment substitution is still seen here.

    The pipe is detected on the RAW stripped code (the lexer tracks it: a
    segment that ends on an EMPTY word DROPS its separator, so the
    separator list cannot answer the question), and the executor is any
    segment whose ``_executor_kind`` is not None. A pipe with NO executor
    stays DATA (the read-only/prose ALLOW legs depend on it).
    """
    if saw_pipe:
        for words in segments:
            if _executor_kind(words, env) is None:
                continue
            if code and _text_has_mutating_ensemble_shape(code):
                return LAUNDERED_TOKEN
            # The raw text is not the whole story: a payload assembled from
            # ENVIRONMENT SUBSTITUTIONS (``A=ens; echo "systemctl restart
            # ${A}-main" | cat | bash``) carries the ensemble token only
            # after resolution, and the adjacency backstop is single-stage
            # (a multi-stage pipe moves the producer away from the
            # executor). Shape-test the RESOLVED payloads too.
            resolved = []
            for seg_words in segments:
                seg_payload = _segment_payload_text(seg_words, env, data_bodies)
                if seg_payload:
                    resolved.append(seg_payload)
            if resolved and _text_has_mutating_ensemble_shape(" ".join(resolved)):
                return LAUNDERED_TOKEN
            # A heredoc body is REMOVED from the code text by
            # ``_strip_heredocs``, so the whole-text test cannot see it —
            # but a body handed to a pipe-fed executor IS executed
            # (``cat <<'EOF' | cat | bash``). Shape-test the bodies too.
            # A body that only lands in a FILE (no pipe, no executor) stays
            # DATA: the disclosed prose over-block class stays dropped.
            for _delim, body in data_bodies:
                if _text_has_mutating_ensemble_shape(body):
                    return LAUNDERED_TOKEN
            break
    for i, words in enumerate(segments):
        sep = seps[i] if i < len(seps) else ""
        if sep != "|":
            continue
        if i + 1 >= len(segments):
            continue
        target = segments[i + 1]
        if _executor_kind(target, env) is None:
            continue
        payload = _segment_payload_text(words, env, data_bodies)
        if payload is None:
            continue
        if _text_has_mutating_ensemble_shape(payload):
            return LAUNDERED_TOKEN
    return None


def _systemctl_segment_violation(
    words: _Segment,
    env: dict[str, str],
    depth: int,
    require_operand: bool = False,
) -> str | None:
    k = _command_word_index(words, env)
    if k is None or k >= len(words):
        return None
    cmd_text, cmd_ok = _resolve_word(words[k], env, 0)
    if not cmd_ok:
        return None
    if cmd_text.rsplit("/", 1)[-1].lower() != "systemctl":
        return None

    verb: str | None = None
    operands: list[tuple[str, bool]] = []
    idx = k + 1
    while idx < len(words):
        text, ok = _resolve_word(words[idx], env, 0)
        if ok and text.startswith("-"):
            # a short BUNDLE (``-aP``) or an ATTACHED value (``-pRestart``)
            # is normalised by the shared analyser
            _known, consumes, _attached = _option_token(
                text, _OPTIONS_WITH_VALUE, _SYSTEMCTL_FLAG_OPTIONS
            )
            idx += 2 if consumes else 1
            continue
        if verb is None:
            if not ok:
                return UNRESOLVED_UNIT_TOKEN
            verb = text.lower()
            idx += 1
            continue
        operands.append((text, ok))
        idx += 1

    if verb is None:
        return None
    if verb in SYSTEMCTL_READONLY:
        # A read-only verb is NOT the end of the analysis. When an option's
        # arity is mis-parsed (a value option missing from the table), the
        # option's VALUE lands in the verb slot and the REAL command stays
        # in the operand tail — ``systemctl -P cat stop <unit>``. The parse
        # therefore CONTINUES: a MUTATING verb found later, FOLLOWED BY an
        # ensemble unit, is refused. ``systemctl status <unit>`` stays
        # allowed — the unit is an operand of the read-only verb with no
        # mutating verb in front of it (the property-name over-block stays
        # dropped: a property VALUE is consumed by its option, so it never
        # reaches this scan).
        for i, (tok, tok_ok) in enumerate(operands):
            if not tok_ok:
                continue
            if tok.lower() not in _SYSTEMCTL_TAIL_MUTATING:
                continue
            for later, later_ok in operands[i + 1:]:
                if later_ok and _unit_is_ensemble(later):
                    return _unit_base(later)
                if not later_ok or any(g in later for g in _GLOB_CHARS):
                    # a mutating verb followed by an operand that cannot be
                    # resolved statically (or is globbed) ⇒ FAIL CLOSED
                    return UNRESOLVED_UNIT_TOKEN
        return None
    if not operands:
        if require_operand:
            # ``xargs systemctl <mutating-verb>`` — the unit operands come
            # from STDIN, so the target is not statically known ⇒ FAIL
            # CLOSED (returning None here was the laundering hole).
            return UNRESOLVED_UNIT_TOKEN
        # No unit operand: nothing to target (``systemctl daemon-reload``,
        # a bare ``systemctl stop`` which is an error anyway) => allowed.
        return None

    for operand, ok in operands:
        if not ok:
            return UNRESOLVED_UNIT_TOKEN
        if any(g in operand for g in _GLOB_CHARS):
            return UNRESOLVED_UNIT_TOKEN
        if _is_xargs_placeholder(operand):
            # ``xargs -I{} systemctl restart {}`` — the operand comes from
            # STDIN ⇒ FAIL CLOSED.
            return UNRESOLVED_UNIT_TOKEN
        if _unit_is_ensemble(operand):
            return _unit_base(operand)
    return None


def _is_xargs_placeholder(operand: str) -> bool:
    """Is this operand an ``xargs`` replacement placeholder (``{}``)?"""
    return bool(operand) and operand.startswith("{") and operand.endswith("}")


def _segment_violation(
    words: _Segment,
    env: dict[str, str],
    depth: int,
    marker: bool = False,
    executed: bool = False,
) -> str | None:
    """Analyse one simple-command segment (recursing into executing hooks).

    ``marker`` is True when a ``ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:*``
    assignment applies to this segment (its own prefix assignment, an
    ``export``/``env`` assignment in the segment, or inherited from a
    preceding segment / enclosing shell).

    ``executed`` is True when the text being analysed IS a command string
    that a nested interpreter will RUN (``bash -c '<payload>'``, ``eval``'s
    argument, a shell's heredoc-stdin body). In that context a literal
    mutating-ensemble shape in the text is itself a refusal — the text will
    be interpreted as commands.
    """
    if depth > _MAX_DEPTH:
        return UNRESOLVED_UNIT_TOKEN

    violation = _protected_wrapper_violation(words, env)
    if violation is not None:
        return violation
    if marker and _script_invocation(words, env):
        return SUPERVISION_PRIMED_TOKEN

    violation = _systemctl_segment_violation(words, env, depth)
    if violation is not None:
        return violation

    # Command substitutions execute wherever they appear — including inside
    # another command's arguments (``echo $(systemctl restart <unit>)``).
    for word in words:
        for kind, value in word:
            if kind == _EXP and value.strip():
                nested = _command_text_violation(value, depth + 1, marker, executed)
                if nested is not None:
                    return nested

    # ``env -S '<cmd string>'`` / ``env --split-string=<cmd string>`` RUN the
    # string: it is an EXECUTED command, not an opaque option value. An
    # UNRESOLVABLE split operand FAILS CLOSED.
    env_status, env_payload = _env_split_string_payload(words, env)
    if env_status == "unresolved":
        return UNRESOLVED_UNIT_TOKEN
    if env_status == "payload" and env_payload is not None:
        nested = _command_text_violation(env_payload, depth + 1, marker, True)
        if nested is not None:
            return nested

    k = _command_word_index(words, env)
    if k is None or k >= len(words):
        return None
    cmd_text, cmd_ok = _resolve_word(words[k], env, 0)
    if not cmd_ok:
        return None
    base = cmd_text.rsplit("/", 1)[-1].lower()

    if base in _SHELLS:
        # ``bash -c '<payload>'`` — the payload is a command string.
        for i in range(k + 1, len(words) - 1):
            flag, flag_ok = _resolve_word(words[i], env, 0)
            if not flag_ok:
                continue
            if flag in ("-c", "-lc", "-ic", "--command"):
                payload, payload_ok = _resolve_word(words[i + 1], env, 0)
                if not payload_ok:
                    return UNRESOLVED_UNIT_TOKEN
                return _command_text_violation(payload, depth + 1, marker, True)
        return None

    if base == "eval":
        payload_text = " ".join(
            _resolve_word(w, env, 0)[0] for w in words[k + 1:]
        )
        for w in words[k + 1:]:
            if not _resolve_word(w, env, 0)[1]:
                return UNRESOLVED_UNIT_TOKEN
        return _command_text_violation(payload_text, depth + 1, marker, True)

    if base in _COMMAND_STRING_RUNNERS:
        # ``su -c '<cmd>'`` / ``runuser -u root -c '<cmd>'`` — the payload is
        # a COMMAND STRING run by a shell (as the target user). These
        # runners' getopt also accepts the ATTACHED form (``su -c<cmd>``),
        # so the payload can ride inside the option token itself. The SHELL
        # branch below deliberately does NOT: bash/sh/zsh reject the
        # attached form, and accepting it there would over-block.
        # The effective command string of the option is its VALUE plus the
        # words that follow it (the ``=``-form carries only the first word
        # of the command line) — the conservative reading, and the one the
        # OLD text guard used when it scanned the whole text.
        for i in range(k + 1, len(words)):
            flag, flag_ok = _resolve_word(words[i], env, 0)
            if not flag_ok:
                continue
            value: str | None = None
            start = i + 1
            if flag.startswith("--command="):
                value = flag[len("--command="):]
            elif flag.startswith("-c") and not flag.startswith("--") and len(flag) > 2:
                value = flag[2:]
            elif flag in ("-c", "--command"):
                if i + 1 >= len(words):
                    continue
                payload, payload_ok = _resolve_word(words[i + 1], env, 0)
                if not payload_ok:
                    return UNRESOLVED_UNIT_TOKEN
                value = payload
                start = i + 2
            if value is None:
                continue
            effective = _join_command_words(value, words, start, env)
            return _command_text_violation(effective, depth + 1, marker, True)
        return None

    if base == "ssh":
        # ``ssh [options] host [command...]`` — the remote command is a
        # COMMAND STRING.
        cmd_words = _ssh_remote_words(words, k, env)
        if cmd_words is None:
            return UNRESOLVED_UNIT_TOKEN
        if not cmd_words:
            return None
        payload_parts: list[str] = []
        for w in cmd_words:
            text, ok = _resolve_word(w, env, 0)
            if not ok:
                return UNRESOLVED_UNIT_TOKEN
            payload_parts.append(text)
        return _command_text_violation(
            " ".join(payload_parts), depth + 1, marker, True
        )

    if base in _ARGV_RUNNERS:
        # ``systemd-run <cmd> ...`` / ``watch <cmd> ...`` — the tail after
        # the runner's own options is an ARGV (command + arguments).
        j = k + 1
        n = len(words)
        while j < n:
            text, ok = _resolve_word(words[j], env, 0)
            if not ok:
                break
            if text.startswith("-"):
                if not _argv_runner_option_is_known(text, base):
                    # An option the runner does NOT have: its arity is
                    # unknown, so the tail analysis could anchor on the
                    # option's VALUE and never analyse the command behind
                    # it ⇒ FAIL CLOSED (the same convention as an
                    # unresolvable operand).
                    return UNRESOLVED_UNIT_TOKEN
                j += (
                    2 if _argv_runner_option_consumes_value(text, base) else 1
                )
                continue
            break
        if j >= len(words):
            return None
        return _segment_violation(words[j:], env, depth + 1, marker, executed)

    if base == "xargs":
        # ``xargs systemctl restart <unit>`` executes the tail as a command.
        j = k + 1
        while j < len(words):
            text, ok = _resolve_word(words[j], env, 0)
            if ok and text.startswith("-"):
                known, consumes, _attached = _option_token(
                    text, _XARGS_VALUE_OPTIONS, _XARGS_FLAG_OPTIONS,
                    _XARGS_ATTACHED_SHORTS,
                )
                if not known:
                    # An option xargs does NOT have: its arity is unknown,
                    # so the tail could anchor on the option's VALUE and
                    # never analyse the command behind it ⇒ FAIL CLOSED.
                    return UNRESOLVED_UNIT_TOKEN
                j += 2 if consumes else 1
                continue
            break
        if j >= len(words):
            return None
        # The operands of an xargs tail come from STDIN: a non-readonly
        # systemctl tail with no operand (or a ``{}`` placeholder) has an
        # UNKNOWN target ⇒ FAIL CLOSED.
        sub_violation = _systemctl_segment_violation(
            words[j:], env, depth + 1, require_operand=True
        )
        if sub_violation is not None:
            return sub_violation
        # The tail is a COMMAND LINE: analyse it as one, whatever it starts
        # with — a runner (``systemd-run``/``su``), a wrapper, ``env -S``,
        # a script, or another ``xargs``. Reading only its first word let
        # ``xargs systemd-run systemctl restart <unit>`` and
        # ``xargs su -c '...'`` through. The recursion is depth-bounded
        # (``_MAX_DEPTH``).
        tail = words[j:]
        if tail:
            violation = _segment_violation(tail, env, depth + 1, marker, executed)
            if violation is not None:
                return violation
        return None

    return None


def _command_text_violation(
    text: str,
    depth: int = 0,
    marker: bool = False,
    executed: bool = False,
) -> str | None:
    if depth > _MAX_DEPTH:
        return UNRESOLVED_UNIT_TOKEN
    code, executed_bodies, data_bodies = _strip_heredocs(text, {})
    segments, seps, saw_pipe = _scan_segments(code)
    env = _collect_assignments(segments)
    # ``ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:*`` carries forward segment
    # by segment (a prefix assignment on one segment does not reach the NEXT
    # command in real shell semantics; carrying it is the conservative
    # reading of the contract and only ever fires alongside a script
    # invocation).
    carried = marker
    for words in segments:
        seg_marker = _segment_has_supervision_marker(words, env)
        eff_marker = carried or seg_marker
        violation = _segment_violation(words, env, depth, eff_marker, executed)
        if violation is not None:
            return violation
        carried = eff_marker
    for body in executed_bodies:
        nested = _command_text_violation(body, depth + 1, carried, True)
        if nested is not None:
            return nested
    if executed and _text_has_mutating_ensemble_shape(code):
        # This text IS a command string that a nested interpreter will RUN
        # (``bash -c``, ``eval``, a shell's heredoc-stdin): a literal
        # mutating-ensemble shape in it is itself the refusal. This is the
        # old text guard's strength, kept for the executed context only.
        # It runs AFTER the structural analysis so the more specific token
        # (the resolved unit) wins where both apply.
        return LAUNDERED_TOKEN
    violation = _pipeline_wrapper_violation(segments, env)
    if violation is not None:
        return violation
    violation = _pipeline_laundering_violation(
        segments, seps, env, data_bodies, code, saw_pipe
    )
    if violation is not None:
        return violation
    return None


def systemctl_violation(command) -> str | None:
    """Return a refusal token when the command mutates an ensemble unit.

    Also refuses execution of the unit-stop wrapper (``stop-ensemble.sh``)
    and env-primed (``ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:*``) script
    invocations — the shapes whose destructive verb is hidden inside a
    script, not present in the command text.
    """
    return _command_text_violation(_as_text(command), 0)


def overlay_violation(command) -> str | None:
    """Combined guard: kill legs (text-scoped) then systemctl (structural)."""
    violation = kill_leg_violation(command)
    if violation is not None:
        return violation
    return systemctl_violation(command)
