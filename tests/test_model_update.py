"""Update-stamp contract: mỗi lần retrain phải để lại lý do + mốc bản trước."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.train_models import build_update_stamp


def test_stamp_keeps_reason_and_previous_timestamp():
    prev = {"version": "logistic_regression_v001", "trained_at": "2026-09-09T13:31:46+00:00"}
    out = build_update_stamp(prev, "drift nhẹ, retrain định kỳ tháng 9")
    assert out["update_reason"] == "drift nhẹ, retrain định kỳ tháng 9"
    assert out["previous_trained_at"] == "2026-09-09T13:31:46+00:00"


def test_stamp_first_train_has_no_previous_and_default_reason():
    out = build_update_stamp(None, "   ")
    assert out["previous_trained_at"] is None
    assert out["update_reason"] == "scheduled retrain (no reason given)"
