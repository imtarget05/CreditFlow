# CreditFlow synthetic graph benchmark — 2026-11-20

[Raw 20-case result](local-graph-benchmark-20-2026-11-20.json) from commit
`7fc1e15` used one local Docker API container, SQLite ledger and bundled
synthetic model. Each sequential case created a fresh REVIEW workflow,
approved it, replayed the same idempotency key, checked a conflicting action
and a new key were rejected, then verified one disbursement and the audit.
No Azure, external bank ledger, or live LLM was used.

| Metric | Result |
| --- | ---: |
| Task completion | 20/20 |
| Full workflow p50 / p95 | 595.5 / 1,165 ms |
| Start decision p50 / p95 | 422.5 / 845 ms |
| Approval p50 / p95 | 166 / 307 ms |
| Explanation source | `template` in all 20 cases |

The full workflow timing includes the API calls for start, approve, replay,
conflict checks, ledger read and audit. Start and approval timings measure
those respective requests. p50 is the median; p95 is nearest rank among
completed cases. Failures would remain in the task-completion denominator and
be excluded from latency quantiles. With only 20 sequential local cases, the
p95 is a small-sample observation rather than a service-level guarantee.
`risk_score` comes from a model trained on synthetic proxy data; the approval
is a human action in this demo, not a validated bank underwriting decision.
The explanation is a deterministic template fallback, not an LLM result.
