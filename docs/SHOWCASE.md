# CreditFlow showcase mode

Showcase mode is for demonstrations with synthetic profiles only. It is not a
production deployment and must not be presented as one.

```bash
export CREDITFLOW_ENV=development
export CREDITFLOW_SHOWCASE=1
export CREDITFLOW_ENABLE_DEMO_LOGIN=1
uvicorn backend.app:app --host 127.0.0.1 --port 8080
```

The API and frontend display:

`DEMO · SYNTHETIC DATA · NO REAL MONEY`

In this mode `/health` reports `runtime_mode=showcase`,
`data_classification=synthetic demo data`, and
`real_integrations_enabled=false`. Core-banking initiation, approval, and
execution remain unavailable. Do not configure production IdP, broker, CIC,
eKYC, or banking credentials for a showcase process.
