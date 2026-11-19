# CreditFlow localhost interview demo

This demo drives the real FastAPI and LangGraph routes through a synthetic
REVIEW → approve → replay → ledger → audit flow. It uses an isolated Docker
container and does not call Azure, Render, a bank IdP or a payment rail.

From the repository root with Docker running:

```sh
docker build -f Dockerfile.api -t creditflow-api-graph-demo .
docker run --rm -d --name creditflow-graph-demo \
  -p 127.0.0.1:18081:8000 \
  -e CREDITFLOW_ENV=development \
  -e CREDITFLOW_ALLOW_ANONYMOUS_DEMO=1 \
  -e CREDITFLOW_API_KEY=local-graph-demo-only \
  -e CREDITFLOW_API_KEY_ID=demo-reviewer \
  -e CREDITFLOW_API_KEY_ROLE=RISK_COMMITTEE_L2 \
  creditflow-api-graph-demo
CREDITFLOW_API_KEY=local-graph-demo-only \
  python3 scripts/run_local_graph_demo.py \
  --base-url http://127.0.0.1:18081 \
  --output /tmp/creditflow-graph-demo.json
docker stop creditflow-graph-demo
```

The runner checks a paused `REVIEW`, approval by the configured local reviewer,
an `APPROVED` application, one completed disbursement, a 64-character ledger
hash, the same disbursement on replay, and an audit decision. It verifies
`hash_valid` on the ledger read. It also checks that a conflicting action or a
different key after approval returns HTTP 409. The local key is a throwaway
demo value and must never be used for a deployment. The container's SQLite ledger and graph
checkpoint are disposable when stopped.

The flow is single-process and uses a bundled model trained on synthetic proxy
data. A model score or APPROVE outcome is **not** validated for real lending.
`CREDITFLOW_ALLOW_ANONYMOUS_DEMO=1` is accepted only outside production, and
approval still requires an API key with adequate authority. The graph's
explanation can use a deterministic fallback when no local LLM is configured;
this demo does not prove live LLM quality. See
[`docs/evidence/local-api-2026-11-19.md`](docs/evidence/local-api-2026-11-19.md)
for the separate 14-case scoring contract and the synthetic model's limits.

For tests covering the behavior in an isolated process:

```sh
python3 -m pytest -q tests/test_e2e_disbursement.py
```

In an interview, explain why the idempotency record is checked **after**
authentication/authority but **before** refusing an already approved
application. A different key against a terminal application still returns
HTTP 409, and a replay with a conflicting action is rejected. The replay test
asserts no second disbursement row is created.
