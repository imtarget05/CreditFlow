# CreditFlow local graph and ledger drill — 2026-11-19

The [machine-readable result](local-graph-demo-2026-11-19.json) was recorded
from clean source commit `d88c6fe8bf4e5243325ae0899fbabb0f64116aea`.
The local Docker image ID was
`sha256:5c6929a07df408f918f5db955eb562e81bcf144097311268c9ff5e63351a091c`.
The image used `Dockerfile.api` and the bundled synthetic model. The API ran
as a single process, bound only to localhost, with an ephemeral SQLite ledger
and graph checkpoint. No Azure, Render, bank IdP, payment rail or real customer
record was involved.

Reproduce the flow with the commands in [DEMO.md](../../DEMO.md). The runner
drives the actual HTTP routes and checks all of these outcomes:

| Step | Observed result |
| --- | --- |
| Start workflow | HTTP 200; `REVIEW`, approval required; authority `RISK_COMMITTEE_L2` |
| Approve with the configured local reviewer | HTTP 200; application `APPROVED` |
| Replay the same key and action | HTTP 200 with `replay=true`; same disbursement ID |
| Replay the same key with a conflicting action | HTTP 409 |
| Approve the terminal application with a new key | HTTP 409 |
| Read disbursement ledger | Exactly one new row; `hash_valid=true` |
| Read audit | HTTP 200; recorded decision `APPROVE` |

The code fix checks an existing idempotency record after authentication and
authority checks but before rejecting an already terminal application. A
regression test covers replay, conflict and the single-row invariant. The
focused `tests/test_e2e_disbursement.py` suite passed 6/6 locally. The 146 ms
start time in the JSON is one local request, not a latency distribution.

This proves a synthetic software workflow, not decision quality. The model
was trained on proxy data; the earlier [14-case scoring report](local-api-2026-11-19.md)
shows a `past-defaults` profile received APPROVE. The graph explanation may
fall back to deterministic text when no LLM is configured. No live LLM quality,
production authorization, multi-replica persistence or real disbursement is
claimed.
