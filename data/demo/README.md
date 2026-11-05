# CreditFlow local app test data

`app_cases.json` contains **14 fictional profiles** for the current `/predict` API: eight accepted cases and six validation failures. Every money value is in **VND**, matching the app form and API. IDs and labels are test descriptions, not customer identifiers. The cases have no names, account numbers, documents, tokens, passwords, or bank data.

From the CreditFlow repository root:

```bash
.venv/bin/python scripts/run_demo_app_cases.py
.venv/bin/pytest -q tests/test_demo_app_cases.py
```

To exercise a **local development** backend, start it in another terminal with the repo's normal development settings, then run:

```bash
.venv/bin/python scripts/run_demo_app_cases.py --send
.venv/bin/python scripts/run_demo_app_cases.py --send --case stable-income
```

`--send` only accepts an HTTP localhost URL, posts the selected profiles to `/predict`, and compares HTTP status to the fixture. The scoring endpoint may append records to its local inference monitoring store. This command does not create loan applications or disbursements. To try a case in the web form, copy its eight `profile` values into the matching fields; the UI already includes three separate presets.

An expected HTTP 200 means the payload passes the **local synthetic scoring contract**, not that a loan should be approved. A 422 case is intentionally invalid. Model scores, recommendations and any generated outcomes are not bank validation. The dataset does **not** supply Sacombank IdP issuer/audience/JWKS, real group IDs, approved customer data, or production acceptance evidence.

The existing `data/creditflow_dataset.csv` is a different 5,000-row synthetic **training** artifact whose money columns use thousands of VND. Do not post its rows directly to the VND API; `training-units-rejected` demonstrates that guard.
