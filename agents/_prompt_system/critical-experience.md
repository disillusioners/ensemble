# Critical Experience

Structured list of concise, high-value project knowledge attached to a project. Always visible to all agents working on the project.

## Writing Guidelines

- **Actionable**: Tells agent WHAT to do or NOT do
- **≤200 characters**: Keep summaries brief
- **Project-specific**: Not general knowledge
- **Imperative mood**: "Use yarn, not npm" not "This project uses yarn"

## Categories

| Category | Description |
|----------|-------------|
| `convention` | Standards the project follows |
| `pattern` | Recurring solutions used in the project |
| `risk` | Things that can go wrong or must be avoided |
| `decision` | Key architectural or design decisions made |
| `constraint` | Technical limitations that must be respected |

## Priority Assignment

| Priority | Use When |
|----------|----------|
| `critical` | Security issues, data loss risks, race conditions, breaking changes |
| `high` | Important patterns, key architectural decisions, critical dependencies |
| `medium` | Conventions, nice-to-know patterns, soft constraints |

## Example Entries

- convention: "Use yarn, not npm — project standard"
- convention: "Node.js must be v20.x — incompatible with v22"
- constraint: "Library X must stay at version 2.3.1 — upgrading breaks API"
- convention: "Always use python venv — run `source venv/bin/activate`"
- pattern: "Run dev.sh for dev environment setup — handles all config"
- risk: "Database migrations run on startup — never manually alter schema"
- risk: "Tests must pass before merge — CI enforces this strictly"
- decision: "Chose SQLite over PostgreSQL for simplicity — single-user design"
- pattern: "All API responses follow {success, data, error} envelope"
- constraint: "No external API calls in tests — mock everything"
- convention: "Use SQLModel for all DB models — project standard ORM"
- risk: "File uploads limited to 10MB — nginx will reject larger"

## RAG vs CE Routing

| Use CE | Use RAG |
|--------|---------|
| Actionable, concise | General knowledge |
| Project-specific | Verbose |
| High-impact | Needs full context |
| | Not actionable |
