# ADR-0001: Idempotency-Key Disbursement vs Distributed Lock

- **Status:** Accepted
- **Date:** 2026-09-27

## Context

Disbursement moves money: `APPROVE → contract HDTD-YYYYMMDD-XXXX → ledger row
+ SHA-256 hash`. Network timeouts between approval and ledger write are
inevitable, and operators retry. A retried disbursement must never create a
second contract or second payout for the same approval.

## Decision

Make disbursement idempotent on `(application_id, idempotency_key)`: the
ledger has a unique constraint on the key; a replay returns the original
contract row without inserting. No distributed lock is introduced; SQLite
single-writer semantics plus the unique constraint are the concurrency control.

## Consequences

- Positive: retries are safe by construction; no lock-service dependency or
  deadlock class; replay test (same key twice → one ledger row) is deterministic.
- Negative: callers must supply stable keys (server generates one if absent and
  returns it); key-space hygiene (TTL/archival) is future work.

## Alternatives

- Distributed lock (Redis Redlock): serialises but does NOT dedupe — a retry
  after a timeout still double-pays inside a fresh lock hold; adds an outage
  domain where lock-down means lending-down.
- Exactly-once messaging: requires transactional outbox + broker; heavier than
  a unique constraint for branch-scale single-writer throughput.
