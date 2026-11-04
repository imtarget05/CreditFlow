# Inputs needed for a Sacombank-controlled pilot

Status: **request list, not received or approved**. CreditFlow currently uses synthetic data and a local demo identity map. No real customer record, lending decision or payment instruction should be sent to this deployment.

| Owner to nominate | Input and acceptance evidence needed |
|---|---|
| Credit operations | Current intake, document check, supplement, appraisal and approval process; each state owner, handoff, SLA, holiday calendar, escalation rule and exception path. Provide anonymized sample cases and UAT participants. |
| Credit policy and Risk | Authority matrix, policy and document versions, required evidence by product, allowed recommendation vocabulary, override rules, model validation protocol and release approvers. |
| IAM and security | OIDC issuer, audience, JWKS, group identifiers, test users by role, revocation and session policy, data-scope model, credential lifecycle and audit access. Use a test tenant first. |
| Data and privacy | Approved historical dataset or lawful test substitute, field dictionary, permitted use and retention, masking, deletion, storage location, encryption and backup requirements. Do not send files through the public demo. |
| Integration teams | Sandbox contracts and credentials for CIC, document repository, core banking status and outcome feed; event ordering, idempotency keys, retry limits, reconciliation and failure codes. No live disbursement endpoint is assumed. |
| Platform and operations | Private network/deployment target, approved PostgreSQL and document storage, observability sink, backup/restore target, RPO/RTO, incident contacts and rollback procedure. |

## Pilot acceptance session

Use only synthetic or bank-approved test data. A loan officer should create and claim a case, check documents, flag a possible repeated profile, request a supplement and escalate an exception. A manager should assign and close work. Risk should inspect an exception and document an outcome. Operations should observe due reminders and recover a failed worker. Record timestamps, actor, reason, request ID and policy/model version for each transition; compare task time and re-entry count against the current process.

The current implementation covers part of this journey: queue claim, manager assignment with configured pilot identities, supplement request/resume, escalation to Risk, automatic document → underwriting → Risk-review task handoff, completion, metadata checklist, exact-profile collision signal and in-app overdue reminders. These handoffs do not make a credit decision. Identity-backed assignment, external notification delivery, outcome feed, independent model validation, restore drill and human UAT remain open. The [readiness report](ENTERPRISE_READINESS_2026-11-03.md) is the release gate register.
