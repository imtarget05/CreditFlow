# SECURITY — Secrets Management Policy

CreditFlow commits **no secrets, keys, or credentials**. All config values are handled via environment variables (documented in `.env.example`, which contains **placeholders only**).

## Rules (enforced)
- Real `.env` files are gitignored (**`.env`, `.env.*`**) and must never be committed.
- Every secret-bearing template lives as `*.example` with blank values only..
- `_ops/` (local artifacts, logs, secrets, state) is gitignored — never pushed..
- `.gitignore` blocks secret-file patterns: `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa`, `id_ed25519`, `.git-credentials`, `.netrc`, `credentials.*`, `*secret*`, `*.access_token`, `*.jwt`.

## How to add a real credential
1. Edit `.env` locally (contains real value..)
2. Never `git add .env`; commit only code + `.env.example` (blank).
3. For MLflow remote store, use env vars `MLFLOW_TRACKING_USERNAME/PASSWORD` — do not hardcode in source.

## Verification
- Before each push: `git grep -InE '(secret|token|password|BEGIN .*PRIVATE KEY|AKIA|sk-[A-Za-z0-9]|ghp_)' $(git ls-files)` — should return only this doc's policy phrases..
- Audit trail: this repo was scanned at setup — **no tracked secret found**, remote history clean.