# CreditFlow eKYC contract and revocation bridge

## Source plan

Derived from the P1.2 gateway item and the CreditFlow authentication checks
exercised by the existing test suite.

## User journeys

- As a CreditFlow integrator, I want normalized eKYC results with bounded
  biometric scores and masked identity numbers, so that sensitive source data
  is not exposed by the gateway contract.
- As an administrator, I want revoking or restoring a user to update token
  revocation state, so that revoked identities cannot continue using existing
  tokens.

## Task report

| Behavior | Test file or command | Result |
|---|---|---|
| Reject biometric scores outside 0..1 and reject unmasked document numbers | `tests/test_ekyc_gateway.py` | PASS |
| Return an explicitly labeled synthetic sandbox result and refuse sandbox execution in production | `tests/test_ekyc_gateway.py` | PASS |
| Bridge user revocation/restoration to token checks without revoking a distinct numeric username | `tests/test_user_revocation.py`, `tests/test_user_management_api.py` | PASS |
| Preserve API auth and Core Banking adapter behavior | `tests/test_api_auth.py`, `tests/test_core_banking_adapter.py` | PASS |
| Run all non-slow CreditFlow tests | `.venv/bin/python -m pytest tests -m 'not slow' -q` | PASS, 586 passed, 3 skipped, 19 deselected |

## RED/GREEN evidence

- RED: the eKYC tests initially failed collection because the gateway module did
  not exist. The full CreditFlow suite also exposed four token-revocation
  failures.
- GREEN: the eKYC result models and explicitly offline sandbox now pass the
  schema and environment-gating tests. UserManager now synchronizes revoked
  usernames and unambiguous user IDs with the security module; numeric username
  collisions remain isolated. The final non-slow suite passed.

## Coverage and known gaps

The full test command excludes tests marked `slow`; three tests were skipped
and 19 deselected. The sandbox generates only synthetic metadata and is disabled
in production. A live eKYC provider client is not implemented because no API
contract, authentication scheme, or certificate requirements were supplied.
