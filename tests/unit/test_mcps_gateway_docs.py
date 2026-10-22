"""Doc-pin tests for ``docs/mcps-gateway.md`` — the remote-hands center
gateway operations runbook (Phase B, SPEC.md v0.1).

The runbook states operational facts that would silently rot as the code
moves: the kill-switch env var name and its OFF-by-default semantics, the
activation-is-a-daemon-restart statement, the route path, every knob's
documented default, and the honest-absence statements (mTLS, the
notification relay, the listener's default state). This file pins those
facts to the doc so a doc edit that drifts from the code goes red.

CONVENTIONS
-----------
* **Positive and negative needles NEVER share a test function.** A test
  that asserts ``"X" in text`` must not also assert ``"Y" not in text`` —
  keep them in clearly-named separate tests (the
  ``test_docs_cleanup_truth_split.py`` precedent).
* Plain ``pathlib`` only — the doc is read from disk relative to the repo
  root (``Path(__file__).resolve().parents[2]``). NO daemon import, NO
  DB, NO network: the suite stays fast and runnable without the app.
* The defaults below are TRANSCRIBED from
  ``daemon/services/mcps_gateway/config.py`` (the ``DEFAULT_*`` constants
  and the ``GatewayConfig`` dataclass defaults). The transcription is
  deliberate: importing the config module would couple this doc test to
  the app import graph, so the cross-check is a literal here. If the code
  default changes, this table and the doc row must change in the same PR
  — that is the point of the pin.

Run with (bare pytest is a broken PATH in this repo — use uv)::

    uv run python -m pytest tests/unit/test_mcps_gateway_docs.py -v
"""

from __future__ import annotations

from pathlib import Path

# Project-root-relative docs file (tests/unit/ → repo root).
DOC_FILE: Path = Path(__file__).resolve().parents[2] / "docs" / "mcps-gateway.md"

#: The kill switch (daemon/services/mcps_gateway/config.py — DEFAULT OFF).
KILL_SWITCH_ENV = "MCPS_GATEWAY_ENABLED"

#: The default route path (config.DEFAULT_PATH).
DEFAULT_ROUTE_PATH = "/api/mcps-gateway/ws"

#: Every knob's documented default, transcribed from
#: ``daemon/services/mcps_gateway/config.py`` (12 knobs — 11 plus
#: ``MCPS_GATEWAY_PING_INTERVAL_MS``; the ``MCPS_GATEWAY_AUDIT_PATH``
#: knob was REMOVED from the implementation — the sink is DB-only and
#: unconditional, and the doc must not resurrect the dead knob). Keys are
#: the env var names; values are the strings the doc's knob table must
#: carry in its Default column (``*(empty)*`` is how the doc renders an
#: empty-string default).
ENV_DEFAULTS: dict[str, str] = {
    "MCPS_GATEWAY_ENABLED": "false",
    "MCPS_GATEWAY_PATH": "/api/mcps-gateway/ws",
    "MCPS_GATEWAY_TOKENS": "*(empty)*",
    "MCPS_GATEWAY_ALLOWED_MACHINES": "*(empty)*",
    "MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS": "15000",
    "MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS": "45000",
    "MCPS_GATEWAY_CLIENT_DEAD_DETECT_MS": "45000",
    "MCPS_GATEWAY_COMMAND_TIMEOUT_MS": "30000",
    "MCPS_GATEWAY_SESSION_TTL_MS": "3600000",
    "MCPS_GATEWAY_SESSION_IDLE_TIMEOUT_MS": "300000",
    "MCPS_GATEWAY_PING_INTERVAL_MS": "200",
    "MCPS_GATEWAY_AUDIT_ENABLED": "true",
}


def _doc_text() -> str:
    """Read the runbook from disk (no caching — each test re-reads)."""
    assert DOC_FILE.exists(), f"docs file missing: {DOC_FILE}"
    return DOC_FILE.read_text(encoding="utf-8")


def _doc_prose() -> str:
    """The runbook with every whitespace run collapsed to one space.

    The doc hard-wraps prose at ~72 columns, so a needle that spans a line
    break would silently miss. Prose needles are matched against THIS
    view; single-line table rows match identically in either view.
    """
    return " ".join(_doc_text().split())


# ── positive needles ────────────────────────────────────────────────────


def test_doc_exists_and_is_substantial() -> None:
    """The runbook exists and is not a stub."""
    text = _doc_text()
    assert len(text) > 5000, (
        f"{DOC_FILE.name} is suspiciously short ({len(text)} chars) — "
        "the operations runbook should cover enablement, posture, tools, "
        "conformance, gaps and troubleshooting."
    )
    for section in (
        "## 1. What this is",
        "## 2. Enablement & activation",
        "## 3. Security posture",
        "## 4. The four tools",
        "## 5. Protocol conformance summary",
        "## 6. Known gaps",
        "## 7. Troubleshooting",
    ):
        assert section in text, f"missing section heading: {section!r}"


def test_doc_states_the_enabled_flag_and_its_default_off() -> None:
    """The kill-switch env var is named and pinned DEFAULT OFF."""
    text = _doc_text()
    assert KILL_SWITCH_ENV in text, (
        f"the kill-switch env var {KILL_SWITCH_ENV} must be named in the "
        "runbook — an operator cannot discover the switch otherwise."
    )
    assert "DEFAULT OFF" in text, (
        "the doc must state the kill switch is DEFAULT OFF in those "
        "words — the strongest form of the statement."
    )


def test_doc_states_the_route_is_not_registered_when_off() -> None:
    """The doc must say the route does not exist at all while off."""
    text = _doc_text()
    assert "NOT REGISTERED AT ALL" in text, (
        "the doc must state unambiguously that while the switch is off "
        "the WebSocket route is NOT REGISTERED AT ALL — not 'registered "
        "but rejecting'."
    )
    assert "404" in text, "the doc must name the 404 the operator sees."


def test_doc_states_activation_requires_a_daemon_restart() -> None:
    """The doc must say plainly that activation needs a restart."""
    text = _doc_prose()
    assert "activation requires a daemon restart" in text, (
        "the doc must carry the plain statement that enabling the env "
        "var is not enough — route registration happens at boot."
    )
    assert "not hot-reloaded" in text.lower(), (
        "the doc must state that env changes are not hot-reloaded for "
        "route registration."
    )


def test_doc_pins_the_route_path() -> None:
    """The default route path is pinned verbatim."""
    text = _doc_text()
    assert DEFAULT_ROUTE_PATH in text, (
        f"the default route path {DEFAULT_ROUTE_PATH} must appear "
        "verbatim — the client-side CenterURL must include it (§2.4)."
    )


def test_doc_pins_every_env_var_and_its_documented_default() -> None:
    """Every knob appears with the default the code actually ships.

    The needle is the knob-table row prefix ``| `NAME` | `default` |`` so
    a silent default change (or a dropped row) goes red here.
    """
    text = _doc_text()
    missing: list[str] = []
    for name, default in ENV_DEFAULTS.items():
        needle = f"| `{name}` | `{default}` |" if default != "*(empty)*" else (
            f"| `{name}` | *(empty)* |"
        )
        if needle not in text:
            missing.append(f"{name} → {default!r}")
    assert not missing, (
        "knob-table rows drifted from the code defaults "
        "(transcribed from daemon/services/mcps_gateway/config.py): "
        + "; ".join(missing)
    )


def test_doc_states_empty_tokens_mean_nobody_may_enroll() -> None:
    """The fail-closed token semantics are pinned."""
    text = _doc_prose().lower()
    assert "empty means nobody may enroll" in text, (
        "the doc must state the fail-closed rule: an empty "
        "MCPS_GATEWAY_TOKENS refuses every upgrade."
    )
    assert "fail closed" in text, "the words 'fail closed' must appear."


def test_doc_states_the_bearer_token_is_checked_constant_time() -> None:
    """The constant-time comparison is stated (rotation = edit + restart)."""
    text = _doc_prose()
    assert "constant time" in text.lower(), (
        "the doc must state the token comparison is constant time."
    )
    assert "hmac.compare_digest" in text, (
        "the doc must name hmac.compare_digest — the mechanism, not just "
        "the adjective."
    )


def test_doc_states_only_the_401_is_fatal_to_the_client() -> None:
    """The 401-vs-403 operational hazard is pinned."""
    text = _doc_prose()
    assert "Only the 401 is fatal to the client" in text, (
        "the doc must say only a 401 is fatal to the shipped client — "
        "a 403 would loop it forever."
    )
    assert "Never 403" in text, "the doc must carry the 'Never 403' rule."


def test_doc_states_mtls_not_implemented() -> None:
    """The doc states mTLS is absent (positive form: it says NOT implemented)."""
    text = _doc_prose()
    assert "mTLS / client certificates are NOT implemented in v0.1" in text, (
        "the doc must carry the explicit sentence that mTLS is not "
        "implemented in v0.1."
    )
    assert "planned for v0.2" in text, "the doc must name v0.2 as the mTLS tier."


def test_doc_states_the_notification_relay_is_not_implemented() -> None:
    """The doc states the notification relay is absent (positive form)."""
    text = _doc_prose()
    assert (
        "`notifications/*` server→client relay is NOT implemented in v0.1" in text
    ), (
        "the doc must carry the explicit sentence that the notification "
        "relay is not implemented in v0.1."
    )
    assert "`notifications: false`" in text or "notifications: false" in text, (
        "the doc must show the center advertises notifications: false — "
        "the honest advertisement."
    )


def test_doc_states_the_per_tool_acl_is_v02() -> None:
    """TOOL_DENIED unreachability and the allowlist tier are pinned."""
    text = _doc_prose()
    assert "TOOL_DENIED" in text, (
        "the doc must name TOOL_DENIED as reserved for v0.2+ and never "
        "emitted in v0.1."
    )
    assert "never emitted" in text, (
        "the doc must say TOOL_DENIED is never emitted in v0.1."
    )
    assert "no deny-list knob" in text, (
        "the doc must state there is no deny-list knob in v0.1."
    )


def test_doc_states_the_privileged_category_and_agent_gate() -> None:
    """The two-layer tool authority is pinned."""
    text = _doc_text()
    assert "PRIVILEGED_TOOL_CATEGORIES" in text, (
        "the doc must name the privileged-category gate."
    )
    assert '"ari"' in text and '"leader"' in text, (
        "the doc must name ari and leader as the authorized agents."
    )


def test_doc_states_all_four_tools_refuse_while_disabled() -> None:
    """R2: the enabled assertion covers roster and disconnect too.

    An earlier build gated only connect/command, so the roster tool
    looked healthy while the transport was dead. The doc must state all
    FOUR tools refuse.
    """
    text = _doc_prose()
    assert "All four `mcp_rpc_*` tools refuse while the gateway is disabled" in text, (
        "the doc must state all four tools refuse while the gateway is "
        "disabled — the roster/disconnect gap was fixed (R2)."
    )


def test_doc_states_the_machine_not_enrolled_disambiguation() -> None:
    """R3: MACHINE_NOT_ENROLLED carries two meanings, split by `enrolled`."""
    text = _doc_prose()
    assert "reconnect pending" in text, (
        "the doc must quote the 'enrolled but currently offline — "
        "reconnect pending' refusal (R3)."
    )
    assert "re-enrollment is required" in text, (
        "the doc must quote the 'not in the enrolled roster — "
        "re-enrollment is required' refusal (R3)."
    )
    assert "branch on the `enrolled` detail" in text, (
        "the doc must tell the operator to branch on the enrolled detail."
    )


def test_doc_pins_the_graceful_retire_recipe() -> None:
    """§7.1: the 401-only-fatal reconnect-loop trap and the two ways down."""
    text = _doc_prose()
    assert "Only an HTTP 401 is fatal to the remote client" in text, (
        "the doc must state only the 401 is fatal — everything else "
        "reconnect-loops forever."
    )
    assert "Graceful retire / pause — clear the tokens" in text, (
        "the doc must carry the graceful retire recipe (clear "
        "MCPS_GATEWAY_TOKENS → every upgrade 401s → clients stop cleanly)."
    )
    assert "Hard kill — `MCPS_GATEWAY_ENABLED=false`" in text, (
        "the doc must name ENABLED=false as the hard kill switch with "
        "reconnect loops expected."
    )
    assert "awaits operator re-enrollment" in text, (
        "the doc must say a 401'd machine awaits operator re-enrollment."
    )


def test_doc_states_the_audit_sink_is_the_db_table() -> None:
    """The audit landing zone and the args-hashing rule are pinned."""
    text = _doc_prose()
    assert "mcp_gateway_audit" in text, (
        "the doc must name the mcp_gateway_audit table as the sink."
    )
    assert "no arguments column" in text, (
        "the doc must state the table has no arguments column at all."
    )
    assert "no file-sink knob" in text, (
        "the doc must state the DB sink is unconditional — there is no "
        "file-sink knob anymore."
    )


def test_doc_states_the_client_url_must_include_the_path() -> None:
    """The client-side CenterURL pitfall is pinned."""
    text = _doc_prose()
    assert "must include the gateway path" in text, (
        "the doc must say the configured center URL must include the "
        "gateway path — the client appends its own default only when the "
        "URL has none."
    )
    assert f"wss://center.example:8443{DEFAULT_ROUTE_PATH}" in text, (
        "the doc must show the example URL with the path appended."
    )


def test_doc_pins_the_ping_interval_knob_and_its_rationale() -> None:
    """D1: MCPS_GATEWAY_PING_INTERVAL_MS, default 200, authority-matched.

    The knob is a WebSocket CONTROL frame (not a protocol message) and is
    the gateway's liveness evidence to the client; the default matches the
    protocol authority's reference center and exists to be lengthened.
    """
    text = _doc_prose()
    assert "`MCPS_GATEWAY_PING_INTERVAL_MS`" in text, (
        "the doc must name the ping-interval knob."
    )
    assert "CONTROL frame" in text or "control frame" in text, (
        "the doc must say the ping is a WebSocket control frame, not a "
        "protocol message."
    )
    assert "liveness evidence to the" in text, (
        "the doc must say the ping is the gateway's liveness evidence to "
        "the client."
    )
    assert "protocol authority's reference center" in text, (
        "the doc must state the 200 ms default matches the protocol "
        "authority's reference center."
    )


def test_doc_states_the_sweep_exclusion_is_defence_in_depth() -> None:
    """D2 (review finding #8): accurate credit for the mcp-sweep exclusion.

    The doc must state the sweep path was UNREACHABLE dead code in
    production — no agent was actually exposed — and the exclusion is
    defence-in-depth verified to change zero existing toolsets.
    """
    text = _doc_prose()
    assert "defence-in-depth" in text or "defense-in-depth" in text, (
        "the doc must frame the sweep exclusion as defence-in-depth."
    )
    assert "unreachable dead code in production" in text, (
        "the doc must say the sweep path was unreachable dead code in "
        "production."
    )
    assert "no agent was actually exposed" in text, (
        "the doc must state no agent was actually exposed."
    )
    assert "change zero existing toolsets" in text, (
        "the doc must state the exclusion was verified to change zero "
        "existing toolsets."
    )


def test_doc_pins_the_conformance_suite_invocation() -> None:
    """D3: where the conformance suite lives and how to run it."""
    text = _doc_prose()
    assert "tests/integration/mcps_gateway" in text, (
        "the doc must name the integration suite directory."
    )
    assert (
        "uv run python -m pytest tests/integration/mcps_gateway -m "
        "integration" in text
    ), (
        "the doc must carry the exact marker-gated invocation command."
    )
    assert "NOT in the default suite" in text or "not in the default suite" in text, (
        "the doc must say the suite is excluded from default runs (repo "
        "convention)."
    )
    assert "tripwires" in text, (
        "the doc must mention the unit-suite literal tripwires for the "
        "wire codes."
    )


def test_doc_states_audit_detail_is_center_authored() -> None:
    """D4(a): peer-authored error text never enters the audit ledger."""
    text = _doc_prose()
    assert "peer-authored error text never enters the audit ledger" in text, (
        "the doc must state peer error text stays out of the ledger."
    )
    assert "center-authored classification" in text, (
        "the doc must say the audit detail is a fixed center-authored "
        "classification."
    )


def test_doc_states_machine_busy_is_atomic_and_center_side() -> None:
    """D4(b): the busy refusal is atomic, by the center, never delegated."""
    text = _doc_prose()
    assert "refusal is **atomic**" in text, (
        "the doc must say the MACHINE_BUSY refusal is atomic."
    )
    assert "never delegated to the machine" in text, (
        "the doc must say the refusal is not delegated to the machine's "
        "own concurrent-session.begin refusal."
    )


def test_doc_states_the_audit_write_is_bounded() -> None:
    """D4(c): the audit write is bounded (~5 s); the command still returns."""
    text = _doc_prose()
    assert "bounded (~5 s)" in text, (
        "the doc must state the audit write bound."
    )
    assert "still returns" in text, (
        "the doc must say the command result still returns on audit "
        "timeout."
    )


def test_doc_states_the_pre_hello_deadline() -> None:
    """D4(d): an upgraded socket that never sends hello is disconnected."""
    text = _doc_prose()
    assert "pre-hello deadline" in text, (
        "the doc must state the pre-hello disconnect deadline."
    )
    assert "never sends `hello`" in text, (
        "the doc must name the never-hello case."
    )


def test_doc_states_reconnect_ends_the_old_session_immediately() -> None:
    """D4(e): reconnect ends the old connection's session immediately."""
    text = _doc_prose()
    assert "ends the old connection's session **immediately**" in text, (
        "the doc must say a reconnect ends the old session immediately."
    )


def test_doc_states_wrong_version_closes_the_socket() -> None:
    """D4(f): a missing/wrong `v` on an inbound frame closes the socket."""
    text = _doc_prose()
    assert "socket closed" in text, (
        "the doc must say a wrong/missing version frame gets the socket "
        "closed."
    )
    assert "mirroring the client's own behavior" in text, (
        "the doc must note this mirrors the client's own behavior."
    )


def test_doc_states_session_not_found_data_shape() -> None:
    """D4(g), final shape: SESSION_NOT_FOUND data has two forms.

    The code (``sessions.py`` ``session_not_found`` /
    ``no_session_for_agent``) carries the REAL session id when one
    exists; when the agent holds no session at all, ``data.session_id``
    is ABSENT and the agent is named in its own ``data.agent_id``
    member. The contract member is never filled with an agent id.
    """
    text = _doc_prose()
    assert "real session id" in text, (
        "the doc must say data.session_id carries the real session id "
        "when one exists."
    )
    assert "no session at all" in text, (
        "the doc must name the no-session-at-all case."
    )
    assert "`data.session_id` is **absent**" in text or (
        "data.session_id` is **absent**" in text
    ), (
        "the doc must say data.session_id is absent when the agent holds "
        "no session."
    )
    assert "never filled with an agent id" in text, (
        "the doc must state the session_id member is never filled with "
        "an agent id."
    )


# ── negative needles (SEPARATE test functions — never mix with the
#    positive needles above) ────────────────────────────────────────────


def test_doc_does_not_claim_mtls_is_implemented() -> None:
    """The runbook must not claim mTLS exists in v0.1.

    (The positive counterpart — the doc DOES say it is not implemented —
    lives in ``test_doc_states_mtls_not_implemented``.)
    """
    text = _doc_prose()
    for phrase in (
        "mTLS is implemented",
        "mTLS is supported",
        "mTLS is enabled",
        "client certificates are verified",
        "client certificates are required",
        "client_cert is advertised true",
    ):
        assert phrase not in text, (
            f"the runbook must not claim {phrase!r} — mTLS is a v0.2 "
            "tier and the center advertises client_cert: false."
        )


def test_doc_does_not_claim_the_notification_relay_is_implemented() -> None:
    """The runbook must not claim the notification relay exists in v0.1.

    (The positive counterpart lives in
    ``test_doc_states_the_notification_relay_is_not_implemented``.)
    """
    text = _doc_prose()
    for phrase in (
        "notification relay is implemented",
        "notifications are relayed",
        "relays MCP notifications",
        "notifications: true",
    ):
        assert phrase not in text, (
            f"the runbook must not claim {phrase!r} — the v0.1 center "
            "relays no notification and advertises the capability false."
        )


def test_doc_does_not_claim_the_listener_is_on_by_default() -> None:
    """The runbook must not tell the operator the listener is on.

    (The positive counterpart — DEFAULT OFF — lives in
    ``test_doc_states_the_enabled_flag_and_its_default_off``.)
    """
    lowered = _doc_prose().lower()
    for phrase in (
        "on by default",
        "enabled by default",
        "active by default",
        "listening by default",
        "registered by default",
    ):
        assert phrase not in lowered, (
            f"the runbook must not say the gateway is {phrase!r} — "
            "MCPS_GATEWAY_ENABLED is DEFAULT OFF and the route is not "
            "registered at all while it is off."
        )


def test_doc_does_not_mention_the_removed_audit_path_knob() -> None:
    """The runbook must not resurrect ``MCPS_GATEWAY_AUDIT_PATH``.

    The knob was REMOVED from the implementation (R1): the audit sink is
    the DB table, unconditionally — no file sink exists, so no such env
    var may appear in the operator-facing knob documentation.
    """
    text = _doc_prose()
    for phrase in ("MCPS_GATEWAY_AUDIT_PATH", "AUDIT_PATH"):
        assert phrase not in text, (
            f"the runbook still mentions {phrase!r} — the knob was "
            "removed; the sink is DB-only. Drop it from the knob table "
            "and every mention."
        )


def test_doc_does_not_overstate_the_pre_fix_sweep_exposure() -> None:
    """D2 negative arm: the doc must NOT claim the pre-fix sweep exposed
    agents in production (review finding #8 — it was unreachable dead
    code, so no agent was actually exposed).

    (The positive counterpart — the defence-in-depth framing — lives in
    ``test_doc_states_the_sweep_exclusion_is_defence_in_depth``.)
    """
    text = _doc_prose()
    for phrase in (
        "would otherwise claim the `mcp_rpc_*` names",
        "would have handed the remote-desktop surface",
        "silently hand the remote-desktop surface",
        "exposed every agent",
        "every agent was exposed",
        "agents were actually exposed",
    ):
        assert phrase not in text, (
            f"the runbook must not claim {phrase!r} — the pre-fix sweep "
            "path was unreachable dead code in production; the exclusion "
            "is defence-in-depth of a latent lane, not a live hole."
        )


# ── Phase C: the operator read surface ─────────────────────────────────────
#
# The read surface is a SECOND, separate lane next to the module-lane
# WebSocket transport. These pins keep the doc honest about the facts
# an operator (and the frontend developer) types against: the three
# paths, the unconditional registration of the READ routes (and the
# still-conditional transport), the scope name, and the pagination
# bounds.

READ_ENDPOINTS = (
    "/api/mcps-gateway/machines",
    "/api/mcps-gateway/sessions",
    "/api/mcps-gateway/audit",
)


def test_doc_names_the_three_read_endpoints() -> None:
    """The read surface's paths are pinned."""
    text = _doc_prose()
    for endpoint in READ_ENDPOINTS:
        assert endpoint in text, (
            f"the runbook must name the read endpoint {endpoint!r} — the "
            "frontend developer types against these paths."
        )


def test_doc_states_the_read_routes_are_registered_unconditionally() -> None:
    """The read routes are registered ALWAYS; only the TRANSPORT is
    gated. The doc must keep the two apart."""
    text = _doc_prose()
    assert "registered **unconditionally**" in text or (
        "registered unconditionally" in text
    ), (
        "the runbook must state that the read routes are registered "
        "unconditionally — the page needs a stable contract while the "
        "transport is off."
    )
    assert "gateway_enabled: false" in text, (
        "the runbook must state the degraded shape the page sees while "
        "the gateway is off (empty lists + the gateway_enabled flag)."
    )


def test_doc_states_the_read_scope_and_the_pagination_bounds() -> None:
    """The auth lane and the pagination bounds are pinned."""
    text = _doc_prose()
    assert "mcp:read" in text, (
        "the runbook must name the scope the read routes require."
    )
    assert "SCOPE_INSUFFICIENT" in text, (
        "the runbook must name the 403 the page sees for a credential "
        "without the scope."
    )
    assert "limit` default 50, max 200" in text, (
        "the runbook must pin the pagination bounds (limit default 50, "
        "max 200; offset default 0) — they are the validation contract "
        "of the audit tail."
    )


# ── Phase C: the management page (the frontend half) ─────────────────────
#
# §9 documents the READ-ONLY Angular page that renders the §8 read
# surface: what it shows, how to run it, the polling + pagination
# contract and the degraded states. These pins keep the operator doc
# honest about the facts a frontend developer types against — the SPA
# route, the poll cadence, the read-only scope, the dev proxy and the
# three degraded states.
#
# CONVENTION (unchanged): positive and negative needles NEVER share a
# test function.

FE_ROUTE = "/mcps-gateway"


def test_doc_names_the_management_page_section() -> None:
    """§9 exists and carries the pinned heading."""
    text = _doc_text()
    assert "## 9. The management page (Phase C)" in text, (
        "the runbook must carry the §9 heading — the management page is "
        "the operator-facing half of Phase C."
    )


def test_doc_pins_the_management_page_route() -> None:
    """The SPA route of the page is pinned."""
    text = _doc_prose()
    assert FE_ROUTE in text, (
        f"the runbook must name the frontend route {FE_ROUTE} — the page "
        "is reachable from the gear menu only (no top-level header link)."
    )


def test_doc_states_the_management_page_is_read_only() -> None:
    """v1 has no write control — the doc must say so, and enumerate them."""
    text = _doc_prose().lower()
    assert "read-only" in text, (
        "the doc must state the management page is read-only."
    )
    assert "no kill, no lock release, no disconnect" in text, (
        "the doc must enumerate the write controls the page does NOT "
        "have (kill / lock release / disconnect) — the gateway is driven "
        "by the agent tools, never by the browser."
    )


def test_doc_states_the_management_page_polls_every_ten_seconds() -> None:
    """The poll cadence is pinned."""
    text = _doc_prose()
    assert "polls every **10 s**" in text, (
        "the doc must pin the page's polling cadence (10 s) — a frontend "
        "developer needs the number, not a vibe."
    )


def test_doc_states_the_management_page_pagination_contract() -> None:
    """The page size + the next/prev guards are pinned."""
    text = _doc_prose()
    assert "page size of 50" in text, (
        "the doc must pin the audit page size the page requests (50)."
    )
    assert "count === limit" in text, (
        "the doc must state the Next guard (a full page) — the audit "
        "count is the page length, not the ledger total."
    )
    assert "offset > 0" in text, (
        "the doc must state the Previous guard (a non-zero offset)."
    )


def test_doc_states_the_management_page_dev_run_and_proxy() -> None:
    """How to run the page in dev — and where the API goes."""
    text = _doc_prose()
    assert "cd frontend && npm start" in text, (
        "the doc must carry the dev invocation of the page."
    )
    assert "http://localhost:8079" in text, (
        "the doc must name the dev proxy target — the frontend uses "
        "relative /api URLs (no hard-coded host)."
    )
    assert "frontend/proxy.conf.json" in text, (
        "the doc must name the proxy config that carries the forwarding."
    )


def test_doc_states_the_management_page_degraded_states() -> None:
    """The four degraded states the operator can meet on the page."""
    text = _doc_prose()
    assert "gateway_enabled: false" in text, (
        "the doc must state the gateway-disabled shape the page renders."
    )
    assert "gateway_unavailable" in text, (
        "the doc must name the 503 the page renders as an error banner."
    )
    assert "SCOPE_INSUFFICIENT" in text, (
        "the doc must name the 403 the page renders."
    )
    assert "Token lacks required scope (needs mcp:read)" in text, (
        "the doc must carry the page's own 403 message — the server's "
        "message with its required scope list (D3a replaced the old "
        "hard-coded 'credential lacks mcp:read' label)."
    )
    assert "degraded: true" in text, (
        "the doc must name the 200+flag degraded shape the page renders "
        "as a DISTINCT banner (D2's fourth degraded state)."
    )


def test_doc_does_not_claim_the_management_page_can_write() -> None:
    """NEGATIVE arm — the page is read-only in v1.

    (The positive counterpart — the doc DOES say the page is read-only —
    lives in ``test_doc_states_the_management_page_is_read_only``.)
    """
    text = _doc_prose().lower()
    for phrase in (
        "click to kill",
        "release the lock",
        "disconnect button",
        "terminate a session from the page",
    ):
        assert phrase not in text, (
            f"the runbook must not advertise {phrase!r} — the management "
            "page is read-only in v1; the write controls arrive with the "
            "v0.2 per-tool ACL work."
        )
