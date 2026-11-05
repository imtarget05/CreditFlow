# QuanLyKho event boundary

CreditFlow may consume `quanlykho.inventory.request.approved` events, but an
event is only evidence that collateral processing completed. In production the
consumer creates a `PENDING_CHECKER` maker-checker request and never initiates
disbursement directly. The request must be approved by an authenticated checker
through the existing CreditFlow approval workflow.

Non-production mode supports a preview response for offline integration tests.
No live Kafka broker, bank rail, or provider credentials are included in this
adapter. A production deployment still needs the provider's Kafka topic,
authentication, TLS/mTLS, schema registry, retry, and dead-letter contract.
