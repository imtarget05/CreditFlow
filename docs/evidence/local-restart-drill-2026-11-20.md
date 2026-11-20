# Local graph and ledger restart drill — 2026-11-20

Core API image source: `4085404f0fe0`.
The final hardened runner was executed from `f90db1ed981c`
plus the local security fix later pushed to PR #7. The [raw result](local-restart-drill-2026-11-20.json)
was produced by `python3 scripts/run_restart_drill.py --port 18083` using image
`sha256:9bd310b86c59265f932aa31a1a182afe6421be04d335b04c15229ede257c683a`.
The image was built from the cited core source. The runner's assertions, not a
hand transcription, generated the JSON. The local drill temporarily runs the
container as root to write its private temporary bind mount; the shipped image
still defaults to a non-root user.

| Check | Observed |
| --- | --- |
| Start | `REVIEW`, awaiting human approval |
| Restart before approval | Approval HTTP 200; ledger `APPROVED` |
| Restart after approval | Same-key replay HTTP 200 and `replay=true` |
| Ledger | Exactly one new disbursement, one matching row, valid hash |
| Audit | `APPROVE` |

Both checkpoint and SQLite ledger were mounted outside the container and
deleted after the run. This proves one local container can resume across
restarts with persistent files. It does **not** prove multi-replica safety,
PostgreSQL-backed shared checkpoints, real banking integration, or model
decision quality; the bundled model uses synthetic proxy data.
