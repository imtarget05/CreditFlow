"""The Azure verification workflow must not record a synthetic inference."""

from scripts import smoke_production


def test_read_only_smoke_skips_predict_post(monkeypatch):
    smoke_production.FAILS.clear()
    monkeypatch.setattr(smoke_production, "READ_ONLY", True, raising=False)
    calls = []

    def fake_req(path, method="GET", headers=None, data=None):
        calls.append((method, path))
        if path == "/health/ready":
            return 200, '{"status":"ready"}', {}
        if path == "/model/info":
            return 200, '{"model_name":"demo"}', {}
        if method == "OPTIONS":
            return 200, "", {"Access-Control-Allow-Origin": smoke_production.ORIGIN}
        if path == "/applications":
            return 401, "", {}
        if path == "/health/live":
            return 200, "", {}
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(smoke_production, "req", fake_req)

    assert smoke_production.main() == 0
    assert all(method != "POST" for method, _ in calls)
