"""Validate synthetic app fixtures, optionally exercise a local /predict API.

This command never contacts a remote host or seeds the persistent loan ledger.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "demo" / "app_cases.json"
PROFILE_FIELDS = {
    "income", "age", "employment_years", "loan_amount", "loan_term",
    "existing_debt", "credit_history", "previous_defaults",
}


def load_cases() -> list[dict]:
    bundle = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if bundle.get("schema_version") != 1 or bundle.get("endpoint") != "/predict":
        raise ValueError("Unsupported demo fixture contract")
    cases = bundle.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Demo fixture must contain cases")
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("Case IDs must be unique nonempty strings")
        seen.add(case_id)
        if case.get("expected_http_status") not in (200, 422):
            raise ValueError(f"{case_id}: unsupported expected status")
        profile = case.get("profile")
        if not isinstance(profile, dict) or not set(profile) <= PROFILE_FIELDS:
            raise ValueError(f"{case_id}: unexpected profile fields")
        if case["expected_http_status"] == 200 and set(profile) != PROFILE_FIELDS:
            raise ValueError(f"{case_id}: valid case lacks a required field")
    return cases


def local_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
        or parsed.username or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query or parsed.fragment
    ):
        raise ValueError("--base-url must be an HTTP localhost URL without credentials or path")
    return value.rstrip("/")


def send_case(base_url: str, case: dict) -> int:
    request = Request(
        f"{base_url}/predict",
        data=json.dumps(case["profile"]).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="POST cases to a local API")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--case", action="append", help="Run only the named case; repeatable")
    args = parser.parse_args()
    try:
        cases = load_cases()
        if args.case:
            selected = set(args.case)
            missing = selected - {case["id"] for case in cases}
            if missing:
                raise ValueError(f"Unknown case ID(s): {', '.join(sorted(missing))}")
            cases = [case for case in cases if case["id"] in selected]
        if not args.send:
            print(f"Validated {len(cases)} synthetic cases from {FIXTURE}")
            for case in cases:
                print(f"{case['id']}: expected HTTP {case['expected_http_status']}")
            return 0
        if os.environ.get("CREDITFLOW_ENV", "").strip().lower() == "production":
            raise ValueError("Refusing demo requests with CREDITFLOW_ENV=production")
        base_url = local_base_url(args.base_url)
        failed = 0
        for case in cases:
            actual = send_case(base_url, case)
            expected = case["expected_http_status"]
            passed = actual == expected
            print(f"{'PASS' if passed else 'FAIL'} {case['id']}: HTTP {actual} (expected {expected})")
            failed += not passed
        return 1 if failed else 0
    except (ValueError, OSError, URLError, json.JSONDecodeError) as exc:
        print(f"Demo cases: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
