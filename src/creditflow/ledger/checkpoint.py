"""Checkpoint save/load utilities for ledger persistence."""

import json
import os
from dataclasses import asdict
from pathlib import Path

from src.creditflow.ledger.format import LedgerEntry


def save_checkpoint(entries: list[LedgerEntry], path: str | Path) -> None:
    """Save a list of ledger entries as a checkpoint file.

    Args:
        entries: List of LedgerEntry instances to persist.
        path: File path for the checkpoint file (JSON format).
    """
    data = [asdict(entry) for entry in entries]
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_checkpoint(path: str | Path) -> list[LedgerEntry]:
    """Load ledger entries from a checkpoint file.

    Args:
        path: File path to the checkpoint file (JSON format).

    Returns:
        List of LedgerEntry instances reconstructed from the file.
    """
    with open(path, "r") as f:
        data = json.load(f)
    entries = [LedgerEntry(**item) for item in data]
    return entries