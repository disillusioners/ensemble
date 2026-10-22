// Remote Hands / MCP Gateway — the READ-ONLY operator surface (Phase C).
//
// Mirrors the BE wire contract VERBATIM:
//
//   daemon/routers/mcps_gateway_schemas.py  — the Pydantic response models
//   daemon/routers/mcps_gateway_read.py     — the three GET routes
//   docs/mcps-gateway.md §8.3               — the JSON contract
//
// Conventions (the house FE-model conventions — snapshot.model.ts is the
// precedent):
//
// * The field names are the WIRE names (snake_case), exactly as they
//   arrive. The page types against the wire as-is — no FE-side renaming
//   layer (that is what the D-2 rename in the snapshots family was for;
//   here the wire names ARE the model names).
// * Timestamps are ISO-8601 UTC strings WITH an explicit offset
//   (`2026-10-22T07:31:04.512000+00:00`) — NEVER epoch-ms. The
//   epoch-milliseconds shape belongs to the AGENT-facing tool surface
//   (`mcp_rpc_*`), not to the browser.
// * Every response carries `gateway_enabled` — the kill-switch state
//   (`MCPS_GATEWAY_ENABLED`, DEFAULT OFF) — so the page can tell "the
//   gateway is OFF" apart from "nothing is enrolled" instead of guessing
//   from an empty list.
// * Every response carries `count`. On the audit tail `count` is the
//   PAGE LENGTH, NOT the ledger total (the read surface does not expose
//   the ledger size) — the pagination contract depends on it.

/**
 * The presence states the roster derives from the heartbeat (SPEC §8.1).
 */
export type McpsGatewayPresence = 'online' | 'stale' | 'offline';

/**
 * The audit outcome vocabulary (SPEC §8.4): `ok` / `error` / `denied` /
 * `timeout`.
 */
export type McpsGatewayAuditOutcome = 'ok' | 'error' | 'denied' | 'timeout';

/**
 * One enrolled machine of the roster (SPEC §8.1).
 */
export interface McpsGatewayMachine {
  /** The stable key of the machine. */
  machine_id: string;
  /**
   * `online` / `stale` / `offline`.
   *
   * Deliberately widened to `string`: the BE models `presence` as a bare
   * `str`, so a NEWER daemon may emit a value this page has never seen.
   * An unknown presence renders as plain text — it must never crash the
   * page (same posture as the audit outcome, below).
   */
  presence: McpsGatewayPresence | string;
  /** The boolean face of `presence` (`presence === "online"`). */
  online: boolean;
  /** Whether the socket is open (the machine upgraded, not disconnected). */
  connected: boolean;
  /** ISO-8601 UTC stamp of the last heartbeat, or `null` if never seen. */
  last_seen_at: string | null;
  /** The ACTIVE session on this machine (the §8.3 lock pointer), or `null`. */
  session_id: string | null;
  /** The agent holding that active session, or `null`. */
  agent_id: string | null;
  /** Whether a session holds the machine's exclusive slot (`MACHINE_BUSY`). */
  busy: boolean;
}

/**
 * One live session of the center (SPEC §6.4 / §8.3).
 */
export interface McpsGatewaySession {
  /** The id the MACHINE minted (§6.4 — the center adopts it). */
  session_id: string;
  /** The machine the session runs on. */
  machine_id: string;
  /** The agent that holds the session. */
  agent_id: string;
  /** The instance of that agent. */
  instance_id: string;
  /**
   * The lifecycle state — `live` in v0.1 (the center keeps only live
   * sessions; a session dies with its connection, §6.8).
   */
  state: string;
  /** Whether this session holds the machine's exclusive §8.3 lock. */
  machine_locked: boolean;
  /** ISO-8601 UTC stamp of `session.begin`, or `null`. */
  started_at: string | null;
  /** ISO-8601 UTC stamp of the last traffic on the session, or `null`. */
  last_activity_at: string | null;
  /** ISO-8601 UTC stamp of the TTL ceiling (§3.3), or `null`. */
  expires_at: string | null;
  /** The names of the remote tools the machine reported in its catalog. */
  tools: string[];
}

/**
 * One row of the remote-command audit (SPEC §8.4).
 */
export interface McpsGatewayAuditEntry {
  /** ISO-8601 UTC stamp of the row, or `null`. */
  timestamp: string | null;
  /** WHO asked. */
  agent_id: string;
  /** The instance of that agent, when known — else `null`. */
  instance_id: string | null;
  /** The machine the command ran on. */
  machine_id: string;
  /** The name of the remote tool. */
  tool: string;
  /**
   * `ok` / `error` / `denied` / `timeout` — widened to `string` for the
   * same reason as `presence`: an unknown value renders as plain text.
   */
  outcome: McpsGatewayAuditOutcome | string;
  /** The session the command ran in, when known — else `null`. */
  session_id: string | null;
  /** The typed error code, when the command failed — else `null`. */
  error_code: string | null;
  /** The SHA-256 of the canonicalised arguments (the args are never stored). */
  args_hash: string;
}

/**
 * Body of `GET /api/mcps-gateway/machines`.
 */
export interface McpsGatewayMachinesResponse {
  machines: McpsGatewayMachine[];
  /** The number of entries in THIS list. */
  count: number;
  /** The kill-switch state (`MCPS_GATEWAY_ENABLED`). */
  gateway_enabled: boolean;
}

/**
 * Body of `GET /api/mcps-gateway/sessions`.
 */
export interface McpsGatewaySessionsResponse {
  sessions: McpsGatewaySession[];
  /** The number of entries in THIS list. */
  count: number;
  /** The kill-switch state (`MCPS_GATEWAY_ENABLED`). */
  gateway_enabled: boolean;
}

/**
 * Body of `GET /api/mcps-gateway/audit?limit=&offset=`.
 */
export interface McpsGatewayAuditResponse {
  /** The audit rows, NEWEST FIRST (the BE never re-sorts them). */
  audit: McpsGatewayAuditEntry[];
  /**
   * The length of THIS PAGE — NOT the ledger total (the read surface
   * does not expose the ledger size).
   */
  count: number;
  /** The page size the server applied (echoed back). */
  limit: number;
  /** The offset the server applied (echoed back). */
  offset: number;
  /** The kill-switch state (`MCPS_GATEWAY_ENABLED`). */
  gateway_enabled: boolean;
}

/**
 * Which lane of the read surface produced an error.
 */
export type McpsGatewayErrorSource = 'machines' | 'sessions' | 'audit';

/**
 * The decoded failure of a read-surface call.
 *
 * `code` is the machine code the page branches on:
 *
 * * `gateway_unavailable` — the 503 the daemon answers when the gateway
 *   service is not wired on `app.state` (a boot-order defect — NOT an
 *   empty roster);
 * * `SCOPE_INSUFFICIENT` — the 403 a credential without the `mcp:read`
 *   scope gets;
 * * `network_error` — the daemon could not be reached at all;
 * * `http_<status>` — any other HTTP failure.
 */
export interface McpsGatewayError {
  /** The machine code (see above). */
  code: string;
  /** The human-facing message the page renders verbatim. */
  message: string;
  /** The HTTP status (0 for a transport-level failure). */
  status: number;
  /** The lane that failed. */
  source: McpsGatewayErrorSource;
}
