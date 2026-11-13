"""Tool registry for storing full documentation.

This module provides a registry for tool documentation that allows
tools to have short docstrings (for LLM context efficiency) while
still providing detailed documentation via the tool_help() function.
"""

from importlib import import_module
from typing import Callable, Any
import logging

_logger = logging.getLogger(__name__)

# Global registry: tool_name -> full documentation string
_full_docs: dict[str, str] = {}

# Tool metadata registry: tool_name -> {category, short_doc, full_doc}
_tool_metadata: dict[str, dict[str, Any]] = {}

# These tools are created by per-instance factories rather than registered during
# module import. They are nevertheless valid names in agent allow/deny config,
# so startup validation must know them before an instance is built.
DYNAMIC_TOOL_NAMES: frozenset[str] = frozenset({
    "rag_insert_text",
    "rag_insert_texts",
    "rag_query",
    "rag_query_data",
    "rag_search_labels",
    "rag_get_graph",
    "rag_create_entity",
    "rag_get_entity",
    "rag_create_relation",
    "rag_update_entity",
    "rag_merge_entities",
    "rag_delete_entity",
    "rag_delete_relation",
    "rag_delete_docs",
    "rag_list_docs",
    "rag_track_status",
    # db_* tools are created by create_db_tools() per-instance factory, not
    # registered at import time; see daemon/tools/db_tools.py.
    "db_conn_add",
    "db_conn_delete",
    "db_conn_list",
    "db_conn_test",
    "db_postgres_dml_select",
    "tool_help",
    "explore",
    "experience",
    "ens_system_log_list",
    "ens_system_log_read",
    "ens_system_log_search",
    "ens_system_log_tail",
    "commit_docs_validated",
    "doc_write",
    "comment_edit",
    "skill_search",
    "shared_meta_kv",
    # P0 job visibility tools — created by create_job_tools() factory
    "job_messages",
    "job_tree",
    "job_progress",
    "job_inject",
    # Mission tools (M2 of mission-class, 2026-09-02) — created by
    # ``create_mission_tools()`` factory in ``daemon/tools/missions.py``.
    # These are READ-ONLY readers of the mission projection; census
    # untouched.
    "get_mission",
    "await_mission",
    "list_missions",
    # ari-tools Phase 1 (the Workbook) — the board tools are created
    # by the create_board_tools() factory
    # (daemon/tools/board_tools.py). The ``board`` category is
    # PRIVILEGED (entry #8 of PRIVILEGED_TOOL_CATEGORIES): the
    # empty-allow inherit universe must NOT auto-grant write
    # authority over the global board. Explicit
    # ``tools.allow: ["board"]`` is the only access path.
    "board_create",
    "board_update",
    "board_list",
    "board_get",
    # ari-tools Phase 2 (the self-alarm wrapper) — the self_alarm tool is
    # created by the create_self_alarm_tools() factory
    # (daemon/tools/self_alarm.py). The ``self-alarm`` category is
    # PRIVILEGED (entry #9 of PRIVILEGED_TOOL_CATEGORIES): the
    # empty-allow inherit universe must NOT auto-grant the power to arm
    # an alarm whose text a fresh Ari instance executes verbatim. An
    # explicit ``tools.allow: ["self-alarm"]`` entry (ari only) is the
    # only access path.
    "self_alarm",
    # nest-ops (2026-10-21) — the instance fleet surfaces for the
    # maintenancer. Four read tools (instance_list / instance_get /
    # instance_health / fleet_capacity_summary, created by the
    # create_instance_read_tools() factory in
    # daemon/tools/instance_read.py) and six act tools
    # (instance_terminate / instance_reclaim / instance_nudge /
    # instance_mark / instance_answer / instance_dismiss, created by
    # the create_instance_ops_tools() factory in
    # daemon/tools/instance_ops.py).
    "instance_list",
    "instance_get",
    "instance_health",
    "fleet_capacity_summary",
    # F1 (nest-ops shakedown, 2026-10-24): the ratified rename's name —
    # an ADDITIVE ALIAS of fleet_capacity_summary (a second literal
    # @tool closure in create_instance_read_tools, same ONE builder, so
    # the payloads cannot drift). The live name is unchanged.
    "instance_fleet_summary",
    "instance_terminate",
    "instance_reclaim",
    "instance_nudge",
    "instance_mark",
    "instance_answer",
    "instance_dismiss",
    # ari-tools Phase 3 (the live-views surface) — the view_write tool is
    # created by the create_view_write_tools() factory
    # (daemon/tools/view_write.py). The ``view-write`` category is
    # PRIVILEGED (entry #10 of PRIVILEGED_TOOL_CATEGORIES): the
    # empty-allow inherit universe must NOT auto-grant write authority
    # over the shared, customer-facing ``reports`` root. Explicit
    # ``tools.allow: ["view-write"]`` on ari is the only access path
    # (ARI-ONLY pre-ruling); the tool body ALSO carries the runtime
    # agent_id gate.
    "view_write",
    # ari-tools Phase 3 (the live board view) — the board_link tool is
    # created by the create_board_link_tools() factory
    # (daemon/tools/board_link.py). The ``board-link`` category is
    # PRIVILEGED (entry #11 of PRIVILEGED_TOOL_CATEGORIES): minting the
    # board's passcode authorises reading the user's whole work board.
    # Explicit ``tools.allow: ["board-link"]`` on ari is the only
    # access path (ARI-ONLY — user ruling 2026-10-20); the tool body
    # ALSO carries the runtime agent_id gate.
    "board_link",
    # wedge-capture (2026-10-22): the two READ-ONLY capture-evidence
    # tools — created by the create_wedge_capture_tools() factory
    # (daemon/tools/wedge_capture.py). Factory-built (not import-time
    # registered), so they belong in DYNAMIC_TOOL_NAMES and
    # KNOWN_TOOL_NAMES. The category is PRIVILEGED (entry #16 of
    # PRIVILEGED_TOOL_CATEGORIES) — opt-in only, never default-granted;
    # the runtime agent gate lives in the tool bodies.
    "wedge_capture_list",
    "wedge_capture_get",
    # ari-voice (2026-10-25): the notify tool — created by the
    # create_notify_user_tools() factory (daemon/tools/notify_user.py).
    # Factory-built (not import-time registered), so it belongs in
    # DYNAMIC_TOOL_NAMES and KNOWN_TOOL_NAMES. The category is
    # PRIVILEGED (entry #16) — opt-in only, never default-granted; the
    # ari-only runtime gate lives in the tool body. The category key is
    # EXACTLY "notify-user" (N7) — never "notify".
    "notify_user",
    # system_upgrade tools — created by create_upgrade_tools() factory
    # (daemon/tools/upgrade_tools.py, P2.2 Dispatch A read pair + Dispatch B
    # actor pair).
    "release_info",
    "upgrade_status",
    "system_restart",
    "system_upgrade",
    # Leader completion attestation (Phase 1 of LCA feature, 2026-09-05) —
    # created by create_attestation_tools() factory
    # (daemon/tools/attestation.py). Single no-arg idempotent tool; the
    # attestation is the tool-call's presence in the message stream.
    "attest_completion",
    # ens-db tools (W1-P2, task 2.4) — created by create_ens_db_tools()
    # factory (daemon/tools/ens_db_tools.py). Privileged category —
    # ``ens_db_*`` tools reach the daemon's own ``ensemble_prod`` DB on
    # the SHARED engine (read path) or a dedicated 2+3 pool (repair
    # writer). Direct DB access is gated by ``PRIVILEGED_TOOL_CATEGORIES``
    # so it is opt-in only via an explicit ``tools.allow`` entry naming
    # ``ens-db`` (architect §4.1 — R-SR16).
    "ens_db_postgres_select",
    "ens_db_inspect",
    "ens_db_repair_execute",
    "ens_db_pool_status",
    # Agent Snapshot tools (agent-snapshot v1 Wave 2b, PR6) — created by
    # create_snapshot_tools() factory (daemon/tools/snapshot_tools.py).
    # snapshot_create + snapshot_search ship in the "snapshot" category
    # (per-tool grants for the creator agents). Warm-start CONSUMPTION
    # is not a tool since unify-spawn-tools (2026-10-05): spawn_instance
    # (the single spawn tool, "instance" category) consumes the snapshot
    # helpers directly, gated by the TARGET agent's snapshot_enabled
    # meta flag — spawn_hot_instance was removed.
    "snapshot_create",
    "snapshot_search",
    # service tools (service-tool Phase 1, 2026-09-15) — created by
    # create_service_tools() factory (daemon/tools/service_tools.py).
    # Default-enabled per override 2026-09-16 (see
    # .agents/shared/planning/service-tool/decisions.md §D4 override
    # note — D4 Option A reversed): the five ``service_*`` tools spawn
    # persistent detached OS processes that survive instance
    # termination AND daemon restart. ``service`` is NOT in
    # ``PRIVILEGED_TOOL_CATEGORIES`` — the default-open universe
    # grants it to every agent, and explicit-allow agents carry it via
    # ``tools.allow``. Global kill-switch
    # ``ENSEMBLE_SERVICE_TOOL_ENABLED=0`` is the unconditional off.
    "service_start",
    "service_stop",
    "service_status",
    "service_list",
    "service_logs",
    # agent-secrets P1 tools (2026-10-17, plan f11965039) — created
    # by create_secret_tools() factory
    # (daemon/tools/secrets_tools.py). Six factory-built tools behind
    # the ``secrets`` privileged category (#6 in
    # ``PRIVILEGED_TOOL_CATEGORIES`` — quintet → sextet per the
    # Q1/Q2/Q3 rulings; ``cu``, which joined 2026-10-16 before
    # this entry, was REMOVED 2026-10-24). Day-one allow-list is
    # ``worker`` /
    # ``tester`` / ``leader`` / ``ari``; agents outside that list
    # get ZERO of these tools through both the
    # ``_strip_privileged_category_tools`` default-deny strip
    # (``daemon/tools/instance.py:6169``) AND the
    # ``resolve_tool_filter`` empty-allow branch
    # (``daemon/tools/instance.py:373``). The trust ruling (plan
    # §1.2) is unconditional — secret-given = trusted for
    # autonomous use; per-agent ``tools.allow`` is the ONLY access
    # boundary. Names are flat snake_case (the ``kms_attach`` /
    # ``ens_env_read`` precedent).
    "secret_generate_ssh_keypair",
    "secret_generate_api_token",
    "secret_store",
    "secret_list",
    "secret_read_public",
    "secret_retire",
    # settings-worker-slots P2 tools (2026-10-18) — created by
    # create_settings_tools() factory
    # (daemon/tools/settings_tools.py). Three factory-built tools
    # behind the ``settings`` privileged category (#7 in
    # ``PRIVILEGED_TOOL_CATEGORIES`` — sextet → septet; ``secrets``
    # joined 2026-10-17 before this entry). The ari + leader
    # meta.json allow entries landed 2026-10-18 (P3); the category
    # remains opt-in-only (NEVER default-granted). HONEST contract:
    # the tools never claim liveness — every write reports
    # ``applies_at: "next daemon restart"`` + ``live: false``.
    "settings_get",
    "settings_set",
    "settings_clear",
    # scheduling tools (scheduled-tasks phase 2+4) — created by
    # create_scheduling_tools() factory (daemon/tools/scheduling.py).
    # Non-privileged category — granted per-agent via tools.allow
    # (agents/ari, agents/leader, agents/jober). Without these entries,
    # startup validation of tools.allow would reject the category.
    "task_schedule",
    "task_schedule_list",
    "task_schedule_cancel",
    "task_schedule_update",
    # Plugin subsystem Port tools (slice ⑤ tier-1 wiring) — created by
    # the build_plugin_tools() factory
    # (daemon/plugin_subsystem/plugin_tool_factory.py) at instance-tool
    # assembly. They are runtime StructuredTools, NOT ``@tool``
    # functions in a category module, so they belong ONLY here: adding
    # them to ``KNOWN_TOOL_NAMES`` would break the frozen-binary drift
    # equality (test_known_tool_names_matches_source_exactly_no_drift
    # compares KNOWN_TOOL_NAMES against the pure source-``@tool``
    # universe). Per-agent visibility is the uniform tools.allow/deny
    # gate — designer's meta.json allow list carries the names.
    "od.generate",
    "od.compose_brief",
    "od.save",
    "od.lint",
    # remote-hands center gateway (2026-10-22) — the four tools of the
    # ``mcp_rpc`` category, created by the create_mcp_rpc_tools()
    # factory (daemon/tools/mcp_rpc_tools.py). The ``mcp_rpc`` category
    # is PRIVILEGED (entry #15 of PRIVILEGED_TOOL_CATEGORIES): the
    # empty-allow inherit universe must NOT auto-grant the authority to
    # drive a remote DESKTOP. Explicit ``tools.allow: ["mcp_rpc"]`` on
    # ari + leader is the only access path, and every tool body ALSO
    # carries its own ``agent_id in {"ari", "leader"}`` gate — a LITERAL
    # tool name in an allow list bypasses the category gate outright.
    "mcp_rpc_list_machine",
    "mcp_rpc_connect",
    "mcp_rpc_command",
    "mcp_rpc_disconnect",
    # metrics-read (2026-11-03, metrics-agent-tools lane): the three
    # READ-ONLY metric-registry tools — created by the
    # create_metrics_read_tools() factory (daemon/tools/metrics_read.py).
    # Factory-built (not import-time registered), so they belong in
    # DYNAMIC_TOOL_NAMES and KNOWN_TOOL_NAMES. The category is
    # PRIVILEGED (entry #17) — opt-in only, never default-granted; the
    # runtime agent gate lives in the tool bodies.
    "metrics_families",
    "metrics_snapshot",
    "metrics_history",
    # nest-report-read (2026-11-05, nest-anomaly-report lane): the TWO
    # READ-ONLY anomaly-store tools — created by the
    # create_nest_report_read_tools() factory
    # (daemon/tools/nest_report_read.py). Factory-built (not
    # import-time registered), so they belong in DYNAMIC_TOOL_NAMES and
    # KNOWN_TOOL_NAMES. The category is PRIVILEGED (entry #18) —
    # opt-in only, never default-granted; the runtime agent gate lives
    # in the tool bodies (the R8 ops trio).
    "anomaly_clusters",
    "anomaly_report_detail",
    # nest-report (2026-11-05, nest-anomaly-report lane): the ONE write
    # tool of the nest-anomaly surface — ``report_anomaly``, created by
    # the create_nest_report_tools() factory
    # (daemon/tools/nest_report.py). Factory-built (not import-time
    # registered), so it belongs in DYNAMIC_TOOL_NAMES and
    # KNOWN_TOOL_NAMES. The category is NOT privileged (R4): the
    # empty-allow inherit universe DOES grant it, and the explicit
    # ``tools.allow: ["nest-report"]`` grant lands in the phase-5
    # ceremony.
    "report_anomaly",
    # memoir (2026-11-05, memoir-store-v1 lane): the five memoir tools —
    # created by the create_memoir_tools() factory
    # (daemon/tools/memoir.py). Factory-built (not import-time
    # registered), so they belong in DYNAMIC_TOOL_NAMES and
    # KNOWN_TOOL_NAMES. The category is PRIVILEGED (entry #18) — opt-in
    # only, never default-granted; the ari-only runtime gate lives in
    # the tool bodies (DD-13 — the factory-captured agent id).
    "memoir_write",
    "memoir_pin",
    "memoir_unpin",
    "memoir_recall",
    "memoir_timeline",
    "memoir_about",
    # prompt-reload (2026-11-06, prompt-reload lane): the ONE
    # ari-self-service prompt refresh tool — created by the
    # create_prompt_reload_tools() factory
    # (daemon/tools/prompt_reload.py). Factory-built (not import-time
    # registered), so it belongs in DYNAMIC_TOOL_NAMES and
    # KNOWN_TOOL_NAMES. The category is PRIVILEGED (entry #20) —
    # opt-in only, never default-granted; the ari-only runtime gate
    # lives in the tool body (factory-captured agent_id).
    "prompt_reload",
    # prompt-assent (2026-11-10, prompt-assent lane): the conversational
    # approval lane — ONE tool, FOUR verbs (list / mint / approve /
    # reject). Created by the create_prompt_assent_tools() factory
    # (daemon/tools/prompt_assent.py). Factory-built (not import-time
    # registered), so it belongs in DYNAMIC_TOOL_NAMES and
    # KNOWN_TOOL_NAMES. The category is PRIVILEGED (entry #21) —
    # opt-in only, never default-granted; the ari-only runtime gate
    # lives in the tool body (factory-captured agent_id).
    "prompt_assent",
    # guidelines (2026-11-13, guideline-read-tool lane): the READ-ONLY
    # window onto the shipped guideline corpus. Created by the
    # create_guidelines_tools() factory (daemon/tools/guidelines.py).
    # Factory-built (not import-time registered), so both names belong
    # in DYNAMIC_TOOL_NAMES and KNOWN_TOOL_NAMES. The category is
    # PRIVILEGED (entry #22) — opt-in only, never default-granted; the
    # GUIDELINES_AUTHORIZED_AGENTS runtime gate lives in the tool
    # bodies (factory-captured agent_id; audience EXACTLY ari + leader
    # + devops + sonic-leader — the first privileged grant to a sonic
    # agent, ruling 2026-11-13).
    "guidelines_list",
    "guideline_read",
})


# Tool name prefixes that are dynamically discovered at runtime (e.g. via MCP
# servers) and therefore cannot be statically registered. Validation must skip
# these — the tool names are only known after the MCP server responds.
DYNAMIC_TOOL_PREFIXES: frozenset[str] = frozenset({
    "plane_",
})


# Categories that are OPT-IN-ONLY regardless of the empty-allow default.
#
# R-SR16 (P2.2 tool-api-design.md §3.5, architect-resolved 2026-08-22):
# ``resolve_tool_filter`` treats an absent/empty allow list as "everything
# is potentially allowed" — which would default-grant these categories to
# every agent created without an explicit allow-list (watcher is empty-allow
# today). Privileged categories NEVER join that default universe: an agent
# reaches them ONLY through an explicit ``tools.allow`` entry naming the
# category or one of its tools. Enforced structurally in
# ``daemon.tools.instance`` (the ``resolve_tool_filter`` empty-allow branch
# and the default-allow paths of ``_apply_tool_filter``) — no deny rules
# needed. The set is exactly three entries — daemon-internal authority
# semantics: ``system_upgrade`` (restart/upgrade authority),
# ``system-log`` (daemon log forensics — designated break-glass for the
# ``worker`` agent), ``ens-db`` (direct ``ensemble_prod`` read + guarded
# repair writer — maintenancer only).
#
# OVERRIDE 2026-09-16 (user decision): the ``service`` category was
# REMOVED from this set by user directive (D4 reversed) and is now
# default-enabled for any agent whose effective toolset can include
# ``bash`` or ``proc``. See
# ``.agents/shared/planning/service-tool/decisions.md`` §D4 override
# note. SECURITY TRADEOFF acknowledged: empty-allow agents and any
# bash/proc-capable agent gain process-group authority by default; the
# global kill-switch ``ENSEMBLE_SERVICE_TOOL_ENABLED=0`` remains the
# unconditional off. ``service`` no longer qualifies as
# daemon-internal authority and was moved out of this UNION; the set
# is still exact-pinned below (now a quartet: ``ens-env`` joined per
# the W4 leader decision, reviewer council 2026-10-02).
#
# SAME-PR RULE (D18/A14): the set is pinned by exact-equality asserts at
# every pin site — five inline ``frozenset({...})`` literals + the
# ``EXPECTED_PRIVILEGED_SET`` definitions — plus a
# ``len(PRIVILEGED_TOOL_CATEGORIES) == N`` count assertion at EACH
# phase-local pin — plus the mutation-pack find/replace STRING pairs
# (``test/packs/metrics_read_mutation_matrix_test.py`` MT-01 and the
# guidelines MT-G01 — both derived from the live constant at
# pack-import time so a count bump cannot stale them).  The site/file
# census is re-derived BY GREP at every lane ceremony (the three greps
# at the end of this block) — the numbers below are the 2026-11-13
# guidelines Phase-1/2 derivation, never a frozen literal to copy
# forward.
# Adding or
# removing a category REQUIRES updating ALL of them in the same commit:
# ``tests/unit/tools/test_upgrade_registration.py``,
# ``tests/unit/tools/test_attestation_registration.py``,
# ``tests/integration/test_maintenancer_spawn_resolves_tools.py``,
# the integration sanity pin in
# ``tests/integration/test_service_tool_flag_off_byte_identical.py``
# (``test_privileged_category_set_is_pinned``),
# ``tests/unit/tools/test_secrets_registration.py`` (the
# fifth exact-set pin — the originally-omitted one, added by the
# iteration-002 council B2 correction),
# ``tests/unit/test_board_registration.py`` (the ari-tools Phase 1
# pin — added 2026-10-20),
# ``tests/unit/test_self_alarm_registration.py`` (the ari-tools Phase 2
# pin — added 2026-10-20),
# ``tests/unit/test_instance_ops_registration.py`` (the nest-ops pin —
# added 2026-10-21; it pins BOTH categories plus the registration
# wiring) and
# ``tests/unit/test_wedge_capture_registration.py`` (the wedge-capture
# pin — added 2026-10-22; exact-set AND count) and
# ``tests/unit/test_notify_user_registration.py`` (the ari-voice pin —
# added 2026-10-25; exact-set AND count) and
# ``tests/unit/test_metrics_read_registration.py`` (the metrics pin —
# added 2026-11-03; exact-set AND count) and
# ``tests/unit/test_anomaly_report_tool_registration.py`` (the
# nest-report WRITE-tool pin — added 2026-11-05; count) and
# ``tests/unit/tools/test_nest_report_read_registration.py`` (the
# nest-report-read pin — added 2026-11-05; exact-set AND count) and
# ``tests/unit/test_memoir_registration.py`` (the memoir pin —
# added 2026-11-06; exact-set AND count) and
# ``tests/unit/tools/test_prompt_reload_pins.py`` (the prompt-reload
# pin — added 2026-11-06; count; it was MISSING from this prose until
# the 2026-11-10 prompt-assent ceremony re-derived the census — the
# prose is the artifact that gets copied forward, so a stale prose is a
# real defect) and
# ``tests/unit/tools/test_prompt_assent_registration.py`` (the
# prompt-assent pin — added 2026-11-10; exact-set AND count; appended
# to this prose by the same lane's Phase 4) and
# ``tests/unit/tools/test_guidelines_registration.py`` (the
# guidelines pin — added 2026-11-13; exact-set AND count; appended to
# this prose by the guideline-read-tool lane).  That is SEVENTEEN pin
# files, PLUS the mutation-pack find/replace STRING pairs
# (``test/packs/metrics_read_mutation_matrix_test.py`` MT-01 — derived
# from the live constant at pack-import time so a count bump cannot
# stale it — and the guideline-read-tool lane's MT-G01, the same
# derived shape aimed at the guidelines pin file; the pack also needs
# every new category as a MUTATION TARGET — a category with no
# mutation target is not covered by the matrix).  The exact-set pin
# surface is now TWELVE comparison sites (SEVEN
# ``EXPECTED_PRIVILEGED_SET`` definitions + FIVE inline ``frozenset``
# literals) and the count-pin surface is THIRTEEN assertion sites.
# The site/file census is re-derived BY GREP at every lane ceremony
# (the three greps at the end of this block) — the numbers below are
# the 2026-11-13 guidelines Phase-1/2 derivation, never a frozen
# literal to copy forward.
# SEPARATELY, ONE
# allow-array TRAILING-ENTRY pin (the ari ``tools.allow``
# ARRAY-ORDER discipline, ``tests/unit/test_board_registration.py``)
# keeps the per-agent grant arrays ordered; it pins ARRAY ORDER,
# not the set.
#
# DERIVE THE PIN SET BY GREPPING THE TREE — do NOT trust this prose:
#   grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/ test/ --include=*.py
#   grep -rn "PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET" tests/ test/ --include=*.py
#   grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ test/ --include=*.py
# Silent additions and silent removals (fail-open regressions) both trip the
# pins.
PRIVILEGED_TOOL_CATEGORIES: frozenset[str] = frozenset({
    "system_upgrade",
    "system-log",
    "ens-db",
    # W4 (leader decision, reviewer council 2026-10-02): ``ens-env``
    # is a KEY-RETURNING tool (``ens_env_read`` returns live env
    # values) — the empty-allow inherit universe must NOT auto-grant
    # it. Explicit ``tools.allow: ["ens-env"]`` opt-in (worker's
    # meta.json entry) remains the only access path; privileged
    # default-deny makes that structural instead of conventional.
    "ens-env",
    # REWORK 2026-10-07 (M1, user refinement #1): ``view-views``
    # is the read-only ``view_link`` URL-minter category. The tool
    # is a public-by-obscurity URL minter (no FS / DB side effects
    # at the tool surface), but the user's first-release
    # visibility decision (Discord, 2026-10-07) is RESTRICTED:
    # only the three commissioned users (ari, leader, designer)
    # opt in via ``tools.allow: ["view-views"]``. The empty-allow
    # inherit universe must NOT auto-grant it; making the
    # category privileged makes that structural instead of
    # conventional. The designer-side schema entry in
    # ``agents/designer/meta.json`` plus the new ari/leader
    # entries are the only access paths. KNOWN_TOOL_NAMES still
    # carries the tool name (inventory, not the gate); the gate
    # is the privileged-category strip + per-agent allow entry.
    "view-views",
    # agent-secrets P1 (2026-10-17, plan f11965039): ``secrets`` is
    # the encrypted-secret-store tool category (#6 in
    # PRIVILEGED_TOOL_CATEGORIES — quintet → sextet per the
    # Q1/Q2/Q3 user rulings 2026-10-17; the count before this
    # entry is 5 — ``cu``, the category that joined 2026-10-16
    # ahead of this entry, was REMOVED 2026-10-24 with the cu
    # builtin). The category owns
    # six factory-built tools (``secret_generate_ssh_keypair`` /
    # ``secret_generate_api_token`` / ``secret_store`` /
    # ``secret_list`` / ``secret_read_public`` / ``secret_retire``)
    # that operate on the Fernet-encrypted ``secrets`` +
    # ``secrets_audit`` PG tables. The trust ruling (plan §1.2)
    # is unconditional: secret-given = trusted for autonomous
    # use by ANY agent holding the category; the only access
    # boundary is the per-agent ``tools.allow`` gate. The day-one
    # allow-list (Q2 ruling) is ``worker``, ``tester``,
    # ``leader``, ``ari``; agents outside that allow-list get
    # ZERO of these tools through both the
    # ``_strip_privileged_category_tools`` default-deny strip
    # (``daemon/tools/instance.py:6169``) AND the
    # ``resolve_tool_filter`` empty-allow branch
    # (``daemon/tools/instance.py:373``). NEVER default-granted
    # — opt-in-only. SAME-PR FIVE-pin updates at
    # ``tests/unit/tools/test_upgrade_registration.py``,
    # ``tests/unit/tools/test_attestation_registration.py``,
    # ``tests/integration/test_maintenancer_spawn_resolves_tools.py``,
    # the integration sanity pin at
    # ``tests/integration/test_service_tool_flag_off_byte_identical.py``,
    # and ``tests/unit/tools/test_secrets_registration.py:218``
    # (the fifth exact-set pin — the originally-omitted one,
    # added by the iteration-002 council B2 correction) are
    # mandatory (the A14/D18 silent-additions-visible rule).
    "secrets",
    # settings-worker-slots P2 (2026-10-18): ``settings`` is the
    # daemon-runtime-settings tool category (#7 in
    # PRIVILEGED_TOOL_CATEGORIES — sextet → septet; ``secrets``
    # joined 2026-10-17 making the count 6 before this entry).
    # The category owns three factory-built tools
    # (``settings_get`` / ``settings_set`` / ``settings_clear``)
    # that read tier values and WRITE DB overrides for
    # daemon-global managed keys (``limits.llm_concurrency`` /
    # ``limits.worker_pool_size`` / ``limits.chat_worker_pool_size``)
    # through the wired ``SettingsService``. Why privileged: the
    # tools mutate daemon-global runtime state (pool sizes +
    # LLM concurrency caps) and read tier values — the
    # empty-allow inherit universe must NOT auto-grant them
    # (R-SR16, same as every entry above). Structural
    # enforcement is the standard pair: the
    # ``_strip_privileged_category_tools`` default-deny strip
    # (``daemon/tools/instance.py``) AND the
    # ``resolve_tool_filter`` empty-allow branch. NEVER
    # default-granted — opt-in-only; the ari + leader
    # meta.json ``tools.allow`` entries landed 2026-10-18 (P3).
    # The honest never-live contract
    # (D5) is load-bearing: every write response states
    # ``applies_at: "next daemon restart"`` + ``live: false``.
    # SAME-PR pin updates at the FIVE files listed in the
    # SAME-PR RULE comment above are mandatory.
    "settings",
    # ari-tools Phase 1 (the Workbook, 2026-10-20): ``board`` is the
    # global work-board tool category (#8 in
    # PRIVILEGED_TOOL_CATEGORIES — septet → octet; ``settings``
    # joined 2026-10-18 making the count 7 before this entry). The
    # category owns four factory-built tools (``board_create`` /
    # ``board_update`` / ``board_list`` / ``board_get``) over the three
    # ``board_items`` / ``board_notes`` / ``board_item_tags`` tables.
    # Why privileged: the board is ONE GLOBAL namespace — every Ari
    # instance reads and writes the SAME rows — so a
    # default-granted board would hand write authority over the
    # user's shared work surface to the empty-allow inherit universe
    # (the in-tree ``watcher`` agent plus every tools-less custom
    # agent). NEVER default-granted — opt-in-only; the ari
    # meta.json ``tools.allow`` entry is the only access path
    # (ARI-ONLY — user ruling 2026-10-20).
    # Defence in depth: a LITERAL TOOL NAME in an allow list bypasses
    # the category gate entirely, so the tool bodies carry their own
    # ``agent_id in {"ari"}`` gate as well. Both gates are
    # required; neither substitutes for the other. SAME-PR pin updates
    # at the FIVE files listed in the SAME-PR RULE comment above are
    # mandatory (this entry ships in the SAME commit as those five
    # insertions — the set must never be red between two commits).
    "board",
    # ari-tools Phase 2 (the self-alarm wrapper, 2026-10-20): ``self-alarm``
    # is the alarm/timer category (#9 in PRIVILEGED_TOOL_CATEGORIES —
    # octet → nonet; ``board`` joined 2026-10-20 making the count 8 before
    # this entry). The category owns ONE factory-built tool
    # (``self_alarm``) that arms a reminder to ari at least 30 minutes
    # out, delivered into a FRESH ari instance that then executes the
    # message text VERBATIM. Why privileged: an alarm armed by the
    # empty-allow inherit universe (``watcher`` and every tools-less
    # custom agent) means a fresh Ari instance — with Ari's FULL allow
    # list (bash, system_upgrade, secrets, …) — executing
    # caller-influenced text. That is a privilege-escalation chain, not
    # a visibility leak, so the category is opt-in-only and NEVER
    # default-granted. Defence in depth: a LITERAL TOOL NAME in an allow
    # list bypasses the category gate, so the tool body ALSO carries an
    # ``agent_id == "ari"`` runtime gate. Both gates are required;
    # neither substitutes for the other. The ari ``tools.allow`` entry is
    # the only access path (ari-for-ari — the leader pre-ruling).
    # SAME-PR pin updates at the FIVE files listed in the SAME-PR RULE
    # comment above are mandatory (this entry ships in the SAME commit as
    # those five insertions — the set must never be red between two
    # commits).
    "self-alarm",
    # ari-tools Phase 3 (the live-views surface, 2026-10-20): ``view-write``
    # is the report-page WRITER category (#10 in
    # PRIVILEGED_TOOL_CATEGORIES — nonet → decet; ``self-alarm`` joined
    # 2026-10-20 making the count 9 before this entry). The category owns
    # ONE factory-built tool (``view_write``) that writes an HTML page under
    # the daemon-global ``reports`` live-view root and returns the
    # passcode-gated public URL in a single call. Why privileged: the
    # ``reports`` root is a SHARED, CUSTOMER-FACING surface — a
    # default-granted writer would let the empty-allow inherit universe
    # (the in-tree ``watcher`` agent plus every tools-less custom agent)
    # publish pages that reach customers under the daemon's own passcode
    # lane. NEVER default-granted — opt-in-only; the ari meta.json
    # ``tools.allow`` entry is the only access path (ARI-ONLY pre-ruling).
    # Defence in depth: a LITERAL TOOL NAME in an allow list bypasses the
    # category gate entirely, so the tool body ALSO carries an
    # ``agent_id == "ari"`` runtime gate (the shared
    # ``daemon/tools/views_auth.py`` helper). Both gates are required;
    # neither substitutes for the other. SAME-PR pin updates at the SEVEN
    # files listed in the SAME-PR RULE comment above are mandatory (this
    # entry ships in the SAME commit as those insertions — the set must
    # never be red between two commits).
    "view-write",
    # ari-tools Phase 3 (the live board view, 2026-10-20): ``board-link`` is
    # the LIVE BOARD's link-minter category (#11 in
    # PRIVILEGED_TOOL_CATEGORIES — decet → undecet; ``view-write`` joined
    # 2026-10-20 making the count 10 before this entry). The category owns
    # ONE factory-built tool (``board_link``) that mints the passcode row
    # for the server-rendered board at ``/views/public/board/index.html``.
    # Why privileged: the board's passcode authorises reading the user's
    # whole work board — a default-granted minter would hand that authority
    # to the empty-allow inherit universe. NEVER default-granted —
    # opt-in-only; the ari meta.json ``tools.allow`` entry is
    # the only access path (ARI-ONLY — user ruling 2026-10-20). Defence in
    # depth: a LITERAL TOOL NAME in an allow list bypasses the category
    # gate, so the tool body ALSO carries an ``agent_id in {"ari"}``
    # runtime gate (the shared ``daemon/tools/views_auth.py``
    # helper — the SAME helper ``view_write`` uses, with a DIFFERENT gate
    # set). Both gates are required; neither substitutes for the other.
    # SAME-PR pin updates at the EIGHT files listed in the SAME-PR RULE
    # comment above are mandatory (this entry ships in the SAME commit as
    # those insertions — the set must never be red between two commits).
    "board-link",
    # wedge-capture (2026-10-22, D-28): the READ-ONLY capture-evidence
    # surface — ``wedge_capture_list`` + ``wedge_capture_get``. Why it is
    # privileged: the capture payload is a privileged diagnostic artifact
    # (all-thread stack dumps, per-thread CPU maps, process RSS and the
    # DB-side executing turns), and the empty-allow inherit universe must
    # never auto-grant it. The allow-list is ari + leader + maintenancer
    # — a DIFFERENT population from ``instance_read`` (maintenancer-only),
    # so a separate category keeps the privilege boundary auditable.
    # Defence in depth: a LITERAL TOOL NAME in an allow list bypasses the
    # category gate, so the tool bodies ALSO carry an ``agent_id in
    # {"ari", "leader", "maintenancer"}`` runtime gate (the shared helper
    # in ``daemon/tools/wedge_capture.py``). Both gates are required.
    # The manual capture trigger is deliberately NOT an agent tool — it
    # stays on the admin API / CLI, so an agent cannot fill the disk.
    "wedge-capture",
    # nest-ops (2026-10-21, D-13): TWO single-owner categories for the
    # maintenancer — ``instance_read`` (#13: the four fleet reads) and
    # ``instance_ops`` (#14: the six audited acts). Why BOTH are
    # privileged, and why the split is structural rather than prose:
    #   * the ACT surface is DESTRUCTIVE (terminate takes a whole
    #     cascade tree) — the empty-allow inherit universe must never
    #     auto-grant it, and the per-tool in-body agent gate is a
    #     SECOND layer, not a substitute for the category strip;
    #   * the READ surface is fleet-wide telemetry (every instance in
    #     the daemon, cross-project by design) — a default-granted
    #     census would leak the whole fleet's shape to the empty-allow
    #     universe;
    #   * two categories keep read and act structurally separable: a
    #     future read-only grant to another agent is a one-line allow
    #     change on a category that structurally CANNOT carry acts.
    # Both name the SAME single owner (maintenancer). Per-tool allow
    # is REJECTED (D-13): a literal tool name in an allow list bypasses
    # the category gate outright. SAME-PR pin updates at the EIGHT
    # files listed in the SAME-PR RULE comment above are mandatory
    # (this entry ships in the SAME commit as those insertions — the
    # set must never be red between two commits).
    "instance_read",
    "instance_ops",
    # remote-hands center gateway (2026-10-22): ``mcp_rpc`` is the
    # category that lets an agent DRIVE A REMOTE MACHINE — the
    # computer-use surface of the remote-hands protocol (SPEC.md v0.1).
    # #15 in PRIVILEGED_TOOL_CATEGORIES (14 → 15; ``instance_read`` +
    # ``instance_ops`` joined 2026-10-21 making the count 13 before
    # this entry).
    #
    # Why privileged: a tool of this category runs a tool on ANOTHER
    # host's desktop — the wrapper's own policy layer is the only thing
    # between the agent and the user's screen. The empty-allow inherit
    # universe (the in-tree ``watcher`` agent plus every tools-less
    # custom agent) must NEVER auto-grant it.
    #
    # Defence in depth: a LITERAL tool name in an allow list bypasses
    # the category gate entirely, so every tool body ALSO carries an
    # ``agent_id in {"ari", "leader"}`` runtime gate. Both gates are
    # required; neither substitutes for the other. The ari + leader
    # ``tools.allow`` entries are the only access paths (the leader
    # froze the audience of this phase).
    #
    # SAME-PR pin updates at the EIGHT files listed in the SAME-PR RULE
    # comment above are mandatory (this entry ships in the SAME commit
    # as those eight insertions — the set must never be red between two
    # commits).
    "mcp_rpc",
    # ari-voice (2026-10-25): ``notify-user`` is the deliberate
    # chat-message category (#16 in PRIVILEGED_TOOL_CATEGORIES — 15 →
    # 16 post-rebase; ``mcp_rpc`` joined 2026-10-22 making the count
    # 15 before this entry; the ``cu`` removal of 2026-10-24 took the
    # old base's 16 down to 15). The category owns ONE factory-built
    # tool (``notify_user``) that sends ONE deliberate message from a
    # non-user-originated turn to the SESSION'S OWN registered chat
    # source. Why privileged: the tool writes to the user's OWN chat
    # channel — the empty-allow inherit universe must never hold a
    # (bounded, audited, but still real) channel into the user's
    # Discord/Slack/Telegram. The bounds are structural: ari-only
    # allow entry, N=1/turn as the >=60s interval proxy, M=6/rolling-
    # hour per resolved destination from the notify_audit ledger,
    # 2000-char cap, ONE 10s never-block bound, every attempt
    # ledgered. NEVER default-granted — opt-in-only; the ari
    # meta.json ``tools.allow`` entry is the only access path
    # (ari-for-ari). Defence in depth: a LITERAL TOOL NAME in an allow
    # list bypasses the category gate, so the tool body ALSO carries
    # an ``agent_id == "ari"`` runtime gate. Both gates are required;
    # neither substitutes for the other.
    # SAME-PR pin updates at the ELEVEN files carrying the EIGHTEEN
    # assertion sites (5 exact-set + 6 EXPECTED_PRIVILEGED_SET
    # definitions + 6 count + 1 allow-array trailing-entry pin —
    # re-derived by grep at the memoir ceremony, 2026-11-05; the
    # memoir-store lane corrected this census) are mandatory (this entry
    # ships in the SAME commit as those bumps — the set must never be red
    # between two commits).
    "notify-user",
    # metrics-read (2026-11-03, metrics-agent-tools lane): the THREE
    # read-only metric-registry tools — created by the
    # create_metrics_read_tools() factory (daemon/tools/metrics_read.py).
    # The category is PRIVILEGED (entry #17 of
    # PRIVILEGED_TOOL_CATEGORIES): the daemon's own metric registry is an
    # internal counter surface, so the empty-allow inherit universe must
    # NOT auto-grant it. Explicit ``tools.allow: ["metrics-read"]`` on
    # maintenancer + devops + wanderer is the only access path, and every
    # tool body ALSO carries its own ``agent_id`` gate (the ONE shared
    # helper in ``daemon/tools/metrics_read.py``) — a LITERAL tool name
    # in an allow list bypasses the category gate outright, so the
    # category alone cannot spell the audience.
    #
    # The surface is structurally READ-ONLY: no write tool exists in it,
    # and the tools read the SAME provider instance (and the SAME in-RAM
    # trend ring) the HTTP query door serves from — one provider, one
    # ring, one truth.
    #
    # SAME-PR pin updates at the THIRTEEN files carrying the NINETEEN
    # assertion sites (11 exact-set + 8 count + 1 pack string) are
    # mandatory (this entry ships in the SAME commit as those bumps
    # — the set must never be red between two commits).
    "metrics-read",
    # nest-report-read (2026-11-05, nest-anomaly-report lane): the
    # READ-ONLY anomaly-store category — ``anomaly_clusters`` +
    # ``anomaly_report_detail``, created by the
    # create_nest_report_read_tools() factory
    # (daemon/tools/nest_report_read.py). PRIVILEGED category (entry
    # #18 of PRIVILEGED_TOOL_CATEGORIES): the anomaly store carries
    # agent-filed incident rows with harvested evidence (the excerpt is
    # raw tool stdout), so the empty-allow inherit universe must NOT
    # auto-grant it. Explicit ``tools.allow: ["nest-report-read"]`` on
    # maintenancer + devops + wanderer is the only access path (the R8
    # ops trio), and every tool body ALSO carries its own ``agent_id``
    # gate (the ONE shared helper in
    # ``daemon/tools/nest_report_read.py``) — a LITERAL tool name in an
    # allow list bypasses the category gate outright, so the category
    # alone cannot spell the audience.
    #
    # The surface is structurally READ-ONLY and reads through the
    # ANOMALY SERVICE (never a raw engine handle, never direct
    # repository access, never REST-scraping — R8); the
    # ``/api/nest-reports`` route calls the SAME service projector
    # (one projector, two transports — AM-1).
    #
    # SAME-PR pin updates at the THIRTEEN files carrying the NINETEEN
    # assertion sites (11 exact-set + 8 count + 1 pack string) are
    # mandatory (this entry ships in the SAME commit as those bumps
    # — the set must never be red between two commits).
    "nest-report-read",
    # memoir (2026-11-05, memoir-store-v1 lane; MERGED as entry #19 on the
    # nest-anomaly lineage, 2026-11-06): ari's durable episodic memory +
    # keepsake governance — the FIVE tools of design §5.4, created by the
    # create_memoir_tools() factory (daemon/tools/memoir.py). PRIVILEGED
    # entry #19 of PRIVILEGED_TOOL_CATEGORIES (18 -> 19 after the
    # nest-report-read entry): the empty-allow inherit universe must NOT
    # auto-grant authority over ari's durable first-person narrative
    # (append-only, never hard-deleted). Explicit ``tools.allow: ["memoir"]``
    # on ari is the only access path — ARI-EXACTLY (R2), and every tool body
    # ALSO carries its own factory-captured agent_id gate (DD-13 — the layer
    # prompt content cannot bend; a LITERAL tool name in an allow list
    # bypasses the category gate outright). The kill-switch
    # (``ENSEMBLE_MEMOIR_ENABLED``) is fail-closed on indeterminate values
    # (DR-3) and is read AT CALL TIME (D4.16), behind the auth gate (AM-16's
    # auth-first order — an unauthorized caller learns nothing about the
    # knob's state). The write path performs exactly ONE insert
    # (AM-12/D4.11 — the embedder is reachable only from the worker module);
    # keepsakes are capped (12 pins / a 4,000-token zone) and NEVER evicted
    # (DD-3).
    "memoir",
    # prompt-reload (2026-11-06, prompt-reload lane): ari's mid-session
    # SELF prompt refresh — the ONE tool of the ``prompt-reload``
    # category, created by the create_prompt_reload_tools() factory
    # (daemon/tools/prompt_reload.py). PRIVILEGED entry #20 of
    # PRIVILEGED_TOOL_CATEGORIES (19 -> 20 after the memoir entry): the
    # tool evicts the CALLING instance's cached graph so the next
    # dispatch cold-restores onto a freshly composed persona — the
    # empty-allow inherit universe must NOT auto-grant that authority
    # (an agent reloading its OWN prompt mid-session is a privileged
    # self-mutation, and the resolution/validation surface reads the
    # release manifest + prompt-versions DB). Explicit
    # ``tools.allow: ["prompt-reload"]`` on ari is the only access
    # path — ARI-EXACTLY (R5), and the tool body ALSO carries its own
    # factory-captured agent_id gate (the notify-user precedent — a
    # LITERAL tool name in an allow list bypasses the category gate
    # outright). The kill-switch (``ENSEMBLE_PROMPT_RELOAD_ENABLED``)
    # is read AT CALL TIME (AM-11) and the tool STAYS REGISTERED under
    # any switch state (refusal-at-call, never construction-absence).
    # SAME-PR pin updates at every file carrying the privileged-set /
    # count assertion sites (re-derive the census BY GREP at the
    # ceremony — see the SAME-PR RULE block above) are mandatory.
    "prompt-reload",
    # prompt-assent (2026-11-10, prompt-assent lane): the conversational
    # approval lane ("The Emperor's Word / Assent") — ONE tool, FOUR
    # verbs (AR-2: list / mint / approve / reject), created by the
    # create_prompt_assent_tools() factory
    # (daemon/tools/prompt_assent.py). PRIVILEGED entry #21 of
    # PRIVILEGED_TOOL_CATEGORIES (20 -> 21 after the prompt-reload
    # entry): the lane mints and consumes ASSENT TOKENS on the
    # Emperor's behalf, so the empty-allow inherit universe must NOT
    # auto-grant that authority. Explicit
    # ``tools.allow: ["prompt-assent"]`` on ari is the only access
    # path — ARI-EXACTLY — and the tool body ALSO carries its own
    # factory-captured agent_id gate (the notify-user precedent — a
    # LITERAL tool name in an allow list bypasses the category gate
    # outright). The kill-switch (``ENSEMBLE_PROMPT_ASSENT_ENABLED``)
    # is read AT CALL TIME, defaults OFF (D-2, fail-closed), and the
    # tool STAYS REGISTERED under any switch state (refusal-at-call,
    # never construction-absence). The three-factor gate has exactly
    # ONE implementation owner: daemon/tools/prompt_assent.py (O-E).
    # SAME-PR pin updates at every file carrying the privileged-set /
    # count assertion sites (re-derive the census BY GREP at the
    # ceremony — see the SAME-PR RULE block above) are mandatory.
    "prompt-assent",
    # guidelines (2026-11-13, guideline-read-tool lane): the READ-ONLY
    # window onto the shipped guideline corpus — ``guidelines_list`` +
    # ``guideline_read``, created by the create_guidelines_tools()
    # factory (daemon/tools/guidelines.py). PRIVILEGED entry #22 of
    # PRIVILEGED_TOOL_CATEGORIES (21 -> 22 after the prompt-assent
    # entry): the corpus is the doctrine layer for dormant /
    # config-armed / ceremony-gated / silently-biting features, so the
    # empty-allow inherit universe must NOT auto-grant it. Explicit
    # ``tools.allow: ["guidelines"]`` on EXACTLY ari + leader + devops
    # + sonic-leader is the only access path, and every tool body ALSO
    # carries its own ``GUIDELINES_AUTHORIZED_AGENTS`` gate (the
    # metrics-read two-layer shape) — a LITERAL tool name in an allow
    # list bypasses the category gate outright.
    #
    # SONIC-FIRST CEREMONY NOTE (ruling 2026-11-13): this is the FIRST
    # privileged-category grant to a sonic agent (sonic-leader; grant
    # authority: sonic roster v0.30.2 amendment, merge 82e2bd849) —
    # supported by census: no other sonic-* allow array carries any
    # privileged member, and sonic-leader's ``midflight`` is
    # NON-privileged (it does not confer this grant).
    #
    # The surface is structurally READ-ONLY: the tools read THE RELEASE
    # PAYLOAD only (frozen: ``<exe-dir>/guidelines``; dev: repo
    # ``docs/guidelines``) and cannot serve any guideline newer than
    # the running release — INTENDED. No write path, no cache, no
    # database; the payload is the natural off-switch (adjudication
    # Q4: NO kill-switch, NO env knob).
    #
    # SAME-PR pin updates at every file carrying the privileged-set /
    # count assertion sites (re-derive the census BY GREP at the
    # ceremony — see the SAME-PR RULE block above) are mandatory (this
    # entry ships in the SAME commit as those bumps — the set must
    # never be red between two commits).
    "guidelines",
})


def register_tool_category(category: str):
    """Decorator to mark a tool with its category.
    
    Usage:
        @register_tool_category("filesystem")
        @tool
        def read_file(...):
            ...
    
    Args:
        category: The category key (e.g., "filesystem", "instance").
    
    Returns:
        Decorator function that sets _tool_category on the tool.
    """
    def decorator(func):
        func._tool_category = category
        # First-party provenance marker. scan_tools_for_full_docs only
        # honors a category OVERRIDE on rescan (existing entry) when this
        # flag is present, so attacker-influenced tool lists (e.g. MCP
        # tools whose ``name`` is server-controlled and may collide with
        # first-party tool names) cannot silently re-categorize an
        # already-registered entry via a spoofed ``_tool_category`` attr.
        func._tool_category_first_party = True
        return func
    return decorator


def register_tool(
    tool_name: str,
    category: str = "general",
    short_doc: str = "",
    full_doc: str = "",
) -> Callable:
    """Decorator to register a tool with its documentation.
    
    Usage:
        @register_tool("project_create", category="project")
        @tool
        def project_create(...):
            ...
            project_create._full_doc_ = "..."
    
    Args:
        tool_name: Unique tool identifier.
        category: Tool category for grouping (e.g., "project", "instance").
        short_doc: Brief one-line description.
        full_doc: Detailed documentation.
    
    Returns:
        Decorator function.
    """
    def decorator(func: Callable) -> Callable:
        _tool_metadata[tool_name] = {
            "category": category,
            "short_doc": short_doc,
            "full_doc": full_doc,
        }
        if full_doc:
            _full_docs[tool_name] = full_doc
        return func
    return decorator


def register_full_doc(tool_name: str, full_doc: str) -> None:
    """Register full documentation for a tool.
    
    Called automatically when a tool function has a _full_doc_ attribute.
    
    Args:
        tool_name: The tool identifier.
        full_doc: Full documentation string.
    """
    _full_docs[tool_name] = full_doc


def get_full_doc(tool_name: str) -> str | None:
    """Retrieve full documentation for a tool.
    
    Args:
        tool_name: The tool identifier.
    
    Returns:
        Full documentation string, or None if not found.
    """
    return _full_docs.get(tool_name)


def get_tool_metadata(tool_name: str) -> dict[str, Any] | None:
    """Get metadata for a specific tool.
    
    Args:
        tool_name: The tool identifier.
    
    Returns:
        Dictionary with category, short_doc, full_doc, or None.
    """
    return _tool_metadata.get(tool_name)


def list_tools() -> dict[str, dict[str, Any]]:
    """List all registered tools with their metadata.
    
    Returns:
        Dictionary mapping tool names to their metadata.
    """
    return dict(_tool_metadata)


def list_tools_by_category() -> dict[str, list[str]]:
    """List tools grouped by category.
    
    Returns:
        Dictionary mapping category names to lists of tool names.
    """
    categories: dict[str, list[str]] = {}
    for tool_name, meta in _tool_metadata.items():
        cat = meta.get("category", "general")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(tool_name)
    return categories


def _scan_category_module_sources() -> tuple[set[str], bool]:
    """AST-scan every CATEGORY_MODULES source file for ``@tool``-decorated names.

    Shared by ``discover_all_tool_names()`` (which then merges with the static
    ``KNOWN_TOOL_NAMES`` fallback) and ``discover_source_only_tool_names()``
    (which raises on zero-source — the frozen-binary case).

    Returns:
        (tool_names, any_source_read) — ``any_source_read`` is True iff at least
        one category-module source file was actually read from disk. The AST
        walker descends into every nested scope so factory-internal ``@tool``
        decorations are caught (they never register at import time).
    """
    import ast
    from pathlib import Path

    tool_names: set[str] = set()
    # Tracks whether ANY source file was actually read on this call. When the
    # whole daemon/ tree is bytecode-only (frozen binary), this stays False and
    # the caller decides what to do (fallback vs raise).
    any_source_read = False

    for category_key, module_path_str in CATEGORY_MODULES.items():
        paths = module_path_str if isinstance(module_path_str, list) else [module_path_str]
        for mod_path in paths:
            # Resolve module path to file path
            # e.g. "daemon.tools.project" -> daemon/tools/project.py
            parts = mod_path.split(".")
            # Skip the "daemon" prefix to find the package root
            # Try relative path from this file's location
            try:
                # Walk up from this file (daemon/tools/_tool_registry.py) to find the file
                # daemon/tools/_tool_registry.py -> parent is daemon/tools/
                base_dir = Path(__file__).parent  # daemon/tools/
                # mod_path like "daemon.tools.project" -> parts ["daemon", "tools", "project"]
                # We need the relative path from the daemon package root
                # daemon/tools/ corresponds to "daemon.tools"
                file_path = base_dir
                for part in parts[2:]:  # skip "daemon", "tools"
                    file_path = file_path / part
                file_path = file_path.with_suffix(".py")

                if not file_path.exists():
                    continue

                any_source_read = True
                source = file_path.read_text()
                tree = ast.parse(source)

                # Walk the ENTIRE tree (not just top-level) to catch tools
                # defined inside factory functions
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    # Check if any decorator is @tool (bare or called: @tool or @tool(...))
                    has_tool_decorator = False
                    for dec in node.decorator_list:
                        dec_name = ""
                        if isinstance(dec, ast.Name):
                            dec_name = dec.id
                        elif isinstance(dec, ast.Attribute):
                            dec_name = dec.attr
                        elif isinstance(dec, ast.Call):
                            if isinstance(dec.func, ast.Name):
                                dec_name = dec.func.id
                            elif isinstance(dec.func, ast.Attribute):
                                dec_name = dec.func.attr
                        if dec_name == "tool":
                            has_tool_decorator = True
                            break

                    if has_tool_decorator:
                        tool_names.add(node.name)

            except (OSError, SyntaxError, Exception):
                continue

    return tool_names, any_source_read


def discover_source_only_tool_names() -> set[str]:
    """Pure source discovery — NO merge with ``KNOWN_TOOL_NAMES``, NO frozen fallback.

    This function is the regen source of truth for the ``KNOWN_TOOL_NAMES``
    static fallback and the canonical basis for bidirectional drift detection
    between source and the static universe. It MUST return only what the
    on-disk ``daemon/tools/`` source contains; it never augments with the
    static list.

    Frozen-binary contract: when ZERO ``CATEGORY_MODULES`` source files are
    readable (typical PyInstaller frozen build where ``daemon/`` ships as
    bytecode only), this function raises ``RuntimeError`` rather than
    silently returning the static universe. Drift detection in a frozen
    environment is not meaningful — callers that need the frozen-safe
    merged result should use ``discover_all_tool_names()`` instead.

    Returns:
        Set of ``@tool``-decorated function names discovered by AST-scanning
        every CATEGORY_MODULES source file on disk.

    Raises:
        RuntimeError: if ZERO ``CATEGORY_MODULES`` source files are readable
            (frozen binary, or path resolution failed for every category).
    """
    tool_names, any_source_read = _scan_category_module_sources()
    if not any_source_read:
        raise RuntimeError(
            "discover_source_only_tool_names(): no CATEGORY_MODULES source files "
            "readable (frozen binary?) — source-only discovery is unavailable; "
            "use discover_all_tool_names() for the frozen-safe universe"
        )
    return tool_names


def discover_all_tool_names() -> set[str]:
    """Statically discover all @tool-decorated function names across CATEGORY_MODULES.

    Many tools are created inside factory functions (e.g. ``create_project_tools``)
    and use ``@tool`` inside the factory body, so they never register in
    ``_tool_metadata`` at import time. This function AST-scans the source of every
    category module to find ``@tool``-decorated functions at ANY nesting depth.

    Frozen-binary safe: in PyInstaller-frozen builds the ``daemon/`` package ships
    as bytecode only and ``file_path.exists()`` is False for every category
    module. In that case the static fallback universe ``KNOWN_TOOL_NAMES`` is
    returned (and a single debug log is emitted). When some source files are
    readable but others are not, the result merges source-discovered names with
    ``KNOWN_TOOL_NAMES`` so source stays canonical where present and the static
    list covers the rest.

    For pure source-of-truth discovery (drift detection, regenerating
    ``KNOWN_TOOL_NAMES``), use ``discover_source_only_tool_names()`` instead.

    Returns:
        Set of tool names discoverable from static source analysis, augmented
        with the static ``KNOWN_TOOL_NAMES`` fallback.
    """
    tool_names, any_source_read = _scan_category_module_sources()

    if not any_source_read:
        # No CATEGORY_MODULES source files readable — typically a PyInstaller
        # frozen binary where daemon/ is bytecode-only. Fall back to the static
        # universe so factory-created tool names remain visible to the validator
        # and we don't emit false-positive "unknown tool" warnings. Single
        # debug log; not per-file.
        _logger.debug(
            "discover_all_tool_names: no CATEGORY_MODULES source files readable "
            "(frozen binary?); falling back to KNOWN_TOOL_NAMES (%d entries)",
            len(KNOWN_TOOL_NAMES),
        )
        return set(KNOWN_TOOL_NAMES)

    # Partial-source path: source is canonical where present; KNOWN_TOOL_NAMES
    # covers the rest. Merging is defensive — if a maintainer adds a @tool and
    # forgets to regenerate KNOWN_TOOL_NAMES, the source scan still catches it.
    tool_names |= KNOWN_TOOL_NAMES
    return tool_names


def clear_registry() -> None:
    """Clear all registered tools. Useful for testing."""
    _full_docs.clear()
    _tool_metadata.clear()


def scan_tools_for_full_docs(tools: list) -> None:
    """Scan a list of tools and register any with _full_doc_ attributes.
    
    This should be called after tools are created to pick up any
    _full_doc_ attributes that were set on tool functions.
    
    Args:
        tools: List of tool functions (from @tool decorator).
    """
    for tool_func in tools:
        tool_name = getattr(tool_func, 'name', None) or getattr(tool_func, '__name__', str(tool_func))
        
        # Check for _full_doc_ attribute
        if hasattr(tool_func, '_full_doc_'):
            register_full_doc(tool_name, tool_func._full_doc_)
        
        # Extract short doc from description or docstring
        short_doc = ""
        if hasattr(tool_func, 'description'):
            short_doc = tool_func.description.split('\n')[0]
        elif hasattr(tool_func, '__doc__') and tool_func.__doc__:
            short_doc = tool_func.__doc__.split('\n')[0]
        
        # Infer category from _tool_category attribute or name fallback
        category = getattr(tool_func, '_tool_category', None)
        if category is None:
            category = tool_name.split('_')[0] if '_' in tool_name else 'general'
        
        # Register metadata
        if tool_name not in _tool_metadata:
            _tool_metadata[tool_name] = {
                "category": category,
                "short_doc": short_doc,
                "full_doc": _full_docs.get(tool_name, ""),
            }
        else:
            # Update existing metadata.
            #
            # short_doc/full_doc always reflect the latest scan (different
            # closures may carry refreshed docstrings). category is sticky:
            # it is only updated when the scanning tool carries the
            # first-party provenance marker ``_tool_category_first_party``
            # (set only by @register_tool_category). This blocks an
            # attacker-influenced tool list (e.g. MCP tools with
            # server-controlled names) from spoofing ``_tool_category``
            # to re-categorize an already-registered first-party tool,
            # which would silently break ``tools.allow`` category
            # resolution. The workdir/instance_id wrappers in
            # daemon.tools.instance rebuild StructuredTool via
            # from_function which strips ``_tool_category``; the wrapper
            # helper re-applies it AND the provenance marker.
            _tool_metadata[tool_name]["short_doc"] = short_doc
            if getattr(tool_func, '_tool_category_first_party', False):
                _tool_metadata[tool_name]["category"] = category
            if tool_name in _full_docs:
                _tool_metadata[tool_name]["full_doc"] = _full_docs[tool_name]


# Category module mapping: category_key -> full module path(s)
CATEGORY_MODULES: dict[str, str | list[str]] = {
    "bash": "daemon.tools.bash",
    "critical_notes": "daemon.tools.critical_notes",
    # POK Phase 1 (project-operating-knowledge, Emperor GO
    # 2026-11-07) — the three project operating-knowledge tools
    # (pok_upsert / pok_query / pok_curate). NON-privileged: grants
    # flow through the per-agent ``tools.allow: ["pok"]`` entries
    # landed by the B4 sweep (40-agent class; watcher excluded —
    # empty allow inherits-all); authorization differences are
    # IN-BODY role gates from the factory-captured agent_id
    # (attribution ≠ authorization): any project-linked agent
    # writes/reads; leader / project-manager curates. The value MUST
    # be a ``daemon.tools.*`` path — the AST scanner resolves the
    # value as ``daemon/tools/<parts[2:]>.py``.
    "pok": "daemon.tools.pok",
    "project_history": "daemon.tools.project_history",
    "filesystem": "daemon.tools.filesystem",
    "time": "daemon.tools.time",
    "instance": ["daemon.tools.instance", "daemon.tools.tunables"],
    "self": ["daemon.tools.inner_soul", "daemon.tools.access_memory"],
    "project": "daemon.tools.project",
    "job": "daemon.tools.job_queue",
    # Scheduling (scheduled-tasks phase 2+4) — wall-clock schedule
    # management over source_configs rows of source_type="scheduler".
    # Non-privileged by design (grants via per-agent tools.allow;
    # PRIVILEGED_TOOL_CATEGORIES untouched). Placed near "job" —
    # scheduling is job-adjacent (the adapter fires JobItems).
    "scheduling": "daemon.tools.scheduling",
    # ari-tools Phase 1 (the Workbook) — the global, DB-backed work
    # board. PRIVILEGED category (entry #8 of
    # PRIVILEGED_TOOL_CATEGORIES): the board is global and shared, so
    # the empty-allow inherit universe must NOT auto-grant it. Placed
    # near "job" — the board is work-adjacent (it links job ids).
    # nest-ops (2026-10-21): the two maintenancer fleet categories. The
    # values MUST be ``daemon.tools.*`` paths — the AST scanner resolves
    # a CATEGORY_MODULES value as ``daemon/tools/<parts[2:]>.py``, so a
    # ``daemon.services.*`` value is NEVER discovered and the
    # exact-equality drift pin fails at boot.
    "instance_read": "daemon.tools.instance_read",
    "instance_ops": "daemon.tools.instance_ops",
    "board": "daemon.tools.board_tools",
    # ari-tools Phase 2 (the self-alarm wrapper) — the alarm/timer
    # category. PRIVILEGED category (entry #9 of
    # PRIVILEGED_TOOL_CATEGORIES): the empty-allow inherit universe must
    # NOT auto-grant the power to arm an alarm whose text a fresh Ari
    # instance executes verbatim.
    #
    # The value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``
    # (``_tool_registry.py:504-506``); a ``daemon.services.*`` value is
    # NEVER discovered and the exact-equality drift pin fails at boot.
    # The PURE helpers live in ``daemon/services/self_alarm_service.py``
    # and are imported BY this module — the category points at the tool
    # module, the service module is a library, never a category target.
    "self-alarm": "daemon.tools.self_alarm",
    # ari-tools Phase 3 (the live-views surface) — the report-page WRITER
    # category. PRIVILEGED category (entry #10 of
    # PRIVILEGED_TOOL_CATEGORIES): the empty-allow inherit universe must
    # NOT auto-grant write authority over the shared, customer-facing
    # ``reports`` live-view root. Placed next to "view-views" — the
    # read-only URL minter is the sibling surface.
    #
    # The value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``
    # (``_tool_registry.py:504-506``); a ``daemon.services.*`` value is
    # NEVER discovered and the exact-equality drift pin fails at boot.
    # The SHARED layer-3 gate helper lives in
    # ``daemon/tools/views_auth.py`` — a LIBRARY imported BY both phase-3
    # tool modules, never a category target.
    "view-write": "daemon.tools.view_write",
    # ari-tools Phase 3 (the live board view) — the board's link-minter
    # category. PRIVILEGED category (entry #11 of
    # PRIVILEGED_TOOL_CATEGORIES): minting the board's passcode
    # authorises reading the user's whole work board, so the empty-allow
    # inherit universe must never auto-grant it.
    #
    # Deliberately a SEPARATE category from phase 1's ``board`` (the data
    # tools): audience separation (mint ≠ read) and zero coupling to the
    # phase-1 module (D13).
    "board-link": "daemon.tools.board_link",
    # remote-hands center gateway (2026-10-22) — the four agent-facing
    # tools of the remote-hands protocol (SPEC.md v0.1). PRIVILEGED
    # category (entry #15 of PRIVILEGED_TOOL_CATEGORIES): the
    # empty-allow inherit universe must NOT auto-grant the authority to
    # drive a remote desktop.
    #
    # The value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``; a
    # ``daemon.services.*`` value is NEVER discovered and the
    # exact-equality drift pin fails at boot. The PURE helpers live in
    # ``daemon/services/mcps_gateway/`` and are imported BY this module —
    # the category points at the tool module, the service package is a
    # library, never a category target.
    "mcp_rpc": "daemon.tools.mcp_rpc_tools",
    # wedge-capture (2026-10-22, D-31/D-41): the READ-ONLY capture
    # evidence surface. PRIVILEGED category (entry #16 of
    # PRIVILEGED_TOOL_CATEGORIES): the empty-allow inherit universe must
    # NOT auto-grant the authority to read the all-thread stack dumps and
    # per-thread CPU maps of a wedged daemon.
    #
    # The value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``; a
    # ``daemon.services.*`` value is NEVER discovered and the
    # exact-equality drift pin fails at boot. The category key is
    # KEBAB-case (``wedge-capture``) while the tool names are snake-case
    # (``wedge_capture_*``) — the house pattern (``self-alarm`` →
    # ``self_alarm``, ``board-link`` → ``board_link``), and the MCP
    # privilege stamping keys off the category prefix, so the two must
    # agree.
    "wedge-capture": "daemon.tools.wedge_capture",
    # ari-voice (2026-10-25): the deliberate chat-message category.
    # PRIVILEGED category (entry #16 of PRIVILEGED_TOOL_CATEGORIES):
    # the tool writes to the user's OWN chat channel, so the
    # empty-allow inherit universe must never auto-grant it.
    #
    # The value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``; a
    # ``daemon.services.*`` value is NEVER discovered and the
    # exact-equality drift pin fails at boot. The category key is
    # KEBAB-case (``notify-user``) while the tool name is snake-case
    # (``notify_user``) — the house pattern (``self-alarm`` →
    # ``self_alarm``, ``wedge-capture`` → ``wedge_capture_*``).
    "notify-user": "daemon.tools.notify_user",
    # metrics-read (2026-11-03, metrics-agent-tools lane): the READ-ONLY
    # metric-registry category. PRIVILEGED category (entry #17 of
    # PRIVILEGED_TOOL_CATEGORIES): the daemon's own internal counters must
    # never reach the empty-allow inherit universe.
    #
    # The value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``; a
    # ``daemon.services.*`` value is NEVER discovered and the
    # exact-equality drift pin fails at boot. The category key is
    # KEBAB-case (``metrics-read``) while the tool names are snake_case
    # (``metrics_*``) — the house pattern (``self-alarm`` →
    # ``self_alarm``, ``wedge-capture`` → ``wedge_capture_*``).
    "metrics-read": "daemon.tools.metrics_read",
    # nest-report-read (2026-11-05, nest-anomaly-report lane): the
    # READ-ONLY anomaly-store category — ``anomaly_clusters`` +
    # ``anomaly_report_detail`` over the anomaly service. PRIVILEGED
    # category (entry #18 of PRIVILEGED_TOOL_CATEGORIES): the store
    # carries agent-filed incident rows with harvested evidence, so the
    # empty-allow inherit universe must never auto-grant it.
    #
    # The value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``. The
    # category key is KEBAB-case (``nest-report-read``) while the tool
    # names keep the DATA vocabulary (``anomaly_*``) — R9's two-layer
    # naming split (the OPERATIONAL surface is nest-, the DATA layer is
    # anomaly-).
    "nest-report-read": "daemon.tools.nest_report_read",
    # nest-report (2026-11-05, nest-anomaly-report lane): the ONE write
    # category of the nest-anomaly surface — ``report_anomaly`` (an agent
    # reports something wrong; the daemon normalizes, scrubs, harvests the
    # bounded evidence and persists ONE row).
    #
    # NOT PRIVILEGED (R4): the category is deliberately OUT of
    # ``PRIVILEGED_TOOL_CATEGORIES`` — the tool cannot read secrets, the
    # environment or another instance's data, and its own evidence harvest
    # is self-scoped and scrubbed.  So the empty-allow inherit universe
    # DOES grant it (agents with an empty allow list inherit the tool
    # immediately); an agent with an EXPLICIT allow list receives it in
    # the phase-5 grant ceremony.  The privileged set and its pin sites
    # stay UNTOUCHED.
    # memoir (2026-11-05, memoir-store-v1 lane): ari's durable episodic
    # memory + keepsake governance. PRIVILEGED category (entry #18 of
    # PRIVILEGED_TOOL_CATEGORIES): ari's durable first-person narrative
    # must never reach the empty-allow inherit universe.
    #
    # The value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``; a
    # ``daemon.services.*`` value is NEVER discovered and the
    # exact-equality drift pin fails at boot. The category key is
    # KEBAB-case (``nest-report``) while the tool name is snake_case
    # (``report_anomaly``) — the house pattern (``self-alarm`` →
    # ``self_alarm``, ``wedge-capture`` → ``wedge_capture_*``).
    "nest-report": "daemon.tools.nest_report",
    #
    # memoir (2026-11-05, memoir-store-v1 lane; merged 2026-11-06):
    # ari's durable episodic memory + keepsake governance. PRIVILEGED
    # category (entry #19 of PRIVILEGED_TOOL_CATEGORIES): ari's durable
    # first-person narrative must never reach the empty-allow inherit
    # universe.
    #
    # The value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``. The category
    # key is the exact tool-family word ``memoir`` (D4.1 — never
    # "memoir-store" / "ari-memoir"); the tool names are snake_case
    # (``memoir_write``/``memoir_pin``/``memoir_unpin``/
    # ``memoir_recall``/``memoir_timeline``).
    "memoir": "daemon.tools.memoir",
    # prompt-reload (2026-11-06, prompt-reload lane): ari's mid-session
    # self prompt refresh. PRIVILEGED category (entry #20 of
    # PRIVILEGED_TOOL_CATEGORIES): see the set's comment block. The
    # value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``; the
    # category key is KEBAB-case (``prompt-reload``) while the tool
    # name is snake_case (``prompt_reload``) — the house pattern
    # (``notify-user`` → ``notify_user``).
    "prompt-reload": "daemon.tools.prompt_reload",
    # prompt-assent (2026-11-10, prompt-assent lane): the conversational
    # approval lane — ari's four-verb assent surface (AR-2: list /
    # mint / approve / reject). PRIVILEGED category (entry #21 of
    # PRIVILEGED_TOOL_CATEGORIES): see the set's comment block. The
    # value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``; the
    # category key is KEBAB-case (``prompt-assent``) while the tool
    # name is snake_case (``prompt_assent``) — the house pattern
    # (``prompt-reload`` → ``prompt_reload``).
    "prompt-assent": "daemon.tools.prompt_assent",
    # guidelines (2026-11-13, guideline-read-tool lane): the READ-ONLY
    # window onto the shipped guideline corpus
    # (``guidelines_list`` / ``guideline_read``). PRIVILEGED category
    # (entry #22 of PRIVILEGED_TOOL_CATEGORIES): the corpus is the
    # doctrine layer for dormant / config-armed / ceremony-gated /
    # silently-biting features, so the empty-allow inherit universe
    # must never auto-grant it.
    #
    # The value MUST be a ``daemon.tools.*`` path — the AST scanner
    # resolves the value as ``daemon/tools/<parts[2:]>.py``; a
    # ``daemon.services.*`` value is NEVER discovered and the
    # exact-equality drift pin fails at boot. The category key is
    # EXACTLY ``guidelines`` (D2) while the tool names are snake_case
    # (``guidelines_list`` / ``guideline_read``) — the house pattern
    # (``self-alarm`` → ``self_alarm``, ``wedge-capture`` →
    # ``wedge_capture_*``).
    "guidelines": "daemon.tools.guidelines",
    # Mission tools (M2 of mission-class, 2026-09-02) — additive
    # READ-ONLY surface for the mission read-model projection. No
    # writers; census stays at 23.
    "mission": "daemon.tools.missions",
    "help": "daemon.tools.help",
    "mother": "daemon.tools.agent_mother",
    "knowledge": "daemon.tools.knowledge_tools",
    "chart": "daemon.tools.chart_tools",
    "image": "daemon.tools.image_tools",
    # Image-Gen tool (image-gen commission Phase 1, 2026-10-14) —
    # single-tool category exposing ``generate_image`` (direct POST to
    # the ensemble LLM lane's /image_generation endpoint; no agent
    # spawn behind it, so TOOL_REQUIRED_AGENTS does not apply).
    # NON-privileged: per-agent visibility is the uniform tools.allow
    # gate — the 6 consumer agents (ari, leader, developer[v2],
    # reviewer, tester, worker) carry the explicit ``"image-gen"``
    # allow token. Deliberately a SEPARATE category from "image" so
    # critic (allow ['read_file','image','design'] + per-tool
    # image_save deny) can never reach generation (decisions.md D3).
    # Caps/limits live as module constants in daemon/tools/image_gen.py
    # — NOT in daemon/constants.py (sealed).
    "image-gen": "daemon.tools.image_gen",
    "todo": "daemon.tools.todo_tools",
    "question": "daemon.tools.question_tools",
    # Mid-flight QA channel (2026-09-21) — non-blocking progress
    # surfacing to job watchers; opt-in per agent via tools.allow.
    "midflight": "daemon.tools.midflight_report",
    "rag": "daemon.tools.rag_tools",
    "mcp": "daemon.tools.mcp_tools",
    "context": "daemon.tools.context_tools",
    "shared_meta_kv": "daemon.tools.shared_meta_kv_tools",
    "db": "daemon.tools.db_tools",
    # Design tools (designer-agent P2-WP8, 2026-09-26) — image-comparator
    # specialist behind the ``compare_images`` facade. Category key MUST
    # byte-match ``@register_tool_category("design")`` in
    # ``daemon/tools/compare_tools.py`` and the ``TOOL_REQUIRED_AGENTS``
    # row in ``daemon/tools/_auth.py``. Row is load-bearing for boot-time
    # allow-list validation (registry.py known_categories) — without it
    # designer's ``tools.allow: ["design"]`` entry emits the
    # "neither a known category nor a known tool" WARNING (WP8 G2-A3).
    "design": "daemon.tools.compare_tools",
    # Live-views subsystem (Phase 1, 2026-10-07) — single-tool
    # category exposing ``view_link`` (URL minter over the
    # ``/views/<root>/<path>`` route family). Category key MUST
    # byte-match ``@register_tool_category("view-views")`` in
    # ``daemon/tools/live_views.py``, the three commissioned
    # ``tools.allow`` entries in ``agents/{ari,leader,designer}/
    # meta.json``, and the ``PRIVILEGED_TOOL_CATEGORIES`` row
    # in ``daemon/tools/_tool_registry.py``. Row is load-bearing
    # for boot-time allow-list validation — without it ari /
    # leader / designer emit three benign
    # "neither a known category nor a known tool" WARNINGS
    # (B3-F1, 2026-10-07) on every boot. Runtime is correct
    # (the warnings are noise; the per-tool allow-list
    # resolution still works because ``view_link`` IS the
    # only ``view-views`` tool and the category's privilege
    # gate fires downstream), but the warnings are visible
    # boot noise we want gone.
    "view-views": "daemon.tools.live_views",
    "infra": "daemon.tools.infra",
    "system": "daemon.tools.system",
    "skill-evolution": "daemon.tools.skill_evolution_tools",
    "dynamic-skill": "daemon.tools.skill_tools",
    "language": "daemon.tools.language_tools",
    "proc": "daemon.tools.proc_tools",
    "council": "daemon.tools.instance",  # Phase 2: spawn_councilor + clear_councilor_errors
    "blueprint": "daemon.tools.blueprint",
    "system-log": "daemon.tools.system_log_tools",
    "ens-db": "daemon.tools.ens_db_tools",  # W1-P2, task 2.3
    "plane": "daemon.tools.plane_tools",
    "plane_sync": "daemon.tools.plane_sync",
    "system_upgrade": "daemon.tools.upgrade_tools",
    # Leader completion attestation (Phase 1 of LCA feature, 2026-09-05) —
    # leader-scoped via explicit agents/leader/meta.json tools.allow
    # opt-in; deliberately NOT in PRIVILEGED_TOOL_CATEGORIES per D7
    # (CLOSED-by-leader) — full boundary argument in
    # daemon/tools/attestation.py module docstring header.
    "attestation": "daemon.tools.attestation",
    # service tool category (service-tool Phase 1, 2026-09-15) —
    # long-lived detached processes tracked in the service_tracking
    # table. Default-grant per override 2026-09-16 (see
    # .agents/shared/planning/service-tool/decisions.md §D4 override
    # note — D4 Option A reversed); the five tools are
    # factory-created (create_service_tools) and listed in
    # DYNAMIC_TOOL_NAMES below.
    "service": "daemon.tools.service_tools",
    # Agent Snapshot tools (agent-snapshot v1 Wave 2b, PR6) — one
    # module hosting the "snapshot" grant category: snapshot_create +
    # snapshot_search (per-tool creator grants). The module ALSO hosts
    # the unified-spawn consumption helpers (resolve_spawn_snapshot /
    # finalize_spawn_snapshot / format_snapshot_citation) consumed by
    # spawn_instance — those are plain functions, NOT @tool defs, so
    # the AST source discovery scans only the two tools here. Since
    # unify-spawn-tools (2026-10-05) spawn_hot_instance no longer
    # exists (its capability rides spawn_instance, gated by the
    # TARGET agent's snapshot_enabled meta flag).
    "snapshot": "daemon.tools.snapshot_tools",
    # ens-env category (Stage 1 of the self-provisioning chain,
    # feature/od-self-provisioning, 2026-10-02) — single-tool
    # category exposing ``ens_env_read`` so the install skills can
    # self-read the ensemble's live LLM connection values for the
    # BYOK reuse contract. IN ``PRIVILEGED_TOOL_CATEGORIES`` since the
    # W4 leader decision (reviewer council 2026-10-02):
    # ``ens_env_read`` is key-returning, so the empty-allow inherit
    # universe must NOT grant it — access requires an explicit
    # ``tools.allow: ["ens-env"]`` entry (worker carries it). See
    # ``daemon/tools/ens_env_tools.py`` module docstring for the full
    # contract (source-of-truth = env, no redaction in result,
    # audit-only log, PB-F1 checkpoint exposure scope).
    "ens-env": "daemon.tools.ens_env_tools",
    # agent-secrets P1 (2026-10-17, plan f11965039): the
    # ``secrets`` category owns six factory-built tools
    # (``secret_generate_ssh_keypair`` /
    # ``secret_generate_api_token`` / ``secret_store`` /
    # ``secret_list`` / ``secret_read_public`` / ``secret_retire``)
    # that operate on the Fernet-encrypted ``secrets`` +
    # ``secrets_audit`` PG tables via the in-process
    # ``SecretService`` (colocated ``SecretCipher`` reads
    # ``SYSTEM_ENCRYPTION_KEY`` at construction time). The
    # category is entry #6 in ``PRIVILEGED_TOOL_CATEGORIES``
    # (quintet → sextet; ``cu``, the category that joined
    # 2026-10-16 before this entry, was REMOVED 2026-10-24);
    # opt-in only via
    # ``tools.allow: ["secrets"]`` on the day-one allow-list
    # (Q2 ruling: ``worker``, ``tester``, ``leader``,
    # ``ari``). See ``daemon/tools/secrets_tools.py`` for the
    # full P1 contract (pubkey-only rule, marker mint format
    # FROZEN at ``__ENS_SECRET__<id>__``, etc.). The list-extend
    # is the third step of the three-step registration seam —
    # see ``daemon/tools/instance.py`` near the
    # ``create_ens_env_tools`` block for the parallel pattern.
    "secrets": "daemon.tools.secrets_tools",
    # settings-worker-slots P2 (2026-10-18): the ``settings``
    # category owns three factory-built tools
    # (``settings_get`` / ``settings_set`` / ``settings_clear``)
    # that read tier values and write DB overrides for the
    # daemon-global managed keys through the wired
    # ``SettingsService`` (router-owned DI global — the
    # council-B3 option-(b) seam). Category is entry #7 in
    # ``PRIVILEGED_TOOL_CATEGORIES`` (sextet → septet; ``secrets``
    # joined 2026-10-17 before this entry); opt-in only — the
    # ari + leader ``tools.allow`` entries landed 2026-10-18 (P3).
    # See ``daemon/tools/settings_tools.py`` for the honest
    # never-live contract (D5). The list-extend in
    # ``daemon/tools/instance.py`` (after the secrets block) is
    # required — decorator-only registration is SILENTLY
    # INVISIBLE.
    "settings": "daemon.tools.settings_tools",
}


# Static fallback universe of @tool-decorated function names across CATEGORY_MODULES.
#
# discover_all_tool_names() AST-scans source files on disk to find @tool-decorated
# functions at any nesting depth. In PyInstaller frozen binaries (e.g. ensemble-prod)
# daemon/ ships as bytecode only — file_path.exists() returns False for every
# category module and the discovery function silently returns an empty set. The
# result: every factory-created tool name (project_*, todo_view, terminate_instance,
# ...) vanishes from the validator universe and produces false-positive "unknown
# tool" warnings on agent boot (prod incident 2026-08-20 20:00:35, 32 warnings for
# agent project-manager).
#
# This constant is the frozen-binary fallback. discover_all_tool_names() falls back
# to set(KNOWN_TOOL_NAMES) when ZERO source files are readable, and merges with
# source-discovered names otherwise (source is canonical where present; the static
# list covers the rest).
#
# Maintenance: when you add a new @tool-decorated function to a CATEGORY_MODULES
# module, regenerate this set by running:
#
#     uv run python -c "from daemon.tools._tool_registry import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))"
#
# The output is the unambiguous source-only universe (no merge with this
# constant, no frozen fallback). In a frozen-binary environment this command
# fails loudly with RuntimeError instead of producing a false-OK output —
# drift detection only makes sense from source. Bidirectional drift between
# source and KNOWN_TOOL_NAMES is caught by the test
# tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift.
#
# Paste the printed (sorted) names into the frozenset below. Keep this
# constant adjacent to CATEGORY_MODULES so a maintainer adding a new tool module
# is looking at exactly this area.
KNOWN_TOOL_NAMES: frozenset[str] = frozenset({
    "access_memory",
    "agent_create",
    "agent_delete",
    "agent_list",
    "agent_modify",
    "agent_read",
    "ask_questions",
    "attest_completion",
    "await_mission",
    "bash",
    # nest-ops (2026-10-21): the instance fleet surfaces — created by
    # the create_instance_read_tools() (daemon/tools/instance_read.py)
    # and create_instance_ops_tools() (daemon/tools/instance_ops.py)
    # factories. Factory-built (not import-time registered), so they
    # belong in DYNAMIC_TOOL_NAMES and KNOWN_TOOL_NAMES. BOTH
    # categories are PRIVILEGED (#13 / #14) — opt-in only, never
    # default-granted; the maintenancer-only runtime gate lives in the
    # tool bodies.
    "instance_list",
    "instance_get",
    "instance_health",
    "fleet_capacity_summary",
    # F1 (nest-ops shakedown, 2026-10-24): the alias — MUST land in the
    # SAME commit as the closure, or the bidirectional drift pin
    # (tests/unit/tools/test_frozen_tool_name_discovery.py) fails in one
    # direction or the other.
    "instance_fleet_summary",
    "instance_terminate",
    "instance_reclaim",
    "instance_nudge",
    "instance_mark",
    "instance_answer",
    "instance_dismiss",
    # ari-tools Phase 1 (the Workbook): board tools — created by the
    # create_board_tools() factory (daemon/tools/board_tools.py).
    # Factory-built (not import-time registered), so they belong in
    # DYNAMIC_TOOL_NAMES and KNOWN_TOOL_NAMES. The category is
    # PRIVILEGED (entry #8 of PRIVILEGED_TOOL_CATEGORIES) — opt-in
    # only, never default-granted.
    "board_create",
    "board_get",
    "board_list",
    "board_update",
    # ari-tools Phase 2 (the self-alarm wrapper): the self_alarm tool —
    # created by the create_self_alarm_tools() factory
    # (daemon/tools/self_alarm.py). Factory-built (not import-time
    # registered), so it belongs in DYNAMIC_TOOL_NAMES and
    # KNOWN_TOOL_NAMES. The category is PRIVILEGED (entry #9 of
    # PRIVILEGED_TOOL_CATEGORIES) — opt-in only, never default-granted;
    # the ari-only runtime gate lives in the tool body.
    "self_alarm",
    # ari-tools Phase 3 (the live-views surface): view_write — writes an
    # HTML page under the daemon-global ``reports`` live-view root and
    # returns the passcode-gated public URL in ONE call. Created by the
    # create_view_write_tools() factory (daemon/tools/view_write.py).
    # Factory-built (not import-time registered), so it belongs in
    # DYNAMIC_TOOL_NAMES and KNOWN_TOOL_NAMES. The category is PRIVILEGED
    # (entry #10 of PRIVILEGED_TOOL_CATEGORIES) — opt-in only, never
    # default-granted; the ari-only runtime gate lives in the tool body.
    "view_write",
    # ari-tools Phase 3 (the live board view): board_link — mints the
    # passcode-gated public link for the server-rendered board at
    # /views/public/board/index.html. Created by the
    # create_board_link_tools() factory (daemon/tools/board_link.py).
    # Factory-built (not import-time registered), so it belongs in
    # DYNAMIC_TOOL_NAMES and KNOWN_TOOL_NAMES. The category is PRIVILEGED
    # (entry #11 of PRIVILEGED_TOOL_CATEGORIES) — opt-in only, never
    # default-granted; the ari+leader runtime gate lives in the tool body.
    "board_link",
    # wedge-capture (2026-10-22): the two READ-ONLY capture-evidence
    # tools — created by the create_wedge_capture_tools() factory
    # (daemon/tools/wedge_capture.py). Factory-built (not import-time
    # registered), so they belong in DYNAMIC_TOOL_NAMES and
    # KNOWN_TOOL_NAMES. The category is PRIVILEGED (entry #16 of
    # PRIVILEGED_TOOL_CATEGORIES) — opt-in only, never default-granted;
    # the runtime agent gate lives in the tool bodies.
    "wedge_capture_list",
    "wedge_capture_get",
    # ari-voice (2026-10-25): the notify tool — created by the
    # create_notify_user_tools() factory (daemon/tools/notify_user.py).
    # Factory-built (not import-time registered), so it belongs in
    # DYNAMIC_TOOL_NAMES and KNOWN_TOOL_NAMES. The category is
    # PRIVILEGED (entry #16) — opt-in only, never default-granted; the
    # ari-only runtime gate lives in the tool body.
    "notify_user",
    "blueprint_acknowledge_pending",
    "blueprint_claim_pending",
    "blueprint_create",
    "blueprint_disable",
    "blueprint_get",
    "blueprint_get_pending_count",
    "blueprint_list",
    "blueprint_release_lease",
    "blueprint_search",
    "blueprint_update",
    "clear_councilor_errors",
    "compare_images",
    "convene_council",
    "convene_council_with_skill",
    "db_conn_add",
    "db_conn_delete",
    "db_conn_list",
    "db_conn_test",
    "db_postgres_dml_select",
    "dlq_list",
    "dlq_replay",
    "edit_file",
    "ens_db_inspect",
    "ens_db_pool_status",
    "ens_db_postgres_select",
    "ens_db_repair_execute",
    "ens_env_read",
    "ens_system_log_list",
    "ens_system_log_read",
    "ens_system_log_search",
    "ens_system_log_tail",
    "experience",
    "explain_image",
    "explore",
    "generate_chart",
    # generate_image — image-gen commission Phase 1 (2026-10-14);
    # factory-created per-instance (create_image_gen_tools,
    # daemon/tools/image_gen.py), category "image-gen".
    "generate_image",
    # image_save / image_list / image_get — designer-agent substrate
    # (Phase 1 WP7/8/9) — factory-created per-instance alongside
    # explain_image; never registered at module import. The
    # explicit names allow agents' ``tools.allow`` to validate the
    # image category at startup.
    "image_save",
    "image_list",
    "image_get",
    "get_instance_info",
    "get_mission",
    "glob_files",
    "grep_files",
    "infra_asset_create",
    "infra_asset_delete",
    "infra_asset_get",
    "infra_asset_list",
    "infra_asset_search",
    "infra_asset_update",
    "infra_history_get",
    "infra_type_list",
    "infra_type_register",
    "inner_soul",
    "job_answer",
    "job_cancel",
    "job_continue",
    "job_create",
    "job_delete",
    "job_get",
    "job_inject",
    "job_list",
    "job_messages",
    "job_pause",
    "job_progress",
    "job_restore",
    "job_resume",
    "job_retry",
    "job_tree",
    "kms_attach",
    "kms_lookup_handle",
    "kms_request",
    "language_skip_check",
    "list_context",
    "list_directory",
    "list_instances",
    "list_missions",
    "list_watched_jobs",
    "mcp_set_env",
    # remote-hands center gateway (2026-10-22): the four agent-facing
    # tools of the ``mcp_rpc`` category — created by the
    # create_mcp_rpc_tools() factory (daemon/tools/mcp_rpc_tools.py).
    # Factory-built (not import-time registered), so they belong in
    # DYNAMIC_TOOL_NAMES and KNOWN_TOOL_NAMES. The category is
    # PRIVILEGED (entry #15 of PRIVILEGED_TOOL_CATEGORIES) — opt-in
    # only, never default-granted; the ari/leader-only runtime gate
    # lives in the tool bodies.
    "mcp_rpc_list_machine",
    "mcp_rpc_connect",
    "mcp_rpc_command",
    "mcp_rpc_disconnect",
    "mid_flight_report",
    "pause_instance",
    "plane_sync_project",
    "proc_list",
    "proc_logs",
    "proc_run",
    "proc_status",
    "proc_stop",
    "project_add_directory",
    "project_add_shortname",
    "project_add_tag",
    "project_cn_add",
    "project_cn_backfill_embeddings",
    "project_cn_list",
    "project_cn_pin",
    "project_cn_remove",
    "project_cn_supersede",
    "project_create",
    "project_delete",
    "project_delete_metadata",
    "project_get",
    "project_get_by_directory",
    "project_get_by_instance",
    "project_history_add",
    "project_history_delete",
    "project_history_list",
    "project_history_search",
    "project_link",
    "project_list",
    "project_remove_directory",
    "project_remove_shortname",
    "project_remove_tag",
    "project_search",
    "project_set_metadata",
    "project_set_shortnames",
    "project_set_status",
    "project_set_tags",
    "project_unlink",
    # POK Phase 1 (project-operating-knowledge, 2026-11-07): the three
    # project operating-knowledge tools (daemon/tools/pok.py — the
    # 'pok' non-privileged category; B4 sweep grants via tools.allow).
    "pok_upsert",
    "pok_query",
    "pok_curate",
    "project_update",
    "queue_create",
    "queue_list",
    "queue_update",
    "rag_create_entity",
    "rag_create_relation",
    "rag_delete_docs",
    "rag_delete_entity",
    "rag_delete_relation",
    "rag_get_entity",
    "rag_get_graph",
    "rag_insert_text",
    "rag_insert_texts",
    "rag_list_docs",
    "rag_merge_entities",
    "rag_query",
    "rag_query_data",
    "rag_search_labels",
    "rag_track_status",
    "rag_update_entity",
    "read_context",
    "read_file",
    "release_info",
    # nest-report (2026-11-05, nest-anomaly-report lane): the ONE write
    # tool of the nest-anomaly surface — created by the
    # create_nest_report_tools() factory
    # (daemon/tools/nest_report.py). Factory-built (not import-time
    # registered), so it belongs in DYNAMIC_TOOL_NAMES and
    # KNOWN_TOOL_NAMES. The category is NOT privileged (R4): the
    # empty-allow inherit universe DOES grant it; the explicit
    # ``tools.allow: ["nest-report"]`` grant lands in the phase-5
    # ceremony.
    "report_anomaly",
    "resume_instance",
    "send_message",
    "service_list",
    "service_logs",
    "service_start",
    "service_status",
    "service_stop",
    "set_instance_tunable",
    # settings-worker-slots P2 (2026-10-18): three factory-built
    # tools behind the ``settings`` privileged category. Same
    # frozen-binary-fallback rationale as the ``secret_*`` block
    # below — in PyInstaller-frozen binaries the source walker
    # falls back to this static universe, so the validator never
    # false-fails on a frozen install. Names match the actual
    # tool NAMES exposed by
    # ``daemon/tools/settings_tools.py:create_settings_tools``
    # (flat snake_case convention).
    "settings_clear",
    "settings_get",
    "settings_set",
    "shared_meta_kv",
    "skill_analyze",
    "skill_create",
    "skill_evolve",
    "skill_execute_capture",
    "skill_feedback",
    "skill_fix",
    "skill_get_metrics",
    "skill_list",
    "skill_resolve_ab",
    "skill_search",
    "skill_view",
    "snapshot_create",
    "snapshot_search",
    "spawn_councilor",
    "spawn_instance",
    "subtree_messages",
    "subtree_status",
    "system_config",
    "system_env",
    "system_health",
    "system_restart",
    "system_upgrade",
    # scheduling tools (scheduled-tasks phase 2+4) — factory-created
    # (create_scheduling_tools, daemon/tools/scheduling.py); regenerated
    # via the discover_source_only_tool_names command above.
    "task_schedule",
    "task_schedule_cancel",
    "task_schedule_list",
    "task_schedule_update",
    # agent-secrets P1 (2026-10-17, plan f11965039): six
    # factory-built tools behind the ``secrets`` privileged
    # category. Same frozen-binary-fallback rationale as
    # ``ens_env_read`` (the discover-source-only walker falls
    # back to ``KNOWN_TOOL_NAMES`` in PyInstaller-frozen
    # binaries; the static fallback universe is the safety
    # net so the validator never false-fails on a frozen
    # install). Names match the actual tool NAMES exposed
    # by ``daemon/tools/secrets_tools.py:create_secret_tools``
    # (flat snake_case convention, ``kms_attach`` /
    # ``ens_env_read`` precedent).
    "secret_generate_ssh_keypair",
    "secret_generate_api_token",
    "secret_store",
    "secret_list",
    "secret_read_public",
    "secret_retire",
    # metrics-read (2026-11-03, metrics-agent-tools lane): the three
    # READ-ONLY metric-registry tools — created by the
    # create_metrics_read_tools() factory (daemon/tools/metrics_read.py).
    # Factory-built (not import-time registered), so they belong in
    # DYNAMIC_TOOL_NAMES and KNOWN_TOOL_NAMES. The category is
    # PRIVILEGED (entry #17) — opt-in only, never default-granted; the
    # runtime agent gate lives in the tool bodies.
    "metrics_families",
    "metrics_snapshot",
    "metrics_history",
    # nest-report-read (2026-11-05, nest-anomaly-report lane): the two
    # READ-ONLY anomaly-store tools — created by the
    # create_nest_report_read_tools() factory
    # (daemon/tools/nest_report_read.py). Factory-built (not
    # import-time registered), so they belong in DYNAMIC_TOOL_NAMES and
    # KNOWN_TOOL_NAMES. The category is PRIVILEGED (entry #18); the
    # runtime agent gate lives in the tool bodies.
    "anomaly_clusters",
    "anomaly_report_detail",
    # memoir (2026-11-05, memoir-store-v1 lane): the five memoir tools —
    # created by the create_memoir_tools() factory
    # (daemon/tools/memoir.py). Factory-built (not import-time
    # registered), so they belong in DYNAMIC_TOOL_NAMES and
    # KNOWN_TOOL_NAMES. The category is PRIVILEGED (entry #18) — opt-in
    # only, never default-granted; the ari-only runtime gate lives in
    # the tool bodies.
    "memoir_write",
    "memoir_pin",
    "memoir_unpin",
    "memoir_recall",
    "memoir_timeline",
    "memoir_about",
    "terminate_instance",
    "time",
    "todo_clear",
    "todo_graph_add_edge",
    "todo_graph_add_subtask",
    "todo_graph_create",
    "todo_graph_remove_edge",
    "todo_graph_remove_subtask",
    "todo_graph_update",
    "todo_graph_update_subtask",
    "todo_list_create",
    "todo_list_update",
    "todo_view",
    "tool_help",
    "unwatch_job",
    "upgrade_status",
    "view_link",
    "watch_job",
    "watch_jobs",
    "watch_mission",
    "write_file",
    # Factory-built (prompt-reload lane, 2026-11-06) — see
    # DYNAMIC_TOOL_NAMES for the registration note.
    "prompt_reload",
    # Factory-built (prompt-assent lane, 2026-11-10) — see
    # DYNAMIC_TOOL_NAMES for the registration note.
    "prompt_assent",
    # Factory-built (guideline-read-tool lane, 2026-11-13) — see
    # DYNAMIC_TOOL_NAMES for the registration note.
    "guidelines_list",
    "guideline_read",
})


def get_tool_categories(allowed_tools: set[str] | None = None) -> dict[str, list[str]]:
    """Get tools grouped by their category, using CATEGORY_NAME as keys.
    
    Args:
        allowed_tools: Optional set of tool names to filter by.
    
    Returns:
        Dictionary mapping CATEGORY_NAME to list of tool names.
    """
    categories: dict[str, list[str]] = {}
    
    for tool_name, meta in _tool_metadata.items():
        # Filter by allowed_tools if provided
        if allowed_tools is not None and tool_name not in allowed_tools:
            continue
        
        category_key = meta.get("category", "general")
        
        # Look up CATEGORY_NAME from the module
        if category_key in CATEGORY_MODULES:
            try:
                module_paths = CATEGORY_MODULES[category_key]
                # Handle both str and list[str] values
                first_module = module_paths[0] if isinstance(module_paths, list) else module_paths
                module = import_module(first_module)
                category_name = getattr(module, "CATEGORY_NAME", category_key)
            except ImportError:
                category_name = category_key
        else:
            category_name = category_key
        
        if category_name not in categories:
            categories[category_name] = []
        categories[category_name].append(tool_name)
    
    return categories


def get_category_doc(category_key: str) -> tuple[str, str] | None:
    """Get CATEGORY_NAME and CATEGORY_DOC for a category.
    
    Args:
        category_key: The category key (e.g., "bash", "filesystem").
    
    Returns:
        Tuple of (CATEGORY_NAME, CATEGORY_DOC), or None if category doesn't exist.
    """
    if category_key not in CATEGORY_MODULES:
        return None
    
    try:
        module_paths = CATEGORY_MODULES[category_key]
        # Handle both str and list[str] values
        if isinstance(module_paths, list):
            category_name = None
            category_docs: list[str] = []
            for path in module_paths:
                module = import_module(path)
                if category_name is None:
                    category_name = getattr(module, "CATEGORY_NAME", category_key)
                doc = getattr(module, "CATEGORY_DOC", "")
                if doc:
                    category_docs.append(doc)
            return (category_name or category_key, "\n\n".join(category_docs))
        else:
            module = import_module(module_paths)
            return (
                getattr(module, "CATEGORY_NAME", category_key),
                getattr(module, "CATEGORY_DOC", ""),
            )
    except ImportError:
        return None
