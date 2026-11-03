# Cloud verification snapshot — 2026-11-03

This records observed service behavior, not a deployment of the local feature
branch or a production acceptance decision. No cloud resources or secrets were
changed during these checks.

| Plane | Evidence | Limit |
|---|---|---|
| Cloudflare Workers AI | The direct API accepted fixed synthetic judge cases. Three original cases returned the requested score schema; the 70B judge ranked 8/8 controlled defect comparisons correctly across two runs. See `scripts/benchmark_llm_judge.py` and `docs/genai-and-agent-governance.md`. | This proves account/API access and a small synthetic contrast only. It does not prove an application deployment is configured to call Cloudflare or that the judge agrees with independent human ratings. |
| Render mirror | On 2026-11-03 at about 07:38 UTC, `GET /health/live` returned 200/`ok` and `GET /health/ready` returned 200/`ready` after an earlier pair of 25-second timeouts. `GET /llm/info` returned 200 with `provider=template` and `configured=false`. Readiness reported model `logistic_regression_v001` and `checkpoint_backend=unset`. | The timeouts' cause is unknown. Public readiness does not prove Neon persistence; `unset` does not identify the backing database. The deployed service is not currently advertising a configured Cloudflare LLM provider. The local feature branch is not shown to be deployed there. |
| Azure Container Apps | The GitHub CD run `38039267822` passed deploy and smoke on 2026-10-10. On 2026-11-03 the documented `creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io` hostname did not resolve locally. | Historical CI success does not establish current reachability. The local Azure login does not see this app; it may use a different subscription from the GitHub secret. The read-only verifier is prepared on local branch `codex/azure-verify` but has not been pushed or run. |

The committed feature snapshot previously passed 490 offline application tests;
a later frozen copy of the worktree, including concurrent uncommitted changes,
passed 531 tests with four external/infrastructure cases deselected. The model
card regenerated exactly under Python 3.12.13, NumPy 2.5.3, pandas 3.0.5, and
scikit-learn 1.9.0. These local results do not verify any cloud deployment.

## Remaining evidence

1. Run the reviewed Azure verifier using the GitHub service principal and
   inspect the live Container App/FQDN. See `docs/qa/azure-recovery-2026-11-03.md`.
2. Use Render account access to confirm the service's source revision,
   `DATABASE_URL`/Neon connection, checkpoint persistence, and optional LLM
   configuration. Do not expose secret values in the evidence.
3. Obtain an approved time-stamped outcome dataset and independent reviewer for
   temporal backtesting. See `docs/qa/temporal-validation-intake.md`.

Until those checks finish, the current cloud and lending-readiness claims remain
partial. The synthetic portfolio demo must not be represented as independently
validated credit decisioning.
