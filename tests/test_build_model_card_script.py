"""Model card must describe the committed synthetic model, not claim approval."""

import json

from scripts import build_model_card


def test_model_card_uses_held_out_metrics_and_discloses_demo_scope():
    meta = json.loads((build_model_card.ROOT / "models/production/meta.json").read_text())
    bundle_mtime = (build_model_card.ROOT / "models/production/pipeline.joblib").stat().st_mtime_ns

    card = build_model_card.build_model_card()

    assert f"# CreditFlow Model Card — {meta['version']}" in card
    assert f"`{meta['dataset_sha256']}`" in card
    assert "## Held-out test performance" in card
    assert "### Calibration bins" in card
    assert "### Decision mix by candidate approve threshold" in card
    assert "### Age-band diagnostic" in card
    assert "not a validation approval for lending use" in card.replace("\n", " ")
    assert "Before any real lending use" in card
    assert (build_model_card.ROOT / "models/production/pipeline.joblib").stat().st_mtime_ns == bundle_mtime
