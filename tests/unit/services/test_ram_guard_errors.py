"""Substrate tier — the envelope producer and the event-payload allow-list
(test-plan §3.1, cases B2/B4-equivalents and the §4.3 negative space).

The key sets are pinned EXACTLY and ORDERED (§15 B2 / B3) and the
negative space is enforced AT THE PRODUCER (§4.3 / T17).
"""

from __future__ import annotations

import json

from daemon.services.ram_guard.errors import (
    ENVELOPE_KEYS,
    EVENT_ONLY_KEYS,
    EVENT_PAYLOAD_KEYS,
    MESSAGE_TEMPLATE,
    RAM_LIMIT_EXCEEDED_CODE,
    build_ram_envelope_payload,
    build_ram_event_payload,
    format_ram_envelope,
)
from daemon.services.ram_guard.verdict import SpawnRecord

MIB = 1024 * 1024


def make_record(**overrides) -> SpawnRecord:
    kwargs = dict(
        tracking_id="t-1",
        instance_id="i-1",
        lane="bash",
        pid=100,
        pgid=500,
        start_time_jiffies=4242,
        ceiling_bytes=3072 * MIB,
        poll_interval_ms=200,
        grace_ms=250,
    )
    kwargs.update(overrides)
    record = SpawnRecord(**kwargs)
    record.state = "KILLED"
    record.reason = "killed:ladder"
    record.metric = "summed_rss_bytes"
    record.trigger_metric = "summed_rss_bytes"
    record.trigger = "rss_threshold"
    record.observed_peak_bytes = 4096 * MIB
    record.peak_is_lower_bound = True
    record.under_report_bound_bytes = 128 * MIB
    record.confirmed_pss_bytes = 3500 * MIB
    record.tree_complete = True
    record.members_total = 9
    record.members_killable = 8
    record.members_unreachable = 1
    record.unreadable_pids = (101, 102)
    record.gone_pids = (103,)
    record.breach_at = 111.0
    record.killed_at = 112.0
    return record


# ── B2 — the envelope key set ──────────────────────────────────────────────


def test_envelope_key_set_is_the_exact_ordered_list():
    """§15 B2 — the envelope key set, ordered, exact."""
    assert ENVELOPE_KEYS == (
        "ok",
        "code",
        "surface",
        "reason",
        "limit_bytes",
        "rearm_threshold_bytes",
        "observed_peak_bytes",
        "peak_is_lower_bound",
        "poll_interval_ms",
        "metric",
        "trigger_metric",
        "confirmed_pss_bytes",
        "under_report_bound_bytes",
        "pid",
        "pgid",
        "tree_complete",
        "members_total",
        "members_killable",
        "members_unreachable",
        "grace_ms",
        "killed_at",
        "ram_breach_observed",
        "coincident_timeout",
        "timeout_seconds",
        "message",
    )


def test_rearm_threshold_sits_immediately_after_limit_bytes():
    """``rearm_threshold_bytes`` sits immediately after ``limit_bytes``."""
    keys = list(ENVELOPE_KEYS)
    assert keys.index("rearm_threshold_bytes") == keys.index("limit_bytes") + 1


def test_envelope_payload_keys_match_the_pinned_set():
    """The producer emits EXACTLY the pinned key set, in order."""
    payload = build_ram_envelope_payload(make_record())
    assert tuple(payload.keys()) == ENVELOPE_KEYS


def test_surface_strings_are_pinned_char_for_char():
    """arch §2 DQ6 (and plan-overview.md:481) — the surface vocabulary.

    The contract pins ``surface="bash.exec"|"proc.exec"`` — the proc
    lane's surface is ``proc.exec``, NOT a tool-name variant.  This
    assertion was MISSING in the first round, which is how a
    ``"proc.run"`` deviation shipped.
    """
    from daemon.services.ram_guard.errors import SURFACE_BY_LANE

    assert SURFACE_BY_LANE == {
        "bash": "bash.exec",
        "proc": "proc.exec",
    }
    bash = build_ram_envelope_payload(make_record(lane="bash"))
    proc = build_ram_envelope_payload(make_record(lane="proc"))
    assert bash["surface"] == "bash.exec"
    assert proc["surface"] == "proc.exec"
    # The envelope's surface is what the bash return string carries.
    raw = format_ram_envelope(make_record(lane="proc"))
    assert '"surface": "proc.exec"' in raw
    assert "proc.run" not in raw


def test_envelope_three_key_prefix_is_byte_identical_to_the_house_shape():
    """The 3-key house prefix is byte-identical to ``_format_secret_envelope``."""
    raw = format_ram_envelope(make_record())
    assert raw.startswith('{"ok": false, "code": "%s", "surface": "bash.exec"' % RAM_LIMIT_EXCEEDED_CODE)
    parsed = json.loads(raw)
    assert list(parsed.keys())[:3] == ["ok", "code", "surface"]


def test_envelope_numerics_precede_message():
    """The numerics precede ``message`` in the envelope."""
    keys = list(ENVELOPE_KEYS)
    assert keys.index("message") == len(keys) - 1
    for numeric in (
        "limit_bytes",
        "rearm_threshold_bytes",
        "observed_peak_bytes",
        "poll_interval_ms",
        "confirmed_pss_bytes",
        "under_report_bound_bytes",
        "pid",
        "pgid",
        "members_total",
        "members_killable",
        "members_unreachable",
        "grace_ms",
    ):
        assert keys.index(numeric) < keys.index("message")


# ── B3 — the event payload key set ─────────────────────────────────────────


def test_event_payload_key_set_is_the_envelope_plus_exactly_four():
    """§15 B3 — the envelope key set + exactly four event-only keys."""
    assert EVENT_ONLY_KEYS == ("trigger", "state", "tracking_id", "breach_at")
    assert EVENT_PAYLOAD_KEYS == ENVELOPE_KEYS + EVENT_ONLY_KEYS
    assert len(EVENT_PAYLOAD_KEYS) == len(ENVELOPE_KEYS) + 4


def test_event_payload_is_produced_in_the_pinned_order():
    """The event payload projects the listed keys, in that order."""
    payload = build_ram_event_payload(make_record())
    assert tuple(payload.keys()) == EVENT_PAYLOAD_KEYS
    assert payload["trigger"] == "rss_threshold"
    assert payload["state"] == "KILLED"
    assert payload["tracking_id"] == "t-1"
    assert payload["breach_at"] == 111.0


def test_event_payload_never_copies_a_key_off_the_list():
    """A key that is not on the list is NEVER copied.

    The bus persists ``data`` UNSANITIZED — the allow-list MUST be
    enforced at the producer.
    """
    record = make_record()
    record.members = (100, 101, 102)
    payload = build_ram_event_payload(record)
    for forbidden in (
        "members",
        "unreadable_pids",
        "gone_pids",
        "instance_id",
        "lane",
        "start_time_jiffies",
        "samples",
        "enumeration_failures",
        "rearm_count",
        "confirm_count",
        "last_confirm_rss_bytes",
        "sample_totals",
        "claim_token",
        "group_member_count",
    ):
        assert forbidden not in payload


def test_event_payload_is_a_fresh_mapping_each_call():
    """No code path may mutate the returned dict before it reaches the bus."""
    record = make_record()
    first = build_ram_event_payload(record)
    first["state"] = "MUTATED"
    second = build_ram_event_payload(record)
    assert second["state"] == "KILLED"


# ── The negative space (T17 / §4.3) ────────────────────────────────────────


def test_negative_space_is_enforced_at_the_producer():
    """No argv, env, output, secret ids, per-member lists or paths.

    The producer's allow-list is the enforcement point: every published
    value is a number, a boolean, ``None`` or the fixed message text.
    """
    record = make_record()
    record.reason = "killed:ladder"
    payload = build_ram_event_payload(record)
    for key, value in payload.items():
        if key == "message":
            assert isinstance(value, str)
            continue
        assert value is None or isinstance(value, (int, float, bool, str)), (key, value)
    # The only strings are the fixed vocabulary — never a path, a
    # command or a repr.
    strings = {k: v for k, v in payload.items() if isinstance(v, str)}
    assert set(strings) <= {
        "code",
        "surface",
        "reason",
        "metric",
        "trigger_metric",
        "state",
        "tracking_id",
        "trigger",
        "message",
    }
    for key, value in strings.items():
        if key == "message":
            continue
        assert "/" not in value
        assert "Traceback" not in value
        assert " " not in value  # a single token — a flag, a path or a
        # secret would carry whitespace or a separator


def test_message_template_is_the_pinned_clause():
    """§15 B10 — the message clause, verbatim."""
    assert MESSAGE_TEMPLATE == (
        "The enforced trigger threshold was {threshold_mib} MiB (the resolved "
        "ceiling is {limit_mib} MiB); when the summed-RSS metric over-counts "
        "shared pages the threshold is re-armed above the ceiling and the kill "
        "decision is always made on the shared-page total."
    )


def test_message_interpolates_numbers_only():
    """The message carries numeric interpolation only — no argv path."""
    record = make_record()
    message = build_ram_envelope_payload(record)["message"]
    assert "3072 MiB" in message  # the resolved ceiling
    assert "MiB" in message
    assert "{" not in message and "}" not in message


def test_message_reports_the_rearmed_threshold_when_armed():
    """The enforced threshold is the RE-ARMED one when armed."""
    record = make_record()
    record.rearm_threshold_bytes = 3072 * MIB + 32 * MIB
    message = build_ram_envelope_payload(record)["message"]
    assert "3104 MiB" in message
    assert "3072 MiB" in message


def test_envelope_reports_the_resolved_ceiling_as_limit_bytes():
    """``limit_bytes`` is the RESOLVED ceiling (derived or explicit)."""
    record = make_record(ceiling_bytes=8367 * MIB)
    payload = build_ram_envelope_payload(record)
    assert payload["limit_bytes"] == 8367 * MIB
    # The re-arm threshold is 0 (never None) when unarmed.
    assert payload["rearm_threshold_bytes"] == 0
