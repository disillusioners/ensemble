---
version: 1.0.0
category: execution
auto_load: false
---

# Integration Testing

Integration tests verify that independently-developed components interact correctly across boundaries — APIs, services, databases, message queues, external dependencies. Unlike unit tests (single module in isolation) and E2E tests (full user journey through real UI), integration tests target the seams between modules and systems.

## When to Apply

Use this skill when validating:

- **Cross-component interactions** — module A's output drives module B's behavior
- **API boundary contracts** — request/response shapes, status codes, error envelopes
- **Service-to-service communication** — HTTP/REST, gRPC, message queues, pub/sub
- **Database integration** — SQL behavior, transactions, migrations, ORM round-trips
- **External dependency boundaries** — third-party SDKs, payment gateways, auth providers
- **Contract testing** — consumer-driven contracts between producer and consumer services

Do NOT use this skill for:

- Pure unit testing (single function/class in isolation) → use the unit-test skill
- Full user-journey browser tests → use the e2e-test skill
- Mock-based service isolation testing → use the mock-test skill

## Pack Structure

Integration tests run through packs (see test-pack-execution skill for full pack conventions). Integration packs follow:

- **Naming**: `[module]_integration_test.sh` (or `tests/packs/[module]_integration_test/`)
- **Timeout**: 5 minutes (integration/feature pack hard cap)
- **Output**: PASS / FAIL / TIMEOUT with exit codes 0 / 1 / 124
- **Scope**: One module or one integration seam per pack

Register every integration pack in `PACKS.md` under the **Integration Test Packs** section.

## Identifying Integration Points

Before writing tests, map the integration surface:

1. **Inspect module boundaries** — where does one module hand off to another? Look for: HTTP clients, DB drivers, message publishers, RPC stubs, SDK calls
2. **Find external dependencies** — `requirements.txt`, `package.json`, `go.mod`, `Cargo.toml`; flag services that leave the process boundary
3. **Identify data flow seams** — what crosses the boundary? (payloads, IDs, tokens, status enums)
4. **Locate shared state** — databases, caches, queues; document setup/teardown needs
5. **Enumerate failure modes** — network errors, timeouts, partial failures, retries, idempotency

Document the integration map in `MOCK_TESTS.md` (when services are mocked) or in `PACKS.md` entries (when real services are used).

## Testing API Boundaries

When the seam is an HTTP/gRPC/REST API:

### Contract Validation

- **Request shape** — verify required fields, types, defaults, edge values (empty, max-length, unicode)
- **Response shape** — assert the full response body matches contract; reject extra/missing fields
- **Status codes** — cover 200/201/204 happy path; 400 (bad input), 401 (unauth), 403 (forbidden), 404 (not found), 409 (conflict), 422 (validation), 500 (server error)
- **Error envelopes** — error responses must follow a documented structure (e.g., `{"error": {"code": "...", "message": "..."}}`)
- **Headers** — content-type, auth tokens, rate-limit headers, caching headers

### Auth and Authorization

- Test with valid credentials (happy path)
- Test with expired/missing/malformed tokens → expect 401
- Test with insufficient permissions → expect 403
- Test token refresh flows when applicable

### Idempotency and Retries

- Repeat the same request → same result (for GET, PUT, DELETE)
- For POST that should be idempotent: verify key/idempotency-token behavior
- Verify retry behavior — does the API respond correctly to `Retry-After`?

### Task Message for API Boundary Pack

When delegating to opencode, use the strict single-pack template (see test-pack-execution skill):

```
Pack: [path/to/<module>_integration_test.sh]
Scope: API boundary tests for [endpoint(s)]
Coverage:
- Happy path: [list primary scenarios]
- Error path: [list expected failure codes]
- Auth: [list auth scenarios]
```

## Database Integration Testing

### Setup Strategy

Choose based on the test goal:

| Strategy | Use When | Pros | Cons |
|----------|----------|------|------|
| **Real DB (ephemeral)** | Need real SQL semantics, transactions, constraints | Most accurate; tests real DB behavior | Slower; needs cleanup |
| **Transaction rollback** | Each test starts in a tx, rolls back at end | Fast; isolated | Doesn't test commit behavior |
| **In-memory (SQLite)** | Light logic, no DB-specific features | Very fast | May diverge from prod behavior |
| **Testcontainers** | Need real DB version (Postgres, MySQL) | Accurate, isolated | Slower startup |

Default to **transaction rollback** for most logic tests; reserve **testcontainers** for cases where SQL semantics matter.

### Required Fixtures

- **Schema** — apply migrations to a fresh DB per test suite, OR wrap each test in a savepoint
- **Seed data** — minimal realistic data; avoid `User.objects.first()`-style assumptions
- **Connection lifecycle** — open per-test or per-suite? Document in pack script
- **Cleanup** — drop test DB, kill connections, free ports (10000-19999 for test DBs)

### Critical Test Scenarios

- **CRUD round-trips** — create, read, update, delete with assertions at each step
- **Transactions** — commit succeeds, rollback restores prior state
- **Constraints** — FK violations, unique constraints, NOT NULL violations
- **Concurrent writes** — only if concurrency matters; otherwise skip
- **Migrations** — apply pending migrations, verify schema matches expectations

## Service Interaction Testing

When modules talk via queues, RPC, or HTTP:

### Message Queue Integration

- **Publish/consume round-trip** — producer publishes, consumer receives the exact payload
- **Acknowledgment semantics** — what happens if consumer crashes mid-processing?
- **Dead-letter handling** — malformed messages route to DLQ, not infinite retry
- **Ordering** — if ordering matters, verify it; if not, document the choice

### RPC/gRPC Integration

- **Schema validation** — request/response types match proto definitions
- **Streaming behavior** — for streaming RPCs, verify backpressure, cancellation
- **Error codes** — gRPC status codes (`UNAUTHENTICATED`, `DEADLINE_EXCEEDED`, etc.)

### HTTP Service-to-Service

- **Timeouts** — connect timeout, read timeout, total timeout all configured
- **Circuit breakers** — fail fast when downstream is degraded
- **Retries with backoff** — exponential, jittered, capped
- **Health checks** — `/health`, `/ready` endpoints respond correctly

## Contract Testing

When multiple teams/services evolve independently, contract tests catch breaking changes:

### Consumer-Driven Contracts

- Consumer defines expected request/response
- Provider verifies it can satisfy all consumer contracts
- Run on every build (provider side) and on consumer side

### Tools by Ecosystem

- **Python**: `pact-python`, `schemathesis`
- **JavaScript/TypeScript**: `pact-js`, `schemathesis`
- **Go**: `pact-go`
- **Java**: `pact-jvm`
- **General**: OpenAPI/Swagger schema validation with `schemathesis` or equivalent

### What to Verify

- All paths in the contract are exercised
- Required fields are present in responses
- Types match (string vs number, object vs array)
- Enum values are within the allowed set
- New required fields in contract → all consumers updated

## Test Fixtures and Setup

### Fixture Types

- **Module-scoped** — created once per pack; reused across tests in the pack
- **Function-scoped** — created per test; slower but isolated
- **Session-scoped** — for the whole test run; only when truly stateless

### Setup Patterns

- **Arrange-Act-Assert** — set up state, execute action, verify outcome
- **Builder pattern** — `UserBuilder().with_email("...").with_role("admin").build()` for readable fixtures
- **Factory pattern** — `create_user(role="admin")` returns a fully-valid instance
- **Truncation vs rollback** — truncate tables between tests (faster) vs wrap in tx (more isolated)

### Teardown Discipline

Every integration test MUST clean up:

- Drop test databases / truncate tables
- Kill background processes (DB connections, mock servers)
- Free ports (especially mock ports in 10000-19999)
- Delete temp files
- Close any open file handles

## Integration Tests vs Unit Tests

| Aspect | Unit Tests | Integration Tests |
|--------|------------|-------------------|
| Scope | Single function/class | Module or boundary |
| Speed | < 100ms each | Seconds to minutes |
| Dependencies | All mocked | Some real (DB, queue, external service) |
| Purpose | Verify logic | Verify wiring |
| Failure meaning | Logic bug | Wiring, contract, or env bug |
| Pack timeout | 2 min (unit hard cap) | 5 min (integration hard cap) |

**Rule of thumb**: if a unit test fails, fix the unit. If an integration test fails with all unit tests passing, suspect the seam.

## Reporting Failures

When an integration test fails, capture in the report:

- **Module pair** — which two modules were interacting
- **Failure point** — request? response? mid-flight?
- **Payload** — request body, response body (with sensitive data redacted)
- **Logs** — both sides of the boundary
- **Environment** — DB version, service version, network conditions
- **Reproducibility** — always reproducible? intermittent? (intermittent → flaky-test-management skill)

## Decision Flow

```
Is the boundary within a single process?
├─ Yes → unit test
└─ No → Is it a single user journey through UI?
       ├─ Yes → E2E test (see e2e-test skill)
       └─ No → Is it a service with HTTP/gRPC/queue boundary?
              ├─ Yes → integration test (this skill)
              └─ No → unit test
```