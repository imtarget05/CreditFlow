from pathlib import Path


def test_applicant_profiles_are_not_persisted_in_browser_storage():
    source = (Path(__file__).resolve().parents[1] / "frontend/src/App.jsx").read_text()
    assert "creditflow-history" not in source
    assert "localStorage.setItem(HISTORY_KEY" not in source
    assert "useState([])" in source
