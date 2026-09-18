"""Tests for the ledger checkpoint module."""

import json
import os

from src.creditflow.ledger.format import LedgerEntry


def test_checkpoint_save_load():
    """Test saving and loading a checkpoint."""
    from src.creditflow.ledger.checkpoint import save_checkpoint, load_checkpoint

    entry = LedgerEntry(timestamp=1.0, action="test", data={}, checkpoint=True)
    path = "/tmp/test_checkpoint.json"

    save_checkpoint([entry], path)
    loaded = load_checkpoint(path)

    assert loaded == [entry]
    os.remove(path)