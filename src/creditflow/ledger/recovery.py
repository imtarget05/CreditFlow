"""Recovery module for ledger persistence after restart.

This module provides functionality to recover ledger data from the last
checkpoint file, ensuring data survives application restarts.
"""

import os
from pathlib import Path

from src.creditflow.ledger.checkpoint import load_checkpoint
from src.creditflow.ledger.format import LedgerEntry


def recover_ledger(path: str | Path) -> list[LedgerEntry]:
    """Load ledger entries from a checkpoint file on restart.

    Args:
        path: File path to the checkpoint file (JSON format).

    Returns:
        List of LedgerEntry instances loaded from the checkpoint.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Checkpoint file not found: {path}")
    entries = load_checkpoint(path)
    return entries