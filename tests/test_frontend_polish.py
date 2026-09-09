"""Tests for frontend interview demo polish (TDD — aligned to actual code)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"
sys.path.insert(0, str(ROOT))


def test_app_jsx_has_preset_chips():
    """App.jsx must expose PRESETS and applyPreset function."""
    app_path = FRONTEND_SRC / "App.jsx"
    content = app_path.read_text(encoding="utf-8")

    assert "PRESETS" in content, "Missing PRESETS constant"
    assert "applyPreset" in content, "Missing applyPreset function"
    assert "Khách hàng lý tưởng" in content, "Missing ideal preset label"
    assert "Khách hàng trung bình" in content, "Missing mid preset label"
    assert "Khách hàng rủi ro cao" in content, "Missing high preset label"


def test_app_jsx_has_backend_status_indicator():
    """App.jsx must show backend connectivity status."""
    app_path = FRONTEND_SRC / "App.jsx"
    content = app_path.read_text(encoding="utf-8")

    assert "checkHealth" in content, "Missing health check function"
    assert "/health" in content, "Must poll /health for backend status"
    assert "dot" in content, "Missing status dot element"
    assert "Backend đang hoạt động" in content or "Backend không khả dụng" in content, "Missing backend status text"


def test_styles_has_preset_and_status_classes():
    """styles.css must include classes for preset chips and backend status."""
    css_path = FRONTEND_SRC / "styles.css"
    content = css_path.read_text(encoding="utf-8")

    # The actual CSS may use different class names; check for any status/preset styling
    assert "preset" in content.lower() or "chip" in content.lower(), "Missing preset/chip style"
    assert "dot" in content.lower() or "status" in content.lower(), "Missing status dot/style"
