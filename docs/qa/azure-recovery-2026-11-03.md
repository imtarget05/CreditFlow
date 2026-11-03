# Azure deployment check and recovery decision — 2026-11-03

## Evidence now

- GitHub Actions CD run `38039267822` deployed `ac978d6` on 2026-10-10. Its
  Azure login, build/deploy, production-mode check, and smoke steps all passed.
  The deploy log reported the FQDN
  `creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io`.
- On 2026-11-03, that hostname did not resolve from the local machine. This
  contradicts a claim of current public availability; the historical green
  workflow is not a current health check.
- The local Azure CLI can list one Container App, `smartdoc-api`, in its default
  subscription and no `creditflow-api`. Its other enabled subscription has not
  registered `Microsoft.App`. This does not establish the state of the
  subscription used by the GitHub `AZURE_CREDENTIALS` secret.
- The GitHub repository lists `AZURE_CREDENTIALS` and `CREDITFLOW_API_KEY` among
  configured secret names. Secret presence does not prove the credentials still
  work or that a Container App exists.

## Reviewable verification path

`.github/workflows/verify-azure.yml` is a read-only verification workflow. A
manual `workflow_dispatch` run requires the file to exist on the default branch.
For an earlier check without merging into `main`, create a separate
`codex/azure-verify` branch from `origin/main`, cherry-pick the local
`ops: prepare read-only Azure deployment verification` commit
(the workflow, read-only smoke mode, test, and this runbook), review its diff,
then push that branch after approval. The workflow runs on that branch's push;
the CD workflow is limited to successful CI on `main` and is not triggered by
this verification branch. It logs in with
the existing secret, locates the existing `creditflow-api`, discovers its live
FQDN, runs the production smoke checks, and fails if the FQDN differs from the
URL embedded in CD and the docs. It does not create resources, change secrets,
build images, deploy code, or submit a `POST /predict` request. The verifier
sets `CREDITFLOW_SMOKE_READ_ONLY=1` because scoring would record an inference
for drift monitoring.

## Recovery decision after verification

1. **Login fails:** repair the GitHub service-principal credential and its
   subscription access; rerun verification. Do not interpret the old CD run as
   evidence of current access.
2. **App missing:** inspect the intended resource group, Container Apps
   environment, registry, and billing state in that subscription. Decide
   whether to restore the existing app or provision a replacement before
   triggering the deploy workflow.
3. **App exists, different FQDN:** update the Azure smoke base and CD
   `API_BASE`, rebuild the frontend against the discovered endpoint, then
   verify the published `release.json` and API calls. Avoid sending traffic to
   the stale FQDN.
4. **App exists, same FQDN, smoke fails:** inspect the active revision and
   application logs; fix the specific failing health, auth, CORS, or model
   check before deploying a new revision.
5. **Verification passes:** the historical Azure deployment is currently
   reachable. Deploying this local feature branch remains a separate release
   action and requires a reviewed source ref.

No Azure deployment or provider registration was performed during this check.
